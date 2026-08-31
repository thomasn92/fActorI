"""Versioned request and response envelopes for the future Rust kernel."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, RootModel, field_validator, model_validator

from factori.schemas.artifacts import ArtifactRef, LedgerCommit
from factori.schemas.base import HASH_RE, StrictModel
from factori.schemas.enums import VerificationLabel


class KernelMode(StrEnum):
    """Compatibility mode requested for one read-only kernel operation."""

    DEVELOPMENT_COMPATIBILITY = "DevelopmentCompatibility"
    STRICT_PRODUCTION = "StrictProduction"


class KernelOperation(StrEnum):
    """Read-only operations exposed by the initial kernel boundary."""

    PROTOCOL_VALIDATE = "protocol.validate"
    HASH_CANONICAL_JSON = "hash.canonical_json"
    ARTIFACT_VERIFY = "artifact.verify"
    LEDGER_VERIFY = "ledger.verify"
    EVIDENCE_CLASSIFY = "evidence.classify"
    EVIDENCE_VALIDATE_BUNDLE = "evidence.validate_bundle"
    CLAIM_RESOLVE = "claim.resolve"
    CHECKPOINT_VERIFY = "checkpoint.verify"
    REPLAY_VERIFY_CORE = "replay.verify_core"


class KernelResponseStatus(StrEnum):
    """Transport-level result status; this is not scientific evidence."""

    ACCEPTED = "accepted"
    REJECTED = "rejected"
    ERROR = "error"


class KernelCanonicalJsonPayload(StrictModel):
    """Payload for the canonical JSON operation."""

    value: Any


class KernelProtocolValidatePayload(StrictModel):
    """Payload for validating one supported kernel protocol instance."""

    protocol_name: Literal["KernelRequestEnvelope", "KernelResponseEnvelope"]
    instance: dict[str, Any]


class KernelLedgerVerifyPayload(StrictModel):
    """Payload for read-only verification of one run's ordered ledger commits."""

    run_id: str = Field(min_length=1)
    commits: list[LedgerCommit]


class KernelArtifactVerifyPayload(StrictModel):
    """Payload for read-only verification of one persisted artifact."""

    run_id: str = Field(min_length=1)
    artifact: ArtifactRef


class KernelEvidenceClassifyPayload(StrictModel):
    """Payload for read-only classification of one persisted artifact."""

    run_id: str = Field(min_length=1)
    artifact: ArtifactRef


_KERNEL_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._-]*$"

KernelAutonomousCheckpointStage = Literal[
    "base_generation",
    "autonomous_loop",
    "final_manuscript_regeneration",
    "final_release_bundle_assembly",
    "final_bundle_verification",
    "handoff",
]


class KernelLeanEvidenceBundle(StrictModel):
    """Exact persisted artifact members of one real Lean Stage C bundle."""

    kind: Literal["LeanProof"]
    contract_artifact_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    payload_artifact_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    trace_artifact_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    result_artifact_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    safety_artifact_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)

    @model_validator(mode="after")
    def require_distinct_members(self) -> KernelLeanEvidenceBundle:
        members = (
            self.contract_artifact_id,
            self.payload_artifact_id,
            self.trace_artifact_id,
            self.result_artifact_id,
            self.safety_artifact_id,
        )
        if len(set(members)) != len(members):
            raise ValueError("Lean evidence bundle members must be distinct")
        return self


class KernelSyntheticEvidenceBundle(StrictModel):
    """Exact persisted artifact members of one local synthetic Stage C bundle."""

    kind: Literal["SyntheticExperiment"]
    contract_artifact_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    input_artifact_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    trace_artifact_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    output_artifact_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    result_artifact_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    safety_artifact_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)

    @model_validator(mode="after")
    def require_distinct_members(self) -> KernelSyntheticEvidenceBundle:
        members = (
            self.contract_artifact_id,
            self.input_artifact_id,
            self.trace_artifact_id,
            self.output_artifact_id,
            self.result_artifact_id,
            self.safety_artifact_id,
        )
        if len(set(members)) != len(members):
            raise ValueError("synthetic evidence bundle members must be distinct")
        return self


