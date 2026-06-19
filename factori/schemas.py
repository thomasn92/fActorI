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
    FALSE_BRIDGE = "FalseBridge"
    TRIVIAL_THEOREM_CANDIDATE = "TrivialTheoremCandidate"
    STAGE_C_READY = "StageCReady"
    BUDGET_DEFERRED = "BudgetDeferred"


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
    STAGE_B_STARTED = "StageBStarted"
    STAGE_B_CHILD_GENERATED = "StageBChildGenerated"
    STAGE_B_REVIEWERS_RUN = "StageBReviewersRun"
    STAGE_B_DISAGREEMENT_RESOLVED = "StageBDisagreementResolved"
    STAGE_B_BRIDGE_CHECKED = "StageBBridgeChecked"
    STAGE_B_BRIDGE_REPAIRED = "StageBBridgeRepaired"
    STAGE_B_BASELINE_CHECKED = "StageBBaselineChecked"
    STAGE_B_REDTEAM_CHECKED = "StageBRedteamChecked"
    STAGE_B_QUESTIONER_ROUTED = "StageBQuestionerRouted"
    STAGE_B_SCORE_COMPUTED = "StageBScoreComputed"
    STAGE_B_GATE_PRUNED = "StageBGatePruned"
    STAGE_B_SURVIVORS_SELECTED = "StageBSurvivorsSelected"
    STAGE_B_REPORT_WRITTEN = "StageBReportWritten"
    STAGE_C_SELECTION_STARTED = "StageCSelectionStarted"
    STAGE_C_REDTEAM_AGGREGATED = "StageCRedteamAggregated"
    STAGE_C_UNCERTAINTY_COMPUTED = "StageCUncertaintyComputed"
    STAGE_C_SCORE_COMPUTED = "StageCScoreComputed"
    STAGE_C_SELECTION_DECIDED = "StageCSelectionDecided"
    STAGE_C_BUDGET_SELECTED = "StageCBudgetSelected"
    STAGE_C_SELECTION_REPORT_WRITTEN = "StageCSelectionReportWritten"
    STAGE_C_VERIFICATION_STARTED = "StageCVerificationStarted"
    STAGE_C_PROOF_VALIDATED = "StageCProofValidated"
    STAGE_C_SYNTHETIC_EXPERIMENT_RUN = "StageCSyntheticExperimentRun"
    STAGE_C_NO_DATA_VALIDATED = "StageCNoDataValidated"
    STAGE_C_VERIFICATION_DECIDED = "StageCVerificationDecided"
    STAGE_C_VERIFICATION_REPORT_WRITTEN = "StageCVerificationReportWritten"
    ABSTRACT_SYNTHESIS_STARTED = "AbstractSynthesisStarted"
    ABSTRACT_MODEL_PROPOSED = "AbstractModelProposed"
    ABSTRACTION_ATTACK_RUN = "AbstractionAttackRun"
    ABSTRACTION_REPORT_WRITTEN = "AbstractionReportWritten"
    FINAL_NUCLEUS_SELECTED = "FinalNucleusSelected"
    ABSTRACT_SYNTHESIS_REPORT_WRITTEN = "AbstractSynthesisReportWritten"
    MANUSCRIPT_PLANNING_STARTED = "ManuscriptPlanningStarted"
    CLAIM_TABLE_BUILT = "ClaimTableBuilt"
    BLOCKED_CLAIMS_IDENTIFIED = "BlockedClaimsIdentified"
    MANUSCRIPT_PLAN_BUILT = "ManuscriptPlanBuilt"
    MANUSCRIPT_PLAN_REPORT_WRITTEN = "ManuscriptPlanReportWritten"
    DRAFT_SKELETON_STARTED = "DraftSkeletonStarted"
    DRAFT_SKELETON_BUILT = "DraftSkeletonBuilt"
    MANUSCRIPT_CHECKLIST_BUILT = "ManuscriptChecklistBuilt"
    DRAFT_SKELETON_REPORT_WRITTEN = "DraftSkeletonReportWritten"
    MANUSCRIPT_CHECKLIST_REPORT_WRITTEN = "ManuscriptChecklistReportWritten"
    RESEARCH_OBJECT_PACKAGING_STARTED = "ResearchObjectPackagingStarted"
    ARTIFACT_MANIFEST_WRITTEN = "ArtifactManifestWritten"
    LEDGER_SUMMARY_WRITTEN = "LedgerSummaryWritten"
    BRANCH_OUTCOMES_WRITTEN = "BranchOutcomesWritten"
    REPRODUCIBILITY_MANIFEST_WRITTEN = "ReproducibilityManifestWritten"
    RESEARCH_OBJECT_WRITTEN = "ResearchObjectWritten"
    PAPER_ASSEMBLY_STARTED = "PaperAssemblyStarted"
    PAPER_SKELETON_WRITTEN = "PaperSkeletonWritten"
    PAPER_ASSEMBLY_REPORT_WRITTEN = "PaperAssemblyReportWritten"
    FINAL_AUDIT_STARTED = "FinalAuditStarted"
    FINAL_AUDIT_REPORT_WRITTEN = "FinalAuditReportWritten"
    RELEASE_GATE_DECIDED = "ReleaseGateDecided"
    EXPORT_PREPARATION_STARTED = "ExportPreparationStarted"
    PROSE_GENERATION_CONTRACT_WRITTEN = "ProseGenerationContractWritten"
    LATEX_EXPORT_PLAN_WRITTEN = "LatexExportPlanWritten"
    EXPORT_SECTION_MAP_WRITTEN = "ExportSectionMapWritten"
    EXPORT_CLAIM_MAP_WRITTEN = "ExportClaimMapWritten"
    EXPORT_READINESS_REPORT_WRITTEN = "ExportReadinessReportWritten"
    EXPORT_BUNDLE_MANIFEST_WRITTEN = "ExportBundleManifestWritten"


