from __future__ import annotations

import json

from typer.testing import CliRunner

from factori.cli import app
from factori.hashing import sha256_file
from factori.paper_critic import build_paper_revision_plan, critique_generated_paper
from factori.paper_revision import (
    apply_safe_fake_revision,
    validate_revision_safety,
)
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    ArtifactRef,
    BibliographyEntry,
    CitationRecord,
    CitationRegistry,
    PaperRevisionActionKind,
    PaperRevisionResult,
    PaperRevisionStatus,
    PipelineRunConfig,
    PipelineStage,
    RevisionSafetyReport,
)


def test_paper_revision_models_are_importable() -> None:
    assert PaperRevisionResult
    assert RevisionSafetyReport


def test_revision_plan_is_deterministic_and_maps_findings_to_actions() -> None:
    report = critique_generated_paper(
        run_id="run-1",
        markdown=_unsafe_markdown(),
        citation_registry=_citation_registry(),
    )

    first = build_paper_revision_plan(report)
    second = build_paper_revision_plan(report)

    assert first == second
    assert PaperRevisionActionKind.DOWNGRADE_UNSUPPORTED_CLAIM_LANGUAGE in first.actions
    assert PaperRevisionActionKind.REMOVE_INVENTED_CITATION in first.actions


def test_fake_revision_inserts_bounded_literature_disclaimer() -> None:
    report = critique_generated_paper(run_id="run-1", markdown=_markdown_without_disclaimer())
    plan = build_paper_revision_plan(report)

    result = apply_safe_fake_revision(
        run_id="run-1",
        markdown=_markdown_without_disclaimer(),
        revision_plan=plan,
    )

    assert "non-exhaustive" in result.revised_markdown
    assert result.revision_status in {
        PaperRevisionStatus.REVISION_APPLIED,
        PaperRevisionStatus.REVISION_APPLIED_WITH_WARNINGS,
    }


def test_fake_revision_downgrades_retrieval_as_proof_language() -> None:
    report = critique_generated_paper(run_id="run-1", markdown=_unsafe_markdown())
    plan = build_paper_revision_plan(report)

    result = apply_safe_fake_revision(
        run_id="run-1",
        markdown=_unsafe_markdown(),
        revision_plan=plan,
    )

    assert "retrieval proves novelty" not in result.revised_markdown.lower()
    assert "bounded context" in result.revised_markdown.lower()


def test_fake_revision_downgrades_synthetic_as_real_empirical_language() -> None:
    markdown = _safe_markdown("The synthetic result is empirically validated.")
    report = critique_generated_paper(run_id="run-1", markdown=markdown)
    plan = build_paper_revision_plan(report)

    result = apply_safe_fake_revision(
        run_id="run-1",
        markdown=markdown,
        revision_plan=plan,
    )

    assert "empirically validated" not in result.revised_markdown.lower()
    assert "synthetic setting" in result.revised_markdown.lower()


def test_fake_revision_does_not_invent_or_upgrade_labels() -> None:
    markdown = _safe_markdown("This claim is LeanVerified.")
    report = critique_generated_paper(run_id="run-1", markdown=markdown)
    plan = build_paper_revision_plan(report)

    result = apply_safe_fake_revision(
        run_id="run-1",
        markdown=markdown,
        revision_plan=plan,
    )

    assert "LeanVerified" not in result.revised_markdown
    assert not result.safety_report.created_or_upgraded_labels


def test_bounded_safe_repair_removes_textual_boundary_violations() -> None:
    markdown = _safe_markdown(
        "[UNSAFE SECTION OMITTED] forbidden label appears in generated prose: Conjecture; "
        "generated prose contains unsupported sentences\n"
        "[UNSUPPORTED SENTENCE] This theorem is empirically validated and publication ready."
    )
    report = critique_generated_paper(run_id="run-1", markdown=markdown)

    result = apply_safe_fake_revision(
        run_id="run-1",
        markdown=markdown,
        revision_plan=build_paper_revision_plan(report),
        bounded_text_repair=True,
    )

    lowered = result.revised_markdown.lower()
    assert "conjecture" not in lowered
    assert "theorem" not in lowered
    assert "empirically validated" not in lowered
    assert "publication ready" not in lowered
    assert "section omitted by safety check" in lowered
    assert "synthetic outputs do not establish empirical validation" in lowered
    assert result.safety_report.safe


