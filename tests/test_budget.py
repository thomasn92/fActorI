from __future__ import annotations

from factori.budget import (
    normalized_cost,
    select_stage_c_budget,
    stage_c_cost_aware_score,
)
from factori.schemas import Candidate, DataRequirement, ScoreVector


def test_budget_selector_respects_max_stage_c_candidates() -> None:
    candidates = [
        _candidate("candidate-a", DataRequirement.NO_DATA),
        _candidate("candidate-b", DataRequirement.SYNTHETIC_ONLY),
        _candidate("candidate-c", DataRequirement.NO_DATA),
    ]
    scores = {
        "candidate-a": _score(0.68),
        "candidate-b": _score(0.82),
        "candidate-c": _score(0.70),
    }

    selected, budget_deferred, report = select_stage_c_budget(
        candidates,
        scores,
        max_stage_c_candidates=1,
    )

    assert len(selected) == 1
    assert len(budget_deferred) == 2
    assert report.selected_candidate_ids == [selected[0].id]
    assert report.budget_deferred_candidate_ids == [
        candidate.id for candidate in budget_deferred
    ]


def test_cost_aware_ranking_is_deterministic() -> None:
    no_data = _candidate("candidate-no-data", DataRequirement.NO_DATA)
    synthetic = _candidate("candidate-synthetic", DataRequirement.SYNTHETIC_ONLY)
    scores = {
        no_data.id: _score(0.60),
        synthetic.id: _score(0.61),
    }

    first = select_stage_c_budget([synthetic, no_data], scores, max_stage_c_candidates=1)
    second = select_stage_c_budget([synthetic, no_data], scores, max_stage_c_candidates=1)

    assert first == second
    assert first[0] == [no_data]
    assert normalized_cost(no_data) < normalized_cost(synthetic)
    assert stage_c_cost_aware_score(no_data, scores[no_data.id]) > stage_c_cost_aware_score(
        synthetic,
        scores[synthetic.id],
    )


def _candidate(candidate_id: str, data_requirement: DataRequirement) -> Candidate:
    return Candidate(
        id=candidate_id,
        question=f"Can {candidate_id} pass Stage C selection?",
        data_requirement=data_requirement,
    )


def _score(base: float) -> ScoreVector:
    return ScoreVector(
        novelty=base,
        feasibility=base,
        verifiability=base,
        reviewer=base,
        difficulty=1.0 - base,
        diversity=base,
    )
