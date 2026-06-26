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
    DataRequirement,
    ExperimentKind,
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
    """Provider-neutral proof-verification request contract.

    Fake proof validation remains the default. A real backend may consume this contract only when
    external tool execution is explicitly enabled by the owning command configuration.
    """

    candidate_id: str = Field(min_length=1)
    claim_id: str = "claim-unspecified"
    claim_text: str = ""
    proof_language: str = "Lean"
    proof_payload_path: str | None = None
    proof_payload_text: str | None = None
    proof_payload: dict[str, Any] = Field(default_factory=dict)
    allowed_imports: list[str] = Field(default_factory=list)
    forbidden_tokens: list[str] = Field(
        default_factory=lambda: ["sorry", "admit", "axiom", "unsafe"]
    )
    timeout_seconds: int = Field(default=10, ge=1, le=60)
    expected_output_type: str = "proof_transcript"
    backend: str = "fake"
    tool_name: str | None = None
    allow_external_calls: bool = False
    allow_external_tools: bool = False
    fake_default: bool = True
    is_verification_evidence: bool = False


class ProofVerificationResult(StrictModel):
    """Provider-neutral proof-verification result for a gated real proof backend."""

    candidate_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    backend: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    proof_language: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    tool_version: str | None = None
    exit_code: int
    stdout_hash: str
    stderr_hash: str
    proof_payload_hash: str
    forbidden_tokens_present: bool
    verified: bool
    label: VerificationLabel
    reason: str = Field(min_length=1)
    elapsed_ms: int | None = Field(default=None, ge=0)
    raw_trace_artifact_id: str | None = None
    safety_report_artifact_id: str | None = None
    fake: bool = False


class ExperimentRunContract(StrictModel):
    """Provider-neutral synthetic experiment request contract.

    Real/local execution is disabled by default. This contract is restricted to NoData and
    SyntheticOnly regimes in the MVP and cannot request empirical or public-download data.
    """

    candidate_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    experiment_kind: ExperimentKind = ExperimentKind.SYNTHETIC_SIMULATION
    data_regime: DataRequirement = DataRequirement.SYNTHETIC_ONLY
    synthetic_data_spec: dict[str, Any] = Field(default_factory=dict)
    model_spec: dict[str, Any] = Field(default_factory=dict)
    algorithm_spec: dict[str, Any] = Field(default_factory=dict)
    metrics: list[str] = Field(default_factory=list)
    acceptance_criteria: dict[str, Any] = Field(default_factory=dict)
    random_seed: int = 0
    replications: int = Field(default=5, ge=1, le=100)
    timeout_seconds: int = Field(default=10, ge=1, le=60)
    backend: str = "fake"
    runner_name: str | None = None
    forbidden_external_inputs: list[str] = Field(default_factory=list)
    expected_output_type: str = "synthetic_experiment_result"
    allow_external_calls: bool = False
    allow_external_tools: bool = False
    fake_default: bool = True
    is_verification_evidence: bool = False


class ExperimentRunResult(StrictModel):
    """Provider-neutral synthetic experiment result for gated local runners."""

    candidate_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    backend: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    experiment_kind: ExperimentKind
    data_regime: DataRequirement
    runner_name: str = Field(min_length=1)
    runner_version: str | None = None
    exit_code: int
    stdout_hash: str
    stderr_hash: str
    input_spec_hash: str
    output_payload_hash: str
    metrics: dict[str, float] = Field(default_factory=dict)
    acceptance_criteria: dict[str, Any] = Field(default_factory=dict)
    passed: bool
    label: VerificationLabel
    reason: str = Field(min_length=1)
    elapsed_ms: int | None = Field(default=None, ge=0)
    raw_trace_artifact_id: str | None = None
    safety_report_artifact_id: str | None = None
    fake: bool = False


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
    proof_result: FakeProofResult | ProofVerificationResult | None = None
    experiment_result: FakeExperimentResult | ExperimentRunResult | None = None
    reason: str = Field(min_length=1)
    fake: bool = True

__all__ = [
    "FakeProofResult",
    "FakeExperimentResult",
    "ProofVerificationContract",
    "ProofVerificationResult",
    "ExperimentRunContract",
    "ExperimentRunResult",
    "VerificationState",
    "StageCVerificationRecord",
]
