"""Minimal Typer CLI for the deterministic foundation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from factori.artifacts import ArtifactStore
from factori.config import DEFAULT_ROOT, DEFAULT_RUN_ID, LEDGER_FILENAME
from factori.ledger import LedgerError, ResearchLedger
from factori.questioner import route_questions_to_action, routed_action, select_questions
from factori.retrieval import compute_retrieval_adequacy
from factori.schemas import (
    ArtifactType,
    Candidate,
    ConstraintSet,
    ControllerActionType,
    DataRequirement,
    LiteratureState,
    ScoreVector,
    StagnationEvent,
    VerificationState,
)
from factori.stage_a import constraint_from_inputs, run_stage_a
from factori.stage_b import StageBError, run_stage_b
from factori.stagnation import compute_stagnation, forced_stagnation_action

app = typer.Typer(no_args_is_help=True)


def _ledger_path(root: Path, run_id: str) -> Path:
    return root / "runs" / run_id / LEDGER_FILENAME


def _ledger(root: Path, run_id: str) -> ResearchLedger:
    return ResearchLedger(_ledger_path(root, run_id))


def _latest_parent(ledger: ResearchLedger, run_id: str) -> str | None:
    return ledger.latest_commit_hash(run_id)


def _ensure_run_initialized(root: Path, run_id: str) -> None:
    store = ArtifactStore(root)
    store.init_run(run_id)
    ledger = _ledger(root, run_id)
    if ledger.latest_commit_hash(run_id) is None:
        ledger.append_commit(
            run_id=run_id,
            action_type=ControllerActionType.INIT_RUN,
            payload={"run_id": run_id},
            timestamp="1970-01-01T00:00:00.000000Z",
        )


@app.command("init-run")
def init_run(
    run_id: Annotated[str, typer.Option("--run-id")] = DEFAULT_RUN_ID,
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
) -> None:
    """Initialize a local run directory and root ledger commit."""
    previous_head = _ledger(root, run_id).latest_commit_hash(run_id)
    _ensure_run_initialized(root, run_id)
    if previous_head is None:
        commit = _ledger(root, run_id).list_commits(run_id)[0]
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


@app.command("run-stage-a")
def run_stage_a_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    domain: Annotated[str, typer.Option("--domain")],
    method: Annotated[str | None, typer.Option("--method")] = None,
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
) -> None:
    """Run deterministic fake Stage 0 and Stage A."""
    _ensure_run_initialized(root, run_id)
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    result = run_stage_a(
        run_id=run_id,
        constraints=constraint_from_inputs(domain=domain, method=method),
        store=store,
        ledger=ledger,
    )
    typer.echo(f"generated_candidates={len(result.generated_candidates)}")
    typer.echo(f"deferred_by_data_gate={len(result.deferred_candidates)}")
    typer.echo(f"pruned_duplicates={len(result.duplicate_decisions)}")
    typer.echo(f"passing_stage_a={len(result.survivors)}")
    typer.echo(f"stage_a_report={result.report_artifact.path}")


@app.command("run-stage-b")
def run_stage_b_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
) -> None:
    """Run deterministic fake Stage B structural validation."""
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = run_stage_b(run_id=run_id, store=store, ledger=ledger)
    except StageBError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"stage_a_survivors={len(result.stage_a_survivors)}")
    typer.echo(f"stage_b_children={len(result.children)}")
    typer.echo(f"rejected_bridge={len(result.rejected_bridge)}")
    typer.echo(f"rejected_review={len(result.rejected_review)}")
    typer.echo(f"rejected_baseline={len(result.rejected_baseline)}")
    typer.echo(f"insufficient_retrieval={len(result.insufficient_retrieval)}")
    typer.echo(f"passing_stage_b={len(result.survivors)}")
    typer.echo(f"stage_b_report={result.report_artifact.path}")


@app.command("questioner-check")
def questioner_check(
    run_id: Annotated[str, typer.Option("--run-id")],
    candidate_id: Annotated[str, typer.Option("--candidate-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
) -> None:
    """Run a deterministic Strategic Questioner check and ledger it."""
    _ensure_run_initialized(root, run_id)
    ledger = _ledger(root, run_id)
    candidate = Candidate(
        id=candidate_id,
        domain="demo-domain",
        method="demo-method",
        question="Should the deterministic control layer continue?",
        data_requirement=DataRequirement.SYNTHETIC_ONLY,
    )
    score = ScoreVector(
        novelty=0.52,
        feasibility=0.62,
        verifiability=0.58,
        reviewer=0.60,
        difficulty=0.52,
        diversity=0.50,
        uncertainty=0.10,
    )
    literature_state = LiteratureState(
        semantic=0.62,
        keyword=0.60,
        citation=0.55,
        diversity=0.58,
        adversarial=0.50,
        novelty_risk=0.30,
    )
    verification_state = VerificationState()
    questions = select_questions(
        "stage_b",
        candidate,
        score,
        literature_state,
        verification_state,
        triggers={"weak_data", "weak_baseline"},
    )
    action = route_questions_to_action(
        questions,
        candidate,
        score,
        literature_state,
        verification_state,
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.QUESTIONER_CHECK,
        payload={
            "controller_action": action.model_dump(mode="json"),
            "routed_action": routed_action(action).value,
        },
    )
    typer.echo(f"questions={len(questions)}")
    typer.echo(f"routed_action={routed_action(action).value}")
    typer.echo(f"commit_hash={commit.commit_hash}")


@app.command("retrieval-adequacy-demo")
def retrieval_adequacy_demo() -> None:
    """Print a deterministic retrieval adequacy certificate."""
    certificate = compute_retrieval_adequacy(
        LiteratureState(
            semantic=0.70,
            keyword=0.74,
            citation=0.66,
            diversity=0.62,
            adversarial=0.58,
            novelty_risk=0.25,
        )
    )
    typer.echo(json.dumps(certificate.model_dump(mode="json"), sort_keys=True))


@app.command("stagnation-demo")
def stagnation_demo() -> None:
    """Print a deterministic stagnation decision."""
    state = compute_stagnation(
        [
            StagnationEvent(action="Refine", score=0.50),
            StagnationEvent(action="Repair", score=0.505),
            StagnationEvent(action="Repair", score=0.507),
            StagnationEvent(action="Repair", score=0.508),
        ],
        epsilon_score=0.01,
        window=4,
    )
    typer.echo(json.dumps(state.model_dump(mode="json"), sort_keys=True))
    typer.echo(f"forced_action={forced_stagnation_action(state).value}")


def main() -> None:
    """Console-script entrypoint."""
    app()
