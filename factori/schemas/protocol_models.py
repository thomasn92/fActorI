"""Protocol-facing schema re-exports for cross-language contracts."""

from __future__ import annotations

from factori.schemas.artifacts import (
    ArtifactManifest,
    ArtifactRef,
    LedgerCommit,
)
from factori.schemas.audit import (
    DiagnosticReport,
    ExportReadinessReport,
    FinalAuditReport,
    ReleaseGateDecision,
    ReplayVerificationReport,
    ResearchObject,
)
from factori.schemas.candidates import (
    Candidate,
    ScoreVector,
)
from factori.schemas.manuscript import (
    Claim,
    ClaimTable,
    DraftSkeleton,
    FinalNucleus,
    ManuscriptPlan,
    NarrativeManuscriptContract,
    PaperShapeCritique,
    PaperShapeScore,
    PaperSkeleton,
)
from factori.schemas.pipeline import (
    HygieneRemediationPlan,
    OutputHygieneReport,
    PipelineDryRunPlan,
    PipelineRunConfig,
    PipelineRunReport,
    PipelineStageResult,
)
from factori.schemas.retrieval import (
    RetrievalAdequacyCertificate,
    RetrievalResult,
    RetrievedDocument,
)
from factori.schemas.stages import (
    BaselineReport,
    BridgeReport,
    StageBReviewerReport,
)
from factori.schemas.verification import (
    ExperimentRunContract,
    ExperimentRunResult,
    FakeExperimentResult,
    FakeProofResult,
    ProofVerificationResult,
)

__all__ = [
    "Candidate",
    "ScoreVector",
    "LedgerCommit",
    "ArtifactRef",
    "ArtifactManifest",
    "PipelineStageResult",
    "RetrievalResult",
    "RetrievedDocument",
    "RetrievalAdequacyCertificate",
    "StageBReviewerReport",
    "BridgeReport",
    "BaselineReport",
    "FakeProofResult",
    "ProofVerificationResult",
    "FakeExperimentResult",
    "ExperimentRunContract",
    "ExperimentRunResult",
    "Claim",
    "ClaimTable",
    "FinalNucleus",
    "ManuscriptPlan",
    "DraftSkeleton",
    "ResearchObject",
    "PaperSkeleton",
    "FinalAuditReport",
    "ReleaseGateDecision",
    "ExportReadinessReport",
    "ReplayVerificationReport",
    "DiagnosticReport",
    "OutputHygieneReport",
    "HygieneRemediationPlan",
    "PipelineRunConfig",
    "PipelineRunReport",
    "PipelineDryRunPlan",
    "NarrativeManuscriptContract",
    "PaperShapeCritique",
    "PaperShapeScore",
]
