"""Strict Pydantic schemas for the fActorI deterministic foundation."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class SchemaError(ValueError):
    """Raised when a schema object violates an MVP invariant."""


class StrictModel(BaseModel):
    """Base model with closed fields for reproducible contracts."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class DataRequirement(StrEnum):
    """Data access regimes from the MVP data gate."""

    NO_DATA = "NoData"
    SYNTHETIC_ONLY = "SyntheticOnly"
    PUBLIC_DOWNLOAD = "PublicDownload"
    USER_PROVIDED = "UserProvided"


MVP_ADMISSIBLE_DATA_REQUIREMENTS = frozenset(
    {DataRequirement.NO_DATA, DataRequirement.SYNTHETIC_ONLY}
)


class VerificationLabel(StrEnum):
    """Verification and epistemic labels required by the specification."""

    LEAN_VERIFIED = "LeanVerified"
    EXPERIMENT_VERIFIED = "ExperimentVerified"
    SYNTHETIC_EXPERIMENT_VERIFIED = "SyntheticExperimentVerified"
    REAL_DATA_EXPERIMENT_VERIFIED = "RealDataExperimentVerified"
    CONJECTURE = "Conjecture"
    NEGATIVE_RESULT = "NegativeResult"
    LIMITATION = "Limitation"
    UNSUPPORTED = "Unsupported"


class BranchStatus(StrEnum):
    """Branch and run status labels for the MVP."""

    ACTIVE = "Active"
    PRUNED_DUPLICATE = "PrunedDuplicate"
    REJECTED_RED_TEAM = "RejectedRedTeam"
    PRUNED_UNCERTAIN = "PrunedUncertain"
    DEFERRED_REAL_DATA_CANDIDATE = "DeferredRealDataCandidate"
    REQUIRES_REAL_DATA = "RequiresRealData"
    INSUFFICIENT_RETRIEVAL_ADEQUACY = "InsufficientRetrievalAdequacy"
    STAGNATION_STOP = "StagnationStop"
    STOP_FAILURE = "StopFailure"
    STOP_SUCCESS = "StopSuccess"
    NEEDS_HUMAN_TAIL_ESCALATION = "NeedsHumanTailEscalation"


class ArtifactType(StrEnum):
    """Artifact categories mapped to the local run directory structure."""

    CANDIDATE = "candidate"
    SCORE = "score"
    REPORT = "report"
    LITERATURE = "literature"
    LEAN = "lean"
    EXPERIMENT = "experiment"
    LOG = "log"
    LATEX = "latex"


class ControllerActionType(StrEnum):
    """Current deterministic action types.

    TODO: Extend this enum as the real controller modules are introduced.
    """

    INIT_RUN = "InitRun"
    ADD_CANDIDATE = "AddCandidate"
    WRITE_ARTIFACT = "WriteArtifact"
    VALIDATE_RUN = "ValidateRun"
    CONTROLLER_ACTION = "ControllerAction"
    STAGE_A_STARTED = "StageAStarted"
    STAGE0_OPPORTUNITY_DISCOVERY = "Stage0OpportunityDiscovery"
    STAGE0_SKIPPED = "Stage0Skipped"
    STAGE_A_DATA_GATE_DEFERRED = "StageADataGateDeferred"
    STAGE_A_CANDIDATE_GENERATED = "StageACandidateGenerated"
    STAGE_A_SCORE_COMPUTED = "StageAScoreComputed"
    STAGE_A_DUPLICATE_PRUNED = "StageADuplicatePruned"
    STAGE_A_GATE_PRUNED = "StageAGatePruned"
    STAGE_A_SURVIVORS_SELECTED = "StageASurvivorsSelected"
    STAGE_A_REPORT_WRITTEN = "StageAReportWritten"
    QUESTIONER_CHECK = "QuestionerCheck"
    RETRIEVAL_ADEQUACY_DEMO = "RetrievalAdequacyDemo"
    STAGNATION_DEMO = "StagnationDemo"


class QuestionCategory(StrEnum):
    """Strategic Questioner categories from the control-layer specification."""

    MICRO_CHECK = "MicroCheck"
    CLARITY = "Clarity"
    NOVELTY = "Novelty"
    EVIDENCE_SUFFICIENCY = "EvidenceSufficiency"
    SIMPLICITY = "Simplicity"
    DATA_SUFFICIENCY = "DataSufficiency"
    BASELINE_STRENGTH = "BaselineStrength"
    REPAIR_SUFFICIENCY = "RepairSufficiency"
    LITERATURE_ADEQUACY = "LiteratureAdequacy"
    VERIFICATION_READINESS = "VerificationReadiness"
    ABSTRACTION = "Abstraction"
    STOPPING = "Stopping"


