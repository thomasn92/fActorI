from __future__ import annotations

import json

from typer.testing import CliRunner

from factori.adapters.fake import FakeProseGenerator
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.full_paper_generation import (
    generate_full_paper,
    inspect_paper_bundle_summary,
    lint_paper_bundle_summary,
)
from factori.full_paper_release import run_full_paper_release_gate
from factori.hashing import sha256_file
from factori.ledger import ResearchLedger
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    FullPaperGenerationConfig,
    FullPaperReleaseGateConfig,
    PipelineRunConfig,
    PipelineStage,
)


def test_inspect_llm_run_cli_is_registered() -> None:
    result = CliRunner().invoke(app, ["inspect-llm-run", "--help"])

    assert result.exit_code == 0, result.output
    assert "--run-id" in result.output
    assert "--json" in result.output


def test_inspect_paper_bundle_cli_is_registered() -> None:
    result = CliRunner().invoke(app, ["inspect-paper-bundle", "--help"])

    assert result.exit_code == 0, result.output
    assert "--run-id" in result.output
    assert "--json" in result.output


def test_lint_paper_bundle_cli_is_registered() -> None:
    result = CliRunner().invoke(app, ["lint-paper-bundle", "--help"])

    assert result.exit_code == 0, result.output
    assert "--run-id" in result.output
    assert "--json" in result.output
    assert "--min-words" in result.output
    assert "--min-avg-words-per-section" in result.output
    assert "--min-citation-markers" in result.output


