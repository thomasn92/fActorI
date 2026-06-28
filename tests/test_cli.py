from __future__ import annotations

from typer.testing import CliRunner

from factori.cli import app


def test_inspect_llm_run_cli_is_registered() -> None:
    result = CliRunner().invoke(app, ["inspect-llm-run", "--help"])

    assert result.exit_code == 0, result.output
    assert "--run-id" in result.output
    assert "--json" in result.output
