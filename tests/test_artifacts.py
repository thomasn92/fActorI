from __future__ import annotations

from typer.testing import CliRunner

from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.config import RUN_SUBDIRECTORIES
from factori.hashing import sha256_file
from factori.ledger import ResearchLedger
from factori.schemas import ArtifactType, ControllerActionType


def test_artifacts_are_hashed_correctly(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    artifact = store.write_json(
        run_id="run-1",
        artifact_id="score-001",
        artifact_type=ArtifactType.SCORE,
        data={"score": 0.75},
    )

    assert artifact.content_hash == sha256_file(tmp_path / artifact.path)


def test_artifacts_are_linked_to_commits(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    artifact = store.write_json(
        run_id="run-1",
        artifact_id="candidate-001",
        artifact_type=ArtifactType.CANDIDATE,
        data={"id": "candidate-001"},
    )
    commit = ledger.append_commit(
        run_id="run-1",
        action_type=ControllerActionType.ADD_CANDIDATE,
        payload={"candidate_id": "candidate-001"},
        artifact_refs=[artifact],
        timestamp="2026-01-01T00:00:00.000000Z",
    )
    linked = store.link_artifact_to_commit(artifact, commit.commit_hash)

    assert linked.producing_commit_hash == commit.commit_hash
    assert (tmp_path / f"{linked.path}.meta.json").is_file()
    stored_commit = ledger.get_commit(commit.commit_hash)
    assert stored_commit.artifact_refs[0].producing_commit_hash == commit.commit_hash


def test_cli_init_run_creates_expected_folder_structure(tmp_path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["init-run", "--root", str(tmp_path), "--run-id", "run-1"])

    assert result.exit_code == 0
    for directory in RUN_SUBDIRECTORIES:
        assert (tmp_path / "runs" / "run-1" / directory).is_dir()
