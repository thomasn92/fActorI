"""Shared artifact-write and ledger-commit persistence helpers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from factori.schemas import ArtifactRef, ArtifactType, ControllerActionType, LedgerCommit
from factori.storage_protocols import ArtifactStoreProtocol, Clock, LedgerProtocol

ArtifactFormat = Literal["json", "markdown", "latex", "bib", "text", "binary"]


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


@dataclass(frozen=True)
class PersistedArtifact:
    """Convenience result for one produced artifact and its commit."""

    artifact: ArtifactRef
    commit: LedgerCommit


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
        timestamp=timestamp,
        clock=clock,
    )
    linked = [
        store.link_artifact_to_commit(artifact, commit.commit_hash)
        for artifact in artifacts
    ]
    return PersistenceResult(artifacts=linked, commit=commit)


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
    return PersistedArtifact(artifact=result.artifacts[0], commit=result.commit)


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
    return PersistedArtifact(artifact=result.artifacts[0], commit=result.commit)


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
    "PersistedArtifact",
    "PersistenceResult",
    "persist_artifacts_with_commit",
    "persist_json_artifact",
    "persist_markdown_artifact",
]
