"""Shared enum labels for fActorI schemas."""

from __future__ import annotations

from enum import StrEnum


class DataRequirement(StrEnum):
    """Data access regimes from the MVP data gate."""

    NO_DATA = "NoData"
    SYNTHETIC_ONLY = "SyntheticOnly"
    PUBLIC_DOWNLOAD = "PublicDownload"
    USER_PROVIDED = "UserProvided"


MVP_ADMISSIBLE_DATA_REQUIREMENTS = frozenset(
    {DataRequirement.NO_DATA, DataRequirement.SYNTHETIC_ONLY}
)


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


class ExperimentKind(StrEnum):
    """Conservative synthetic experiment kinds supported by the gated MVP seam."""

    SYNTHETIC_SIMULATION = "SyntheticSimulation"
    SYNTHETIC_ABLATION = "SyntheticAblation"
    SYNTHETIC_ROBUSTNESS_CHECK = "SyntheticRobustnessCheck"
    NO_DATA_SANITY_CHECK = "NoDataSanityCheck"


class NarrativeSectionRole(StrEnum):
    """Manuscript section roles used by the narrative-quality critic."""

    CENTRAL_MESSAGE = "CentralMessage"
    PROBLEM_FRAMING = "ProblemFraming"
    BACKGROUND_LITERATURE_POSITIONING = "BackgroundLiteraturePositioning"
    MODEL_FRAME = "ModelFrame"
    MAIN_BODY_RESULT = "MainBodyResult"
    DERIVATIVE_COROLLARY = "DerivativeOrCorollary"
    TECHNICAL_LEMMA = "TechnicalLemma"
    APPENDIX_ONLY_PROOF = "AppendixOnlyProof"
    NUMERICAL_VALIDATION = "NumericalValidation"
    EMPIRICAL_DISCUSSION = "EmpiricalDiscussion"
    SYNTHETIC_BOUNDARY = "SyntheticBoundary"
    LIMITATIONS_DISCUSSION = "LimitationsDiscussion"


class PaperShapeStatus(StrEnum):
    """Diagnostic paper-shape status; not a scientific validation label."""

    PAPER_SHAPED = "PaperShaped"
    PAPER_SHAPED_WITH_WARNINGS = "PaperShapedWithWarnings"
    PAPER_SHAPE_WEAK = "PaperShapeWeak"
    NOT_PAPER_SHAPED = "NotPaperShaped"


class ManuscriptDraftStatus(StrEnum):
    """Section-by-section manuscript draft status; not scientific validation."""

    DRAFT_COMPLETE = "DraftComplete"
    DRAFT_COMPLETE_WITH_WARNINGS = "DraftCompleteWithWarnings"
    DRAFT_INCOMPLETE_UNSAFE_SECTIONS = "DraftIncompleteUnsafeSections"
    DRAFT_FAILED = "DraftFailed"


class PaperCriticFindingSeverity(StrEnum):
    """Paper critic finding severity; manuscript-quality diagnostic only."""

    INFO = "Info"
    WARNING = "Warning"
    MAJOR = "Major"
    BLOCKING = "Blocking"


class PaperCriticFindingType(StrEnum):
    """Deterministic paper critic finding categories."""

    NARRATIVE_SHAPE_FINDING = "NarrativeShapeFinding"
    CITATION_SAFETY_FINDING = "CitationSafetyFinding"
    EVIDENCE_BOUNDARY_FINDING = "EvidenceBoundaryFinding"
    LATEX_SAFETY_FINDING = "LatexSafetyFinding"
    SOURCE_MAP_FINDING = "SourceMapFinding"
    SECTION_COHERENCE_FINDING = "SectionCoherenceFinding"
    EMPIRICAL_BOUNDARY_FINDING = "EmpiricalBoundaryFinding"
    APPENDIX_ALLOCATION_FINDING = "AppendixAllocationFinding"


