"""Packaging, audit, release, export, replay, and diagnostics schemas."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from factori.schemas.artifacts import ArtifactManifest, ArtifactRef
from factori.schemas.base import StrictModel
from factori.schemas.enums import (
    ArtifactType,
    AuditCategory,
    AuditCheckStatus,
    AuditSeverity,
    BranchStatus,
    ControllerActionType,
    DiagnosticSeverity,
    DiagnosticStatus,
    RegressionCategory,
    RegressionSeverity,
    RegressionStatus,
    ReleaseGateStatus,
    ReplayStatus,
    RootCauseCategory,
    VerificationLabel,
)
from factori.schemas.manuscript import FinalNucleus


class LedgerSummary(StrictModel):
    """Derived ledger summary. This is not provenance."""

    run_id: str = Field(min_length=1)
    commit_count: int = Field(ge=0)
    root_commit_hash: str | None = None
    latest_commit_hash: str | None = None
    action_type_counts: dict[str, int]
    candidate_count: int = Field(ge=0)
    artifact_count: int = Field(ge=0)
    verification_decision_count: int = Field(ge=0)
    human_tail_escalation_count: int = Field(ge=0)
    source_of_truth: str = "ledger"


class BranchOutcomeSummary(StrictModel):
    """Derived branch outcome summary."""

    candidate_id: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    status: BranchStatus | None = None
    verification_label: VerificationLabel | None = None
    action_type: ControllerActionType
    reason: str = Field(min_length=1)


class ReproducibilityManifest(StrictModel):
    """Deterministic reproducibility checks for a packaged run."""

    run_id: str = Field(min_length=1)
    ledger_exists: bool
    root_commit_exists: bool
    latest_commit_exists: bool
    all_artifacts_have_hashes: bool
    all_evidence_artifacts_have_producing_commits: bool
    claim_table_exists: bool
    draft_skeleton_exists: bool
    manuscript_plan_exists: bool
    final_nucleus_exists: bool
    blocked_claims_list_exists: bool
    environment_metadata_present: bool
    reproducible: bool
    blocking_issues: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ResearchObjectManifest(StrictModel):
    """References to research object package files."""

    research_object_json: ArtifactRef
    research_object_markdown: ArtifactRef
    artifact_manifest: ArtifactRef
    ledger_summary: ArtifactRef
    branch_outcomes: ArtifactRef
    reproducibility_manifest: ArtifactRef


class ResearchObject(StrictModel):
    """Packaged deterministic research object summary."""

    run_id: str = Field(min_length=1)
    final_nucleus: FinalNucleus
    manuscript_plan_ref: ArtifactRef
    draft_skeleton_ref: ArtifactRef
    claim_table_ref: ArtifactRef
    blocked_claims_ref: ArtifactRef
    checklist_ref: ArtifactRef
    stage_reports: dict[str, ArtifactRef]
    artifact_manifest_ref: ArtifactRef | None = None
    ledger_summary_ref: ArtifactRef | None = None
    branch_outcomes_ref: ArtifactRef | None = None
    reproducibility_manifest_ref: ArtifactRef | None = None
    created_at: str = Field(min_length=1)


class PackagedOutput(StrictModel):
    """Complete packaged output returned by the packaging step."""

    run_id: str = Field(min_length=1)
    research_object: ResearchObject
    manifest: ResearchObjectManifest
    artifact_manifest: ArtifactManifest
    ledger_summary: LedgerSummary
    branch_outcomes: list[BranchOutcomeSummary]
    reproducibility_manifest: ReproducibilityManifest


class AuditFinding(StrictModel):
    """One deterministic final audit finding."""

    check_id: str = Field(min_length=1)
    category: AuditCategory
    status: AuditCheckStatus
    severity: AuditSeverity
    message: str = Field(min_length=1)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    commit_refs: list[str] = Field(default_factory=list)


class AuditCheck(StrictModel):
    """One deterministic final audit check."""

    check_id: str = Field(min_length=1)
    category: AuditCategory
    status: AuditCheckStatus
    severity: AuditSeverity
    message: str = Field(min_length=1)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    commit_refs: list[str] = Field(default_factory=list)


class FinalAuditReport(StrictModel):
    """Deterministic internal consistency audit, not a validity certificate."""

    run_id: str = Field(min_length=1)
    checks: list[AuditCheck]
    findings: list[AuditFinding] = Field(default_factory=list)
    passes_count: int = Field(ge=0)
    warnings_count: int = Field(ge=0)
    failures_count: int = Field(ge=0)
    blocking_failures_count: int = Field(ge=0)
    certifies_scientific_validity: bool = False
    fake: bool = True


class ReleaseGateDecision(StrictModel):
    """Deterministic release gate decision from a final audit report."""

    run_id: str = Field(min_length=1)
    status: ReleaseGateStatus
    ready_for_polished_prose: bool
    ready_for_latex_export: bool
    ready_for_external_review: bool
    blocking_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    audit_checks: int = Field(ge=0)
    certifies_scientific_validity: bool = False


class ExportEvidencePlaceholder(StrictModel):
    """Placeholder for evidence/citation references in a future export."""

    placeholder_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    artifact_type: ArtifactType
    content_hash: str | None = None
    producing_commit_hash: str | None = None
    placeholder_text: str = Field(min_length=1)
    is_verification_evidence: bool


class ExportSectionMap(StrictModel):
    """Deterministic section-to-source map for future export."""

    section_id: str = Field(min_length=1)
    section_title: str = Field(min_length=1)
    source_plan_section_id: str | None = None
    source_draft_section_id: str | None = None
    claim_ids: list[str] = Field(default_factory=list)
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ExportClaimMap(StrictModel):
    """Deterministic claim-to-evidence export map."""

    claim_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    claim_label: VerificationLabel
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    evidence_types: list[str] = Field(default_factory=list)
    evidence_hashes: dict[str, str] = Field(default_factory=dict)
    producing_commit_hashes: dict[str, str] = Field(default_factory=dict)
    allowed_export_sections: list[str] = Field(default_factory=list)
    export_allowed: bool
    blocking_reason: str | None = None


class ProseGenerationContract(StrictModel):
    """Label-preserving contract for a future prose generator."""

    run_id: str = Field(min_length=1)
    allowed_sections: list[str]
    allowed_claims: list[str]
    blocked_claims: list[str]
    claim_labels: dict[str, VerificationLabel]
    claim_evidence_links: dict[str, list[str]]
    style_constraints: list[str]
    forbidden_transformations: list[str]
    required_disclaimers: list[str]
    ready_for_polished_prose: bool
    is_verification_evidence: bool = False


class LatexExportPlan(StrictModel):
    """Plan for future LaTeX export. This is not LaTeX source."""

    run_id: str = Field(min_length=1)
    target_template_name: str = Field(min_length=1)
    section_order: list[str]
    section_ids: list[str]
    claim_placeholder_ids: list[str]
    evidence_placeholder_ids: list[str]
    appendix_order: list[str]
    bibliography_placeholder_policy: str = Field(min_length=1)
    figure_placeholder_policy: str = Field(min_length=1)
    table_placeholder_policy: str = Field(min_length=1)
    forbidden_latex_commands: list[str]
    latex_safety_warnings: list[str]
    ready_for_latex_export: bool
    is_verification_evidence: bool = False


class LatexExportContract(StrictModel):
    """Contract for deterministic Markdown-to-LaTeX export; not evidence."""

    run_id: str = Field(min_length=1)
    manuscript_draft_artifact_id: str = Field(min_length=1)
    citation_registry_artifact_id: str | None = None
    bibliography_style: str = Field(default="plain", min_length=1)
    document_class: str = Field(default="article", min_length=1)
    packages: list[str] = Field(default_factory=list)
    section_order: list[str] = Field(default_factory=list)
    source_map_policy: str = Field(min_length=1)
    allowed_citation_keys: list[str] = Field(default_factory=list)
    allowed_claim_ids: list[str] = Field(default_factory=list)
    allowed_evidence_artifact_ids: list[str] = Field(default_factory=list)
    forbidden_labels: list[VerificationLabel] = Field(default_factory=list)
    render_check_enabled: bool = False
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


class LatexSourceMapEntry(StrictModel):
    """Trace one LaTeX block back to manuscript, claim, evidence, and citation sources."""

    latex_block_id: str = Field(min_length=1)
    section_id: str = Field(min_length=1)
    section_title: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    citation_keys: list[str] = Field(default_factory=list)
    markdown_line_range: list[int] = Field(default_factory=list)
    latex_line_range: list[int] = Field(default_factory=list)
    source_contract_hashes: dict[str, str] = Field(default_factory=dict)


class LatexSourceMap(StrictModel):
    """Source map from generated LaTeX back to manuscript drafting inputs."""

    run_id: str = Field(min_length=1)
    entries: list[LatexSourceMapEntry] = Field(default_factory=list)
    source_map_policy: str = Field(min_length=1)
    covers_all_major_sections: bool
    missing_sections: list[str] = Field(default_factory=list)
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


class LatexSafetyReport(StrictModel):
    """Safety report for deterministic LaTeX export; not scientific validation."""

    run_id: str = Field(min_length=1)
    safe: bool
    rejected: bool
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    used_citation_keys: list[str] = Field(default_factory=list)
    unknown_citation_keys: list[str] = Field(default_factory=list)
    source_map_sections: list[str] = Field(default_factory=list)
    missing_source_map_sections: list[str] = Field(default_factory=list)
    latex_is_presentation_only: bool = True
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class LatexRenderConfig(StrictModel):
    """Configuration for an optional gated LaTeX render/check."""

    run_id: str = Field(min_length=1)
    render_check_enabled: bool = False
    allow_external_tools: bool = False
    latex_executable: str | None = None
    timeout_seconds: int = Field(default=30, ge=1, le=600)
    backend: str = "local_latex"


class LatexRenderResult(StrictModel):
    """Result of an optional LaTeX render/check; presentation only."""

    run_id: str = Field(min_length=1)
    backend: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    tool_version: str | None = None
    exit_code: int
    stdout_hash: str = Field(min_length=1)
    stderr_hash: str = Field(min_length=1)
    tex_hash: str = Field(min_length=1)
    pdf_hash: str | None = None
    rendered_pdf_artifact_id: str | None = None
    passed: bool
    warnings: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class LatexCompileCheckReport(StrictModel):
    """Aggregate render/check report; not evidence or publication readiness."""

    run_id: str = Field(min_length=1)
    config: LatexRenderConfig
    render_result: LatexRenderResult | None = None
    passed: bool
    warnings: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class LatexExportResult(StrictModel):
    """Complete deterministic LaTeX export payload; presentation context only."""

    run_id: str = Field(min_length=1)
    contract: LatexExportContract
    paper_tex: str = Field(min_length=1)
    references_bib: str = ""
    source_map: LatexSourceMap
    safety_report: LatexSafetyReport
    render_result: LatexRenderResult | None = None
    compile_check_report: LatexCompileCheckReport | None = None
    warnings: list[str] = Field(default_factory=list)
    latex_artifact_id: str | None = None
    bibliography_artifact_id: str | None = None
    source_map_artifact_id: str | None = None
    export_report_artifact_id: str | None = None
    safety_report_artifact_id: str | None = None
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class ExportReadinessReport(StrictModel):
    """Readiness report for deterministic export preparation."""

    run_id: str = Field(min_length=1)
    ready_for_polished_prose: bool
    ready_for_latex_export: bool
    ready_for_external_review: bool
    export_blocked: bool
    export_allowed_claims: int = Field(ge=0)
    export_blocked_claims: int = Field(ge=0)
    blocking_reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    is_verification_evidence: bool = False


class ExportBundleManifest(StrictModel):
    """Manifest of deterministic export-preparation artifacts."""

    run_id: str = Field(min_length=1)
    prose_contract_ref: ArtifactRef
    latex_plan_ref: ArtifactRef
    section_map_ref: ArtifactRef
    claim_map_ref: ArtifactRef
    readiness_report_ref: ArtifactRef
    export_artifact_refs: list[ArtifactRef]
    contains_final_latex: bool = False
    contains_polished_prose: bool = False
    is_verification_evidence: bool = False


class ReplayFinding(StrictModel):
    """One read-only replay finding."""

    check_id: str = Field(min_length=1)
    category: AuditCategory
    status: AuditCheckStatus
    severity: AuditSeverity
    message: str = Field(min_length=1)
    expected: str | None = None
    observed: str | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    commit_refs: list[str] = Field(default_factory=list)


class ReplayCheck(StrictModel):
    """One deterministic replay verification check."""

    check_id: str = Field(min_length=1)
    category: AuditCategory
    status: AuditCheckStatus
    severity: AuditSeverity
    message: str = Field(min_length=1)
    expected: str | None = None
    observed: str | None = None
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    commit_refs: list[str] = Field(default_factory=list)


class ReplayVerificationReport(StrictModel):
    """Read-only deterministic replay report. This is not provenance."""

    run_id: str = Field(min_length=1)
    checks: list[ReplayCheck]
    findings: list[ReplayFinding] = Field(default_factory=list)
    replay_status: ReplayStatus
    ledger_commits_checked: int = Field(ge=0)
    artifacts_checked: int = Field(ge=0)
    hashes_verified: int = Field(ge=0)
    evidence_artifacts_checked: int = Field(ge=0)
    presentation_artifacts_checked: int = Field(ge=0)
    stage_outputs_checked: int = Field(ge=0)
    warnings_count: int = Field(ge=0)
    blocking_failures_count: int = Field(ge=0)
    ledger_mutated: bool
    artifact_manifest_mutated: bool
    not_provenance: bool = True
    not_evidence: bool = True
    not_ledgered: bool = True
    certifies_scientific_validity: bool = False


class RunVerificationSummary(StrictModel):
    """Compact summary of a read-only replay verification."""

    run_id: str = Field(min_length=1)
    ledger_commits_checked: int = Field(ge=0)
    artifacts_checked: int = Field(ge=0)
    hashes_verified: int = Field(ge=0)
    evidence_artifacts_checked: int = Field(ge=0)
    presentation_artifacts_checked: int = Field(ge=0)
    stage_outputs_checked: int = Field(ge=0)
    warnings: int = Field(ge=0)
    blocking_failures: int = Field(ge=0)
    replay_status: ReplayStatus
    ledger_mutated: bool
    artifact_manifest_mutated: bool


class RootCauseHypothesis(StrictModel):
    """One deterministic explanation for an audit, replay, or export finding."""

    root_cause_id: str = Field(min_length=1)
    category: RootCauseCategory
    severity: DiagnosticSeverity
    summary: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_check_ids: list[str] = Field(default_factory=list)
    artifact_refs: list[str] = Field(default_factory=list)
    affected_output: str | None = None
    rerun_from_stage: str | None = None
    manual_inspection_required: bool = False


class RecommendedRerunStep(StrictModel):
    """A safe deterministic command suggestion that diagnostics never executes."""

    step_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    command: str | None = None
    reason: str = Field(min_length=1)
    order: int = Field(ge=0)
    downstream: bool = False
    safe_to_run: bool = True
    executes_automatically: bool = False
    manual_inspection_required: bool = False


class DiagnosticFindingGroup(StrictModel):
    """Deterministic grouping of related root-cause hypotheses."""

    category: RootCauseCategory
    severity: DiagnosticSeverity
    root_cause_ids: list[str]
    summary: str = Field(min_length=1)


class DiagnosticReport(StrictModel):
    """Read-only failure explanation report. This is not provenance or evidence."""

    run_id: str = Field(min_length=1)
    diagnostic_status: DiagnosticStatus
    root_causes: list[RootCauseHypothesis]
    finding_groups: list[DiagnosticFindingGroup]
    recommended_steps: list[RecommendedRerunStep]
    sources_loaded: list[str]
    warnings: list[str] = Field(default_factory=list)
    blocking_causes_count: int = Field(ge=0)
    warning_causes_count: int = Field(ge=0)
    ledger_mutated: bool
    artifact_manifest_mutated: bool
    not_provenance: bool = True
    not_evidence: bool = True
    not_ledgered: bool = True
    certifies_scientific_validity: bool = False


class RunDifference(StrictModel):
    """One deterministic field-level difference between two completed runs."""

    difference_id: str = Field(min_length=1)
    category: RegressionCategory
    severity: RegressionSeverity
    field: str = Field(min_length=1)
    baseline_value: Any = None
    candidate_value: Any = None
    message: str = Field(min_length=1)
    baseline_refs: list[str] = Field(default_factory=list)
    candidate_refs: list[str] = Field(default_factory=list)
    is_regression: bool = False
    optional_output: bool = False


class RegressionFinding(StrictModel):
    """A deterministic regression derived from one or more run differences."""

    finding_id: str = Field(min_length=1)
    category: RegressionCategory
    severity: RegressionSeverity
    summary: str = Field(min_length=1)
    difference_ids: list[str]
    baseline_value: Any = None
    candidate_value: Any = None


class CrossRunComparisonReport(StrictModel):
    """Read-only cross-run comparison. This is not provenance or scientific validation."""

    baseline_run_id: str = Field(min_length=1)
    candidate_run_id: str = Field(min_length=1)
    differences: list[RunDifference]
    regression_findings: list[RegressionFinding] = Field(default_factory=list)
    regression_status: RegressionStatus
    sources_loaded: dict[str, list[str]]
    comparison_errors: list[str] = Field(default_factory=list)
    baseline_release_status: ReleaseGateStatus | None = None
    candidate_release_status: ReleaseGateStatus | None = None
    baseline_replay_status: ReplayStatus | None = None
    candidate_replay_status: ReplayStatus | None = None
    baseline_diagnostic_status: DiagnosticStatus | None = None
    candidate_diagnostic_status: DiagnosticStatus | None = None
    baseline_ledger_mutated: bool = False
    candidate_ledger_mutated: bool = False
    baseline_artifact_manifest_mutated: bool = False
    candidate_artifact_manifest_mutated: bool = False
    ledger_mutated: bool = False
    artifact_manifest_mutated: bool = False
    not_provenance: bool = True
    not_evidence: bool = True
    not_ledgered: bool = True
    certifies_scientific_validity: bool = False


class RunComparisonSummary(StrictModel):
    """Compact deterministic summary of a cross-run comparison."""

    baseline_run_id: str = Field(min_length=1)
    candidate_run_id: str = Field(min_length=1)
    differences_count: int = Field(ge=0)
    blocking_regressions: int = Field(ge=0)
    warning_regressions: int = Field(ge=0)
    info_differences: int = Field(ge=0)
    regression_status: RegressionStatus
    baseline_release_status: ReleaseGateStatus | None = None
    candidate_release_status: ReleaseGateStatus | None = None
    baseline_replay_status: ReplayStatus | None = None
    candidate_replay_status: ReplayStatus | None = None
    ledger_mutated: bool
    artifact_manifest_mutated: bool

__all__ = [
    "LedgerSummary",
    "BranchOutcomeSummary",
    "ReproducibilityManifest",
    "ResearchObjectManifest",
    "ResearchObject",
    "PackagedOutput",
    "AuditFinding",
    "AuditCheck",
    "FinalAuditReport",
    "ReleaseGateDecision",
    "ExportEvidencePlaceholder",
    "ExportSectionMap",
    "ExportClaimMap",
    "ProseGenerationContract",
    "LatexExportPlan",
    "LatexExportContract",
    "LatexSourceMapEntry",
    "LatexSourceMap",
    "LatexSafetyReport",
    "LatexRenderConfig",
    "LatexRenderResult",
    "LatexCompileCheckReport",
    "LatexExportResult",
    "ExportReadinessReport",
    "ExportBundleManifest",
    "ReplayFinding",
    "ReplayCheck",
    "ReplayVerificationReport",
    "RunVerificationSummary",
    "RootCauseHypothesis",
    "RecommendedRerunStep",
    "DiagnosticFindingGroup",
    "DiagnosticReport",
    "RunDifference",
    "RegressionFinding",
    "CrossRunComparisonReport",
    "RunComparisonSummary",
]
