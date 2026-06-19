from __future__ import annotations

from factori.autonomy import human_required
from factori.schemas import AutonomyContext


def test_ordinary_context_does_not_require_human() -> None:
    context = AutonomyContext(
        decision_uncertainty=0.20,
        action_risk=0.30,
        candidate_value=0.50,
    )

    assert not human_required(context)


def test_tail_risk_conditions_require_human() -> None:
    assert human_required(AutonomyContext(decision_uncertainty=0.90))
    assert human_required(AutonomyContext(action_risk=0.90))
    assert human_required(AutonomyContext(extra_budget_required=True))
    assert human_required(AutonomyContext(irreversible_decision=True))
    assert human_required(AutonomyContext(external_access_required=True))
    assert human_required(AutonomyContext(user_preference_needed=True))
