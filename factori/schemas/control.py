"""Control-layer and runtime-summary schemas."""

from __future__ import annotations

from pydantic import Field

from factori.schemas.base import StrictModel
from factori.schemas.enums import (
    BranchStatus,
    ControllerDecisionAction,
    QuestionCategory,
    VerificationLabel,
)


class Question(StrictModel):
    """A selected deterministic diagnostic question."""

    id: str = Field(min_length=1)
    category: QuestionCategory
    prompt: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class AutonomyContext(StrictModel):
    """Inputs to the autonomy contract."""

    candidate_id: str | None = None
    decision_uncertainty: float = Field(default=0.0, ge=0.0, le=1.0)
    action_risk: float = Field(default=0.0, ge=0.0, le=1.0)
    extra_budget_required: bool = False
    irreversible_decision: bool = False
    external_access_required: bool = False
    user_preference_needed: bool = False
    candidate_value: float = Field(default=0.0, ge=0.0, le=1.0)


class StagnationEvent(StrictModel):
    """One compact event used to compute the global stagnation index."""

    action: str = Field(min_length=1)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    verification_label: VerificationLabel = VerificationLabel.UNSUPPORTED
    status: BranchStatus = BranchStatus.ACTIVE


class StagnationState(StrictModel):
    """Deterministic global stagnation index result."""

    candidate_id: str | None = None
    stagnation_count: int = Field(ge=0)
    stagnant: bool
    forced_actions: list[ControllerDecisionAction] = Field(default_factory=list)
    high_value: bool = False
    high_uncertainty: bool = False
    can_ask_human: bool = False


class RuntimeSummary(StrictModel):
    """Compressed runtime context. This is explicitly not provenance."""

    candidate_id: str | None = None
    action_count: int = Field(ge=0)
    last_action: str | None = None
    failed_repair_count: int = Field(ge=0)
    last_score: float | None = Field(default=None, ge=0.0, le=1.0)
    best_score: float | None = Field(default=None, ge=0.0, le=1.0)
    verification_label: VerificationLabel = VerificationLabel.UNSUPPORTED
    status: BranchStatus = BranchStatus.ACTIVE
    short_summary: str = Field(min_length=1)
    is_provenance: bool = False
    source_of_truth: str = "ledger"

__all__ = [
    "Question",
    "AutonomyContext",
    "StagnationEvent",
    "StagnationState",
    "RuntimeSummary",
]
