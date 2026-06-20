from __future__ import annotations

import pytest

from factori.pipeline import (
    PIPELINE_STAGE_ORDER,
    PipelineConfigurationError,
    pipeline_status_for_results,
    selected_pipeline_stages,
    stage_is_read_only,
)
from factori.schemas import (
    PipelineRunConfig,
    PipelineRunStatus,
    PipelineStage,
    PipelineStageResult,
)


def test_default_pipeline_stage_order() -> None:
    stages = selected_pipeline_stages(
        PipelineRunConfig(run_id="run-1", domain="human geography")
    )

    assert stages == PIPELINE_STAGE_ORDER[:-1]
    assert stages[0] == PipelineStage.RUN_STAGE_A
    assert stages[-1] == PipelineStage.REPLAY_VERIFY


def test_optional_stage_flags_are_deterministic() -> None:
    config = PipelineRunConfig(
        run_id="run-1",
        domain="human geography",
        skip_replay=True,
        run_diagnostics=True,
    )

    assert selected_pipeline_stages(config)[-1] == PipelineStage.DIAGNOSE_RUN
    assert PipelineStage.REPLAY_VERIFY not in selected_pipeline_stages(config)


def test_start_and_stop_select_a_contiguous_stage_range() -> None:
    config = PipelineRunConfig(
        run_id="run-1",
        domain="human geography",
        start_at=PipelineStage.PLAN_MANUSCRIPT,
        stop_after=PipelineStage.FINAL_AUDIT,
    )

    assert selected_pipeline_stages(config) == [
        PipelineStage.PLAN_MANUSCRIPT,
        PipelineStage.BUILD_DRAFT_SKELETON,
        PipelineStage.PACKAGE_RESEARCH_OBJECT,
        PipelineStage.ASSEMBLE_PAPER_SKELETON,
        PipelineStage.FINAL_AUDIT,
    ]


def test_invalid_start_after_stop_is_rejected() -> None:
    config = PipelineRunConfig(
        run_id="run-1",
        domain="human geography",
        start_at=PipelineStage.FINAL_AUDIT,
        stop_after=PipelineStage.RUN_STAGE_C,
    )

    with pytest.raises(PipelineConfigurationError, match="must not follow"):
        selected_pipeline_stages(config)


def test_disabled_optional_stage_cannot_be_selected() -> None:
    config = PipelineRunConfig(
        run_id="run-1",
        domain="human geography",
        skip_replay=True,
        stop_after=PipelineStage.REPLAY_VERIFY,
    )

    with pytest.raises(PipelineConfigurationError, match="disabled"):
        selected_pipeline_stages(config)


def test_only_replay_and_diagnostics_are_read_only() -> None:
    assert stage_is_read_only(PipelineStage.REPLAY_VERIFY)
    assert stage_is_read_only(PipelineStage.DIAGNOSE_RUN)
    assert not stage_is_read_only(PipelineStage.RUN_STAGE_A)
    assert not stage_is_read_only(PipelineStage.PREPARE_EXPORT)


def test_pipeline_status_precedence_is_deterministic() -> None:
    succeeded = _stage_result(PipelineRunStatus.PIPELINE_SUCCEEDED)
    warning = _stage_result(PipelineRunStatus.PIPELINE_SUCCEEDED_WITH_WARNINGS)
    blocked = _stage_result(PipelineRunStatus.PIPELINE_BLOCKED)
    failed = _stage_result(PipelineRunStatus.PIPELINE_FAILED)

    assert pipeline_status_for_results([succeeded], []) == (
        PipelineRunStatus.PIPELINE_SUCCEEDED
    )
    assert pipeline_status_for_results([succeeded], ["warning"]) == (
        PipelineRunStatus.PIPELINE_SUCCEEDED_WITH_WARNINGS
    )
    assert pipeline_status_for_results([succeeded, warning], []) == (
        PipelineRunStatus.PIPELINE_SUCCEEDED_WITH_WARNINGS
    )
    assert pipeline_status_for_results([blocked, warning], []) == (
        PipelineRunStatus.PIPELINE_BLOCKED
    )
    assert pipeline_status_for_results([blocked, failed], []) == (
        PipelineRunStatus.PIPELINE_FAILED
    )


def _stage_result(status: PipelineRunStatus) -> PipelineStageResult:
    return PipelineStageResult(
        stage_name=PipelineStage.RUN_STAGE_A,
        started_at="1970-01-01T00:00:00Z",
        finished_at="1970-01-01T00:00:00Z",
        status=status,
    )
