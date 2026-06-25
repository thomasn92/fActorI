"""Stage B and Stage C selection report schemas."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from factori.schemas.base import StrictModel
from factori.schemas.enums import (
    BranchStatus,
    BridgeRepairAction,
    ControllerDecisionAction,
    ReviewerDisagreementType,
    ReviewerRecommendation,
)
from factori.schemas.retrieval import RetrievalAdequacyCertificate


class StageBReviewerReport(StrictModel):
    """One Stage B structural reviewer report without verification authority."""

    reviewer_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    novelty_score: float = Field(ge=0.0, le=1.0)
    feasibility_score: float = Field(ge=0.0, le=1.0)
    verifiability_score: float = Field(ge=0.0, le=1.0)
    clarity_score: float = Field(ge=0.0, le=1.0)
    significance_score: float = Field(ge=0.0, le=1.0)
    objections: list[str] = Field(default_factory=list)
    recommendation: ReviewerRecommendation
    metadata: dict[str, Any] = Field(default_factory=dict)
    fake: bool = True
    is_verification_evidence: bool = False
    scientific_approval: bool = False

    def aggregate_score(self) -> float:
        """Mean reviewer score used for deterministic disagreement."""
        return (
            self.novelty_score
            + self.feasibility_score
            + self.verifiability_score
            + self.clarity_score
            + self.significance_score
        ) / 5.0


class ReviewerPanelResult(StrictModel):
    """Reviewer panel output and disagreement resolution."""

    candidate_id: str = Field(min_length=1)
    reports: list[StageBReviewerReport]
    aggregate_scores: list[float]
    disagreement: float = Field(ge=0.0)
    disagreement_type: ReviewerDisagreementType
    excluded_reviewer_id: str | None = None
    resolved_aggregate_score: float = Field(ge=0.0, le=1.0)
    preserved: bool = False
    rejected: bool = False


class BridgeReport(StrictModel):
    """Deterministic bridge validation report."""

    candidate_id: str = Field(min_length=1)
    map_score: float = Field(ge=0.0, le=1.0)
    transfer_score: float = Field(ge=0.0, le=1.0)
    baseline_score: float = Field(ge=0.0, le=1.0)
    data_score: float = Field(ge=0.0, le=1.0)
    falsify_score: float = Field(ge=0.0, le=1.0)
    nondecorative_score: float = Field(ge=0.0, le=1.0)
    survival_score: float = Field(ge=0.0, le=1.0)
    survives: bool
    repair_attempted: bool = False
    repair_action: BridgeRepairAction | None = None
    final_status: BranchStatus = BranchStatus.ACTIVE


class BaselineReport(StrictModel):
    """Deterministic baseline validation report."""

    candidate_id: str = Field(min_length=1)
    baseline_strength: float = Field(ge=0.0, le=1.0)
    candidate_score_advantage: float = Field(ge=-1.0, le=1.0)
    baseline_valid: bool
    repairable: bool
    routed_action: ControllerDecisionAction


class RedTeamReport(StrictModel):
    """Deterministic Stage B red-team report."""

    candidate_id: str = Field(min_length=1)
    retrieval_certificate: RetrievalAdequacyCertificate
    novelty_risk: float = Field(ge=0.0, le=1.0)
    triviality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    triviality_passed: bool = True
    redteam_rejection: bool = False
    stage_c_ready: bool = False
    status: BranchStatus = BranchStatus.ACTIVE


class NoveltyAttackResult(StrictModel):
    """Deterministic novelty attack result for Stage C selection."""

    candidate_id: str = Field(min_length=1)
    rt_novelty: float = Field(ge=0.0, le=1.0)
    novelty_risk: float = Field(ge=0.0, le=1.0)
    near_duplicate_reason: str | None = None
    passed: bool


class UncertaintyEstimate(StrictModel):
    """Deterministic score uncertainty estimate."""

    candidate_id: str = Field(min_length=1)
    s_hat: float = Field(ge=0.0, le=1.0)
    u_s: float = Field(ge=0.0, le=1.0)
    s_lower: float = Field(ge=0.0, le=1.0)
    tau_s: float = Field(ge=0.0, le=1.0)
    passed: bool
    components: dict[str, float]


class StageCRedTeamSelectionReport(StrictModel):
    """Aggregated pre-Stage-C red-team report."""

    candidate_id: str = Field(min_length=1)
    novelty: NoveltyAttackResult
    rt_bridge: float = Field(ge=0.0, le=1.0)
    rt_baseline: float = Field(ge=0.0, le=1.0)
    rt_triviality: float = Field(ge=0.0, le=1.0)
    rt_retrieval: float = Field(ge=0.0, le=1.0)
    rt_total: float = Field(ge=0.0, le=1.0)
    rt_threshold: float = Field(ge=0.0, le=1.0)
    retrieval_certificate: RetrievalAdequacyCertificate
    redteam_passed: bool
    stage_c_ready: bool
    status: BranchStatus


class BudgetSelectionReport(StrictModel):
    """Deterministic budget selector output."""

    max_stage_c_candidates: int = Field(ge=0)
    selected_candidate_ids: list[str]
    budget_deferred_candidate_ids: list[str]
    cost_aware_scores: dict[str, float]

__all__ = [
    "StageBReviewerReport",
    "ReviewerPanelResult",
    "BridgeReport",
    "BaselineReport",
    "RedTeamReport",
    "NoveltyAttackResult",
    "UncertaintyEstimate",
    "StageCRedTeamSelectionReport",
    "BudgetSelectionReport",
]
