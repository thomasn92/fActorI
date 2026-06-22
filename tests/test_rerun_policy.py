from __future__ import annotations

import sqlite3

import pytest
from typer.testing import CliRunner

from factori.cli import app
from factori.ledger import ResearchLedger
from factori.rerun_policy import decide_stage_rerun, validate_ledger_tip
from factori.run_all import PipelineRunError, run_deterministic_pipeline
from factori.schemas import (
    ControllerActionType,
    LedgerTipStatus,
    PipelineRunConfig,
    PipelineStage,
    RerunPolicy,
    RunCompletenessStatus,
    StageRerunStatus,
)
from factori.status import inspect_run_status


def test_default_rerun_policy_is_fail_if_exists(tmp_path) -> None:
    config = PipelineRunConfig(run_id="run-1", domain="machine learning", root=tmp_path)

    assert config.rerun_policy == RerunPolicy.FAIL_IF_EXISTS


def test_read_only_stage_is_always_allowed(tmp_path) -> None:
    status = inspect_run_status("run-1", tmp_path)

    decision = decide_stage_rerun(
        "run-1",
        PipelineStage.REPLAY_VERIFY,
        RerunPolicy.READ_ONLY_ONLY,
        status,
        root=tmp_path,
    )

    assert decision.status == StageRerunStatus.READ_ONLY_ALLOWED
    assert decision.should_run is True


def test_completed_stage_policy_decisions_are_explicit(tmp_path) -> None:
    status = _completed_stage_a_status(tmp_path)

    blocked = decide_stage_rerun(
        "run-1",
        PipelineStage.RUN_STAGE_A,
        RerunPolicy.FAIL_IF_EXISTS,
        status,
        root=tmp_path,
    )
    skipped = decide_stage_rerun(
        "run-1",
        PipelineStage.RUN_STAGE_A,
        RerunPolicy.SKIP_IF_COMPLETE,
        status,
        root=tmp_path,
    )
    force_required = decide_stage_rerun(
        "run-1",
        PipelineStage.RUN_STAGE_A,
        RerunPolicy.ALLOW_IF_FORCED,
        status,
        root=tmp_path,
    )
    forced = decide_stage_rerun(
        "run-1",
        PipelineStage.RUN_STAGE_A,
        RerunPolicy.ALLOW_IF_FORCED,
        status,
        force=True,
        root=tmp_path,
    )

    assert blocked.status == StageRerunStatus.BLOCKED_ALREADY_COMPLETE
    assert skipped.status == StageRerunStatus.SKIPPED_ALREADY_COMPLETE
    assert skipped.should_skip is True
    assert force_required.status == StageRerunStatus.BLOCKED_ALREADY_COMPLETE
    assert forced.status == StageRerunStatus.ALLOWED_FORCED
    assert forced.should_run is True


def test_inconsistent_run_blocks_mutating_stage(tmp_path) -> None:
    status = _completed_stage_a_status(tmp_path)
    inconsistent = status.model_copy(
        update={"completeness_status": RunCompletenessStatus.INCONSISTENT_RUN}
    )

    decision = decide_stage_rerun(
        "run-1",
        PipelineStage.RUN_STAGE_B,
        RerunPolicy.FAIL_IF_EXISTS,
        inconsistent,
        root=tmp_path,
    )

    assert decision.status == StageRerunStatus.BLOCKED_INCONSISTENT
    assert decision.should_run is False


def test_run_all_blocks_rerun_and_can_skip_completed_stage(tmp_path) -> None:
    first = _pipeline_config(tmp_path, stop_after=PipelineStage.RUN_STAGE_A)
    run_deterministic_pipeline(first)

    with pytest.raises(PipelineRunError, match="Stage is already complete"):
        run_deterministic_pipeline(_pipeline_config(tmp_path))

    resumed = run_deterministic_pipeline(
        _pipeline_config(
            tmp_path,
            stop_after=PipelineStage.RUN_STAGE_B,
            rerun_policy=RerunPolicy.SKIP_IF_COMPLETE,
        )
    )

    assert resumed.stage_results[0].stage_name == PipelineStage.RUN_STAGE_A
    assert resumed.stage_results[0].summary["rerun_status"] == (
        StageRerunStatus.SKIPPED_ALREADY_COMPLETE.value
    )
    assert resumed.stage_results[1].stage_name == PipelineStage.RUN_STAGE_B


def test_per_stage_cli_blocks_completed_stage_a_and_b(tmp_path) -> None:
    runner = CliRunner()
    stage_a = [
        "run-stage-a",
        "--run-id",
        "run-1",
        "--domain",
        "machine learning",
        "--root",
        str(tmp_path),
    ]
    stage_b = ["run-stage-b", "--run-id", "run-1", "--root", str(tmp_path)]

    assert runner.invoke(app, stage_a).exit_code == 0
    assert runner.invoke(app, stage_b).exit_code == 0
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    commits_before = len(ledger.list_commits("run-1"))

    repeated_a = runner.invoke(app, stage_a)
    repeated_b = runner.invoke(app, stage_b)

    assert repeated_a.exit_code == 1
    assert repeated_b.exit_code == 1
    assert "BlockedAlreadyComplete" in repeated_a.output
    assert "BlockedAlreadyComplete" in repeated_b.output
    assert len(ledger.list_commits("run-1")) == commits_before


