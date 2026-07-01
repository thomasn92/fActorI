from factori.adapters.config import AdapterConfig
from factori.protocols import (
    PROTOCOL_VERSION,
    get_protocol_definition,
    get_protocol_definitions,
    protocol_slug,
)
from factori.schemas import (
    ArtifactRef,
    ClaimEvidenceMap,
    ClaimEvidenceMapLink,
    CompleteMarkdownDraft,
    EvidenceAwareRefreshReport,
    ExperimentArtifact,
    ExperimentRunContract,
    ExperimentRunResult,
    FullPaperArtifactBundle,
    FullPaperGenerationConfig,
    FullPaperGenerationReport,
    FullPaperGenerationResult,
    FullPaperGenerationStep,
    HumanReviewArtifact,
    LatexExportContract,
    LatexExportResult,
    LatexRenderResult,
    LatexSafetyReport,
    LatexSourceMap,
    LedgerTipValidationReport,
    LLMBudgetConfig,
    LLMBudgetDecision,
    LLMBudgetUsage,
    LLMCallAccountingRecord,
    LLMCandidateParseReport,
    LLMOrchestrationConfig,
    LLMOrchestrationReport,
    LLMOrchestrationResult,
    LLMOrchestrationStep,
    LLMPromptContract,
    ManuscriptAssemblyReport,
    ManuscriptDraftingPlan,
    ManuscriptDraftingReport,
    PaperCriticFinding,
    PaperCriticReport,
    PaperReleaseReadinessPreview,
    PaperRevisionPatch,
    PaperRevisionPlan,
    PaperRevisionResult,
    PipelineStageResult,
    ProofArtifact,
    ProofVerificationResult,
    ProseGenerationParseResult,
    ProseGenerationRequest,
    ProsePromptContract,
    ProseSafetyReport,
    ProseSectionContract,
    QualityRepairReport,
    ReviewerBundleSummary,
    RevisionSafetyReport,
    RunStatusReport,
    SectionDraftingResult,
    SectionDraftingTask,
    SectionRevisionPlan,
    StageBReviewerReport,
    StageRerunDecision,
)
from factori.stage_c_selection import StageCSelectionResult


def test_protocol_registry_is_complete_unique_and_deterministic() -> None:
    first = get_protocol_definitions()
    second = get_protocol_definitions()
    names = [definition.name for definition in first]

    assert first == second
    assert len(first) == 170
    assert len(names) == len(set(names))
    assert PROTOCOL_VERSION == "0.32.0"
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
    assert get_protocol_definition("PaperCriticFinding").model is PaperCriticFinding
    assert get_protocol_definition("PaperCriticReport").model is PaperCriticReport
    assert get_protocol_definition("PaperReleaseReadinessPreview").model is (
        PaperReleaseReadinessPreview
    )
    assert get_protocol_definition("SectionRevisionPlan").model is SectionRevisionPlan
    assert get_protocol_definition("PaperRevisionPlan").model is PaperRevisionPlan
    assert get_protocol_definition("PaperRevisionPatch").model is PaperRevisionPatch
    assert get_protocol_definition("RevisionSafetyReport").model is RevisionSafetyReport
    assert get_protocol_definition("PaperRevisionResult").model is PaperRevisionResult
    assert get_protocol_definition("QualityRepairReport").model is QualityRepairReport
    assert get_protocol_definition("ReviewerBundleSummary").model is (
        ReviewerBundleSummary
    )
    assert get_protocol_definition("ClaimEvidenceMapLink").model is ClaimEvidenceMapLink
    assert get_protocol_definition("ClaimEvidenceMap").model is ClaimEvidenceMap
    assert get_protocol_definition("EvidenceAwareRefreshReport").model is (
        EvidenceAwareRefreshReport
    )
    assert get_protocol_definition("HumanReviewArtifact").model is HumanReviewArtifact
    assert get_protocol_definition("ProofArtifact").model is ProofArtifact
    assert get_protocol_definition("ExperimentArtifact").model is ExperimentArtifact
    assert get_protocol_definition("FullPaperGenerationConfig").model is (
        FullPaperGenerationConfig
    )
    assert get_protocol_definition("FullPaperGenerationStep").model is (
        FullPaperGenerationStep
    )
    assert get_protocol_definition("FullPaperArtifactBundle").model is FullPaperArtifactBundle
    assert get_protocol_definition("FullPaperGenerationReport").model is (
        FullPaperGenerationReport
    )
    assert get_protocol_definition("FullPaperGenerationResult").model is (
        FullPaperGenerationResult
    )
    assert get_protocol_definition("LLMBudgetConfig").model is LLMBudgetConfig
    assert get_protocol_definition("LLMBudgetUsage").model is LLMBudgetUsage
    assert get_protocol_definition("LLMBudgetDecision").model is LLMBudgetDecision
    assert get_protocol_definition("LLMCallAccountingRecord").model is (
        LLMCallAccountingRecord
    )
    assert get_protocol_definition("LLMOrchestrationConfig").model is (
        LLMOrchestrationConfig
    )
    assert get_protocol_definition("LLMOrchestrationStep").model is (
        LLMOrchestrationStep
    )
    assert get_protocol_definition("LLMOrchestrationReport").model is (
        LLMOrchestrationReport
    )
    assert get_protocol_definition("LLMOrchestrationResult").model is (
        LLMOrchestrationResult
    )


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
        "PaperCriticFinding",
        "PaperCriticReport",
        "PaperReleaseReadinessPreview",
        "SectionRevisionPlan",
        "PaperRevisionPlan",
        "PaperRevisionPatch",
        "RevisionSafetyReport",
        "PaperRevisionResult",
        "FullPaperGenerationConfig",
        "ReviewerBundleSummary",
        "ClaimEvidenceMapLink",
        "ClaimEvidenceMap",
        "EvidenceAwareRefreshReport",
        "HumanReviewArtifact",
        "ProofArtifact",
        "ExperimentArtifact",
        "FullPaperGenerationStep",
        "FullPaperArtifactBundle",
        "FullPaperGenerationReport",
        "FullPaperGenerationResult",
        "LLMBudgetConfig",
        "LLMBudgetUsage",
        "LLMBudgetDecision",
        "LLMCallAccountingRecord",
        "LLMRunSafetyReport",
        "LLMOrchestrationConfig",
        "LLMOrchestrationStep",
        "LLMOrchestrationReport",
        "LLMOrchestrationResult",
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
        "RetrievalQualityReport",
        "SourceRelevanceAdjudication",
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
        "FullPaperGenerationStatus",
        "FullPaperGenerationStepStatus",
        "LLMOrchestrationStatus",
        "LLMOrchestrationStepStatus",
        "LLMBudgetDecisionStatus",
        "LLMCallStatus",
        "ReleaseStatus",
        "ProtocolCompatibilityStatus",
    }

    assert expected <= names


def test_protocol_filenames_are_language_neutral_and_stable() -> None:
    assert protocol_slug("StageCSelectionResult") == "stage-c-selection-result"
    assert get_protocol_definition("PipelineRunReport").filename == (
        "pipeline-run-report.schema.json"
    )
