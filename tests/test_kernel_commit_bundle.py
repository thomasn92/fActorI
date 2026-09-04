from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from factori.artifacts import ArtifactStore
from factori.kernel_bridge import commit_artifact_bundle
from factori.ledger import ResearchLedger
from factori.schemas import (
    ControllerActionType,
    KernelCommitBundleArtifact,
    KernelResponseStatus,
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


def _fixture(tmp_path: Path) -> tuple[str, ResearchLedger, str]:
    run_id = "run-bundle"
    ArtifactStore(tmp_path).init_run(run_id)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    root = ledger.append_commit(
        run_id=run_id,
        action_type=ControllerActionType.INIT_RUN,
        payload={},
        timestamp="2026-01-01T00:00:00Z",
    )
    return run_id, ledger, root.commit_hash


def test_commit_artifact_bundle_persists_artifacts_sidecars_and_commit(tmp_path: Path) -> None:
    run_id, ledger, tip = _fixture(tmp_path)
    artifacts = [
        KernelCommitBundleArtifact(
            artifact_id="candidate-1",
            artifact_type="candidate",
            json_value={"b": 2, "a": 1},
            metadata={"context": "test"},
        ),
        KernelCommitBundleArtifact(
            artifact_id="report-1",
            artifact_type="report",
            json_value=["ok", 1],
        ),
    ]

    response = commit_artifact_bundle(
        run_id,
        tip,
        artifacts,
        ControllerActionType.WRITE_ARTIFACT,
        {"artifact_ids": ["candidate-1", "report-1"]},
        timestamp="2026-01-01T00:00:01Z",
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status is KernelResponseStatus.ACCEPTED
    assert response.mutation_performed is True
    result = response.result
    assert result.artifact_count == 2  # type: ignore[union-attr]
    assert result.sidecar_count == 2  # type: ignore[union-attr]
    assert result.authority_granted is False  # type: ignore[union-attr]
    assert len(ledger.list_commits_read_only()) == 2
    for artifact in result.artifacts:  # type: ignore[union-attr]
        path = tmp_path / artifact.path
        assert path.is_file()
        assert (tmp_path / f"{artifact.path}.meta.json").is_file()
        assert artifact.producing_commit_hash == result.new_tip_hash  # type: ignore[union-attr]


def test_commit_artifact_bundle_rejects_existing_destination_without_mutation(
    tmp_path: Path,
) -> None:
    run_id, ledger, tip = _fixture(tmp_path)
    destination = tmp_path / "runs" / run_id / "candidates" / "candidate-1.json"
    destination.write_bytes(b"existing\n")
    response = commit_artifact_bundle(
        run_id,
        tip,
        [
            KernelCommitBundleArtifact(
                artifact_id="candidate-1", artifact_type="candidate", json_value={}
            )
        ],
        ControllerActionType.WRITE_ARTIFACT,
        {},
        timestamp="2026-01-01T00:00:01Z",
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )
    assert response.status is KernelResponseStatus.REJECTED
    assert response.mutation_performed is False
    assert destination.read_bytes() == b"existing\n"
    assert len(ledger.list_commits_read_only()) == 1