def test_ledger_tip_validation_passes_for_linear_run(tmp_path) -> None:
    run_deterministic_pipeline(_pipeline_config(tmp_path, stop_after=PipelineStage.RUN_STAGE_A))

    report = validate_ledger_tip("run-1", root=tmp_path)

    assert report.status == LedgerTipStatus.VALID
    assert len(report.tip_hashes) == 1
    assert report.blocking_findings == []


def test_ledger_tip_validation_detects_broken_parent(tmp_path) -> None:
    ledger = _linear_ledger(tmp_path)
    with sqlite3.connect(ledger.path) as connection:
        connection.execute(
            """
            INSERT INTO commits (
                commit_hash, parent_hash, run_id, candidate_id, action_type,
                payload_json, artifact_refs_json, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "e" * 64,
                "f" * 64,
                "run-1",
                None,
                ControllerActionType.STAGE_B_STARTED.value,
                "{}",
                "[]",
                "2024-01-01T00:00:02Z",
            ),
        )

    report = validate_ledger_tip("run-1", root=tmp_path)

    assert report.status == LedgerTipStatus.INVALID
    assert any(finding.finding_type == "BrokenParentLink" for finding in report.blocking_findings)


def test_ledger_tip_validation_detects_fork_and_multiple_tips(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    root = ledger.append_commit(
        run_id="run-1",
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": "run-1"},
        timestamp="2024-01-01T00:00:00Z",
    )
    ledger.append_commit(
        run_id="run-1",
        parent_hash=root.commit_hash,
        action_type=ControllerActionType.STAGE_A_STARTED,
        payload={"attempt": 1},
        timestamp="2024-01-01T00:00:01Z",
    )
    ledger.append_commit(
        run_id="run-1",
        parent_hash=root.commit_hash,
        action_type=ControllerActionType.STAGE_B_STARTED,
        payload={"attempt": 1},
        timestamp="2024-01-01T00:00:02Z",
    )

    report = validate_ledger_tip("run-1", root=tmp_path)

    assert report.status == LedgerTipStatus.INVALID
    assert len(report.tip_hashes) == 2
    finding_types = {finding.finding_type for finding in report.branch_findings}
    assert "ForkedParent" in finding_types
    assert "MultipleTips" in finding_types


def test_ledger_tip_validation_detects_duplicate_mutating_stage(tmp_path) -> None:
    ledger = _linear_ledger(tmp_path)
    parent = ledger.latest_commit_hash("run-1")
    ledger.append_commit(
        run_id="run-1",
        parent_hash=parent,
        action_type=ControllerActionType.STAGE_A_STARTED,
        payload={"attempt": 2},
        timestamp="2024-01-01T00:00:02Z",
    )

    report = validate_ledger_tip("run-1", root=tmp_path)

    assert report.status == LedgerTipStatus.INVALID
    assert len(report.duplicate_stage_findings) == 1
    assert report.duplicate_stage_findings[0].stage_name == PipelineStage.RUN_STAGE_A


def test_validate_ledger_tip_cli_is_read_only(tmp_path) -> None:
    ledger = _linear_ledger(tmp_path)
    commits_before = len(ledger.list_commits("run-1"))

    result = CliRunner().invoke(
        app,
        ["validate-ledger-tip", "--run-id", "run-1", "--root", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert "ledger_tip_status=Valid" in result.output
    assert len(ledger.list_commits("run-1")) == commits_before


def _completed_stage_a_status(tmp_path):
    run_path = tmp_path / "runs" / "run-1"
    report = run_path / "reports" / "stage-a-report.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Stage A\n", encoding="utf-8", newline="\n")
    ledger = ResearchLedger(run_path / "ledger.sqlite")
    ledger.append_commit(
        run_id="run-1",
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": "run-1"},
        timestamp="2024-01-01T00:00:00Z",
    )
    return inspect_run_status("run-1", tmp_path)


def _linear_ledger(tmp_path) -> ResearchLedger:
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    root = ledger.append_commit(
        run_id="run-1",
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": "run-1"},
        timestamp="2024-01-01T00:00:00Z",
    )
    ledger.append_commit(
        run_id="run-1",
        parent_hash=root.commit_hash,
        action_type=ControllerActionType.STAGE_A_STARTED,
        payload={"attempt": 1},
        timestamp="2024-01-01T00:00:01Z",
    )
    return ledger


def _pipeline_config(tmp_path, **updates: object) -> PipelineRunConfig:
    values: dict[str, object] = {
        "run_id": "run-1",
        "domain": "machine learning",
        "root": tmp_path,
    }
    values.update(updates)
    return PipelineRunConfig(**values)
