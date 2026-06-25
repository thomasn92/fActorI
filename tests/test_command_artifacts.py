from __future__ import annotations

import inspect

from typer.testing import CliRunner

from factori.cli import app
from factori.commands.artifacts import WriteArtifactCommandResult, write_artifact
from factori.hashing import sha256_file
from factori.ledger import ResearchLedger
from factori.schemas import ArtifactType, ControllerActionType


def test_write_artifact_library_entry_point_works_and_links_commit(tmp_path, capsys) -> None:
    result = write_artifact(
        run_id="run-1",
        artifact_id="artifact-1",
        root=tmp_path,
        kind=ArtifactType.REPORT,
        format_="json",
        content="library content",
    )

    captured = capsys.readouterr()
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    commits = ledger.list_commits("run-1")
    artifact_path = tmp_path / result.artifact.path

    assert isinstance(result, WriteArtifactCommandResult)
    assert captured.out == ""
    assert captured.err == ""
    assert artifact_path.is_file()
    assert result.artifact.content_hash == sha256_file(artifact_path)
    assert result.artifact.producing_commit_hash == result.commit.commit_hash
    assert commits == [result.commit]
    assert commits[0].action_type == ControllerActionType.WRITE_ARTIFACT


def test_write_artifact_library_rejects_invalid_format_without_typer_exit(tmp_path) -> None:
    source = inspect.getsource(write_artifact)

    assert "typer.Exit" not in source
    try:
        write_artifact(
            run_id="run-1",
            artifact_id="artifact-1",
            root=tmp_path,
            format_="txt",
        )
    except ValueError as exc:
        assert str(exc) == "format must be json or markdown"
    else:
        raise AssertionError("invalid format should fail")


def test_write_artifact_cli_still_works(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "write-artifact",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--artifact-id",
            "artifact-1",
            "--format",
            "markdown",
            "--content",
            "# CLI Artifact\n",
        ],
    )

    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    commits = ledger.list_commits("run-1")

    assert result.exit_code == 0, result.output
    assert "wrote runs/run-1/reports/artifact-1.md " in result.output
    assert len(commits) == 1
    assert commits[0].action_type == ControllerActionType.WRITE_ARTIFACT
