"""Synthesis, claim, manuscript, draft, and paper-shape schemas."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from factori.schemas.artifacts import ArtifactRef
from factori.schemas.base import StrictModel
from factori.schemas.enums import (
    ArtifactType,
    ChecklistCategory,
    FinalNucleusType,
    NarrativeSectionRole,
    PaperShapeStatus,
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
