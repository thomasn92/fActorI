"""Deterministic Strategic Questioner Controller."""

from __future__ import annotations

from collections.abc import Iterable

from factori.autonomy import human_required
from factori.retrieval import compute_retrieval_adequacy
from factori.schemas import (
    AutonomyContext,
    Candidate,
    ControllerAction,
    ControllerActionType,
    ControllerDecisionAction,
    DataRequirement,
    LiteratureState,
    Question,
    QuestionCategory,
    ScoreVector,
    StagnationState,
    VerificationLabel,
    VerificationState,
)
from factori.stagnation import forced_stagnation_action

MICRO_QUESTIONS = [
    Question(
        id="micro-ledger-artifacts",
        category=QuestionCategory.MICRO_CHECK,
        prompt="Were the action, reason, and artifact references ledgered?",
        reason="every transition invariant",
    ),
    Question(
        id="micro-evidence-label",
        category=QuestionCategory.MICRO_CHECK,
        prompt="Did any verification label change, and is it supported by evidence?",
        reason="every transition invariant",
    ),
]

STAGE_GATE_QUESTIONS = {
    "stage_a": [
        Question(
            id="stage-a-clarity",
            category=QuestionCategory.CLARITY,
            prompt="Is the branch question narrow enough for cheap validation?",
            reason="Stage A gate",
        ),
        Question(
            id="stage-a-novelty",
            category=QuestionCategory.NOVELTY,
            prompt="Is the candidate meaningfully distinct from nearby branches?",
            reason="Stage A gate",
        ),
        Question(
            id="stage-a-data",
            category=QuestionCategory.DATA_SUFFICIENCY,
            prompt="Can the claim stay within NoData or SyntheticOnly MVP scope?",
            reason="Stage A gate",
        ),
    ],
    "stage_b": [
        Question(
            id="stage-b-baseline",
            category=QuestionCategory.BASELINE_STRENGTH,
            prompt="Is the baseline strong enough for structural validation?",
            reason="Stage B gate",
        ),
        Question(
            id="stage-b-literature",
            category=QuestionCategory.LITERATURE_ADEQUACY,
            prompt="Is retrieval adequacy sufficient for the current novelty claim?",
            reason="Stage B gate",
        ),
        Question(
            id="stage-b-simplicity",
            category=QuestionCategory.SIMPLICITY,
            prompt="Can the branch be simplified before expensive validation?",
            reason="Stage B gate",
        ),
    ],
    "stage_c": [
        Question(
            id="stage-c-evidence",
            category=QuestionCategory.EVIDENCE_SUFFICIENCY,
            prompt="What final label does the current evidence support?",
            reason="Stage C gate",
        ),
        Question(
            id="stage-c-verification",
            category=QuestionCategory.VERIFICATION_READINESS,
            prompt="Is the branch ready for formal or empirical verification?",
            reason="Stage C gate",
        ),
        Question(
            id="stage-c-stopping",
            category=QuestionCategory.STOPPING,
            prompt="Should the branch continue, downgrade, or stop?",
            reason="Stage C gate",
        ),
    ],
}

SYNTHESIS_QUESTIONS = [
    Question(
        id="synthesis-abstraction",
        category=QuestionCategory.ABSTRACTION,
        prompt="Do surviving branches share a valid abstraction?",
        reason="before synthesis",
    ),
    Question(
        id="synthesis-stopping",
        category=QuestionCategory.STOPPING,
        prompt="Is there an admissible final nucleus or should synthesis stop?",
        reason="before synthesis",
    ),
]

TRIGGER_QUESTIONS = {
    "high_complexity": Question(
        id="trigger-simplicity",
        category=QuestionCategory.SIMPLICITY,
        prompt="Can the claim be stated with fewer objects or assumptions?",
        reason="high complexity trigger",
    ),
    "weak_data": Question(
        id="trigger-data",
        category=QuestionCategory.DATA_SUFFICIENCY,
        prompt="Should the branch add synthetic data or downgrade the claim?",
        reason="weak data trigger",
    ),
    "weak_baseline": Question(
        id="trigger-baseline",
        category=QuestionCategory.BASELINE_STRENGTH,
        prompt="What stronger baseline is needed before continuing?",
        reason="weak baseline trigger",
    ),
    "recent_repair": Question(
        id="trigger-repair",
        category=QuestionCategory.REPAIR_SUFFICIENCY,
        prompt="Did the repair fix the stated flaw without hiding it?",
        reason="recent repair trigger",
    ),
    "low_retrieval_adequacy": Question(
        id="trigger-literature",
        category=QuestionCategory.LITERATURE_ADEQUACY,
        prompt="Which retrieval channel should be expanded?",
        reason="retrieval adequacy trigger",
    ),
    "verification_missing": Question(
        id="trigger-verification",
        category=QuestionCategory.VERIFICATION_READINESS,
        prompt="What minimum evidence blocks stronger verification?",
        reason="verification readiness trigger",
    ),
    "stagnation": Question(
        id="trigger-stopping",
        category=QuestionCategory.STOPPING,
        prompt="Is further budget likely to change the conclusion?",
        reason="stagnation trigger",
    ),
    "abstraction_candidate": Question(
        id="trigger-abstraction",
        category=QuestionCategory.ABSTRACTION,
        prompt="Are branches genuine instances of a common structure?",
        reason="abstraction trigger",
    ),
}


