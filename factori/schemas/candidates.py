"""Candidate, score, budget, state, and controller action schemas."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from factori.schemas.artifacts import ArtifactRef
from factori.schemas.base import HASH_RE, SchemaError, StrictModel
from factori.schemas.enums import (
    MVP_ADMISSIBLE_DATA_REQUIREMENTS,
    BranchStatus,
    ControllerActionType,
    DataRequirement,
)
from factori.schemas.verification import VerificationState


class ConstraintSet(StrictModel):
    """User constraints over the candidate search space."""

    domain: str | None = None
    primitives: list[str] = Field(default_factory=list)
    method: str | None = None
    question: str | None = None
    hypothesis: str | None = None
    theory: str | None = None
    experiment: str | None = None
    baseline: str | None = None
    data_requirement: DataRequirement = DataRequirement.NO_DATA


class ScoreVector(StrictModel):
    """Continuous candidate scores in [0, 1]."""

    novelty: float = Field(ge=0.0, le=1.0)
    feasibility: float = Field(ge=0.0, le=1.0)
    verifiability: float = Field(ge=0.0, le=1.0)
    reviewer: float = Field(default=0.0, ge=0.0, le=1.0)
    difficulty: float = Field(default=0.5, ge=0.0, le=1.0)
    diversity: float = Field(default=0.0, ge=0.0, le=1.0)
    uncertainty: float = Field(default=0.0, ge=0.0, le=1.0)

    def base_score(self) -> float:
        """Return the base MVP score formula from the specification."""
        return (
            0.25 * self.novelty
            + 0.20 * self.feasibility
            + 0.20 * self.verifiability
            + 0.15 * self.reviewer
            + 0.10 * (1.0 - self.difficulty)
            + 0.10 * self.diversity
        )


class BudgetVector(StrictModel):
    """Independent non-negative resource caps or usage values."""

    api: float = Field(default=0.0, ge=0.0)
    retrieval: float = Field(default=0.0, ge=0.0)
    lean: float = Field(default=0.0, ge=0.0)
    gpu: float = Field(default=0.0, ge=0.0)


class LiteratureState(StrictModel):
    """Retrieval adequacy state for a candidate."""

    k: int = Field(default=0, ge=0)
    semantic: float = Field(default=0.0, ge=0.0, le=1.0)
    keyword: float = Field(default=0.0, ge=0.0, le=1.0)
    citation: float = Field(default=0.0, ge=0.0, le=1.0)
    diversity: float = Field(default=0.0, ge=0.0, le=1.0)
    adversarial: float = Field(default=0.0, ge=0.0, le=1.0)
    novelty_risk: float = Field(default=1.0, ge=0.0, le=1.0)
    closest_priors: list[str] = Field(default_factory=list)

    @property
    def adequacy(self) -> float:
        """Equal-weight MVP adequacy score over retrieval channels."""
        return (
            self.semantic + self.keyword + self.citation + self.diversity + self.adversarial
        ) / 5.0


class ReviewReport(StrictModel):
    """Structured reviewer critique."""

    id: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    scores: ScoreVector
    objections: list[str] = Field(default_factory=list)
    recommendation: BranchStatus | None = None


class Candidate(StrictModel):
    """Minimal candidate branch representation."""

    id: str = Field(min_length=1)
    parent_candidate_id: str | None = None
    variant_type: str | None = None
    constraints: ConstraintSet = Field(default_factory=ConstraintSet)
    domain: str | None = None
    primitives: list[str] = Field(default_factory=list)
    method: str | None = None
    question: str = Field(min_length=1)
    hypothesis: str | None = None
    theory: str | None = None
    experiment: str | None = None
    baseline: str | None = None
    data_requirement: DataRequirement = DataRequirement.NO_DATA
    literature: LiteratureState = Field(default_factory=LiteratureState)
    verification: VerificationState = Field(default_factory=VerificationState)
    reviews: list[ReviewReport] = Field(default_factory=list)
    artifacts: list[ArtifactRef] = Field(default_factory=list)
    symbolic_state: dict[str, Any] = Field(default_factory=dict)
    status: BranchStatus = BranchStatus.ACTIVE

    def is_mvp_admissible(self) -> bool:
        """Return whether this candidate passes the current MVP data gate."""
        return self.data_requirement in MVP_ADMISSIBLE_DATA_REQUIREMENTS

    def require_mvp_admissible(self) -> None:
        """Raise if this candidate requires real data under the MVP policy."""
        if not self.is_mvp_admissible():
            raise SchemaError("PublicDownload and UserProvided candidates are deferred in the MVP")


class ControllerAction(StrictModel):
    """A deterministic controller or CLI action."""

    id: str = Field(min_length=1)
    action_type: ControllerActionType
    run_id: str = Field(min_length=1)
    candidate_id: str | None = None
    reason: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)


class RunState(StrictModel):
    """Minimal reproducible run state snapshot."""

    run_id: str = Field(min_length=1)
    constraints: ConstraintSet = Field(default_factory=ConstraintSet)
    candidates: list[Candidate] = Field(default_factory=list)
    budget: BudgetVector = Field(default_factory=BudgetVector)
    ledger_head: str | None = None
    status: BranchStatus = BranchStatus.ACTIVE
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("ledger_head")
    @classmethod
    def validate_ledger_head(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not HASH_RE.fullmatch(value):
            raise ValueError("ledger_head must be a lowercase SHA-256 hex digest")
        return value


def assert_mvp_data_admissible(candidate: Candidate) -> None:
    """Validate the MVP data gate for a candidate."""
    candidate.require_mvp_admissible()

__all__ = [
    "ConstraintSet",
    "ScoreVector",
    "BudgetVector",
    "LiteratureState",
    "ReviewReport",
    "Candidate",
    "ControllerAction",
    "RunState",
    "assert_mvp_data_admissible",
]
