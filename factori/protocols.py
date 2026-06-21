"""Language-neutral protocol registry backed by existing typed models."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from factori.adapters.config import AdapterConfig
from factori.schemas import (
    ArtifactRef,
    BaselineReport,
    BridgeReport,
    Candidate,
    Claim,
    ClaimTable,
    DiagnosticReport,
    DraftSkeleton,
    ExportReadinessReport,
    FakeExperimentResult,
    FakeProofResult,
    FinalAuditReport,
    FinalNucleus,
    HygieneRemediationPlan,
    LedgerCommit,
    ManuscriptPlan,
    OutputHygieneReport,
    PaperSkeleton,
    PipelineDryRunPlan,
    PipelineRunConfig,
    PipelineRunReport,
    PipelineStageResult,
    ReleaseGateDecision,
    ReplayVerificationReport,
    ResearchObject,
    RetrievalAdequacyCertificate,
    RetrievalResult,
    RetrievedDocument,
    ScoreVector,
    StageBReviewerReport,
)
from factori.stage_c_selection import StageCSelectionResult

PROTOCOL_VERSION = "0.1.0"
SCHEMA_FORMAT = "json-schema"
PROTOCOL_SOURCE = "factori-pydantic-models"
PROTOCOL_GENERATOR = "factori export-protocols"


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
    ProtocolDefinition("StageResult", PipelineStageResult, "One pipeline-stage execution result."),
    ProtocolDefinition("RetrievalResult", RetrievalResult, "Normalized retrieval source result."),
    ProtocolDefinition("RetrievedDocument", RetrievedDocument, "Fetched source document metadata."),
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
    ProtocolDefinition("BridgeReport", BridgeReport, "Stage B bridge validation result."),
    ProtocolDefinition("BaselineReport", BaselineReport, "Stage B baseline validation result."),
    ProtocolDefinition(
        "StageCSelectionResult",
        StageCSelectionResult,
        "Typed Stage C candidate-selection result.",
    ),
    ProtocolDefinition(
        "ProofVerificationResult",
        FakeProofResult,
        "Current fake proof-result shape; fake is explicit.",
    ),
    ProtocolDefinition(
        "ExperimentRunResult",
        FakeExperimentResult,
        "Current fake synthetic-experiment result shape.",
    ),
    ProtocolDefinition("Claim", Claim, "One label-preserving research claim."),
    ProtocolDefinition("ClaimTable", ClaimTable, "Claim and evidence-link table."),
    ProtocolDefinition("FinalNucleus", FinalNucleus, "Selected abstraction or candidate nucleus."),
    ProtocolDefinition("ManuscriptPlan", ManuscriptPlan, "Structured manuscript plan."),
    ProtocolDefinition("DraftSkeleton", DraftSkeleton, "Deterministic draft scaffold."),
    ProtocolDefinition("ResearchObject", ResearchObject, "Packaged reproducible research object."),
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
    ProtocolDefinition("AdapterConfig", AdapterConfig, "Fake-default adapter configuration."),
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
    "ProtocolDefinition",
    "get_protocol_definition",
    "get_protocol_definitions",
    "protocol_slug",
]
