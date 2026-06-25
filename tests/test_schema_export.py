from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter
from typer.testing import CliRunner

from factori.cli import app
from factori.ledger import ResearchLedger
from factori.protocols import get_protocol_definition, get_protocol_definitions
from factori.schema_export import (
    EXAMPLE_PROTOCOLS,
    check_protocols,
    export_protocols,
    protocol_examples,
    require_protocols_current,
)


def test_protocol_export_is_deterministic_and_emits_all_schemas(tmp_path: Path) -> None:
    output_dir = tmp_path / "protocols" / "jsonschema"

    first = export_protocols(output_dir)
    first_contents = {path.name: path.read_text(encoding="utf-8") for path in first.schema_files}
    second = export_protocols(output_dir)
    second_contents = {path.name: path.read_text(encoding="utf-8") for path in second.schema_files}

    expected = {definition.filename for definition in get_protocol_definitions()}
    assert {path.name for path in first.schema_files} == expected
    assert {
        "run-status-report.schema.json",
        "resume-validation-report.schema.json",
        "stage-checkpoint.schema.json",
        "rerun-policy.schema.json",
        "stage-rerun-decision.schema.json",
        "ledger-tip-validation-report.schema.json",
        "artifact-manifest.schema.json",
        "research-object-manifest.schema.json",
        "llm-prompt-contract.schema.json",
        "llm-candidate-parse-report.schema.json",
        "llm-reviewer-prompt-contract.schema.json",
        "llm-reviewer-parse-report.schema.json",
        "retrieval-query.schema.json",
        "retrieval-run-report.schema.json",
        "retrieval-parse-report.schema.json",
        "proof-verification-contract.schema.json",
        "adapter-backend.schema.json",
        "retrieval-backend.schema.json",
        "reviewer-backend.schema.json",
        "proof-backend.schema.json",
    } <= {path.name for path in first.schema_files}
    assert first_contents == second_contents
    assert len(first.schema_files) == len(get_protocol_definitions()) == 63
    for path in first.schema_files:
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["x-factori-protocol-version"] == "0.2.0"
        assert schema["x-factori-verification-evidence"] is False


def test_protocol_version_and_examples_are_validated_by_source_models(tmp_path: Path) -> None:
    output_dir = tmp_path / "protocols" / "jsonschema"
    result = export_protocols(output_dir)

    metadata = json.loads(result.version_file.read_text(encoding="utf-8"))
    assert metadata == {
        "protocol_version": "0.2.0",
        "schema_format": "json-schema",
        "source": "factori-pydantic-models",
        "generated_by": "factori export-protocols",
    }
    assert {path.name for path in result.example_files} == set(EXAMPLE_PROTOCOLS)
    for path in result.example_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        definition = get_protocol_definition(EXAMPLE_PROTOCOLS[path.name])
        TypeAdapter(definition.model).validate_python(payload)


def test_check_passes_after_export_and_detects_stale_schema(tmp_path: Path) -> None:
    output_dir = tmp_path / "protocols" / "jsonschema"
    export_protocols(output_dir)

    assert require_protocols_current(output_dir).up_to_date
    stale_path = output_dir / "candidate.schema.json"
    stale_path.write_text("{}\n", encoding="utf-8")

    result = check_protocols(output_dir)
    assert not result.up_to_date
    assert result.stale_files == (stale_path,)


def test_export_cli_and_check_mode_work(tmp_path: Path) -> None:
    output_dir = tmp_path / "protocols" / "jsonschema"
    runner = CliRunner()

    exported = runner.invoke(app, ["export-protocols", "--output-dir", str(output_dir)])
    checked = runner.invoke(
        app,
        ["export-protocols", "--output-dir", str(output_dir), "--check"],
    )

    assert exported.exit_code == 0, exported.output
    assert "schemas=63" in exported.output
    assert checked.exit_code == 0, checked.output
    assert "check=ok" in checked.output


def test_export_cli_check_fails_clearly_for_stale_schema(tmp_path: Path) -> None:
    output_dir = tmp_path / "protocols" / "jsonschema"
    export_protocols(output_dir)
    (output_dir / "candidate.schema.json").write_text("{}\n", encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["export-protocols", "--output-dir", str(output_dir), "--check"],
    )

    assert result.exit_code == 1
    assert "Protocol files are stale or missing" in result.stderr
    assert "candidate.schema.json" in result.stderr


def test_protocol_export_does_not_touch_run_provenance(tmp_path: Path) -> None:
    ledger_path = tmp_path / "runs" / "run-1" / "ledger.sqlite"
    ledger = ResearchLedger(ledger_path)
    run_file = tmp_path / "runs" / "run-1" / "reports" / "existing.json"
    run_file.parent.mkdir(parents=True, exist_ok=True)
    run_file.write_text('{"existing": true}\n', encoding="utf-8")
    before = run_file.read_bytes()
    commits_before = len(ledger.list_commits("run-1"))

    export_protocols(tmp_path / "protocols" / "jsonschema")

    assert len(ledger.list_commits("run-1")) == commits_before
    assert run_file.read_bytes() == before
    assert list((tmp_path / "runs" / "run-1" / "reports").iterdir()) == [run_file]


def test_checked_in_protocol_files_are_current() -> None:
    assert require_protocols_current().up_to_date
    assert len(protocol_examples()) == len(EXAMPLE_PROTOCOLS) == 19


def test_timestamp_fields_are_exported_with_date_time_format(tmp_path: Path) -> None:
    output_dir = tmp_path / "protocols" / "jsonschema"
    export_protocols(output_dir)

    ledger_schema = json.loads((output_dir / "ledger-commit.schema.json").read_text())
    stage_schema = json.loads((output_dir / "stage-result.schema.json").read_text())
    retrieval_schema = json.loads((output_dir / "retrieval-result.schema.json").read_text())

    assert ledger_schema["properties"]["timestamp"]["format"] == "date-time"
    assert stage_schema["properties"]["started_at"]["format"] == "date-time"
    assert stage_schema["properties"]["finished_at"]["format"] == "date-time"
    assert retrieval_schema["properties"]["retrieved_at"]["format"] == "date-time"


def test_python_specific_formats_are_normalized_for_protocols(tmp_path: Path) -> None:
    output_dir = tmp_path / "protocols" / "jsonschema"
    export_protocols(output_dir)

    adapter_schema = json.loads((output_dir / "adapter-config.schema.json").read_text())
    config_schema = json.loads((output_dir / "pipeline-run-config.schema.json").read_text())

    api_key_branch = adapter_schema["properties"]["api_key"]["anyOf"][0]
    assert api_key_branch["x-factori-sensitive"] is True
    assert "format" not in api_key_branch
    assert "writeOnly" not in api_key_branch
    assert config_schema["properties"]["root"]["x-factori-format"] == "portable-path-string"
    assert "format" not in config_schema["properties"]["root"]
