"""Read-only dry-run planner for deterministic run-all orchestration."""

from __future__ import annotations

from factori.checkpoints import get_stage_prerequisites, inspect_stage_checkpoint
from factori.pipeline import (
    PIPELINE_STAGE_ORDER,
    PipelineConfigurationError,
    selected_pipeline_stages,
    stage_is_read_only,
)
from factori.pipeline_plan import (
    all_supported_stage_names,
    expected_outputs_for_stage,
    expected_pipeline_report_outputs,
)
from factori.schemas import (
    DiagnosticSeverity,
    DryRunStatus,
    DryRunValidationFinding,
    PipelineDryRunPlan,
    PipelineRunConfig,
    PipelineStage,
    PlannedStage,
    PlannedStageStatus,
    ResumeValidationStatus,
)
from factori.status import inspect_run_status, validate_resume_request


def build_pipeline_dry_run_plan(config: PipelineRunConfig) -> PipelineDryRunPlan:
    """Build a read-only deterministic plan for what run-all would do."""
    run_status = inspect_run_status(config.run_id, config.root)
    selected: list[PipelineStage] = []
    selection_error: PipelineConfigurationError | None = None
    try:
        selected = selected_pipeline_stages(config)
    except PipelineConfigurationError as exc:
        selection_error = exc

    resume_validation = None
    initial_findings: list[DryRunValidationFinding] = []
    if selection_error is not None:
        initial_findings.append(
            _finding(
                "invalid-stage-selection",
                DiagnosticSeverity.BLOCKING,
                str(selection_error),
                blocking=True,
            )
        )
    if config.start_at is not None:
        resume_validation = validate_resume_request(
            run_id=config.run_id,
            start_at_stage=config.start_at,
            root=config.root,
        )
        if resume_validation.resume_status == ResumeValidationStatus.RESUME_BLOCKED:
            for index, issue in enumerate(resume_validation.blocking_issues):
                initial_findings.append(
                    _finding(
                        f"missing-prerequisite-{index + 1}",
                        DiagnosticSeverity.BLOCKING,
                        issue,
                        stage_name=config.start_at.value,
                        blocking=True,
                    )
                )
    elif run_status.ledger_commit_count > 0:
        initial_findings.append(
            _finding(
                "existing-run-without-start-at",
                DiagnosticSeverity.BLOCKING,
                f"Run already exists: {config.run_id}; use --start-at to resume",
                stage_name=PipelineStage.RUN_STAGE_A.value,
                blocking=True,
            )
        )

    planned_stages = _planned_stages(config, selected, selection_error is None)
    planned_outputs = _planned_outputs(config, planned_stages)
    preliminary = PipelineDryRunPlan(
        run_id=config.run_id,
        domain=config.domain,
        method=config.method,
        root=str(config.root),
        start_at=config.start_at,
        stop_after=config.stop_after,
        skip_replay=config.skip_replay,
        run_diagnostics=config.run_diagnostics,
        write_replay_report=config.write_replay_report,
        write_diagnostic_report=config.write_diagnostic_report,
        failure_policy=config.failure_policy,
        dry_run_status=DryRunStatus.DRY_RUN_RUNNABLE,
        planned_stages=planned_stages,
        planned_outputs=planned_outputs,
        validation_findings=initial_findings,
        run_status=run_status,
        resume_validation=resume_validation,
        next_stage=_next_stage(planned_stages),
        selected_stages=selected,
        warnings_count=0,
        blocking_findings_count=0,
    )
    findings = _dedupe_findings(
        [*initial_findings, *validate_dry_run_plan(preliminary)]
    )
    warnings = sum(1 for finding in findings if not finding.blocking)
    blocking = sum(1 for finding in findings if finding.blocking)
    return preliminary.model_copy(
        update={
            "dry_run_status": _dry_run_status(findings),
            "validation_findings": findings,
            "warnings_count": warnings,
            "blocking_findings_count": blocking,
        }
    )