def select_questions(
    stage: str,
    candidate: Candidate,
    score: ScoreVector,
    literature_state: LiteratureState,
    verification_state: VerificationState,
    triggers: Iterable[str] | None = None,
) -> list[Question]:
    """Select a bounded deterministic set of questions for the current invocation."""
    del candidate, score, literature_state, verification_state
    normalized_stage = _normalize_stage(stage)
    selected = list(MICRO_QUESTIONS)

    if normalized_stage in STAGE_GATE_QUESTIONS:
        selected.extend(STAGE_GATE_QUESTIONS[normalized_stage])
    elif normalized_stage == "before_synthesis":
        selected.extend(SYNTHESIS_QUESTIONS)

    trigger_set = set(triggers or [])
    for trigger in sorted(trigger_set):
        question = TRIGGER_QUESTIONS.get(trigger)
        if question is not None:
            selected.append(question)

    return _dedupe_questions(selected)


def route_questions_to_action(
    selected_questions: list[Question],
    candidate: Candidate,
    score: ScoreVector,
    literature_state: LiteratureState,
    verification_state: VerificationState,
    *,
    autonomy_context: AutonomyContext | None = None,
    stagnation_state: StagnationState | None = None,
) -> ControllerAction:
    """Route selected questions to one deterministic controller action."""
    context = autonomy_context or autonomy_context_from_candidate(candidate, score)
    if human_required(context):
        routed_action = ControllerDecisionAction.ASK_HUMAN
        reason = "autonomy contract requires human escalation"
    elif stagnation_state is not None and stagnation_state.stagnant:
        routed_action = forced_stagnation_action(stagnation_state)
        reason = "stagnation requires forced resolution"
    else:
        routed_action, reason = _autonomous_route(
            selected_questions,
            candidate,
            score,
            literature_state,
            verification_state,
        )

    return ControllerAction(
        id=f"questioner-{candidate.id}-{routed_action.value}",
        action_type=ControllerActionType.CONTROLLER_ACTION,
        run_id="local-control",
        candidate_id=candidate.id,
        reason=reason,
        payload={
            "routed_action": routed_action.value,
            "question_ids": [question.id for question in selected_questions],
            "question_categories": [question.category.value for question in selected_questions],
            "human_required": routed_action == ControllerDecisionAction.ASK_HUMAN,
        },
    )


def autonomy_context_from_candidate(candidate: Candidate, score: ScoreVector) -> AutonomyContext:
    """Create the default autonomy context for deterministic routing."""
    return AutonomyContext(
        candidate_id=candidate.id,
        decision_uncertainty=score.uncertainty,
        action_risk=max(0.0, score.difficulty - 0.20),
        external_access_required=candidate.data_requirement == DataRequirement.USER_PROVIDED,
        candidate_value=score.base_score(),
    )


def routed_action(action: ControllerAction) -> ControllerDecisionAction:
    """Extract the routed decision action from a controller action."""
    return ControllerDecisionAction(str(action.payload["routed_action"]))


def _autonomous_route(
    selected_questions: list[Question],
    candidate: Candidate,
    score: ScoreVector,
    literature_state: LiteratureState,
    verification_state: VerificationState,
) -> tuple[ControllerDecisionAction, str]:
    categories = {question.category for question in selected_questions}
    retrieval_certificate = compute_retrieval_adequacy(literature_state)

    if QuestionCategory.STOPPING in categories and score.base_score() < 0.35:
        return ControllerDecisionAction.STOP_FAILURE, "low score with stopping trigger"
    if QuestionCategory.SIMPLICITY in categories and score.difficulty >= 0.65:
        return ControllerDecisionAction.SIMPLIFY, "complexity can be handled autonomously"
    if QuestionCategory.BASELINE_STRENGTH in categories and score.reviewer < 0.65:
        return ControllerDecisionAction.STRENGTHEN_BASELINE, "baseline weakness is routine"
    if (
        QuestionCategory.DATA_SUFFICIENCY in categories
        and candidate.data_requirement == DataRequirement.SYNTHETIC_ONLY
    ):
        return ControllerDecisionAction.ADD_SYNTHETIC_DATA, "synthetic data is inside MVP scope"
    if (
        QuestionCategory.DATA_SUFFICIENCY in categories
        and candidate.data_requirement == DataRequirement.PUBLIC_DOWNLOAD
    ):
        return ControllerDecisionAction.DOWNGRADE_CLAIM, "public real data is outside MVP scope"
    if QuestionCategory.LITERATURE_ADEQUACY in categories and not retrieval_certificate.passed:
        return (
            ControllerDecisionAction.INCREASE_RETRIEVAL_ADEQUACY,
            "retrieval adequacy is below threshold",
        )
    if (
        QuestionCategory.VERIFICATION_READINESS in categories
        and VerificationLabel.UNSUPPORTED in verification_state.labels
        and score.verifiability < 0.65
    ):
        return ControllerDecisionAction.DOWNGRADE_CLAIM, "unsupported weak evidence must downgrade"
    if QuestionCategory.ABSTRACTION in categories:
        return ControllerDecisionAction.ATTEMPT_ABSTRACTION, "abstraction question selected"
    if score.base_score() >= 0.65 and score.uncertainty <= 0.20:
        return ControllerDecisionAction.CONTINUE, "candidate can continue autonomously"
    return ControllerDecisionAction.NARROW_SCOPE, "default autonomous refinement"


def _normalize_stage(stage: str) -> str:
    return stage.strip().lower().replace("-", "_").replace(" ", "_")


def _dedupe_questions(questions: list[Question]) -> list[Question]:
    seen: set[str] = set()
    deduped: list[Question] = []
    for question in questions:
        if question.id in seen:
            continue
        seen.add(question.id)
        deduped.append(question)
    return deduped
