from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from factori.abstract_synthesis import run_abstract_synthesis
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.draft_skeleton import run_draft_skeleton_generation
from factori.export_plan import prepare_export
from factori.final_audit import run_final_audit
from factori.final_paper import run_paper_assembly
from factori.hashing import sha256_file
from factori.ledger import ResearchLedger
from factori.manuscript_plan import run_manuscript_planning
from factori.replay import ReplayVerificationError, replay_verify_run, write_replay_report
from factori.research_object import build_research_object
from factori.schemas import ConstraintSet, ReplayStatus
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_selection import run_stage_c_selection


def test_replay_verify_errors_without_export_preparation(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=ArtifactStore(tmp_path),
        ledger=ledger,
    )

    with pytest.raises(ReplayVerificationError, match="Export preparation artifacts not found"):
        replay_verify_run("run-1", root=tmp_path)


def test_cli_replay_verify_errors_without_export_preparation(tmp_path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["replay-verify", "--root", str(tmp_path), "--run-id", "run-1"],
    )

    assert result.exit_code == 1
    assert "Export preparation artifacts not found" in result.stderr


def test_cli_replay_verify_works_after_full_flow(tmp_path) -> None:
    _run_pipeline_to_export(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["replay-verify", "--root", str(tmp_path), "--run-id", "run-1"],
    )

    assert result.exit_code == 0
    assert "run_id=run-1" in result.output
    assert "ledger_mutated=false" in result.output
    assert "artifact_manifest_mutated=false" in result.output
    assert "replay_status=" in result.output


def test_cli_replay_verify_write_report_is_outside_provenance(tmp_path) -> None:
    _, ledger = _run_pipeline_to_export(tmp_path)
    manifest_path = tmp_path / "runs" / "run-1" / "research_object" / "artifact-manifest.json"
    before_commits = len(ledger.list_commits("run-1"))
    before_manifest_hash = sha256_file(manifest_path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "replay-verify",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--write-report",
        ],
    )

    assert result.exit_code == 0
    assert len(ledger.list_commits("run-1")) == before_commits
    assert sha256_file(manifest_path) == before_manifest_hash
    json_path = tmp_path / "runs" / "run-1" / "replay" / "replay-verification-report.json"
    markdown_path = tmp_path / "runs" / "run-1" / "replay" / "replay-verification-report.md"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert payload["not_provenance"] is True
    assert payload["not_evidence"] is True
    assert payload["not_ledgered"] is True
    assert "not_provenance: true" in markdown
    assert "not_evidence: true" in markdown
    assert "not_ledgered: true" in markdown


def test_replay_write_report_helper_returns_paths(tmp_path) -> None:
    _run_pipeline_to_export(tmp_path)
    report = replay_verify_run("run-1", root=tmp_path)

    json_path, markdown_path = write_replay_report(
        run_id="run-1",
        report=report,
        root=tmp_path,
    )

    assert json_path.is_file()
    assert markdown_path.is_file()
    assert report.replay_status in {
        ReplayStatus.REPLAY_VERIFIED,
        ReplayStatus.REPLAY_VERIFIED_WITH_WARNINGS,
    }


def _run_pipeline_to_export(tmp_path) -> tuple[ArtifactStore, ResearchLedger]:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )
    run_stage_b(run_id="run-1", store=store, ledger=ledger)
    run_stage_c_selection(run_id="run-1", store=store, ledger=ledger)
    run_stage_c(run_id="run-1", store=store, ledger=ledger)
    run_abstract_synthesis(run_id="run-1", store=store, ledger=ledger)
    run_manuscript_planning(run_id="run-1", store=store, ledger=ledger)
    run_draft_skeleton_generation(run_id="run-1", store=store, ledger=ledger)
    build_research_object(run_id="run-1", store=store, ledger=ledger)
    run_paper_assembly(run_id="run-1", store=store, ledger=ledger)
    run_final_audit(run_id="run-1", store=store, ledger=ledger)
    prepare_export(run_id="run-1", store=store, ledger=ledger)
    return store, ledger
