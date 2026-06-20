from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from factori.cli import app
from factori.hashing import sha256_file
from factori.ledger import ResearchLedger
from factori.run_all import PipelineRunError, run_deterministic_pipeline
from factori.schemas import (
    ArtifactRef,
    ControllerActionType,
    PipelineFailurePolicy,
    PipelineRunConfig,
    PipelineRunReport,
    PipelineRunStatus,
    PipelineStage,
    ReleaseGateStatus,
    ReplayStatus,
    VerificationLabel,
)


def test_full_pipeline_creates_final_outputs_and_ledgered_report(tmp_path) -> None:
    report = run_deterministic_pipeline(_config(tmp_path))
    run_path = tmp_path / "runs" / "run-1"
    json_path = run_path / "reports" / "pipeline-run-report.json"
    markdown_path = run_path / "reports" / "pipeline-run-report.md"
    ledger = ResearchLedger(run_path / "ledger.sqlite")

    assert report.pipeline_status == PipelineRunStatus.PIPELINE_SUCCEEDED
    assert report.release_status == ReleaseGateStatus.RELEASE_READY
    assert report.replay_status == ReplayStatus.REPLAY_VERIFIED
    assert len(report.stage_results) == 12
    assert json_path.is_file()
    assert markdown_path.is_file()
    assert (run_path / "research_object" / "research-object.json").is_file()
    assert (run_path / "research_object" / "paper-skeleton.json").is_file()
    assert (run_path / "reports" / "export-readiness-report.json").is_file()
    assert "not verification evidence" in markdown_path.read_text(encoding="utf-8")

    stored = PipelineRunReport.model_validate_json(json_path.read_text(encoding="utf-8"))
    assert stored.pipeline_status == report.pipeline_status
    assert sha256_file(json_path)
    json_meta = ArtifactRef.model_validate_json(
        (run_path / "reports" / "pipeline-run-report.json.meta.json").read_text(
            encoding="utf-8"
        )
    )
    markdown_meta = ArtifactRef.model_validate_json(
        (run_path / "reports" / "pipeline-run-report.md.meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert json_meta.producing_commit_hash
    assert markdown_meta.producing_commit_hash == json_meta.producing_commit_hash
    assert ledger.list_commits("run-1")[-1].action_type == (
        ControllerActionType.PIPELINE_RUN_REPORT_WRITTEN
    )
    assert any(
        commit.action_type == ControllerActionType.STAGE_A_CANDIDATE_GENERATED
        for commit in ledger.list_commits("run-1")
    )
    assert all(
        commit.payload.get("label") != VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED.value
        for commit in ledger.list_commits("run-1")
    )


def test_replay_and_diagnostics_remain_read_only_with_optional_reports(tmp_path) -> None:
    report = run_deterministic_pipeline(
        _config(
            tmp_path,
            run_diagnostics=True,
            write_replay_report=True,
            write_diagnostic_report=True,
        )
    )
    stages = {result.stage_name: result for result in report.stage_results}
    replay = stages[PipelineStage.REPLAY_VERIFY]
    diagnostics = stages[PipelineStage.DIAGNOSE_RUN]
    run_path = tmp_path / "runs" / "run-1"

    assert replay.summary["ledger_commits_before"] == replay.summary["ledger_commits_after"]
    assert diagnostics.summary["ledger_commits_before"] == (
        diagnostics.summary["ledger_commits_after"]
    )
    replay_payload = json.loads(
        (run_path / "replay" / "replay-verification-report.json").read_text(
            encoding="utf-8"
        )
    )
    diagnostic_payload = json.loads(
        (run_path / "diagnostics" / "diagnostic-report.json").read_text(
            encoding="utf-8"
        )
    )
    for payload in (replay_payload, diagnostic_payload):
        assert payload["not_provenance"] is True
        assert payload["not_evidence"] is True
        assert payload["not_ledgered"] is True
    assert not (run_path / "replay" / "replay-verification-report.json.meta.json").exists()
    assert not (run_path / "diagnostics" / "diagnostic-report.json.meta.json").exists()


def test_skip_replay_and_stop_after_stage_c(tmp_path) -> None:
    report = run_deterministic_pipeline(
        _config(
            tmp_path,
            skip_replay=True,
            stop_after=PipelineStage.RUN_STAGE_C,
        )
    )

    assert [result.stage_name for result in report.stage_results] == [
        PipelineStage.RUN_STAGE_A,
        PipelineStage.RUN_STAGE_B,
        PipelineStage.SELECT_STAGE_C,
        PipelineStage.RUN_STAGE_C,
    ]
    assert report.replay_status is None
    assert not (
        tmp_path / "runs" / "run-1" / "research_object" / "research-object.json"
    ).exists()


def test_start_at_plan_manuscript_resumes_from_existing_artifacts(tmp_path) -> None:
    first = run_deterministic_pipeline(
        _config(tmp_path, stop_after=PipelineStage.SYNTHESIZE_ABSTRACT)
    )
    second = run_deterministic_pipeline(
        _config(tmp_path, start_at=PipelineStage.PLAN_MANUSCRIPT)
    )

    assert first.stage_results[-1].stage_name == PipelineStage.SYNTHESIZE_ABSTRACT
    assert second.stage_results[0].stage_name == PipelineStage.PLAN_MANUSCRIPT
    assert second.replay_status in {
        ReplayStatus.REPLAY_VERIFIED,
        ReplayStatus.REPLAY_VERIFIED_WITH_WARNINGS,
    }
    assert (tmp_path / "runs" / "run-1" / "research_object" / "paper-skeleton.md").is_file()


def test_start_at_requires_prior_run_artifacts(tmp_path) -> None:
    with pytest.raises(PipelineRunError, match="run ledger does not exist"):
        run_deterministic_pipeline(
            _config(tmp_path, start_at=PipelineStage.PLAN_MANUSCRIPT)
        )


def test_repeated_full_run_fails_clearly(tmp_path) -> None:
    run_deterministic_pipeline(
        _config(tmp_path, stop_after=PipelineStage.RUN_STAGE_A)
    )

    with pytest.raises(PipelineRunError, match="Run already exists"):
        run_deterministic_pipeline(_config(tmp_path))


def test_fail_fast_stops_on_first_failed_stage(tmp_path, monkeypatch) -> None:
    def fail_stage_b(**_kwargs: object) -> None:
        raise RuntimeError("forced stage-b failure")

    monkeypatch.setattr("factori.run_all.run_stage_b", fail_stage_b)
    report = run_deterministic_pipeline(
        _config(tmp_path, failure_policy=PipelineFailurePolicy.FAIL_FAST)
    )

    assert [result.stage_name for result in report.stage_results] == [
        PipelineStage.RUN_STAGE_A,
        PipelineStage.RUN_STAGE_B,
    ]
    assert report.pipeline_status == PipelineRunStatus.PIPELINE_FAILED
    assert report.blocking_stage == PipelineStage.RUN_STAGE_B
    assert "forced stage-b failure" in (report.stage_results[-1].error_message or "")


def test_cli_run_all_works_with_root_and_domain(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-all",
            "--run-id",
            "cli-run",
            "--domain",
            "human geography",
            "--root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "pipeline_status=PipelineSucceeded" in result.output
    assert "release_status=ReleaseReady" in result.output
    assert "replay_status=ReplayVerified" in result.output
    assert "pipeline_report=runs/cli-run/reports/pipeline-run-report.md" in result.output


def _config(tmp_path, **updates: object) -> PipelineRunConfig:
    values: dict[str, object] = {
        "run_id": "run-1",
        "domain": "human geography",
        "root": tmp_path,
    }
    values.update(updates)
    return PipelineRunConfig(**values)
