from __future__ import annotations

from factori.bridge import compute_bridge_survival_score, run_bridge_check
from factori.schemas import BranchStatus, Candidate


def test_bridge_score_is_computed_correctly() -> None:
    score = compute_bridge_survival_score(
        map_score=0.80,
        transfer_score=0.70,
        baseline_score=0.60,
        data_score=0.50,
        falsify_score=0.90,
        nondecorative_score=0.40,
    )

    assert score == 0.665


def test_bridge_repair_is_attempted_at_most_once() -> None:
    candidate = Candidate(
        id="candidate-false-bridge",
        question="Does the bridge fail?",
        symbolic_state={"variant_type": "narrow_scope"},
    )

    report = run_bridge_check(candidate)

    assert report.repair_attempted
    assert report.repair_action is not None
    assert not report.survives
    assert report.final_status in {BranchStatus.FALSE_BRIDGE, BranchStatus.REJECTED_RED_TEAM}


def test_bridge_can_survive_without_repair() -> None:
    candidate = Candidate(
        id="candidate-valid-bridge",
        question="Does the bridge survive?",
        symbolic_state={"variant_type": "stronger_baseline"},
    )

    report = run_bridge_check(candidate)

    assert report.survives
    assert not report.repair_attempted
