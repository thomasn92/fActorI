"""Small persistence protocols and injectable clocks for fActorI."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from factori.schemas import ArtifactRef, ArtifactType, ControllerActionType, LedgerCommit


@runtime_checkable
class Clock(Protocol):
    """Timestamp source used by persistence and orchestration boundaries."""

    def now(self) -> str: ...


@dataclass(frozen=True)
class SystemClock:
    """UTC wall clock preserving the existing timestamp representation."""

    def now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class FixedClock:
    """Deterministic clock for tests and reproducible orchestration."""

    timestamp: str

    def now(self) -> str:
        return self.timestamp


@runtime_checkable
class LedgerProtocol(Protocol):
    """Append-only ledger methods currently consumed by pipeline stages."""

    path: Path

    def append_commit(
        self,
        *,
        run_id: str,
        action_type: ControllerActionType,
        payload: dict[str, Any],
        parent_hash: str | None = None,
        candidate_id: str | None = None,
        artifact_refs: Iterable[ArtifactRef] = (),
        timestamp: str | None = None,
        clock: Clock | None = None,
    ) -> LedgerCommit: ...

    def has_commit(self, commit_hash: str) -> bool: ...

    def get_commit(self, commit_hash: str) -> LedgerCommit: ...

    def list_commits(self, run_id: str | None = None) -> list[LedgerCommit]: ...

    def latest_commit_hash(self, run_id: str) -> str | None: ...

    def validate(self) -> None: ...


@runtime_checkable
class ArtifactStoreProtocol(Protocol):
    """Filesystem artifact methods currently consumed by pipeline stages."""

    root: Path

    def run_path(self, run_id: str) -> Path: ...

    def init_run(self, run_id: str) -> Path: ...

    def write_json(
        self,
        *,
        run_id: str,
        artifact_id: str,
        artifact_type: ArtifactType,
        data: Any,
        metadata: dict[str, Any] | None = None,
        producing_commit_hash: str | None = None,
    ) -> ArtifactRef: ...

    def write_markdown(
        self,
        *,
        run_id: str,
        artifact_id: str,
        artifact_type: ArtifactType,
        markdown: str,
        metadata: dict[str, Any] | None = None,
        producing_commit_hash: str | None = None,
    ) -> ArtifactRef: ...

    def link_artifact_to_commit(
        self,
        artifact: ArtifactRef,
        commit_hash: str,
    ) -> ArtifactRef: ...


__all__ = [
    "ArtifactStoreProtocol",
    "Clock",
    "FixedClock",
    "LedgerProtocol",
    "SystemClock",
]
