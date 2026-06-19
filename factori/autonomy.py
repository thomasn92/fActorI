"""Deterministic autonomy contract."""

from __future__ import annotations

from factori.schemas import AutonomyContext

TAU_HUMAN = 0.75
TAU_RISK = 0.75


def human_required(
    context: AutonomyContext,
    *,
    tau_human: float = TAU_HUMAN,
    tau_risk: float = TAU_RISK,
) -> bool:
    """Return whether the autonomy contract requires human escalation."""
    return (
        context.decision_uncertainty > tau_human
        or context.action_risk > tau_risk
        or context.extra_budget_required
        or context.irreversible_decision
        or context.external_access_required
        or context.user_preference_needed
    )
