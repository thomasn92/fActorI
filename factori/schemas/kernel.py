"""Versioned request and response envelopes for the future Rust kernel."""

from __future__ import annotations

import json
import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import ConfigDict, Field, RootModel, field_validator, model_validator

from factori.hashing import to_jsonable
from factori.schemas.artifacts import ArtifactRef, LedgerCommit
from factori.schemas.base import HASH_RE, StrictModel
from factori.schemas.enums import ArtifactType, ControllerActionType, VerificationLabel


class KernelMode(StrEnum):
    """Compatibility mode requested for one read-only kernel operation."""

    DEVELOPMENT_COMPATIBILITY = "DevelopmentCompatibility"
    STRICT_PRODUCTION = "StrictProduction"


class KernelOperation(StrEnum):
    """Operations exposed by the kernel boundary."""

    PROTOCOL_VALIDATE = "protocol.validate"
    HASH_CANONICAL_JSON = "hash.canonical_json"
    ARTIFACT_VERIFY = "artifact.verify"
    LEDGER_VERIFY = "ledger.verify"
    EVIDENCE_CLASSIFY = "evidence.classify"
    EVIDENCE_VALIDATE_BUNDLE = "evidence.validate_bundle"
    CLAIM_RESOLVE = "claim.resolve"
    CHECKPOINT_VERIFY = "checkpoint.verify"
    REPLAY_VERIFY_CORE = "replay.verify_core"
    ARTIFACT_PERSIST = "artifact.persist"
    LEDGER_APPEND = "ledger.append"
    ARTIFACT_LINK = "artifact.link"
    PERSISTENCE_COMMIT_BUNDLE = "persistence.commit_bundle"


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
_KERNEL_ARTIFACT_DIRECTORY_BY_TYPE = {
    ArtifactType.CANDIDATE: "candidates",
    ArtifactType.SCORE: "scores",
    ArtifactType.REPORT: "reports",
    ArtifactType.LITERATURE: "literature",
    ArtifactType.LEAN: "lean",
    ArtifactType.EXPERIMENT: "experiments",
    ArtifactType.LOG: "logs",
    ArtifactType.LATEX: "latex",
}
_KERNEL_FORBIDDEN_AUTHORITY_TRUE = {
    "accepted_for_publication",
    "accepted_paper",
    "certifies_scientific_validity",
    "creates_scientific_validation",
    "human_approval_granted",
    "human_approved",
    "implies_publication_readiness",
    "novelty_proven",
    "publication_ready",
}


def _contains_forbidden_kernel_authority(item: Any) -> bool:
    if isinstance(item, dict):
        return any(
            (key in _KERNEL_FORBIDDEN_AUTHORITY_TRUE and child is True)
            or _contains_forbidden_kernel_authority(child)
            for key, child in item.items()
        )
    if isinstance(item, list):
        return any(_contains_forbidden_kernel_authority(child) for child in item)
    return item in {"ExperimentVerified", "RealDataExperimentVerified"}


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


