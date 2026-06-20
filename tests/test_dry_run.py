from __future__ import annotations

import json

from typer.testing import CliRunner

from factori.cli import app
from factori.dry_run import build_pipeline_dry_run_plan
from factori.ledger import ResearchLedger
from factori.pipeline import selected_pipeline_stages
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    DryRunStatus,
    PipelineFailurePolicy,
    PipelineRunConfig,
    PipelineStage,
    PlannedStageStatus,
)


def test_dry_run_with_no_existing_run_plans_full_pipeline(tmp_path) -> None:
    config = _config(tmp_path)
    plan = build_pipeline_dry_run_plan(config)

    assert plan.dry_run_status == DryRunStatus.DRY_RUN_RUNNABLE
    assert plan.selected_stages == selected_pipeline_stages(config)
    assert _status(plan, PipelineStage.RUN_STAGE_A) == PlannedStageStatus.WOULD_RUN
    assert _status(plan, PipelineStage.REPLAY_VERIFY) == PlannedStageStatus.READ_ONLY_CHECK
    assert _status(plan, PipelineStage.DIAGNOSE_RUN) == PlannedStageStatus.WOULD_SKIP
    assert plan.next_stage == PipelineStage.RUN_STAGE_A


def test_dry_run_stop_after_stage_c_limits_selected_run_range(tmp_path) -> None:
    plan = build_pipeline_dry_run_plan(
        _config(tmp_path, stop_after=PipelineStage.RUN_STAGE_C)
    )

    assert plan.selected_stages == [
        PipelineStage.RUN_STAGE_A,
        PipelineStage.RUN_STAGE_B,
        PipelineStage.SELECT_STAGE_C,
        PipelineStage.RUN_STAGE_C,
    ]
    assert _status(plan, PipelineStage.SYNTHESIZE_ABSTRACT) == (
        PlannedStageStatus.BLOCKED_BY_STOP_AFTER
    )


def test_dry_run_start_at_plan_manuscript_validates_prerequisites(tmp_path) -> None:
    _init_empty_run(tmp_path)

    plan = build_pipeline_dry_run_plan(
        _config(tmp_path, start_at=PipelineStage.PLAN_MANUSCRIPT)
    )

    assert plan.dry_run_status == DryRunStatus.DRY_RUN_BLOCKED
    assert _status(plan, PipelineStage.PLAN_MANUSCRIPT) == (
        PlannedStageStatus.BLOCKED_BY_PREREQUISITE
    )
    assert plan.resume_validation is not None
    assert plan.resume_validation.missing_prerequisites


def test_dry_run_allows_start_at_plan_manuscript_when_prerequisites_exist(tmp_path) -> None:
    run_deterministic_pipeline(
        _config(tmp_path, stop_after=PipelineStage.SYNTHESIZE_ABSTRACT)
    )

    plan = build_pipeline_dry_run_plan(
        _config(tmp_path, start_at=PipelineStage.PLAN_MANUSCRIPT)
    )

    assert plan.dry_run_status == DryRunStatus.DRY_RUN_RUNNABLE
    assert _status(plan, PipelineStage.SYNTHESIZE_ABSTRACT) == (
        PlannedStageStatus.ALREADY_COMPLETE
    )
    assert _status(plan, PipelineStage.PLAN_MANUSCRIPT) == PlannedStageStatus.WOULD_RUN


def test_dry_run_respects_skip_replay(tmp_path) -> None:
    plan = build_pipeline_dry_run_plan(_config(tmp_path, skip_replay=True))

    assert PipelineStage.REPLAY_VERIFY not in plan.selected_stages
    assert _status(plan, PipelineStage.REPLAY_VERIFY) == PlannedStageStatus.WOULD_SKIP


def test_dry_run_includes_diagnostics_only_when_requested(tmp_path) -> None:
    without_diagnostics = build_pipeline_dry_run_plan(_config(tmp_path))
    with_diagnostics = build_pipeline_dry_run_plan(_config(tmp_path, run_diagnostics=True))

    assert PipelineStage.DIAGNOSE_RUN not in without_diagnostics.selected_stages
    assert _status(without_diagnostics, PipelineStage.DIAGNOSE_RUN) == (
        PlannedStageStatus.WOULD_SKIP
    )
    assert PipelineStage.DIAGNOSE_RUN in with_diagnostics.selected_stages
    assert _status(with_diagnostics, PipelineStage.DIAGNOSE_RUN) == (
        PlannedStageStatus.READ_ONLY_CHECK
    )


def test_dry_run_flags_diagnostics_requested_without_prerequisite_outputs(tmp_path) -> None:
    _init_empty_run(tmp_path)

    plan = build_pipeline_dry_run_plan(
        _config(
            tmp_path,
            start_at=PipelineStage.DIAGNOSE_RUN,
            run_diagnostics=True,
            skip_replay=True,
        )
    )

    assert plan.dry_run_status == DryRunStatus.DRY_RUN_BLOCKED
    assert any(
        finding.finding_id == "diagnostics-missing-prerequisites"
        for finding in plan.validation_findings
    )


