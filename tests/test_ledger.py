from __future__ import annotations

import sqlite3

import pytest

from factori.ledger import LedgerError, ResearchLedger
from factori.schemas import ControllerActionType


def test_ledger_commits_are_append_only(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "ledger.sqlite")
    commit = ledger.append_commit(
        run_id="run-1",
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": "run-1"},
        timestamp="2026-01-01T00:00:00.000000Z",
    )

    with sqlite3.connect(tmp_path / "ledger.sqlite") as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE commits SET action_type = ? WHERE commit_hash = ?",
                ("Tamper", commit.commit_hash),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute("DELETE FROM commits WHERE commit_hash = ?", (commit.commit_hash,))


def test_commit_hashes_are_deterministic(tmp_path) -> None:
    kwargs = {
        "run_id": "run-1",
        "action_type": ControllerActionType.INIT_RUN,
        "payload": {"run_id": "run-1"},
        "timestamp": "2026-01-01T00:00:00.000000Z",
    }
    ledger_a = ResearchLedger(tmp_path / "a.sqlite")
    ledger_b = ResearchLedger(tmp_path / "b.sqlite")

    commit_a = ledger_a.append_commit(**kwargs)
    commit_b = ledger_b.append_commit(**kwargs)

    assert commit_a.commit_hash == commit_b.commit_hash


def test_parent_hash_must_exist(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "ledger.sqlite")

    with pytest.raises(LedgerError):
        ledger.append_commit(
            run_id="run-1",
            parent_hash="0" * 64,
            action_type=ControllerActionType.ADD_CANDIDATE,
            payload={},
            timestamp="2026-01-01T00:00:00.000000Z",
        )


def test_ledger_validates_hash_chain(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "ledger.sqlite")
    root = ledger.append_commit(
        run_id="run-1",
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": "run-1"},
        timestamp="2026-01-01T00:00:00.000000Z",
    )
    ledger.append_commit(
        run_id="run-1",
        parent_hash=root.commit_hash,
        action_type=ControllerActionType.ADD_CANDIDATE,
        payload={"candidate_id": "candidate-001"},
        timestamp="2026-01-01T00:00:01.000000Z",
    )

    ledger.validate()
