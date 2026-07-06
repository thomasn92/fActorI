"""Language-neutral protocol registry backed by existing typed models."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from factori.adapters.config import AdapterConfig
from factori.protocol_compat import ProtocolCompatibilityStatus
from factori.schemas import (
    AppendixAllocationAssessment,
    ArtifactManifest,
    ArtifactRef,
    ArtifactType,
    AtlasScanInspectionReport,
    AtlasScanReport,
    AutonomousEvidenceGapPlan,
    AutonomousEvidenceGapPlanItem,
    AutonomousLoopDecision,
    AutonomousLoopGapTerminalClassification,
    AutonomousLoopIndex,
    AutonomousLoopIterationReport,
    AutonomousLoopRunReport,
    AutonomousPaperCheckpoint,
    AutonomousPaperCheckpointIndex,
    AutonomousPaperResumeReport,
    AutonomousPaperRunHandoff,
    AutonomousPaperRunIndex,
    AutonomousPaperRunReport,
    AutonomousPaperRunStage,
    AutonomousPlanExecutionAction,
    AutonomousPlanExecutionIndex,
    AutonomousPlanExecutionReport,
    BackendKind,
    BaselineReport,
    BibliographyEntry,
    BranchRouteDecision,
    BranchRouteExecutionHint,
    BranchRouteInspectionReport,
    BranchRoutePlan,
    BranchRouteType,
    BridgeReport,
    Candidate,
    CapabilityEscalationIndex,
    CapabilityEscalationItem,
    CapabilityEscalationPolicy,
    CapabilityEscalationReport,
    CitationRecord,
    CitationRegistry,
    CitationSafetyReport,
    CitationUsage,
    Claim,
    ClaimAdjudication,
    ClaimEvidenceMap,
    ClaimEvidenceMapLink,
    ClaimSupportAuditReport,
    ClaimSupportItem,
    ClaimTable,
    CompatibilityExclusion,
    CompatibilityFilterReport,
    CompleteMarkdownDraft,
    CreativeMutationCandidate,
    CreativeMutationInspectionReport,
    CreativeMutationOperator,
    CreativeMutationPlan,
    CreativeMutationReport,
    CreativeSearchControllerConfig,
    CreativeSearchControllerReport,
    CreativeSearchCycle,
    CreativeSearchInspectionReport,
    CreativeSearchLineageEntry,
    CreativeSearchStopReason,
    DataRequirement,
    DeepOpportunityCandidate,
    DeepOpportunityDiscoveryConfig,
    DeepOpportunityDiscoveryInspectionReport,
    DeepOpportunityDiscoveryReport,
    DeepOpportunityScore,
    DiagnosticReport,
    DomainAtlasEntry,
    DomainMethodPair,
    DomainPrimitive,
    DraftSkeleton,
    EmpiricalBoundaryAssessment,
    EvidenceAwareRefreshReport,
    ExperimentArtifact,
    ExperimentGapRoutingIndex,
    ExperimentGapRoutingItem,
    ExperimentGapRoutingReport,
    ExperimentKind,
    ExperimentRunContract,
    ExperimentRunResult,
    ExperimentTemplate,
    ExperimentTemplateRegistry,
    ExperimentTemplateSelection,
    ExportReadinessReport,
    FinalAuditReport,
    FinalBundleReplaySummary,
    FinalBundleVerificationCheck,
    FinalBundleVerificationReport,
    FinalManuscriptClaimSummary,
    FinalManuscriptRegenerationIndex,
    FinalManuscriptRegenerationReport,
    FinalManuscriptSection,
    FinalManuscriptStructuredDocument,
    FinalNucleus,
    FinalReleaseBundle,
    FinalReleaseBundleArtifact,
    FinalReleaseBundleIndex,
    FinalReleaseBundleManifest,
    FinalReleaseBundleReport,
    FinalReleaseReproducibilityManifest,
    FullPaperArtifactBundle,
    FullPaperBundleCompletenessReport,
    FullPaperEvidenceBoundaryReport,
    FullPaperGenerationConfig,
    FullPaperGenerationReport,
    FullPaperGenerationResult,
    FullPaperGenerationStatus,
    FullPaperGenerationStep,
    FullPaperGenerationStepStatus,
    FullPaperReadinessDecision,
    FullPaperReleaseCheck,
    FullPaperReleaseFinding,
    FullPaperReleaseFindingSeverity,
    FullPaperReleaseGateConfig,
    FullPaperReleaseReport,
    FullPaperReleaseStatus,
    GapAttemptHistory,
    GapAttemptRecord,
    GapStrategyDiversificationIndex,
    GapStrategyDiversificationReport,
    GapStrategyOption,
    GeneratedSectionDraft,
    GenerationMutationCandidate,
    GenerationMutationContext,
    GenerationMutationDiversityCheck,
    GenerationMutationInspectionReport,
    GenerationMutationOperator,
    GenerationMutationPlan,
    HumanReviewArtifact,
    HumanReviewDecision,
    HumanReviewReconciliationCycle,
    HumanReviewReconciliationIndex,
    HumanReviewReconciliationItem,
    HumanReviewReconciliationReport,
    HygieneRemediationPlan,
    IdeaClusterDiagnostic,
    IdeaEdge,
    IdeaNode,
    IdeaNodeFeatureVector,
    IdeaSpaceAxis,
    IdeaSpaceDiversityReport,
    IdeaSpaceInspectionReport,
    IdeaSpacePCADiagnostic,
    IdeaTree,
    IdeaTreeExportReport,
    IdeaTreeInspectionReport,
    LatexCompileCheckReport,
    LatexExportContract,
    LatexExportResult,
    LatexRenderConfig,
    LatexRenderResult,
    LatexSafetyReport,
    LatexSourceMap,
    LatexSourceMapEntry,
    LedgerCommit,
    LedgerSummary,
    LedgerTipValidationReport,
    LiteratureGapStatement,
    LiteraturePositioningAssessment,
    LiteraturePositioningContract,
    LiteraturePositioningReport,
    LLMBudgetConfig,
    LLMBudgetDecision,
    LLMBudgetDecisionStatus,
    LLMBudgetUsage,
    LLMCallAccountingRecord,
    LLMCallStatus,
    LLMCandidateParseReport,
    LLMOpportunityDiscoveryRawArtifact,
    LLMOrchestrationConfig,
    LLMOrchestrationReport,
    LLMOrchestrationResult,
    LLMOrchestrationStatus,
    LLMOrchestrationStep,
    LLMOrchestrationStepStatus,
    LLMPairRankingPrompt,
    LLMPairRankingReport,
    LLMPairRankingResult,
    LLMPromptContract,
    LLMReviewerParseResult,
    LLMRunSafetyReport,
    MainMessageAssessment,
    MainResultAssessment,
    ManuscriptAssemblyReport,
    ManuscriptDraftingPlan,
    ManuscriptDraftingReport,
    ManuscriptDraftStatus,
    ManuscriptPlan,
    MethodAtlasEntry,
    MethodLens,
    ModelNotationAssessment,
    MutationTournamentComparison,
    MutationTournamentEntry,
    MutationTournamentInspectionReport,
    MutationTournamentResult,
    MutationTournamentSpec,
    NarrativeManuscriptContract,
    NumericalStudyAssessment,
    OpportunityCandidate,
    OpportunityDiscoveryInspectionReport,
    OpportunityDiscoveryReport,
    OpportunityScoreBreakdown,
    OpportunitySeedConstraint,
    OutputHygieneReport,
    PaperCriticFinding,
    PaperCriticReport,
    PaperReleaseReadinessPreview,
    PaperRevisionPatch,
    PaperRevisionPlan,
    PaperRevisionResult,
    PaperShapeCritique,
    PaperShapeScore,
    PaperSkeleton,
    PipelineDryRunPlan,
    PipelineRunConfig,
    PipelineRunReport,
    PipelineStageResult,
    PlannedExperimentSpec,
    PlannedSpecDedupIndex,
    PlannedSpecDuplicateRecord,
    PlannedSpecExecutionIndex,
    PlannedSpecExecutionItem,
    PlannedSpecExecutionReport,
    PlannedStage,
    ProductionModePolicy,
    ProductionModeReport,
    ProductionModeViolation,
    ProofArtifact,
    ProofObligationSpec,
    ProofVerificationContract,
    ProofVerificationResult,
    ProseGenerationParseResult,
    ProseGenerationRequest,
    ProsePromptContract,
    ProseSafetyReport,
    ProseSectionContract,
    PythonExperimentSandboxIndex,
    PythonExperimentSandboxManifest,
    PythonExperimentSandboxReport,
    PythonExperimentSandboxRun,
    QualityRepairReport,
    ReleaseGateDecision,
    ReleaseGateStatus,
    ReplayVerificationReport,
    ReproducibilityManifest,
    RerunPolicy,
    ResearchObject,
    ResearchObjectManifest,
    ResumeValidationReport,
    RetrievalAdequacyCertificate,
    RetrievalContext,
    RetrievalExpansionRequest,
    RetrievalParseReport,
    RetrievalQualityReport,
    RetrievalQuery,
    RetrievalResult,
    RetrievalRunReport,
    RetrievedDocument,
    RetrievedSourceSummary,
    ReviewerBundleSummary,
    ReviewerChangeRequest,
    ReviewerChangeRequestSet,
    ReviewerPromptContract,
    RevisionSafetyReport,
    RouteExecutionInputContract,
    RouteExecutionInspectionReport,
    RouteExecutionOutputContract,
    RouteExecutionReport,
    RouteExecutionResult,
    RouteExecutionSpec,
    RouteExecutionStatus,
    RunStatusReport,
    SandboxBudgetPolicy,
    SandboxBudgetReport,
    ScientificStageKind,
    ScientificSubstrate,
    ScientificSubstrateAssumption,
    ScientificSubstrateBuildReport,
    ScientificSubstrateExperimentDesign,
    ScientificSubstrateInspectionReport,
    ScientificSubstrateModelObject,
    ScientificSubstrateResultSchema,
    ScientificSubstrateVariable,
    ScoreVector,
    SectionDraftingResult,
    SectionDraftingTask,
    SectionDraftSafetySummary,
    SectionRevisionPlan,
    SourceRelevanceAdjudication,
    StageBackendRecord,
    StageBReviewerReport,
    StageCheckpoint,
    StageRerunDecision,
    StageRerunStatus,
    SubstrateExperimentComparisonTable,
    SubstrateExperimentResult,
    SubstrateExperimentRoutingReport,
    SubstrateExperimentSpec,
    SubstratePromotionCandidate,
    SubstratePromotionConfig,
    SubstratePromotionDecision,
    SubstratePromotionInspectionReport,
    SubstratePromotionReport,
    SubstrateTournamentComparison,
    SubstrateTournamentEntry,
    SubstrateTournamentInspectionReport,
    SubstrateTournamentResult,
    SubstrateTournamentSpec,
    VarianceAugmentationBatch,
    VarianceAugmentationConfig,
    VarianceAugmentationInspectionReport,
    VarianceAugmentationReport,
    VarianceAugmentedCandidate,
    VarianceDiversityDiagnostic,
    VerificationLabel,
)
from factori.stage_c_selection import StageCSelectionResult

PROTOCOL_VERSION = "0.64.0"
SCHEMA_FORMAT = "json-schema"
PROTOCOL_SOURCE = "factori-pydantic-models"
PROTOCOL_GENERATOR = "factori export-protocols"


class AdapterBackend(StrEnum):
    """Provider-neutral adapter backend names exposed by the protocol layer."""

    FAKE = "fake"
    OPENAI = "openai"


class RetrievalBackend(StrEnum):
    """Provider-neutral retrieval backend names exposed by the protocol layer."""

    FAKE = "fake"
    LOCAL = "local"
    OPENALEX = "openalex"


class ReviewerBackend(StrEnum):
    """Provider-neutral Stage B reviewer backend names exposed by the protocol layer."""

    FAKE = "fake"
    OPENAI = "openai"


class ProofBackend(StrEnum):
    """Proof backend names exposed by the protocol layer."""

    FAKE = "fake"
    LEAN = "lean"


class ExperimentBackend(StrEnum):
    """Synthetic experiment backend names exposed by the protocol layer."""

    FAKE = "fake"
    LOCAL_SYNTHETIC = "local_synthetic"


class ProseBackend(StrEnum):
    """Prose backend names exposed by the protocol layer."""

    FAKE = "fake"
    OPENAI = "openai"


@dataclass(frozen=True)
class ProtocolDefinition:
    """Stable public protocol name mapped to one existing Python type."""

    name: str
    model: Any
    description: str

    @property
    def filename(self) -> str:
        """Return the deterministic language-neutral schema filename."""
        return f"{protocol_slug(self.name)}.schema.json"

    @property
    def source_model(self) -> str:
        """Return the qualified source type used to generate the schema."""
        return f"{self.model.__module__}.{self.model.__name__}"


PROTOCOL_DEFINITIONS: tuple[ProtocolDefinition, ...] = (
    ProtocolDefinition("Candidate", Candidate, "Research candidate branch."),
    ProtocolDefinition("ScoreVector", ScoreVector, "Structured candidate score vector."),
    ProtocolDefinition("LedgerCommit", LedgerCommit, "Append-only provenance commit."),
    ProtocolDefinition("ArtifactRecord", ArtifactRef, "Content-hashed artifact reference."),
    ProtocolDefinition("ArtifactManifest", ArtifactManifest, "Derived artifact manifest."),
    ProtocolDefinition("StageResult", PipelineStageResult, "One pipeline-stage execution result."),
    ProtocolDefinition("RunStatusReport", RunStatusReport, "Read-only run status report."),
    ProtocolDefinition(
        "ResumeValidationReport",
        ResumeValidationReport,
        "Read-only resume-prerequisite validation report.",
    ),
    ProtocolDefinition(
        "StageCheckpoint",
        StageCheckpoint,
        "Artifact-derived stage completion checkpoint.",
    ),
    ProtocolDefinition(
        "RerunPolicy",
        RerunPolicy,
        "Explicit mutating-stage rerun policy.",
    ),
    ProtocolDefinition(
        "StageRerunDecision",
        StageRerunDecision,
        "Read-only mutating-stage rerun decision.",
    ),
    ProtocolDefinition(
        "StageRerunStatus",
        StageRerunStatus,
        "Stage rerun decision status enum.",
    ),
    ProtocolDefinition(
        "LedgerTipValidationReport",
        LedgerTipValidationReport,
        "Read-only ledger tip and fork validation report.",
    ),
    ProtocolDefinition("RetrievalResult", RetrievalResult, "Normalized retrieval source result."),
    ProtocolDefinition("RetrievalQuery", RetrievalQuery, "Provider retrieval query contract."),
    ProtocolDefinition("RetrievedDocument", RetrievedDocument, "Fetched source document metadata."),
    ProtocolDefinition(
        "RetrievalQualityReport",
        RetrievalQualityReport,
        "Bounded source-quality and relevance filtering report.",
    ),
    ProtocolDefinition(
        "SourceRelevanceAdjudication",
        SourceRelevanceAdjudication,
        "Bounded source relevance adjudication; not scientific evidence.",
    ),
    ProtocolDefinition(
        "RetrievalRunReport",
        RetrievalRunReport,
        "Bounded retrieval run report; not proof of novelty.",
    ),
    ProtocolDefinition(
        "RetrievalParseReport",
        RetrievalParseReport,
        "Provider retrieval response parse report.",
    ),
    ProtocolDefinition(
        "RetrievalAdequacyCertificate",
        RetrievalAdequacyCertificate,
        "Bounded retrieval adequacy signal, not novelty proof.",
    ),
    ProtocolDefinition(
        "ReviewerReport",
        StageBReviewerReport,
        "Stage B structural review without verification authority.",
    ),
    ProtocolDefinition(
        "LLMReviewerPromptContract",
        ReviewerPromptContract,
        "Stage B structural-review prompt contract.",
    ),
    ProtocolDefinition(
        "LLMReviewerParseReport",
        LLMReviewerParseResult,
        "Stage B LLM reviewer parse and safety result.",
    ),
    ProtocolDefinition("BridgeReport", BridgeReport, "Stage B bridge validation result."),
    ProtocolDefinition("BaselineReport", BaselineReport, "Stage B baseline validation result."),
    ProtocolDefinition(
        "StageCSelectionResult",
        StageCSelectionResult,
        "Typed Stage C candidate-selection result.",
    ),
    ProtocolDefinition(
        "ProofVerificationResult",
        ProofVerificationResult,
        "Provider-neutral proof verification result for gated real proof backends.",
    ),
    ProtocolDefinition(
        "ProofVerificationContract",
        ProofVerificationContract,
        "Provider-neutral proof verification request contract.",
    ),
    ProtocolDefinition(
        "ExperimentRunResult",
        ExperimentRunResult,
        "Provider-neutral synthetic experiment result for gated local backends.",
    ),
    ProtocolDefinition(
        "ExperimentRunContract",
        ExperimentRunContract,
        "Provider-neutral synthetic experiment request contract.",
    ),
    ProtocolDefinition("Claim", Claim, "One label-preserving research claim."),
    ProtocolDefinition(
        "IdeaNode",
        IdeaNode,
        "One context-only node in the reconstructed creative search tree.",
    ),
    ProtocolDefinition(
        "IdeaEdge",
        IdeaEdge,
        "One context-only relation in the reconstructed creative search tree.",
    ),
    ProtocolDefinition(
        "IdeaTree",
        IdeaTree,
        "Derived research idea search tree without evidence authority.",
    ),
    ProtocolDefinition(
        "IdeaTreeInspectionReport",
        IdeaTreeInspectionReport,
        "Read-only inspection report for a reconstructed idea tree.",
    ),
    ProtocolDefinition(
        "IdeaTreeExportReport",
        IdeaTreeExportReport,
        "Context-only append-only idea-tree export result.",
    ),
    ProtocolDefinition(
        "BackendKind",
        BackendKind,
        "Authority classification for a pipeline backend.",
    ),
    ProtocolDefinition(
        "ScientificStageKind",
        ScientificStageKind,
        "Scientific generation, judgment, execution, or verification stage class.",
    ),
    ProtocolDefinition(
        "StageBackendRecord",
        StageBackendRecord,
        "Explicit backend authority record for one pipeline stage.",
    ),
    ProtocolDefinition(
        "ProductionModePolicy",
        ProductionModePolicy,
        "Fail-closed policy for strict non-fake scientific execution.",
    ),
    ProtocolDefinition(
        "ProductionModeViolation",
        ProductionModeViolation,
        "One backend authority violation found by a production-mode check.",
    ),
    ProtocolDefinition(
        "ProductionModeReport",
        ProductionModeReport,
        "Backend inventory and strict-production policy result for one run.",
    ),
    ProtocolDefinition(
        "DomainAtlasEntry",
        DomainAtlasEntry,
        "One curated domain entry used as atlas search infrastructure.",
    ),
    ProtocolDefinition(
        "MethodAtlasEntry",
        MethodAtlasEntry,
        "One curated method-lens entry used as atlas search infrastructure.",
    ),
    ProtocolDefinition(
        "DomainMethodPair",
        DomainMethodPair,
        "One domain-method pair with exclusion-only compatibility metadata.",
    ),
    ProtocolDefinition(
        "CompatibilityExclusion",
        CompatibilityExclusion,
        "One deterministic negative compatibility finding without opportunity authority.",
    ),
    ProtocolDefinition(
        "CompatibilityFilterReport",
        CompatibilityFilterReport,
        "Complete exclusion-only compatibility report over an atlas product.",
    ),
    ProtocolDefinition(
        "LLMPairRankingPrompt",
        LLMPairRankingPrompt,
        "Secret-free structured prompt contract for one LLM ranking batch.",
    ),
    ProtocolDefinition(
        "LLMPairRankingResult",
        LLMPairRankingResult,
        "Non-fake LLM scientific judgment for one compatible atlas pair.",
    ),
    ProtocolDefinition(
        "LLMPairRankingReport",
        LLMPairRankingReport,
        "Append-only non-fake LLM ranking report over compatible pairs.",
    ),
    ProtocolDefinition(
        "AtlasScanReport",
        AtlasScanReport,
        "Curated atlas build or diversity-constrained LLM pair-scan report.",
    ),
    ProtocolDefinition(
        "AtlasScanInspectionReport",
        AtlasScanInspectionReport,
        "Read-only latest atlas build or pair-scan inspection payload.",
    ),
    ProtocolDefinition(
        "DeepOpportunityDiscoveryConfig",
        DeepOpportunityDiscoveryConfig,
        "Bounded configuration for retrieval-contextualized LLM opportunity discovery.",
    ),
    ProtocolDefinition(
        "RetrievedSourceSummary",
        RetrievedSourceSummary,
        "Bounded source metadata supplied as non-evidence retrieval context.",
    ),
    ProtocolDefinition(
        "RetrievalContext",
        RetrievalContext,
        "Per-pair retrieval context with explicit novelty and coverage boundaries.",
    ),
    ProtocolDefinition(
        "DeepOpportunityCandidate",
        DeepOpportunityCandidate,
        "Concrete non-fake LLM research opportunity with Q/H/T/E/B fields.",
    ),
    ProtocolDefinition(
        "DeepOpportunityScore",
        DeepOpportunityScore,
        "Non-fake LLM scientific score for one concrete opportunity.",
    ),
    ProtocolDefinition(
        "LLMOpportunityDiscoveryRawArtifact",
        LLMOpportunityDiscoveryRawArtifact,
        "Secret-free prompt and raw-response provenance for one discovery call.",
    ),
    ProtocolDefinition(
        "DeepOpportunityDiscoveryReport",
        DeepOpportunityDiscoveryReport,
        "Append-only retrieval-contextualized LLM opportunity discovery report.",
    ),
    ProtocolDefinition(
        "DeepOpportunityDiscoveryInspectionReport",
        DeepOpportunityDiscoveryInspectionReport,
        "Read-only inspection of the latest deep opportunity discovery report.",
    ),
    ProtocolDefinition(
        "DomainPrimitive",
        DomainPrimitive,
        "One deterministic primitive extracted from a broad Stage 0 domain.",
    ),
    ProtocolDefinition(
        "MethodLens",
        MethodLens,
        "One mathematical or computational method lens for Stage 0 discovery.",
    ),
    ProtocolDefinition(
        "OpportunityScoreBreakdown",
        OpportunityScoreBreakdown,
        "Deterministic easy-win score components for a domain-method pair.",
    ),
    ProtocolDefinition(
        "OpportunityCandidate",
        OpportunityCandidate,
        "One scored Stage 0 domain-method opportunity candidate.",
    ),
    ProtocolDefinition(
        "OpportunitySeedConstraint",
        OpportunitySeedConstraint,
        "Promoted Stage 0 seed constraint for later candidate generation.",
    ),
    ProtocolDefinition(
        "OpportunityDiscoveryReport",
        OpportunityDiscoveryReport,
        "Append-only non-evidence Stage 0 opportunity discovery report.",
    ),
    ProtocolDefinition(
        "OpportunityDiscoveryInspectionReport",
        OpportunityDiscoveryInspectionReport,
        "Read-only inspection report for latest Stage 0 opportunity discovery.",
    ),
    ProtocolDefinition(
        "VarianceAugmentationConfig",
        VarianceAugmentationConfig,
        "Deterministic limits for opportunity-seeded candidate generation.",
    ),
    ProtocolDefinition(
        "VarianceAugmentedCandidate",
        VarianceAugmentedCandidate,
        "One context-only branch lifted from a promoted opportunity seed.",
    ),
    ProtocolDefinition(
        "VarianceAugmentationBatch",
        VarianceAugmentationBatch,
        "Candidate branches generated from one promoted opportunity seed.",
    ),
    ProtocolDefinition(
        "VarianceDiversityDiagnostic",
        VarianceDiversityDiagnostic,
        "Coverage and duplication diagnostic over variance-augmented branches.",
    ),
    ProtocolDefinition(
        "VarianceAugmentationReport",
        VarianceAugmentationReport,
        "Append-only opportunity-seeded variance generation or application report.",
    ),
    ProtocolDefinition(
        "VarianceAugmentationInspectionReport",
        VarianceAugmentationInspectionReport,
        "Read-only inspection report for latest variance augmentation.",
    ),
    ProtocolDefinition(
        "SubstratePromotionConfig",
        SubstratePromotionConfig,
        "Diversity and capacity constraints for variance substrate promotion.",
    ),
    ProtocolDefinition(
        "SubstratePromotionCandidate",
        SubstratePromotionCandidate,
        "One scored variance candidate eligible for substrate promotion.",
    ),
    ProtocolDefinition(
        "SubstratePromotionDecision",
        SubstratePromotionDecision,
        "One deterministic variance candidate promotion decision.",
    ),
    ProtocolDefinition(
        "SubstratePromotionReport",
        SubstratePromotionReport,
        "Append-only diversity-constrained substrate promotion report.",
    ),
    ProtocolDefinition(
        "SubstratePromotionInspectionReport",
        SubstratePromotionInspectionReport,
        "Read-only inspection report for latest substrate promotion.",
    ),
    ProtocolDefinition(
        "BranchRouteType",
        BranchRouteType,
        "Deterministic next-action class assigned to a scientific branch.",
    ),
    ProtocolDefinition(
        "BranchRouteExecutionHint",
        BranchRouteExecutionHint,
        "Non-executing next-command hint for a routed scientific branch.",
    ),
    ProtocolDefinition(
        "BranchRouteDecision",
        BranchRouteDecision,
        "One context-only route decision for a ScientificSubstrate.",
    ),
    ProtocolDefinition(
        "BranchRoutePlan",
        BranchRoutePlan,
        "Append-only deterministic route plan over the latest substrate build.",
    ),
    ProtocolDefinition(
        "BranchRouteInspectionReport",
        BranchRouteInspectionReport,
        "Read-only inspection report for the latest branch route plan.",
    ),
    ProtocolDefinition(
        "RouteExecutionStatus",
        RouteExecutionStatus,
        "Lifecycle state for route-specific spec construction and execution.",
    ),
    ProtocolDefinition(
        "RouteExecutionInputContract",
        RouteExecutionInputContract,
        "Bounded substrate inputs copied into a deterministic execution spec.",
    ),
    ProtocolDefinition(
        "RouteExecutionOutputContract",
        RouteExecutionOutputContract,
        "Required bounded outputs and support boundary for route execution.",
    ),
    ProtocolDefinition(
        "RouteExecutionSpec",
        RouteExecutionSpec,
        "Immutable deterministic execution specification for one branch route.",
    ),
    ProtocolDefinition(
        "RouteExecutionResult",
        RouteExecutionResult,
        "Bounded deterministic output for one route execution specification.",
    ),
    ProtocolDefinition(
        "RouteExecutionReport",
        RouteExecutionReport,
        "Append-only aggregate report for route specs or execution results.",
    ),
    ProtocolDefinition(
        "RouteExecutionInspectionReport",
        RouteExecutionInspectionReport,
        "Read-only inspection report for latest route execution state.",
    ),
    ProtocolDefinition(
        "IdeaNodeFeatureVector",
        IdeaNodeFeatureVector,
        "Deterministic lexical and domain feature vector for an idea-tree node.",
    ),
    ProtocolDefinition(
        "IdeaSpaceAxis",
        IdeaSpaceAxis,
        "One deterministic PCA-like axis over idea-node features.",
    ),
    ProtocolDefinition(
        "IdeaSpacePCADiagnostic",
        IdeaSpacePCADiagnostic,
        "PCA/SVD or deterministic fallback summary for idea-space diversity.",
    ),
    ProtocolDefinition(
        "IdeaClusterDiagnostic",
        IdeaClusterDiagnostic,
        "Near-duplicate and collapsed-axis diagnostic for idea nodes.",
    ),
    ProtocolDefinition(
        "IdeaSpaceDiversityReport",
        IdeaSpaceDiversityReport,
        "Context-only idea-space diversity report with recommended mutation axes.",
    ),
    ProtocolDefinition(
        "IdeaSpaceInspectionReport",
        IdeaSpaceInspectionReport,
        "Read-only idea-space inspection payload.",
    ),
    ProtocolDefinition(
        "ScientificSubstrateVariable",
        ScientificSubstrateVariable,
        "One variable or notation entry in a concrete scientific substrate.",
    ),
    ProtocolDefinition(
        "ScientificSubstrateAssumption",
        ScientificSubstrateAssumption,
        "One explicit modeling or experiment assumption for a scientific substrate.",
    ),
    ProtocolDefinition(
        "ScientificSubstrateModelObject",
        ScientificSubstrateModelObject,
        "Concrete model object, equations, and falsification notes for a substrate.",
    ),
    ProtocolDefinition(
        "ScientificSubstrateExperimentDesign",
        ScientificSubstrateExperimentDesign,
        "Bounded experiment design for a scientific substrate.",
    ),
    ProtocolDefinition(
        "ScientificSubstrateResultSchema",
        ScientificSubstrateResultSchema,
        "Expected result table and support rule for a substrate experiment.",
    ),
    ProtocolDefinition(
        "ScientificSubstrate",
        ScientificSubstrate,
        "Context-only concrete scientific substrate generated from idea-space axes.",
    ),
    ProtocolDefinition(
        "ScientificSubstrateBuildReport",
        ScientificSubstrateBuildReport,
        "Append-only report for deterministic scientific-substrate generation.",
    ),
    ProtocolDefinition(
        "ScientificSubstrateInspectionReport",
        ScientificSubstrateInspectionReport,
        "Read-only inspection report for generated scientific substrates.",
    ),
    ProtocolDefinition(
        "SubstrateExperimentSpec",
        SubstrateExperimentSpec,
        "Planned uv-local experiment instantiated from a selected substrate.",
    ),
    ProtocolDefinition(
        "SubstrateExperimentRoutingReport",
        SubstrateExperimentRoutingReport,
        "Append-only route from a selected substrate to an approved local bundle.",
    ),
    ProtocolDefinition(
        "SubstrateExperimentResult",
        SubstrateExperimentResult,
        "Bounded substrate experiment outcome without broad validation authority.",
    ),
    ProtocolDefinition(
        "SubstrateExperimentComparisonTable",
        SubstrateExperimentComparisonTable,
        "Baseline-versus-method comparison table for a substrate experiment.",
    ),
    ProtocolDefinition(
        "SubstrateTournamentSpec",
        SubstrateTournamentSpec,
        "Execution plan for a bounded multi-substrate synthetic tournament.",
    ),
    ProtocolDefinition(
        "SubstrateTournamentEntry",
        SubstrateTournamentEntry,
        "One substrate branch and its bounded tournament outcome.",
    ),
    ProtocolDefinition(
        "SubstrateTournamentResult",
        SubstrateTournamentResult,
        "Append-only bounded result of a multi-substrate experiment tournament.",
    ),
    ProtocolDefinition(
        "SubstrateTournamentComparison",
        SubstrateTournamentComparison,
        "Normalized synthetic-scope comparison across substrate branches.",
    ),
    ProtocolDefinition(
        "SubstrateTournamentInspectionReport",
        SubstrateTournamentInspectionReport,
        "Read-only inspection report for the latest substrate tournament.",
    ),
    ProtocolDefinition(
        "CreativeMutationOperator",
        CreativeMutationOperator,
        "Tournament-driven scientific mutation operator enum.",
    ),
    ProtocolDefinition(
        "CreativeMutationCandidate",
        CreativeMutationCandidate,
        "One tournament-driven scientific mutation candidate.",
    ),
    ProtocolDefinition(
        "CreativeMutationPlan",
        CreativeMutationPlan,
        "Context-only plan for tournament-driven creative mutations.",
    ),
    ProtocolDefinition(
        "CreativeMutationReport",
        CreativeMutationReport,
        "Append-only report for applied creative mutations.",
    ),
    ProtocolDefinition(
        "CreativeMutationInspectionReport",
        CreativeMutationInspectionReport,
        "Read-only inspection report for creative mutation planning and application.",
    ),
    ProtocolDefinition(
        "MutationTournamentSpec",
        MutationTournamentSpec,
        "Execution plan for a second-generation mutation substrate tournament.",
    ),
    ProtocolDefinition(
        "MutationTournamentEntry",
        MutationTournamentEntry,
        "One original or mutation substrate branch in a mutation tournament.",
    ),
    ProtocolDefinition(
        "MutationTournamentResult",
        MutationTournamentResult,
        "Append-only bounded result of testing creative mutation substrates.",
    ),
    ProtocolDefinition(
        "MutationTournamentComparison",
        MutationTournamentComparison,
        "Second-generation comparison of mutation substrates against the prior winner.",
    ),
    ProtocolDefinition(
        "MutationTournamentInspectionReport",
        MutationTournamentInspectionReport,
        "Read-only inspection report for the latest mutation substrate tournament.",
    ),
    ProtocolDefinition(
        "CreativeSearchStopReason",
        CreativeSearchStopReason,
        "Deterministic terminal reason for recursive creative search.",
    ),
    ProtocolDefinition(
        "CreativeSearchControllerConfig",
        CreativeSearchControllerConfig,
        "Bounded deterministic recursive creative-search policy.",
    ),
    ProtocolDefinition(
        "CreativeSearchLineageEntry",
        CreativeSearchLineageEntry,
        "One bounded branch transition in creative-search lineage.",
    ),
    ProtocolDefinition(
        "CreativeSearchCycle",
        CreativeSearchCycle,
        "One append-only recursive creative-search cycle summary.",
    ),
    ProtocolDefinition(
        "CreativeSearchControllerReport",
        CreativeSearchControllerReport,
        "Aggregate report for bounded recursive creative search.",
    ),
    ProtocolDefinition(
        "CreativeSearchInspectionReport",
        CreativeSearchInspectionReport,
        "Read-only inspection of the latest recursive creative search.",
    ),
    ProtocolDefinition(
        "GenerationMutationOperator",
        GenerationMutationOperator,
        "Current-winner-conditioned generation mutation operator enum.",
    ),
    ProtocolDefinition(
        "GenerationMutationContext",
        GenerationMutationContext,
        "Stable tournament and idea-space context for generation mutations.",
    ),
    ProtocolDefinition(
        "GenerationMutationCandidate",
        GenerationMutationCandidate,
        "One semantically de-duplicated generation mutation candidate.",
    ),
    ProtocolDefinition(
        "GenerationMutationDiversityCheck",
        GenerationMutationDiversityCheck,
        "Semantic diversity and duplicate check for generation mutations.",
    ),
    ProtocolDefinition(
        "GenerationMutationPlan",
        GenerationMutationPlan,
        "Context-only plan for current-winner-conditioned mutations.",
    ),
    ProtocolDefinition(
        "GenerationMutationInspectionReport",
        GenerationMutationInspectionReport,
        "Read-only inspection of generation mutation planning and application.",
    ),
    ProtocolDefinition(
        "ClaimEvidenceMapLink",
        ClaimEvidenceMapLink,
        "One final claim-to-evidence support classification.",
    ),
    ProtocolDefinition(
        "ClaimEvidenceMap",
        ClaimEvidenceMap,
        "Deterministic final claim-evidence support map.",
    ),
    ProtocolDefinition(
        "AutonomousEvidenceGapPlanItem",
        AutonomousEvidenceGapPlanItem,
        "One deterministic autonomous evidence-gap planning item.",
    ),
    ProtocolDefinition(
        "AutonomousEvidenceGapPlan",
        AutonomousEvidenceGapPlan,
        "Non-evidence autonomous next-action plan over evidence gaps.",
    ),
    ProtocolDefinition(
        "AutonomousPlanExecutionAction",
        AutonomousPlanExecutionAction,
        "One deterministic autonomous plan execution action.",
    ),
    ProtocolDefinition(
        "AutonomousPlanExecutionReport",
        AutonomousPlanExecutionReport,
        "Non-evidence autonomous plan execution report.",
    ),
    ProtocolDefinition(
        "AutonomousPlanExecutionIndex",
        AutonomousPlanExecutionIndex,
        "Derived latest pointer over immutable autonomous executions.",
    ),
    ProtocolDefinition(
        "PlannedExperimentSpec",
        PlannedExperimentSpec,
        "Planned experiment specification without experiment-evidence authority.",
    ),
    ProtocolDefinition(
        "ProofObligationSpec",
        ProofObligationSpec,
        "Planned formal proof obligation without verification authority.",
    ),
    ProtocolDefinition(
        "RetrievalExpansionRequest",
        RetrievalExpansionRequest,
        "Planned bounded retrieval expansion request without source authority.",
    ),
    ProtocolDefinition(
        "PlannedSpecExecutionItem",
        PlannedSpecExecutionItem,
        "One bounded disposition of a planned local spec execution.",
    ),
    ProtocolDefinition(
        "PlannedSpecExecutionReport",
        PlannedSpecExecutionReport,
        "Non-evidence planned local spec execution report.",
    ),
    ProtocolDefinition(
        "PlannedSpecExecutionIndex",
        PlannedSpecExecutionIndex,
        "Derived latest pointer over immutable planned-spec executions.",
    ),
    ProtocolDefinition(
        "PythonExperimentSandboxManifest",
        PythonExperimentSandboxManifest,
        "Hashed approved files and closed local Python sandbox policy.",
    ),
    ProtocolDefinition(
        "PythonExperimentSandboxRun",
        PythonExperimentSandboxRun,
        "One bounded uv-based local Python experiment execution record.",
    ),
    ProtocolDefinition(
        "PythonExperimentSandboxReport",
        PythonExperimentSandboxReport,
        "Append-only non-evidence report for one Python sandbox run.",
    ),
    ProtocolDefinition(
        "PythonExperimentSandboxIndex",
        PythonExperimentSandboxIndex,
        "Derived latest pointer over immutable Python sandbox reports.",
    ),
    ProtocolDefinition(
        "ExperimentTemplate",
        ExperimentTemplate,
        "Approved local experiment template metadata.",
    ),
    ProtocolDefinition(
        "ExperimentTemplateRegistry",
        ExperimentTemplateRegistry,
        "Deterministic registry of approved local experiment templates.",
    ),
    ProtocolDefinition(
        "ExperimentTemplateSelection",
        ExperimentTemplateSelection,
        "One deterministic experiment-template selection decision.",
    ),
    ProtocolDefinition(
        "ExperimentGapRoutingItem",
        ExperimentGapRoutingItem,
        "One deterministic experiment-gap routing item.",
    ),
    ProtocolDefinition(
        "ExperimentGapRoutingReport",
        ExperimentGapRoutingReport,
        "Append-only non-evidence experiment-gap routing report.",
    ),
    ProtocolDefinition(
        "ExperimentGapRoutingIndex",
        ExperimentGapRoutingIndex,
        "Derived latest pointer over immutable experiment-gap routing reports.",
    ),
    ProtocolDefinition(
        "SandboxBudgetPolicy",
        SandboxBudgetPolicy,
        "Bounded uv sandbox budget policy for autonomous loops.",
    ),
    ProtocolDefinition(
        "SandboxBudgetReport",
        SandboxBudgetReport,
        "Sandbox budget accounting report for local experiment execution.",
    ),
    ProtocolDefinition(
        "GapAttemptRecord",
        GapAttemptRecord,
        "One stable gap-attempt history record.",
    ),
    ProtocolDefinition(
        "GapAttemptHistory",
        GapAttemptHistory,
        "Derived append-only history of gap attempts and exhaustion state.",
    ),
    ProtocolDefinition(
        "PlannedSpecDuplicateRecord",
        PlannedSpecDuplicateRecord,
        "One planned-spec duplicate record keyed by stable fingerprint.",
    ),
    ProtocolDefinition(
        "PlannedSpecDedupIndex",
        PlannedSpecDedupIndex,
        "Derived de-duplication index over planned proof, experiment, and retrieval specs.",
    ),
    ProtocolDefinition(
        "GapStrategyOption",
        GapStrategyOption,
        "One bounded deterministic alternative for an exhausted workflow gap.",
    ),
    ProtocolDefinition(
        "GapStrategyDiversificationReport",
        GapStrategyDiversificationReport,
        "Append-only non-evidence strategy diversification report.",
    ),
    ProtocolDefinition(
        "GapStrategyDiversificationIndex",
        GapStrategyDiversificationIndex,
        "Derived latest pointer over immutable strategy diversification reports.",
    ),
    ProtocolDefinition(
        "CapabilityEscalationPolicy",
        CapabilityEscalationPolicy,
        "Fail-closed local/offline capability escalation policy.",
    ),
    ProtocolDefinition(
        "CapabilityEscalationItem",
        CapabilityEscalationItem,
        "Disposition of one deferred proof or retrieval escalation candidate.",
    ),
    ProtocolDefinition(
        "CapabilityEscalationReport",
        CapabilityEscalationReport,
        "Append-only non-evidence capability escalation report.",
    ),
    ProtocolDefinition(
        "CapabilityEscalationIndex",
        CapabilityEscalationIndex,
        "Derived latest pointer over immutable capability escalation reports.",
    ),
    ProtocolDefinition(
        "FinalManuscriptSection",
        FinalManuscriptSection,
        "One deterministic section in a regenerated final manuscript.",
    ),
    ProtocolDefinition(
        "FinalManuscriptClaimSummary",
        FinalManuscriptClaimSummary,
        "Scoped source-claim disposition during final regeneration.",
    ),
    ProtocolDefinition(
        "FinalManuscriptStructuredDocument",
        FinalManuscriptStructuredDocument,
        "Machine-checkable regenerated final manuscript.",
    ),
    ProtocolDefinition(
        "FinalManuscriptRegenerationReport",
        FinalManuscriptRegenerationReport,
        "Append-only non-evidence final manuscript regeneration report.",
    ),
    ProtocolDefinition(
        "FinalManuscriptRegenerationIndex",
        FinalManuscriptRegenerationIndex,
        "Derived latest pointer over immutable final manuscript regenerations.",
    ),
    ProtocolDefinition(
        "FinalReleaseBundleArtifact",
        FinalReleaseBundleArtifact,
        "One file included in an immutable final release bundle.",
    ),
    ProtocolDefinition(
        "FinalReleaseBundleManifest",
        FinalReleaseBundleManifest,
        "Machine-checkable manifest for a final release bundle.",
    ),
    ProtocolDefinition(
        "FinalReleaseReproducibilityManifest",
        FinalReleaseReproducibilityManifest,
        "Reproducibility metadata locked into a final release bundle.",
    ),
    ProtocolDefinition(
        "FinalReleaseBundle",
        FinalReleaseBundle,
        "Structured summary of one assembled final release bundle.",
    ),
    ProtocolDefinition(
        "FinalReleaseBundleReport",
        FinalReleaseBundleReport,
        "Append-only non-evidence final release bundle assembly report.",
    ),
    ProtocolDefinition(
        "FinalReleaseBundleIndex",
        FinalReleaseBundleIndex,
        "Derived latest pointer over immutable final release bundles.",
    ),
    ProtocolDefinition(
        "FinalBundleVerificationCheck",
        FinalBundleVerificationCheck,
        "One read-only integrity or policy check over a final release bundle.",
    ),
    ProtocolDefinition(
        "FinalBundleReplaySummary",
        FinalBundleReplaySummary,
        "Reproducibility replay summary reconstructed without command execution.",
    ),
    ProtocolDefinition(
        "FinalBundleVerificationReport",
        FinalBundleVerificationReport,
        "Independent read-only verification report for a final release bundle.",
    ),
    ProtocolDefinition(
        "AutonomousPaperRunStage",
        AutonomousPaperRunStage,
        "One fail-closed stage in autonomous paper finalization.",
    ),
    ProtocolDefinition(
        "AutonomousPaperRunHandoff",
        AutonomousPaperRunHandoff,
        "Bounded final handoff decision from autonomous paper finalization.",
    ),
    ProtocolDefinition(
        "AutonomousPaperRunReport",
        AutonomousPaperRunReport,
        "Append-only orchestration report for one autonomous paper run.",
    ),
    ProtocolDefinition(
        "AutonomousPaperRunIndex",
        AutonomousPaperRunIndex,
        "Derived latest pointer for autonomous paper controller reports.",
    ),
    ProtocolDefinition(
        "AutonomousPaperCheckpoint",
        AutonomousPaperCheckpoint,
        "Immutable verified stage checkpoint for crash-safe controller resume.",
    ),
    ProtocolDefinition(
        "AutonomousPaperCheckpointIndex",
        AutonomousPaperCheckpointIndex,
        "Immutable index snapshot over autonomous paper checkpoints.",
    ),
    ProtocolDefinition(
        "AutonomousPaperResumeReport",
        AutonomousPaperResumeReport,
        "Append-only report for one verified autonomous paper resume attempt.",
    ),
    ProtocolDefinition(
        "AutonomousLoopDecision",
        AutonomousLoopDecision,
        "Deterministic stop or continue decision for one autonomous loop iteration.",
    ),
    ProtocolDefinition(
        "AutonomousLoopGapTerminalClassification",
        AutonomousLoopGapTerminalClassification,
        "Terminal workflow classification for one autonomous evidence gap.",
    ),
    ProtocolDefinition(
        "AutonomousLoopIterationReport",
        AutonomousLoopIterationReport,
        "One autonomous loop iteration report.",
    ),
    ProtocolDefinition(
        "AutonomousLoopRunReport",
        AutonomousLoopRunReport,
        "Non-evidence autonomous iterative loop controller report.",
    ),
    ProtocolDefinition(
        "AutonomousLoopIndex",
        AutonomousLoopIndex,
        "Derived latest pointer over immutable autonomous loop runs.",
    ),
    ProtocolDefinition(
        "EvidenceAwareRefreshReport",
        EvidenceAwareRefreshReport,
        "Bounded evidence-aware manuscript wording refresh report.",
    ),
    ProtocolDefinition(
        "HumanReviewReconciliationItem",
        HumanReviewReconciliationItem,
        "Deterministic disposition of one human-review requested change.",
    ),
    ProtocolDefinition(
        "HumanReviewReconciliationReport",
        HumanReviewReconciliationReport,
        "Bounded human-review manuscript reconciliation report.",
    ),
    ProtocolDefinition(
        "HumanReviewReconciliationCycle",
        HumanReviewReconciliationCycle,
        "Immutable reconciliation-cycle index entry.",
    ),
    ProtocolDefinition(
        "HumanReviewReconciliationIndex",
        HumanReviewReconciliationIndex,
        "Derived latest pointer over immutable reconciliation cycles.",
    ),
    ProtocolDefinition(
        "ReviewerChangeRequest",
        ReviewerChangeRequest,
        "One structured reviewer workflow request.",
    ),
    ProtocolDefinition(
        "ReviewerChangeRequestSet",
        ReviewerChangeRequestSet,
        "Immutable structured reviewer request set.",
    ),
    ProtocolDefinition("ClaimTable", ClaimTable, "Claim and evidence-link table."),
    ProtocolDefinition(
        "CitationRecord",
        CitationRecord,
        "Retrieval-backed citation metadata for literature context only.",
    ),
    ProtocolDefinition(
        "CitationRegistry",
        CitationRegistry,
        "Deterministic citation registry; not novelty or verification proof.",
    ),
    ProtocolDefinition(
        "BibliographyEntry",
        BibliographyEntry,
        "Deterministic bibliography entry backed by source provenance.",
    ),
    ProtocolDefinition(
        "CitationUsage",
        CitationUsage,
        "Observed citation-key usage in a manuscript draft.",
    ),
    ProtocolDefinition(
        "CitationSafetyReport",
        CitationSafetyReport,
        "Citation-safety report for manuscript drafts.",
    ),
    ProtocolDefinition(
        "ClaimAdjudication",
        ClaimAdjudication,
        "Bounded semantic sentence classification; not artifact verification or evidence.",
    ),
    ProtocolDefinition(
        "ClaimSupportItem",
        ClaimSupportItem,
        "One sentence-level claim-to-source support classification.",
    ),
    ProtocolDefinition(
        "ClaimSupportAuditReport",
        ClaimSupportAuditReport,
        "Deterministic citation-placement and claim-support audit report.",
    ),
    ProtocolDefinition(
        "LiteratureGapStatement",
        LiteratureGapStatement,
        "Bounded literature-gap statement; not novelty proof.",
    ),
    ProtocolDefinition(
        "LiteraturePositioningContract",
        LiteraturePositioningContract,
        "Bounded literature-positioning contract.",
    ),
    ProtocolDefinition(
        "LiteraturePositioningReport",
        LiteraturePositioningReport,
        "Citation-safe literature-positioning report.",
    ),
    ProtocolDefinition("FinalNucleus", FinalNucleus, "Selected abstraction or candidate nucleus."),
    ProtocolDefinition("ManuscriptPlan", ManuscriptPlan, "Structured manuscript plan."),
    ProtocolDefinition(
        "ProseSectionContract",
        ProseSectionContract,
        "Section-level prose contract with claim/evidence grounding.",
    ),
    ProtocolDefinition(
        "ProsePromptContract",
        ProsePromptContract,
        "One-section prose prompt contract.",
    ),
    ProtocolDefinition(
        "ProseGenerationRequest",
        ProseGenerationRequest,
        "Provider-neutral one-section prose generation request.",
    ),
    ProtocolDefinition(
        "ProseGenerationParseResult",
        ProseGenerationParseResult,
        "Parsed one-section prose generation response.",
    ),
    ProtocolDefinition(
        "ProseSafetyReport",
        ProseSafetyReport,
        "One-section prose safety and grounding report.",
    ),
    ProtocolDefinition(
        "NarrativeManuscriptContract",
        NarrativeManuscriptContract,
        "Deterministic paper narrative contract; not scientific validation.",
    ),
    ProtocolDefinition(
        "PaperShapeCritique",
        PaperShapeCritique,
        "Deterministic paper-shape critique; not verification evidence.",
    ),
    ProtocolDefinition(
        "PaperShapeScore",
        PaperShapeScore,
        "Weighted diagnostic paper-shape score.",
    ),
    ProtocolDefinition(
        "MainMessageAssessment",
        MainMessageAssessment,
        "Central-message diagnostic assessment.",
    ),
    ProtocolDefinition(
        "LiteraturePositioningAssessment",
        LiteraturePositioningAssessment,
        "Literature-positioning diagnostic assessment.",
    ),
    ProtocolDefinition(
        "ModelNotationAssessment",
        ModelNotationAssessment,
        "Model and notation diagnostic assessment.",
    ),
    ProtocolDefinition(
        "MainResultAssessment",
        MainResultAssessment,
        "One-main-result diagnostic assessment.",
    ),
    ProtocolDefinition(
        "NumericalStudyAssessment",
        NumericalStudyAssessment,
        "Numerical-study purpose diagnostic assessment.",
    ),
    ProtocolDefinition(
        "EmpiricalBoundaryAssessment",
        EmpiricalBoundaryAssessment,
        "Synthetic/empirical boundary diagnostic assessment.",
    ),
    ProtocolDefinition(
        "AppendixAllocationAssessment",
        AppendixAllocationAssessment,
        "Appendix-allocation diagnostic assessment.",
    ),
    ProtocolDefinition("DraftSkeleton", DraftSkeleton, "Deterministic draft scaffold."),
    ProtocolDefinition(
        "ManuscriptDraftingPlan",
        ManuscriptDraftingPlan,
        "Section-by-section manuscript drafting plan.",
    ),
    ProtocolDefinition(
        "SectionDraftingTask",
        SectionDraftingTask,
        "One planned section drafting task.",
    ),
    ProtocolDefinition(
        "SectionDraftingResult",
        SectionDraftingResult,
        "One safety-checked section draft result.",
    ),
    ProtocolDefinition(
        "SectionDraftSafetySummary",
        SectionDraftSafetySummary,
        "Compact section prose safety summary.",
    ),
    ProtocolDefinition(
        "CompleteMarkdownDraft",
        CompleteMarkdownDraft,
        "Assembled Markdown manuscript draft; presentation context only.",
    ),
    ProtocolDefinition(
        "ManuscriptDraftingReport",
        ManuscriptDraftingReport,
        "Machine-readable section drafting report.",
    ),
    ProtocolDefinition(
        "ManuscriptAssemblyReport",
        ManuscriptAssemblyReport,
        "Markdown manuscript assembly report.",
    ),
    ProtocolDefinition(
        "LatexExportContract",
        LatexExportContract,
        "Markdown-to-LaTeX export contract; presentation only.",
    ),
    ProtocolDefinition(
        "LatexSourceMapEntry",
        LatexSourceMapEntry,
        "One LaTeX source-map entry.",
    ),
    ProtocolDefinition(
        "LatexSourceMap",
        LatexSourceMap,
        "LaTeX source map back to manuscript, claim, evidence, and citation sources.",
    ),
    ProtocolDefinition(
        "LatexSafetyReport",
        LatexSafetyReport,
        "LaTeX export safety report.",
    ),
    ProtocolDefinition(
        "LatexRenderConfig",
        LatexRenderConfig,
        "Optional gated LaTeX render/check configuration.",
    ),
    ProtocolDefinition(
        "LatexRenderResult",
        LatexRenderResult,
        "Optional gated LaTeX render/check result.",
    ),
    ProtocolDefinition(
        "LatexCompileCheckReport",
        LatexCompileCheckReport,
        "Optional LaTeX compile/check aggregate report.",
    ),
    ProtocolDefinition(
        "LatexExportResult",
        LatexExportResult,
        "Complete LaTeX export report; presentation only.",
    ),
    ProtocolDefinition(
        "PaperCriticFinding",
        PaperCriticFinding,
        "One deterministic paper critic finding; not evidence.",
    ),
    ProtocolDefinition(
        "PaperCriticReport",
        PaperCriticReport,
        "Paper-level critique over Markdown/LaTeX artifacts; not scientific validation.",
    ),
    ProtocolDefinition(
        "PaperReleaseReadinessPreview",
        PaperReleaseReadinessPreview,
        "Non-authoritative manuscript-quality readiness preview.",
    ),
    ProtocolDefinition(
        "SectionRevisionPlan",
        SectionRevisionPlan,
        "Safe deterministic revision plan for one manuscript section.",
    ),
    ProtocolDefinition(
        "PaperRevisionPlan",
        PaperRevisionPlan,
        "Deterministic non-authoritative paper revision plan.",
    ),
    ProtocolDefinition(
        "PaperRevisionPatch",
        PaperRevisionPatch,
        "One deterministic safe fake revision patch.",
    ),
    ProtocolDefinition(
        "RevisionSafetyReport",
        RevisionSafetyReport,
        "Safety report for a revised manuscript draft.",
    ),
    ProtocolDefinition(
        "PaperRevisionResult",
        PaperRevisionResult,
        "Result of one deterministic fake paper revision pass.",
    ),
    ProtocolDefinition(
        "QualityRepairReport",
        QualityRepairReport,
        "Bounded manuscript-quality repair report.",
    ),
    ProtocolDefinition(
        "FullPaperGenerationConfig",
        FullPaperGenerationConfig,
        "Configuration for end-to-end non-evidence paper generation.",
    ),
    ProtocolDefinition(
        "FullPaperGenerationStep",
        FullPaperGenerationStep,
        "One step in the full-paper generation workflow.",
    ),
    ProtocolDefinition(
        "FullPaperArtifactBundle",
        FullPaperArtifactBundle,
        "Artifact IDs that make up one generated paper package.",
    ),
    ProtocolDefinition(
        "FullPaperGenerationReport",
        FullPaperGenerationReport,
        "Summary report for full-paper package generation.",
    ),
    ProtocolDefinition(
        "FullPaperGenerationResult",
        FullPaperGenerationResult,
        "Typed result for full-paper generation commands.",
    ),
    ProtocolDefinition(
        "FullPaperReleaseGateConfig",
        FullPaperReleaseGateConfig,
        "Policy for generated-paper human-review readiness.",
    ),
    ProtocolDefinition(
        "FullPaperReleaseCheck",
        FullPaperReleaseCheck,
        "One deterministic generated-paper readiness check.",
    ),
    ProtocolDefinition(
        "FullPaperReleaseFinding",
        FullPaperReleaseFinding,
        "One generated-paper readiness finding.",
    ),
    ProtocolDefinition(
        "FullPaperBundleCompletenessReport",
        FullPaperBundleCompletenessReport,
        "Artifact completeness and provenance report for a paper bundle.",
    ),
    ProtocolDefinition(
        "FullPaperEvidenceBoundaryReport",
        FullPaperEvidenceBoundaryReport,
        "Evidence-boundary report for generated paper text.",
    ),
    ProtocolDefinition(
        "FullPaperReadinessDecision",
        FullPaperReadinessDecision,
        "Human-review-only readiness decision.",
    ),
    ProtocolDefinition(
        "FullPaperReleaseReport",
        FullPaperReleaseReport,
        "End-to-end generated-paper readiness report.",
    ),
    ProtocolDefinition(
        "ReviewerBundleSummary",
        ReviewerBundleSummary,
        "Reviewer-facing generated-paper bundle summary.",
    ),
    ProtocolDefinition(
        "HumanReviewArtifact",
        HumanReviewArtifact,
        "Local human-review intake artifact.",
    ),
    ProtocolDefinition(
        "ProofArtifact",
        ProofArtifact,
        "Local proof artifact intake record.",
    ),
    ProtocolDefinition(
        "ExperimentArtifact",
        ExperimentArtifact,
        "Local experiment artifact intake record.",
    ),
    ProtocolDefinition("ResearchObject", ResearchObject, "Packaged reproducible research object."),
    ProtocolDefinition(
        "ResearchObjectManifest",
        ResearchObjectManifest,
        "References to research-object package files.",
    ),
    ProtocolDefinition(
        "ReproducibilityManifest",
        ReproducibilityManifest,
        "Derived reproducibility manifest.",
    ),
    ProtocolDefinition("RunSummary", LedgerSummary, "Derived run ledger summary."),
    ProtocolDefinition("PaperSkeleton", PaperSkeleton, "Assembled paper-shaped scaffold."),
    ProtocolDefinition("FinalAuditReport", FinalAuditReport, "Internal-consistency audit report."),
    ProtocolDefinition(
        "ReleaseGateDecision",
        ReleaseGateDecision,
        "Deterministic release decision.",
    ),
    ProtocolDefinition(
        "ExportReadinessReport",
        ExportReadinessReport,
        "Readiness report for future prose and LaTeX export.",
    ),
    ProtocolDefinition(
        "ReplayVerificationReport",
        ReplayVerificationReport,
        "Read-only replay consistency report.",
    ),
    ProtocolDefinition("DiagnosticReport", DiagnosticReport, "Read-only failure diagnostics."),
    ProtocolDefinition(
        "OutputHygieneReport",
        OutputHygieneReport,
        "Read-only run-directory hygiene report.",
    ),
    ProtocolDefinition(
        "HygieneRemediationPlan",
        HygieneRemediationPlan,
        "Non-executing hygiene remediation plan.",
    ),
    ProtocolDefinition(
        "PipelineRunConfig",
        PipelineRunConfig,
        "One-command pipeline configuration.",
    ),
    ProtocolDefinition(
        "PipelineRunReport",
        PipelineRunReport,
        "Ledgered pipeline execution report.",
    ),
    ProtocolDefinition(
        "PipelineDryRunPlan",
        PipelineDryRunPlan,
        "Read-only pipeline execution plan.",
    ),
    ProtocolDefinition(
        "PipelineStagePlan",
        PlannedStage,
        "One planned stage from the dry-run planner.",
    ),
    ProtocolDefinition("AdapterConfig", AdapterConfig, "Fake-default adapter configuration."),
    ProtocolDefinition(
        "LLMBudgetConfig",
        LLMBudgetConfig,
        "Explicit budget limits for gated LLM paper orchestration.",
    ),
    ProtocolDefinition(
        "LLMBudgetUsage",
        LLMBudgetUsage,
        "Planned or observed LLM usage accounting.",
    ),
    ProtocolDefinition(
        "LLMBudgetDecision",
        LLMBudgetDecision,
        "Preflight LLM budget decision.",
    ),
    ProtocolDefinition(
        "LLMCallAccountingRecord",
        LLMCallAccountingRecord,
        "Secret-safe accounting record for one LLM call.",
    ),
    ProtocolDefinition(
        "LLMRunSafetyReport",
        LLMRunSafetyReport,
        "Evidence-boundary report for LLM orchestration.",
    ),
    ProtocolDefinition(
        "LLMOrchestrationConfig",
        LLMOrchestrationConfig,
        "Configuration for gated end-to-end LLM paper orchestration.",
    ),
    ProtocolDefinition(
        "LLMOrchestrationStep",
        LLMOrchestrationStep,
        "One step in the gated LLM paper workflow.",
    ),
    ProtocolDefinition(
        "LLMOrchestrationReport",
        LLMOrchestrationReport,
        "Report for gated end-to-end LLM paper orchestration.",
    ),
    ProtocolDefinition(
        "LLMOrchestrationResult",
        LLMOrchestrationResult,
        "Typed result for gated LLM paper orchestration commands.",
    ),
    ProtocolDefinition(
        "LLMPromptContract",
        LLMPromptContract,
        "Stage A candidate-generation prompt contract.",
    ),
    ProtocolDefinition(
        "LLMCandidateParseReport",
        LLMCandidateParseReport,
        "Stage A LLM candidate parse and safety report.",
    ),
    ProtocolDefinition(
        "GeneratedSectionDraft",
        GeneratedSectionDraft,
        "Future prose adapter section draft placeholder.",
    ),
    ProtocolDefinition(
        "HumanReviewDecision",
        HumanReviewDecision,
        "Future human-review adapter decision placeholder.",
    ),
    ProtocolDefinition("DataRequirement", DataRequirement, "Candidate data regime enum."),
    ProtocolDefinition("EvidenceType", ArtifactType, "Artifact/evidence category enum."),
    ProtocolDefinition("ClaimLabel", VerificationLabel, "Claim and verification label enum."),
    ProtocolDefinition("AdapterBackend", AdapterBackend, "Adapter backend enum."),
    ProtocolDefinition("RetrievalBackend", RetrievalBackend, "Retrieval backend enum."),
    ProtocolDefinition("ReviewerBackend", ReviewerBackend, "Reviewer backend enum."),
    ProtocolDefinition("ProofBackend", ProofBackend, "Proof backend enum."),
    ProtocolDefinition("ExperimentBackend", ExperimentBackend, "Experiment backend enum."),
    ProtocolDefinition("ProseBackend", ProseBackend, "Prose backend enum."),
    ProtocolDefinition(
        "ManuscriptDraftStatus",
        ManuscriptDraftStatus,
        "Manuscript draft status enum.",
    ),
    ProtocolDefinition(
        "FullPaperGenerationStatus",
        FullPaperGenerationStatus,
        "Full-paper generation aggregate status enum.",
    ),
    ProtocolDefinition(
        "FullPaperGenerationStepStatus",
        FullPaperGenerationStepStatus,
        "Full-paper generation step status enum.",
    ),
    ProtocolDefinition(
        "FullPaperReleaseStatus",
        FullPaperReleaseStatus,
        "Generated-paper human-review readiness status enum.",
    ),
    ProtocolDefinition(
        "FullPaperReleaseFindingSeverity",
        FullPaperReleaseFindingSeverity,
        "Generated-paper readiness finding severity enum.",
    ),
    ProtocolDefinition(
        "LLMOrchestrationStatus",
        LLMOrchestrationStatus,
        "LLM paper orchestration aggregate status enum.",
    ),
    ProtocolDefinition(
        "LLMOrchestrationStepStatus",
        LLMOrchestrationStepStatus,
        "LLM paper orchestration step status enum.",
    ),
    ProtocolDefinition(
        "LLMBudgetDecisionStatus",
        LLMBudgetDecisionStatus,
        "LLM budget decision status enum.",
    ),
    ProtocolDefinition(
        "LLMCallStatus",
        LLMCallStatus,
        "LLM call accounting status enum.",
    ),
    ProtocolDefinition("ExperimentKind", ExperimentKind, "Synthetic experiment kind enum."),
    ProtocolDefinition("ReleaseStatus", ReleaseGateStatus, "Release status enum."),
    ProtocolDefinition(
        "ProtocolCompatibilityStatus",
        ProtocolCompatibilityStatus,
        "Protocol schema compatibility status enum.",
    ),
)


def protocol_slug(name: str) -> str:
    """Convert a public protocol name to deterministic kebab case."""
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1-\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", first).lower()


def get_protocol_definitions() -> tuple[ProtocolDefinition, ...]:
    """Return the immutable protocol registry in canonical export order."""
    return PROTOCOL_DEFINITIONS


def get_protocol_definition(name: str) -> ProtocolDefinition:
    """Resolve one stable protocol name or raise a clear error."""
    for definition in PROTOCOL_DEFINITIONS:
        if definition.name == name:
            return definition
    raise KeyError(f"Unknown fActorI protocol: {name}")


__all__ = [
    "PROTOCOL_DEFINITIONS",
    "PROTOCOL_GENERATOR",
    "PROTOCOL_SOURCE",
    "PROTOCOL_VERSION",
    "SCHEMA_FORMAT",
    "AdapterBackend",
    "ExperimentBackend",
    "ExperimentKind",
    "FullPaperGenerationStatus",
    "FullPaperGenerationStepStatus",
    "FullPaperReleaseStatus",
    "FullPaperReleaseFindingSeverity",
    "LLMBudgetDecisionStatus",
    "LLMCallStatus",
    "LLMOrchestrationStatus",
    "LLMOrchestrationStepStatus",
    "ProofBackend",
    "ProseBackend",
    "ProtocolDefinition",
    "RetrievalBackend",
    "ReviewerBackend",
    "get_protocol_definition",
    "get_protocol_definitions",
    "protocol_slug",
]
