from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from factori.artifacts import ArtifactStore
from factori.kernel_bridge import KernelBridgeError, append_ledger_commit
from factori.ledger import ResearchLedger, compute_commit_hash
from factori.protocols import PROTOCOL_VERSION
from factori.schemas import (
    ControllerActionType,
    KernelLedgerAppendResult,
    KernelResponseEnvelope,
    KernelResponseStatus,
)

KERNEL_BINARY = Path(__file__).parent / ".." / "rust-kernel" / "target" / "debug" / "factori-kernel"


@pytest.fixture(scope="module", autouse=True)
def build_kernel_binary() -> None:
    subprocess.run(
        ["cargo", "build", "--manifest-path", "rust-kernel/Cargo.toml", "--locked", "--offline"],
        capture_output=True,
        check=True,
        text=True,
    )


def _init_ledger(tmp_path: Path, run_id: str = "run-append") -> tuple[Path, str]:
    ArtifactStore(tmp_path).init_run(run_id)
    path = tmp_path / "runs" / run_id / "ledger.sqlite"
    root = ResearchLedger(path).append_commit(
        run_id=run_id,
        action_type=ControllerActionType.INIT_RUN,
        payload={},
        timestamp="2026-01-01T00:00:00Z",
    )
    return path, root.commit_hash


def _request(
    tip: str,
    *,
    run_id: str = "run-append",
    action_type: str = "AddCandidate",
    payload: object | None = None,
    candidate_id: str | None = None,
    timestamp: str = "2026-01-01T00:00:01Z",
    mode: str = "DevelopmentCompatibility",
) -> dict[str, object]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "ledger-append-test",
        "operation": "ledger.append",
        "mode": mode,
        "payload": {
            "run_id": run_id,
            "expected_tip_hash": tip,
            "action_type": action_type,
            "payload": {} if payload is None else payload,
            "candidate_id_optional": candidate_id,
            "timestamp": timestamp,
        },
    }


def _run_kernel(root: Path, request: dict[str, object]) -> KernelResponseEnvelope:
    completed = subprocess.run(
        [str(KERNEL_BINARY), "--root", str(root)],
        input=json.dumps(request) + "\n",
        capture_output=True,
        check=True,
        text=True,
        timeout=10,
    )
    return KernelResponseEnvelope.model_validate_json(completed.stdout)