class PaperRevisionActionKind(StrEnum):
    """Conservative deterministic revision actions."""

    ADD_CENTRAL_MESSAGE = "AddCentralMessage"
    CLARIFY_PROBLEM_STATEMENT = "ClarifyProblemStatement"
    ADD_BOUNDED_LITERATURE_GAP = "AddBoundedLiteratureGap"
    DOWNGRADE_UNSUPPORTED_CLAIM_LANGUAGE = "DowngradeUnsupportedClaimLanguage"
    REMOVE_INVENTED_CITATION = "RemoveInventedCitation"
    ADD_CITATION_LIMITATION = "AddCitationLimitation"
    MOVE_TECHNICAL_LEMMA_TO_APPENDIX = "MoveTechnicalLemmaToAppendix"
    CLARIFY_SYNTHETIC_ONLY_BOUNDARY = "ClarifySyntheticOnlyBoundary"
    ADD_MISSING_LIMITATION = "AddMissingLimitation"
    ADD_SOURCE_MAP_WARNING = "AddSourceMapWarning"
    NO_ACTION_NEEDED = "NoActionNeeded"


class PaperRevisionStatus(StrEnum):
    """Status of one deterministic paper revision pass."""

    REVISION_APPLIED = "RevisionApplied"
    REVISION_APPLIED_WITH_WARNINGS = "RevisionAppliedWithWarnings"
    REVISION_BLOCKED_UNSAFE = "RevisionBlockedUnsafe"
    NO_REVISION_NEEDED = "NoRevisionNeeded"
    REVISION_FAILED = "RevisionFailed"


class FullPaperGenerationStatus(StrEnum):
    """Status of the non-evidence full-paper generation workflow."""

    PAPER_GENERATION_SUCCEEDED = "PaperGenerationSucceeded"
    PAPER_GENERATION_SUCCEEDED_WITH_WARNINGS = "PaperGenerationSucceededWithWarnings"
    PAPER_GENERATION_BLOCKED = "PaperGenerationBlocked"
    PAPER_GENERATION_FAILED = "PaperGenerationFailed"


class FullPaperGenerationStepStatus(StrEnum):
    """Status of one full-paper generation orchestration step."""

    PENDING = "Pending"
    SKIPPED = "Skipped"
    SUCCEEDED = "Succeeded"
    SUCCEEDED_WITH_WARNINGS = "SucceededWithWarnings"
    BLOCKED = "Blocked"
    FAILED = "Failed"


class FullPaperReleaseStatus(StrEnum):
    """Human-review readiness status for a generated paper bundle."""

    READY_FOR_HUMAN_REVIEW = "ReadyForHumanReview"
    READY_FOR_HUMAN_REVIEW_WITH_WARNINGS = "ReadyForHumanReviewWithWarnings"
    BLOCKED_MISSING_ARTIFACTS = "BlockedMissingArtifacts"
    BLOCKED_EVIDENCE_BOUNDARY_VIOLATION = "BlockedEvidenceBoundaryViolation"
    BLOCKED_CITATION_SAFETY_VIOLATION = "BlockedCitationSafetyViolation"
    BLOCKED_LATEX_SAFETY_VIOLATION = "BlockedLatexSafetyViolation"
    BLOCKED_CRITIC_FINDINGS = "BlockedCriticFindings"
    BLOCKED_INCONSISTENT_PROVENANCE = "BlockedInconsistentProvenance"
    RELEASE_GATE_FAILED = "ReleaseGateFailed"


class FullPaperReleaseFindingSeverity(StrEnum):
    """Severity of one generated-paper readiness finding."""

    INFO = "Info"
    WARNING = "Warning"
    MAJOR = "Major"
    BLOCKING = "Blocking"


class LLMOrchestrationStatus(StrEnum):
    """Aggregate status for gated LLM-assisted paper orchestration."""

    ORCHESTRATION_SUCCEEDED = "LLMOrchestrationSucceeded"
    ORCHESTRATION_SUCCEEDED_WITH_WARNINGS = "LLMOrchestrationSucceededWithWarnings"
    ORCHESTRATION_BLOCKED = "LLMOrchestrationBlocked"
    ORCHESTRATION_FAILED = "LLMOrchestrationFailed"


class LLMOrchestrationStepStatus(StrEnum):
    """Status for one gated LLM orchestration step."""

    PENDING = "Pending"
    SKIPPED = "Skipped"
    SUCCEEDED = "Succeeded"
    SUCCEEDED_WITH_WARNINGS = "SucceededWithWarnings"
    BLOCKED = "Blocked"
    FAILED = "Failed"


