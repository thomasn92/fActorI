from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from factori.cli import app
from factori.protocol_validation import validate_protocol_examples
from factori.schema_export import export_protocols


def test_protocol_examples_validate_against_json_schema(tmp_path: Path) -> None:
    output_dir = tmp_path / "protocols" / "jsonschema"
    export_protocols(output_dir)

    first = validate_protocol_examples(output_dir, output_dir.parent / "examples")
    second = validate_protocol_examples(output_dir, output_dir.parent / "examples")

    assert first == second
    assert first.examples_checked == 42
    assert first.examples_valid == 42
    assert first.examples_invalid == 0


def test_invalid_example_fails_validation_clearly(tmp_path: Path) -> None:
    output_dir = tmp_path / "protocols" / "jsonschema"
    export_protocols(output_dir)
    candidate = output_dir.parent / "examples" / "candidate.example.json"
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload.pop("id")
    candidate.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    report = validate_protocol_examples(output_dir, output_dir.parent / "examples")

    assert report.examples_invalid == 1
    invalid = [result for result in report.results if not result.valid]
    assert len(invalid) == 1
    assert "missing required property 'id'" in invalid[0].errors[0]


def test_validate_protocol_examples_cli_and_json_mode_work(tmp_path: Path) -> None:
    output_dir = tmp_path / "protocols" / "jsonschema"
    export_protocols(output_dir)
    runner = CliRunner()

    text = runner.invoke(
        app,
        [
            "validate-protocol-examples",
            "--schema-dir",
            str(output_dir),
            "--examples-dir",
            str(output_dir.parent / "examples"),
        ],
    )
    json_result = runner.invoke(
        app,
        [
            "validate-protocol-examples",
            "--schema-dir",
            str(output_dir),
            "--examples-dir",
            str(output_dir.parent / "examples"),
            "--json",
        ],
    )

    assert text.exit_code == 0, text.output
    assert "examples_invalid=0" in text.output
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["examples_checked"] == 42
    assert payload["examples_invalid"] == 0


def test_validation_command_is_read_only(tmp_path: Path) -> None:
    output_dir = tmp_path / "protocols" / "jsonschema"
    export_protocols(output_dir)
    before = _tree_snapshot(tmp_path)

    report = validate_protocol_examples(output_dir, output_dir.parent / "examples")
    result = CliRunner().invoke(
        app,
        [
            "validate-protocol-examples",
            "--schema-dir",
            str(output_dir),
            "--examples-dir",
            str(output_dir.parent / "examples"),
        ],
    )

    assert report.examples_invalid == 0
    assert result.exit_code == 0, result.output
    assert _tree_snapshot(tmp_path) == before
    assert not (tmp_path / "runs").exists()


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
