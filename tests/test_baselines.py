from __future__ import annotations

from factori.baselines import evaluate_baseline
from factori.schemas import Candidate, ControllerDecisionAction, ScoreVector


def test_baseline_failure_routes_to_strengthen_baseline_when_repairable() -> None:
    candidate = Candidate(
        id="candidate-weak-baseline",
        question="Does weak baseline route to repair?",
        baseline="weak baseline",
        symbolic_state={"variant_type": "narrow_scope"},
    )
    score = ScoreVector(
        novelty=0.65,
        feasibility=0.80,
        verifiability=0.75,
        reviewer=0.60,
        difficulty=0.40,
        diversity=0.50,
        uncertainty=0.10,
    )

    report = evaluate_baseline(candidate, score)

    assert not report.baseline_valid
    assert report.repairable
    assert report.routed_action == ControllerDecisionAction.STRENGTHEN_BASELINE


def test_baseline_valid_when_strength_and_advantage_pass() -> None:
    candidate = Candidate(
        id="candidate-strong-baseline",
        question="Does strong baseline pass?",
        baseline="strong deterministic baseline",
        symbolic_state={"variant_type": "stronger_baseline"},
    )
    score = ScoreVector(
        novelty=0.80,
        feasibility=0.85,
        verifiability=0.82,
        reviewer=0.75,
        difficulty=0.35,
        diversity=0.60,
    )

    report = evaluate_baseline(candidate, score)

    assert report.baseline_valid
    assert report.routed_action == ControllerDecisionAction.CONTINUE
