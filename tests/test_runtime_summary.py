from __future__ import annotations

from factori.ledger import ResearchLedger
from factori.runtime_summary import compress_runtime_history
from factori.schemas import ControllerActionType, VerificationLabel


def test_runtime_summary_does_not_mutate_ledger(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    ledger.append_commit(
        run_id="run-1",
        candidate_id="candidate-1",
        action_type=ControllerActionType.CONTROLLER_ACTION,
        payload={"base_score": 0.50, "verification_label": VerificationLabel.UNSUPPORTED.value},
        timestamp="2026-01-01T00:00:00.000000Z",
    )
    before = ledger.list_commits("run-1")

    summary = compress_runtime_history(before)
    after = ledger.list_commits("run-1")

    assert before == after
    assert not summary.is_provenance
    assert summary.source_of_truth == "ledger"


def test_runtime_summary_is_deterministic() -> None:
    events = [
        {
            "candidate_id": "candidate-1",
            "action": "Score",
            "score": 0.50,
            "verification_label": VerificationLabel.UNSUPPORTED,
        },
        {
            "candidate_id": "candidate-1",
            "action": "Repair",
            "score": 0.54,
            "failed_repair": True,
        },
    ]

    first = compress_runtime_history(events)
    second = compress_runtime_history(events)

    assert first == second
    assert first.candidate_id == "candidate-1"
    assert first.action_count == 2
    assert first.failed_repair_count == 1
    assert first.best_score == 0.54
