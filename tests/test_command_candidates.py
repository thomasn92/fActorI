from __future__ import annotations

import inspect

from typer.testing import CliRunner

import factori.cli as cli
from factori.cli import app
from factori.commands.candidates import AddCandidateCommandResult, add_candidate
from factori.ledger import ResearchLedger
from factori.schemas import ControllerActionType, DataRequirement


def test_add_candidate_library_entry_point_works(tmp_path, capsys) -> None:
    result = add_candidate(
        run_id="run-1",
        candidate_id="candidate-1",
        root=tmp_path,
        domain="human geography",
        question="Can a candidate be added through a library entry point?",
        data_requirement=DataRequirement.SYNTHETIC_ONLY,
    )

    captured = capsys.readouterr()
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    commits = ledger.list_commits("run-1")

    assert isinstance(result, AddCandidateCommandResult)
    assert captured.out == ""
    assert captured.err == ""
    assert result.candidate.id == "candidate-1"
    assert result.artifact.producing_commit_hash == result.commit.commit_hash
    assert commits == [result.commit]
    assert commits[0].action_type == ControllerActionType.ADD_CANDIDATE
    assert (tmp_path / result.artifact.path).is_file()
    assert (tmp_path / f"{result.artifact.path}.meta.json").is_file()


def test_add_candidate_cli_still_works(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "add-candidate",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--candidate-id",
            "candidate-1",
            "--domain",
            "human geography",
            "--question",
            "Can the CLI remain compatible?",
        ],
    )

    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    commits = ledger.list_commits("run-1")

    assert result.exit_code == 0, result.output
    assert "added candidate-1 " in result.output
    assert len(commits) == 1
    assert commits[0].action_type == ControllerActionType.ADD_CANDIDATE


def test_selected_cli_functions_delegate_to_library_entry_points() -> None:
    targets = {
        cli.add_candidate: "add_candidate_entry",
        cli.write_artifact: "write_artifact_entry",
        cli.questioner_check: "run_questioner_check",
        cli.retrieval_adequacy_demo: "run_retrieval_adequacy_demo",
    }

    for function, entry_point_name in targets.items():
        source = inspect.getsource(function)
        assert entry_point_name in source
        assert "ledger.append_commit" not in source
        assert "store.write_json" not in source
        assert "store.write_markdown" not in source
