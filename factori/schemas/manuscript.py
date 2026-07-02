"""Synthesis, claim, manuscript, draft, and paper-shape schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from factori.schemas.adapters import GeneratedSectionDraft, ProseSafetyReport, ProseSectionContract
from factori.schemas.artifacts import ArtifactRef
from factori.schemas.base import HASH_RE, StrictModel
from factori.schemas.enums import (
    ArtifactType,
    ChecklistCategory,
    FinalNucleusType,
    FullPaperGenerationStatus,
    FullPaperGenerationStepStatus,
    ManuscriptDraftStatus,
    NarrativeSectionRole,
    PaperCriticFindingSeverity,
    PaperCriticFindingType,
    PaperRevisionActionKind,
    PaperRevisionStatus,
    PaperShapeStatus,
    RerunPolicy,
    VerificationLabel,
)


class InstantiationMap(StrictModel):
    """Deterministic map from an abstract model to a branch instance."""

    id: str = Field(min_length=1)
    abstract_model_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    coherent: bool
    coherence_score: float = Field(ge=0.0, le=1.0)
    role: str = Field(min_length=1)
    branch_label: VerificationLabel
    label_preserved: bool
    reason: str = Field(min_length=1)


class AbstractModel(StrictModel):
    """Proposed abstract synthesis model."""

    id: str = Field(min_length=1)
    objects: list[str]
    assumptions: list[str]
    mechanism: str = Field(min_length=1)
    claim_family: str = Field(min_length=1)
    instantiation_maps: list[InstantiationMap] = Field(default_factory=list)
    synthesis_label: str = "AbstractSynthesis"


class AbstractionReport(StrictModel):
    """Deterministic abstraction score report."""

    abstract_model_id: str = Field(min_length=1)
    model: AbstractModel
    coverage: float = Field(ge=0.0, le=1.0)
    coherence: float = Field(ge=0.0, le=1.0)
    compression: float = Field(ge=0.0, le=1.0)
    generativity: float = Field(ge=0.0, le=1.0)
    verifiability: float = Field(ge=0.0, le=1.0)
    total_score: float = Field(ge=0.0, le=1.0)
    tau_a: float = Field(ge=0.0, le=1.0)
    accepted_by_score: bool
    branch_ids: list[str]


class AbstractionAttackReport(StrictModel):
    """Deterministic red-team attack against an abstract model."""

    abstract_model_id: str = Field(min_length=1)
    rt_abstract: float = Field(ge=0.0, le=1.0)
    tau_abstract_redteam: float = Field(ge=0.0, le=1.0)
    attack_passed: bool
    failure_reasons: list[str] = Field(default_factory=list)


class FinalNucleus(StrictModel):
    """Selected final research nucleus before manuscript generation."""

    id: str = Field(min_length=1)
    nucleus_type: FinalNucleusType
    abstract_model: AbstractModel | None = None
    candidate_id: str | None = None
    supporting_candidate_ids: list[str]
    labels_by_candidate: dict[str, VerificationLabel]
    evidence_artifacts: list[ArtifactRef] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    synthesis_label: str = "AbstractSynthesis"


class ClaimEvidenceLink(StrictModel):
    """One deterministic claim-to-evidence link."""

    claim_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    artifact_type: ArtifactType
    evidence_role: str | None = None
    supports_label: bool


class Claim(StrictModel):
    """One labeled manuscript claim candidate."""

    claim_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    claim_label: VerificationLabel
    candidate_id: str = Field(min_length=1)
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    evidence_types: list[str] = Field(default_factory=list)
    allowed_in_main_text: bool
    allowed_section: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class BlockedClaim(StrictModel):
    """A claim blocked or downgraded by manuscript planning."""

    claim_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    claim_text: str = Field(min_length=1)
    claim_label: VerificationLabel
    blocked_reason: str = Field(min_length=1)
    downgraded_to: VerificationLabel | None = None
    suggested_section: str | None = None


class ClaimTable(StrictModel):
    """Deterministic claim/evidence table."""

    final_nucleus_id: str = Field(min_length=1)
    claims: list[Claim]
    evidence_links: list[ClaimEvidenceLink] = Field(default_factory=list)


class ManuscriptSectionPlan(StrictModel):
    """Section-level manuscript plan."""

    section_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    bullets: list[str]
    allowed_claim_ids: list[str] = Field(default_factory=list)
    narrative_roles: list[NarrativeSectionRole] = Field(default_factory=list)


class ManuscriptPlan(StrictModel):
    """Structured manuscript planning artifact, not a full paper."""

    plan_id: str = Field(min_length=1)
    final_nucleus_id: str = Field(min_length=1)
    nucleus_type: FinalNucleusType
    title: str = Field(min_length=1)
    sections: list[ManuscriptSectionPlan]
    allowed_claim_ids: list[str]
    blocked_claim_ids: list[str]
    fake: bool = True


class NarrativeManuscriptContract(StrictModel):
    """Deterministic manuscript narrative contract; not verification evidence."""

    contract_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    final_nucleus_id: str | None = None
    central_message: str = ""
    problem_statement: str = ""
    why_interesting: str = ""
    literature_gap: str = ""
    novelty_claim: str = ""
    model_frame: str = ""
    notation_policy: str = ""
    main_result_id: str | None = None
    main_result_in_words: str = ""
    main_result_formal_pointer: str | None = None
    derivatives_or_corollaries: list[str] = Field(default_factory=list)
    numerical_study_purpose: str = ""
    synthetic_study_boundary: str = ""
    empirical_study_boundary: str = ""
    appendix_policy: str = ""
    section_plan: list[dict[str, Any]] = Field(default_factory=list)
    blocked_or_missing_items: list[str] = Field(default_factory=list)
    fake: bool = True
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class MainMessageAssessment(StrictModel):
    """Assessment of central paper message focus."""

    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    findings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class LiteraturePositioningAssessment(StrictModel):
    """Assessment of novelty and literature-positioning shape."""

    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    findings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ModelNotationAssessment(StrictModel):
    """Assessment of simple model frame and notation policy."""

    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    findings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class MainResultAssessment(StrictModel):
    """Assessment of one-main-result discipline."""

    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    primary_main_results: int = Field(ge=0)
    findings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class NumericalStudyAssessment(StrictModel):
    """Assessment of numerical-study purpose."""

    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    numerics_present: bool
    findings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class EmpiricalBoundaryAssessment(StrictModel):
    """Assessment of synthetic/empirical boundary discipline."""

    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    empirical_section_present: bool
    findings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class AppendixAllocationAssessment(StrictModel):
    """Assessment of appendix allocation for technical material."""

    score: float = Field(ge=0.0, le=1.0)
    passed: bool
    technical_lemmas_in_main_body: int = Field(ge=0)
    findings: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PaperShapeScore(StrictModel):
    """Weighted deterministic paper-shape score; diagnostic only."""

    central_message: float = Field(ge=0.0, le=1.0)
    problem_framing: float = Field(ge=0.0, le=1.0)
    literature_positioning: float = Field(ge=0.0, le=1.0)
    model_clarity: float = Field(ge=0.0, le=1.0)
    main_result_focus: float = Field(ge=0.0, le=1.0)
    numerics_purpose: float = Field(ge=0.0, le=1.0)
    empirical_boundary: float = Field(ge=0.0, le=1.0)
    appendix_allocation: float = Field(ge=0.0, le=1.0)
    total: float = Field(ge=0.0, le=1.0)


class PaperShapeCritique(StrictModel):
    """Deterministic narrative-quality critique; not scientific validation."""

    critique_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    contract_id: str = Field(min_length=1)
    status: PaperShapeStatus
    score: PaperShapeScore
    central_message_assessment: MainMessageAssessment
    literature_positioning_assessment: LiteraturePositioningAssessment
    model_notation_assessment: ModelNotationAssessment
    main_result_assessment: MainResultAssessment
    numerical_study_assessment: NumericalStudyAssessment
    empirical_boundary_assessment: EmpiricalBoundaryAssessment
    appendix_allocation_assessment: AppendixAllocationAssessment
    missing_items: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    recommended_structural_edits: list[str] = Field(default_factory=list)
    fake: bool = True
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class PaperCriticFinding(StrictModel):
    """One deterministic paper-level manuscript-quality finding; not evidence."""

    finding_id: str = Field(min_length=1)
    finding_type: PaperCriticFindingType
    severity: PaperCriticFindingSeverity
    section_id: str | None = None
    section_title: str | None = None
    message: str = Field(min_length=1)
    recommended_action: PaperRevisionActionKind
    source: str = Field(min_length=1)
    blocking: bool = False
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


class PaperReleaseReadinessPreview(StrictModel):
    """Non-authoritative readiness preview from manuscript-quality checks only."""

    run_id: str = Field(min_length=1)
    ready_for_revision_review: bool
    blocking_findings: int = Field(ge=0)
    major_findings: int = Field(ge=0)
    warning_findings: int = Field(ge=0)
    publication_ready: bool = False
    reasons: list[str] = Field(default_factory=list)
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class PaperCriticReport(StrictModel):
    """Paper-level critique over Markdown/LaTeX artifacts; not scientific validation."""

    report_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    manuscript_draft_artifact_id: str | None = None
    latex_artifact_id: str | None = None
    source_map_artifact_id: str | None = None
    findings: list[PaperCriticFinding] = Field(default_factory=list)
    findings_count: int = Field(ge=0)
    blocking_findings: int = Field(ge=0)
    major_findings: int = Field(ge=0)
    warning_findings: int = Field(ge=0)
    info_findings: int = Field(ge=0)
    paper_shape_status: PaperShapeStatus | None = None
    citation_safe: bool | None = None
    latex_safe: bool | None = None
    source_map_covered: bool | None = None
    release_readiness_preview: PaperReleaseReadinessPreview
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class SectionRevisionPlan(StrictModel):
    """Safe deterministic revision actions for one section."""

    section_id: str = Field(min_length=1)
    section_title: str = Field(min_length=1)
    actions: list[PaperRevisionActionKind] = Field(default_factory=list)
    finding_ids: list[str] = Field(default_factory=list)
    safe_to_apply: bool = True
    notes: list[str] = Field(default_factory=list)
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


class PaperRevisionPlan(StrictModel):
    """A deterministic non-authoritative paper revision plan."""

    plan_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    critic_report_id: str = Field(min_length=1)
    actions: list[PaperRevisionActionKind] = Field(default_factory=list)
    section_plans: list[SectionRevisionPlan] = Field(default_factory=list)
    blocking_actions: list[PaperRevisionActionKind] = Field(default_factory=list)
    safe_to_apply: bool
    warnings: list[str] = Field(default_factory=list)
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class PaperRevisionPatch(StrictModel):
    """One deterministic text patch from the safe fake revision pass."""

    patch_id: str = Field(min_length=1)
    action: PaperRevisionActionKind
    target_section_id: str | None = None
    before_snippet: str = ""
    after_snippet: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    safe: bool = True
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


class RevisionSafetyReport(StrictModel):
    """Safety report for a revised manuscript draft; not evidence."""

    run_id: str = Field(min_length=1)
    safe: bool
    rejected: bool
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    invented_citation_keys: list[str] = Field(default_factory=list)
    known_citation_keys_preserved: list[str] = Field(default_factory=list)
    created_or_upgraded_labels: bool = False
    mutated_claim_table: bool = False
    mutated_evidence_map: bool = False
    source_aware_missing_citation_repairs_attempted: int = Field(default=0, ge=0)
    source_aware_citations_added: int = Field(default=0, ge=0)
    source_aware_claims_downgraded: int = Field(default=0, ge=0)
    source_aware_claims_removed: int = Field(default=0, ge=0)
    source_aware_repairs_unresolved: int = Field(default=0, ge=0)
    source_aware_repair_used_rejected_source: bool = False
    source_aware_repair_used_hard_rejected_source: bool = False
    citation_required_items_adjudicated_or_repaired: bool = True
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class PaperRevisionResult(StrictModel):
    """Result of one deterministic fake paper revision pass."""

    run_id: str = Field(min_length=1)
    revision_status: PaperRevisionStatus
    critic_report_id: str = Field(min_length=1)
    revision_plan_id: str = Field(min_length=1)
    revised_markdown: str = ""
    patches: list[PaperRevisionPatch] = Field(default_factory=list)
    safety_report: RevisionSafetyReport
    critic_report_artifact_id: str | None = None
    revision_plan_artifact_id: str | None = None
    revision_safety_artifact_id: str | None = None
    revised_markdown_artifact_id: str | None = None
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class QualityRepairReport(StrictModel):
    """Bounded manuscript-quality repair report; not evidence or publication approval."""

    run_id: str = Field(min_length=1)
    quality_repair_enabled: bool = False
    quality_repair_backend: Literal["off", "deterministic", "fake", "openai"] = "off"
    quality_repair_status: Literal[
        "disabled",
        "repaired",
        "no_action_needed",
        "blocked",
        "failed",
    ] = "disabled"
    before_quality_status: str | None = None
    after_quality_status: str | None = None
    quality_failures_before: list[str] = Field(default_factory=list)
    quality_failures_after: list[str] = Field(default_factory=list)
    quality_warnings_before: list[str] = Field(default_factory=list)
    quality_warnings_after: list[str] = Field(default_factory=list)
    sections_repaired: list[str] = Field(default_factory=list)
    section_depth_targets: dict[str, dict[str, int]] = Field(default_factory=dict)
    section_word_counts_before: dict[str, int] = Field(default_factory=dict)
    section_word_counts_after: dict[str, int] = Field(default_factory=dict)
    sections_below_target_before: list[str] = Field(default_factory=list)
    sections_below_target_after: list[str] = Field(default_factory=list)
    placeholder_like_sections_before: list[str] = Field(default_factory=list)
    placeholder_like_sections_after: list[str] = Field(default_factory=list)
    warnings_reduced_count: int = Field(default=0, ge=0)
    irreducible_warnings: list[str] = Field(default_factory=list)
    abstract_repaired: bool = False
    problem_statement_repaired: bool = False
    method_summary_repaired: bool = False
    limitations_repaired: bool = False
    conclusion_repaired: bool = False
    placeholder_sections_repaired: int = Field(default=0, ge=0)
    underdeveloped_sections_repaired: int = Field(default=0, ge=0)
    claim_support_rechecked_after_repair: bool = False
    citation_safety_rechecked_after_repair: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class EvidenceAwareRefreshReport(StrictModel):
    """Bounded evidence-aware manuscript refresh report; not scientific approval."""

    run_id: str = Field(min_length=1)
    refresh_enabled: bool = False
    refresh_backend: Literal["off", "deterministic", "fake", "openai"] = "off"
    refresh_status: Literal[
        "disabled",
        "refreshed",
        "no_action_needed",
        "blocked",
        "failed",
    ] = "disabled"
    claim_evidence_map_path: str | None = None
    proof_supported_claim_count: int = Field(default=0, ge=0)
    experiment_supported_claim_count: int = Field(default=0, ge=0)
    citation_supported_claim_count: int = Field(default=0, ge=0)
    sections_refreshed: list[str] = Field(default_factory=list)
    proof_language_inserted: bool = False
    experiment_language_inserted: bool = False
    unsupported_claims_removed_or_downgraded: int = Field(default=0, ge=0)
    claim_support_rechecked_after_refresh: bool = False
    claim_evidence_map_rechecked_after_refresh: bool = False
    citation_safety_rechecked_after_refresh: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


HumanReviewReconciliationOutcome = Literal[
    "applied_safe_text_revision",
    "applied_boundary_clarification",
    "applied_existing_evidence_reference",
    "rejected_unsupported_claim",
    "rejected_forbidden_authority_claim",
    "deferred_requires_proof_artifact",
    "deferred_requires_experiment_artifact",
    "deferred_requires_retrieval_expansion",
    "deferred_requires_human_decision",
]


class HumanReviewReconciliationItem(StrictModel):
    """Deterministic disposition of one human-review requested change."""

    requested_change: str = Field(min_length=1)
    request_id: str | None = None
    outcome: HumanReviewReconciliationOutcome
    target_section: str | None = None
    rationale: str = Field(min_length=1)
    applied_text: str | None = None
    supporting_artifact_ids: list[str] = Field(default_factory=list)
    supporting_citation_keys: list[str] = Field(default_factory=list)
    requires_new_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class HumanReviewReconciliationReport(StrictModel):
    """Bounded human-review reconciliation report; revision context only."""

    run_id: str = Field(min_length=1)
    cycle_number: int = Field(default=1, ge=1)
    request_set_id: str | None = None
    source_manuscript_path: str | None = None
    reconciled_manuscript_path: str | None = None
    human_review_artifact_path: str = Field(min_length=1)
    review_status: str = Field(min_length=1)
    reconciliation_status: Literal[
        "no_action_needed",
        "reconciled",
        "reconciled_with_rejections_or_deferrals",
        "blocked",
        "failed",
    ]
    requested_change_count: int = Field(ge=0)
    applied_change_count: int = Field(ge=0)
    rejected_change_count: int = Field(ge=0)
    deferred_change_count: int = Field(ge=0)
    requires_new_evidence_count: int = Field(ge=0)
    sections_modified: list[str] = Field(default_factory=list)
    change_outcomes: list[HumanReviewReconciliationItem] = Field(default_factory=list)
    remaining_requested_changes: list[str] = Field(default_factory=list)
    claim_support_rechecked_after_reconciliation: bool = False
    claim_evidence_map_rechecked_after_reconciliation: bool = False
    citation_safety_rechecked_after_reconciliation: bool = False
    release_rechecked_after_reconciliation: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class ReviewerChangeRequest(StrictModel):
    """One structured reviewer workflow request without evidence authority."""

    request_id: str = Field(min_length=1)
    target_type: Literal[
        "section",
        "claim",
        "citation",
        "proof_artifact",
        "experiment_artifact",
        "reviewer_summary",
        "release_report",
    ]
    target_section_optional: str | None = None
    target_claim_id_optional: str | None = None
    target_claim_text_hash_optional: str | None = Field(
        default=None,
        pattern=HASH_RE.pattern,
    )
    target_evidence_artifact_id_optional: str | None = None
    requested_action: Literal[
        "clarify_wording",
        "expand_section",
        "add_boundary_language",
        "add_existing_citation",
        "add_existing_proof_reference",
        "add_existing_experiment_reference",
        "remove_unsupported_claim",
        "downgrade_claim",
        "request_new_proof_artifact",
        "request_new_experiment_artifact",
        "request_retrieval_expansion",
        "forbidden_publication_ready_request",
        "forbidden_validation_request",
    ]
    requested_text_optional: str | None = None
    rationale_optional: str | None = None
    priority: Literal["low", "medium", "high", "blocking"] = "medium"
    requires_new_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class ReviewerChangeRequestSet(StrictModel):
    """Immutable structured reviewer request set for one run."""

    run_id: str = Field(min_length=1)
    request_set_id: str = Field(min_length=1)
    review_id: str = Field(min_length=1)
    reviewer_name_optional: str | None = None
    created_at: str = Field(min_length=1)
    target_artifact_path: str = Field(min_length=1)
    requests: list[ReviewerChangeRequest] = Field(min_length=1)
    reviewer_attestation: str = Field(min_length=1)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class HumanReviewReconciliationCycle(StrictModel):
    """Immutable index entry for one reconciliation cycle."""

    cycle_number: int = Field(ge=1)
    request_set_id: str | None = None
    reconciliation_status: str = Field(min_length=1)
    reconciliation_report_path: str = Field(min_length=1)
    reconciled_manuscript_path: str = Field(min_length=1)
    reviewer_summary_path: str = Field(min_length=1)
    applied_change_count: int = Field(ge=0)
    rejected_change_count: int = Field(ge=0)
    deferred_change_count: int = Field(ge=0)
    requires_new_evidence_count: int = Field(ge=0)
    unresolved_request_count: int = Field(ge=0)
    ledger_tip_after_cycle: str = Field(min_length=1)


class HumanReviewReconciliationIndex(StrictModel):
    """Derived latest pointer over immutable reconciliation cycles."""

    run_id: str = Field(min_length=1)
    latest_cycle: int = Field(ge=1)
    cycle_count: int = Field(ge=1)
    cycles: list[HumanReviewReconciliationCycle] = Field(min_length=1)
    current_preferred_reconciled_manuscript: str = Field(min_length=1)
    current_preferred_reviewer_summary: str = Field(min_length=1)
    ledger_tip_after_latest_cycle: str = Field(min_length=1)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class ReviewerBundleSummary(StrictModel):
    """Reviewer-facing paper-bundle summary; explanatory context only."""

    run_id: str = Field(min_length=1)
    release_status: str = Field(min_length=1)
    publication_ready: bool = False
    safety_status: str = Field(min_length=1)
    quality_status: str = Field(min_length=1)
    claim_support_status: str = Field(min_length=1)
    citation_status: str = Field(min_length=1)
    retrieval_quality_status: str = Field(min_length=1)
    source_relevance_status: str = Field(min_length=1)
    quality_repair_status: str = Field(min_length=1)
    paper_artifact_paths: dict[str, str] = Field(default_factory=dict)
    audit_artifact_paths: dict[str, str] = Field(default_factory=dict)
    remaining_warnings: dict[str, list[str]] = Field(default_factory=dict)
    blocking_issues: list[str] = Field(default_factory=list)
    evidence_boundaries: dict[str, list[str]] = Field(default_factory=dict)
    evidence_gaps: list[str] = Field(default_factory=list)
    source_limitations: list[str] = Field(default_factory=list)
    claim_support_summary: dict[str, Any] = Field(default_factory=dict)
    citation_summary: dict[str, Any] = Field(default_factory=dict)
    retrieval_summary: dict[str, Any] = Field(default_factory=dict)
    quality_summary: dict[str, Any] = Field(default_factory=dict)
    human_review_artifact_present: bool = False
    human_review_status: str | None = None
    human_review_artifact_path: str | None = None
    human_review_blocking_concern_count: int = Field(default=0, ge=0)
    human_review_requested_change_count: int = Field(default=0, ge=0)
    human_review_recommended_next_action: str | None = None
    proof_artifacts_present: bool = False
    proof_artifact_count: int = Field(default=0, ge=0)
    formal_verification_artifact_count: int = Field(default=0, ge=0)
    informal_proof_artifact_count: int = Field(default=0, ge=0)
    proof_artifact_paths: list[str] = Field(default_factory=list)
    experiment_artifacts_present: bool = False
    experiment_artifact_count: int = Field(default=0, ge=0)
    completed_experiment_count: int = Field(default=0, ge=0)
    inconclusive_experiment_count: int = Field(default=0, ge=0)
    failed_experiment_count: int = Field(default=0, ge=0)
    experiment_artifact_paths: list[str] = Field(default_factory=list)
    remaining_evidence_gaps: list[str] = Field(default_factory=list)
    claim_evidence_map_present: bool = False
    claim_evidence_supported_count: int = Field(default=0, ge=0)
    claim_evidence_partial_count: int = Field(default=0, ge=0)
    claim_evidence_unsupported_count: int = Field(default=0, ge=0)
    proof_supported_claim_count: int = Field(default=0, ge=0)
    experiment_supported_claim_count: int = Field(default=0, ge=0)
    citation_supported_claim_count: int = Field(default=0, ge=0)
    human_review_linked_claim_count: int = Field(default=0, ge=0)
    human_review_reconciliation_present: bool = False
    human_review_reconciliation_status: str | None = None
    human_review_applied_change_count: int = Field(default=0, ge=0)
    human_review_rejected_change_count: int = Field(default=0, ge=0)
    human_review_deferred_change_count: int = Field(default=0, ge=0)
    human_review_requires_new_evidence_count: int = Field(default=0, ge=0)
    human_review_remaining_requested_changes: list[str] = Field(default_factory=list)
    reviewer_change_requests_present: bool = False
    reviewer_request_set_count: int = Field(default=0, ge=0)
    latest_reconciliation_cycle: int = Field(default=0, ge=0)
    human_review_reconciliation_cycle_count: int = Field(default=0, ge=0)
    unresolved_reviewer_request_count: int = Field(default=0, ge=0)
    autonomous_evidence_plan_present: bool = False
    autonomous_next_actions: list[str] = Field(default_factory=list)
    automation_ready_item_count: int = Field(default=0, ge=0)
    human_intervention_required: bool = False
    autonomous_execution_present: bool = False
    latest_autonomous_execution_mode: str | None = None
    latest_autonomous_execution_status: str | None = None
    autonomous_actions_applied: int = Field(default=0, ge=0)
    autonomous_actions_deferred: int = Field(default=0, ge=0)
    autonomous_actions_rejected: int = Field(default=0, ge=0)
    autonomous_actions_failed: int = Field(default=0, ge=0)
    autonomous_next_required_artifacts: list[str] = Field(default_factory=list)
    planned_spec_execution_present: bool = False
    latest_planned_spec_execution_mode: str | None = None
    latest_planned_spec_execution_status: str | None = None
    experiment_specs_executed: int = Field(default=0, ge=0)
    proof_specs_executed: int = Field(default=0, ge=0)
    retrieval_specs_executed: int = Field(default=0, ge=0)
    experiment_artifacts_created: int = Field(default=0, ge=0)
    proof_artifacts_created: int = Field(default=0, ge=0)
    retrieval_artifacts_created: int = Field(default=0, ge=0)
    autonomous_loop_present: bool = False
    latest_autonomous_loop_status: str | None = None
    latest_autonomous_loop_iterations_completed: int = Field(default=0, ge=0)
    latest_autonomous_loop_stop_reason: str | None = None
    autonomous_loop_terminal_state: str | None = None
    autonomous_loop_terminal_state_reason: str | None = None
    autonomous_loop_resolved_gap_count: int = Field(default=0, ge=0)
    autonomous_loop_deferred_gap_count: int = Field(default=0, ge=0)
    autonomous_loop_exhausted_gap_count: int = Field(default=0, ge=0)
    autonomous_loop_duplicate_only_gap_count: int = Field(default=0, ge=0)
    autonomous_loop_blocking_gap_count: int = Field(default=0, ge=0)
    autonomous_loop_automation_ready_after_history_count: int = Field(default=0, ge=0)
    autonomous_loop_stopped_before_max_iterations: bool = False
    final_gap_counts: dict[str, int] = Field(default_factory=dict)
    autonomous_loop_requires_human_intervention: bool = False
    gap_attempt_history_present: bool = False
    duplicate_specs_skipped: int = Field(default=0, ge=0)
    gaps_exhausted_no_progress: int = Field(default=0, ge=0)
    remaining_deferred_gaps: int = Field(default=0, ge=0)
    strategy_diversification_present: bool = False
    strategy_option_count: int = Field(default=0, ge=0)
    selected_strategy_count: int = Field(default=0, ge=0)
    duplicate_strategy_count: int = Field(default=0, ge=0)
    gaps_deferred_after_strategy_exhaustion: int = Field(default=0, ge=0)
    python_experiment_sandbox_present: bool = False
    latest_python_sandbox_status: str | None = None
    python_sandbox_completed_count: int = Field(default=0, ge=0)
    python_sandbox_failed_count: int = Field(default=0, ge=0)
    python_sandbox_artifacts_created: int = Field(default=0, ge=0)
    experiment_gap_routing_present: bool = False
    routed_experiment_gap_count: int = Field(default=0, ge=0)
    unrouted_experiment_gap_count: int = Field(default=0, ge=0)
    created_experiment_spec_count: int = Field(default=0, ge=0)
    bounded_empirical_gap_count: int = Field(default=0, ge=0)
    needs_python_experiment_count: int = Field(default=0, ge=0)
    routed_empirical_gap_count: int = Field(default=0, ge=0)
    sandbox_experiment_completed_count: int = Field(default=0, ge=0)
    experiment_artifacts_ingested_count: int = Field(default=0, ge=0)
    sandbox_budget_exhausted: bool = False
    sandbox_budget_remaining: dict[str, int] = Field(default_factory=dict)
    capability_escalation_present: bool = False
    capability_escalation_status: str | None = None
    proof_escalation_attempt_count: int = Field(default=0, ge=0)
    retrieval_escalation_attempt_count: int = Field(default=0, ge=0)
    successful_escalation_count: int = Field(default=0, ge=0)
    deferred_after_escalation_count: int = Field(default=0, ge=0)
    capability_escalation_network_allowed: bool = False
    capability_escalation_external_tools_allowed: bool = False
    final_manuscript_present: bool = False
    final_manuscript_regeneration_status: str | None = None
    final_manuscript_sections_generated: int = Field(default=0, ge=0)
    final_manuscript_supported_claim_count: int = Field(default=0, ge=0)
    final_manuscript_deferred_gap_count: int = Field(default=0, ge=0)
    human_review_checklist: list[str] = Field(default_factory=list)
    recommended_next_actions: list[str] = Field(default_factory=list)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class HumanReviewArtifact(StrictModel):
    """Local human-review artifact; evidence only that human review occurred."""

    run_id: str = Field(min_length=1)
    review_id: str = Field(min_length=1)
    reviewer_name_optional: str | None = None
    reviewer_role: str = Field(min_length=1)
    reviewer_is_human: bool = True
    llm_generated: bool = False
    reviewed_artifact_paths: list[str] = Field(min_length=1)
    reviewed_at: str = Field(min_length=1)
    review_status: Literal[
        "not_reviewed",
        "reviewed_with_blocking_changes",
        "reviewed_with_nonblocking_comments",
        "reviewed_ready_for_evidence_generation",
        "reviewed_rejected",
    ]
    checklist_items: list[str] = Field(min_length=1)
    blocking_concerns: list[str] = Field(default_factory=list)
    non_blocking_comments: list[str] = Field(default_factory=list)
    requested_changes: list[str] = Field(default_factory=list)
    accepted_limitations: list[str] = Field(default_factory=list)
    recommended_next_action: str = Field(min_length=1)
    reviewer_attestation: str = Field(min_length=1)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class ProofArtifact(StrictModel):
    """Local proof artifact intake record with bounded proof authority."""

    run_id: str = Field(min_length=1)
    proof_id: str = Field(min_length=1)
    proof_type: Literal[
        "lean_verified",
        "formal_verified",
        "informal_proof_note",
        "proof_plan",
        "external_certificate",
    ]
    claim_ids_or_statement_ids: list[str] = Field(min_length=1)
    statement: str = Field(min_length=1)
    artifact_path_optional: str | None = None
    checker_name_optional: str | None = None
    checker_version_optional: str | None = None
    checker_status: Literal["not_checked", "passed", "failed", "inconclusive"]
    checker_log_hash_optional: str | None = Field(default=None, pattern=HASH_RE.pattern)
    proof_hash: str = Field(pattern=HASH_RE.pattern)
    review_status: str = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)
    created_at: str = Field(min_length=1)
    ingested_at: str = Field(min_length=1)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class ExperimentArtifact(StrictModel):
    """Local experiment artifact intake record with bounded experiment authority."""

    run_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    experiment_type: str = Field(min_length=1)
    claim_ids_or_section_ids: list[str] = Field(min_length=1)
    hypothesis_or_question: str = Field(min_length=1)
    status: Literal["completed", "failed", "inconclusive", "not_reproducible"]
    dataset_name_optional: str | None = None
    dataset_hash_optional: str | None = Field(default=None, pattern=HASH_RE.pattern)
    config_hash: str = Field(pattern=HASH_RE.pattern)
    code_commit_hash_optional: str | None = None
    command_optional: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    result_summary: str = Field(min_length=1)
    artifact_paths: list[str] = Field(min_length=1)
    limitations: list[str] = Field(default_factory=list)
    created_at: str = Field(min_length=1)
    ingested_at: str = Field(min_length=1)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


ClaimEvidenceSupportType = Literal[
    "none_required",
    "citation_background_context",
    "formal_proof_verification",
    "informal_proof_context",
    "experiment_result",
    "human_review_occurrence",
    "unsupported",
]


ClaimEvidenceSupportStatus = Literal[
    "supported_within_scope",
    "partially_supported",
    "unsupported",
    "not_required_scaffold",
    "blocked_forbidden_claim",
]


ClaimEvidenceClassification = Literal[
    "citation_supported_background_claim",
    "proof_supported_claim",
    "experiment_supported_claim",
    "human_reviewed_claim",
    "unsupported_claim",
    "scaffold_or_boundary_statement",
]


class ClaimEvidenceMapLink(StrictModel):
    """Deterministic final claim-to-evidence support classification."""

    run_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    claim_text_hash: str = Field(min_length=64, max_length=64)
    section_name: str = Field(min_length=1)
    claim_class: str = Field(min_length=1)
    requires_support: bool
    support_status: ClaimEvidenceSupportStatus
    classification: ClaimEvidenceClassification
    supporting_citation_keys: list[str] = Field(default_factory=list)
    supporting_source_ids: list[str] = Field(default_factory=list)
    supporting_proof_artifact_ids: list[str] = Field(default_factory=list)
    supporting_experiment_artifact_ids: list[str] = Field(default_factory=list)
    supporting_human_review_ids: list[str] = Field(default_factory=list)
    support_type: ClaimEvidenceSupportType
    support_scope: str = Field(min_length=1)
    unsupported_reason: str | None = None
    evidence_limitations: list[str] = Field(default_factory=list)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class ClaimEvidenceMap(StrictModel):
    """Final deterministic map from claims to bounded supporting artifacts."""

    run_id: str = Field(min_length=1)
    links: list[ClaimEvidenceMapLink] = Field(default_factory=list)
    summary_counts: dict[str, int] = Field(default_factory=dict)
    unsupported_non_scaffold_claim_ids: list[str] = Field(default_factory=list)
    evidence_limitations: list[str] = Field(default_factory=list)
    publication_ready: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


AutonomousEvidenceGapType = Literal[
    "needs_python_experiment",
    "needs_formal_proof",
    "needs_retrieval_expansion",
    "needs_claim_downgrade",
    "needs_claim_removal",
    "needs_manuscript_refresh",
    "sufficiently_supported_for_bounded_draft",
]


class AutonomousEvidenceGapPlanItem(StrictModel):
    """One deterministic non-evidence planner item for a claim or bundle gap."""

    item_id: str = Field(min_length=1)
    target_type: str = Field(min_length=1)
    target_claim_id_optional: str | None = None
    target_section_optional: str | None = None
    current_support_status: str = Field(min_length=1)
    gap_type: AutonomousEvidenceGapType
    recommended_action: str = Field(min_length=1)
    priority: Literal["low", "medium", "high", "blocking"] = "medium"
    blocking: bool = False
    rationale: str = Field(min_length=1)
    required_inputs: list[str] = Field(default_factory=list)
    expected_artifact_type: str = Field(min_length=1)
    automation_ready: bool = False
    gap_fingerprint: str | None = Field(default=None, pattern=HASH_RE.pattern)
    plan_item_fingerprint: str | None = Field(default=None, pattern=HASH_RE.pattern)
    gap_attempt_history_present: bool = False
    gap_attempt_count: int = Field(default=0, ge=0)
    gap_already_attempted: bool = False
    gap_exhausted: bool = False
    automation_ready_after_history: bool = False
    source_gap_fingerprint: str | None = Field(default=None, pattern=HASH_RE.pattern)
    strategy_fingerprint: str | None = Field(default=None, pattern=HASH_RE.pattern)
    strategy_family: str | None = None
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class AutonomousEvidenceGapPlan(StrictModel):
    """Autonomous next-action plan over evidence gaps; scheduling context only."""

    run_id: str = Field(min_length=1)
    planner_backend: Literal["off", "deterministic", "fake", "openai"] = "off"
    planner_status: str = Field(min_length=1)
    claim_evidence_map_path: str | None = None
    claim_support_audit_path: str | None = None
    retrieval_quality_report_path: str | None = None
    plan_items: list[AutonomousEvidenceGapPlanItem] = Field(default_factory=list)
    next_action_summary: list[str] = Field(default_factory=list)
    ready_for_python_experiment_runner: bool = False
    ready_for_formal_proof_attempt: bool = False
    ready_for_retrieval_expansion: bool = False
    ready_for_manuscript_refresh: bool = False
    gap_attempt_history_present: bool = False
    gap_attempt_count: int = Field(default=0, ge=0)
    exhausted_gap_count: int = Field(default=0, ge=0)
    empirical_demonstration_gap_count: int = Field(default=0, ge=0)
    needs_python_experiment_count: int = Field(default=0, ge=0)
    bounded_empirical_claim_count: int = Field(default=0, ge=0)
    requires_human_intervention: bool = False
    human_intervention_reason_optional: str | None = None
    publication_ready: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


AutonomousPlanExecutionMode = Literal["dry_run", "apply"]
AutonomousPlanExecutorBackend = Literal["deterministic", "fake", "openai"]
AutonomousPlanExecutionStatus = Literal[
    "completed",
    "completed_with_deferred_actions",
    "blocked",
    "failed",
    "dry_run_completed",
]


class AutonomousPlanExecutionAction(StrictModel):
    """One bounded deterministic disposition of an autonomous plan item."""

    action_id: str = Field(min_length=1)
    plan_item_id: str = Field(min_length=1)
    target_claim_id_optional: str | None = None
    target_section_optional: str | None = None
    gap_type: AutonomousEvidenceGapType
    recommended_action: str = Field(min_length=1)
    execution_decision: Literal["would_apply", "apply", "noop", "defer", "reject", "skip"]
    execution_status: Literal[
        "planned",
        "completed",
        "deferred",
        "rejected",
        "failed",
        "skipped",
    ]
    dry_run: bool
    applied: bool = False
    gap_fingerprint: str | None = Field(default=None, pattern=HASH_RE.pattern)
    plan_item_fingerprint: str | None = Field(default=None, pattern=HASH_RE.pattern)
    planned_spec_fingerprint_optional: str | None = Field(default=None, pattern=HASH_RE.pattern)
    source_gap_fingerprint_optional: str | None = Field(default=None, pattern=HASH_RE.pattern)
    strategy_fingerprint_optional: str | None = Field(default=None, pattern=HASH_RE.pattern)
    strategy_family_optional: str | None = None
    deferred_reason_optional: str | None = None
    rejected_reason_optional: str | None = None
    created_artifact_path_optional: str | None = None
    before_hash_optional: str | None = Field(default=None, pattern=HASH_RE.pattern)
    after_hash_optional: str | None = Field(default=None, pattern=HASH_RE.pattern)
    safety_notes: list[str] = Field(default_factory=list)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class PlannedExperimentSpec(StrictModel):
    """Planned bounded experiment contract; never a completed experiment artifact."""

    run_id: str = Field(min_length=1)
    spec_id: str = Field(min_length=1)
    target_claim_id: str = Field(min_length=1)
    target_section: str = Field(min_length=1)
    hypothesis_or_question: str = Field(min_length=1)
    suggested_dataset: str = Field(min_length=1)
    suggested_metrics: list[str] = Field(min_length=1)
    suggested_baselines: list[str] = Field(min_length=1)
    suggested_seed_policy: str = Field(min_length=1)
    expected_output_artifacts: list[str] = Field(min_length=1)
    experiment_bundle_path_optional: str | None = None
    template_id_optional: str | None = None
    template_family_optional: str | None = None
    sandbox_backend: Literal["off", "uv_local", "fake"] = "off"
    requested_dependencies: list[str] = Field(default_factory=list)
    allow_network: bool = False
    shell_command_optional: str | None = None
    seed: int = 1729
    timeout_seconds: int = Field(default=30, ge=1, le=300)
    status: Literal["planned"] = "planned"
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class ProofObligationSpec(StrictModel):
    """Planned formal proof obligation; never verification evidence."""

    run_id: str = Field(min_length=1)
    spec_id: str = Field(min_length=1)
    target_claim_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    suggested_checker: str = Field(min_length=1)
    required_artifact_type: str = Field(min_length=1)
    status: Literal["planned"] = "planned"
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class RetrievalExpansionRequest(StrictModel):
    """Planned local retrieval expansion request; never source or claim evidence."""

    run_id: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    target_claim_id_optional: str | None = None
    target_section_optional: str | None = None
    query_terms: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1)
    minimum_source_quality: str = Field(min_length=1)
    status: Literal["planned"] = "planned"
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class AutonomousPlanExecutionReport(StrictModel):
    """Append-only autonomous plan execution report; workflow context only."""

    run_id: str = Field(min_length=1)
    plan_path: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    execution_mode: AutonomousPlanExecutionMode
    executor_backend: AutonomousPlanExecutorBackend = "deterministic"
    execution_status: AutonomousPlanExecutionStatus
    plan_item_count: int = Field(default=0, ge=0)
    actions: list[AutonomousPlanExecutionAction] = Field(default_factory=list)
    actions_attempted: int = Field(default=0, ge=0)
    actions_applied: int = Field(default=0, ge=0)
    actions_deferred: int = Field(default=0, ge=0)
    actions_rejected: int = Field(default=0, ge=0)
    actions_failed: int = Field(default=0, ge=0)
    duplicate_specs_skipped: int = Field(default=0, ge=0)
    existing_specs_reused: int = Field(default=0, ge=0)
    gap_attempt_history_updated: bool = False
    actions_marked_exhausted: int = Field(default=0, ge=0)
    manuscript_modified: bool = False
    claim_evidence_map_rebuilt: bool = False
    claim_support_rechecked: bool = False
    citation_safety_rechecked: bool = False
    release_rechecked: bool = False
    next_required_artifacts: list[str] = Field(default_factory=list)
    created_artifact_paths: list[str] = Field(default_factory=list)
    requires_human_intervention: bool = False
    human_intervention_reason_optional: str | None = None
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


class AutonomousPlanExecutionIndex(StrictModel):
    """Derived latest pointer over immutable autonomous execution reports."""

    run_id: str = Field(min_length=1)
    latest_execution_id: str = Field(min_length=1)
    execution_count: int = Field(ge=1)
    latest_execution_mode: AutonomousPlanExecutionMode
    latest_execution_status: AutonomousPlanExecutionStatus
    latest_created_artifact_paths: list[str] = Field(default_factory=list)
    latest_requires_human_intervention: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


PlannedSpecExecutionMode = Literal["dry_run", "apply"]
PlannedSpecExecutorBackend = Literal["deterministic_local", "fake", "external_tool"]
PlannedSpecExecutionStatus = Literal[
    "completed",
    "completed_with_deferred_specs",
    "blocked",
    "failed",
    "dry_run_completed",
]
PlannedSpecType = Literal[
    "experiment_spec",
    "proof_obligation_spec",
    "retrieval_expansion_request",
]


class PlannedSpecExecutionItem(StrictModel):
    """One bounded disposition of a planned proof/experiment/retrieval spec."""

    item_id: str = Field(min_length=1)
    spec_id: str = Field(min_length=1)
    spec_type: PlannedSpecType
    target_claim_id_optional: str | None = None
    gap_fingerprint: str | None = Field(default=None, pattern=HASH_RE.pattern)
    planned_spec_fingerprint: str | None = Field(default=None, pattern=HASH_RE.pattern)
    execution_attempt_fingerprint: str | None = Field(default=None, pattern=HASH_RE.pattern)
    executor_decision: Literal[
        "would_execute",
        "execute",
        "defer",
        "reject",
        "skip",
    ]
    execution_status: Literal[
        "planned",
        "executed",
        "deferred",
        "rejected",
        "failed",
        "skipped",
    ]
    created_artifact_path_optional: str | None = None
    ingested_artifact_path_optional: str | None = None
    deferred_reason_optional: str | None = None
    rejected_reason_optional: str | None = None
    failed_reason_optional: str | None = None
    safety_notes: list[str] = Field(default_factory=list)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class PlannedSpecExecutionReport(StrictModel):
    """Append-only execution report for planned local specs; workflow context only."""

    run_id: str = Field(min_length=1)
    execution_id: str = Field(min_length=1)
    execution_mode: PlannedSpecExecutionMode
    spec_executor_backend: PlannedSpecExecutorBackend = "deterministic_local"
    execution_status: PlannedSpecExecutionStatus
    spec_count: int = Field(default=0, ge=0)
    experiment_specs_found: int = Field(default=0, ge=0)
    proof_specs_found: int = Field(default=0, ge=0)
    retrieval_specs_found: int = Field(default=0, ge=0)
    specs_attempted: int = Field(default=0, ge=0)
    specs_executed: int = Field(default=0, ge=0)
    specs_deferred: int = Field(default=0, ge=0)
    specs_rejected: int = Field(default=0, ge=0)
    specs_failed: int = Field(default=0, ge=0)
    experiment_artifacts_created: int = Field(default=0, ge=0)
    proof_artifacts_created: int = Field(default=0, ge=0)
    retrieval_artifacts_created: int = Field(default=0, ge=0)
    experiment_specs_executed: int = Field(default=0, ge=0)
    proof_specs_executed: int = Field(default=0, ge=0)
    retrieval_specs_executed: int = Field(default=0, ge=0)
    duplicate_specs_skipped: int = Field(default=0, ge=0)
    unique_specs_executed: int = Field(default=0, ge=0)
    gap_attempt_history_updated: bool = False
    executions_marked_no_progress: int = Field(default=0, ge=0)
    items: list[PlannedSpecExecutionItem] = Field(default_factory=list)
    created_artifact_paths: list[str] = Field(default_factory=list)
    ingested_artifact_paths: list[str] = Field(default_factory=list)
    claim_evidence_map_rebuilt: bool = False
    autonomous_plan_rebuilt: bool = False
    manuscript_refreshed: bool = False
    release_rechecked: bool = False
    requires_human_intervention: bool = False
    human_intervention_reason_optional: str | None = None
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


class PlannedSpecExecutionIndex(StrictModel):
    """Derived latest pointer over immutable planned-spec execution reports."""

    run_id: str = Field(min_length=1)
    latest_execution_id: str = Field(min_length=1)
    execution_count: int = Field(ge=1)
    latest_execution_mode: PlannedSpecExecutionMode
    latest_execution_status: PlannedSpecExecutionStatus
    latest_created_artifact_paths: list[str] = Field(default_factory=list)
    latest_ingested_artifact_paths: list[str] = Field(default_factory=list)
    latest_requires_human_intervention: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


PythonExperimentSandboxStatus = Literal[
    "dry_run_ready",
    "completed",
    "failed",
    "timed_out",
    "rejected_policy_violation",
    "not_reproducible",
]


class PythonExperimentSandboxManifest(StrictModel):
    """Hashed files and closed execution policy for one local Python sandbox run."""

    run_id: str = Field(min_length=1)
    sandbox_run_id: str = Field(min_length=1)
    files: dict[str, str] = Field(default_factory=dict)
    allowed_dependencies: list[str] = Field(default_factory=list)
    blocked_dependencies: list[str] = Field(default_factory=list)
    python_version: str = Field(min_length=1)
    platform: str = Field(min_length=1)
    uv_version: str = Field(min_length=1)
    allow_network: bool = False
    allow_subprocess: bool = False
    allow_file_write_outside_workspace: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class PythonExperimentSandboxRun(StrictModel):
    """One bounded uv-based local experiment execution record."""

    run_id: str = Field(min_length=1)
    experiment_spec_id: str = Field(min_length=1)
    sandbox_run_id: str = Field(min_length=1)
    sandbox_backend: Literal["uv_local", "fake"]
    execution_mode: Literal["dry_run", "apply"]
    sandbox_status: PythonExperimentSandboxStatus
    experiment_bundle_path: str = Field(min_length=1)
    working_directory: str = Field(min_length=1)
    pyproject_path: str = Field(min_length=1)
    uv_lock_path_optional: str | None = None
    dependency_policy: dict[str, Any] = Field(default_factory=dict)
    network_disabled: bool = True
    seed: int
    timeout_seconds: int = Field(ge=1)
    resource_limits: dict[str, int] = Field(default_factory=dict)
    command_executed: list[str] = Field(default_factory=list)
    stdout_path: str = Field(min_length=1)
    stderr_path: str = Field(min_length=1)
    metrics_path: str = Field(min_length=1)
    artifact_manifest_path: str = Field(min_length=1)
    config_hash: str = Field(pattern=HASH_RE.pattern)
    code_hash: str = Field(pattern=HASH_RE.pattern)
    input_hash: str = Field(pattern=HASH_RE.pattern)
    metrics_hash_optional: str | None = Field(default=None, pattern=HASH_RE.pattern)
    output_hash_optional: str | None = Field(default=None, pattern=HASH_RE.pattern)
    created_experiment_artifact_path_optional: str | None = None
    ingested_experiment_artifact_path_optional: str | None = None
    claim_evidence_map_rebuilt: bool = False
    release_rechecked: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


class PythonExperimentSandboxReport(PythonExperimentSandboxRun):
    """Canonical append-only report for one sandbox run."""


class PythonExperimentSandboxIndex(StrictModel):
    """Derived latest pointer over immutable Python sandbox reports."""

    run_id: str = Field(min_length=1)
    latest_sandbox_run_id: str = Field(min_length=1)
    sandbox_run_count: int = Field(ge=1)
    latest_sandbox_status: PythonExperimentSandboxStatus
    completed_count: int = Field(default=0, ge=0)
    failed_count: int = Field(default=0, ge=0)
    experiment_artifacts_created_count: int = Field(default=0, ge=0)
    network_disabled: bool = True
    latest_report_path: str = Field(min_length=1)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


ExperimentTemplateFamily = Literal[
    "synthetic_calibration",
    "synthetic_ablation",
    "baseline_vs_method",
    "robustness_sanity_check",
    "simulation_comparison",
]


class ExperimentTemplate(StrictModel):
    """Approved local experiment template; template selection is not evidence."""

    template_id: str = Field(min_length=1)
    template_name: str = Field(min_length=1)
    template_family: ExperimentTemplateFamily
    bundle_path: str = Field(min_length=1)
    supported_gap_types: list[AutonomousEvidenceGapType] = Field(min_length=1)
    supported_claim_classes: list[str] = Field(min_length=1)
    required_inputs: list[str] = Field(default_factory=list)
    default_metrics: list[str] = Field(min_length=1)
    default_baselines: list[str] = Field(min_length=1)
    dependency_profile: list[str] = Field(default_factory=list)
    network_required: bool = False
    arbitrary_code_required: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class ExperimentTemplateRegistry(StrictModel):
    """Deterministic registry of approved local experiment templates."""

    run_id: str = Field(min_length=1)
    registry_id: str = Field(min_length=1)
    templates: list[ExperimentTemplate] = Field(default_factory=list)
    network_required_template_count: int = Field(default=0, ge=0)
    arbitrary_code_required_template_count: int = Field(default=0, ge=0)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


class ExperimentTemplateSelection(StrictModel):
    """One deterministic template selection decision for a gap."""

    target_claim_id: str = Field(min_length=1)
    gap_fingerprint: str = Field(pattern=HASH_RE.pattern)
    selected_template_id_optional: str | None = None
    rejected_template_ids: list[str] = Field(default_factory=list)
    selection_reason: str = Field(min_length=1)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


ExperimentGapRoutingStatus = Literal[
    "routed",
    "unrouted_no_template",
    "rejected_policy",
    "deferred_budget_exhausted",
    "deferred_requires_inputs",
]


class ExperimentGapRoutingItem(StrictModel):
    """One deterministic routing decision from an experiment gap to a template."""

    item_id: str = Field(min_length=1)
    target_claim_id: str = Field(min_length=1)
    target_section_optional: str | None = None
    gap_fingerprint: str = Field(pattern=HASH_RE.pattern)
    claim_class: str = Field(min_length=1)
    gap_type: AutonomousEvidenceGapType
    selected_template_id_optional: str | None = None
    template_family_optional: str | None = None
    routing_decision: str = Field(min_length=1)
    routing_status: ExperimentGapRoutingStatus
    created_experiment_spec_path_optional: str | None = None
    existing_experiment_spec_path_optional: str | None = None
    rejection_reason_optional: str | None = None
    safety_notes: list[str] = Field(default_factory=list)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class ExperimentGapRoutingReport(StrictModel):
    """Append-only non-evidence report for experiment-gap template routing."""

    run_id: str = Field(min_length=1)
    routing_id: str = Field(min_length=1)
    routing_backend: Literal["deterministic", "fake", "openai"] = "deterministic"
    routing_status: str = Field(min_length=1)
    claim_evidence_map_path: str = Field(min_length=1)
    autonomous_plan_path: str = Field(min_length=1)
    template_registry_path: str = Field(min_length=1)
    gap_count: int = Field(default=0, ge=0)
    routed_gap_count: int = Field(default=0, ge=0)
    unrouted_gap_count: int = Field(default=0, ge=0)
    created_experiment_spec_count: int = Field(default=0, ge=0)
    bounded_empirical_gaps_routed: int = Field(default=0, ge=0)
    synthetic_template_specs_created: int = Field(default=0, ge=0)
    selected_template_count: int = Field(default=0, ge=0)
    rejected_template_count: int = Field(default=0, ge=0)
    items: list[ExperimentGapRoutingItem] = Field(default_factory=list)
    requires_human_intervention: bool = False
    human_intervention_reason_optional: str | None = None
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


class ExperimentGapRoutingIndex(StrictModel):
    """Derived latest pointer over immutable experiment-gap routing reports."""

    run_id: str = Field(min_length=1)
    latest_routing_id: str = Field(min_length=1)
    routing_count: int = Field(ge=1)
    latest_routing_status: str = Field(min_length=1)
    routed_gap_count: int = Field(default=0, ge=0)
    unrouted_gap_count: int = Field(default=0, ge=0)
    created_experiment_spec_count: int = Field(default=0, ge=0)
    bounded_empirical_gaps_routed: int = Field(default=0, ge=0)
    synthetic_template_specs_created: int = Field(default=0, ge=0)
    latest_report_path: str = Field(min_length=1)
    latest_template_registry_path: str = Field(min_length=1)
    latest_requires_human_intervention: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


class SandboxBudgetPolicy(StrictModel):
    """Bounded uv sandbox budget for one autonomous loop."""

    max_sandbox_runs_per_loop: int = Field(default=3, ge=0)
    max_sandbox_runs_per_iteration: int = Field(default=1, ge=0)
    max_sandbox_seconds_per_loop: int = Field(default=120, ge=0)
    max_sandbox_failures_per_loop: int = Field(default=1, ge=0)
    max_experiment_artifacts_per_loop: int = Field(default=3, ge=0)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class SandboxBudgetReport(StrictModel):
    """Budget accounting for gated local Python sandbox execution."""

    run_id: str = Field(min_length=1)
    budget_id: str = Field(min_length=1)
    policy: SandboxBudgetPolicy
    sandbox_budget_used: dict[str, int] = Field(default_factory=dict)
    sandbox_budget_remaining: dict[str, int] = Field(default_factory=dict)
    budget_exhausted: bool = False
    deferred_reason_optional: str | None = None
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


GapAttemptStatus = Literal[
    "open",
    "resolved",
    "deferred",
    "exhausted_no_progress",
    "exhausted_initial_strategy",
    "exhausted_all_strategies",
    "deferred_after_diversification",
    "blocked",
]


class GapAttemptRecord(StrictModel):
    """Attempt history for one stable evidence/workflow gap fingerprint."""

    gap_fingerprint: str = Field(pattern=HASH_RE.pattern)
    target_claim_id_optional: str | None = None
    target_section_optional: str | None = None
    gap_type: str = Field(min_length=1)
    recommended_action: str = Field(min_length=1)
    expected_artifact_type: str = Field(min_length=1)
    first_seen_iteration_optional: int | None = Field(default=None, ge=1)
    latest_seen_iteration_optional: int | None = Field(default=None, ge=1)
    attempt_count: int = Field(default=0, ge=0)
    successful_attempt_count: int = Field(default=0, ge=0)
    deferred_attempt_count: int = Field(default=0, ge=0)
    failed_attempt_count: int = Field(default=0, ge=0)
    no_op_attempt_count: int = Field(default=0, ge=0)
    strategy_attempt_count: int = Field(default=0, ge=0)
    strategy_fingerprints_attempted: list[str] = Field(default_factory=list)
    diversified_strategy_count: int = Field(default=0, ge=0)
    final_deferral_after_strategy_exhaustion: bool = False
    latest_attempt_status: str | None = None
    current_gap_status: GapAttemptStatus = "open"
    created_spec_fingerprints: list[str] = Field(default_factory=list)
    created_artifact_paths: list[str] = Field(default_factory=list)
    linked_evidence_artifact_ids: list[str] = Field(default_factory=list)
    resolution_reason_optional: str | None = None
    exhaustion_reason_optional: str | None = None
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class GapAttemptHistory(StrictModel):
    """Append-only derived history over autonomous gap attempts."""

    run_id: str = Field(min_length=1)
    history_version: int = Field(ge=1)
    gap_count: int = Field(default=0, ge=0)
    attempt_count: int = Field(default=0, ge=0)
    open_gap_count: int = Field(default=0, ge=0)
    exhausted_gap_count: int = Field(default=0, ge=0)
    deferred_gap_count: int = Field(default=0, ge=0)
    resolved_gap_count: int = Field(default=0, ge=0)
    strategy_attempt_count: int = Field(default=0, ge=0)
    diversified_strategy_count: int = Field(default=0, ge=0)
    deferred_after_strategy_exhaustion_count: int = Field(default=0, ge=0)
    records: list[GapAttemptRecord] = Field(default_factory=list)
    created_at: str = Field(min_length=1)
    updated_at: str = Field(min_length=1)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


class PlannedSpecDuplicateRecord(StrictModel):
    """One duplicate planned spec skipped by stable fingerprint."""

    duplicate_spec_fingerprint: str = Field(pattern=HASH_RE.pattern)
    existing_spec_path: str = Field(min_length=1)
    skipped_new_spec_reason: str = Field(min_length=1)
    gap_fingerprint: str | None = Field(default=None, pattern=HASH_RE.pattern)
    duplicate_spec_path_optional: str | None = None
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class PlannedSpecDedupIndex(StrictModel):
    """Derived index over planned spec fingerprints and duplicates."""

    run_id: str = Field(min_length=1)
    spec_count: int = Field(default=0, ge=0)
    unique_spec_count: int = Field(default=0, ge=0)
    duplicate_spec_count: int = Field(default=0, ge=0)
    spec_fingerprints: list[str] = Field(default_factory=list)
    duplicate_records: list[PlannedSpecDuplicateRecord] = Field(default_factory=list)
    latest_updated_at: str = Field(min_length=1)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


GapStrategyFamily = Literal[
    "retrieval_query_variant",
    "retrieval_source_type_variant",
    "proof_decomposition_variant",
    "proof_checker_variant",
    "experiment_metric_variant",
    "experiment_baseline_variant",
    "experiment_dataset_variant",
    "claim_downgrade_variant",
    "claim_removal_variant",
    "manuscript_boundary_variant",
]


class GapStrategyOption(StrictModel):
    """One bounded alternative strategy for an exhausted workflow gap."""

    strategy_id: str = Field(min_length=1)
    gap_fingerprint: str = Field(pattern=HASH_RE.pattern)
    target_claim_id_optional: str | None = None
    target_section_optional: str | None = None
    gap_type: AutonomousEvidenceGapType
    original_recommended_action: str = Field(min_length=1)
    alternative_action: str = Field(min_length=1)
    strategy_family: GapStrategyFamily
    strategy_fingerprint: str = Field(pattern=HASH_RE.pattern)
    novel_relative_to_previous_attempts: bool
    expected_artifact_type: str = Field(min_length=1)
    required_inputs: list[str] = Field(default_factory=list)
    automation_ready: bool
    selected: bool = False
    rationale: str = Field(min_length=1)
    safety_notes: list[str] = Field(default_factory=list)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class GapStrategyDiversificationReport(StrictModel):
    """Append-only deterministic diversification report; workflow context only."""

    run_id: str = Field(min_length=1)
    diversification_id: str = Field(min_length=1)
    source_gap_attempt_history_path: str = Field(min_length=1)
    source_autonomous_plan_path: str = Field(min_length=1)
    strategy_backend: Literal["deterministic", "fake", "openai"] = "deterministic"
    strategy_status: str = Field(min_length=1)
    candidate_gap_count: int = Field(default=0, ge=0)
    strategy_option_count: int = Field(default=0, ge=0)
    new_strategy_count: int = Field(default=0, ge=0)
    duplicate_strategy_count: int = Field(default=0, ge=0)
    selected_strategy_count: int = Field(default=0, ge=0)
    selected_strategy_ids: list[str] = Field(default_factory=list)
    created_plan_item_count: int = Field(default=0, ge=0)
    created_spec_count: int = Field(default=0, ge=0)
    strategy_options: list[GapStrategyOption] = Field(default_factory=list)
    requires_human_intervention: bool = False
    human_intervention_reason_optional: str | None = None
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


class GapStrategyDiversificationIndex(StrictModel):
    """Derived latest pointer over immutable diversification reports."""

    run_id: str = Field(min_length=1)
    latest_diversification_id: str = Field(min_length=1)
    diversification_count: int = Field(ge=1)
    latest_strategy_status: str = Field(min_length=1)
    latest_strategy_option_count: int = Field(default=0, ge=0)
    latest_selected_strategy_count: int = Field(default=0, ge=0)
    latest_duplicate_strategy_count: int = Field(default=0, ge=0)
    strategy_option_count: int = Field(default=0, ge=0)
    selected_strategy_count: int = Field(default=0, ge=0)
    duplicate_strategy_count: int = Field(default=0, ge=0)
    latest_requires_human_intervention: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


CapabilityProofBackend = Literal[
    "proof_plan_refinement_local",
    "formal_proof_fixture_local",
    "lean_external_disabled",
]
CapabilityRetrievalBackend = Literal[
    "local_source_pack_expansion",
    "bibliography_fixture_expansion",
    "network_retrieval_disabled",
]
CapabilityEscalationStatus = Literal[
    "completed",
    "completed_with_deferred_gaps",
    "no_candidate_deferred_gaps",
    "blocked_policy",
    "failed",
]
CapabilityEscalationItemStatus = Literal[
    "created_context_artifact",
    "created_formal_artifact",
    "expanded_local_sources",
    "no_match_found",
    "rejected_policy",
    "failed",
    "deferred",
]


class CapabilityEscalationPolicy(StrictModel):
    """Fail-closed local/offline capability escalation policy; not evidence."""

    run_id: str = Field(min_length=1)
    allow_network: bool = False
    allow_external_proof_tools: bool = False
    allow_external_retrieval_tools: bool = False
    allowed_proof_backends: list[CapabilityProofBackend] = Field(
        default_factory=lambda: [
            "proof_plan_refinement_local",
            "formal_proof_fixture_local",
            "lean_external_disabled",
        ]
    )
    allowed_retrieval_backends: list[CapabilityRetrievalBackend] = Field(
        default_factory=lambda: [
            "local_source_pack_expansion",
            "bibliography_fixture_expansion",
            "network_retrieval_disabled",
        ]
    )
    max_escalation_attempts_per_gap: int = Field(default=1, ge=0)
    max_escalation_attempts_per_loop: int = Field(default=4, ge=0)
    max_retrieval_sources_per_escalation: int = Field(default=8, ge=0)
    max_tool_runtime_seconds: int = Field(default=30, ge=0)
    fail_closed: bool = True
    publication_ready: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class CapabilityEscalationItem(StrictModel):
    """Disposition of one deferred proof/retrieval escalation candidate."""

    item_id: str = Field(min_length=1)
    gap_fingerprint: str | None = Field(default=None, pattern=HASH_RE.pattern)
    target_claim_id_optional: str | None = None
    target_section_optional: str | None = None
    gap_type: str = Field(min_length=1)
    deferred_reason: str = Field(min_length=1)
    selected_backend: str = Field(min_length=1)
    backend_allowed_by_policy: bool
    execution_status: CapabilityEscalationItemStatus
    created_artifact_path_optional: str | None = None
    ingested_artifact_path_optional: str | None = None
    failure_reason_optional: str | None = None
    safety_notes: list[str] = Field(default_factory=list)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


class CapabilityEscalationReport(StrictModel):
    """Append-only capability escalation report; workflow context only."""

    run_id: str = Field(min_length=1)
    escalation_id: str = Field(min_length=1)
    policy_path: str = Field(min_length=1)
    source_loop_report_path: str | None = None
    source_gap_attempt_history_path: str | None = None
    escalation_status: CapabilityEscalationStatus
    network_allowed: bool = False
    external_tools_allowed: bool = False
    candidate_deferred_gap_count: int = Field(default=0, ge=0)
    attempted_gap_count: int = Field(default=0, ge=0)
    proof_escalation_attempt_count: int = Field(default=0, ge=0)
    retrieval_escalation_attempt_count: int = Field(default=0, ge=0)
    successful_escalation_count: int = Field(default=0, ge=0)
    failed_escalation_count: int = Field(default=0, ge=0)
    deferred_after_escalation_count: int = Field(default=0, ge=0)
    created_artifact_paths: list[str] = Field(default_factory=list)
    ingested_artifact_paths: list[str] = Field(default_factory=list)
    claim_evidence_map_rebuilt: bool = False
    autonomous_plan_rebuilt: bool = False
    release_rechecked: bool = False
    items: list[CapabilityEscalationItem] = Field(default_factory=list)
    requires_human_intervention: bool = False
    human_intervention_reason_optional: str | None = None
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


class CapabilityEscalationIndex(StrictModel):
    """Derived latest pointer over immutable capability escalation reports."""

    run_id: str = Field(min_length=1)
    latest_escalation_id: str = Field(min_length=1)
    escalation_count: int = Field(ge=1)
    latest_escalation_status: CapabilityEscalationStatus
    proof_escalation_attempt_count: int = Field(default=0, ge=0)
    retrieval_escalation_attempt_count: int = Field(default=0, ge=0)
    successful_escalation_count: int = Field(default=0, ge=0)
    deferred_after_escalation_count: int = Field(default=0, ge=0)
    latest_artifact_paths: list[str] = Field(default_factory=list)
    latest_requires_human_intervention: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


FinalManuscriptRegenerationBackend = Literal["deterministic", "fake", "openai"]
FinalManuscriptRegenerationStatus = Literal["completed", "blocked", "failed"]


class FinalManuscriptSection(StrictModel):
    """One deterministic section in a regenerated final manuscript."""

    section_id: str = Field(min_length=1)
    heading: str = Field(min_length=1)
    body_markdown: str = Field(min_length=1)
    word_count: int = Field(ge=0)
    included_claim_ids: list[str] = Field(default_factory=list)
    citation_keys: list[str] = Field(default_factory=list)
    proof_artifact_ids: list[str] = Field(default_factory=list)
    experiment_artifact_ids: list[str] = Field(default_factory=list)
    deferred_gap_types: list[str] = Field(default_factory=list)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class FinalManuscriptClaimSummary(StrictModel):
    """Scoped disposition of one source claim during final regeneration."""

    claim_id: str = Field(min_length=1)
    section_name: str = Field(min_length=1)
    claim_class: str = Field(min_length=1)
    support_status: str = Field(min_length=1)
    support_type: str = Field(min_length=1)
    disposition: Literal[
        "included_supported_within_scope",
        "included_scaffold_or_boundary",
        "downgraded_to_boundary",
        "removed_forbidden_or_unsupported",
    ]
    supporting_citation_keys: list[str] = Field(default_factory=list)
    supporting_proof_artifact_ids: list[str] = Field(default_factory=list)
    supporting_experiment_artifact_ids: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class FinalManuscriptStructuredDocument(StrictModel):
    """Machine-checkable representation of the regenerated manuscript."""

    run_id: str = Field(min_length=1)
    regeneration_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    sections: list[FinalManuscriptSection] = Field(min_length=1)
    claim_summaries: list[FinalManuscriptClaimSummary] = Field(default_factory=list)
    accepted_citation_keys: list[str] = Field(default_factory=list)
    deferred_gap_statements: list[str] = Field(default_factory=list)
    publication_ready: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class FinalManuscriptRegenerationReport(StrictModel):
    """Append-only report for deterministic final manuscript regeneration."""

    run_id: str = Field(min_length=1)
    regeneration_id: str = Field(min_length=1)
    regeneration_backend: FinalManuscriptRegenerationBackend = "deterministic"
    regeneration_status: FinalManuscriptRegenerationStatus
    source_claim_evidence_map_path: str = Field(min_length=1)
    source_citation_registry_path: str = Field(min_length=1)
    source_retrieval_report_path: str = Field(min_length=1)
    source_autonomous_loop_report_path: str = Field(min_length=1)
    source_capability_escalation_report_path_optional: str | None = None
    final_manuscript_path: str = Field(min_length=1)
    final_manuscript_structured_path: str = Field(min_length=1)
    sections_generated: int = Field(ge=0)
    claim_count_total: int = Field(ge=0)
    supported_claim_count: int = Field(ge=0)
    citation_supported_claim_count: int = Field(ge=0)
    proof_supported_claim_count: int = Field(ge=0)
    experiment_supported_claim_count: int = Field(ge=0)
    human_review_linked_claim_count: int = Field(ge=0)
    unsupported_claim_count: int = Field(ge=0)
    deferred_gap_count: int = Field(ge=0)
    deferred_proof_gap_count: int = Field(ge=0)
    deferred_retrieval_gap_count: int = Field(ge=0)
    deferred_experiment_gap_count: int = Field(ge=0)
    claim_support_rechecked_after_regeneration: bool = False
    claim_evidence_map_rechecked_after_regeneration: bool = False
    citation_safety_rechecked_after_regeneration: bool = False
    quality_lint_rechecked_after_regeneration: bool = False
    release_rechecked_after_regeneration: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


class FinalManuscriptRegenerationIndex(StrictModel):
    """Derived latest pointer over immutable final manuscript regenerations."""

    run_id: str = Field(min_length=1)
    latest_regeneration_id: str = Field(min_length=1)
    regeneration_count: int = Field(ge=1)
    latest_regeneration_status: FinalManuscriptRegenerationStatus
    current_preferred_manuscript: str = Field(min_length=1)
    current_preferred_structured_manuscript: str = Field(min_length=1)
    latest_report_path: str = Field(min_length=1)
    publication_ready: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


AutonomousLoopBackend = Literal["deterministic", "fake", "openai"]
AutonomousLoopStatus = Literal[
    "completed",
    "completed_with_deferred_gaps",
    "stopped_no_progress",
    "stopped_max_iterations",
    "blocked_requires_human_intervention",
    "failed",
]
AutonomousLoopStopReason = Literal[
    "no_unsupported_claims",
    "no_automation_ready_items",
    "no_progress",
    "max_iterations_reached",
    "safety_gate_blocked",
    "ledger_invalid",
    "requires_human_intervention",
    "exhausted_gaps",
    "deferred_gaps",
]
AutonomousLoopTerminalState = Literal[
    "completed_all_supported",
    "completed_with_deferred_gaps",
    "completed_with_exhausted_noncritical_gaps",
    "stopped_no_progress",
    "stopped_safety_blocked",
    "stopped_requires_human_intervention",
    "stopped_max_iterations",
]
AutonomousLoopGapTerminalClass = Literal[
    "resolved_by_evidence",
    "resolved_by_claim_downgrade",
    "resolved_by_claim_removal",
    "deferred_exhausted_proof",
    "deferred_exhausted_retrieval",
    "deferred_budget_exhausted",
    "deferred_requires_external_tool",
    "deferred_requires_network",
    "duplicate_only",
    "noncritical_boundary_gap",
    "blocking_unsupported_claim",
    "unclassified_requires_human_intervention",
]


class AutonomousLoopGapTerminalClassification(StrictModel):
    """Terminal disposition of one autonomous workflow gap; not evidence."""

    gap_fingerprint: str | None = Field(default=None, pattern=HASH_RE.pattern)
    target_claim_id_optional: str | None = None
    target_section_optional: str | None = None
    gap_type: str = Field(min_length=1)
    terminal_class: AutonomousLoopGapTerminalClass
    blocking: bool = False
    automation_ready_after_history: bool = False
    reason: str = Field(min_length=1)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class AutonomousLoopDecision(StrictModel):
    """Deterministic stop/continue decision for one autonomous loop iteration."""

    continue_loop: bool
    stop_reason: AutonomousLoopStopReason | None = None
    loop_status: AutonomousLoopStatus | None = None
    rationale: str = Field(min_length=1)
    resolved_gap_count: int = Field(default=0, ge=0)
    deferred_gap_count: int = Field(default=0, ge=0)
    exhausted_gap_count: int = Field(default=0, ge=0)
    duplicate_only_gap_count: int = Field(default=0, ge=0)
    blocking_gap_count: int = Field(default=0, ge=0)
    automation_ready_after_history_count: int = Field(default=0, ge=0)
    terminal_state: AutonomousLoopTerminalState | None = None
    terminal_state_reason: str | None = None
    changes_this_iteration: list[str] = Field(default_factory=list)
    unchanged_this_iteration: list[str] = Field(default_factory=list)
    remaining_deferred_reasons: list[str] = Field(default_factory=list)
    non_automation_ready_reasons: list[str] = Field(default_factory=list)
    gap_terminal_classifications: list[AutonomousLoopGapTerminalClassification] = Field(
        default_factory=list
    )
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


class AutonomousLoopIterationReport(StrictModel):
    """One iteration of the autonomous orchestration loop."""

    iteration: int = Field(ge=1)
    claim_evidence_map_path: str | None = None
    autonomous_plan_path: str | None = None
    autonomous_execution_report_path: str | None = None
    planned_spec_execution_report_path: str | None = None
    evidence_aware_refresh_report_path: str | None = None
    release_report_path: str | None = None
    reviewer_summary_path_optional: str | None = None
    strategy_diversification_report_path: str | None = None
    experiment_gap_routing_report_path: str | None = None
    capability_escalation_report_path: str | None = None
    sandbox_budget_report_path: str | None = None
    strategy_option_count: int = Field(default=0, ge=0)
    selected_strategy_count: int = Field(default=0, ge=0)
    routed_experiment_gap_count: int = Field(default=0, ge=0)
    routed_experiment_spec_count: int = Field(default=0, ge=0)
    sandbox_budget_runs_used: int = Field(default=0, ge=0)
    sandbox_budget_runs_remaining: int = Field(default=0, ge=0)
    sandbox_budget_exhausted: bool = False
    supported_claim_count: int = Field(default=0, ge=0)
    unsupported_claim_count: int = Field(default=0, ge=0)
    partial_claim_count: int = Field(default=0, ge=0)
    automation_ready_item_count: int = Field(default=0, ge=0)
    actions_applied: int = Field(default=0, ge=0)
    actions_deferred: int = Field(default=0, ge=0)
    actions_rejected: int = Field(default=0, ge=0)
    planned_specs_created: int = Field(default=0, ge=0)
    planned_specs_executed: int = Field(default=0, ge=0)
    experiment_artifacts_created: int = Field(default=0, ge=0)
    proof_artifacts_created: int = Field(default=0, ge=0)
    retrieval_artifacts_created: int = Field(default=0, ge=0)
    proof_escalation_attempt_count: int = Field(default=0, ge=0)
    retrieval_escalation_attempt_count: int = Field(default=0, ge=0)
    successful_escalation_count: int = Field(default=0, ge=0)
    deferred_after_escalation_count: int = Field(default=0, ge=0)
    manuscript_modified: bool = False
    release_status: str = Field(min_length=1)
    publication_ready: bool = False
    created_artifact_count: int = Field(default=0, ge=0)
    new_evidence_artifact_count: int = Field(default=0, ge=0)
    new_spec_count: int = Field(default=0, ge=0)
    manuscript_hash_before: str | None = None
    manuscript_hash_after: str | None = None
    claim_evidence_map_hash_before: str | None = None
    claim_evidence_map_hash_after: str | None = None
    duplicate_specs_skipped: int = Field(default=0, ge=0)
    gaps_marked_exhausted: int = Field(default=0, ge=0)
    iterations_without_progress: int = Field(default=0, ge=0)
    decision: AutonomousLoopDecision
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class AutonomousLoopRunReport(StrictModel):
    """Append-only autonomous loop orchestration report; not evidence."""

    run_id: str = Field(min_length=1)
    loop_id: str = Field(min_length=1)
    loop_backend: AutonomousLoopBackend = "deterministic"
    loop_status: AutonomousLoopStatus
    max_iterations: int = Field(ge=1)
    iterations_completed: int = Field(ge=0)
    stop_reason: AutonomousLoopStopReason
    started_at: str = Field(min_length=1)
    completed_at: str = Field(min_length=1)
    initial_release_status: str = Field(min_length=1)
    final_release_status: str = Field(min_length=1)
    initial_publication_ready: bool = False
    final_publication_ready: bool = False
    initial_claim_evidence_counts: dict[str, int] = Field(default_factory=dict)
    final_claim_evidence_counts: dict[str, int] = Field(default_factory=dict)
    initial_gap_counts: dict[str, int] = Field(default_factory=dict)
    final_gap_counts: dict[str, int] = Field(default_factory=dict)
    gap_attempt_history_path: str | None = None
    dedup_index_path: str | None = None
    duplicate_specs_skipped: int = Field(default=0, ge=0)
    gaps_marked_exhausted: int = Field(default=0, ge=0)
    iterations_without_progress: int = Field(default=0, ge=0)
    stopped_due_to_exhausted_gaps: bool = False
    strategy_diversification_enabled: bool = False
    strategy_diversification_report_paths: list[str] = Field(default_factory=list)
    strategy_option_count: int = Field(default=0, ge=0)
    selected_strategy_count: int = Field(default=0, ge=0)
    duplicate_strategy_count: int = Field(default=0, ge=0)
    experiment_routing_enabled: bool = False
    experiment_gap_routing_report_paths: list[str] = Field(default_factory=list)
    sandbox_budget_report_path: str | None = None
    sandbox_budget_policy: SandboxBudgetPolicy | None = None
    routed_experiment_gap_count: int = Field(default=0, ge=0)
    routed_experiment_spec_count: int = Field(default=0, ge=0)
    sandbox_budget_exhausted: bool = False
    sandbox_budget_runs_used: int = Field(default=0, ge=0)
    sandbox_budget_runs_remaining: int = Field(default=0, ge=0)
    empirical_gaps_created: int = Field(default=0, ge=0)
    empirical_gaps_routed: int = Field(default=0, ge=0)
    sandbox_experiments_completed: int = Field(default=0, ge=0)
    experiment_artifacts_ingested: int = Field(default=0, ge=0)
    terminal_state: AutonomousLoopTerminalState = "completed_all_supported"
    terminal_state_reason: str = Field(
        default="No terminal-state detail was recorded by this loop version.",
        min_length=1,
    )
    resolved_gap_count: int = Field(default=0, ge=0)
    deferred_gap_count: int = Field(default=0, ge=0)
    exhausted_gap_count: int = Field(default=0, ge=0)
    duplicate_only_gap_count: int = Field(default=0, ge=0)
    blocking_gap_count: int = Field(default=0, ge=0)
    automation_ready_after_history_count: int = Field(default=0, ge=0)
    empirical_paths_resolved: int = Field(default=0, ge=0)
    proof_paths_deferred: int = Field(default=0, ge=0)
    retrieval_paths_deferred: int = Field(default=0, ge=0)
    stopped_before_max_iterations: bool = False
    capability_escalation_enabled: bool = False
    capability_escalation_report_paths: list[str] = Field(default_factory=list)
    capability_escalation_status: str | None = None
    proof_escalation_attempt_count: int = Field(default=0, ge=0)
    retrieval_escalation_attempt_count: int = Field(default=0, ge=0)
    successful_escalation_count: int = Field(default=0, ge=0)
    deferred_after_escalation_count: int = Field(default=0, ge=0)
    capability_escalation_network_allowed: bool = False
    capability_escalation_external_tools_allowed: bool = False
    gap_terminal_classifications: list[AutonomousLoopGapTerminalClassification] = Field(
        default_factory=list
    )
    iterations: list[AutonomousLoopIterationReport] = Field(default_factory=list)
    artifacts_created: list[str] = Field(default_factory=list)
    requires_human_intervention: bool = False
    human_intervention_reason_optional: str | None = None
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    publication_ready: bool = False


class AutonomousLoopIndex(StrictModel):
    """Derived latest pointer over immutable autonomous loop runs."""

    run_id: str = Field(min_length=1)
    latest_loop_id: str = Field(min_length=1)
    loop_count: int = Field(ge=1)
    latest_loop_status: AutonomousLoopStatus
    latest_iterations_completed: int = Field(ge=0)
    latest_stop_reason: AutonomousLoopStopReason
    latest_artifact_paths: list[str] = Field(default_factory=list)
    latest_requires_human_intervention: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False


class FullPaperGenerationConfig(StrictModel):
    """Configuration for the non-evidence full-paper generation workflow."""

    run_id: str = Field(min_length=1)
    include_citations: bool = True
    citation_policy: Literal["none", "registry-only"] = "none"
    max_retrieval_sources: int = Field(default=5, ge=1, le=100)
    export_latex: bool = True
    critique: bool = True
    revise: bool = False
    apply_safe_fake_revision: bool = False
    reexport_latex_after_revision: bool = False
    render_check: bool = False
    allow_external_tools: bool = False
    latex_executable: str | None = None
    prose_backend: str = "fake"
    allow_external_calls: bool = False
    prose_model: str | None = None
    claim_adjudicator_backend: Literal["off", "fake", "openai"] = "off"
    claim_adjudicator_model: str | None = None
    quality_repair_backend: Literal["off", "deterministic", "fake", "openai"] = "off"
    quality_repair_model: str | None = None
    write_report: bool = False
    rerun_policy: RerunPolicy = RerunPolicy.FAIL_IF_EXISTS
    force: bool = False
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class FullPaperGenerationStep(StrictModel):
    """One deterministic full-paper workflow step."""

    step_name: str = Field(min_length=1)
    status: FullPaperGenerationStepStatus
    summary: str = Field(min_length=1)
    artifact_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_message: str | None = None
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


class FullPaperArtifactBundle(StrictModel):
    """Artifact IDs that make up one generated paper package."""

    run_id: str = Field(min_length=1)
    retrieval_report_artifact_id: str | None = None
    citation_registry_artifact_id: str | None = None
    literature_positioning_report_artifact_id: str | None = None
    citation_safety_report_artifact_id: str | None = None
    claim_support_audit_artifact_id: str | None = None
    manuscript_drafting_plan_artifact_id: str | None = None
    manuscript_drafting_report_artifact_id: str | None = None
    complete_manuscript_draft_artifact_id: str | None = None
    manuscript_assembly_report_artifact_id: str | None = None
    latex_artifact_id: str | None = None
    references_artifact_id: str | None = None
    latex_source_map_artifact_id: str | None = None
    latex_export_report_artifact_id: str | None = None
    latex_safety_report_artifact_id: str | None = None
    latex_compile_check_report_artifact_id: str | None = None
    paper_critic_report_artifact_id: str | None = None
    paper_revision_plan_artifact_id: str | None = None
    revision_safety_report_artifact_id: str | None = None
    revised_manuscript_draft_artifact_id: str | None = None
    paper_revision_result_artifact_id: str | None = None
    quality_repair_report_artifact_id: str | None = None
    revised_latex_artifact_id: str | None = None
    revised_references_artifact_id: str | None = None
    revised_latex_source_map_artifact_id: str | None = None
    revised_latex_export_report_artifact_id: str | None = None
    revised_latex_safety_report_artifact_id: str | None = None
    full_paper_generation_report_artifact_id: str | None = None
    full_paper_artifact_bundle_artifact_id: str | None = None
    artifact_ids: list[str] = Field(default_factory=list)
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class FullPaperGenerationReport(StrictModel):
    """Summary report for generated-paper package orchestration."""

    report_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    config: FullPaperGenerationConfig
    generation_status: FullPaperGenerationStatus
    steps: list[FullPaperGenerationStep] = Field(default_factory=list)
    artifact_bundle: FullPaperArtifactBundle
    warnings: list[str] = Field(default_factory=list)
    blocking_issues: list[str] = Field(default_factory=list)
    revision_applied: bool = False
    render_check_requested: bool = False
    publication_ready: bool = False
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class FullPaperGenerationResult(StrictModel):
    """Typed result for full-paper generation commands and protocol consumers."""

    run_id: str = Field(min_length=1)
    generation_status: FullPaperGenerationStatus
    report: FullPaperGenerationReport
    artifact_bundle: FullPaperArtifactBundle
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class CitationKey(StrictModel):
    """Deterministic citation-key assignment for one retrieval source."""

    citation_id: str = Field(min_length=1)
    citation_key: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    disambiguator: str | None = None


class CitationRecord(StrictModel):
    """Source metadata allowed for citation-safe literature positioning only."""

    citation_id: str = Field(min_length=1)
    citation_key: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=0)
    venue: str | None = None
    doi: str | None = None
    url: str | None = None
    provider: str = Field(min_length=1)
    retrieval_backend: str = Field(default="unknown", min_length=1)
    retrieved_at: str = Field(min_length=1)
    raw_metadata_hash: str = Field(min_length=1)
    source_artifact_id: str | None = None
    source_type: str = Field(default="retrieval_metadata", min_length=1)
    abstract_or_snippet: str | None = None
    allowed_citation_key: str | None = None
    trust_level: str = Field(default="metadata_only", min_length=1)
    source_status: Literal[
        "retrieved",
        "user_provided",
        "fixture",
        "rejected",
        "stale",
        "unverified_metadata",
    ] = "unverified_metadata"
    support_scope: list[str] = Field(default_factory=lambda: ["background_context"])
    supported_topics: list[str] = Field(default_factory=list)
    source_snippet: str | None = None
    source_summary: str | None = None
    fixture_only: bool = False
    retrieval_quality_status: str = Field(default="not_evaluated", min_length=1)
    relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)
    source_quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    accepted_for_registry: bool = True
    may_support_background_context: bool = True
    may_support_method_context: bool = False
    may_support_empirical_claims: bool = False
    may_support_proof_claims: bool = False
    may_support_novelty_claims: bool = False
    warnings: list[str] = Field(default_factory=list)
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    proves_novelty: bool = False
    claims_literature_coverage: bool = False


class BibliographyEntry(StrictModel):
    """Deterministic Markdown bibliography entry backed by source provenance."""

    citation_id: str = Field(min_length=1)
    citation_key: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    markdown: str = Field(min_length=1)
    has_source_provenance: bool
    warnings: list[str] = Field(default_factory=list)
    is_verification_evidence: bool = False
    proves_novelty: bool = False


class CitationRegistry(StrictModel):
    """Run-level citation registry derived from retrieval metadata."""

    run_id: str = Field(min_length=1)
    citations: list[CitationRecord] = Field(default_factory=list)
    bibliography: list[BibliographyEntry] = Field(default_factory=list)
    citation_key_policy: str = Field(min_length=1)
    citation_policy: Literal["none", "registry-only"] = "none"
    retrieval_backend: str = Field(default="none", min_length=1)
    retrieval_scope: str = Field(default="bounded", min_length=1)
    source_registry_hash: str = Field(min_length=1)
    source_count: int = Field(default=0, ge=0)
    accepted_source_count: int = Field(default=0, ge=0)
    rejected_source_count: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)
    fake: bool = True
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    proves_novelty: bool = False
    claims_literature_coverage: bool = False


class CitationUsage(StrictModel):
    """Observed citation markers in a manuscript draft."""

    citation_key: str = Field(min_length=1)
    count: int = Field(ge=0)
    known: bool
    citation_id: str | None = None


class CitationSafetyReport(StrictModel):
    """Safety report for citation use in a manuscript draft."""

    run_id: str = Field(min_length=1)
    safe: bool
    rejected: bool
    citation_usages: list[CitationUsage] = Field(default_factory=list)
    unknown_citation_keys: list[str] = Field(default_factory=list)
    invented_bibliography_keys: list[str] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    used_citation_keys: list[str] = Field(default_factory=list)
    used_citation_ids: list[str] = Field(default_factory=list)
    bibliography_entries_count: int = Field(default=0, ge=0)
    citation_policy: Literal["none", "registry-only"] = "none"
    citation_registry_source_count: int = Field(default=0, ge=0)
    registry_backed_citation_count: int = Field(default=0, ge=0)
    unregistered_citation_keys: list[str] = Field(default_factory=list)
    bibliography_registry_backed: bool = False
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    proves_novelty: bool = False


ClaimSupportClaimClass = Literal[
    "scaffold_statement",
    "problem_framing_statement",
    "method_description_statement",
    "evidence_boundary_statement",
    "limitation_statement",
    "provenance_statement",
    "source_context_claim",
    "literature_background_claim",
    "external_factual_claim",
    "pipeline_status_claim",
    "proof_claim",
    "experiment_claim",
    "novelty_claim",
    "publication_readiness_claim",
]


ClaimSupportStatus = Literal[
    "not_required_scaffold",
    "registry_supported",
    "evidence_artifact_supported",
    "registry_key_present_but_scope_mismatch",
    "missing_required_citation",
    "forbidden_claim_without_evidence",
    "unsupported_external_claim",
    "citation_as_validation_misuse",
]


ClaimAdjudicationCitationUse = Literal[
    "none",
    "background_context",
    "local_support",
    "misused_as_proof",
    "misused_as_validation",
    "misused_as_novelty",
    "misused_as_publication_readiness",
]


ClaimCitationRequirementReason = Literal[
    "positive_external_claim",
    "positive_source_context_claim",
    "positive_literature_claim",
    "current_run_status_no_citation_required",
    "absence_of_evidence_no_citation_required",
    "scaffold_role_no_citation_required",
    "evidence_boundary_no_citation_required",
    "claim_class_no_citation_required",
]


class ClaimAdjudication(StrictModel):
    """Bounded semantic classification of one manuscript sentence; never evidence."""

    sentence_id: str = Field(min_length=1)
    section_name: str = Field(min_length=1)
    sentence_hash: str = Field(min_length=64, max_length=64)
    adjudicated_claim_class: ClaimSupportClaimClass
    requires_citation: bool
    requires_citation_reason: ClaimCitationRequirementReason = "claim_class_no_citation_required"
    citation_use: ClaimAdjudicationCitationUse = "none"
    forbidden_claim_detected: bool = False
    citation_as_validation_misuse: bool = False
    publication_readiness_claim: bool = False
    reasoning_brief: str = Field(default="", max_length=400)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    adjudicator_backend: str = Field(default="deterministic_fallback", min_length=1)
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class ClaimSupportItem(StrictModel):
    """One deterministic manuscript sentence-to-source support classification."""

    sentence_id: str = Field(min_length=1)
    section_name: str = Field(min_length=1)
    sentence_text_hash: str = Field(min_length=64, max_length=64)
    sentence_snippet: str = Field(default="", max_length=240)
    claim_class: ClaimSupportClaimClass
    citation_keys_present: list[str] = Field(default_factory=list)
    requires_citation: bool = False
    requires_citation_reason: ClaimCitationRequirementReason = "claim_class_no_citation_required"
    required_support_type: str = Field(default="none", min_length=1)
    supporting_source_ids: list[str] = Field(default_factory=list)
    support_status: ClaimSupportStatus
    unsupported_reason: str | None = None
    paragraph_index: int = Field(default=0, ge=0)
    sentence_index: int = Field(default=0, ge=0)
    preliminary_claim_class: ClaimSupportClaimClass | None = None
    adjudicated_claim_class: ClaimSupportClaimClass | None = None
    adjudication_changed_class: bool = False
    adjudication_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    adjudication_reasoning_brief: str | None = Field(default=None, max_length=400)
    citation_use: ClaimAdjudicationCitationUse = "none"
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class ClaimSupportAuditReport(StrictModel):
    """Citation-placement and claim-support audit for a generated manuscript draft."""

    run_id: str = Field(min_length=1)
    citation_registry_present: bool
    citation_policy: Literal["none", "registry-only"] = "none"
    claim_support_items: list[ClaimSupportItem] = Field(default_factory=list)
    summary_counts: dict[str, int] = Field(default_factory=dict)
    unsupported_items: list[ClaimSupportItem] = Field(default_factory=list)
    citation_placement_violations: list[str] = Field(default_factory=list)
    citation_as_validation_misuse_count: int = Field(default=0, ge=0)
    claim_adjudication_enabled: bool = False
    claim_adjudicator_backend: str = "off"
    claim_adjudicator_model: str | None = None
    claim_adjudication_calls: int = Field(default=0, ge=0)
    adjudicated_sentence_count: int = Field(default=0, ge=0)
    deterministic_sentence_count: int = Field(default=0, ge=0)
    adjudication_items: list[ClaimAdjudication] = Field(default_factory=list)
    post_adjudication_summary_counts: dict[str, int] = Field(default_factory=dict)
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class LiteratureGapStatement(StrictModel):
    """Bounded literature-gap statement with explicit limitations."""

    statement_id: str = Field(min_length=1)
    problem_context: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    citation_ids: list[str] = Field(default_factory=list)
    citation_keys: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)
    non_exhaustive: bool = True
    is_verification_evidence: bool = False
    proves_novelty: bool = False


class LiteraturePositioningContract(StrictModel):
    """Bounded literature-positioning contract for manuscript drafting."""

    run_id: str = Field(min_length=1)
    contract_id: str = Field(min_length=1)
    problem_context: str = Field(min_length=1)
    retrieval_queries_used: list[str] = Field(default_factory=list)
    included_citation_ids: list[str] = Field(default_factory=list)
    excluded_or_deferred_sources: list[str] = Field(default_factory=list)
    literature_gap_statement: str = Field(min_length=1)
    novelty_positioning_statement: str = Field(min_length=1)
    coverage_limitations: list[str] = Field(default_factory=list)
    non_exhaustiveness_disclaimer: str = Field(min_length=1)
    fake: bool = True
    is_verification_evidence: bool = False
    proves_novelty: bool = False
    claims_literature_coverage: bool = False


class LiteraturePositioningReport(StrictModel):
    """Citation-safe literature-positioning report; not novelty proof."""

    run_id: str = Field(min_length=1)
    citation_registry_id: str = Field(min_length=1)
    contract: LiteraturePositioningContract
    gap_statement: LiteratureGapStatement
    markdown_intro_paragraph: str = Field(min_length=1)
    literature_limitations_paragraph: str = Field(min_length=1)
    citation_keys_used: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    proves_novelty: bool = False
    claims_literature_coverage: bool = False


class SectionDraftingTask(StrictModel):
    """One planned section drafting task with an explicit prose contract."""

    section_id: str = Field(min_length=1)
    section_title: str = Field(min_length=1)
    section_role: str = Field(min_length=1)
    narrative_role: list[NarrativeSectionRole] = Field(default_factory=list)
    allowed_claim_ids: list[str] = Field(default_factory=list)
    allowed_evidence_artifact_ids: list[str] = Field(default_factory=list)
    allowed_citation_ids: list[str] = Field(default_factory=list)
    allowed_citation_keys: list[str] = Field(default_factory=list)
    source_contract_hashes: dict[str, str] = Field(default_factory=dict)
    prose_contract: ProseSectionContract
    fake: bool = True
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


class ManuscriptDraftingPlan(StrictModel):
    """Plan for section-by-section manuscript drafting; not evidence."""

    run_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    manuscript_plan_id: str = Field(min_length=1)
    narrative_contract_id: str = Field(min_length=1)
    paper_shape_critique_id: str = Field(min_length=1)
    prose_backend: str = "fake"
    sections_count: int = Field(ge=0)
    tasks: list[SectionDraftingTask]
    warnings: list[str] = Field(default_factory=list)
    fake: bool = True
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


class SectionDraftSafetySummary(StrictModel):
    """Compact safety status for one generated section draft."""

    section_id: str = Field(min_length=1)
    safety_status: str = Field(min_length=1)
    safe: bool
    rejected: bool
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    used_claim_ids: list[str] = Field(default_factory=list)
    used_evidence_artifact_ids: list[str] = Field(default_factory=list)
    used_citation_ids: list[str] = Field(default_factory=list)
    used_citation_keys: list[str] = Field(default_factory=list)
    allowed_statement_classes_used: list[str] = Field(default_factory=list)
    safe_scaffold_sentences_retained: list[str] = Field(default_factory=list)
    unsafe_sentences_removed: list[str] = Field(default_factory=list)
    original_sentence_count: int = Field(default=0, ge=0)
    removed_sentence_count: int = Field(default=0, ge=0)
    retained_sentence_count: int = Field(default=0, ge=0)
    section_status: str = "retained"
    removal_reasons: list[str] = Field(default_factory=list)
    unsupported_sentences: list[str] = Field(default_factory=list)
    created_or_upgraded_labels: bool = False
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class SectionDraftingResult(StrictModel):
    """Generated and safety-checked section draft."""

    section_id: str = Field(min_length=1)
    section_title: str = Field(min_length=1)
    section_role: str = Field(min_length=1)
    narrative_role: list[NarrativeSectionRole] = Field(default_factory=list)
    draft_markdown: str = ""
    used_claim_ids: list[str] = Field(default_factory=list)
    used_evidence_artifact_ids: list[str] = Field(default_factory=list)
    used_citation_ids: list[str] = Field(default_factory=list)
    used_citation_keys: list[str] = Field(default_factory=list)
    allowed_statement_classes_used: list[str] = Field(default_factory=list)
    safety_status: str = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)
    unsupported_sentences: list[str] = Field(default_factory=list)
    unsafe_sentences_removed: list[str] = Field(default_factory=list)
    safe_scaffold_sentences_retained: list[str] = Field(default_factory=list)
    original_sentence_count: int = Field(default=0, ge=0)
    removed_sentence_count: int = Field(default=0, ge=0)
    retained_sentence_count: int = Field(default=0, ge=0)
    section_status: str = "retained"
    removal_reasons: list[str] = Field(default_factory=list)
    source_contract_hashes: dict[str, str] = Field(default_factory=dict)
    safe: bool
    rejected: bool
    safety_reasons: list[str] = Field(default_factory=list)
    draft: GeneratedSectionDraft | None = None
    safety_report: ProseSafetyReport
    fake: bool = True
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class CompleteMarkdownDraft(StrictModel):
    """Complete assembled Markdown manuscript draft; presentation context only."""

    run_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    markdown: str = Field(min_length=1)
    section_ids: list[str] = Field(default_factory=list)
    unsafe_section_ids: list[str] = Field(default_factory=list)
    claim_evidence_appendix: str = Field(min_length=1)
    provenance_appendix: str = Field(min_length=1)
    bibliography_markdown: str = ""
    literature_limitations: str = ""
    citation_registry_id: str | None = None
    warnings: list[str] = Field(default_factory=list)
    fake: bool = True
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class ManuscriptAssemblyReport(StrictModel):
    """Report for Markdown manuscript assembly; not scientific validation."""

    run_id: str = Field(min_length=1)
    assembled_sections: int = Field(ge=0)
    omitted_sections: list[str] = Field(default_factory=list)
    unsafe_section_ids: list[str] = Field(default_factory=list)
    sections_partially_sanitized: list[str] = Field(default_factory=list)
    sections_replaced_by_safe_fallback: list[str] = Field(default_factory=list)
    sections_omitted: list[str] = Field(default_factory=list)
    safe_scaffold_sentences_retained: int = Field(default=0, ge=0)
    unsafe_sentences_removed: int = Field(default=0, ge=0)
    sentence_salvage: list[dict[str, Any]] = Field(default_factory=list)
    allowed_statement_classes_used: list[str] = Field(default_factory=list)
    forbidden_labels_detected: list[str] = Field(default_factory=list)
    forbidden_labels_allowed_as_scaffold: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    draft_status: ManuscriptDraftStatus
    complete_markdown_artifact_id: str | None = None
    citation_safety_report_artifact_id: str | None = None
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False


class ManuscriptDraftingReport(StrictModel):
    """Machine-readable report for section-by-section manuscript drafting."""

    run_id: str = Field(min_length=1)
    drafting_plan_id: str = Field(min_length=1)
    prose_backend: str = "fake"
    sections_total: int = Field(ge=0)
    sections_safe: int = Field(ge=0)
    sections_unsafe: int = Field(ge=0)
    draft_status: ManuscriptDraftStatus
    section_summaries: list[SectionDraftSafetySummary]
    warnings: list[str] = Field(default_factory=list)
    manuscript_draft_artifact_id: str | None = None
    assembly_report_artifact_id: str | None = None
    citation_registry_artifact_id: str | None = None
    literature_positioning_artifact_id: str | None = None
    citation_safety_artifact_id: str | None = None
    fake: bool = True
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


class ChecklistItem(StrictModel):
    """One deterministic manuscript checklist item."""

    id: str = Field(min_length=1)
    category: ChecklistCategory
    description: str = Field(min_length=1)
    passed: bool
    reason: str = Field(min_length=1)


class ManuscriptChecklist(StrictModel):
    """Deterministic manuscript readiness checklist."""

    checklist_id: str = Field(min_length=1)
    items: list[ChecklistItem]
    failures_count: int = Field(ge=0)
    fake: bool = True


class DraftSection(StrictModel):
    """Section scaffold for the draft skeleton."""

    section_id: str = Field(min_length=1)
    section_title: str = Field(min_length=1)
    section_purpose: str = Field(min_length=1)
    allowed_claim_ids: list[str] = Field(default_factory=list)
    required_evidence_ids: list[str] = Field(default_factory=list)
    paragraph_placeholders: list[str]
    warnings: list[str] = Field(default_factory=list)


class DraftClaimPlaceholder(StrictModel):
    """Label-preserving placeholder for one allowed claim."""

    claim_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    claim_label: VerificationLabel
    placeholder_text: str = Field(min_length=1)
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    allowed_section: str = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)


class DraftSkeleton(StrictModel):
    """Structured deterministic draft scaffold, not polished prose."""

    skeleton_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    abstract_stub: str = Field(min_length=1)
    section_stubs: list[DraftSection]
    claim_placeholders: list[DraftClaimPlaceholder]
    evidence_links: list[ClaimEvidenceLink] = Field(default_factory=list)
    blocked_claim_warnings: list[str] = Field(default_factory=list)
    checklist: ManuscriptChecklist | None = None
    fake: bool = True


class PaperSection(StrictModel):
    """One section in the deterministic assembled paper skeleton."""

    section_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    claim_placeholders: list[DraftClaimPlaceholder] = Field(default_factory=list)
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class PaperAppendix(StrictModel):
    """One appendix in the deterministic assembled paper skeleton."""

    appendix_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content_lines: list[str]
    claim_ids: list[str] = Field(default_factory=list)
    artifact_ref_ids: list[str] = Field(default_factory=list)


class PaperAssemblyReport(StrictModel):
    """Readiness report for deterministic paper assembly."""

    sections_count: int = Field(ge=0)
    claims_included: int = Field(ge=0)
    claims_blocked: int = Field(ge=0)
    evidence_links_count: int = Field(ge=0)
    warnings: list[str] = Field(default_factory=list)
    ready_for_polished_prose: bool


class PaperSkeleton(StrictModel):
    """Paper-shaped deterministic scaffold. This is not verification evidence."""

    paper_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    abstract_scaffold: str = Field(min_length=1)
    sections: list[PaperSection]
    appendices: list[PaperAppendix]
    claim_placeholders: list[DraftClaimPlaceholder]
    provenance_refs: dict[str, ArtifactRef]
    fake: bool = True
    is_verification_evidence: bool = False


__all__ = [
    "InstantiationMap",
    "AbstractModel",
    "AbstractionReport",
    "AbstractionAttackReport",
    "FinalNucleus",
    "ClaimEvidenceLink",
    "ClaimEvidenceMapLink",
    "ClaimEvidenceMap",
    "AutonomousEvidenceGapPlanItem",
    "AutonomousEvidenceGapPlan",
    "AutonomousPlanExecutionAction",
    "AutonomousPlanExecutionReport",
    "AutonomousPlanExecutionIndex",
    "PlannedExperimentSpec",
    "ProofObligationSpec",
    "RetrievalExpansionRequest",
    "PlannedSpecExecutionItem",
    "PlannedSpecExecutionReport",
    "PlannedSpecExecutionIndex",
    "PythonExperimentSandboxManifest",
    "PythonExperimentSandboxRun",
    "PythonExperimentSandboxReport",
    "PythonExperimentSandboxIndex",
    "ExperimentTemplate",
    "ExperimentTemplateRegistry",
    "ExperimentTemplateSelection",
    "ExperimentGapRoutingItem",
    "ExperimentGapRoutingReport",
    "ExperimentGapRoutingIndex",
    "SandboxBudgetPolicy",
    "SandboxBudgetReport",
    "CapabilityEscalationPolicy",
    "CapabilityEscalationItem",
    "CapabilityEscalationReport",
    "CapabilityEscalationIndex",
    "FinalManuscriptSection",
    "FinalManuscriptClaimSummary",
    "FinalManuscriptStructuredDocument",
    "FinalManuscriptRegenerationReport",
    "FinalManuscriptRegenerationIndex",
    "GapAttemptRecord",
    "GapAttemptHistory",
    "PlannedSpecDuplicateRecord",
    "PlannedSpecDedupIndex",
    "AutonomousLoopDecision",
    "AutonomousLoopIterationReport",
    "AutonomousLoopRunReport",
    "AutonomousLoopIndex",
    "Claim",
    "BlockedClaim",
    "ClaimTable",
    "ManuscriptSectionPlan",
    "ManuscriptPlan",
    "NarrativeManuscriptContract",
    "MainMessageAssessment",
    "LiteraturePositioningAssessment",
    "ModelNotationAssessment",
    "MainResultAssessment",
    "NumericalStudyAssessment",
    "EmpiricalBoundaryAssessment",
    "AppendixAllocationAssessment",
    "PaperShapeScore",
    "PaperShapeCritique",
    "PaperCriticFinding",
    "PaperCriticReport",
    "PaperReleaseReadinessPreview",
    "SectionRevisionPlan",
    "PaperRevisionPlan",
    "PaperRevisionPatch",
    "RevisionSafetyReport",
    "PaperRevisionResult",
    "QualityRepairReport",
    "ProofArtifact",
    "ExperimentArtifact",
    "HumanReviewArtifact",
    "ReviewerBundleSummary",
    "FullPaperGenerationConfig",
    "FullPaperGenerationStep",
    "FullPaperArtifactBundle",
    "FullPaperGenerationReport",
    "FullPaperGenerationResult",
    "CitationKey",
    "CitationRecord",
    "CitationRegistry",
    "BibliographyEntry",
    "CitationUsage",
    "CitationSafetyReport",
    "LiteratureGapStatement",
    "LiteraturePositioningContract",
    "LiteraturePositioningReport",
    "SectionDraftingTask",
    "ManuscriptDraftingPlan",
    "SectionDraftSafetySummary",
    "SectionDraftingResult",
    "CompleteMarkdownDraft",
    "ManuscriptDraftingReport",
    "ManuscriptAssemblyReport",
    "ChecklistItem",
    "ManuscriptChecklist",
    "DraftSection",
    "DraftClaimPlaceholder",
    "DraftSkeleton",
    "PaperSection",
    "PaperAppendix",
    "PaperAssemblyReport",
    "PaperSkeleton",
]
