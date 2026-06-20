from __future__ import annotations

from factori.dry_run import validate_dry_run_plan
from factori.pipeline import PIPELINE_STAGE_ORDER
from factori.pipeline_plan import (
    all_supported_stage_names,
    expected_outputs_for_stage,
    expected_pipeline_report_outputs,
)
from factori.schemas import (
    DiagnosticSeverity,
    DryRunStatus,
    PipelineDryRunPlan,
    PipelineStage,
    PlannedStage,
    PlannedStageStatus,
)


def test_expected_outputs_are_deterministic() -> None:
    first = [
        output.model_dump(mode="json")
        for output in expected_outputs_for_stage(PipelineStage.RUN_STAGE_C, "run-1")
    ]
    second = [
        output.model_dump(mode="json")
        for output in expected_outputs_for_stage(PipelineStage.RUN_STAGE_C, "run-1")
    ]

    assert first == second
    assert first[0]["output_kind"] == "stage_c_verification_report"
    assert first[0]["path"] == "runs/run-1/reports/stage-c-verification-report.md"


def test_optional_replay_outputs_reflect_write_report_flag() -> None:
    without_report = expected_outputs_for_stage(PipelineStage.REPLAY_VERIFY, "run-1")
    with_report = expected_outputs_for_stage(
        PipelineStage.REPLAY_VERIFY,
        "run-1",
        write_replay_report=True,
    )

    assert without_report[0].output_kind == "replay_status"
    assert without_report[0].path is None
    assert [output.output_kind for output in with_report] == [
        "replay_verification_report",
        "replay_verification_report_markdown",
    ]


def test_pipeline_report_outputs_are_deterministic() -> None:
    outputs = expected_pipeline_report_outputs("run-1")

    assert [output.output_kind for output in outputs] == [
        "pipeline_run_report",
        "pipeline_run_report_markdown",
    ]
    assert outputs[0].path == "runs/run-1/reports/pipeline-run-report.json"


def test_supported_stage_names_match_pipeline_order() -> None:
    assert all_supported_stage_names() == [stage.value for stage in PIPELINE_STAGE_ORDER]


def test_validate_dry_run_plan_flags_invalid_stage_names() -> None:
    plan = PipelineDryRunPlan(
        run_id="run-1",
        dry_run_status=DryRunStatus.DRY_RUN_RUNNABLE,
        planned_stages=[
            PlannedStage(
                stage_name="not-a-stage",
                status=PlannedStageStatus.WOULD_RUN,
                reason="Invalid test stage.",
            )
        ],
        warnings_count=0,
        blocking_findings_count=0,
    )

    findings = validate_dry_run_plan(plan)

    assert findings[0].severity == DiagnosticSeverity.BLOCKING
    assert findings[0].blocking is True
    assert findings[0].finding_id == "invalid-stage-name-not-a-stage"


def test_validate_dry_run_plan_flags_unknown_stage_ordering() -> None:
    plan = PipelineDryRunPlan(
        run_id="run-1",
        dry_run_status=DryRunStatus.DRY_RUN_RUNNABLE,
        planned_stages=[
            PlannedStage(
                stage_name=PipelineStage.RUN_STAGE_B.value,
                status=PlannedStageStatus.WOULD_RUN,
                reason="Out of order.",
            ),
            PlannedStage(
                stage_name=PipelineStage.RUN_STAGE_A.value,
                status=PlannedStageStatus.WOULD_RUN,
                reason="Out of order.",
            ),
        ],
        warnings_count=0,
        blocking_findings_count=0,
    )

    finding_ids = {finding.finding_id for finding in validate_dry_run_plan(plan)}

    assert "unknown-stage-ordering" in finding_ids