class LLMBudgetDecisionStatus(StrEnum):
    """Budget decision status for a planned LLM-assisted run."""

    ALLOWED = "Allowed"
    ALLOWED_WITH_WARNINGS = "AllowedWithWarnings"
    BLOCKED = "Blocked"


class LLMCallStatus(StrEnum):
    """Accounting status for one planned or observed LLM call."""

    PLANNED = "Planned"
    SKIPPED = "Skipped"
    SUCCEEDED = "Succeeded"
    BLOCKED = "Blocked"
    FAILED = "Failed"


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
    STAGE_A_LLM_CANDIDATES_PROPOSED = "StageALLMCandidatesProposed"
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
    RETRIEVAL_RUN_RECORDED = "RetrievalRunRecorded"
    STAGNATION_DEMO = "StagnationDemo"
    STAGE_B_STARTED = "StageBStarted"
    STAGE_B_CHILD_GENERATED = "StageBChildGenerated"
    STAGE_B_LLM_REVIEW_RECORDED = "StageBLLMReviewRecorded"
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
    NARRATIVE_CONTRACT_WRITTEN = "NarrativeContractWritten"
    PAPER_SHAPE_CRITIQUE_WRITTEN = "PaperShapeCritiqueWritten"
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
    PROSE_SECTION_DRAFT_WRITTEN = "ProseSectionDraftWritten"
    MANUSCRIPT_DRAFT_WRITTEN = "ManuscriptDraftWritten"
    CITATION_REGISTRY_WRITTEN = "CitationRegistryWritten"
    LATEX_EXPORT_WRITTEN = "LatexExportWritten"
    PAPER_CRITIC_REPORT_WRITTEN = "PaperCriticReportWritten"
    PAPER_REVISION_WRITTEN = "PaperRevisionWritten"
    FULL_PAPER_GENERATION_WRITTEN = "FullPaperGenerationWritten"
    FULL_PAPER_RELEASE_EVALUATED = "FullPaperReleaseEvaluated"
    HUMAN_REVIEW_INGESTED = "HumanReviewIngested"
    PROOF_ARTIFACT_INGESTED = "ProofArtifactIngested"
    EXPERIMENT_ARTIFACT_INGESTED = "ExperimentArtifactIngested"
    CLAIM_EVIDENCE_MAP_WRITTEN = "ClaimEvidenceMapWritten"
    EVIDENCE_AWARE_REFRESH_WRITTEN = "EvidenceAwareRefreshWritten"
    HUMAN_REVIEW_RECONCILIATION_WRITTEN = "HumanReviewReconciliationWritten"
    REVIEWER_CHANGE_REQUESTS_INGESTED = "ReviewerChangeRequestsIngested"
    HUMAN_REVIEW_RECONCILIATION_INDEX_WRITTEN = (
        "HumanReviewReconciliationIndexWritten"
    )
    LLM_ORCHESTRATION_WRITTEN = "LLMOrchestrationWritten"
    PIPELINE_RUN_REPORT_WRITTEN = "PipelineRunReportWritten"


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


class DiagnosticSeverity(StrEnum):
    """Severity assigned to deterministic diagnostic root causes."""

    INFO = "Info"
    WARNING = "Warning"
    BLOCKING = "Blocking"


class DiagnosticStatus(StrEnum):
    """Overall read-only diagnostic status."""

    NO_ISSUES = "NoIssues"
    WARNINGS_ONLY = "WarningsOnly"
    ACTION_RECOMMENDED = "ActionRecommended"
    BLOCKED = "Blocked"


