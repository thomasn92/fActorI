"""Deterministic Stage C budget selector."""

from __future__ import annotations

from factori.schemas import BudgetSelectionReport, Candidate, DataRequirement, ScoreVector

DEFAULT_MAX_STAGE_C_CANDIDATES = 1
DEFAULT_COST_LAMBDA = 0.05


def normalized_cost(candidate: Candidate) -> float:
    """Return deterministic normalized verification cost."""
    return {
        DataRequirement.NO_DATA: 0.50,
        DataRequirement.SYNTHETIC_ONLY: 1.00,
        DataRequirement.PUBLIC_DOWNLOAD: 2.50,
        DataRequirement.USER_PROVIDED: 3.00,
    }[candidate.data_requirement]


def stage_c_cost_aware_score(
    candidate: Candidate,
    score: ScoreVector,
    *,
    cost_lambda: float = DEFAULT_COST_LAMBDA,
) -> float:
    """Return deterministic cost-aware Stage C selection score."""
    return round(score.base_score() / (1.0 + cost_lambda * normalized_cost(candidate)), 6)


def select_stage_c_budget(
    candidates: list[Candidate],
    scores: dict[str, ScoreVector],
    *,
    max_stage_c_candidates: int = DEFAULT_MAX_STAGE_C_CANDIDATES,
    cost_lambda: float = DEFAULT_COST_LAMBDA,
) -> tuple[list[Candidate], list[Candidate], BudgetSelectionReport]:
    """Select Stage C candidates by cost-aware score under a max count."""
    ranked = sorted(
        candidates,
        key=lambda candidate: (
            -stage_c_cost_aware_score(candidate, scores[candidate.id], cost_lambda=cost_lambda),
            candidate.id,
        ),
    )
    selected = ranked[:max_stage_c_candidates]
    budget_deferred = ranked[max_stage_c_candidates:]
    report = BudgetSelectionReport(
        max_stage_c_candidates=max_stage_c_candidates,
        selected_candidate_ids=[candidate.id for candidate in selected],
        budget_deferred_candidate_ids=[candidate.id for candidate in budget_deferred],
        cost_aware_scores={
            candidate.id: stage_c_cost_aware_score(
                candidate,
                scores[candidate.id],
                cost_lambda=cost_lambda,
            )
            for candidate in ranked
        },
    )
    return selected, budget_deferred, report
