"""Deterministic fake baseline checks for Stage B."""

from __future__ import annotations

from factori.autonomy import human_required
from factori.schemas import (
    AutonomyContext,
    BaselineReport,
    Candidate,
    ControllerDecisionAction,
    ScoreVector,
)

BASELINE_STRENGTH_THRESHOLD = 0.60


def evaluate_baseline(
    candidate: Candidate,
    score: ScoreVector,
    *,
    autonomy_context: AutonomyContext | None = None,
) -> BaselineReport:
    """Evaluate deterministic fake baseline strength and route repair if needed."""
    baseline_strength = _baseline_strength(candidate)
    candidate_advantage = _candidate_advantage(candidate, score, baseline_strength)
    baseline_valid = (
        baseline_strength >= BASELINE_STRENGTH_THRESHOLD and candidate_advantage > 0
    )
    repairable = not baseline_valid and baseline_strength >= 0.35
    context = autonomy_context or AutonomyContext(
        candidate_id=candidate.id,
        decision_uncertainty=score.uncertainty,
        action_risk=score.difficulty,
        candidate_value=score.base_score(),
    )

    if baseline_valid:
        routed_action = ControllerDecisionAction.CONTINUE
    elif repairable and not human_required(context):
        routed_action = ControllerDecisionAction.STRENGTHEN_BASELINE
    elif repairable:
        routed_action = ControllerDecisionAction.ASK_HUMAN
    else:
        routed_action = ControllerDecisionAction.STOP_FAILURE

    return BaselineReport(
        candidate_id=candidate.id,
        baseline_strength=baseline_strength,
        candidate_score_advantage=candidate_advantage,
        baseline_valid=baseline_valid,
        repairable=repairable,
        routed_action=routed_action,
    )


def _baseline_strength(candidate: Candidate) -> float:
    variant_type = str(candidate.symbolic_state.get("variant_type", "narrow_scope"))
    if "weak-baseline" in candidate.id:
        return 0.45
    if "no-baseline" in candidate.id:
        return 0.25
    base = 0.62 if candidate.baseline else 0.35
    if variant_type == "stronger_baseline":
        base += 0.16
    elif variant_type == "synthetic_experiment_contract":
        base += 0.05
    elif variant_type == "theorem_or_conjecture_form":
        base += 0.04
    return round(min(1.0, base), 4)


def _candidate_advantage(
    candidate: Candidate,
    score: ScoreVector,
    baseline_strength: float,
) -> float:
    if "baseline-loses" in candidate.id:
        return -0.05
    return round(score.base_score() - (baseline_strength * 0.72), 6)
