from __future__ import annotations

import importlib

import factori.schemas as schemas
from factori.protocols import get_protocol_definition, get_protocol_definitions
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AutonomousPaperRunHandoff,
    AutonomousPaperRunIndex,
    AutonomousPaperRunReport,
    AutonomousPaperRunStage,
    BranchStatus,
    Candidate,
    ClaimEvidenceMap,
    ClaimEvidenceMapLink,
    CompleteMarkdownDraft,
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
    HumanReviewArtifact,
    LatexExportResult,
    ManuscriptDraftingPlan,
    NarrativeManuscriptContract,
    PaperCriticReport,
    PaperRevisionResult,
    PaperShapeCritique,
    PipelineRunConfig,
    ProofArtifact,
    QualityRepairReport,
    ReviewerBundleSummary,
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
    assert len(get_protocol_definitions()) == 219
