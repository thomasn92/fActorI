from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

import pytest

from factori.artifacts import ArtifactStore
from factori.kernel_bridge import KernelBridgeError, link_artifact
from factori.ledger import ResearchLedger
from factori.protocols import PROTOCOL_VERSION
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    KernelMode,
    KernelResponseEnvelope,
    KernelResponseStatus,
    LedgerCommit,
)

KERNEL_BINARY = Path(__file__).parent.parent / "rust-kernel" / "target" / "debug" / "factori-kernel"


@pytest.fixture(scope="module", autouse=True)
def build_kernel_binary() -> None:
    subprocess.run(
        ["cargo", "build", "--manifest-path", "rust-kernel/Cargo.toml", "--locked", "--offline"],
        check=True,
        capture_output=True,
        text=True,
    )


def _fixture(
    tmp_path: Path,
    artifact_type: ArtifactType = ArtifactType.CANDIDATE,
) -> tuple[str, ArtifactRef, ResearchLedger, LedgerCommit]:
    run_id = "run-link"
    ref = ArtifactStore(tmp_path).write_json(
        run_id=run_id,
        artifact_id="artifact-1",
        artifact_type=artifact_type,
        data={"nested": [1, "é"]},
        metadata={"context": "test"},
    )
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    commit = ledger.append_commit(
        run_id=run_id,
        action_type=ControllerActionType.WRITE_ARTIFACT,
        payload={"artifact_id": ref.id},
        artifact_refs=[ref],
        timestamp="2026-01-01T00:00:00Z",
    )
    return run_id, ref, ledger, commit


def _request(
    run_id: str,
    ref: ArtifactRef,
    commit: LedgerCommit,
    *,
    producing_commit_hash: str | None = None,
) -> dict[str, object]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "artifact-link-test",
        "operation": "artifact.link",
        "mode": "DevelopmentCompatibility",
        "payload": {
            "run_id": run_id,
            "expected_ledger_tip_hash": commit.commit_hash,
            "artifact": ref.model_dump(mode="json"),
            "producing_commit_hash": producing_commit_hash or commit.commit_hash,
            "overwrite_policy": "FailIfExists",
        },
    }


def _run_kernel(root: Path, request: dict[str, object]) -> KernelResponseEnvelope:
    completed = subprocess.run(
        [str(KERNEL_BINARY), "--root", str(root)],
        input=json.dumps(request) + "\n",
        capture_output=True,
        check=True,
        text=True,
    )
    return KernelResponseEnvelope.model_validate_json(completed.stdout)


@pytest.mark.parametrize("artifact_type", list(ArtifactType))
@pytest.mark.parametrize("mode", list(KernelMode))
def test_link_artifact_writes_exact_canonical_sidecar(
    tmp_path: Path,
    artifact_type: ArtifactType,
    mode: KernelMode,
) -> None:
    run_id, ref, _, commit = _fixture(tmp_path, artifact_type)

    response = link_artifact(
        run_id,
        ref,
        commit.commit_hash,
        commit.commit_hash,
        mode=mode,
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status is KernelResponseStatus.ACCEPTED
    assert response.mutation_performed is True
    result = response.result
    assert result.artifact.producing_commit_hash == commit.commit_hash  # type: ignore[union-attr]
    sidecar = tmp_path / f"{ref.path}.meta.json"
    assert sidecar.read_bytes().endswith(b"\n")
    assert sidecar.read_bytes() == (
        sidecar.read_text(encoding="utf-8").rstrip("\n").encode("utf-8") + b"\n"
    )


def test_link_artifact_rejects_existing_sidecar_without_mutation(tmp_path: Path) -> None:
    run_id, ref, _, commit = _fixture(tmp_path)
    sidecar = tmp_path / f"{ref.path}.meta.json"
    sidecar.write_bytes(b"existing\n")

    with pytest.raises(KernelBridgeError, match="sidecar already exists"):
        link_artifact(
            run_id,
            ref,
            commit.commit_hash,
            commit.commit_hash,
            root=tmp_path,
            kernel_binary=KERNEL_BINARY,
        )
    assert sidecar.read_bytes() == b"existing\n"


def test_link_artifact_rejects_nonmatching_producer(tmp_path: Path) -> None:
    run_id, ref, _, commit = _fixture(tmp_path)

    with pytest.raises(KernelBridgeError, match="producing commit"):
        link_artifact(
            run_id,
            ref,
            "0" * 64,
            commit.commit_hash,
            root=tmp_path,
            kernel_binary=KERNEL_BINARY,
        )


@pytest.mark.parametrize("damage", ["missing_trigger", "foreign_run"])
def test_artifact_link_rejects_invalid_complete_ledger(tmp_path: Path, damage: str) -> None:
    run_id, ref, ledger, commit = _fixture(tmp_path)
    if damage == "missing_trigger":
        with sqlite3.connect(ledger.path) as connection:
            connection.execute("DROP TRIGGER commits_no_delete")
    else:
        ledger.append_commit(
            run_id="foreign-run",
            action_type=ControllerActionType.INIT_RUN,
            payload={},
            timestamp="2026-01-01T00:00:01Z",
        )

    response = _run_kernel(tmp_path, _request(run_id, ref, commit))

    assert response.status is KernelResponseStatus.REJECTED
    assert response.diagnostics[0].code == "artifact_link_ledger_invalid"
    assert response.mutation_performed is False
    assert not (tmp_path / f"{ref.path}.meta.json").exists()


def test_artifact_link_reports_missing_producer_commit(tmp_path: Path) -> None:
    run_id, ref, _, commit = _fixture(tmp_path)

    response = _run_kernel(
        tmp_path,
        _request(run_id, ref, commit, producing_commit_hash="0" * 64),
    )

    assert response.status is KernelResponseStatus.REJECTED
    assert response.diagnostics[0].code == "artifact_link_commit_missing"
    assert response.mutation_performed is False


def test_artifact_link_rejects_held_exclusive_lock_as_busy(tmp_path: Path) -> None:
    run_id, ref, ledger, commit = _fixture(tmp_path)
    connection = sqlite3.connect(ledger.path, timeout=0)
    connection.execute("BEGIN EXCLUSIVE")
    try:
        response = _run_kernel(tmp_path, _request(run_id, ref, commit))
    finally:
        connection.rollback()
        connection.close()

    assert response.status is KernelResponseStatus.REJECTED
    assert response.diagnostics[0].code == "artifact_link_busy"
    assert response.mutation_performed is False
    assert not (tmp_path / f"{ref.path}.meta.json").exists()


def test_artifact_link_concurrent_callers_have_one_winner(tmp_path: Path) -> None:
    run_id, ref, _, commit = _fixture(tmp_path)
    input_text = json.dumps(_request(run_id, ref, commit)) + "\n"
    processes = [
        subprocess.Popen(
            [str(KERNEL_BINARY), "--root", str(tmp_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    responses = [
        KernelResponseEnvelope.model_validate_json(process.communicate(input_text, timeout=10)[0])
        for process in processes
    ]

    assert [response.status for response in responses].count(KernelResponseStatus.ACCEPTED) == 1
    loser = next(
        response for response in responses if response.status is not KernelResponseStatus.ACCEPTED
    )
    assert loser.diagnostics[0].code == "artifact_link_target_exists"
    assert loser.mutation_performed is False