class ReleaseGateStatus(StrEnum):
    """Final deterministic release gate statuses."""

    RELEASE_READY = "ReleaseReady"
    RELEASE_BLOCKED = "ReleaseBlocked"
    RELEASE_READY_WITH_WARNINGS = "ReleaseReadyWithWarnings"


class ReplayStatus(StrEnum):
    """Read-only replay verification statuses."""

    REPLAY_VERIFIED = "ReplayVerified"
    REPLAY_VERIFIED_WITH_WARNINGS = "ReplayVerifiedWithWarnings"
    REPLAY_FAILED = "ReplayFailed"


class AuditCategory(StrEnum):
    """Final audit check categories."""

    LEDGER_INTEGRITY = "LedgerIntegrity"
    ARTIFACT_INTEGRITY = "ArtifactIntegrity"
    EVIDENCE_BOUNDARY = "EvidenceBoundary"
    CLAIM_LABEL_PRESERVATION = "ClaimLabelPreservation"
    SYNTHETIC_DATA_BOUNDARY = "SyntheticDataBoundary"
    BLOCKED_CLAIM_HANDLING = "BlockedClaimHandling"
    PROVENANCE_COMPLETENESS = "ProvenanceCompleteness"
    RESEARCH_OBJECT_COMPLETENESS = "ResearchObjectCompleteness"
    PAPER_SKELETON_CONSISTENCY = "PaperSkeletonConsistency"
    REPRODUCIBILITY_READINESS = "ReproducibilityReadiness"
    HUMAN_ESCALATION_POLICY = "HumanEscalationPolicy"


class AuditCheckStatus(StrEnum):
    """Per-check audit statuses."""

    PASS = "Pass"
    WARNING = "Warning"
    FAIL = "Fail"
    NOT_APPLICABLE = "NotApplicable"


class AuditSeverity(StrEnum):
    """Audit finding severities."""

    INFO = "Info"
    WARNING = "Warning"
    BLOCKING = "Blocking"


class BranchVerificationType(StrEnum):
    """Deterministic Stage C verification branch types."""

    MATHEMATICAL = "Mathematical"
    SYNTHETIC_EMPIRICAL = "SyntheticEmpirical"
    NO_DATA_METHODOLOGICAL = "NoDataMethodological"
    UNSUPPORTED = "Unsupported"


class FinalNucleusType(StrEnum):
    """Final synthesis nucleus types."""

    ABSTRACT_NUCLEUS = "AbstractNucleus"
    BRANCH_NUCLEUS = "BranchNucleus"