KernelEvidenceBundle = Annotated[
    KernelLeanEvidenceBundle | KernelSyntheticEvidenceBundle,
    Field(discriminator="kind"),
]


class KernelProofPayload(StrictModel):
    """Closed persisted payload member of a real Lean Stage C bundle."""

    candidate_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    proof_language: str = Field(min_length=1)
    proof_payload_text: str = Field(min_length=1)
    is_verification_evidence: bool


class KernelProofTrace(StrictModel):
    """Closed persisted execution trace member of a real Lean bundle."""

    backend: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    exit_code: int
    stdout: str
    stderr: str
    elapsed_ms: int = Field(ge=0)
    tool_version: str | None = None
    fake: bool
    is_verification_evidence: bool


class KernelProofSafetyReport(StrictModel):
    """Closed persisted safety member of a real Lean Stage C bundle."""

    candidate_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    contract_valid: bool
    contract_reasons: list[str]
    result_valid: bool
    result_reasons: list[str]
    is_verification_evidence: bool
    fake: bool


class KernelSyntheticExperimentInput(StrictModel):
    """Closed persisted input member of a local synthetic Stage C bundle."""

    candidate_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    experiment_kind: str = Field(min_length=1)
    data_regime: str = Field(min_length=1)
    synthetic_data_spec: dict[str, Any]
    model_spec: dict[str, Any]
    algorithm_spec: dict[str, Any]
    metrics: list[str]
    acceptance_criteria: dict[str, Any]
    random_seed: int
    replications: int = Field(ge=1)


class KernelSyntheticExperimentTrace(StrictModel):
    """Closed persisted execution trace member of a synthetic bundle."""

    backend: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    runner_name: str = Field(min_length=1)
    exit_code: int
    stdout: str
    stderr: str
    elapsed_ms: int = Field(ge=0)
    runner_version: str | None = None
    fake: bool
    is_verification_evidence: bool


class KernelSyntheticExperimentOutput(StrictModel):
    """Closed persisted output member of a local synthetic bundle."""

    metrics: dict[str, float]
    synthetic_only: bool | None = None


class KernelSyntheticExperimentSafetyReport(StrictModel):
    """Closed persisted safety member of a synthetic Stage C bundle."""

    candidate_id: str = Field(min_length=1)
    claim_id: str = Field(min_length=1)
    contract_valid: bool
    contract_reasons: list[str]
    result_valid: bool
    result_reasons: list[str]
    is_verification_evidence: bool
    fake: bool


class KernelEvidenceValidateBundlePayload(StrictModel):
    """Locator-only request for strict persisted Stage C bundle validation."""

    run_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    candidate_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    claim_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    producing_commit_hash: str = Field(pattern=HASH_RE.pattern)
    bundle: KernelEvidenceBundle


class KernelClaimEvidenceLocator(StrictModel):
    """Locator for the persisted bundle revalidated during claim resolution."""

    producing_commit_hash: str = Field(pattern=HASH_RE.pattern)
    bundle: KernelEvidenceBundle


class KernelClaimTableLocator(StrictModel):
    """Locator for the persisted claim-table record resolved by the kernel."""

    artifact_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    producing_commit_hash: str = Field(pattern=HASH_RE.pattern)


class KernelClaimResolvePayload(StrictModel):
    """Locator-only claim admissibility request with no authority-bearing input."""

    run_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    claim_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    claim_table: KernelClaimTableLocator
    evidence: KernelClaimEvidenceLocator | None = None


class KernelCheckpointIndexLocator(StrictModel):
    """Locator for the latest persisted autonomous checkpoint index."""

    artifact_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    producing_commit_hash: str = Field(pattern=HASH_RE.pattern)


class KernelCheckpointVerifyPayload(StrictModel):
    """Locator-only autonomous checkpoint-chain verification request."""

    run_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    index: KernelCheckpointIndexLocator