def test_ledger_append_bridge_matches_python_hash_and_canonical_row(tmp_path: Path) -> None:
    path, tip = _init_ledger(tmp_path)
    payload = {"unicode": "é水", "nested": {"b": 2, "a": [1, -0.0]}, "empty": {}}

    response = append_ledger_commit(
        "run-append",
        tip,
        ControllerActionType.ADD_CANDIDATE,
        payload,
        candidate_id_optional="candidate-1",
        timestamp="2026-01-01T00:00:01.123456Z",
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status == KernelResponseStatus.ACCEPTED
    assert response.mutation_performed is True
    assert isinstance(response.result, KernelLedgerAppendResult)
    expected_hash = compute_commit_hash(
        parent_hash=tip,
        run_id="run-append",
        candidate_id="candidate-1",
        action_type=ControllerActionType.ADD_CANDIDATE,
        payload=payload,
        artifact_refs=[],
        timestamp="2026-01-01T00:00:01.123456Z",
    )
    assert response.result.new_tip_hash == expected_hash
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT payload_json, artifact_refs_json FROM commits ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
    assert row == ('{"empty":{},"nested":{"a":[1,-0.0],"b":2},"unicode":"é水"}', "[]")


def test_ledger_append_accepts_every_non_root_action_type(tmp_path: Path) -> None:
    path, tip = _init_ledger(tmp_path)
    expected_count = 1
    for action_type in ControllerActionType:
        if action_type is ControllerActionType.INIT_RUN:
            continue
        response = _run_kernel(
            tmp_path,
            _request(tip, action_type=action_type.value, payload={"action": action_type.value}),
        )
        assert response.status == KernelResponseStatus.ACCEPTED, action_type
        assert isinstance(response.result, KernelLedgerAppendResult)
        tip = response.result.new_tip_hash
        expected_count += 1
        assert response.result.commit_count_after == expected_count
    commits = ResearchLedger.open_existing(path).list_commits_read_only("run-append")
    ResearchLedger.validate_snapshot(commits)
    assert len(commits) == expected_count


@pytest.mark.parametrize("mode", ["DevelopmentCompatibility", "StrictProduction"])
def test_ledger_append_modes_have_identical_semantics(tmp_path: Path, mode: str) -> None:
    _, tip = _init_ledger(tmp_path)
    response = _run_kernel(tmp_path, _request(tip, mode=mode))

    assert response.status == KernelResponseStatus.ACCEPTED
    assert isinstance(response.result, KernelLedgerAppendResult)
    assert response.result.authority_granted is False
    assert response.result.linked_artifact_count == 0


@pytest.mark.parametrize(
    ("update", "code"),
    [
        ({"run_id": "../escape"}, "ledger_append_payload_invalid"),
        ({"expected_tip_hash": "A" * 64}, "ledger_append_payload_invalid"),
        ({"action_type": "InitRun"}, "ledger_append_payload_invalid"),
        ({"action_type": "Unknown"}, "ledger_append_payload_invalid"),
        ({"payload": []}, "ledger_append_payload_invalid"),
        ({"candidate_id_optional": "../escape"}, "ledger_append_payload_invalid"),
        ({"timestamp": "2026-02-29T00:00:00Z"}, "ledger_append_payload_invalid"),
        ({"timestamp": "2026-01-01T00:00:00+00:00"}, "ledger_append_payload_invalid"),
    ],
)
def test_ledger_append_rejects_invalid_payload_without_mutation(
    tmp_path: Path, update: dict[str, object], code: str
) -> None:
    path, tip = _init_ledger(tmp_path)
    before = path.read_bytes()
    request = _request(tip)
    assert isinstance(request["payload"], dict)
    request["payload"].update(update)

    response = _run_kernel(tmp_path, request)

    assert response.status == KernelResponseStatus.REJECTED
    assert response.diagnostics[0].code == code
    assert response.mutation_performed is False
    assert path.read_bytes() == before


def test_ledger_append_rejects_stale_tip_and_bridge_fails_preflight(tmp_path: Path) -> None:
    path, tip = _init_ledger(tmp_path)
    stale = "0" * 64
    before = path.read_bytes()

    response = _run_kernel(tmp_path, _request(stale))

    assert response.status == KernelResponseStatus.REJECTED
    assert response.diagnostics[0].code == "ledger_append_tip_mismatch"
    assert path.read_bytes() == before
    with pytest.raises(KernelBridgeError, match="expected tip"):
        append_ledger_commit(
            "run-append",
            stale,
            ControllerActionType.ADD_CANDIDATE,
            {},
            timestamp="2026-01-01T00:00:01Z",
            root=tmp_path,
            kernel_binary=KERNEL_BINARY,
        )
    assert tip != stale


def test_ledger_append_rejects_noncanonical_existing_row(tmp_path: Path) -> None:
    path, tip = _init_ledger(tmp_path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER commits_no_update")
        connection.execute("UPDATE commits SET payload_json = '{ }'")
        connection.execute(
            """CREATE TRIGGER commits_no_update BEFORE UPDATE ON commits BEGIN
            SELECT RAISE(ABORT, 'commits are append-only'); END"""
        )
    before = path.read_bytes()

    response = _run_kernel(tmp_path, _request(tip))

    assert response.status == KernelResponseStatus.REJECTED
    assert response.diagnostics[0].code == "ledger_append_ledger_invalid"
    assert response.mutation_performed is False
    assert path.read_bytes() == before


@pytest.mark.parametrize("damage", ["trigger", "foreign_key", "auxiliary"])
def test_ledger_append_rejects_damaged_ledger_contract(tmp_path: Path, damage: str) -> None:
    path, tip = _init_ledger(tmp_path)
    if damage == "trigger":
        with sqlite3.connect(path) as connection:
            connection.execute("DROP TRIGGER commits_no_delete")
    elif damage == "foreign_key":
        path.unlink()
        with sqlite3.connect(path) as connection:
            connection.executescript(
                """CREATE TABLE commits (
                commit_hash TEXT PRIMARY KEY, parent_hash TEXT, run_id TEXT NOT NULL,
                candidate_id TEXT, action_type TEXT NOT NULL, payload_json TEXT NOT NULL,
                artifact_refs_json TEXT NOT NULL, timestamp TEXT NOT NULL);
                CREATE TRIGGER commits_no_update BEFORE UPDATE ON commits BEGIN
                SELECT RAISE(ABORT, 'commits are append-only'); END;
                CREATE TRIGGER commits_no_delete BEFORE DELETE ON commits BEGIN
                SELECT RAISE(ABORT, 'commits are append-only'); END;"""
            )
            connection.execute(
                "INSERT INTO commits VALUES "
                "(?, NULL, 'run-append', NULL, 'InitRun', '{}', '[]', ?)",
                (tip, "2026-01-01T00:00:00Z"),
            )
    else:
        path.with_name("ledger.sqlite-journal").write_bytes(b"persistent")
    before = {item.name: item.read_bytes() for item in path.parent.iterdir() if item.is_file()}

    response = _run_kernel(tmp_path, _request(tip))

    assert response.status == KernelResponseStatus.REJECTED
    assert response.diagnostics[0].code == "ledger_append_ledger_invalid"
    assert response.mutation_performed is False
    after = {item.name: item.read_bytes() for item in path.parent.iterdir() if item.is_file()}
    assert after == before


def test_ledger_append_rejects_held_immediate_lock(tmp_path: Path) -> None:
    path, tip = _init_ledger(tmp_path)
    connection = sqlite3.connect(path, timeout=0)
    connection.execute("BEGIN IMMEDIATE")
    try:
        response = _run_kernel(tmp_path, _request(tip))
    finally:
        connection.rollback()
        connection.close()

    assert response.status == KernelResponseStatus.REJECTED
    assert response.diagnostics[0].code == "ledger_append_busy"
    assert response.mutation_performed is False


def test_ledger_append_concurrent_callers_create_one_linear_extension(tmp_path: Path) -> None:
    path, tip = _init_ledger(tmp_path)
    input_text = json.dumps(_request(tip)) + "\n"
    processes = [
        subprocess.Popen(
            [str(KERNEL_BINARY), "--root", str(tmp_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(3)
    ]
    outputs = [process.communicate(input_text, timeout=10)[0] for process in processes]
    responses = [KernelResponseEnvelope.model_validate_json(output) for output in outputs]

    assert sum(response.status == KernelResponseStatus.ACCEPTED for response in responses) == 1
    assert all(
        response.status == KernelResponseStatus.ACCEPTED
        or response.diagnostics[0].code in {"ledger_append_busy", "ledger_append_tip_mismatch"}
        for response in responses
    )
    commits = ResearchLedger.open_existing(path).list_commits_read_only("run-append")
    assert len(commits) == 2
    assert commits[1].parent_hash == tip
    ResearchLedger.validate_snapshot(commits)


def test_ledger_append_response_schema_rejects_coerced_authority_flags() -> None:
    tip = "0" * 64
    new = "1" * 64
    response = {
        "protocol_version": PROTOCOL_VERSION,
        "kernel_version": "0.1.0-dev",
        "request_id": "ledger-append-test",
        "operation": "ledger.append",
        "mode": "DevelopmentCompatibility",
        "status": "accepted",
        "result": {
            "commit": {
                "commit_hash": new,
                "parent_hash": tip,
                "run_id": "run-append",
                "candidate_id": None,
                "action_type": "AddCandidate",
                "payload": {},
                "artifact_refs": [],
                "timestamp": "2026-01-01T00:00:01Z",
            },
            "previous_tip_hash": tip,
            "new_tip_hash": new,
            "commit_count_before": 1,
            "commit_count_after": 2,
            "appended": 1,
            "linked_artifact_count": False,
            "authority_granted": 0,
        },
        "diagnostics": [],
        "mutation_performed": True,
    }

    with pytest.raises(ValidationError):
        KernelResponseEnvelope.model_validate(response)