class ChecklistCategory(StrEnum):
    """Draft/manuscript checklist categories."""

    EVIDENCE_BOUNDARY = "EvidenceBoundary"
    CLAIM_LABELS = "ClaimLabels"
    SYNTHETIC_DATA_BOUNDARY = "SyntheticDataBoundary"
    BLOCKED_CLAIMS = "BlockedClaims"
    SECTION_COMPLETENESS = "SectionCompleteness"
    REPRODUCIBILITY = "Reproducibility"
    LEDGER_LINKS = "LedgerLinks"
    ARTIFACT_HASHES = "ArtifactHashes"
    FINAL_SYNTHESIS_READINESS = "FinalSynthesisReadiness"


class ReviewerRecommendation(StrEnum):
    """Deterministic fake reviewer recommendation labels."""

    ACCEPT = "Accept"
    WEAK_ACCEPT = "WeakAccept"
    REVISE = "Revise"
    WEAK_REJECT = "WeakReject"
    REJECT = "Reject"


class ReviewerDisagreementType(StrEnum):
    """Reviewer disagreement resolver labels."""

    NOVEL_CONTROVERSY = "NovelControversy"
    AMBIGUOUS_CLAIM = "AmbiguousClaim"
    REVIEWER_ERROR = "ReviewerError"
    FATAL_CONFUSION = "FatalConfusion"
    LOW_DISAGREEMENT = "LowDisagreement"


class BridgeRepairAction(StrEnum):
    """Allowed deterministic bridge repair actions."""

    DEFINE_OBJECTS = "DefineObjects"
    CHANGE_METRIC = "ChangeMetric"
    ADD_BASELINE = "AddBaseline"
    ADD_SYNTHETIC_DATA = "AddSyntheticData"
    NARROW_CLAIM = "NarrowClaim"
    REPLACE_METHOD = "ReplaceMethod"
    REJECT_BRIDGE = "RejectBridge"


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


class StageBReviewerReport(StrictModel):
    """One deterministic fake Stage B reviewer report."""

    reviewer_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    novelty_score: float = Field(ge=0.0, le=1.0)
    feasibility_score: float = Field(ge=0.0, le=1.0)
    verifiability_score: float = Field(ge=0.0, le=1.0)
    clarity_score: float = Field(ge=0.0, le=1.0)
    significance_score: float = Field(ge=0.0, le=1.0)
    objections: list[str] = Field(default_factory=list)
    recommendation: ReviewerRecommendation

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


class FakeProofResult(StrictModel):
    """Deterministic fake proof validation result."""

    candidate_id: str = Field(min_length=1)
    proof_attempt_id: str = Field(min_length=1)
    lean_exit_code_fake: int = Field(ge=0)
    forbidden_tokens_present: bool
    proof_score: float = Field(ge=0.0, le=1.0)
    label: VerificationLabel
    evidence_artifact_type: ArtifactType
    reason: str = Field(min_length=1)


class FakeExperimentResult(StrictModel):
    """Deterministic fake synthetic experiment result."""

    candidate_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    generator_name: str = Field(min_length=1)
    generator_parameters: dict[str, Any]
    seed: int
    metric_name: str = Field(min_length=1)
    metric_value: float
    baseline_value: float
    delta: float
    predeclared_delta: float
    lcb_95: float
    ablation_passed: bool
    baseline_strong: bool
    label: VerificationLabel
    reason: str = Field(min_length=1)


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


class ArtifactManifestEntry(StrictModel):
    """One artifact entry in the research object manifest."""

    artifact_id: str = Field(min_length=1)
    artifact_type: ArtifactType
    path: str = Field(min_length=1)
    content_hash: str | None = None
    producing_commit_hash: str | None = None
    is_evidence: bool
    is_presentation: bool
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactManifest(StrictModel):
    """Derived manifest of run artifacts. The ledger remains authoritative."""

    run_id: str = Field(min_length=1)
    artifacts: list[ArtifactManifestEntry]
    evidence_artifact_count: int = Field(ge=0)
    presentation_artifact_count: int = Field(ge=0)
    source_of_truth: str = "ledger"


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


class StageCVerificationRecord(StrictModel):
    """One deterministic Stage C verification decision."""

    candidate_id: str = Field(min_length=1)
    branch_type: BranchVerificationType
    label: VerificationLabel
    status: BranchStatus
    evidence_artifacts: list[ArtifactRef] = Field(default_factory=list)
    proof_result: FakeProofResult | None = None
    experiment_result: FakeExperimentResult | None = None
    reason: str = Field(min_length=1)
    fake: bool = True


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
