"""Minimal Typer CLI for the deterministic foundation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from factori.artifacts import ArtifactStore
from factori.config import DEFAULT_ROOT, DEFAULT_RUN_ID, LEDGER_FILENAME
from factori.ledger import LedgerError, ResearchLedger
from factori.schemas import (
    ArtifactType,
    Candidate,
    ConstraintSet,
    ControllerActionType,
    DataRequirement,
)

app = typer.Typer(no_args_is_help=True)


def _ledger_path(root: Path, run_id: str) -> Path:
    return root / "runs" / run_id / LEDGER_FILENAME


def _ledger(root: Path, run_id: str) -> ResearchLedger:
    return ResearchLedger(_ledger_path(root, run_id))


def _latest_parent(ledger: ResearchLedger, run_id: str) -> str | None:
    return ledger.latest_commit_hash(run_id)


@app.command("init-run")
def init_run(
    run_id: Annotated[str, typer.Option("--run-id")] = DEFAULT_RUN_ID,
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
) -> None:
    """Initialize a local run directory and root ledger commit."""
    store = ArtifactStore(root)
    store.init_run(run_id)
    ledger = _ledger(root, run_id)
    if ledger.latest_commit_hash(run_id) is None:
        commit = ledger.append_commit(
            run_id=run_id,
            action_type=ControllerActionType.INIT_RUN,
            payload={"run_id": run_id},
            timestamp="1970-01-01T00:00:00.000000Z",
        )
        typer.echo(f"initialized {run_id} {commit.commit_hash}")
    else:
        typer.echo(f"initialized {run_id}")


@app.command("add-candidate")
def add_candidate(
    run_id: Annotated[str, typer.Option("--run-id")] = DEFAULT_RUN_ID,
    candidate_id: Annotated[str, typer.Option("--candidate-id")] = "candidate-001",
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    domain: Annotated[str, typer.Option("--domain")] = "example-domain",
    question: Annotated[str, typer.Option("--question")] = (
        "What deterministic MVP invariant is tested?"
    ),
    data_requirement: Annotated[
        DataRequirement,
        typer.Option("--data-requirement", case_sensitive=True),
    ] = DataRequirement.NO_DATA,
) -> None:
    """Add a deterministic example candidate and ledger it."""
    store = ArtifactStore(root)
    store.init_run(run_id)
    ledger = _ledger(root, run_id)
    candidate = Candidate(
        id=candidate_id,
        constraints=ConstraintSet(
            domain=domain,
            question=question,
            data_requirement=data_requirement,
        ),
        domain=domain,
        question=question,
        data_requirement=data_requirement,
    )
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=candidate_id,
        artifact_type=ArtifactType.CANDIDATE,
        data=candidate,
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=candidate_id,
        parent_hash=_latest_parent(ledger, run_id),
        action_type=ControllerActionType.ADD_CANDIDATE,
        payload={"candidate": candidate.model_dump(mode="json")},
        artifact_refs=[artifact],
    )
    store.link_artifact_to_commit(artifact, commit.commit_hash)
    typer.echo(f"added {candidate_id} {commit.commit_hash}")


@app.command("show-ledger")
def show_ledger(
    run_id: Annotated[str, typer.Option("--run-id")] = DEFAULT_RUN_ID,
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
) -> None:
    """Print ledger commits as JSON lines."""
    ledger = _ledger(root, run_id)
    for commit in ledger.list_commits(run_id):
        typer.echo(json.dumps(commit.model_dump(mode="json"), sort_keys=True))


@app.command("write-artifact")
def write_artifact(
    run_id: Annotated[str, typer.Option("--run-id")] = DEFAULT_RUN_ID,
    artifact_id: Annotated[str, typer.Option("--artifact-id")] = "artifact-001",
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    kind: Annotated[ArtifactType, typer.Option("--kind", case_sensitive=True)] = (
        ArtifactType.REPORT
    ),
    format_: Annotated[str, typer.Option("--format")] = "json",
    content: Annotated[str | None, typer.Option("--content")] = None,
) -> None:
    """Write a JSON or Markdown artifact and ledger it."""
    store = ArtifactStore(root)
    store.init_run(run_id)
    ledger = _ledger(root, run_id)

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
        raise typer.BadParameter("format must be json or markdown")

    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=_latest_parent(ledger, run_id),
        action_type=ControllerActionType.WRITE_ARTIFACT,
        payload={"artifact_id": artifact_id, "kind": kind.value, "format": format_},
        artifact_refs=[artifact],
    )
    store.link_artifact_to_commit(artifact, commit.commit_hash)
    typer.echo(f"wrote {artifact.path} {commit.commit_hash}")


@app.command("validate-run")
def validate_run(
    run_id: Annotated[str, typer.Option("--run-id")] = DEFAULT_RUN_ID,
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
) -> None:
    """Validate the local run directory and ledger invariants."""
    store = ArtifactStore(root)
    store.validate_run_structure(run_id)
    ledger = _ledger(root, run_id)
    try:
        ledger.validate()
    except LedgerError as exc:
        raise typer.Exit(code=1) from exc
    typer.echo(f"valid {run_id}")


def main() -> None:
    """Console-script entrypoint."""
    app()
