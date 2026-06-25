"""Library entry point for writing deterministic artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from factori.artifacts import ArtifactStore
from factori.commands import latest_parent, research_ledger
from factori.ledger import ResearchLedger
from factori.schemas import ArtifactRef, ArtifactType, ControllerActionType, LedgerCommit


@dataclass(frozen=True)
class WriteArtifactCommandResult:
    """Result of the write-artifact library command."""

    artifact: ArtifactRef
    commit: LedgerCommit
    format: str


def write_artifact(
    *,
    run_id: str,
    artifact_id: str,
    root: Path = Path("."),
    kind: ArtifactType = ArtifactType.REPORT,
    format_: str = "json",
    content: str | None = None,
    store: ArtifactStore | None = None,
    ledger: ResearchLedger | None = None,
) -> WriteArtifactCommandResult:
    """Write a JSON or Markdown artifact and append the corresponding ledger commit."""
    store = store or ArtifactStore(root)
    ledger = ledger or research_ledger(root, run_id)
    store.init_run(run_id)

    if format_ == "json":
        payload = {"content": content or "deterministic artifact"}
        artifact = store.write_json(
            run_id=run_id,
            artifact_id=artifact_id,
            artifact_type=kind,
            data=payload,
        )
    elif format_ in {"md", "markdown"}:
        artifact = store.write_markdown(
            run_id=run_id,
            artifact_id=artifact_id,
            artifact_type=kind,
            markdown=content or "# Deterministic Artifact\n",
        )
    else:
        raise ValueError("format must be json or markdown")

    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=latest_parent(ledger, run_id),
        action_type=ControllerActionType.WRITE_ARTIFACT,
        payload={"artifact_id": artifact_id, "kind": kind.value, "format": format_},
        artifact_refs=[artifact],
    )
    linked_artifact = store.link_artifact_to_commit(artifact, commit.commit_hash)
    return WriteArtifactCommandResult(
        artifact=linked_artifact,
        commit=commit,
        format=format_,
    )


__all__ = ["WriteArtifactCommandResult", "write_artifact"]
