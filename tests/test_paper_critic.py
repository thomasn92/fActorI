from __future__ import annotations

import json

from typer.testing import CliRunner

from factori.cli import app
from factori.hashing import sha256_file
from factori.paper_critic import critique_generated_paper
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    ArtifactRef,
    BibliographyEntry,
    CitationRecord,
    CitationRegistry,
    LatexSourceMap,
    PaperCriticFindingType,
    PaperCriticReport,
    PaperRevisionPlan,
    PipelineRunConfig,
    PipelineStage,
)


def test_paper_critic_models_are_importable() -> None:
    assert PaperCriticReport
    assert PaperRevisionPlan


def test_critic_detects_missing_central_message() -> None:
    report = critique_generated_paper(run_id="run-1", markdown="# Draft\n\n## Introduction\n\n")

    assert _messages(report)
    assert any("central message" in message for message in _messages(report))


def test_critic_detects_missing_problem_framing() -> None:
    report = critique_generated_paper(
        run_id="run-1",
        markdown="# Draft\n\n## Central Message\n\nA bounded message.\n\n## Introduction\n\n",
    )

    assert any("problem framing" in message for message in _messages(report))


def test_critic_detects_retrieval_as_novelty_proof() -> None:
    report = critique_generated_paper(
        run_id="run-1",
        markdown=_safe_markdown("retrieval proves novelty for this paper."),
    )

    assert any("novelty/proof" in message for message in _messages(report))
    assert report.blocking_findings >= 1


def test_critic_detects_synthetic_as_real_empirical_validation() -> None:
    report = critique_generated_paper(
        run_id="run-1",
        markdown=_safe_markdown("The synthetic run is empirically validated."),
    )

    assert any("real-world empirical validation" in message for message in _messages(report))


def test_critic_detects_invented_citation_key() -> None:
    report = critique_generated_paper(
        run_id="run-1",
        markdown=_safe_markdown("A source is cited [@Unknown2024]."),
        citation_registry=_citation_registry(),
    )

    assert any("Unknown2024" in message for message in _messages(report))


def test_critic_detects_missing_bibliography_source_provenance() -> None:
    registry = _citation_registry(has_source_provenance=False)
    report = critique_generated_paper(
        run_id="run-1",
        markdown=_safe_markdown("A source is cited [@Smith2024Bounded]."),
        citation_registry=registry,
    )

    assert any("lacks source provenance" in message for message in _messages(report))


def test_critic_detects_unsupported_verification_labels() -> None:
    report = critique_generated_paper(
        run_id="run-1",
        markdown=_safe_markdown("This is LeanVerified and SyntheticExperimentVerified."),
    )

    messages = _messages(report)
    assert any("LeanVerified" in message for message in messages)
    assert any("SyntheticExperimentVerified" in message for message in messages)


def test_critic_detects_missing_appendices() -> None:
    report = critique_generated_paper(
        run_id="run-1",
        markdown="# Draft\n\n## Central Message\n\nProblem and main result.\n",
    )

    assert any("claim/evidence appendix" in message for message in _messages(report))
    assert any("provenance appendix" in message for message in _messages(report))


def test_critic_checks_latex_source_map_coverage() -> None:
    report = critique_generated_paper(
        run_id="run-1",
        markdown=_safe_markdown(),
        source_map=LatexSourceMap(
            run_id="run-1",
            entries=[],
            source_map_policy="map sections",
            covers_all_major_sections=False,
            missing_sections=["Introduction"],
        ),
    )

    assert any(
        finding.finding_type == PaperCriticFindingType.SOURCE_MAP_FINDING
        for finding in report.findings
    )
    assert any("Introduction" in message for message in _messages(report))


def test_critic_finding_order_is_deterministic() -> None:
    kwargs = {
        "run_id": "run-1",
        "markdown": _safe_markdown("retrieval proves novelty [@Unknown2024]."),
        "citation_registry": _citation_registry(),
    }

    first = critique_generated_paper(**kwargs)
    second = critique_generated_paper(**kwargs)

    assert first == second
    assert [finding.finding_id for finding in first.findings] == sorted(
        finding.finding_id for finding in first.findings
    )


def test_critique_paper_cli_works_and_write_report(tmp_path) -> None:
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id="run-1",
            domain="human geography",
            root=tmp_path,
            stop_after=PipelineStage.PLAN_MANUSCRIPT,
        )
    )
    runner = CliRunner()
    draft = runner.invoke(
        app,
        [
            "draft-manuscript",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--write-report",
        ],
    )
    assert draft.exit_code == 0, draft.output

    result = runner.invoke(
        app,
        [
            "critique-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    report = payload["paper_critic_report"]
    assert report["is_verification_evidence"] is False
    ref = ArtifactRef.model_validate(payload["artifacts"]["paper_critic_report"])
    assert (tmp_path / ref.path).is_file()
    assert ref.content_hash == sha256_file(tmp_path / ref.path)
    linked = ArtifactRef.model_validate_json(
        (tmp_path / f"{ref.path}.meta.json").read_text(encoding="utf-8")
    )
    assert linked.metadata["is_verification_evidence"] is False


def _messages(report: PaperCriticReport) -> list[str]:
    return [finding.message for finding in report.findings]


def _safe_markdown(extra: str = "") -> str:
    return (
        "# Draft\n\n"
        "## Central Message\n\n"
        "A bounded problem and main result are presented.\n\n"
        "## Introduction\n\n"
        "This draft uses bounded literature context and is non-exhaustive.\n"
        f"{extra}\n\n"
        "## Main Result and Derivatives\n\n"
        "The main result is stated in prose.\n\n"
        "## Limitations\n\n"
        "Limitations are explicit.\n\n"
        "## Claim/Evidence Appendix\n\n"
        "- claim-main: evidence-a\n\n"
        "## Provenance Appendix\n\n"
        "- Presentation only.\n"
    )


def _citation_registry(*, has_source_provenance: bool = True) -> CitationRegistry:
    record = CitationRecord(
        citation_id="citation-source-1",
        citation_key="Smith2024Bounded",
        source_id="source-1",
        title="Bounded source",
        authors=["Smith"],
        year=2024,
        provider="fake",
        retrieved_at="1970-01-01T00:00:00.000000Z",
        raw_metadata_hash="0" * 64,
        source_artifact_id="retrieval-normalized-results",
    )
    return CitationRegistry(
        run_id="run-1",
        citations=[record],
        bibliography=[
            BibliographyEntry(
                citation_id=record.citation_id,
                citation_key=record.citation_key,
                source_id=record.source_id,
                markdown="- [@Smith2024Bounded] Bounded source.",
                has_source_provenance=has_source_provenance,
            )
        ],
        citation_key_policy="deterministic",
        source_registry_hash="0" * 64,
    )
