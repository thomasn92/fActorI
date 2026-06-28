from __future__ import annotations

import json

from typer.testing import CliRunner

from factori.adapters.fake import FakeProseGenerator
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.full_paper_generation import (
    generate_full_paper,
    inspect_paper_bundle_summary,
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