def test_inspect_paper_bundle_with_revised_artifacts_is_read_only(tmp_path) -> None:
    run_id = "inspect-paper-revised"
    _prepare_paper_bundle(tmp_path, run_id=run_id, revised=True, release=True)
    before = _run_file_snapshot(tmp_path, run_id)

    json_result = CliRunner().invoke(
        app,
        [
            "inspect-paper-bundle",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )

    assert json_result.exit_code == 0, json_result.output
    assert _run_file_snapshot(tmp_path, run_id) == before
    payload = json.loads(json_result.output)
    assert payload["run_id"] == run_id
    assert payload["paper_exists"] is True
    assert payload["revised_paper_exists"] is True
    assert payload["complete_manuscript_draft_exists"] is True
    assert payload["revised_manuscript_draft_exists"] is True
    assert payload["latex_exists"] is True
    assert payload["revised_latex_exists"] is True
    assert payload["safe_repair_report_exists"] is True
    assert payload["release_report_exists"] is True
    assert payload["generation_report_exists"] is True
    assert payload["primary_artifact_to_read"].endswith(
        "reports/revised-manuscript-draft.md"
    )
    assert payload["primary_latex_to_read"].endswith("latex/revised-paper.tex")
    assert payload["line_count"] > 0
    assert payload["word_count"] > 0
    assert payload["section_count"] > 0
    assert payload["section_headings_detected"]
    assert payload["title_detected"]
    assert payload["safe_repair_applied_count"] >= 0
    assert payload["citations_present"] is False
    assert payload["artifacts"]["revised_paper"].endswith("latex/revised-paper.tex")

    human_result = CliRunner().invoke(
        app,
        ["inspect-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )

    assert human_result.exit_code == 0, human_result.output
    assert f"Paper bundle: {run_id}" in human_result.output
    assert "Primary draft: revised-manuscript-draft.md" in human_result.output
    assert "Release: ReadyForHumanReviewWithWarnings" in human_result.output
    assert "Safe repair: present" in human_result.output
    assert "Citations: absent" in human_result.output
    assert "Artifacts:" in human_result.output
    assert "- revised manuscript:" in human_result.output
    assert "- revised latex:" in human_result.output
    assert "- release report:" in human_result.output
    assert _run_file_snapshot(tmp_path, run_id) == before


def test_inspect_paper_bundle_without_revised_artifacts_degrades_gracefully(tmp_path) -> None:
    run_id = "inspect-paper-unrevised"
    _prepare_paper_bundle(tmp_path, run_id=run_id, revised=False, release=False)

    summary = inspect_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert summary["paper_exists"] is True
    assert summary["revised_paper_exists"] is False
    assert summary["complete_manuscript_draft_exists"] is True
    assert summary["revised_manuscript_draft_exists"] is False
    assert summary["safe_repair_report_exists"] is False
    assert summary["release_report_exists"] is False
    assert summary["generation_report_exists"] is True
    assert summary["primary_artifact_to_read"].endswith(
        "reports/complete-manuscript-draft.md"
    )
    assert summary["primary_latex_to_read"].endswith("latex/paper.tex")
    assert "safe_repair_report" not in summary["artifacts"]
    assert summary["release_status"] is None


def test_inspect_paper_bundle_missing_run_gives_clear_error(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "inspect-paper-bundle",
            "--root",
            str(tmp_path),
            "--run-id",
            "missing-paper-run",
        ],
    )

    assert result.exit_code == 1
    assert "No run directory found for run_id=missing-paper-run" in result.output


def test_lint_paper_bundle_fails_placeholder_short_draft_read_only(tmp_path) -> None:
    run_id = "lint-paper-placeholder"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_short_placeholder_markdown(),
    )
    before = _run_file_snapshot(tmp_path, run_id)

    json_result = CliRunner().invoke(
        app,
        [
            "lint-paper-bundle",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )

    assert json_result.exit_code == 0, json_result.output
    assert _run_file_snapshot(tmp_path, run_id) == before
    payload = json.loads(json_result.output)
    assert payload["run_id"] == run_id
    assert payload["quality_status"] == "DraftQualityFailed"
    assert payload["word_count"] < 1500
    assert payload["section_count"] > 10
    assert payload["title_is_placeholder"] is True
    assert payload["citations_present"] is False
    assert payload["too_many_sections_for_length"] is True
    assert payload["publication_ready"] is False
    assert payload["is_verification_evidence"] is False
    assert "Draft is below minimum word count." in payload["issues"]
    assert "Title appears to be a placeholder." in payload["issues"]
    assert "Too many sections for draft length." in payload["issues"]
    assert "No citation markers found." in payload["issues"]
    assert "No citation markers found." in payload["warnings"]

    human_result = CliRunner().invoke(
        app,
        ["lint-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )

    assert human_result.exit_code == 0, human_result.output
    assert f"Paper quality: {run_id}" in human_result.output
    assert "Status: DraftQualityFailed" in human_result.output
    assert "Title: placeholder" in human_result.output
    assert "Citations: absent" in human_result.output
    assert "Issues:" in human_result.output
    assert _run_file_snapshot(tmp_path, run_id) == before


def test_lint_paper_bundle_warns_on_missing_citations(tmp_path) -> None:
    run_id = "lint-paper-no-citations"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_acceptable_markdown(include_citation=False),
    )

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityWarnings"
    assert payload["citation_marker_count"] == 0
    assert payload["blocking_quality_issues"] == []
    assert payload["warnings"] == ["No citation markers found."]
    assert "No citation markers found." in payload["issues"]


def test_lint_paper_bundle_passes_acceptable_synthetic_fixture(tmp_path) -> None:
    run_id = "lint-paper-pass"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_acceptable_markdown(include_citation=True),
    )

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityPass"
    assert payload["word_count"] >= payload["thresholds"]["min_words"]
    assert payload["average_words_per_section"] >= payload["thresholds"][
        "min_avg_words_per_section"
    ]
    assert payload["citation_marker_count"] >= 1
    assert payload["issues"] == []
    assert payload["warnings"] == []


def test_lint_paper_bundle_prefers_revised_artifact(tmp_path) -> None:
    run_id = "lint-paper-prefers-revised"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_acceptable_markdown(include_citation=True),
        revised_markdown=_short_placeholder_markdown(),
    )

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["primary_artifact_to_read"].endswith(
        "reports/revised-manuscript-draft.md"
    )
    assert payload["quality_status"] == "DraftQualityFailed"
    assert payload["title_is_placeholder"] is True


def test_lint_paper_bundle_missing_run_gives_clear_error(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "lint-paper-bundle",
            "--root",
            str(tmp_path),
            "--run-id",
            "missing-paper-run",
        ],
    )

    assert result.exit_code == 1
    assert "No run directory found for run_id=missing-paper-run" in result.output