class ControllerDecisionAction(StrEnum):
    """Allowed deterministic control actions."""

    CONTINUE = "Continue"
    SIMPLIFY = "Simplify"
    NARROW_SCOPE = "NarrowScope"
    INCREASE_RETRIEVAL_ADEQUACY = "IncreaseRetrievalAdequacy"
    ADD_SYNTHETIC_DATA = "AddSyntheticData"
    STRENGTHEN_BASELINE = "StrengthenBaseline"
    RUN_ABLATION = "RunAblation"
    DOWNGRADE_CLAIM = "DowngradeClaim"
    CONVERT_TO_NEGATIVE_RESULT = "ConvertToNegativeResult"
    ATTEMPT_ABSTRACTION = "AttemptAbstraction"
    STOP_FAILURE = "StopFailure"
    STOP_SUCCESS = "StopSuccess"
    ASK_HUMAN = "AskHuman"


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


class RetrievalAdequacyCertificate(StrictModel):
    """Skeleton retrieval adequacy certificate."""

    semantic: float = Field(ge=0.0, le=1.0)
    keyword: float = Field(ge=0.0, le=1.0)
    citation: float = Field(ge=0.0, le=1.0)
    diversity: float = Field(ge=0.0, le=1.0)
    adversarial: float = Field(ge=0.0, le=1.0)
    weights: dict[str, float]
    rho_adequacy: float = Field(ge=0.0, le=1.0)
    tau_adequacy: float = Field(ge=0.0, le=1.0)
    passed: bool
    status: BranchStatus
    fake: bool = True


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


class ArtifactRef(StrictModel):
    """Reference to an artifact stored on the local filesystem."""

    id: str = Field(min_length=1)
    type: ArtifactType
    path: str = Field(min_length=1)
    content_hash: str
    producing_commit_hash: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("content_hash", "producing_commit_hash")
    @classmethod
    def validate_hash(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not HASH_RE.fullmatch(value):
            raise ValueError("hash values must be lowercase SHA-256 hex digests")
        return value

    def is_mvp_verification_evidence(self) -> bool:
        """Return whether this artifact may serve as verification evidence.

        LaTeX is presentation only. Markdown is also treated as presentation in this MVP,
        even when it is stored under reports.
        """
        suffix = self.path.rsplit(".", maxsplit=1)[-1].lower() if "." in self.path else ""
        if self.type == ArtifactType.LATEX:
            return False
        if suffix in {"md", "markdown", "tex", "pdf"}:
            return False
        return self.type in {
            ArtifactType.CANDIDATE,
            ArtifactType.SCORE,
            ArtifactType.LITERATURE,
            ArtifactType.LEAN,
            ArtifactType.EXPERIMENT,
            ArtifactType.LOG,
            ArtifactType.REPORT,
        }

    def require_evidence_ready(self) -> None:
        """Raise if the artifact cannot be used as verification evidence."""
        if not self.is_mvp_verification_evidence():
            raise SchemaError("presentation artifacts are not verification evidence")
        if self.producing_commit_hash is None:
            raise SchemaError("evidence artifacts require a producing commit hash")


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


class VerificationState(StrictModel):
    """Current verification labels and evidence artifacts."""

    labels: list[VerificationLabel] = Field(default_factory=lambda: [VerificationLabel.UNSUPPORTED])
    evidence_artifacts: list[ArtifactRef] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def evidence_must_be_ready(self) -> VerificationState:
        for artifact in self.evidence_artifacts:
            try:
                artifact.require_evidence_ready()
            except SchemaError as exc:
                raise ValueError(str(exc)) from exc
        return self


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


class LedgerCommit(StrictModel):
    """Immutable ledger commit."""

    commit_hash: str
    parent_hash: str | None = None
    run_id: str = Field(min_length=1)
    candidate_id: str | None = None
    action_type: ControllerActionType
    payload: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[ArtifactRef] = Field(default_factory=list)
    timestamp: str = Field(min_length=1)

    @field_validator("commit_hash", "parent_hash")
    @classmethod
    def validate_hash(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if not HASH_RE.fullmatch(value):
            raise ValueError("hash values must be lowercase SHA-256 hex digests")
        return value


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


def parse_model_json(model_type: type[StrictModel], data: str) -> StrictModel:
    """Deserialize a strict model and keep ValidationError in the public schema module."""
    try:
        return model_type.model_validate_json(data)
    except ValidationError:
        raise
