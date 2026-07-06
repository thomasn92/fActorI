from __future__ import annotations

import importlib

import factori.schemas as schemas
from factori.protocols import get_protocol_definition, get_protocol_definitions
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AutonomousPaperCheckpoint,
    AutonomousPaperCheckpointIndex,
    AutonomousPaperResumeReport,
    AutonomousPaperRunHandoff,
    AutonomousPaperRunIndex,
    AutonomousPaperRunReport,
    AutonomousPaperRunStage,
    BranchRouteDecision,
    BranchRouteExecutionHint,
    BranchRouteInspectionReport,
    BranchRoutePlan,
    BranchRouteType,
    BranchStatus,
    Candidate,
    ClaimEvidenceMap,
    ClaimEvidenceMapLink,
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
    EvidenceAwareRefreshReport,
    ExperimentArtifact,
    FinalBundleReplaySummary,
    FinalBundleVerificationCheck,
    FinalBundleVerificationReport,
    FinalReleaseBundle,
    FinalReleaseBundleArtifact,
    FinalReleaseBundleIndex,
    FinalReleaseBundleManifest,
    FinalReleaseBundleReport,
    FinalReleaseReproducibilityManifest,
    FullPaperGenerationReport,
    GenerationMutationCandidate,
    GenerationMutationContext,
    GenerationMutationDiversityCheck,
    GenerationMutationInspectionReport,
    GenerationMutationOperator,
    GenerationMutationPlan,
    HumanReviewArtifact,
    IdeaClusterDiagnostic,
    IdeaNodeFeatureVector,
    IdeaSpaceAxis,
    IdeaSpaceDiversityReport,
    IdeaSpaceInspectionReport,
    IdeaSpacePCADiagnostic,
    LatexExportResult,
    ManuscriptDraftingPlan,
    MutationTournamentComparison,
    MutationTournamentEntry,
    MutationTournamentInspectionReport,
    MutationTournamentResult,
    MutationTournamentSpec,
    NarrativeManuscriptContract,
    PaperCriticReport,
    PaperRevisionResult,
    PaperShapeCritique,
    PipelineRunConfig,
    ProofArtifact,
    QualityRepairReport,
    ReviewerBundleSummary,
    RouteExecutionInputContract,
    RouteExecutionInspectionReport,
    RouteExecutionOutputContract,
    RouteExecutionReport,
    RouteExecutionResult,
    RouteExecutionSpec,
    RouteExecutionStatus,
    ScientificSubstrate,
    ScientificSubstrateBuildReport,
    ScientificSubstrateInspectionReport,
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
    VerificationLabel,
)


def test_factori_schemas_imports_as_package() -> None:
    assert schemas.__file__.endswith("factori/schemas/__init__.py")
    assert hasattr(schemas, "__path__")


