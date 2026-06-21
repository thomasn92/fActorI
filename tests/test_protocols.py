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
    PipelineStageResult,
    StageBReviewerReport,
)
from factori.stage_c_selection import StageCSelectionResult


def test_protocol_registry_is_complete_unique_and_deterministic() -> None:
    first = get_protocol_definitions()
    second = get_protocol_definitions()
    names = [definition.name for definition in first]

    assert first == second
    assert len(first) == 32
    assert len(names) == len(set(names))
    assert PROTOCOL_VERSION == "0.1.0"
    assert names[:3] == ["Candidate", "ScoreVector", "LedgerCommit"]
    assert names[-1] == "AdapterConfig"


def test_protocol_aliases_map_to_existing_typed_models() -> None:
    assert get_protocol_definition("ArtifactRecord").model is ArtifactRef
    assert get_protocol_definition("StageResult").model is PipelineStageResult
    assert get_protocol_definition("ReviewerReport").model is StageBReviewerReport
    assert get_protocol_definition("StageCSelectionResult").model is StageCSelectionResult
    assert get_protocol_definition("ProofVerificationResult").model is FakeProofResult
    assert get_protocol_definition("ExperimentRunResult").model is FakeExperimentResult
    assert get_protocol_definition("AdapterConfig").model is AdapterConfig


def test_protocol_filenames_are_language_neutral_and_stable() -> None:
    assert protocol_slug("StageCSelectionResult") == "stage-c-selection-result"
    assert get_protocol_definition("PipelineRunReport").filename == (
        "pipeline-run-report.schema.json"
    )
