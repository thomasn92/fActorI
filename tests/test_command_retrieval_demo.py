from __future__ import annotations

import inspect
import json

from typer.testing import CliRunner

from factori.cli import app
from factori.commands.retrieval_demo import (
    RetrievalAdequacyDemoResult,
    run_retrieval_adequacy_demo,
)


def test_retrieval_adequacy_demo_library_entry_point_works_with_fake(capsys) -> None:
    result = run_retrieval_adequacy_demo()

    captured = capsys.readouterr()

    assert isinstance(result, RetrievalAdequacyDemoResult)
    assert captured.out == ""
    assert captured.err == ""
    assert result.retrieval_backend == "fake"
    assert result.certificate.rho_adequacy == 0.66
    assert result.certificate.proves_novelty is False
    assert result.certificate.claims_literature_coverage is False


def test_retrieval_adequacy_demo_library_does_not_use_typer_exit() -> None:
    source = inspect.getsource(run_retrieval_adequacy_demo)

    assert "typer." not in source


def test_retrieval_adequacy_demo_cli_still_works() -> None:
    result = CliRunner().invoke(app, ["retrieval-adequacy-demo"])

    payload = json.loads(result.output)

    assert result.exit_code == 0, result.output
    assert payload["rho_adequacy"] == 0.66
    assert payload["proves_novelty"] is False
    assert payload["claims_literature_coverage"] is False