def test_dry_run_flags_write_replay_report_when_replay_is_skipped(tmp_path) -> None:
    plan = build_pipeline_dry_run_plan(
        _config(tmp_path, skip_replay=True, write_replay_report=True)
    )

    assert plan.dry_run_status == DryRunStatus.DRY_RUN_RUNNABLE_WITH_WARNINGS
    assert any(
        finding.finding_id == "write-replay-report-without-replay"
        for finding in plan.validation_findings
    )


def test_dry_run_flags_start_at_after_stop_after(tmp_path) -> None:
    plan = build_pipeline_dry_run_plan(
        _config(
            tmp_path,
            start_at=PipelineStage.FINAL_AUDIT,
            stop_after=PipelineStage.RUN_STAGE_C,
        )
    )

    assert plan.dry_run_status == DryRunStatus.DRY_RUN_INVALID
    assert any(
        finding.finding_id == "invalid-start-after-stop"
        for finding in plan.validation_findings
    )


def test_dry_run_requires_domain_if_stage_a_would_run(tmp_path) -> None:
    plan = build_pipeline_dry_run_plan(PipelineRunConfig(run_id="run-1", root=tmp_path))

    assert plan.dry_run_status == DryRunStatus.DRY_RUN_INVALID
    assert any(
        finding.finding_id == "missing-domain-for-stage-a"
        for finding in plan.validation_findings
    )


def test_planned_stage_order_matches_run_all_stage_order(tmp_path) -> None:
    plan = build_pipeline_dry_run_plan(_config(tmp_path, run_diagnostics=True))

    assert [stage.stage_name for stage in plan.planned_stages] == [
        stage.value for stage in PipelineStage
    ]


def test_expected_outputs_are_deterministic(tmp_path) -> None:
    first = build_pipeline_dry_run_plan(_config(tmp_path)).model_dump(mode="json")
    second = build_pipeline_dry_run_plan(_config(tmp_path)).model_dump(mode="json")

    assert first == second
    output_kinds = {output.output_kind for output in build_pipeline_dry_run_plan(
        _config(tmp_path)
    ).planned_outputs}
    assert "stage_a_report" in output_kinds
    assert "pipeline_run_report" in output_kinds


def test_dry_run_does_not_mutate_ledger_or_manifest(tmp_path) -> None:
    run_deterministic_pipeline(_config(tmp_path))
    run_path = tmp_path / "runs" / "run-1"
    ledger = ResearchLedger(run_path / "ledger.sqlite")
    before_commits = len(ledger.list_commits("run-1"))
    manifest_path = run_path / "research_object" / "artifact-manifest.json"
    before_manifest = manifest_path.read_text(encoding="utf-8")
    before_files = sorted(path.relative_to(run_path).as_posix() for path in run_path.rglob("*"))

    build_pipeline_dry_run_plan(
        _config(tmp_path, start_at=PipelineStage.PLAN_MANUSCRIPT)
    )

    after_files = sorted(path.relative_to(run_path).as_posix() for path in run_path.rglob("*"))
    assert len(ledger.list_commits("run-1")) == before_commits
    assert manifest_path.read_text(encoding="utf-8") == before_manifest
    assert after_files == before_files


def test_run_all_dry_run_cli_works(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-all",
            "--dry-run",
            "--run-id",
            "run-1",
            "--domain",
            "human geography",
            "--root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "dry_run_status=DryRunRunnable" in result.output
    assert "planned_stages=13" in result.output
    assert not (tmp_path / "runs").exists()


def test_plan_run_cli_works(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "plan-run",
            "--run-id",
            "run-1",
            "--domain",
            "human geography",
            "--root",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 0, result.output
    assert "dry_run_status=DryRunRunnable" in result.output


def test_dry_run_json_output_is_valid_json(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "plan-run",
            "--run-id",
            "run-1",
            "--domain",
            "human geography",
            "--root",
            str(tmp_path),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["run_id"] == "run-1"
    assert payload["dry_run_status"] == "DryRunRunnable"


def test_dry_run_introduces_no_real_external_actions(tmp_path) -> None:
    plan = build_pipeline_dry_run_plan(_config(tmp_path, run_diagnostics=True))
    serialized = json.dumps(plan.model_dump(mode="json")).lower()

    assert "openai" not in serialized
    assert "docker" not in serialized
    assert "real lean" not in serialized


def _status(plan, stage: PipelineStage) -> PlannedStageStatus:
    for planned in plan.planned_stages:
        if planned.stage_name == stage.value:
            return planned.status
    raise AssertionError(f"missing planned stage: {stage.value}")


def _config(tmp_path, **updates: object) -> PipelineRunConfig:
    values: dict[str, object] = {
        "run_id": "run-1",
        "domain": "human geography",
        "root": tmp_path,
    }
    values.update(updates)
    return PipelineRunConfig(**values)


def _init_empty_run(tmp_path) -> None:
    run_deterministic_pipeline(
        _config(
            tmp_path,
            stop_after=PipelineStage.RUN_STAGE_A,
            failure_policy=PipelineFailurePolicy.FAIL_FAST,
        )
    )
    (tmp_path / "runs" / "run-1" / "reports" / "stage-a-report.md").unlink()