def test_bounded_safe_repair_preserves_known_citations_and_invents_none() -> None:
    registry = _citation_registry()
    markdown = _safe_markdown(
        "Known context [@Smith2024Bounded]. This Conjecture is not evidence."
    )
    report = critique_generated_paper(
        run_id="run-1",
        markdown=markdown,
        citation_registry=registry,
    )

    result = apply_safe_fake_revision(
        run_id="run-1",
        markdown=markdown,
        revision_plan=build_paper_revision_plan(report),
        citation_registry=registry,
        bounded_text_repair=True,
    )

    assert "[@Smith2024Bounded]" in result.revised_markdown
    assert result.safety_report.invented_citation_keys == []
    assert not result.safety_report.created_or_upgraded_labels


def test_fake_revision_preserves_known_citations_and_removes_unknown() -> None:
    markdown = _safe_markdown("Known [@Smith2024Bounded] and unknown [@Missing2024].")
    registry = _citation_registry()
    report = critique_generated_paper(
        run_id="run-1",
        markdown=markdown,
        citation_registry=registry,
    )
    plan = build_paper_revision_plan(report)

    result = apply_safe_fake_revision(
        run_id="run-1",
        markdown=markdown,
        revision_plan=plan,
        citation_registry=registry,
    )

    assert "[@Smith2024Bounded]" in result.revised_markdown
    assert "[@Missing2024]" not in result.revised_markdown
    assert result.safety_report.invented_citation_keys == []
    assert result.safety_report.known_citation_keys_preserved == ["Smith2024Bounded"]


def test_revision_safety_rejects_unsafe_generated_revision() -> None:
    safety = validate_revision_safety(
        run_id="run-1",
        original_markdown=_safe_markdown(),
        revised_markdown=_safe_markdown("novelty is proven by retrieval [@Missing]."),
        citation_registry=_citation_registry(),
    )

    assert safety.rejected
    assert any("novelty/proof" in reason for reason in safety.reasons)
    assert safety.invented_citation_keys == ["Missing"]


def test_revision_does_not_mutate_claim_or_evidence_tables() -> None:
    result = apply_safe_fake_revision(
        run_id="run-1",
        markdown=_unsafe_markdown(),
        revision_plan=build_paper_revision_plan(
            critique_generated_paper(run_id="run-1", markdown=_unsafe_markdown())
        ),
    )

    assert not result.safety_report.mutated_claim_table
    assert not result.safety_report.mutated_evidence_map
    assert not result.implies_publication_readiness


def test_revise_paper_cli_planning_mode_works(tmp_path) -> None:
    _prepare_draft(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "revise-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["paper_revision_plan"]["is_verification_evidence"] is False
    assert payload["paper_revision_result"] is None


def test_revise_paper_cli_apply_safe_fake_revision_writes_artifacts(tmp_path) -> None:
    _prepare_draft(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "revise-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--apply-safe-fake-revision",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    revision = payload["paper_revision_result"]
    assert revision["is_verification_evidence"] is False
    assert revision["implies_publication_readiness"] is False
    artifacts = payload["artifacts"]
    for key in (
        "paper_critic_report",
        "paper_revision_plan",
        "revision_safety_report",
        "revised_manuscript_draft",
    ):
        ref = ArtifactRef.model_validate(artifacts[key])
        assert (tmp_path / ref.path).is_file()
        assert ref.content_hash == sha256_file(tmp_path / ref.path)
        linked = ArtifactRef.model_validate_json(
            (tmp_path / f"{ref.path}.meta.json").read_text(encoding="utf-8")
        )
        assert linked.metadata["is_verification_evidence"] is False
        assert linked.metadata["creates_scientific_validation"] is False


def _prepare_draft(tmp_path) -> None:
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


def _unsafe_markdown() -> str:
    return _safe_markdown("retrieval proves novelty [@Missing2024].")


def _markdown_without_disclaimer() -> str:
    return (
        "# Draft\n\n"
        "## Central Message\n\n"
        "A problem and main result are presented.\n\n"
        "## Introduction\n\n"
        "This is background.\n\n"
        "## Claim/Evidence Appendix\n\n"
        "- claim-main: evidence-a\n\n"
        "## Provenance Appendix\n\n"
        "- Presentation only.\n"
    )


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


def _citation_registry() -> CitationRegistry:
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
                has_source_provenance=True,
            )
        ],
        citation_key_policy="deterministic",
        source_registry_hash="0" * 64,
    )
