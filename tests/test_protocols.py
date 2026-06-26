from factori.adapters.config import AdapterConfig
from factori.protocols import (
    PROTOCOL_VERSION,
    get_protocol_definition,
    get_protocol_definitions,
    protocol_slug,
)
from factori.schemas import (
    ArtifactRef,
    CompleteMarkdownDraft,
    ExperimentRunContract,
    ExperimentRunResult,
    LatexExportContract,
    LatexExportResult,
    LatexRenderResult,
    LatexSafetyReport,
    LatexSourceMap,
    LedgerTipValidationReport,
    LLMCandidateParseReport,
    LLMPromptContract,
    ManuscriptAssemblyReport,
    ManuscriptDraftingPlan,
    ManuscriptDraftingReport,
    PipelineStageResult,
    ProofVerificationResult,
    ProseGenerationParseResult,
    ProseGenerationRequest,
    ProsePromptContract,
    ProseSafetyReport,
    ProseSectionContract,
    RunStatusReport,
    SectionDraftingResult,
    SectionDraftingTask,
    StageBReviewerReport,
    StageRerunDecision,
)
from factori.stage_c_selection import StageCSelectionResult


def test_protocol_registry_is_complete_unique_and_deterministic() -> None:
    first = get_protocol_definitions()
    second = get_protocol_definitions()
    names = [definition.name for definition in first]

    assert first == second
    assert len(first) == 106
    assert len(names) == len(set(names))
    assert PROTOCOL_VERSION == "0.8.0"
    assert names[:3] == ["Candidate", "ScoreVector", "LedgerCommit"]
    assert names[-1] == "ProtocolCompatibilityStatus"


def test_protocol_aliases_map_to_existing_typed_models() -> None:
    assert get_protocol_definition("ArtifactRecord").model is ArtifactRef
    assert get_protocol_definition("StageResult").model is PipelineStageResult
    assert get_protocol_definition("ReviewerReport").model is StageBReviewerReport
    assert get_protocol_definition("StageCSelectionResult").model is StageCSelectionResult
    assert get_protocol_definition("ProofVerificationResult").model is ProofVerificationResult
    assert get_protocol_definition("ExperimentRunResult").model is ExperimentRunResult
    assert get_protocol_definition("ExperimentRunContract").model is ExperimentRunContract
    assert get_protocol_definition("AdapterConfig").model is AdapterConfig
    assert get_protocol_definition("RunStatusReport").model is RunStatusReport
    assert get_protocol_definition("StageRerunDecision").model is StageRerunDecision
    assert get_protocol_definition("LedgerTipValidationReport").model is LedgerTipValidationReport
    assert get_protocol_definition("LLMPromptContract").model is LLMPromptContract
    assert get_protocol_definition("LLMCandidateParseReport").model is LLMCandidateParseReport
    assert get_protocol_definition("ProseSectionContract").model is ProseSectionContract
    assert get_protocol_definition("ProsePromptContract").model is ProsePromptContract
    assert get_protocol_definition("ProseGenerationRequest").model is ProseGenerationRequest
    assert get_protocol_definition("ProseGenerationParseResult").model is ProseGenerationParseResult
    assert get_protocol_definition("ProseSafetyReport").model is ProseSafetyReport
    assert get_protocol_definition("ManuscriptDraftingPlan").model is ManuscriptDraftingPlan
    assert get_protocol_definition("SectionDraftingTask").model is SectionDraftingTask
    assert get_protocol_definition("SectionDraftingResult").model is SectionDraftingResult
    assert get_protocol_definition("CompleteMarkdownDraft").model is CompleteMarkdownDraft
    assert get_protocol_definition("ManuscriptDraftingReport").model is ManuscriptDraftingReport
    assert get_protocol_definition("ManuscriptAssemblyReport").model is ManuscriptAssemblyReport
    assert get_protocol_definition("LatexExportContract").model is LatexExportContract
    assert get_protocol_definition("LatexSourceMap").model is LatexSourceMap
    assert get_protocol_definition("LatexSafetyReport").model is LatexSafetyReport
    assert get_protocol_definition("LatexRenderResult").model is LatexRenderResult
    assert get_protocol_definition("LatexExportResult").model is LatexExportResult


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
        "NarrativeManuscriptContract",
        "PaperShapeCritique",
        "PaperShapeScore",
        "MainMessageAssessment",
        "LiteraturePositioningAssessment",
        "ModelNotationAssessment",
        "MainResultAssessment",
        "NumericalStudyAssessment",
        "EmpiricalBoundaryAssessment",
        "AppendixAllocationAssessment",
        "LLMPromptContract",
        "LLMCandidateParseReport",
        "LLMReviewerPromptContract",
        "LLMReviewerParseReport",
        "ProseSectionContract",
        "ProsePromptContract",
        "ProseGenerationRequest",
        "ProseGenerationParseResult",
        "ProseSafetyReport",
        "ManuscriptDraftingPlan",
        "SectionDraftingTask",
        "SectionDraftingResult",
        "CompleteMarkdownDraft",
        "ManuscriptDraftingReport",
        "ManuscriptAssemblyReport",
        "LatexExportContract",
        "LatexSourceMapEntry",
        "LatexSourceMap",
        "LatexSafetyReport",
        "LatexRenderConfig",
        "LatexRenderResult",
        "LatexCompileCheckReport",
        "LatexExportResult",
        "CitationRecord",
        "CitationRegistry",
        "BibliographyEntry",
        "CitationUsage",
        "CitationSafetyReport",
        "LiteratureGapStatement",
        "LiteraturePositioningContract",
        "LiteraturePositioningReport",
        "RetrievalQuery",
        "RetrievalRunReport",
        "RetrievalParseReport",
        "ProofVerificationContract",
        "ExperimentRunContract",
        "HumanReviewDecision",
        "GeneratedSectionDraft",
        "DataRequirement",
        "EvidenceType",
        "ClaimLabel",
        "AdapterBackend",
        "RetrievalBackend",
        "ReviewerBackend",
        "ProofBackend",
        "ExperimentBackend",
        "ProseBackend",
        "ExperimentKind",
        "ReleaseStatus",
        "ProtocolCompatibilityStatus",
    }

    assert expected <= names


def test_protocol_filenames_are_language_neutral_and_stable() -> None:
    assert protocol_slug("StageCSelectionResult") == "stage-c-selection-result"
    assert get_protocol_definition("PipelineRunReport").filename == (
        "pipeline-run-report.schema.json"
    )