def test_key_public_models_import_from_factori_schemas() -> None:
    assert Candidate(id="candidate-1", question="Can schemas remain compatible?")
    assert PipelineRunConfig(run_id="run-1", domain="human geography")
    assert NarrativeManuscriptContract(contract_id="contract-1", run_id="run-1")
    assert PaperShapeCritique in vars(schemas).values()
    assert ManuscriptDraftingPlan in vars(schemas).values()
    assert CompleteMarkdownDraft in vars(schemas).values()
    assert LatexExportResult in vars(schemas).values()
    assert PaperCriticReport in vars(schemas).values()
    assert FullPaperGenerationReport in vars(schemas).values()
    assert PaperRevisionResult in vars(schemas).values()
    assert QualityRepairReport in vars(schemas).values()
    assert HumanReviewArtifact in vars(schemas).values()
    assert ProofArtifact in vars(schemas).values()
    assert ExperimentArtifact in vars(schemas).values()
    assert ReviewerBundleSummary in vars(schemas).values()
    assert ClaimEvidenceMap in vars(schemas).values()
    assert ClaimEvidenceMapLink in vars(schemas).values()
    assert EvidenceAwareRefreshReport in vars(schemas).values()
    assert FinalReleaseBundleArtifact in vars(schemas).values()
    assert FinalReleaseBundleManifest in vars(schemas).values()
    assert FinalReleaseReproducibilityManifest in vars(schemas).values()
    assert FinalReleaseBundle in vars(schemas).values()
    assert FinalReleaseBundleReport in vars(schemas).values()
    assert FinalReleaseBundleIndex in vars(schemas).values()
    assert FinalBundleVerificationCheck in vars(schemas).values()
    assert FinalBundleReplaySummary in vars(schemas).values()
    assert FinalBundleVerificationReport in vars(schemas).values()
    assert AutonomousPaperRunStage in vars(schemas).values()
    assert AutonomousPaperRunHandoff in vars(schemas).values()
    assert AutonomousPaperRunReport in vars(schemas).values()
    assert AutonomousPaperRunIndex in vars(schemas).values()
    assert AutonomousPaperCheckpoint in vars(schemas).values()
    assert AutonomousPaperCheckpointIndex in vars(schemas).values()
    assert AutonomousPaperResumeReport in vars(schemas).values()
    assert IdeaNodeFeatureVector in vars(schemas).values()
    assert IdeaSpaceAxis in vars(schemas).values()
    assert IdeaSpacePCADiagnostic in vars(schemas).values()
    assert IdeaClusterDiagnostic in vars(schemas).values()
    assert IdeaSpaceDiversityReport in vars(schemas).values()
    assert IdeaSpaceInspectionReport in vars(schemas).values()
    assert ScientificSubstrate in vars(schemas).values()
    assert ScientificSubstrateBuildReport in vars(schemas).values()
    assert ScientificSubstrateInspectionReport in vars(schemas).values()
    assert BranchRouteType in vars(schemas).values()
    assert BranchRouteExecutionHint in vars(schemas).values()
    assert BranchRouteDecision in vars(schemas).values()
    assert BranchRoutePlan in vars(schemas).values()
    assert BranchRouteInspectionReport in vars(schemas).values()
    assert RouteExecutionStatus in vars(schemas).values()
    assert RouteExecutionInputContract in vars(schemas).values()
    assert RouteExecutionOutputContract in vars(schemas).values()
    assert RouteExecutionSpec in vars(schemas).values()
    assert RouteExecutionResult in vars(schemas).values()
    assert RouteExecutionReport in vars(schemas).values()
    assert RouteExecutionInspectionReport in vars(schemas).values()
    assert SubstratePromotionConfig in vars(schemas).values()
    assert SubstratePromotionCandidate in vars(schemas).values()
    assert SubstratePromotionDecision in vars(schemas).values()
    assert SubstratePromotionReport in vars(schemas).values()
    assert SubstratePromotionInspectionReport in vars(schemas).values()
    assert SubstrateTournamentSpec in vars(schemas).values()
    assert SubstrateTournamentEntry in vars(schemas).values()
    assert SubstrateTournamentResult in vars(schemas).values()
    assert SubstrateTournamentComparison in vars(schemas).values()
    assert SubstrateTournamentInspectionReport in vars(schemas).values()
    assert CreativeMutationOperator in vars(schemas).values()
    assert CreativeMutationCandidate in vars(schemas).values()
    assert CreativeMutationPlan in vars(schemas).values()
    assert CreativeMutationReport in vars(schemas).values()
    assert CreativeMutationInspectionReport in vars(schemas).values()
    assert CreativeSearchStopReason in vars(schemas).values()
    assert CreativeSearchControllerConfig in vars(schemas).values()
    assert CreativeSearchLineageEntry in vars(schemas).values()
    assert CreativeSearchCycle in vars(schemas).values()
    assert CreativeSearchControllerReport in vars(schemas).values()
    assert CreativeSearchInspectionReport in vars(schemas).values()
    assert GenerationMutationOperator in vars(schemas).values()
    assert GenerationMutationContext in vars(schemas).values()
    assert GenerationMutationCandidate in vars(schemas).values()
    assert GenerationMutationDiversityCheck in vars(schemas).values()
    assert GenerationMutationPlan in vars(schemas).values()
    assert GenerationMutationInspectionReport in vars(schemas).values()
    assert MutationTournamentSpec in vars(schemas).values()
    assert MutationTournamentEntry in vars(schemas).values()
    assert MutationTournamentResult in vars(schemas).values()
    assert MutationTournamentComparison in vars(schemas).values()
    assert MutationTournamentInspectionReport in vars(schemas).values()
    assert ArtifactRef in vars(schemas).values()


def test_key_public_enums_import_from_factori_schemas() -> None:
    assert DataRequirement.NO_DATA.value == "NoData"
    assert VerificationLabel.CONJECTURE.value == "Conjecture"
    assert BranchStatus.ACTIVE.value == "Active"
    assert ArtifactType.REPORT.value == "report"