class KernelReplayVerifyCorePayload(StrictModel):
    """Locator-only request for persisted mechanical replay-core verification."""

    run_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    ledger_tip_hash: str = Field(pattern=HASH_RE.pattern)


class KernelRequestFields(StrictModel):
    """Fields common to every kernel request variant."""

    protocol_version: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    mode: KernelMode


class KernelCanonicalJsonRequest(KernelRequestFields):
    """Typed request for Python-compatible canonical JSON and SHA-256."""

    operation: Literal[KernelOperation.HASH_CANONICAL_JSON]
    payload: KernelCanonicalJsonPayload


class KernelProtocolValidateRequest(KernelRequestFields):
    """Typed request for validating one selected kernel protocol."""

    operation: Literal[KernelOperation.PROTOCOL_VALIDATE]
    payload: KernelProtocolValidatePayload


class KernelLedgerVerifyRequest(KernelRequestFields):
    """Typed request for Python/Rust ledger hash-chain parity."""

    operation: Literal[KernelOperation.LEDGER_VERIFY]
    payload: KernelLedgerVerifyPayload


class KernelArtifactVerifyRequest(KernelRequestFields):
    """Typed request for Python/Rust artifact integrity parity."""

    operation: Literal[KernelOperation.ARTIFACT_VERIFY]
    payload: KernelArtifactVerifyPayload


class KernelEvidenceClassifyRequest(KernelRequestFields):
    """Typed request for non-authoritative artifact classification."""

    operation: Literal[KernelOperation.EVIDENCE_CLASSIFY]
    payload: KernelEvidenceClassifyPayload


class KernelEvidenceValidateBundleRequest(KernelRequestFields):
    """Typed request for read-only strict evidence-bundle validation."""

    operation: Literal[KernelOperation.EVIDENCE_VALIDATE_BUNDLE]
    payload: KernelEvidenceValidateBundlePayload


class KernelClaimResolveRequest(KernelRequestFields):
    """Typed request for read-only claim admissibility resolution."""

    operation: Literal[KernelOperation.CLAIM_RESOLVE]
    payload: KernelClaimResolvePayload


class KernelCheckpointVerifyRequest(KernelRequestFields):
    """Typed request for autonomous checkpoint-chain verification."""

    operation: Literal[KernelOperation.CHECKPOINT_VERIFY]
    payload: KernelCheckpointVerifyPayload


class KernelReplayVerifyCoreRequest(KernelRequestFields):
    """Typed request for bounded persisted replay-core verification."""

    operation: Literal[KernelOperation.REPLAY_VERIFY_CORE]
    payload: KernelReplayVerifyCorePayload


KernelRequestVariant = Annotated[
    KernelCanonicalJsonRequest
    | KernelProtocolValidateRequest
    | KernelArtifactVerifyRequest
    | KernelLedgerVerifyRequest
    | KernelEvidenceClassifyRequest
    | KernelEvidenceValidateBundleRequest
    | KernelClaimResolveRequest
    | KernelCheckpointVerifyRequest
    | KernelReplayVerifyCoreRequest,
    Field(discriminator="operation"),
]


class KernelRequestEnvelope(RootModel[KernelRequestVariant]):
    """Discriminated, language-neutral request envelope for the kernel."""

    model_config = ConfigDict(frozen=True)
    root: KernelRequestVariant

    @property
    def protocol_version(self) -> str:
        return self.root.protocol_version

    @property
    def request_id(self) -> str:
        return self.root.request_id

    @property
    def operation(self) -> KernelOperation:
        return KernelOperation(self.root.operation)

    @property
    def mode(self) -> KernelMode:
        return self.root.mode

    @property
    def payload(
        self,
    ) -> (
        KernelCanonicalJsonPayload
        | KernelProtocolValidatePayload
        | KernelArtifactVerifyPayload
        | KernelLedgerVerifyPayload
        | KernelEvidenceClassifyPayload
        | KernelEvidenceValidateBundlePayload
        | KernelClaimResolvePayload
        | KernelCheckpointVerifyPayload
        | KernelReplayVerifyCorePayload
    ):
        return self.root.payload