class KernelArtifactPersistPayload(StrictModel):
    """JSON-only atomic artifact persistence request."""

    run_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    artifact_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    artifact_type: ArtifactType
    json_value: Any
    metadata: dict[str, Any] = Field(default_factory=dict)
    filename_stem_optional: str | None = Field(default=None, pattern=_KERNEL_IDENTIFIER_PATTERN)
    overwrite_policy: Literal["FailIfExists"] = "FailIfExists"

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 64:
            raise ValueError("metadata must contain at most 64 entries")
        for key in value:
            if len(key.encode("utf-8")) > 128:
                raise ValueError("metadata keys must be at most 128 UTF-8 bytes")
            if key in {"format", "producer", "is_verification_evidence"}:
                raise ValueError(f"metadata key is kernel-controlled: {key}")
        if _contains_forbidden_kernel_authority(value):
            raise ValueError("metadata contains forbidden authority values")
        return value

    @model_validator(mode="after")
    def validate_serialized_bounds(self) -> KernelArtifactPersistPayload:
        try:
            metadata_json = json.dumps(
                to_jsonable(self.metadata),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            payload_json = (
                json.dumps(
                    to_jsonable(self.json_value),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"artifact persistence values must be valid JSON: {exc}") from exc
        if len(metadata_json) > 64 * 1024:
            raise ValueError("metadata exceeds 64 KiB serialized size")
        if len(payload_json) > 12 * 1024 * 1024:
            raise ValueError("serialized JSON payload exceeds 12 MiB")
        return self


class KernelLedgerAppendPayload(StrictModel):
    """Artifact-free append request for one existing run ledger."""

    run_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    expected_tip_hash: str = Field(pattern=HASH_RE.pattern)
    action_type: ControllerActionType
    payload: dict[str, Any]
    candidate_id_optional: str | None = Field(default=None, pattern=_KERNEL_IDENTIFIER_PATTERN)
    timestamp: str = Field(min_length=1, max_length=32)

    @field_validator("action_type")
    @classmethod
    def reject_init_run(cls, value: ControllerActionType) -> ControllerActionType:
        if value is ControllerActionType.INIT_RUN:
            raise ValueError("ledger.append does not support InitRun")
        return value

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", value):
            raise ValueError("timestamp must be an ASCII UTC timestamp")
        try:
            datetime.fromisoformat(value[:-1])
        except ValueError as exc:
            raise ValueError("timestamp is not a real UTC date/time") from exc
        return value

    @model_validator(mode="after")
    def validate_payload_size(self) -> KernelLedgerAppendPayload:
        try:
            encoded = json.dumps(
                to_jsonable(self.payload),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"payload must be valid JSON: {exc}") from exc
        if len(encoded) > 4 * 1024 * 1024:
            raise ValueError("serialized payload exceeds 4 MiB")
        return self


class KernelArtifactLinkPayload(StrictModel):
    """Link one persisted artifact to its existing producing ledger commit."""

    run_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    expected_ledger_tip_hash: str = Field(pattern=HASH_RE.pattern)
    artifact: ArtifactRef
    producing_commit_hash: str = Field(pattern=HASH_RE.pattern)
    overwrite_policy: Literal["FailIfExists"] = "FailIfExists"

    @model_validator(mode="after")
    def validate_link_contract(self) -> KernelArtifactLinkPayload:
        if self.artifact.producing_commit_hash is not None:
            raise ValueError("artifact.link requires an unlinked artifact reference")
        if len(self.artifact.metadata) > 64:
            raise ValueError("artifact metadata must contain at most 64 entries")
        if any(len(key.encode("utf-8")) > 128 for key in self.artifact.metadata):
            raise ValueError("artifact metadata keys must be at most 128 UTF-8 bytes")
        if _contains_forbidden_kernel_authority(self.artifact.metadata):
            raise ValueError("artifact metadata contains forbidden authority values")
        if not re.fullmatch(_KERNEL_IDENTIFIER_PATTERN, self.artifact.id):
            raise ValueError("artifact id must use safe segment syntax")
        parts = self.artifact.path.split("/")
        expected_directory = _KERNEL_ARTIFACT_DIRECTORY_BY_TYPE[self.artifact.type]
        if (
            len(parts) != 4
            or parts[0] != "runs"
            or parts[1] != self.run_id
            or parts[2] != expected_directory
            or not re.fullmatch(_KERNEL_IDENTIFIER_PATTERN, parts[3])
            or "." not in parts[3]
            or any(part in {"", ".", ".."} for part in parts)
        ):
            raise ValueError("artifact path does not match its type directory")
        try:
            metadata_json = json.dumps(
                to_jsonable(self.artifact.metadata),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            encoded = (
                json.dumps(
                    to_jsonable(
                        self.artifact.model_copy(
                            update={"producing_commit_hash": self.producing_commit_hash}
                        ).model_dump(mode="json")
                    ),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"artifact link reference must be valid JSON: {exc}") from exc
        if len(metadata_json) > 64 * 1024:
            raise ValueError("artifact metadata exceeds 64 KiB serialized size")
        if len(encoded) > 1024 * 1024:
            raise ValueError("serialized linked artifact exceeds 1 MiB")
        return self


class KernelCommitBundleArtifact(StrictModel):
    """One new JSON artifact in a transactional persistence bundle."""

    artifact_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    artifact_type: ArtifactType
    json_value: Any
    metadata: dict[str, Any] = Field(default_factory=dict)
    filename_stem_optional: str | None = Field(default=None, pattern=_KERNEL_IDENTIFIER_PATTERN)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        if len(value) > 64:
            raise ValueError("metadata must contain at most 64 entries")
        for key in value:
            if len(key.encode("utf-8")) > 128:
                raise ValueError("metadata keys must be at most 128 UTF-8 bytes")
            if key in {"format", "producer", "is_verification_evidence"}:
                raise ValueError(f"metadata key is kernel-controlled: {key}")
        if _contains_forbidden_kernel_authority(value):
            raise ValueError("metadata contains forbidden authority values")
        return value

    @model_validator(mode="after")
    def validate_serialized_bounds(self) -> KernelCommitBundleArtifact:
        try:
            metadata_json = json.dumps(
                to_jsonable(self.metadata),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            payload_json = (
                json.dumps(
                    to_jsonable(self.json_value),
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                )
                + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"bundle artifact values must be valid JSON: {exc}") from exc
        if len(metadata_json) > 64 * 1024:
            raise ValueError("metadata exceeds 64 KiB serialized size")
        if len(payload_json) > 12 * 1024 * 1024:
            raise ValueError("serialized JSON payload exceeds 12 MiB")
        return self


class KernelPersistenceCommitBundlePayload(StrictModel):
    """Crash-recoverable JSON artifact/commit/sidecar composition request."""

    run_id: str = Field(min_length=1, pattern=_KERNEL_IDENTIFIER_PATTERN)
    expected_tip_hash: str = Field(pattern=HASH_RE.pattern)
    artifacts: list[KernelCommitBundleArtifact] = Field(min_length=1, max_length=16)
    action_type: ControllerActionType
    commit_payload: dict[str, Any]
    candidate_id_optional: str | None = Field(default=None, pattern=_KERNEL_IDENTIFIER_PATTERN)
    timestamp: str = Field(min_length=1, max_length=32)
    overwrite_policy: Literal["FailIfExists"] = "FailIfExists"
    recovery_policy: Literal["ResumeExact"] = "ResumeExact"

    @field_validator("action_type")
    @classmethod
    def reject_init_run(cls, value: ControllerActionType) -> ControllerActionType:
        if value is ControllerActionType.INIT_RUN:
            raise ValueError("persistence.commit_bundle does not support InitRun")
        return value

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        return KernelLedgerAppendPayload.validate_timestamp(value)

    @model_validator(mode="after")
    def validate_bundle(self) -> KernelPersistenceCommitBundlePayload:
        ids = [item.artifact_id for item in self.artifacts]
        if len(ids) != len(set(ids)):
            raise ValueError("bundle artifact IDs must be unique")
        paths = [
            (
                "runs",
                self.run_id,
                _KERNEL_ARTIFACT_DIRECTORY_BY_TYPE[item.artifact_type],
                f"{item.filename_stem_optional or item.artifact_id}.json",
            )
            for item in self.artifacts
        ]
        if len(paths) != len(set(paths)):
            raise ValueError("bundle artifact paths must be unique")
        try:
            payload_json = json.dumps(
                to_jsonable(self.commit_payload),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            artifact_bytes = sum(
                len(
                    (
                        json.dumps(
                            to_jsonable(item.json_value),
                            sort_keys=True,
                            separators=(",", ":"),
                            ensure_ascii=False,
                            allow_nan=False,
                        )
                        + "\n"
                    ).encode("utf-8")
                )
                for item in self.artifacts
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"bundle values must be valid JSON: {exc}") from exc
        if len(payload_json) > 4 * 1024 * 1024:
            raise ValueError("serialized commit payload exceeds 4 MiB")
        if artifact_bytes > 12 * 1024 * 1024:
            raise ValueError("aggregate artifact payload exceeds 12 MiB")
        return self


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


class KernelArtifactPersistRequest(KernelRequestFields):
    """Typed request for atomic JSON artifact persistence."""

    operation: Literal[KernelOperation.ARTIFACT_PERSIST]
    payload: KernelArtifactPersistPayload


class KernelLedgerAppendRequest(KernelRequestFields):
    """Typed request for a transactional, artifact-free ledger append."""

    operation: Literal[KernelOperation.LEDGER_APPEND]
    payload: KernelLedgerAppendPayload


class KernelArtifactLinkRequest(KernelRequestFields):
    """Typed request for one atomic producer sidecar publication."""

    operation: Literal[KernelOperation.ARTIFACT_LINK]
    payload: KernelArtifactLinkPayload


class KernelPersistenceCommitBundleRequest(KernelRequestFields):
    """Request for one crash-recoverable artifact/commit/sidecar bundle."""

    operation: Literal[KernelOperation.PERSISTENCE_COMMIT_BUNDLE]
    payload: KernelPersistenceCommitBundlePayload


KernelRequestVariant = Annotated[
    KernelCanonicalJsonRequest
    | KernelProtocolValidateRequest
    | KernelArtifactVerifyRequest
    | KernelLedgerVerifyRequest
    | KernelEvidenceClassifyRequest
    | KernelEvidenceValidateBundleRequest
    | KernelClaimResolveRequest
    | KernelCheckpointVerifyRequest
    | KernelReplayVerifyCoreRequest
    | KernelArtifactPersistRequest
    | KernelLedgerAppendRequest
    | KernelArtifactLinkRequest
    | KernelPersistenceCommitBundleRequest,
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
        | KernelArtifactPersistPayload
        | KernelLedgerAppendPayload
        | KernelArtifactLinkPayload
        | KernelPersistenceCommitBundlePayload
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


class KernelArtifactPersistResult(StrictModel):
    """Accepted result for JSON artifact persistence; it grants no authority."""

    artifact: ArtifactRef
    bytes_written: int = Field(ge=1)
    created: Literal[True]
    linked_to_ledger: Literal[False]
    authority_granted: Literal[False]

    @model_validator(mode="after")
    def validate_artifact_contract(self) -> KernelArtifactPersistResult:
        artifact = self.artifact
        expected_directory = _KERNEL_ARTIFACT_DIRECTORY_BY_TYPE[artifact.type]
        parts = artifact.path.split("/")
        if (
            not re.fullmatch(_KERNEL_IDENTIFIER_PATTERN, artifact.id)
            or len(parts) != 4
            or parts[0] != "runs"
            or not re.fullmatch(_KERNEL_IDENTIFIER_PATTERN, parts[1])
            or parts[2] != expected_directory
            or not parts[3].endswith(".json")
            or not re.fullmatch(_KERNEL_IDENTIFIER_PATTERN, parts[3])
        ):
            raise ValueError("persisted artifact path does not match its type directory")
        if artifact.producing_commit_hash is not None:
            raise ValueError("persisted artifact must not have a producing commit")
        if artifact.metadata.get("format") != "json":
            raise ValueError("persisted artifact metadata must report JSON format")
        if artifact.metadata.get("is_verification_evidence") is not False:
            raise ValueError("persisted artifact must not claim verification evidence")
        if "producer" in artifact.metadata:
            raise ValueError("persisted artifact metadata must not contain producer authority")
        if _contains_forbidden_kernel_authority(artifact.metadata):
            raise ValueError("persisted artifact metadata contains forbidden authority")
        return self


class KernelLedgerAppendResult(StrictModel):
    """Accepted result for one artifact-free ledger append."""

    commit: LedgerCommit
    previous_tip_hash: str = Field(pattern=HASH_RE.pattern)
    new_tip_hash: str = Field(pattern=HASH_RE.pattern)
    commit_count_before: int = Field(ge=1, strict=True)
    commit_count_after: int = Field(ge=2, strict=True)
    appended: Literal[True]
    linked_artifact_count: Literal[0]
    authority_granted: Literal[False]

    @field_validator("appended", mode="before")
    @classmethod
    def require_strict_appended(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("appended must be the boolean true")
        return value

    @field_validator("linked_artifact_count", mode="before")
    @classmethod
    def require_strict_zero_links(cls, value: Any) -> Any:
        if isinstance(value, bool) or value != 0:
            raise ValueError("linked_artifact_count must be the integer zero")
        return value

    @field_validator("authority_granted", mode="before")
    @classmethod
    def require_strict_no_authority(cls, value: Any) -> Any:
        if value is not False:
            raise ValueError("authority_granted must be the boolean false")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> KernelLedgerAppendResult:
        if self.commit.parent_hash != self.previous_tip_hash:
            raise ValueError("commit parent must equal previous tip")
        if self.commit.commit_hash != self.new_tip_hash:
            raise ValueError("new tip must equal commit hash")
        if self.commit_count_after != self.commit_count_before + 1:
            raise ValueError("append must increase commit count by one")
        if self.commit.artifact_refs:
            raise ValueError("ledger.append cannot link artifacts")
        if self.commit.action_type is ControllerActionType.INIT_RUN:
            raise ValueError("ledger.append cannot return an InitRun commit")
        if not re.fullmatch(_KERNEL_IDENTIFIER_PATTERN, self.commit.run_id):
            raise ValueError("ledger.append commit run_id is unsafe")
        if self.commit.candidate_id is not None and not re.fullmatch(
            _KERNEL_IDENTIFIER_PATTERN, self.commit.candidate_id
        ):
            raise ValueError("ledger.append commit candidate_id is unsafe")
        KernelLedgerAppendPayload.validate_timestamp(self.commit.timestamp)
        return self


class KernelArtifactLinkResult(StrictModel):
    """Accepted result for one atomic producer sidecar link."""

    artifact: ArtifactRef
    sidecar_path: str = Field(min_length=1)
    sidecar_content_hash: str = Field(pattern=HASH_RE.pattern)
    bytes_written: int = Field(ge=1, strict=True)
    created: Literal[True]
    linked_to_ledger: Literal[True]
    authority_granted: Literal[False]

    @field_validator("created", "linked_to_ledger", mode="before")
    @classmethod
    def require_strict_true(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("artifact link success flags must be boolean true")
        return value

    @field_validator("authority_granted", mode="before")
    @classmethod
    def require_strict_false(cls, value: Any) -> Any:
        if value is not False:
            raise ValueError("authority_granted must be boolean false")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> KernelArtifactLinkResult:
        if self.artifact.producing_commit_hash is None:
            raise ValueError("linked artifact must have a producing commit")
        parts = self.artifact.path.split("/")
        expected_directory = _KERNEL_ARTIFACT_DIRECTORY_BY_TYPE[self.artifact.type]
        if len(parts) != 4 or parts[0] != "runs" or parts[2] != expected_directory:
            raise ValueError("linked artifact path does not match its type directory")
        expected_sidecar = f"{self.artifact.path}.meta.json"
        if self.sidecar_path != expected_sidecar:
            raise ValueError("sidecar path does not match artifact path")
        return self


class KernelPersistenceCommitBundleResult(StrictModel):
    """Accepted result for one artifact-bearing persistence bundle."""

    artifacts: list[ArtifactRef] = Field(min_length=1, max_length=16)
    commit: LedgerCommit
    previous_tip_hash: str = Field(pattern=HASH_RE.pattern)
    new_tip_hash: str = Field(pattern=HASH_RE.pattern)
    commit_count_before: int = Field(ge=1, strict=True)
    commit_count_after: int = Field(ge=2, strict=True)
    artifact_count: int = Field(ge=1, le=16, strict=True)
    sidecar_count: int = Field(ge=1, le=16, strict=True)
    bundle_committed: Literal[True]
    recovered_from_intent: bool = Field(strict=True)
    authority_granted: Literal[False]

    @field_validator("bundle_committed", mode="before")
    @classmethod
    def require_strict_bundle_commit(cls, value: Any) -> Any:
        if value is not True:
            raise ValueError("bundle_committed must be boolean true")
        return value

    @field_validator("authority_granted", mode="before")
    @classmethod
    def require_strict_bundle_authority(cls, value: Any) -> Any:
        if value is not False:
            raise ValueError("authority_granted must be boolean false")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> KernelPersistenceCommitBundleResult:
        if self.previous_tip_hash != self.commit.parent_hash:
            raise ValueError("bundle commit parent must equal previous tip")
        if self.new_tip_hash != self.commit.commit_hash:
            raise ValueError("bundle new tip must equal commit hash")
        if self.commit_count_after != self.commit_count_before + 1:
            raise ValueError("bundle commit count must increase by one")
        if self.artifact_count != len(self.artifacts) or self.sidecar_count != len(self.artifacts):
            raise ValueError("bundle output counts must equal artifact count")
        if len(self.commit.artifact_refs) != len(self.artifacts):
            raise ValueError("bundle commit references must equal result artifacts")
        if [item.model_dump(mode="json") for item in self.commit.artifact_refs] != [
            item.model_dump(mode="json") for item in self.artifacts
        ]:
            raise ValueError("bundle result artifacts must equal commit references")
        if any(item.producing_commit_hash != self.commit.commit_hash for item in self.artifacts):
            raise ValueError("bundle artifacts must be linked to the new commit")
        if self.commit.action_type is ControllerActionType.INIT_RUN:
            raise ValueError("bundle cannot return an InitRun commit")
        return self


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
    | KernelReplayVerifyCoreResult
    | KernelArtifactPersistResult
    | KernelLedgerAppendResult
    | KernelArtifactLinkResult
    | KernelPersistenceCommitBundleResult,
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
        if self.operation == KernelOperation.ARTIFACT_PERSIST:
            persist_codes = {
                "artifact_persist_run_missing",
                "artifact_persist_directory_invalid",
                "artifact_persist_target_exists",
                "artifact_persist_payload_invalid",
                "artifact_persist_size_exceeded",
                "artifact_persist_temp_write_failed",
                "artifact_persist_publish_failed",
                "artifact_persist_temp_cleanup_warning",
                "artifact_persist_durability_uncertain",
                "artifact_persist_postcondition_failed",
            }
            if any(item.code not in persist_codes for item in self.diagnostics):
                raise ValueError("artifact.persist response has an invalid diagnostic")
            if self.status == KernelResponseStatus.ACCEPTED and not self.mutation_performed:
                raise ValueError("accepted artifact.persist responses must report mutation")
            if self.status == KernelResponseStatus.ACCEPTED and (
                len(self.diagnostics) > 1
                or any(
                    item.code != "artifact_persist_temp_cleanup_warning"
                    for item in self.diagnostics
                )
            ):
                raise ValueError("accepted artifact.persist diagnostics are invalid")
            if self.status != KernelResponseStatus.ACCEPTED and self.mutation_performed:
                allowed = {
                    "artifact_persist_durability_uncertain",
                    "artifact_persist_postcondition_failed",
                }
                if (
                    self.status != KernelResponseStatus.ERROR
                    or len(self.diagnostics) != 1
                    or self.diagnostics[0].code not in allowed
                ):
                    raise ValueError("artifact.persist mutation flag is invalid for this response")
            if self.status != KernelResponseStatus.ACCEPTED and not self.mutation_performed:
                disallowed = {
                    "artifact_persist_temp_cleanup_warning",
                    "artifact_persist_durability_uncertain",
                    "artifact_persist_postcondition_failed",
                }
                if len(self.diagnostics) != 1 or self.diagnostics[0].code in disallowed:
                    raise ValueError("artifact.persist pre-publication diagnostics are invalid")
        elif self.operation == KernelOperation.LEDGER_APPEND:
            append_codes = {
                "ledger_append_run_missing",
                "ledger_append_directory_invalid",
                "ledger_append_ledger_invalid",
                "ledger_append_root_unsupported",
                "ledger_append_payload_invalid",
                "ledger_append_size_exceeded",
                "ledger_append_tip_mismatch",
                "ledger_append_busy",
                "ledger_append_insert_failed",
                "ledger_append_commit_uncertain",
                "ledger_append_postcondition_failed",
            }
            if any(item.code not in append_codes for item in self.diagnostics):
                raise ValueError("ledger.append response has an invalid diagnostic")
            if self.status == KernelResponseStatus.ACCEPTED:
                if not self.mutation_performed or self.diagnostics:
                    raise ValueError("accepted ledger.append responses must be committed")
            elif self.mutation_performed:
                if (
                    self.status != KernelResponseStatus.ERROR
                    or len(self.diagnostics) != 1
                    or self.diagnostics[0].code
                    not in {"ledger_append_commit_uncertain", "ledger_append_postcondition_failed"}
                ):
                    raise ValueError("ledger.append mutation flag is invalid")
            elif len(self.diagnostics) != 1 or self.diagnostics[0].code in {
                "ledger_append_commit_uncertain",
                "ledger_append_postcondition_failed",
            }:
                raise ValueError("ledger.append rejection diagnostics are invalid")
        elif self.operation == KernelOperation.ARTIFACT_LINK:
            link_codes = {
                "artifact_link_run_missing",
                "artifact_link_directory_invalid",
                "artifact_link_payload_invalid",
                "artifact_link_size_exceeded",
                "artifact_link_artifact_invalid",
                "artifact_link_ledger_invalid",
                "artifact_link_commit_missing",
                "artifact_link_commit_mismatch",
                "artifact_link_tip_mismatch",
                "artifact_link_busy",
                "artifact_link_target_exists",
                "artifact_link_temp_write_failed",
                "artifact_link_publish_failed",
                "artifact_link_temp_cleanup_warning",
                "artifact_link_durability_uncertain",
                "artifact_link_snapshot_changed",
                "artifact_link_postcondition_failed",
            }
            if any(item.code not in link_codes for item in self.diagnostics):
                raise ValueError("artifact.link response has an invalid diagnostic")
            if self.status == KernelResponseStatus.ACCEPTED:
                if not self.mutation_performed or len(self.diagnostics) > 1:
                    raise ValueError("accepted artifact.link responses must report mutation")
                if any(
                    item.code != "artifact_link_temp_cleanup_warning" for item in self.diagnostics
                ):
                    raise ValueError("artifact.link accepted diagnostics are invalid")
            elif self.mutation_performed:
                if (
                    self.status != KernelResponseStatus.ERROR
                    or len(self.diagnostics) != 1
                    or self.diagnostics[0].code
                    not in {
                        "artifact_link_durability_uncertain",
                        "artifact_link_snapshot_changed",
                        "artifact_link_postcondition_failed",
                    }
                ):
                    raise ValueError("artifact.link mutation flag is invalid")
            elif len(self.diagnostics) != 1 or self.diagnostics[0].code in {
                "artifact_link_temp_cleanup_warning",
                "artifact_link_durability_uncertain",
                "artifact_link_postcondition_failed",
            }:
                raise ValueError("artifact.link rejection diagnostics are invalid")
        elif self.operation == KernelOperation.PERSISTENCE_COMMIT_BUNDLE:
            bundle_codes = {
                "persistence_bundle_run_missing",
                "persistence_bundle_directory_invalid",
                "persistence_bundle_payload_invalid",
                "persistence_bundle_size_exceeded",
                "persistence_bundle_duplicate",
                "persistence_bundle_target_exists",
                "persistence_bundle_ledger_invalid",
                "persistence_bundle_tip_mismatch",
                "persistence_bundle_busy",
                "persistence_bundle_intent_write_failed",
                "persistence_bundle_recovery_required",
                "persistence_bundle_recovery_invalid",
                "persistence_bundle_recovery_conflict",
                "persistence_bundle_temp_write_failed",
                "persistence_bundle_snapshot_changed",
                "persistence_bundle_publish_failed",
                "persistence_bundle_rollback_uncertain",
                "persistence_bundle_insert_failed",
                "persistence_bundle_commit_uncertain",
                "persistence_bundle_durability_uncertain",
                "persistence_bundle_intent_cleanup_failed",
                "persistence_bundle_postcondition_failed",
            }
            if any(item.code not in bundle_codes for item in self.diagnostics):
                raise ValueError("persistence.commit_bundle response has an invalid diagnostic")
            if self.status == KernelResponseStatus.ACCEPTED:
                if not self.mutation_performed or self.diagnostics:
                    raise ValueError("accepted persistence bundles must be committed")
            elif len(self.diagnostics) != 1:
                raise ValueError("persistence.commit_bundle responses require one diagnostic")
            if self.status != KernelResponseStatus.ACCEPTED and self.mutation_performed:
                allowed = {
                    "persistence_bundle_rollback_uncertain",
                    "persistence_bundle_recovery_conflict",
                    "persistence_bundle_commit_uncertain",
                    "persistence_bundle_durability_uncertain",
                    "persistence_bundle_intent_cleanup_failed",
                    "persistence_bundle_postcondition_failed",
                }
                if (
                    self.status != KernelResponseStatus.ERROR
                    or self.diagnostics[0].code not in allowed
                ):
                    raise ValueError("persistence.commit_bundle mutation flag is invalid")
            elif self.status != KernelResponseStatus.ACCEPTED and not self.mutation_performed:
                disallowed = {
                    "persistence_bundle_rollback_uncertain",
                    "persistence_bundle_commit_uncertain",
                    "persistence_bundle_durability_uncertain",
                    "persistence_bundle_intent_cleanup_failed",
                    "persistence_bundle_postcondition_failed",
                }
                if self.diagnostics[0].code in disallowed:
                    raise ValueError("persistence.commit_bundle rejection diagnostic is invalid")
        elif self.mutation_performed:
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
            KernelOperation.ARTIFACT_PERSIST: KernelArtifactPersistResult,
            KernelOperation.LEDGER_APPEND: KernelLedgerAppendResult,
            KernelOperation.ARTIFACT_LINK: KernelArtifactLinkResult,
            KernelOperation.PERSISTENCE_COMMIT_BUNDLE: KernelPersistenceCommitBundleResult,
        }[self.operation]
        if not isinstance(self.result, expected_result):
            raise ValueError(
                f"accepted {self.operation.value} responses have an invalid result type"
            )
        return self


__all__ = [
    "KernelCommitBundleArtifact",
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
    "KernelArtifactPersistPayload",
    "KernelArtifactPersistRequest",
    "KernelArtifactPersistResult",
    "KernelLedgerAppendPayload",
    "KernelLedgerAppendRequest",
    "KernelLedgerAppendResult",
    "KernelArtifactLinkPayload",
    "KernelArtifactLinkRequest",
    "KernelArtifactLinkResult",
    "KernelPersistenceCommitBundlePayload",
    "KernelPersistenceCommitBundleRequest",
    "KernelPersistenceCommitBundleResult",
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
