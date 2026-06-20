"""Stage ordering and status helpers for deterministic direct orchestration."""

from __future__ import annotations

from factori.schemas import (
    PipelineRunConfig,
    PipelineRunStatus,
    PipelineStage,
    PipelineStageResult,
)


class PipelineConfigurationError(ValueError):
    """Raised when start, stop, or optional-stage settings conflict."""


PIPELINE_STAGE_ORDER = [
    PipelineStage.RUN_STAGE_A,
    PipelineStage.RUN_STAGE_B,
    PipelineStage.SELECT_STAGE_C,
    PipelineStage.RUN_STAGE_C,
    PipelineStage.SYNTHESIZE_ABSTRACT,
    PipelineStage.PLAN_MANUSCRIPT,
    PipelineStage.BUILD_DRAFT_SKELETON,
    PipelineStage.PACKAGE_RESEARCH_OBJECT,
    PipelineStage.ASSEMBLE_PAPER_SKELETON,
    PipelineStage.FINAL_AUDIT,
    PipelineStage.PREPARE_EXPORT,
    PipelineStage.REPLAY_VERIFY,
    PipelineStage.DIAGNOSE_RUN,
]

READ_ONLY_STAGES = {
    PipelineStage.REPLAY_VERIFY,
    PipelineStage.DIAGNOSE_RUN,
}


def selected_pipeline_stages(config: PipelineRunConfig) -> list[PipelineStage]:
    """Return the validated ordered stages selected by a pipeline config."""
    stages = list(PIPELINE_STAGE_ORDER)
    if not config.run_diagnostics:
        stages.remove(PipelineStage.DIAGNOSE_RUN)
    if config.skip_replay:
        stages.remove(PipelineStage.REPLAY_VERIFY)

    if config.start_at is not None and config.start_at not in stages:
        raise PipelineConfigurationError(
            f"start-at stage is disabled by current flags: {config.start_at.value}"
        )
    if config.stop_after is not None and config.stop_after not in stages:
        raise PipelineConfigurationError(
            f"stop-after stage is disabled by current flags: {config.stop_after.value}"
        )

    start = stages.index(config.start_at) if config.start_at is not None else 0
    stop = stages.index(config.stop_after) if config.stop_after is not None else len(stages) - 1
    if start > stop:
        raise PipelineConfigurationError("start-at stage must not follow stop-after stage")
    return stages[start : stop + 1]


def stage_is_read_only(stage: PipelineStage) -> bool:
    """Return whether a stage must not append ledger commits."""
    return stage in READ_ONLY_STAGES


def pipeline_status_for_results(
    results: list[PipelineStageResult],
    warnings: list[str],
) -> PipelineRunStatus:
    """Derive the deterministic overall pipeline status."""
    if any(result.status == PipelineRunStatus.PIPELINE_FAILED for result in results):
        return PipelineRunStatus.PIPELINE_FAILED
    if any(result.status == PipelineRunStatus.PIPELINE_BLOCKED for result in results):
        return PipelineRunStatus.PIPELINE_BLOCKED
    if warnings or any(
        result.status == PipelineRunStatus.PIPELINE_SUCCEEDED_WITH_WARNINGS
        for result in results
    ):
        return PipelineRunStatus.PIPELINE_SUCCEEDED_WITH_WARNINGS
    return PipelineRunStatus.PIPELINE_SUCCEEDED