class RootCauseCategory(StrEnum):
    """Deterministic root-cause categories for failed run outputs."""

    MISSING_STAGE_OUTPUT = "MissingStageOutput"
    MISSING_ARTIFACT = "MissingArtifact"
    HASH_MISMATCH = "HashMismatch"
    LEDGER_CONTINUITY_ISSUE = "LedgerContinuityIssue"
    EVIDENCE_BOUNDARY_VIOLATION = "EvidenceBoundaryViolation"
    CLAIM_LABEL_INFLATION = "ClaimLabelInflation"
    SYNTHETIC_BOUNDARY_VIOLATION = "SyntheticBoundaryViolation"
    BLOCKED_CLAIM_LEAK = "BlockedClaimLeak"
    RELEASE_GATE_INCONSISTENCY = "ReleaseGateInconsistency"
    EXPORT_READINESS_INCONSISTENCY = "ExportReadinessInconsistency"
    REPLAY_REPORT_ONLY = "ReplayReportOnly"
    RUNTIME_SUMMARY_MISUSE = "RuntimeSummaryMisuse"
    UNKNOWN = "Unknown"


class RegressionStatus(StrEnum):
    """Overall deterministic cross-run regression status."""

    NO_REGRESSION = "NoRegression"
    REGRESSION_WARNINGS = "RegressionWarnings"
    REGRESSION_DETECTED = "RegressionDetected"
    COMPARISON_FAILED = "ComparisonFailed"


class RegressionSeverity(StrEnum):
    """Severity assigned to deterministic cross-run findings."""

    INFO = "Info"
    WARNING = "Warning"
    BLOCKING = "Blocking"


class RegressionCategory(StrEnum):
    """Deterministic cross-run difference and regression categories."""

    LEDGER_DRIFT = "LedgerDrift"
    ARTIFACT_DRIFT = "ArtifactDrift"
    HASH_DRIFT = "HashDrift"
    MISSING_OUTPUT = "MissingOutput"
    STAGE_COUNT_CHANGE = "StageCountChange"
    CANDIDATE_COUNT_CHANGE = "CandidateCountChange"
    CLAIM_LABEL_CHANGE = "ClaimLabelChange"
    EVIDENCE_BOUNDARY_REGRESSION = "EvidenceBoundaryRegression"
    RELEASE_STATUS_REGRESSION = "ReleaseStatusRegression"
    EXPORT_READINESS_REGRESSION = "ExportReadinessRegression"
    REPLAY_STATUS_REGRESSION = "ReplayStatusRegression"
    DIAGNOSTIC_STATUS_REGRESSION = "DiagnosticStatusRegression"
    BRANCH_OUTCOME_CHANGE = "BranchOutcomeChange"
    BLOCKED_CLAIM_CHANGE = "BlockedClaimChange"
    UNKNOWN = "Unknown"


class PipelineStage(StrEnum):
    """Supported deterministic one-command pipeline stages."""

    RUN_STAGE_A = "run-stage-a"
    RUN_STAGE_B = "run-stage-b"
    SELECT_STAGE_C = "select-stage-c"
    RUN_STAGE_C = "run-stage-c"
    SYNTHESIZE_ABSTRACT = "synthesize-abstract"
    PLAN_MANUSCRIPT = "plan-manuscript"
    BUILD_DRAFT_SKELETON = "build-draft-skeleton"
    PACKAGE_RESEARCH_OBJECT = "package-research-object"
    ASSEMBLE_PAPER_SKELETON = "assemble-paper-skeleton"
    FINAL_AUDIT = "final-audit"
    PREPARE_EXPORT = "prepare-export"
    REPLAY_VERIFY = "replay-verify"
    DIAGNOSE_RUN = "diagnose-run"


class PipelineRunStatus(StrEnum):
    """Overall and per-stage deterministic pipeline statuses."""

    PIPELINE_SUCCEEDED = "PipelineSucceeded"
    PIPELINE_SUCCEEDED_WITH_WARNINGS = "PipelineSucceededWithWarnings"
    PIPELINE_BLOCKED = "PipelineBlocked"
    PIPELINE_FAILED = "PipelineFailed"


class PipelineFailurePolicy(StrEnum):
    """Pipeline behavior after the first blocking or failed stage."""

    CONTINUE_SAFE = "ContinueSafe"
    FAIL_FAST = "FailFast"


class RerunPolicy(StrEnum):
    """Explicit policy for invoking a stage against existing completion artifacts."""

    FAIL_IF_EXISTS = "FailIfExists"
    SKIP_IF_COMPLETE = "SkipIfComplete"
    ALLOW_IF_FORCED = "AllowIfForced"
    READ_ONLY_ONLY = "ReadOnlyOnly"


