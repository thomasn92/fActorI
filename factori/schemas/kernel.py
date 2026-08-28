"""Versioned request and response envelopes for the future Rust kernel."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, RootModel, field_validator, model_validator

from factori.schemas.artifacts import ArtifactRef, LedgerCommit
from factori.schemas.base import HASH_RE, StrictModel


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


KernelRequestVariant = Annotated[
    KernelCanonicalJsonRequest
    | KernelProtocolValidateRequest
    | KernelArtifactVerifyRequest
    | KernelLedgerVerifyRequest
    | KernelEvidenceClassifyRequest,
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


KernelResult = Annotated[
    KernelEmptyResult
    | KernelCanonicalJsonResult
    | KernelProtocolValidateResult
    | KernelArtifactVerifyResult
    | KernelLedgerVerifyResult
    | KernelEvidenceClassifyResult,
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
