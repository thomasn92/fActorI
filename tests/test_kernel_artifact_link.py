from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from factori.artifacts import ArtifactStore
from factori.kernel_bridge import KernelBridgeError, link_artifact
from factori.ledger import ResearchLedger
from factori.schemas import ArtifactType, ControllerActionType, KernelResponseStatus

KERNEL_BINARY = Path(__file__).parent.parent / "rust-kernel" / "target" / "debug" / "factori-kernel"


@pytest.fixture(scope="module", autouse=True)
def build_kernel_binary() -> None:
    subprocess.run(
        ["cargo", "build", "--manifest-path", "rust-kernel/Cargo.toml", "--locked", "--offline"],
        check=True,
        capture_output=True,
        text=True,
    )


def _fixture(tmp_path: Path):
    run_id = "run-link"
    ref = ArtifactStore(tmp_path).write_json(
        run_id=run_id,
        artifact_id="artifact-1",
        artifact_type=ArtifactType.CANDIDATE,
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
    return run_id, ref, commit


def test_link_artifact_writes_exact_canonical_sidecar(tmp_path: Path) -> None:
    run_id, ref, commit = _fixture(tmp_path)

    response = link_artifact(
        run_id,
        ref,
        commit.commit_hash,
        commit.commit_hash,
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
    run_id, ref, commit = _fixture(tmp_path)
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
    run_id, ref, commit = _fixture(tmp_path)

    with pytest.raises(KernelBridgeError, match="producing commit"):
        link_artifact(
            run_id,
            ref,
            "0" * 64,
            commit.commit_hash,
            root=tmp_path,
            kernel_binary=KERNEL_BINARY,
        )