class StageRerunStatus(StrEnum):
    """Deterministic decision status for one requested stage invocation."""

    ALLOWED = "Allowed"
    BLOCKED_ALREADY_COMPLETE = "BlockedAlreadyComplete"
    SKIPPED_ALREADY_COMPLETE = "SkippedAlreadyComplete"
    ALLOWED_FORCED = "AllowedForced"
    READ_ONLY_ALLOWED = "ReadOnlyAllowed"
    BLOCKED_INCONSISTENT = "BlockedInconsistent"


class LedgerTipStatus(StrEnum):
    """Read-only ledger linearity validation status."""

    VALID = "Valid"
    WARNING = "Warning"
    INVALID = "Invalid"
    MISSING = "Missing"


class DryRunStatus(StrEnum):
    """Overall read-only pipeline dry-run planning status."""

    DRY_RUN_RUNNABLE = "DryRunRunnable"
    DRY_RUN_RUNNABLE_WITH_WARNINGS = "DryRunRunnableWithWarnings"
    DRY_RUN_BLOCKED = "DryRunBlocked"
    DRY_RUN_INVALID = "DryRunInvalid"


class PlannedStageStatus(StrEnum):
    """Read-only status of one planned pipeline stage."""

    WOULD_RUN = "WouldRun"
    WOULD_SKIP = "WouldSkip"
    ALREADY_COMPLETE = "AlreadyComplete"
    BLOCKED_BY_PREREQUISITE = "BlockedByPrerequisite"
    BLOCKED_BY_STOP_AFTER = "BlockedByStopAfter"
    OUT_OF_RANGE = "OutOfRange"
    READ_ONLY_CHECK = "ReadOnlyCheck"


class OutputHygieneStatus(StrEnum):
    """Overall status of a read-only run output hygiene inspection."""

    CLEAN = "Clean"
    CLEAN_WITH_WARNINGS = "CleanWithWarnings"
    HYGIENE_ISSUES_FOUND = "HygieneIssuesFound"
    HYGIENE_INSPECTION_FAILED = "HygieneInspectionFailed"


class OutputHygieneSeverity(StrEnum):
    """Severity assigned to deterministic output hygiene findings."""

    INFO = "Info"
    WARNING = "Warning"
    BLOCKING = "Blocking"


class OutputHygieneCategory(StrEnum):
    """Deterministic categories for run-directory hygiene findings."""

    ORPHANED_ARTIFACT = "OrphanedArtifact"
    MISSING_MANIFEST_ENTRY = "MissingManifestEntry"
    MISSING_FILE_FOR_MANIFEST_ENTRY = "MissingFileForManifestEntry"
    HASH_MISMATCH = "HashMismatch"
    DUPLICATE_OUTPUT = "DuplicateOutput"
    STALE_OUTPUT = "StaleOutput"
    NON_PROVENANCE_LEAK = "NonProvenanceLeak"
    UNEXPECTED_DIRECTORY = "UnexpectedDirectory"
    UNEXPECTED_FILE = "UnexpectedFile"
    MANIFEST_EXCLUSION_VIOLATION = "ManifestExclusionViolation"
    EVIDENCE_BOUNDARY_RISK = "EvidenceBoundaryRisk"
    REPLAY_DIAGNOSTICS_LEAK = "ReplayDiagnosticsLeak"
    UNKNOWN = "Unknown"


class RemediationActionKind(StrEnum):
    """Non-executing hygiene remediation recommendation kinds."""

    INSPECT_MANUALLY = "InspectManually"
    RERUN_PRODUCING_STAGE = "RerunProducingStage"
    REGENERATE_MANIFEST = "RegenerateManifest"
    QUARANTINE_UNMANIFESTED_FILE = "QuarantineUnmanifestedFile"
    REMOVE_NON_PROVENANCE_REPORT = "RemoveNonProvenanceReport"
    REMOVE_STALE_TEMP_FILE = "RemoveStaleTempFile"
    RESTORE_MISSING_ARTIFACT = "RestoreMissingArtifact"
    REJECT_RUN_AS_INCONSISTENT = "RejectRunAsInconsistent"
    NO_ACTION_NEEDED = "NoActionNeeded"


