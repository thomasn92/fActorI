"""SQLite-backed append-only research ledger."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from factori.hashing import canonical_json, sha256_json
from factori.schemas import ArtifactRef, ControllerActionType, LedgerCommit
from factori.storage_protocols import Clock, SystemClock


class LedgerError(RuntimeError):
    """Raised when the ledger invariant is violated."""


def utc_timestamp() -> str:
    """Return an ISO-8601 UTC timestamp."""
    return SystemClock().now()


def _artifact_for_hash(
    ref: ArtifactRef,
    commit_hash: str | None,
    self_link: bool,
) -> dict[str, Any]:
    data = ref.model_dump(mode="json")
    if self_link or (commit_hash is not None and ref.producing_commit_hash == commit_hash):
        data["producing_commit_hash"] = "<self>"
    return data


def commit_hash_payload(
    *,
    parent_hash: str | None,
    run_id: str,
    candidate_id: str | None,
    action_type: ControllerActionType,
    payload: dict[str, Any],
    artifact_refs: Iterable[ArtifactRef],
    timestamp: str,
    self_link_artifact_ids: set[str] | None = None,
    commit_hash: str | None = None,
) -> dict[str, Any]:
    """Return the canonical commit content used for hashing.

    Artifact references produced by the commit itself are represented by a stable
    ``<self>`` marker during hashing. This avoids a circular dependency while preserving
    deterministic hashes and stored self-links.
    """
    self_link_artifact_ids = self_link_artifact_ids or set()
    artifacts = [
        _artifact_for_hash(
            ref,
            commit_hash,
            self_link=ref.id in self_link_artifact_ids,
        )
        for ref in artifact_refs
    ]
    return {
        "parent_hash": parent_hash,
        "run_id": run_id,
        "candidate_id": candidate_id,
        "action_type": action_type.value,
        "payload": payload,
        "artifact_refs": artifacts,
        "timestamp": timestamp,
    }


def compute_commit_hash(
    *,
    parent_hash: str | None,
    run_id: str,
    candidate_id: str | None,
    action_type: ControllerActionType,
    payload: dict[str, Any],
    artifact_refs: Iterable[ArtifactRef],
    timestamp: str,
    self_link_artifact_ids: set[str] | None = None,
    commit_hash: str | None = None,
) -> str:
    """Compute the deterministic commit hash."""
    return sha256_json(
        commit_hash_payload(
            parent_hash=parent_hash,
            run_id=run_id,
            candidate_id=candidate_id,
            action_type=action_type,
            payload=payload,
            artifact_refs=artifact_refs,
            timestamp=timestamp,
            self_link_artifact_ids=self_link_artifact_ids,
            commit_hash=commit_hash,
        )
    )


class ResearchLedger:
    """Local append-only SQLite ledger."""

    def __init__(self, path: str | Path, *, clock: Clock | None = None) -> None:
        self.path = Path(path)
        self.clock = clock or SystemClock()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @classmethod
    def open_existing(cls, path: str | Path, *, clock: Clock | None = None) -> ResearchLedger:
        """Open an existing ledger without creating tables or changing the file."""
        ledger_path = Path(path)
        if not ledger_path.is_file():
            raise LedgerError(f"ledger does not exist: {ledger_path}")
        ledger = cls.__new__(cls)
        ledger.path = ledger_path
        ledger.clock = clock or SystemClock()
        return ledger

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _connect_read_only(self) -> sqlite3.Connection:
        """Open the existing database through SQLite's read-only URI mode."""
        connection = sqlite3.connect(
            f"{self.path.resolve().as_uri()}?mode=ro",
            uri=True,
        )
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS commits (
                    commit_hash TEXT PRIMARY KEY,
                    parent_hash TEXT REFERENCES commits(commit_hash),
                    run_id TEXT NOT NULL,
                    candidate_id TEXT,
                    action_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    artifact_refs_json TEXT NOT NULL,
                    timestamp TEXT NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS commits_no_update
                BEFORE UPDATE ON commits
                BEGIN
                    SELECT RAISE(ABORT, 'commits are append-only');
                END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS commits_no_delete
                BEFORE DELETE ON commits
                BEGIN
                    SELECT RAISE(ABORT, 'commits are append-only');
                END
                """
            )

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
    ) -> LedgerCommit:
        """Append one immutable commit.

        Parent hashes must already exist unless this is a root commit. Artifact references
        without a producing commit hash are treated as artifacts produced by this commit and
        are stored with a self-link after the commit hash is computed.
        """
        refs = list(artifact_refs)
        if parent_hash is not None and not self.has_commit(parent_hash):
            raise LedgerError(f"parent hash does not exist: {parent_hash}")

        timestamp = timestamp or (clock or self.clock).now()
        self_link_artifact_ids = {ref.id for ref in refs if ref.producing_commit_hash is None}
        commit_hash = compute_commit_hash(
            parent_hash=parent_hash,
            run_id=run_id,
            candidate_id=candidate_id,
            action_type=action_type,
            payload=payload,
            artifact_refs=refs,
            timestamp=timestamp,
            self_link_artifact_ids=self_link_artifact_ids,
        )
        linked_refs = [
            ref.model_copy(update={"producing_commit_hash": commit_hash})
            if ref.id in self_link_artifact_ids
            else ref
            for ref in refs
        ]
        commit = LedgerCommit(
            commit_hash=commit_hash,
            parent_hash=parent_hash,
            run_id=run_id,
            candidate_id=candidate_id,
            action_type=action_type,
            payload=payload,
            artifact_refs=linked_refs,
            timestamp=timestamp,
        )

        with self._connect() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO commits (
                        commit_hash,
                        parent_hash,
                        run_id,
                        candidate_id,
                        action_type,
                        payload_json,
                        artifact_refs_json,
                        timestamp
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        commit.commit_hash,
                        commit.parent_hash,
                        commit.run_id,
                        commit.candidate_id,
                        commit.action_type.value,
                        canonical_json(commit.payload),
                        canonical_json(commit.artifact_refs),
                        commit.timestamp,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise LedgerError(str(exc)) from exc
        return commit

    def has_commit(self, commit_hash: str) -> bool:
        """Return whether a commit exists."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM commits WHERE commit_hash = ?",
                (commit_hash,),
            ).fetchone()
        return row is not None

    def get_commit(self, commit_hash: str) -> LedgerCommit:
        """Load one commit by hash."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM commits WHERE commit_hash = ?",
                (commit_hash,),
            ).fetchone()
        if row is None:
            raise LedgerError(f"commit does not exist: {commit_hash}")
        return self._row_to_commit(row)

    def list_commits(self, run_id: str | None = None) -> list[LedgerCommit]:
        """Return commits in insertion order."""
        query = "SELECT rowid, * FROM commits"
        params: tuple[str, ...] = ()
        if run_id is not None:
            query += " WHERE run_id = ?"
            params = (run_id,)
        query += " ORDER BY rowid"
        with self._connect() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_commit(row) for row in rows]

    def list_commits_read_only(self, run_id: str | None = None) -> list[LedgerCommit]:
        """Return commits without opening SQLite in read-write mode."""
        query = "SELECT rowid, * FROM commits"
        params: tuple[str, ...] = ()
        if run_id is not None:
            query += " WHERE run_id = ?"
            params = (run_id,)
        query += " ORDER BY rowid"
        with self._connect_read_only() as connection:
            rows = connection.execute(query, params).fetchall()
        return [self._row_to_commit(row) for row in rows]

    def latest_commit_hash(self, run_id: str) -> str | None:
        """Return the latest commit hash for a run."""
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT commit_hash FROM commits
                WHERE run_id = ?
                ORDER BY rowid DESC
                LIMIT 1
                """,
                (run_id,),
            ).fetchone()
        return None if row is None else str(row["commit_hash"])

    def validate(self) -> None:
        """Validate parent links and deterministic commit hashes."""
        self.validate_snapshot(self.list_commits())

    @staticmethod
    def validate_snapshot(commits: list[LedgerCommit]) -> None:
        """Validate an explicitly supplied, insertion-ordered commit snapshot."""
        seen: set[str] = set()
        for commit in commits:
            if commit.parent_hash is not None and commit.parent_hash not in seen:
                raise LedgerError(f"missing or out-of-order parent hash: {commit.parent_hash}")
            recomputed = compute_commit_hash(
                parent_hash=commit.parent_hash,
                run_id=commit.run_id,
                candidate_id=commit.candidate_id,
                action_type=commit.action_type,
                payload=commit.payload,
                artifact_refs=commit.artifact_refs,
                timestamp=commit.timestamp,
                commit_hash=commit.commit_hash,
            )
            if recomputed != commit.commit_hash:
                raise LedgerError(f"commit hash mismatch: {commit.commit_hash}")
            seen.add(commit.commit_hash)

    def _row_to_commit(self, row: sqlite3.Row) -> LedgerCommit:
        artifact_refs = [
            ArtifactRef.model_validate(item) for item in json.loads(str(row["artifact_refs_json"]))
        ]
        return LedgerCommit(
            commit_hash=str(row["commit_hash"]),
            parent_hash=row["parent_hash"],
            run_id=str(row["run_id"]),
            candidate_id=row["candidate_id"],
            action_type=ControllerActionType(str(row["action_type"])),
            payload=json.loads(str(row["payload_json"])),
            artifact_refs=artifact_refs,
            timestamp=str(row["timestamp"]),
        )