def test_schema_all_contains_expected_public_names() -> None:
    expected = {
        "Candidate",
        "PipelineRunConfig",
        "NarrativeManuscriptContract",
        "PaperShapeCritique",
        "ManuscriptDraftingPlan",
        "CompleteMarkdownDraft",
        "LatexExportResult",
        "PaperCriticReport",
        "FullPaperGenerationReport",
        "PaperRevisionResult",
        "QualityRepairReport",
        "HumanReviewArtifact",
        "ProofArtifact",
        "ExperimentArtifact",
        "ReviewerBundleSummary",
        "ClaimEvidenceMap",
        "ClaimEvidenceMapLink",
        "EvidenceAwareRefreshReport",
        "FinalReleaseBundleArtifact",
        "FinalReleaseBundleManifest",
        "FinalReleaseReproducibilityManifest",
        "FinalReleaseBundle",
        "FinalReleaseBundleReport",
        "FinalReleaseBundleIndex",
        "FinalBundleVerificationCheck",
        "FinalBundleReplaySummary",
        "FinalBundleVerificationReport",
        "AutonomousPaperRunStage",
        "AutonomousPaperRunHandoff",
        "AutonomousPaperRunReport",
        "AutonomousPaperRunIndex",
        "AutonomousPaperCheckpoint",
        "AutonomousPaperCheckpointIndex",
        "AutonomousPaperResumeReport",
        "IdeaNodeFeatureVector",
        "IdeaSpaceAxis",
        "IdeaSpacePCADiagnostic",
        "IdeaClusterDiagnostic",
        "IdeaSpaceDiversityReport",
        "IdeaSpaceInspectionReport",
        "ScientificSubstrate",
        "ScientificSubstrateBuildReport",
        "ScientificSubstrateInspectionReport",
        "SubstratePromotionConfig",
        "SubstratePromotionCandidate",
        "SubstratePromotionDecision",
        "SubstratePromotionReport",
        "SubstratePromotionInspectionReport",
        "BranchRouteType",
        "BranchRouteExecutionHint",
        "BranchRouteDecision",
        "BranchRoutePlan",
        "BranchRouteInspectionReport",
        "RouteExecutionStatus",
        "RouteExecutionInputContract",
        "RouteExecutionOutputContract",
        "RouteExecutionSpec",
        "RouteExecutionResult",
        "RouteExecutionReport",
        "RouteExecutionInspectionReport",
        "SubstrateTournamentSpec",
        "SubstrateTournamentEntry",
        "SubstrateTournamentResult",
        "SubstrateTournamentComparison",
        "SubstrateTournamentInspectionReport",
        "CreativeMutationOperator",
        "CreativeMutationCandidate",
        "CreativeMutationPlan",
        "CreativeMutationReport",
        "CreativeMutationInspectionReport",
        "CreativeSearchStopReason",
        "CreativeSearchControllerConfig",
        "CreativeSearchLineageEntry",
        "CreativeSearchCycle",
        "CreativeSearchControllerReport",
        "CreativeSearchInspectionReport",
        "GenerationMutationOperator",
        "GenerationMutationContext",
        "GenerationMutationCandidate",
        "GenerationMutationDiversityCheck",
        "GenerationMutationPlan",
        "GenerationMutationInspectionReport",
        "MutationTournamentSpec",
        "MutationTournamentEntry",
        "MutationTournamentResult",
        "MutationTournamentComparison",
        "MutationTournamentInspectionReport",
        "ArtifactRef",
        "DataRequirement",
        "VerificationLabel",
        "assert_mvp_data_admissible",
    }

    assert expected <= set(schemas.__all__)
    assert len(schemas.__all__) == len(set(schemas.__all__))


def test_schema_submodules_import_without_obvious_cycles() -> None:
    submodules = [
        "base",
        "enums",
        "artifacts",
        "candidates",
        "stages",
        "control",
        "adapters",
        "retrieval",
        "verification",
        "manuscript",
        "audit",
        "pipeline",
        "protocol_models",
    ]

    for submodule in submodules:
        imported = importlib.import_module(f"factori.schemas.{submodule}")
        assert imported is not None


def test_protocol_source_model_paths_remain_stable() -> None:
    assert get_protocol_definition("Candidate").source_model == "factori.schemas.Candidate"
    assert get_protocol_definition("PipelineRunReport").source_model == (
        "factori.schemas.PipelineRunReport"
    )
    assert get_protocol_definition("PaperShapeCritique").source_model == (
        "factori.schemas.PaperShapeCritique"
    )
    assert len(get_protocol_definitions()) == 308
