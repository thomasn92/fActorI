"""Library entry point for adding a deterministic candidate."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from factori.artifacts import ArtifactStore
from factori.commands import latest_parent, research_ledger
from factori.ledger import ResearchLedger
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    Candidate,
    ConstraintSet,
    ControllerActionType,
    DataRequirement,
    LedgerCommit,
)


@dataclass(frozen=True)
class AddCandidateCommandResult:
    """Result of the add-candidate library command."""

    candidate: Candidate
    artifact: ArtifactRef
    commit: LedgerCommit


def add_candidate(
    *,
    run_id: str,
    candidate_id: str,
    root: Path = Path("."),
    domain: str = "example-domain",
    question: str = "What deterministic MVP invariant is tested?",
    data_requirement: DataRequirement = DataRequirement.NO_DATA,
    store: ArtifactStore | None = None,
    ledger: ResearchLedger | None = None,
) -> AddCandidateCommandResult:
    """Add a candidate artifact and append the corresponding ledger commit."""
    store = store or ArtifactStore(root)
    ledger = ledger or research_ledger(root, run_id)
    store.init_run(run_id)
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
        parent_hash=latest_parent(ledger, run_id),
        action_type=ControllerActionType.ADD_CANDIDATE,
        payload={"candidate": candidate.model_dump(mode="json")},
        artifact_refs=[artifact],
    )
    linked_artifact = store.link_artifact_to_commit(artifact, commit.commit_hash)
    return AddCandidateCommandResult(
        candidate=candidate,
        artifact=linked_artifact,
        commit=commit,
    )


__all__ = ["AddCandidateCommandResult", "add_candidate"]
