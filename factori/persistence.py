"""Shared artifact-write and ledger-commit persistence helpers."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from factori.artifacts import ArtifactStore
from factori.hashing import to_jsonable
from factori.ledger import ResearchLedger
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    KernelCommitBundleArtifact,
    KernelMode,
    KernelPersistenceCommitBundlePayload,
    KernelPersistenceCommitBundleResult,
    KernelResponseStatus,
    LedgerCommit,
)
from factori.storage_protocols import ArtifactStoreProtocol, Clock, LedgerProtocol

ArtifactFormat = Literal["json", "markdown", "latex", "bib", "text", "binary"]
PersistenceBackend = Literal["python", "rust-kernel"]
PersistenceRoutingReason = Literal[
    "kernel_not_configured",
    "rust_eligible",
    "non_json_bundle",
    "python_owned_metadata",
    "unsupported_storage_implementation",
    "kernel_contract_ineligible",
]


class PersistenceKernelError(RuntimeError):
    """Raised when an activated Rust persistence attempt cannot be accepted safely."""

    def __init__(
        self,
        message: str,
        *,
        diagnostic_code: str | None,
        mutation_performed: bool | None,
        request_timestamp: str,
    ) -> None:
        super().__init__(message)
        self.diagnostic_code = diagnostic_code
        self.mutation_performed = mutation_performed
        self.request_timestamp = request_timestamp


@dataclass(frozen=True)
class ArtifactWriteSpec:
    """Description of one artifact write that will be committed to the ledger."""

    artifact_id: str
    artifact_type: ArtifactType
    payload: Any
    artifact_format: ArtifactFormat
    metadata: Mapping[str, Any] | None = None
    extension: str | None = None
    format_label: str | None = None
    filename_stem: str | None = None


@dataclass(frozen=True)
class PersistenceResult:
    """Result of one ledger commit that produced one or more artifacts."""

    artifacts: list[ArtifactRef]
    commit: LedgerCommit
    backend_used: PersistenceBackend = "python"
    routing_reason: PersistenceRoutingReason = "kernel_not_configured"


@dataclass(frozen=True)
class PersistedArtifact:
    """Convenience result for one produced artifact and its commit."""

    artifact: ArtifactRef
    commit: LedgerCommit
    backend_used: PersistenceBackend = "python"
    routing_reason: PersistenceRoutingReason = "kernel_not_configured"


@dataclass(frozen=True)
class _KernelBundlePlan:
    artifacts: list[KernelCommitBundleArtifact]
    expected_tip_hash: str
    timestamp: str


@dataclass(frozen=True)
class _PersistenceRouteDecision:
    plan: _KernelBundlePlan | None
    reason: PersistenceRoutingReason
    resolved_timestamp: str | None = None


def persist_artifacts_with_commit(
    *,
    run_id: str,
    store: ArtifactStoreProtocol,
    ledger: LedgerProtocol,
    artifact_specs: Sequence[ArtifactWriteSpec],
    action_type: ControllerActionType,
    commit_payload: Mapping[str, Any],
    candidate_id: str | None = None,
    parent_hash: str | None = None,
    timestamp: str | None = None,
    clock: Clock | None = None,
) -> PersistenceResult:
    """Write artifacts, append one ledger commit, and link produced artifacts to it."""
    if not artifact_specs:
        raise ValueError("at least one artifact spec is required")

    route = _select_persistence_route(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=artifact_specs,
        action_type=action_type,
        commit_payload=commit_payload,
        candidate_id=candidate_id,
        parent_hash=parent_hash,
        timestamp=timestamp,
        clock=clock,
    )
    if route.plan is not None:
        return _persist_with_rust_kernel(
            run_id=run_id,
            store=store,
            ledger=ledger,
            plan=route.plan,
            action_type=action_type,
            commit_payload=commit_payload,
            candidate_id=candidate_id,
        )

    artifacts = [
        _write_artifact(run_id=run_id, store=store, spec=spec)
        for spec in artifact_specs
    ]
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=candidate_id,
        parent_hash=parent_hash if parent_hash is not None else ledger.latest_commit_hash(run_id),
        action_type=action_type,
        payload=dict(commit_payload),
        artifact_refs=artifacts,
        timestamp=route.resolved_timestamp or timestamp,
        clock=clock,
    )
    linked = [
        store.link_artifact_to_commit(artifact, commit.commit_hash)
        for artifact in artifacts
    ]
    return PersistenceResult(
        artifacts=linked,
        commit=commit,
        backend_used="python",
        routing_reason=route.reason,
    )


def persist_json_artifact(
    *,
    run_id: str,
    store: ArtifactStoreProtocol,
    ledger: LedgerProtocol,
    artifact_id: str,
    artifact_type: ArtifactType,
    payload: Any,
    action_type: ControllerActionType,
    commit_payload: Mapping[str, Any],
    candidate_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    parent_hash: str | None = None,
    timestamp: str | None = None,
    clock: Clock | None = None,
) -> PersistedArtifact:
    """Persist one JSON artifact and one producing ledger commit."""
    result = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                payload=payload,
                artifact_format="json",
                metadata=metadata,
            )
        ],
        action_type=action_type,
        commit_payload=commit_payload,
        candidate_id=candidate_id,
        parent_hash=parent_hash,
        timestamp=timestamp,
        clock=clock,
    )
    return PersistedArtifact(
        artifact=result.artifacts[0],
        commit=result.commit,
        backend_used=result.backend_used,
        routing_reason=result.routing_reason,
    )


def persist_markdown_artifact(
    *,
    run_id: str,
    store: ArtifactStoreProtocol,
    ledger: LedgerProtocol,
    artifact_id: str,
    artifact_type: ArtifactType,
    markdown: str,
    action_type: ControllerActionType,
    commit_payload: Mapping[str, Any],
    candidate_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
    parent_hash: str | None = None,
    timestamp: str | None = None,
    clock: Clock | None = None,
) -> PersistedArtifact:
    """Persist one Markdown artifact and one producing ledger commit."""
    result = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                artifact_id=artifact_id,
                artifact_type=artifact_type,
                payload=markdown,
                artifact_format="markdown",
                metadata=metadata,
            )
        ],
        action_type=action_type,
        commit_payload=commit_payload,
        candidate_id=candidate_id,
        parent_hash=parent_hash,
        timestamp=timestamp,
        clock=clock,
    )
    return PersistedArtifact(
        artifact=result.artifacts[0],
        commit=result.commit,
        backend_used=result.backend_used,
        routing_reason=result.routing_reason,
    )


def _select_persistence_route(
    *,
    run_id: str,
    store: ArtifactStoreProtocol,
    ledger: LedgerProtocol,
    artifact_specs: Sequence[ArtifactWriteSpec],
    action_type: ControllerActionType,
    commit_payload: Mapping[str, Any],
    candidate_id: str | None,
    parent_hash: str | None,
    timestamp: str | None,
    clock: Clock | None,
) -> _PersistenceRouteDecision:
    if not isinstance(store, ArtifactStore) or store.kernel_binary is None:
        return _PersistenceRouteDecision(None, "kernel_not_configured")
    if not isinstance(ledger, ResearchLedger):
        return _PersistenceRouteDecision(None, "unsupported_storage_implementation")
    expected_ledger = store.run_path(run_id) / "ledger.sqlite"
    if ledger.path.resolve() != expected_ledger.resolve():
        return _PersistenceRouteDecision(None, "unsupported_storage_implementation")
    if any(spec.artifact_format != "json" for spec in artifact_specs):
        return _PersistenceRouteDecision(None, "non_json_bundle")

    kernel_artifacts: list[KernelCommitBundleArtifact] = []
    for spec in artifact_specs:
        metadata = (
            {"is_verification_evidence": False}
            if spec.metadata is None
            else dict(spec.metadata)
        )
        final_metadata = {"format": "json", **metadata}
        if (
            final_metadata.get("format") != "json"
            or final_metadata.get("is_verification_evidence") is not False
            or "producer" in final_metadata
        ):
            return _PersistenceRouteDecision(None, "python_owned_metadata")
        request_metadata = {
            key: value
            for key, value in final_metadata.items()
            if key not in {"format", "is_verification_evidence"}
        }
        try:
            normalized_metadata = to_jsonable(request_metadata)
            KernelCommitBundleArtifact.validate_metadata(normalized_metadata)
        except (TypeError, ValueError):
            return _PersistenceRouteDecision(None, "python_owned_metadata")
        try:
            kernel_artifacts.append(
                KernelCommitBundleArtifact(
                    artifact_id=spec.artifact_id,
                    artifact_type=spec.artifact_type,
                    json_value=to_jsonable(spec.payload),
                    metadata=normalized_metadata,
                    filename_stem_optional=spec.filename_stem,
                )
            )
        except (TypeError, ValueError):
            return _PersistenceRouteDecision(None, "kernel_contract_ineligible")

    current_tip = ledger.latest_commit_hash(run_id)
    expected_tip = parent_hash if parent_hash is not None else current_tip
    if expected_tip is None or current_tip != expected_tip:
        return _PersistenceRouteDecision(None, "kernel_contract_ineligible")
    resolved_timestamp = timestamp or (clock or ledger.clock).now()
    try:
        KernelPersistenceCommitBundlePayload(
            run_id=run_id,
            expected_tip_hash=expected_tip,
            artifacts=kernel_artifacts,
            action_type=action_type,
            commit_payload=dict(commit_payload),
            candidate_id_optional=candidate_id,
            timestamp=resolved_timestamp,
        )
    except (TypeError, ValueError):
        return _PersistenceRouteDecision(
            None,
            "kernel_contract_ineligible",
            resolved_timestamp=resolved_timestamp,
        )
    return _PersistenceRouteDecision(
        _KernelBundlePlan(
            artifacts=kernel_artifacts,
            expected_tip_hash=expected_tip,
            timestamp=resolved_timestamp,
        ),
        "rust_eligible",
        resolved_timestamp=resolved_timestamp,
    )


def _persist_with_rust_kernel(
    *,
    run_id: str,
    store: ArtifactStoreProtocol,
    ledger: LedgerProtocol,
    plan: _KernelBundlePlan,
    action_type: ControllerActionType,
    commit_payload: Mapping[str, Any],
    candidate_id: str | None,
) -> PersistenceResult:
    from factori.kernel_bridge import KernelBridgeError, commit_artifact_bundle

    if not isinstance(store, ArtifactStore) or not isinstance(ledger, ResearchLedger):
        raise AssertionError("Rust persistence route requires concrete local storage")
    binary = store.kernel_binary
    if binary is None:
        raise AssertionError("Rust persistence route requires an explicit kernel binary")
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise PersistenceKernelError(
            f"configured Rust kernel is not an executable file: {binary}",
            diagnostic_code="kernel_binary_invalid",
            mutation_performed=False,
            request_timestamp=plan.timestamp,
        )
    store.init_run(run_id)
    try:
        response = commit_artifact_bundle(
            run_id,
            plan.expected_tip_hash,
            plan.artifacts,
            action_type,
            dict(commit_payload),
            candidate_id_optional=candidate_id,
            timestamp=plan.timestamp,
            mode=KernelMode.STRICT_PRODUCTION,
            root=store.root,
            kernel_binary=binary,
        )
    except KernelBridgeError as exc:
        raise PersistenceKernelError(
            str(exc),
            diagnostic_code=getattr(exc, "diagnostic_code", None),
            mutation_performed=getattr(exc, "mutation_performed", None),
            request_timestamp=plan.timestamp,
        ) from exc
    if response.status is not KernelResponseStatus.ACCEPTED:
        diagnostic = response.diagnostics[0] if response.diagnostics else None
        raise PersistenceKernelError(
            "Rust persistence bundle was not accepted"
            + ("" if diagnostic is None else f": {diagnostic.code}"),
            diagnostic_code=None if diagnostic is None else diagnostic.code,
            mutation_performed=response.mutation_performed,
            request_timestamp=plan.timestamp,
        )
    result = response.result
    if not isinstance(result, KernelPersistenceCommitBundleResult):
        raise PersistenceKernelError(
            "Rust persistence bundle returned the wrong result type",
            diagnostic_code="kernel_response_invalid",
            mutation_performed=response.mutation_performed,
            request_timestamp=plan.timestamp,
        )
    return PersistenceResult(
        artifacts=result.artifacts,
        commit=result.commit,
        backend_used="rust-kernel",
        routing_reason="rust_eligible",
    )


def _write_artifact(
    *,
    run_id: str,
    store: ArtifactStoreProtocol,
    spec: ArtifactWriteSpec,
) -> ArtifactRef:
    metadata = (
        {"is_verification_evidence": False}
        if spec.metadata is None
        else dict(spec.metadata)
    )
    if spec.artifact_format == "json":
        return store.write_json(
            run_id=run_id,
            artifact_id=spec.artifact_id,
            artifact_type=spec.artifact_type,
            data=spec.payload,
            metadata=metadata,
            filename_stem=spec.filename_stem,
        )
    if spec.artifact_format == "binary":
        if not isinstance(spec.payload, bytes):
            raise TypeError("binary artifact payload must be bytes")
        return store.write_bytes(
            run_id=run_id,
            artifact_id=spec.artifact_id,
            artifact_type=spec.artifact_type,
            content=spec.payload,
            extension=spec.extension or "bin",
            format_label=spec.format_label or "binary",
            metadata=metadata,
            filename_stem=spec.filename_stem,
        )
    if not isinstance(spec.payload, str):
        raise TypeError("text artifact payload must be a string")
    if spec.artifact_format == "markdown":
        return store.write_markdown(
            run_id=run_id,
            artifact_id=spec.artifact_id,
            artifact_type=spec.artifact_type,
            markdown=spec.payload,
            metadata=metadata,
            filename_stem=spec.filename_stem,
        )
    if spec.artifact_format == "latex":
        return store.write_text(
            run_id=run_id,
            artifact_id=spec.artifact_id,
            artifact_type=spec.artifact_type,
            text=spec.payload,
            extension=spec.extension or "tex",
            format_label=spec.format_label or "latex",
            metadata=metadata,
            filename_stem=spec.filename_stem,
        )
    if spec.artifact_format == "bib":
        return store.write_text(
            run_id=run_id,
            artifact_id=spec.artifact_id,
            artifact_type=spec.artifact_type,
            text=spec.payload,
            extension=spec.extension or "bib",
            format_label=spec.format_label or "bibtex",
            metadata=metadata,
            filename_stem=spec.filename_stem,
        )
    if spec.artifact_format == "text":
        return store.write_text(
            run_id=run_id,
            artifact_id=spec.artifact_id,
            artifact_type=spec.artifact_type,
            text=spec.payload,
            extension=spec.extension or "txt",
            format_label=spec.format_label or "text",
            metadata=metadata,
            filename_stem=spec.filename_stem,
        )
    raise ValueError(f"unsupported artifact format: {spec.artifact_format}")


__all__ = [
    "ArtifactFormat",
    "ArtifactWriteSpec",
    "PersistenceBackend",
    "PersistenceKernelError",
    "PersistedArtifact",
    "PersistenceResult",
    "PersistenceRoutingReason",
    "persist_artifacts_with_commit",
    "persist_json_artifact",
    "persist_markdown_artifact",
]
