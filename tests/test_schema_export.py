from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter
from typer.testing import CliRunner

from factori.cli import app
from factori.ledger import ResearchLedger
from factori.protocols import get_protocol_definition, get_protocol_definitions
from factori.schema_export import (
    check_protocols,
    export_protocols,
    protocol_examples,
    require_protocols_current,
)

EXAMPLE_PROTOCOLS = {
    "candidate.example.json": "Candidate",
    "artifact.example.json": "ArtifactRecord",
    "stage-result.example.json": "StageResult",
    "retrieval-result.example.json": "RetrievalResult",
    "proof-result.example.json": "ProofVerificationResult",
    "experiment-result.example.json": "ExperimentRunResult",
    "claim.example.json": "Claim",
    "pipeline-run-report.example.json": "PipelineRunReport",
}


def test_protocol_export_is_deterministic_and_emits_all_schemas(tmp_path: Path) -> None:
    output_dir = tmp_path / "protocols" / "jsonschema"

    first = export_protocols(output_dir)
    first_contents = {path.name: path.read_text(encoding="utf-8") for path in first.schema_files}
    second = export_protocols(output_dir)
    second_contents = {path.name: path.read_text(encoding="utf-8") for path in second.schema_files}

    expected = {definition.filename for definition in get_protocol_definitions()}
    assert {path.name for path in first.schema_files} == expected
    assert first_contents == second_contents
    assert len(first.schema_files) == 32
    for path in first.schema_files:
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["x-factori-protocol-version"] == "0.1.0"
        assert schema["x-factori-verification-evidence"] is False


def test_protocol_version_and_examples_are_validated_by_source_models(tmp_path: Path) -> None:
    output_dir = tmp_path / "protocols" / "jsonschema"
    result = export_protocols(output_dir)

    metadata = json.loads(result.version_file.read_text(encoding="utf-8"))
    assert metadata == {
        "protocol_version": "0.1.0",
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
    assert "schemas=32" in exported.output
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
    assert len(protocol_examples()) == 8
