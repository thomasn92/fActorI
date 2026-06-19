from __future__ import annotations

from factori.dedup import candidate_distance, deduplicate_candidates
from factori.schemas import Candidate, DataRequirement
from factori.scoring import score_candidate


def test_duplicate_candidates_are_pruned_deterministically() -> None:
    first = Candidate(
        id="candidate-a",
        domain="human geography",
        method="optimal transport",
        question="Can optimal transport expose structure in human geography?",
        hypothesis="The method reveals structure.",
        theory="Define the method over flows.",
        baseline="Compare with a direct baseline.",
        data_requirement=DataRequirement.NO_DATA,
    )
    duplicate = first.model_copy(update={"id": "candidate-b"})
    different = first.model_copy(
        update={
            "id": "candidate-c",
            "method": "spatial statistics",
            "question": "Can spatial statistics expose regional dependence?",
        }
    )
    candidates = [first, duplicate, different]
    scores = {candidate.id: score_candidate(candidate) for candidate in candidates}

    result = deduplicate_candidates(candidates, scores)

    assert candidate_distance(first, duplicate) == 0.0
    assert [candidate.id for candidate in result.kept] == ["candidate-a", "candidate-c"]
    assert [(decision.candidate_id, decision.duplicate_of) for decision in result.pruned] == [
        ("candidate-b", "candidate-a")
    ]


def test_dedup_result_is_stable_across_runs() -> None:
    candidates = [
        Candidate(
            id=f"candidate-{index}",
            domain="machine learning",
            method="calibration",
            question="Can calibration expose uncertainty?",
            data_requirement=DataRequirement.NO_DATA,
        )
        for index in range(3)
    ]
    scores = {candidate.id: score_candidate(candidate) for candidate in candidates}

    first = deduplicate_candidates(candidates, scores)
    second = deduplicate_candidates(candidates, scores)

    assert first == second
