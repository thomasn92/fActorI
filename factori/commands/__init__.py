"""Typed library entry points behind selected CLI commands."""

from __future__ import annotations

from pathlib import Path

from factori.artifacts import ArtifactStore
from factori.config import LEDGER_FILENAME
from factori.ledger import ResearchLedger
from factori.schemas import ControllerActionType


def ledger_path(root: Path, run_id: str) -> Path:
    """Return the default ledger path for one run."""
    return root / "runs" / run_id / LEDGER_FILENAME


def research_ledger(root: Path, run_id: str) -> ResearchLedger:
    """Construct the default local research ledger for one run."""
    return ResearchLedger(ledger_path(root, run_id))


def latest_parent(ledger: ResearchLedger, run_id: str) -> str | None:
    """Return the latest ledger parent for appending a command commit."""
    return ledger.latest_commit_hash(run_id)


def ensure_run_initialized(
    *,
    root: Path,
    run_id: str,
    store: ArtifactStore | None = None,
    ledger: ResearchLedger | None = None,
) -> None:
    """Create the run structure and deterministic root commit if absent."""
    store = store or ArtifactStore(root)
    ledger = ledger or research_ledger(root, run_id)
    store.init_run(run_id)
    if ledger.latest_commit_hash(run_id) is None:
        ledger.append_commit(
            run_id=run_id,
            action_type=ControllerActionType.INIT_RUN,
            payload={"run_id": run_id},
            timestamp="1970-01-01T00:00:00.000000Z",
        )


__all__ = [
    "ensure_run_initialized",
    "latest_parent",
    "ledger_path",
    "research_ledger",
]
