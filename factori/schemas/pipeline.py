"""Pipeline, checkpoint, dry-run, status, and hygiene schemas."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import Field

from factori.schemas.artifacts import ArtifactType
from factori.schemas.base import StrictModel
from factori.schemas.enums import (
    DiagnosticSeverity,
    DiagnosticStatus,
    DryRunStatus,
    LedgerTipStatus,
    OutputHygieneCategory,
    OutputHygieneSeverity,
    OutputHygieneStatus,
    PipelineFailurePolicy,
    PipelineRunStatus,
    PipelineStage,
    PlannedStageStatus,
    ReleaseGateStatus,
    RemediationActionKind,
    RemediationPlanStatus,
    RemediationRisk,
    RemediationStatus,
    ReplayStatus,
    RerunPolicy,
    ResumeValidationStatus,
    RunCompletenessStatus,
    RunFileClassification,
    StageRerunStatus,
)


class PipelineStageResult(StrictModel):
    """One stage result in the deterministic one-command pipeline."""

    stage_name: PipelineStage
    started_at: str = Field(min_length=1)
    finished_at: str = Field(min_length=1)
    status: PipelineRunStatus
    created_artifacts: list[str] = Field(default_factory=list)
    summary: dict[str, Any] = Field(default_factory=dict)
    error_message: str | None = None


class PipelineRunConfig(StrictModel):
    """Configuration for deterministic direct pipeline orchestration."""

    run_id: str = Field(min_length=1)
    domain: str = ""
    method: str | None = None
    root: Path = Path(".")
    adapter_backend: str = "fake"
    allow_external_calls: bool = False
    llm_model: str = "gpt-5-mini"
    reviewer_backend: str = "fake"
    use_llm_reviewers: bool = False
    reviewer_model: str = "gpt-5-mini"
    reviewer_max_objections: int = Field(default=5, ge=1, le=20)
    proof_backend: str = "fake"
    allow_external_tools: bool = False
    proof_executable: str | None = None
    proof_timeout_seconds: int = Field(default=10, ge=1, le=60)
    stop_after: PipelineStage | None = None
    start_at: PipelineStage | None = None
    skip_replay: bool = False
    run_diagnostics: bool = False
    write_replay_report: bool = False
    write_diagnostic_report: bool = False
    failure_policy: PipelineFailurePolicy = PipelineFailurePolicy.CONTINUE_SAFE
    rerun_policy: RerunPolicy = RerunPolicy.FAIL_IF_EXISTS
    force: bool = False


class PipelineRunReport(StrictModel):
    """Ledgered deterministic orchestration report. This is not verification evidence."""

    run_id: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    method: str | None = None
    stage_results: list[PipelineStageResult]
    started_at: str = Field(min_length=1)
    finished_at: str = Field(min_length=1)
    pipeline_status: PipelineRunStatus
    failure_policy: PipelineFailurePolicy
    blocking_stage: PipelineStage | None = None
    warnings: list[str] = Field(default_factory=list)
    final_outputs: dict[str, str] = Field(default_factory=dict)
    release_status: ReleaseGateStatus | None = None
    replay_status: ReplayStatus | None = None
    diagnostic_status: DiagnosticStatus | None = None
    pipeline_report_path: str = Field(min_length=1)
    fake: bool = True
    is_verification_evidence: bool = False


class PlannedOutput(StrictModel):
    """Expected output from a dry-run planned stage."""

    output_kind: str = Field(min_length=1)
    path: str | None = None
    required_for_completion: bool = True
    optional: bool = False
    description: str = Field(min_length=1)


class DryRunValidationFinding(StrictModel):
    """One deterministic dry-run validation finding."""

    finding_id: str = Field(min_length=1)
    severity: DiagnosticSeverity
    message: str = Field(min_length=1)
    stage_name: str | None = None
    blocking: bool = False


class StagePrerequisite(StrictModel):
    """One explicit artifact or report prerequisite for a pipeline stage."""

    stage_name: PipelineStage
    required_prior_stage: PipelineStage | None = None
    required_artifact_path_or_kind: str = Field(min_length=1)
    required_report: str | None = None
    blocking_if_missing: bool = True
    message: str = Field(min_length=1)


class StageRerunDecision(StrictModel):
    """Read-only stage rerun decision derived from completion artifacts."""

    run_id: str = Field(min_length=1)
    stage_name: PipelineStage
    policy: RerunPolicy
    status: StageRerunStatus
    stage_completed: bool
    force_requested: bool = False
    should_run: bool
    should_skip: bool = False
    reason: str = Field(min_length=1)
    read_only: bool = True
    is_provenance: bool = False


class LedgerBranchFinding(StrictModel):
    """One read-only finding about ledger linearity or duplicate stage markers."""

    finding_type: str = Field(min_length=1)
    message: str = Field(min_length=1)
    commit_hashes: list[str] = Field(default_factory=list)
    parent_hash: str | None = None
    stage_name: PipelineStage | None = None
    blocking: bool = False


class LedgerTipValidationReport(StrictModel):
    """Read-only ledger tip, parent, fork, and duplicate-stage validation."""

    run_id: str = Field(min_length=1)
    status: LedgerTipStatus
    commit_count: int = Field(ge=0)
    tip_hashes: list[str] = Field(default_factory=list)
    branch_findings: list[LedgerBranchFinding] = Field(default_factory=list)
    duplicate_stage_findings: list[LedgerBranchFinding] = Field(default_factory=list)
    blocking_findings: list[LedgerBranchFinding] = Field(default_factory=list)
    ledger_exists: bool
    read_only: bool = True
    is_provenance: bool = False


class StageCheckpoint(StrictModel):
    """Read-only stage completion inspection derived from files on disk."""

    stage_name: PipelineStage
    completed: bool
    required_artifacts_present: list[str] = Field(default_factory=list)
    required_artifacts_missing: list[str] = Field(default_factory=list)
    completion_evidence: list[str] = Field(default_factory=list)
    optional: bool = False
    warnings: list[str] = Field(default_factory=list)


class NextStageRecommendation(StrictModel):
    """Deterministic next-stage suggestion from checkpoint inspection."""

    stage_name: PipelineStage | None = None
    command: str | None = None
    reason: str = Field(min_length=1)


class ResumeValidationReport(StrictModel):
    """Read-only validation of whether a run can resume at a stage."""

    run_id: str = Field(min_length=1)
    start_at_stage: PipelineStage
    resume_status: ResumeValidationStatus
    prerequisites: list[StagePrerequisite]
    missing_prerequisites: list[StagePrerequisite] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    next_recommended_stage: NextStageRecommendation
    run_exists: bool
    ledger_exists: bool
    ledger_commit_count: int = Field(ge=0)
    read_only: bool = True
    is_provenance: bool = False


class RunStatusReport(StrictModel):
    """Read-only run checkpoint report. The ledger remains provenance."""

    run_id: str = Field(min_length=1)
    run_exists: bool
    completed_stages: list[PipelineStage]
    missing_stages: list[PipelineStage]
    next_recommended_stage: NextStageRecommendation
    last_completed_stage: PipelineStage | None = None
    required_artifacts_present: list[str] = Field(default_factory=list)
    required_artifacts_missing: list[str] = Field(default_factory=list)
    stage_checkpoints: list[StageCheckpoint] = Field(default_factory=list)
    ledger_exists: bool
    ledger_commit_count: int = Field(ge=0)
    artifact_manifest_exists: bool
    research_object_exists: bool
    paper_skeleton_exists: bool
    final_audit_exists: bool
    export_preparation_exists: bool
    replay_report_exists: bool
    diagnostic_report_exists: bool
    release_status: ReleaseGateStatus | None = None
    replay_status: ReplayStatus | None = None
    diagnostic_status: DiagnosticStatus | None = None
    completeness_status: RunCompletenessStatus
    warnings: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    read_only: bool = True
    is_provenance: bool = False


class PlannedStage(StrictModel):
    """One stage in a read-only pipeline dry-run plan."""

    stage_name: str = Field(min_length=1)
    status: PlannedStageStatus
    read_only: bool = False
    reason: str = Field(min_length=1)
    expected_outputs: list[PlannedOutput] = Field(default_factory=list)
    prerequisites: list[StagePrerequisite] = Field(default_factory=list)
    missing_prerequisites: list[StagePrerequisite] = Field(default_factory=list)
    already_complete: bool = False
    warnings: list[str] = Field(default_factory=list)


class PipelineDryRunPlan(StrictModel):
    """Read-only dry-run plan for deterministic run-all orchestration."""

    run_id: str = Field(min_length=1)
    domain: str = ""
    method: str | None = None
    root: str = "."
    start_at: PipelineStage | None = None
    stop_after: PipelineStage | None = None
    skip_replay: bool = False
    run_diagnostics: bool = False
    write_replay_report: bool = False
    write_diagnostic_report: bool = False
    failure_policy: PipelineFailurePolicy = PipelineFailurePolicy.CONTINUE_SAFE
    dry_run_status: DryRunStatus
    planned_stages: list[PlannedStage]
    planned_outputs: list[PlannedOutput] = Field(default_factory=list)
    validation_findings: list[DryRunValidationFinding] = Field(default_factory=list)
    run_status: RunStatusReport | None = None
    resume_validation: ResumeValidationReport | None = None
    next_stage: PipelineStage | None = None
    selected_stages: list[PipelineStage] = Field(default_factory=list)
    warnings_count: int = Field(ge=0)
    blocking_findings_count: int = Field(ge=0)
    dry_run: bool = True
    not_provenance: bool = True
    not_evidence: bool = True
    not_ledgered: bool = True
    certifies_scientific_validity: bool = False


class RunFileRecord(StrictModel):
    """One deterministic file record from a run-directory scan."""

    path: str = Field(min_length=1)
    classification: RunFileClassification
    size_bytes: int = Field(ge=0)
    suffix: str = ""
    artifact_id: str | None = None
    artifact_type: ArtifactType | None = None
    artifact_manifest_entry: bool = False
    manifested: bool = False
    ledgered: bool = False
    has_metadata: bool = False
    non_provenance_marked: bool = False
    is_evidence: bool = False
    is_presentation: bool = False


class RunFileIndex(StrictModel):
    """Read-only deterministic index of files below one run directory."""

    run_id: str = Field(min_length=1)
    run_exists: bool
    run_path: str = Field(min_length=1)
    files: list[RunFileRecord]
    directories: list[str] = Field(default_factory=list)
    files_scanned: int = Field(ge=0)
    manifest_entries: int = Field(ge=0)
    ledger_exists: bool
    ledger_commit_count: int = Field(ge=0)
    artifact_manifest_exists: bool
    load_errors: list[str] = Field(default_factory=list)
    not_provenance: bool = True
    not_evidence: bool = True
    not_ledgered: bool = True


class OutputHygieneFinding(StrictModel):
    """One deterministic run output hygiene finding."""

    finding_id: str = Field(min_length=1)
    category: OutputHygieneCategory
    severity: OutputHygieneSeverity
    message: str = Field(min_length=1)
    paths: list[str] = Field(default_factory=list)
    expected: str | None = None
    observed: str | None = None


class OutputHygieneReport(StrictModel):
    """Read-only run output hygiene report. This is not provenance."""

    run_id: str = Field(min_length=1)
    hygiene_status: OutputHygieneStatus
    file_index: RunFileIndex
    findings: list[OutputHygieneFinding]
    files_scanned: int = Field(ge=0)
    manifest_entries: int = Field(ge=0)
    orphaned_files: int = Field(ge=0)
    missing_manifest_files: int = Field(ge=0)
    hash_mismatches: int = Field(ge=0)
    duplicate_outputs: int = Field(ge=0)
    non_provenance_files: int = Field(ge=0)
    unexpected_files: int = Field(ge=0)
    warnings_count: int = Field(ge=0)
    blocking_findings_count: int = Field(ge=0)
    ledger_mutated: bool = False
    artifact_manifest_mutated: bool = False
    not_provenance: bool = True
    not_evidence: bool = True
    not_ledgered: bool = True
    certifies_scientific_validity: bool = False


class RemediationAction(StrictModel):
    """One deterministic recommendation that this MVP never executes."""

    action_id: str = Field(min_length=1)
    finding_id: str | None = None
    finding_category: OutputHygieneCategory | None = None
    kind: RemediationActionKind
    risk: RemediationRisk
    status: RemediationStatus
    reason: str = Field(min_length=1)
    paths: list[str] = Field(default_factory=list)
    recommended_stage: str | None = None
    recommended_command: str | None = None
    requires_human_review: bool = True
    execution_performed: bool = False


class HygieneRemediationPlan(StrictModel):
    """Read-only, non-executing remediation plan derived from hygiene findings."""

    run_id: str = Field(min_length=1)
    source_hygiene_status: OutputHygieneStatus
    plan_status: RemediationPlanStatus
    actions: list[RemediationAction]
    source_finding_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    ledger_mutated: bool = False
    artifact_manifest_mutated: bool = False
    execution_performed: bool = False
    not_provenance: bool = True
    not_evidence: bool = True
    not_ledgered: bool = True
    certifies_scientific_validity: bool = False

__all__ = [
    "PipelineStageResult",
    "PipelineRunConfig",
    "PipelineRunReport",
    "PlannedOutput",
    "DryRunValidationFinding",
    "StagePrerequisite",
    "StageRerunDecision",
    "LedgerBranchFinding",
    "LedgerTipValidationReport",
    "StageCheckpoint",
    "NextStageRecommendation",
    "ResumeValidationReport",
    "RunStatusReport",
    "PlannedStage",
    "PipelineDryRunPlan",
    "RunFileRecord",
    "RunFileIndex",
    "OutputHygieneFinding",
    "OutputHygieneReport",
    "RemediationAction",
    "HygieneRemediationPlan",
]