def validate_dry_run_plan(plan: PipelineDryRunPlan) -> list[DryRunValidationFinding]:
    """Validate structural dry-run invariants without mutating provenance."""
    findings: list[DryRunValidationFinding] = []
    supported = set(all_supported_stage_names())
    stage_names = [stage.stage_name for stage in plan.planned_stages]
    invalid_stage_names = [stage for stage in stage_names if stage not in supported]
    for stage_name in invalid_stage_names:
        findings.append(
            _finding(
                f"invalid-stage-name-{stage_name}",
                DiagnosticSeverity.BLOCKING,
                f"Invalid pipeline stage name: {stage_name}",
                stage_name=stage_name,
                blocking=True,
            )
        )

    known_stage_names = [stage for stage in stage_names if stage in supported]
    canonical = [stage for stage in all_supported_stage_names() if stage in known_stage_names]
    if known_stage_names != canonical:
        findings.append(
            _finding(
                "unknown-stage-ordering",
                DiagnosticSeverity.BLOCKING,
                "Planned stages are not in canonical run-all order.",
                blocking=True,
            )
        )

    if (
        plan.start_at is not None
        and plan.stop_after is not None
        and _stage_index(plan.start_at) > _stage_index(plan.stop_after)
    ):
        findings.append(
            _finding(
                "invalid-start-after-stop",
                DiagnosticSeverity.BLOCKING,
                "start-at stage must not follow stop-after stage",
                blocking=True,
            )
        )

    stage_a = _planned_stage(plan, PipelineStage.RUN_STAGE_A)
    if (
        stage_a is not None
        and stage_a.status == PlannedStageStatus.WOULD_RUN
        and not plan.domain.strip()
    ):
        findings.append(
            _finding(
                "missing-domain-for-stage-a",
                DiagnosticSeverity.BLOCKING,
                "domain is required when run-stage-a would run",
                stage_name=PipelineStage.RUN_STAGE_A.value,
                blocking=True,
            )
        )

    for stage in plan.planned_stages:
        if stage.status == PlannedStageStatus.BLOCKED_BY_PREREQUISITE:
            for prerequisite in stage.missing_prerequisites:
                findings.append(
                    _finding(
                        f"blocked-{stage.stage_name}-{prerequisite.required_report}",
                        DiagnosticSeverity.BLOCKING,
                        prerequisite.message,
                        stage_name=stage.stage_name,
                        blocking=True,
                    )
                )

    diagnostic_stage = _planned_stage(plan, PipelineStage.DIAGNOSE_RUN)
    if (
        plan.run_diagnostics
        and diagnostic_stage is not None
        and diagnostic_stage.status == PlannedStageStatus.BLOCKED_BY_PREREQUISITE
    ):
        findings.append(
            _finding(
                "diagnostics-missing-prerequisites",
                DiagnosticSeverity.BLOCKING,
                "diagnostics requested without final-audit or replay outputs",
                stage_name=PipelineStage.DIAGNOSE_RUN.value,
                blocking=True,
            )
        )

    replay_stage = _planned_stage(plan, PipelineStage.REPLAY_VERIFY)
    if plan.write_replay_report and (
        replay_stage is None
        or replay_stage.status
        in {
            PlannedStageStatus.WOULD_SKIP,
            PlannedStageStatus.OUT_OF_RANGE,
            PlannedStageStatus.BLOCKED_BY_STOP_AFTER,
        }
    ):
        findings.append(
            _finding(
                "write-replay-report-without-replay",
                DiagnosticSeverity.WARNING,
                "write-replay-report was requested while replay-verify is skipped.",
                stage_name=PipelineStage.REPLAY_VERIFY.value,
                blocking=False,
            )
        )

    if plan.write_diagnostic_report and (
        diagnostic_stage is None
        or diagnostic_stage.status
        in {
            PlannedStageStatus.WOULD_SKIP,
            PlannedStageStatus.OUT_OF_RANGE,
            PlannedStageStatus.BLOCKED_BY_STOP_AFTER,
        }
    ):
        findings.append(
            _finding(
                "write-diagnostic-report-without-diagnostics",
                DiagnosticSeverity.WARNING,
                "write-diagnostic-report was requested while diagnose-run is skipped.",
                stage_name=PipelineStage.DIAGNOSE_RUN.value,
                blocking=False,
            )
        )
    return findings