class KernelDiagnostic(StrictModel):
    """Bounded stable diagnostic returned by a kernel operation."""

    code: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_]*$")
    message: str = Field(min_length=1)
    path: str | None = None


class KernelEmptyResult(StrictModel):
    """Empty result used by rejected kernel operations."""


class KernelCanonicalJsonResult(StrictModel):
    """Result returned by canonical JSON hashing."""

    canonical_json: str
    sha256: str = Field(pattern=HASH_RE.pattern)


class KernelProtocolValidateResult(StrictModel):
    """Result returned by protocol validation."""

    valid: Literal[True]
    protocol_name: Literal["KernelRequestEnvelope", "KernelResponseEnvelope"]

    @field_validator("valid", mode="before")
    @classmethod
    def require_strict_true(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("valid must be the boolean true")
        return value


class KernelLedgerVerifyResult(StrictModel):
    """Result returned by ledger verification."""

    valid: Literal[True]
    run_id: str = Field(min_length=1)
    commit_count: int = Field(ge=0, strict=True)
    root_hash: str | None = Field(default=None, pattern=HASH_RE.pattern)
    tip_hash: str | None = Field(default=None, pattern=HASH_RE.pattern)

    @field_validator("valid", mode="before")
    @classmethod
    def require_strict_true(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("valid must be the boolean true")
        return value


class KernelArtifactVerifyResult(StrictModel):
    """Result returned by artifact integrity verification."""

    valid: Literal[True]
    run_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    content_hash: str = Field(pattern=HASH_RE.pattern)
    producing_commit_hash: str | None = Field(default=None, pattern=HASH_RE.pattern)

    @field_validator("valid", mode="before")
    @classmethod
    def require_strict_true(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("valid must be the boolean true")
        return value


class KernelEvidenceAuthorityClass(StrEnum):
    """Non-authoritative classification returned by evidence.classify."""

    CONTEXT = "Context"
    PRESENTATION = "Presentation"
    CAPABILITY_CANDIDATE = "CapabilityCandidate"


class KernelEvidenceCandidateKind(StrEnum):
    """Candidate family eligible for later strict evidence validation."""

    LEAN_PROOF = "LeanProof"
    SYNTHETIC_EXPERIMENT = "SyntheticExperiment"


class KernelEvidenceClassifyResult(StrictModel):
    """Classification result that cannot grant evidence authority."""

    run_id: str = Field(min_length=1)
    artifact_id: str = Field(min_length=1)
    authority_class: KernelEvidenceAuthorityClass
    candidate_kind: KernelEvidenceCandidateKind | None = None
    compatibility_only: bool = Field(strict=True)
    authority_granted: Literal[False]

    @field_validator("authority_granted", mode="before")
    @classmethod
    def require_strict_false(cls, value: Any) -> Any:
        if value is not False:
            raise ValueError("authority_granted must be the boolean false")
        return value

    @model_validator(mode="after")
    def validate_classification_shape(self) -> KernelEvidenceClassifyResult:
        """Keep candidate kind and compatibility flags coupled to the class."""

        is_candidate = self.authority_class == KernelEvidenceAuthorityClass.CAPABILITY_CANDIDATE
        if is_candidate != (self.candidate_kind is not None):
            raise ValueError(
                "CapabilityCandidate requires candidate_kind and other classes forbid it"
            )
        if self.compatibility_only and not is_candidate:
            raise ValueError("compatibility_only is valid only for capability candidates")
        return self


class KernelEvidenceValidateBundleResult(StrictModel):
    """Non-authoritative result of strict persisted bundle validation."""

    run_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    candidate_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    claim_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    bundle_kind: Literal["LeanProof", "SyntheticExperiment"]
    producing_commit_hash: str = Field(pattern=HASH_RE.pattern)
    validated_artifact_ids: list[str] = Field(min_length=1)
    bundle_valid: Literal[True]
    authority_granted: Literal[False]

    @field_validator("bundle_valid", mode="before")
    @classmethod
    def require_strict_bundle_valid(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("bundle_valid must be the boolean true")
        return value

    @field_validator("authority_granted", mode="before")
    @classmethod
    def require_strict_authority_false(cls, value: Any) -> Any:
        if value is not False:
            raise ValueError("authority_granted must be the boolean false")
        return value

    @model_validator(mode="after")
    def validate_artifact_order(self) -> KernelEvidenceValidateBundleResult:
        if any(
            re.fullmatch(_KERNEL_IDENTIFIER_PATTERN, artifact_id) is None
            for artifact_id in self.validated_artifact_ids
        ):
            raise ValueError("validated artifact ids use invalid identifier grammar")
        expected_count = 5 if self.bundle_kind == "LeanProof" else 6
        if len(self.validated_artifact_ids) != expected_count:
            raise ValueError("validated artifact ids do not match bundle kind")
        if len(set(self.validated_artifact_ids)) != expected_count:
            raise ValueError("validated artifact ids must be distinct")
        return self


class KernelClaimResolveResult(StrictModel):
    """Non-authoritative claim admissibility result."""

    run_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    candidate_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    claim_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    claim_text_hash: str = Field(pattern=HASH_RE.pattern)
    claim_label: VerificationLabel
    allowed_in_main_text: bool = Field(strict=True)
    allowed_section: str = Field(min_length=1)
    claim_record_validated: Literal[True]
    admissible: bool = Field(strict=True)
    evidence_bundle_validated: bool = Field(strict=True)
    authority_granted: Literal[False]

    @field_validator("claim_record_validated", mode="before")
    @classmethod
    def require_strict_claim_record_true(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("claim_record_validated must be the boolean true")
        return value

    @field_validator("authority_granted", mode="before")
    @classmethod
    def require_strict_authority_false(cls, value: Any) -> Any:
        if value is not False:
            raise ValueError("authority_granted must be the boolean false")
        return value


KernelAutonomousCheckpointVerificationStatus = Literal[
    "verified",
    "verified_with_warnings",
    "failed",
    "unverified",
]


class KernelAutonomousPaperCheckpoint(StrictModel):
    """Closed persisted autonomous checkpoint record for Rust verification."""

    run_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    controller_run_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    stage_name: KernelAutonomousCheckpointStage
    stage_status: str = Field(min_length=1)
    stage_artifact_paths: list[str]
    stage_started_at: str = Field(min_length=1)
    stage_completed_at: str = Field(min_length=1)
    protocol_version: str = Field(min_length=1)
    ledger_tip_hash_optional: str | None = Field(default=None, pattern=HASH_RE.pattern)
    checkpoint_hash: str = Field(pattern=HASH_RE.pattern)
    input_hashes: dict[str, str]
    output_hashes: dict[str, str]
    safety_gate_status: Literal["passed", "passed_with_warnings", "failed"]
    release_status_optional: str | None = None
    publication_ready: bool = Field(strict=True)
    verified_for_resume: bool = Field(strict=True)
    verification_status: KernelAutonomousCheckpointVerificationStatus
    verification_errors: list[str]
    creates_scientific_validation: bool = Field(strict=True)
    implies_publication_readiness: bool = Field(strict=True)
    is_verification_evidence: bool = Field(strict=True)


class KernelAutonomousPaperCheckpointIndex(StrictModel):
    """Closed persisted autonomous checkpoint index for Rust verification."""

    run_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    latest_controller_run_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    checkpoint_count: int = Field(ge=1)
    latest_completed_stage: str | None = None
    checkpoints: list[str]
    resume_allowed: bool = Field(strict=True)
    resume_blockers: list[str]
    publication_ready: bool = Field(strict=True)
    creates_scientific_validation: bool = Field(strict=True)
    implies_publication_readiness: bool = Field(strict=True)
    is_verification_evidence: bool = Field(strict=True)


class KernelCheckpointVerifyResult(StrictModel):
    """Non-authoritative autonomous checkpoint integrity and resume result."""

    run_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    checkpoint_index_artifact_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    checkpoint_index_producing_commit_hash: str = Field(pattern=HASH_RE.pattern)
    checkpoint_count: int = Field(ge=1)
    validated_checkpoint_hashes: list[str] = Field(min_length=1)
    latest_checkpoint_hash: str = Field(pattern=HASH_RE.pattern)
    latest_completed_stage: str = Field(min_length=1)
    validated_output_count: int = Field(ge=0)
    checkpoint_chain_valid: Literal[True]
    resume_allowed: bool = Field(strict=True)
    authority_granted: Literal[False]

    @field_validator("checkpoint_chain_valid", mode="before")
    @classmethod
    def require_strict_checkpoint_chain_true(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("checkpoint_chain_valid must be the boolean true")
        return value

    @field_validator("authority_granted", mode="before")
    @classmethod
    def require_strict_checkpoint_authority_false(cls, value: Any) -> Any:
        if value is not False:
            raise ValueError("authority_granted must be the boolean false")
        return value

    @model_validator(mode="after")
    def validate_checkpoint_result_shape(self) -> KernelCheckpointVerifyResult:
        if len(self.validated_checkpoint_hashes) != self.checkpoint_count:
            raise ValueError("validated checkpoint hashes must match checkpoint count")
        if any(not HASH_RE.fullmatch(item) for item in self.validated_checkpoint_hashes):
            raise ValueError("validated checkpoint hashes must be lowercase SHA-256 hashes")
        if len(set(self.validated_checkpoint_hashes)) != self.checkpoint_count:
            raise ValueError("validated checkpoint hashes must be distinct")
        if self.latest_checkpoint_hash != self.validated_checkpoint_hashes[-1]:
            raise ValueError("latest checkpoint hash must be the final validated hash")
        return self


class KernelReplayVerifyCoreResult(StrictModel):
    """Non-authoritative persisted replay-core integrity result."""

    run_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    ledger_tip_hash: str = Field(pattern=HASH_RE.pattern)
    ledger_commit_count: int = Field(ge=1)
    ledger_artifact_count: int = Field(ge=0)
    ledger_artifact_inventory_hash: str = Field(pattern=HASH_RE.pattern)
    required_outputs_checked: Literal[11]
    manifest_artifact_id: Literal["artifact-manifest"]
    manifest_producing_commit_hash: str = Field(pattern=HASH_RE.pattern)
    manifest_entry_count: int = Field(ge=1)
    manifest_inventory_hash: str = Field(pattern=HASH_RE.pattern)
    claims_checked: int = Field(ge=0)
    claim_evidence_links_checked: int = Field(ge=0)
    core_replay_valid: Literal[True]
    ledger_snapshot_stable: Literal[True]
    authority_boundary_valid: Literal[True]
    authority_granted: Literal[False]

    @field_validator(
        "core_replay_valid", "ledger_snapshot_stable", "authority_boundary_valid", mode="before"
    )
    @classmethod
    def require_strict_true(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("replay core integrity flags must be the boolean true")
        return value

    @field_validator("authority_granted", mode="before")
    @classmethod
    def require_strict_authority_false(cls, value: Any) -> Any:
        if value is not False:
            raise ValueError("authority_granted must be the boolean false")
        return value


KernelResult = Annotated[
    KernelEmptyResult
    | KernelCanonicalJsonResult
    | KernelProtocolValidateResult
    | KernelArtifactVerifyResult
    | KernelLedgerVerifyResult
    | KernelEvidenceClassifyResult
    | KernelEvidenceValidateBundleResult
    | KernelClaimResolveResult
    | KernelCheckpointVerifyResult
    | KernelReplayVerifyCoreResult,
    Field(union_mode="left_to_right"),
]


class KernelResponseEnvelope(StrictModel):
    """Strict outer response envelope returned by a language-neutral kernel."""

    protocol_version: str = Field(min_length=1)
    kernel_version: str = Field(min_length=1)
    request_id: str = Field(min_length=1)
    operation: KernelOperation
    mode: KernelMode
    status: KernelResponseStatus
    result: KernelResult
    diagnostics: list[KernelDiagnostic]
    mutation_performed: bool = Field(strict=True)

    @model_validator(mode="after")
    def validate_operation_result_contract(self) -> KernelResponseEnvelope:
        """Keep response status and result types coupled to the operation."""
        if self.mutation_performed:
            raise ValueError("kernel responses must not report mutations")
        if self.status != KernelResponseStatus.ACCEPTED:
            if not isinstance(self.result, KernelEmptyResult):
                raise ValueError("rejected and error responses must have an empty result")
            return self
        expected_result = {
            KernelOperation.HASH_CANONICAL_JSON: KernelCanonicalJsonResult,
            KernelOperation.PROTOCOL_VALIDATE: KernelProtocolValidateResult,
            KernelOperation.ARTIFACT_VERIFY: KernelArtifactVerifyResult,
            KernelOperation.LEDGER_VERIFY: KernelLedgerVerifyResult,
            KernelOperation.EVIDENCE_CLASSIFY: KernelEvidenceClassifyResult,
            KernelOperation.EVIDENCE_VALIDATE_BUNDLE: KernelEvidenceValidateBundleResult,
            KernelOperation.CLAIM_RESOLVE: KernelClaimResolveResult,
            KernelOperation.CHECKPOINT_VERIFY: KernelCheckpointVerifyResult,
            KernelOperation.REPLAY_VERIFY_CORE: KernelReplayVerifyCoreResult,
        }[self.operation]
        if not isinstance(self.result, expected_result):
            raise ValueError(
                f"accepted {self.operation.value} responses have an invalid result type"
            )
        return self


__all__ = [
    "KernelCanonicalJsonPayload",
    "KernelCanonicalJsonResult",
    "KernelCanonicalJsonRequest",
    "KernelDiagnostic",
    "KernelEmptyResult",
    "KernelArtifactVerifyPayload",
    "KernelArtifactVerifyRequest",
    "KernelArtifactVerifyResult",
    "KernelEvidenceAuthorityClass",
    "KernelEvidenceCandidateKind",
    "KernelEvidenceClassifyPayload",
    "KernelEvidenceClassifyRequest",
    "KernelEvidenceClassifyResult",
    "KernelEvidenceBundle",
    "KernelProofPayload",
    "KernelProofSafetyReport",
    "KernelProofTrace",
    "KernelSyntheticExperimentInput",
    "KernelSyntheticExperimentOutput",
    "KernelSyntheticExperimentSafetyReport",
    "KernelSyntheticExperimentTrace",
    "KernelEvidenceValidateBundlePayload",
    "KernelEvidenceValidateBundleRequest",
    "KernelEvidenceValidateBundleResult",
    "KernelClaimEvidenceLocator",
    "KernelClaimTableLocator",
    "KernelClaimResolvePayload",
    "KernelClaimResolveRequest",
    "KernelClaimResolveResult",
    "KernelCheckpointIndexLocator",
    "KernelCheckpointVerifyPayload",
    "KernelCheckpointVerifyRequest",
    "KernelCheckpointVerifyResult",
    "KernelReplayVerifyCorePayload",
    "KernelReplayVerifyCoreRequest",
    "KernelReplayVerifyCoreResult",
    "KernelAutonomousCheckpointStage",
    "KernelAutonomousCheckpointVerificationStatus",
    "KernelAutonomousPaperCheckpoint",
    "KernelAutonomousPaperCheckpointIndex",
    "KernelLeanEvidenceBundle",
    "KernelSyntheticEvidenceBundle",
    "KernelLedgerVerifyPayload",
    "KernelLedgerVerifyRequest",
    "KernelLedgerVerifyResult",
    "KernelMode",
    "KernelOperation",
    "KernelProtocolValidatePayload",
    "KernelProtocolValidateRequest",
    "KernelProtocolValidateResult",
    "KernelRequestEnvelope",
    "KernelRequestFields",
    "KernelRequestVariant",
    "KernelResult",
    "KernelResponseEnvelope",
    "KernelResponseStatus",
]
