"""Synthesis, claim, manuscript, draft, and paper-shape schemas."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from factori.schemas.adapters import GeneratedSectionDraft, ProseSafetyReport, ProseSectionContract
from factori.schemas.artifacts import ArtifactRef
from factori.schemas.base import StrictModel
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


class FullPaperGenerationConfig(StrictModel):
    """Configuration for the non-evidence full-paper generation workflow."""

    run_id: str = Field(min_length=1)
    include_citations: bool = True
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
    citation_registry_artifact_id: str | None = None
    literature_positioning_report_artifact_id: str | None = None
    citation_safety_report_artifact_id: str | None = None
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
    retrieved_at: str = Field(min_length=1)
    raw_metadata_hash: str = Field(min_length=1)
    source_artifact_id: str | None = None
    warnings: list[str] = Field(default_factory=list)
    is_verification_evidence: bool = False
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
    source_registry_hash: str = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)
    fake: bool = True
    is_verification_evidence: bool = False
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
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    proves_novelty: bool = False


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
    unsupported_sentences: list[str] = Field(default_factory=list)
    created_or_upgraded_labels: bool = False
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


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
    safety_status: str = Field(min_length=1)
    warnings: list[str] = Field(default_factory=list)
    unsupported_sentences: list[str] = Field(default_factory=list)
    source_contract_hashes: dict[str, str] = Field(default_factory=dict)
    safe: bool
    rejected: bool
    safety_reasons: list[str] = Field(default_factory=list)
    draft: GeneratedSectionDraft | None = None
    safety_report: ProseSafetyReport
    fake: bool = True
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


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


class ManuscriptAssemblyReport(StrictModel):
    """Report for Markdown manuscript assembly; not scientific validation."""

    run_id: str = Field(min_length=1)
    assembled_sections: int = Field(ge=0)
    omitted_sections: list[str] = Field(default_factory=list)
    unsafe_section_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    draft_status: ManuscriptDraftStatus
    complete_markdown_artifact_id: str | None = None
    citation_safety_report_artifact_id: str | None = None
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False


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
