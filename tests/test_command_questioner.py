from __future__ import annotations

import inspect

from typer.testing import CliRunner

from factori.cli import app
from factori.commands.questioner import (
    QuestionerCheckCommandResult,
    run_questioner_check,
)
from factori.ledger import ResearchLedger
from factori.schemas import ControllerActionType


def test_questioner_check_library_entry_point_works(tmp_path, capsys) -> None:
    result = run_questioner_check(
        run_id="run-1",
        candidate_id="candidate-1",
        root=tmp_path,
    )

    captured = capsys.readouterr()
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    commits = ledger.list_commits("run-1")

    assert isinstance(result, QuestionerCheckCommandResult)
    assert captured.out == ""
    assert captured.err == ""
    assert len(result.questions) > 2
    assert result.routed_action.value == "StrengthenBaseline"
    assert len(commits) == 2
    assert commits[0].action_type == ControllerActionType.INIT_RUN
    assert commits[1] == result.commit
    assert commits[1].action_type == ControllerActionType.QUESTIONER_CHECK


def test_questioner_check_library_does_not_use_typer_exit() -> None:
    source = inspect.getsource(run_questioner_check)

    assert "typer." not in source


def test_questioner_check_cli_still_works(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "questioner-check",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--candidate-id",
            "candidate-1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "questions=" in result.output
    assert "routed_action=StrengthenBaseline" in result.output
    assert "commit_hash=" in result.output
