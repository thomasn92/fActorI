"""Verification-state and fake validation result schemas."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from factori.schemas.artifacts import ArtifactRef
from factori.schemas.base import SchemaError, StrictModel
from factori.schemas.enums import (
    ArtifactType,
    BranchStatus,
    BranchVerificationType,
    VerificationLabel,
)


class FakeProofResult(StrictModel):
    """Deterministic fake proof validation result."""

    candidate_id: str = Field(min_length=1)
    proof_attempt_id: str = Field(min_length=1)
    lean_exit_code_fake: int = Field(ge=0)
    forbidden_tokens_present: bool
    proof_score: float = Field(ge=0.0, le=1.0)
    label: VerificationLabel
    evidence_artifact_type: ArtifactType
    reason: str = Field(min_length=1)
    fake: bool = True


class FakeExperimentResult(StrictModel):
    """Deterministic fake synthetic experiment result."""

    candidate_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    generator_name: str = Field(min_length=1)
    generator_parameters: dict[str, Any]
    seed: int
    metric_name: str = Field(min_length=1)
    metric_value: float
    baseline_value: float
    delta: float
    predeclared_delta: float
    lcb_95: float
    ablation_passed: bool
    baseline_strong: bool
    label: VerificationLabel
    reason: str = Field(min_length=1)
    fake: bool = True


class ProofVerificationContract(StrictModel):
    """Provider-neutral proof-verification request contract for future adapters.

    The current MVP only supports deterministic fake proof validation. This contract is exported
    for future servers and tools; it does not authorize external prover execution.
    """

    candidate_id: str = Field(min_length=1)
    backend: str = "fake"
    proof_payload: dict[str, Any] = Field(default_factory=dict)
    allow_external_calls: bool = False
    fake_default: bool = True
    is_verification_evidence: bool = False


class VerificationState(StrictModel):
    """Current verification labels and evidence artifacts."""

    labels: list[VerificationLabel] = Field(default_factory=lambda: [VerificationLabel.UNSUPPORTED])
    evidence_artifacts: list[ArtifactRef] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def evidence_must_be_ready(self) -> VerificationState:
        for artifact in self.evidence_artifacts:
            try:
                artifact.require_evidence_ready()
            except SchemaError as exc:
                raise ValueError(str(exc)) from exc
        return self


class StageCVerificationRecord(StrictModel):
    """One deterministic Stage C verification decision."""

    candidate_id: str = Field(min_length=1)
    branch_type: BranchVerificationType
    label: VerificationLabel
    status: BranchStatus
    evidence_artifacts: list[ArtifactRef] = Field(default_factory=list)
    proof_result: FakeProofResult | None = None
    experiment_result: FakeExperimentResult | None = None
    reason: str = Field(min_length=1)
    fake: bool = True

__all__ = [
    "FakeProofResult",
    "FakeExperimentResult",
    "ProofVerificationContract",
    "VerificationState",
    "StageCVerificationRecord",
]
