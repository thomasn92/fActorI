"""Read-only run status inspection and resume validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factori.checkpoints import (
    get_stage_prerequisites,
    inspect_all_stage_checkpoints,
    inspect_stage_checkpoint,
    ledger_path,
    load_diagnostic_report,
    load_pipeline_report,
    load_replay_report,
    materialize_prerequisite_path,
    prerequisite_exists,
    stage_is_optional_checkpoint,
)
from factori.ledger import ResearchLedger
from factori.schemas import (
    DiagnosticReport,
    DiagnosticStatus,
    NextStageRecommendation,
    PipelineStage,
    ReleaseGateDecision,
    ReleaseGateStatus,
    ReplayStatus,
    ResumeValidationReport,
    ResumeValidationStatus,
    RunCompletenessStatus,
    RunStatusReport,
    StageCheckpoint,
    StagePrerequisite,
)

_STAGE_COMMANDS = {
    PipelineStage.RUN_STAGE_A: 'uv run factori run-stage-a --run-id <run_id> --domain "<domain>"',
    PipelineStage.RUN_STAGE_B: "uv run factori run-stage-b --run-id <run_id>",
    PipelineStage.SELECT_STAGE_C: "uv run factori select-stage-c --run-id <run_id>",
    PipelineStage.RUN_STAGE_C: "uv run factori run-stage-c --run-id <run_id>",
    PipelineStage.SYNTHESIZE_ABSTRACT: "uv run factori synthesize-abstract --run-id <run_id>",
    PipelineStage.PLAN_MANUSCRIPT: "uv run factori plan-manuscript --run-id <run_id>",
    PipelineStage.BUILD_DRAFT_SKELETON: (
        "uv run factori build-draft-skeleton --run-id <run_id>"
    ),
    PipelineStage.PACKAGE_RESEARCH_OBJECT: (
        "uv run factori package-research-object --run-id <run_id>"
    ),
    PipelineStage.ASSEMBLE_PAPER_SKELETON: (
        "uv run factori assemble-paper-skeleton --run-id <run_id>"
    ),
    PipelineStage.FINAL_AUDIT: "uv run factori final-audit --run-id <run_id>",
    PipelineStage.PREPARE_EXPORT: "uv run factori prepare-export --run-id <run_id>",
    PipelineStage.REPLAY_VERIFY: "uv run factori replay-verify --run-id <run_id>",
    PipelineStage.DIAGNOSE_RUN: "uv run factori diagnose-run --run-id <run_id>",
}


def inspect_run_status(run_id: str, root: str | Path = ".") -> RunStatusReport:
    """Inspect run checkpoints from disk without mutating provenance."""
    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    run_exists = run_path.is_dir()
    ledger_file = ledger_path(root_path, run_id)
    ledger_exists = ledger_file.is_file()
    ledger_commit_count = 0
    warnings: list[str] = []
    blocking_issues: list[str] = []

    if ledger_exists:
        try:
            ledger_commit_count = len(ResearchLedger(ledger_file).list_commits(run_id))
        except Exception as exc:  # pragma: no cover - defensive against corrupt SQLite files.
            blocking_issues.append(f"ledger could not be read: {exc}")

    checkpoints = (
        inspect_all_stage_checkpoints(run_id, root_path, include_optional=True)
        if run_exists
        else []
    )
    warnings.extend(_checkpoint_warnings(checkpoints))

    nonoptional = [
        checkpoint
        for checkpoint in checkpoints
        if not stage_is_optional_checkpoint(checkpoint.stage_name)
    ]
    completed_stages = [checkpoint.stage_name for checkpoint in checkpoints if checkpoint.completed]
    missing_stages = [
        checkpoint.stage_name
        for checkpoint in nonoptional
        if not checkpoint.completed
    ]
    last_completed, first_missing, gap_issues = _completion_continuity(nonoptional)
    blocking_issues.extend(gap_issues)

    present = sorted(
        {
            path
            for checkpoint in checkpoints
            for path in checkpoint.required_artifacts_present
        }
    )
    missing = sorted(
        {
            path
            for checkpoint in nonoptional
            for path in checkpoint.required_artifacts_missing
        }
    )

    release_status = _load_release_status(root_path, run_id, warnings)
    replay_status = _load_replay_status(root_path, run_id, warnings)
    diagnostic_status = _load_diagnostic_status(root_path, run_id, warnings)
    if run_exists and not ledger_exists:
        blocking_issues.append("ledger is missing")
    if release_status == ReleaseGateStatus.RELEASE_BLOCKED:
        blocking_issues.append("release gate is blocked")
    if replay_status == ReplayStatus.REPLAY_FAILED:
        blocking_issues.append("replay verification failed")
    if diagnostic_status == DiagnosticStatus.BLOCKED:
        blocking_issues.append("diagnostics found blocking causes")

    next_recommendation = _next_stage_recommendation(run_id, first_missing)
    completeness = _completeness_status(
        run_exists=run_exists,
        all_nonoptional_complete=bool(nonoptional) and not missing_stages,
        has_gap=bool(gap_issues),
        blocking_issues=blocking_issues,
        warnings=warnings,
        release_status=release_status,
        replay_status=replay_status,
        diagnostic_status=diagnostic_status,
    )
    artifact_manifest_exists = (
        run_path / "research_object" / "artifact-manifest.json"
    ).is_file()

    return RunStatusReport(
        run_id=run_id,
        run_exists=run_exists,
        completed_stages=completed_stages,
        missing_stages=missing_stages,
        next_recommended_stage=next_recommendation,
        last_completed_stage=last_completed,
        required_artifacts_present=present,
        required_artifacts_missing=missing,
        stage_checkpoints=checkpoints,
        ledger_exists=ledger_exists,
        ledger_commit_count=ledger_commit_count,
        artifact_manifest_exists=artifact_manifest_exists,
        research_object_exists=(run_path / "research_object" / "research-object.json").is_file(),
        paper_skeleton_exists=(run_path / "research_object" / "paper-skeleton.json").is_file(),
        final_audit_exists=(run_path / "reports" / "final-audit-report.json").is_file(),
        export_preparation_exists=(run_path / "reports" / "export-readiness-report.json").is_file(),
        replay_report_exists=(run_path / "replay" / "replay-verification-report.json").is_file(),
        diagnostic_report_exists=(run_path / "diagnostics" / "diagnostic-report.json").is_file(),
        release_status=release_status,
        replay_status=replay_status,
        diagnostic_status=diagnostic_status,
        completeness_status=completeness,
        warnings=sorted(set(warnings)),
        blocking_issues=sorted(set(blocking_issues)),
    )


def validate_resume_request(
    run_id: str,
    start_at_stage: str | PipelineStage,
    root: str | Path = ".",
) -> ResumeValidationReport:
    """Validate whether a pipeline can safely resume at the requested stage."""
    stage = PipelineStage(start_at_stage)
    status = inspect_run_status(run_id, root)
    prerequisites = get_stage_prerequisites(stage)
    missing = _missing_prerequisites(run_id, stage, prerequisites, root)
    blocking_issues: list[str] = []

    if stage != PipelineStage.RUN_STAGE_A:
        if not status.run_exists:
            blocking_issues.append(f"run does not exist: {run_id}")
        if not status.ledger_exists:
            blocking_issues.append("run ledger does not exist")
        if stage == PipelineStage.DIAGNOSE_RUN:
            if len(missing) == len(prerequisites):
                blocking_issues.append(
                    "diagnose-run requires at least final audit or replay outputs"
                )
        else:
            blocking_issues.extend(
                f"missing prerequisite: {materialize_prerequisite_path(prereq, run_id)}"
                for prereq in missing
                if prereq.blocking_if_missing
            )

    warnings = [*status.warnings]
    if status.completeness_status == RunCompletenessStatus.INCONSISTENT_RUN:
        blocking_issues.extend(status.blocking_issues)
    elif status.blocking_issues and not blocking_issues:
        warnings.extend(status.blocking_issues)

    resume_status = (
        ResumeValidationStatus.RESUME_BLOCKED
        if blocking_issues
        else ResumeValidationStatus.RESUME_ALLOWED_WITH_WARNINGS
        if warnings
        else ResumeValidationStatus.RESUME_ALLOWED
    )
    return ResumeValidationReport(
        run_id=run_id,
        start_at_stage=stage,
        resume_status=resume_status,
        prerequisites=prerequisites,
        missing_prerequisites=missing,
        warnings=sorted(set(warnings)),
        blocking_issues=sorted(set(blocking_issues)),
        next_recommended_stage=status.next_recommended_stage,
        run_exists=status.run_exists,
        ledger_exists=status.ledger_exists,
        ledger_commit_count=status.ledger_commit_count,
    )


def stage_status_detail(
    run_id: str,
    stage_name: str | PipelineStage,
    root: str | Path = ".",
) -> dict[str, Any]:
    """Return a small JSON-serializable status payload for one stage."""
    stage = PipelineStage(stage_name)
    checkpoint = inspect_stage_checkpoint(run_id, stage, root)
    prerequisites = get_stage_prerequisites(stage)
    return {
        "run_id": run_id,
        "stage": stage.value,
        "completed": checkpoint.completed,
        "required_artifacts_present": checkpoint.required_artifacts_present,
        "required_artifacts_missing": checkpoint.required_artifacts_missing,
        "prerequisites": [item.model_dump(mode="json") for item in prerequisites],
    }


def _missing_prerequisites(
    run_id: str,
    stage: PipelineStage,
    prerequisites: list[StagePrerequisite],
    root: str | Path,
) -> list[StagePrerequisite]:
    if stage == PipelineStage.RUN_STAGE_A:
        return []
    return [
        prerequisite
        for prerequisite in prerequisites
        if not prerequisite_exists(run_id, prerequisite, root)
    ]


def _checkpoint_warnings(checkpoints: list[StageCheckpoint]) -> list[str]:
    return [
        warning
        for checkpoint in checkpoints
        for warning in checkpoint.warnings
    ]


def _completion_continuity(
    checkpoints: list[StageCheckpoint],
) -> tuple[PipelineStage | None, PipelineStage | None, list[str]]:
    last_completed: PipelineStage | None = None
    first_missing: PipelineStage | None = None
    issues: list[str] = []
    for checkpoint in checkpoints:
        if checkpoint.completed and first_missing is None:
            last_completed = checkpoint.stage_name
            continue
        if not checkpoint.completed and first_missing is None:
            first_missing = checkpoint.stage_name
            continue
        if checkpoint.completed and first_missing is not None:
            issues.append(
                f"{checkpoint.stage_name.value} is complete but "
                f"{first_missing.value} is missing"
            )
    return last_completed, first_missing, issues


def _next_stage_recommendation(
    run_id: str,
    stage: PipelineStage | None,
) -> NextStageRecommendation:
    if stage is None:
        return NextStageRecommendation(
            stage_name=None,
            command=None,
            reason="All required deterministic stages are complete.",
        )
    command = _STAGE_COMMANDS[stage].replace("<run_id>", run_id)
    return NextStageRecommendation(
        stage_name=stage,
        command=command,
        reason=f"Next incomplete deterministic stage is {stage.value}.",
    )


def _completeness_status(
    *,
    run_exists: bool,
    all_nonoptional_complete: bool,
    has_gap: bool,
    blocking_issues: list[str],
    warnings: list[str],
    release_status: ReleaseGateStatus | None,
    replay_status: ReplayStatus | None,
    diagnostic_status: DiagnosticStatus | None,
) -> RunCompletenessStatus:
    if not run_exists:
        return RunCompletenessStatus.NO_RUN_FOUND
    if has_gap or "ledger is missing" in blocking_issues:
        return RunCompletenessStatus.INCONSISTENT_RUN
    if (
        release_status == ReleaseGateStatus.RELEASE_BLOCKED
        or replay_status == ReplayStatus.REPLAY_FAILED
        or diagnostic_status == DiagnosticStatus.BLOCKED
    ):
        return RunCompletenessStatus.BLOCKED_RUN
    if all_nonoptional_complete:
        if (
            warnings
            or release_status == ReleaseGateStatus.RELEASE_READY_WITH_WARNINGS
            or replay_status == ReplayStatus.REPLAY_VERIFIED_WITH_WARNINGS
            or diagnostic_status
            in {DiagnosticStatus.WARNINGS_ONLY, DiagnosticStatus.ACTION_RECOMMENDED}
        ):
            return RunCompletenessStatus.COMPLETE_WITH_WARNINGS
        return RunCompletenessStatus.COMPLETE_RUN
    return RunCompletenessStatus.PARTIAL_RUN


def _load_release_status(
    root: Path,
    run_id: str,
    warnings: list[str],
) -> ReleaseGateStatus | None:
    path = root / "runs" / run_id / "reports" / "release-gate-decision.json"
    if path.is_file():
        try:
            return ReleaseGateDecision.model_validate_json(
                path.read_text(encoding="utf-8")
            ).status
        except ValueError as exc:
            warnings.append(f"release gate decision could not be parsed: {exc}")
            return None
    pipeline_report = _load_pipeline_report_safe(root, run_id, warnings)
    return None if pipeline_report is None else pipeline_report.release_status


def _load_replay_status(
    root: Path,
    run_id: str,
    warnings: list[str],
) -> ReplayStatus | None:
    try:
        replay_report = load_replay_report(root, run_id)
    except ValueError as exc:
        replay_report = None
        warnings.append(f"replay report could not be parsed: {exc}")
    if replay_report is not None:
        return replay_report.replay_status
    pipeline_report = _load_pipeline_report_safe(root, run_id, warnings)
    return None if pipeline_report is None else pipeline_report.replay_status


def _load_diagnostic_status(
    root: Path,
    run_id: str,
    warnings: list[str],
) -> DiagnosticStatus | None:
    try:
        diagnostic_report = load_diagnostic_report(root, run_id)
    except ValueError as exc:
        diagnostic_report = None
        warnings.append(f"diagnostic report could not be parsed: {exc}")
    if isinstance(diagnostic_report, DiagnosticReport):
        return diagnostic_report.diagnostic_status
    pipeline_report = _load_pipeline_report_safe(root, run_id, warnings)
    return None if pipeline_report is None else pipeline_report.diagnostic_status


def _load_pipeline_report_safe(root: Path, run_id: str, warnings: list[str]):
    try:
        return load_pipeline_report(root, run_id)
    except ValueError as exc:
        warnings.append(f"pipeline report could not be parsed: {exc}")
        return None
