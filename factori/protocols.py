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
    BaselineReport,
    BridgeReport,
    Candidate,
    Claim,
    ClaimTable,
    DataRequirement,
    DiagnosticReport,
    DraftSkeleton,
    EmpiricalBoundaryAssessment,
    ExperimentKind,
    ExperimentRunContract,
    ExperimentRunResult,
    ExportReadinessReport,
    FinalAuditReport,
    FinalNucleus,
    GeneratedSectionDraft,
    HumanReviewDecision,
    HygieneRemediationPlan,
    LedgerCommit,
    LedgerSummary,
    LedgerTipValidationReport,
    LiteraturePositioningAssessment,
    LLMCandidateParseReport,
    LLMPromptContract,
    LLMReviewerParseResult,
    MainMessageAssessment,
    MainResultAssessment,
    ManuscriptPlan,
    ModelNotationAssessment,
    NarrativeManuscriptContract,
    NumericalStudyAssessment,
    OutputHygieneReport,
    PaperShapeCritique,
    PaperShapeScore,
    PaperSkeleton,
    PipelineDryRunPlan,
    PipelineRunConfig,
    PipelineRunReport,
    PipelineStageResult,
    PlannedStage,
    ProofVerificationContract,
    ProofVerificationResult,
    ProseGenerationParseResult,
    ProseGenerationRequest,
    ProsePromptContract,
    ProseSafetyReport,
    ProseSectionContract,
    ReleaseGateDecision,
    ReleaseGateStatus,
    ReplayVerificationReport,
    ReproducibilityManifest,
    RerunPolicy,
    ResearchObject,
    ResearchObjectManifest,
    ResumeValidationReport,
    RetrievalAdequacyCertificate,
    RetrievalParseReport,
    RetrievalQuery,
    RetrievalResult,
    RetrievalRunReport,
    RetrievedDocument,
    ReviewerPromptContract,
    RunStatusReport,
    ScoreVector,
    StageBReviewerReport,
    StageCheckpoint,
    StageRerunDecision,
    StageRerunStatus,
    VerificationLabel,
)
from factori.stage_c_selection import StageCSelectionResult

PROTOCOL_VERSION = "0.5.0"
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
    ProtocolDefinition("ClaimTable", ClaimTable, "Claim and evidence-link table."),
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
    "ProofBackend",
    "ProseBackend",
    "ProtocolDefinition",
    "RetrievalBackend",
    "ReviewerBackend",
    "get_protocol_definition",
    "get_protocol_definitions",
    "protocol_slug",
]
