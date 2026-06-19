"""Deterministic global stagnation index."""

from __future__ import annotations

from collections.abc import Iterable

from factori.schemas import (
    ControllerDecisionAction,
    StagnationEvent,
    StagnationState,
    VerificationLabel,
)

N_STAG = 3
DEFAULT_EPSILON_SCORE = 0.01
DEFAULT_WINDOW = 4
HIGH_VALUE_THRESHOLD = 0.70
HIGH_UNCERTAINTY_THRESHOLD = 0.75

FORCED_STAGNATION_ACTIONS = [
    ControllerDecisionAction.SIMPLIFY,
    ControllerDecisionAction.DOWNGRADE_CLAIM,
    ControllerDecisionAction.CONVERT_TO_NEGATIVE_RESULT,
    ControllerDecisionAction.STOP_FAILURE,
]

VERIFICATION_RANK = {
    VerificationLabel.UNSUPPORTED: 0,
    VerificationLabel.LIMITATION: 1,
    VerificationLabel.NEGATIVE_RESULT: 2,
    VerificationLabel.CONJECTURE: 3,
    VerificationLabel.EXPERIMENT_VERIFIED: 4,
    VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED: 4,
    VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED: 5,
    VerificationLabel.LEAN_VERIFIED: 5,
}


def compute_stagnation(
    history: Iterable[StagnationEvent],
    epsilon_score: float = DEFAULT_EPSILON_SCORE,
    window: int = DEFAULT_WINDOW,
    *,
    n_stag: int = N_STAG,
    candidate_id: str | None = None,
    candidate_value: float = 0.0,
    decision_uncertainty: float = 0.0,
) -> StagnationState:
    """Compute stagnation over recent ledger-derived events."""
    events = list(history)
    recent_start = max(0, len(events) - window)
    stagnation_count = 0
    for index in range(recent_start, len(events)):
        if index == 0:
            continue
        previous = events[index - 1]
        current = events[index]
        if not _score_improved(previous, current, epsilon_score) and not _verification_improved(
            previous,
            current,
        ):
            stagnation_count += 1

    stagnant = stagnation_count >= n_stag
    high_value = candidate_value >= HIGH_VALUE_THRESHOLD
    high_uncertainty = decision_uncertainty > HIGH_UNCERTAINTY_THRESHOLD
    can_ask_human = stagnant and high_value and high_uncertainty
    return StagnationState(
        candidate_id=candidate_id,
        stagnation_count=stagnation_count,
        stagnant=stagnant,
        forced_actions=FORCED_STAGNATION_ACTIONS if stagnant and not can_ask_human else [],
        high_value=high_value,
        high_uncertainty=high_uncertainty,
        can_ask_human=can_ask_human,
    )


def forced_stagnation_action(state: StagnationState) -> ControllerDecisionAction:
    """Return the deterministic action for a stagnant branch."""
    if state.can_ask_human:
        return ControllerDecisionAction.ASK_HUMAN
    if ControllerDecisionAction.SIMPLIFY in state.forced_actions:
        return ControllerDecisionAction.SIMPLIFY
    return ControllerDecisionAction.CONTINUE


def _score_improved(
    previous: StagnationEvent,
    current: StagnationEvent,
    epsilon_score: float,
) -> bool:
    if previous.score is None or current.score is None:
        return False
    return current.score - previous.score >= epsilon_score


def _verification_improved(previous: StagnationEvent, current: StagnationEvent) -> bool:
    return VERIFICATION_RANK[current.verification_label] > VERIFICATION_RANK[
        previous.verification_label
    ]