def test_lint_paper_bundle_missing_optional_artifacts_degrades_gracefully(tmp_path) -> None:
    run_id = "lint-paper-minimal"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_acceptable_markdown(include_citation=True),
    )

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityPass"
    assert payload["primary_artifact_to_read"].endswith(
        "reports/complete-manuscript-draft.md"
    )
    assert payload["paper_release_status"] is None
    assert payload["release_status_unchanged"] is True
    assert payload["safety_status_unchanged"] is True


def _prepare_paper_bundle(
    tmp_path,
    *,
    run_id: str,
    revised: bool,
    release: bool,
) -> None:
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id=run_id,
            domain="human geography",
            root=tmp_path,
            stop_after=PipelineStage.PLAN_MANUSCRIPT,
        )
    )
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    generate_full_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        config=FullPaperGenerationConfig(run_id=run_id, write_report=True),
        enable_safe_repair=revised,
    )
    if release:
        run_full_paper_release_gate(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            config=FullPaperReleaseGateConfig(run_id=run_id, write_report=True),
        )


def _run_file_snapshot(tmp_path, run_id: str) -> dict[str, str]:
    run_path = tmp_path / "runs" / run_id
    return {
        path.relative_to(run_path).as_posix(): sha256_file(path)
        for path in sorted(run_path.rglob("*"))
        if path.is_file()
    }


def _write_paper_bundle_markdown(
    tmp_path,
    *,
    run_id: str,
    complete_markdown: str,
    revised_markdown: str | None = None,
) -> None:
    reports = tmp_path / "runs" / run_id / "reports"
    reports.mkdir(parents=True)
    (reports / "complete-manuscript-draft.md").write_text(
        complete_markdown,
        encoding="utf-8",
    )
    if revised_markdown is not None:
        (reports / "revised-manuscript-draft.md").write_text(
            revised_markdown,
            encoding="utf-8",
        )


def _short_placeholder_markdown() -> str:
    sections = "\n".join(
        f"## Section {index}\nPlaceholder text for section {index}."
        for index in range(1, 15)
    )
    return f"# Deterministic Branch Manuscript Plan\n\n{sections}\n"


def _acceptable_markdown(*, include_citation: bool) -> str:
    citation = " [@Smith2024]" if include_citation else ""
    sections = [
        ("Abstract", "This abstract summarizes the bounded argument"),
        (
            "Introduction",
            "This introduction gives problem framing for the research problem"
            f"{citation}",
        ),
        ("Problem Framing", "The problem statement explains why the setting matters"),
        ("Method Summary", "The method summary describes the model and approach"),
        ("Results", "The results section reports only supported internal findings"),
        ("Limitations", "The limitations section keeps the scope bounded"),
        (
            "Claim/Evidence Appendix",
            "The claim/evidence appendix links claims to available artifacts",
        ),
        (
            "Provenance Appendix",
            "The provenance appendix records artifact and run context",
        ),
    ]
    body = "\n\n".join(
        f"## {heading}\n{_repeated_quality_paragraph(seed)}"
        for heading, seed in sections
    )
    return f"# Bounded Transport Calibration\n\n{body}\n"


def _repeated_quality_paragraph(seed: str) -> str:
    sentence = (
        f"{seed} while preserving evidence boundaries, citation safety, "
        "problem framing, method summary, limitations, claim evidence traceability, "
        "and provenance context for human review only."
    )
    return " ".join(sentence for _ in range(8))
