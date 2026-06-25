"""Library entry point for the deterministic questioner check."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from factori.artifacts import ArtifactStore
from factori.commands import ensure_run_initialized, research_ledger
from factori.ledger import ResearchLedger
from factori.questioner import route_questions_to_action, routed_action, select_questions
from factori.schemas import (
    Candidate,
    ControllerAction,
    ControllerActionType,
    ControllerDecisionAction,
    DataRequirement,
    LedgerCommit,
    LiteratureState,
    Question,
    ScoreVector,
    VerificationState,
)


@dataclass(frozen=True)
class QuestionerCheckCommandResult:
    """Result of the questioner-check library command."""

    questions: list[Question]
    controller_action: ControllerAction
    routed_action: ControllerDecisionAction
    commit: LedgerCommit


def run_questioner_check(
    *,
    run_id: str,
    candidate_id: str,
    root: Path = Path("."),
    store: ArtifactStore | None = None,
    ledger: ResearchLedger | None = None,
) -> QuestionerCheckCommandResult:
    """Run the deterministic Strategic Questioner check and append its commit."""
    store = store or ArtifactStore(root)
    ledger = ledger or research_ledger(root, run_id)
    ensure_run_initialized(root=root, run_id=run_id, store=store, ledger=ledger)
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
    routed = routed_action(action)
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.QUESTIONER_CHECK,
        payload={
            "controller_action": action.model_dump(mode="json"),
            "routed_action": routed.value,
        },
    )
    return QuestionerCheckCommandResult(
        questions=questions,
        controller_action=action,
        routed_action=routed,
        commit=commit,
    )


__all__ = ["QuestionerCheckCommandResult", "run_questioner_check"]
