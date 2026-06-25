from factori.adapters.config import AdapterConfig
from factori.protocols import (
    PROTOCOL_VERSION,
    get_protocol_definition,
    get_protocol_definitions,
    protocol_slug,
)
from factori.schemas import (
    ArtifactRef,
    FakeExperimentResult,
    FakeProofResult,
    LedgerTipValidationReport,
    LLMCandidateParseReport,
    LLMPromptContract,
    PipelineStageResult,
    RunStatusReport,
    StageBReviewerReport,
    StageRerunDecision,
)
from factori.stage_c_selection import StageCSelectionResult


def test_protocol_registry_is_complete_unique_and_deterministic() -> None:
    first = get_protocol_definitions()
    second = get_protocol_definitions()
    names = [definition.name for definition in first]

    assert first == second
    assert len(first) == 63
    assert len(names) == len(set(names))
    assert PROTOCOL_VERSION == "0.2.0"
    assert names[:3] == ["Candidate", "ScoreVector", "LedgerCommit"]
    assert names[-1] == "ProtocolCompatibilityStatus"


def test_protocol_aliases_map_to_existing_typed_models() -> None:
    assert get_protocol_definition("ArtifactRecord").model is ArtifactRef
    assert get_protocol_definition("StageResult").model is PipelineStageResult
    assert get_protocol_definition("ReviewerReport").model is StageBReviewerReport
    assert get_protocol_definition("StageCSelectionResult").model is StageCSelectionResult
    assert get_protocol_definition("ProofVerificationResult").model is FakeProofResult
    assert get_protocol_definition("ExperimentRunResult").model is FakeExperimentResult
    assert get_protocol_definition("AdapterConfig").model is AdapterConfig
    assert get_protocol_definition("RunStatusReport").model is RunStatusReport
    assert get_protocol_definition("StageRerunDecision").model is StageRerunDecision
    assert get_protocol_definition("LedgerTipValidationReport").model is LedgerTipValidationReport
    assert get_protocol_definition("LLMPromptContract").model is LLMPromptContract
    assert get_protocol_definition("LLMCandidateParseReport").model is LLMCandidateParseReport


def test_server_facing_protocols_and_enums_are_registered() -> None:
    names = {definition.name for definition in get_protocol_definitions()}

    expected = {
        "RunStatusReport",
        "ResumeValidationReport",
        "StageCheckpoint",
        "RerunPolicy",
        "StageRerunDecision",
        "StageRerunStatus",
        "LedgerTipValidationReport",
        "PipelineStagePlan",
        "ArtifactManifest",
        "ResearchObjectManifest",
        "ReproducibilityManifest",
        "RunSummary",
        "LLMPromptContract",
        "LLMCandidateParseReport",
        "LLMReviewerPromptContract",
        "LLMReviewerParseReport",
        "RetrievalQuery",
        "RetrievalRunReport",
        "RetrievalParseReport",
        "ProofVerificationContract",
        "HumanReviewDecision",
        "GeneratedSectionDraft",
        "DataRequirement",
        "EvidenceType",
        "ClaimLabel",
        "AdapterBackend",
        "RetrievalBackend",
        "ReviewerBackend",
        "ProofBackend",
        "ReleaseStatus",
        "ProtocolCompatibilityStatus",
    }

    assert expected <= names


def test_protocol_filenames_are_language_neutral_and_stable() -> None:
    assert protocol_slug("StageCSelectionResult") == "stage-c-selection-result"
    assert get_protocol_definition("PipelineRunReport").filename == (
        "pipeline-run-report.schema.json"
    )
