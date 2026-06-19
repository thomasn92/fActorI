from __future__ import annotations

from factori.schemas import ControllerDecisionAction, StagnationEvent, VerificationLabel
from factori.stagnation import compute_stagnation, forced_stagnation_action


def test_stagnation_is_detected_deterministically() -> None:
    history = [
        StagnationEvent(action="Refine", score=0.50),
        StagnationEvent(action="Repair", score=0.505),
        StagnationEvent(action="Repair", score=0.507),
        StagnationEvent(action="Repair", score=0.508),
    ]

    first = compute_stagnation(history, epsilon_score=0.01, window=4)
    second = compute_stagnation(history, epsilon_score=0.01, window=4)

    assert first == second
    assert first.stagnant
    assert first.stagnation_count == 3


def test_stagnation_forces_simplifying_or_terminal_action() -> None:
    state = compute_stagnation(
        [
            StagnationEvent(action="A", score=0.40),
            StagnationEvent(action="B", score=0.401),
            StagnationEvent(action="C", score=0.402),
            StagnationEvent(action="D", score=0.403),
        ],
        epsilon_score=0.01,
        window=4,
    )

    assert forced_stagnation_action(state) in {
        ControllerDecisionAction.SIMPLIFY,
        ControllerDecisionAction.DOWNGRADE_CLAIM,
        ControllerDecisionAction.CONVERT_TO_NEGATIVE_RESULT,
        ControllerDecisionAction.STOP_FAILURE,
    }


def test_verification_improvement_breaks_stagnation() -> None:
    state = compute_stagnation(
        [
            StagnationEvent(action="A", score=0.40),
            StagnationEvent(action="B", score=0.401),
            StagnationEvent(
                action="C",
                score=0.402,
                verification_label=VerificationLabel.CONJECTURE,
            ),
            StagnationEvent(
                action="D",
                score=0.403,
                verification_label=VerificationLabel.LEAN_VERIFIED,
            ),
        ],
        epsilon_score=0.01,
        window=4,
    )

    assert not state.stagnant