def _planned_stages(
    config: PipelineRunConfig,
    selected: list[PipelineStage],
    selection_valid: bool,
) -> list[PlannedStage]:
    selected_set = set(selected)
    planned: list[PlannedStage] = []
    start_index = _stage_index(config.start_at) if config.start_at is not None else 0
    stop_index = (
        _stage_index(config.stop_after)
        if config.stop_after is not None
        else len(PIPELINE_STAGE_ORDER) - 1
    )
    resume_blocked = False
    missing_for_start = []
    if config.start_at is not None:
        resume = validate_resume_request(config.run_id, config.start_at, config.root)
        resume_blocked = resume.resume_status == ResumeValidationStatus.RESUME_BLOCKED
        missing_for_start = resume.missing_prerequisites

    for stage in PIPELINE_STAGE_ORDER:
        checkpoint = _checkpoint_for(config, stage)
        prerequisites = get_stage_prerequisites(stage)
        missing = missing_for_start if stage == config.start_at else []
        status = PlannedStageStatus.WOULD_RUN
        reason = f"{stage.value} is selected by run-all options."
        warnings: list[str] = []

        if stage == PipelineStage.REPLAY_VERIFY and config.skip_replay:
            status = PlannedStageStatus.WOULD_SKIP
            reason = "Replay verification is disabled by --skip-replay."
        elif stage == PipelineStage.DIAGNOSE_RUN and not config.run_diagnostics:
            status = PlannedStageStatus.WOULD_SKIP
            reason = "Diagnostics are disabled unless --run-diagnostics is set."
        elif not selection_valid:
            status = PlannedStageStatus.OUT_OF_RANGE
            reason = "Stage selection is invalid, so no stage would run."
        elif stage not in selected_set:
            index = _stage_index(stage)
            if config.stop_after is not None and index > stop_index:
                status = PlannedStageStatus.BLOCKED_BY_STOP_AFTER
                reason = f"{stage.value} is after --stop-after {config.stop_after.value}."
            elif checkpoint.completed:
                status = PlannedStageStatus.ALREADY_COMPLETE
                reason = f"{stage.value} is already complete and outside the selected range."
            elif index < start_index:
                status = PlannedStageStatus.OUT_OF_RANGE
                reason = f"{stage.value} is before --start-at {config.start_at.value}."
            else:
                status = PlannedStageStatus.OUT_OF_RANGE
                reason = f"{stage.value} is outside the selected run-all range."
        elif resume_blocked and stage == config.start_at:
            status = PlannedStageStatus.BLOCKED_BY_PREREQUISITE
            reason = f"{stage.value} cannot run because resume prerequisites are missing."
        elif resume_blocked:
            status = PlannedStageStatus.OUT_OF_RANGE
            reason = "This stage would not be reached because resume validation is blocked."
        elif stage_is_read_only(stage):
            status = PlannedStageStatus.READ_ONLY_CHECK
            reason = f"{stage.value} is selected as a read-only check."
        elif checkpoint.completed:
            status = PlannedStageStatus.WOULD_RUN
            reason = (
                f"{stage.value} is already complete, but current run-all behavior "
                "reruns selected completed stages."
            )
            warnings.append("Selected completed stages are rerun by current run-all behavior.")

        planned.append(
            PlannedStage(
                stage_name=stage.value,
                status=status,
                read_only=stage_is_read_only(stage),
                reason=reason,
                expected_outputs=expected_outputs_for_stage(
                    stage,
                    config.run_id,
                    write_replay_report=config.write_replay_report,
                    write_diagnostic_report=config.write_diagnostic_report,
                ),
                prerequisites=prerequisites,
                missing_prerequisites=missing,
                already_complete=checkpoint.completed,
                warnings=warnings,
            )
        )
    return planned


def _planned_outputs(
    config: PipelineRunConfig,
    planned_stages: list[PlannedStage],
):
    outputs = [
        output
        for stage in planned_stages
        if stage.status
        in {
            PlannedStageStatus.WOULD_RUN,
            PlannedStageStatus.READ_ONLY_CHECK,
            PlannedStageStatus.BLOCKED_BY_PREREQUISITE,
        }
        for output in stage.expected_outputs
    ]
    if any(
        stage.status in {PlannedStageStatus.WOULD_RUN, PlannedStageStatus.READ_ONLY_CHECK}
        for stage in planned_stages
    ):
        outputs.extend(expected_pipeline_report_outputs(config.run_id))
    return outputs


def _checkpoint_for(config: PipelineRunConfig, stage: PipelineStage):
    return inspect_stage_checkpoint(config.run_id, stage, config.root)


def _next_stage(planned_stages: list[PlannedStage]) -> PipelineStage | None:
    for stage in planned_stages:
        if stage.status in {
            PlannedStageStatus.WOULD_RUN,
            PlannedStageStatus.READ_ONLY_CHECK,
            PlannedStageStatus.BLOCKED_BY_PREREQUISITE,
        }:
            try:
                return PipelineStage(stage.stage_name)
            except ValueError:
                return None
    return None


def _dry_run_status(findings: list[DryRunValidationFinding]) -> DryRunStatus:
    invalid_ids = {
        "invalid-stage-selection",
        "invalid-start-after-stop",
        "missing-domain-for-stage-a",
        "unknown-stage-ordering",
    }
    if any(
        finding.finding_id in invalid_ids
        or finding.finding_id.startswith("invalid-stage-name-")
        for finding in findings
    ):
        return DryRunStatus.DRY_RUN_INVALID
    if any(finding.blocking for finding in findings):
        return DryRunStatus.DRY_RUN_BLOCKED
    if findings:
        return DryRunStatus.DRY_RUN_RUNNABLE_WITH_WARNINGS
    return DryRunStatus.DRY_RUN_RUNNABLE


def _planned_stage(
    plan: PipelineDryRunPlan,
    stage: PipelineStage,
) -> PlannedStage | None:
    for planned in plan.planned_stages:
        if planned.stage_name == stage.value:
            return planned
    return None


def _stage_index(stage: PipelineStage | None) -> int:
    if stage is None:
        return 0
    return PIPELINE_STAGE_ORDER.index(stage)


def _finding(
    finding_id: str,
    severity: DiagnosticSeverity,
    message: str,
    *,
    stage_name: str | None = None,
    blocking: bool,
) -> DryRunValidationFinding:
    return DryRunValidationFinding(
        finding_id=finding_id,
        severity=severity,
        message=message,
        stage_name=stage_name,
        blocking=blocking,
    )


def _dedupe_findings(
    findings: list[DryRunValidationFinding],
) -> list[DryRunValidationFinding]:
    deduped: dict[str, DryRunValidationFinding] = {}
    for finding in findings:
        deduped[finding.finding_id] = finding
    return [deduped[key] for key in sorted(deduped)]
