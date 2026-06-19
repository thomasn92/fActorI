from __future__ import annotations

from factori.schemas import Candidate, DataRequirement
from factori.scoring import cost_aware_score, passes_stage_a_gate, score_candidate


def test_fake_scoring_is_deterministic() -> None:
    candidate = Candidate(
        id="cand-human-geography-optimal-transport-theory",
        domain="human geography",
        method="optimal transport",
        question="Can optimal transport expose structure in human geography?",
        data_requirement=DataRequirement.NO_DATA,
    )

    first = score_candidate(candidate)
    second = score_candidate(candidate)

    assert first == second
    assert passes_stage_a_gate(first)
    assert cost_aware_score(candidate, first) == cost_aware_score(candidate, second)


def test_real_data_scores_are_less_feasible() -> None:
    no_data = Candidate(
        id="no-data",
        domain="human geography",
        method="optimal transport",
        question="Can optimal transport expose structure in human geography?",
        data_requirement=DataRequirement.NO_DATA,
    )
    public_download = no_data.model_copy(
        update={"id": "public", "data_requirement": DataRequirement.PUBLIC_DOWNLOAD}
    )

    assert score_candidate(public_download).feasibility < score_candidate(no_data).feasibility