class RemediationRisk(StrEnum):
    """Risk of a recommended remediation if a human later executes it."""

    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    UNSAFE = "Unsafe"


class RemediationStatus(StrEnum):
    """Action-level status for a recommendation that is never auto-executed."""

    RECOMMENDED = "Recommended"
    MANUAL_APPROVAL_REQUIRED = "ManualApprovalRequired"
    UNSAFE_TO_EXECUTE = "UnsafeToExecute"
    NOT_REQUIRED = "NotRequired"


class RemediationPlanStatus(StrEnum):
    """Overall status of a deterministic hygiene remediation plan."""

    NO_REMEDIATION_NEEDED = "NoRemediationNeeded"
    REMEDIATION_RECOMMENDED = "RemediationRecommended"
    MANUAL_INSPECTION_REQUIRED = "ManualInspectionRequired"
    RUN_INCONSISTENT = "RunInconsistent"


class RunFileClassification(StrEnum):
    """Filesystem classification used by read-only run indexing."""

    LEDGER = "ledger"
    NORMAL_ARTIFACT = "normal_artifact"
    MANIFESTED_ARTIFACT = "manifested_artifact"
    UNMANIFESTED_FILE = "unmanifested_file"
    NON_PROVENANCE_REPORT = "non_provenance_report"
    REPLAY_REPORT = "replay_report"
    DIAGNOSTIC_REPORT = "diagnostic_report"
    COMPARISON_REPORT = "comparison_report"
    CACHE_OR_TEMP = "cache_or_temp"
    UNEXPECTED = "unexpected"


class RunCompletenessStatus(StrEnum):
    """Disk-inspected deterministic run completeness status."""

    NO_RUN_FOUND = "NoRunFound"
    PARTIAL_RUN = "PartialRun"
    READY_TO_RESUME = "ReadyToResume"
    COMPLETE_RUN = "CompleteRun"
    COMPLETE_WITH_WARNINGS = "CompleteWithWarnings"
    BLOCKED_RUN = "BlockedRun"
    INCONSISTENT_RUN = "InconsistentRun"


class ResumeValidationStatus(StrEnum):
    """Read-only validation status for a requested pipeline resume point."""

    RESUME_ALLOWED = "ResumeAllowed"
    RESUME_BLOCKED = "ResumeBlocked"
    RESUME_ALLOWED_WITH_WARNINGS = "ResumeAllowedWithWarnings"


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

__all__ = [
    "DataRequirement",
    "MVP_ADMISSIBLE_DATA_REQUIREMENTS",
    "VerificationLabel",
    "NarrativeSectionRole",
    "PaperShapeStatus",
    "FullPaperReleaseStatus",
    "FullPaperReleaseFindingSeverity",
    "LLMOrchestrationStatus",
    "LLMOrchestrationStepStatus",
    "LLMBudgetDecisionStatus",
    "LLMCallStatus",
    "BranchStatus",
    "ArtifactType",
    "ControllerActionType",
    "ReleaseGateStatus",
    "ReplayStatus",
    "DiagnosticSeverity",
    "DiagnosticStatus",
    "RootCauseCategory",
    "RegressionStatus",
    "RegressionSeverity",
    "RegressionCategory",
    "PipelineStage",
    "PipelineRunStatus",
    "PipelineFailurePolicy",
    "RerunPolicy",
    "StageRerunStatus",
    "LedgerTipStatus",
    "DryRunStatus",
    "PlannedStageStatus",
    "OutputHygieneStatus",
    "OutputHygieneSeverity",
    "OutputHygieneCategory",
    "RemediationActionKind",
    "RemediationRisk",
    "RemediationStatus",
    "RemediationPlanStatus",
    "RunFileClassification",
    "RunCompletenessStatus",
    "ResumeValidationStatus",
    "AuditCategory",
    "AuditCheckStatus",
    "AuditSeverity",
    "BranchVerificationType",
    "FinalNucleusType",
    "ChecklistCategory",
    "ReviewerRecommendation",
    "ReviewerDisagreementType",
    "BridgeRepairAction",
    "QuestionCategory",
    "ControllerDecisionAction",
]
