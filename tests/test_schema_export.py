from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter
from typer.testing import CliRunner

from factori.cli import app
from factori.ledger import ResearchLedger
from factori.protocols import PROTOCOL_VERSION, get_protocol_definition, get_protocol_definitions
from factori.schema_export import (
    EXAMPLE_PROTOCOLS,
    build_protocol_schema,
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
        "retrieval-quality-report.schema.json",
        "source-relevance-adjudication.schema.json",
        "proof-verification-contract.schema.json",
        "experiment-run-contract.schema.json",
        "prose-section-contract.schema.json",
        "citation-record.schema.json",
        "citation-registry.schema.json",
        "bibliography-entry.schema.json",
        "citation-usage.schema.json",
        "citation-safety-report.schema.json",
        "literature-gap-statement.schema.json",
        "literature-positioning-contract.schema.json",
        "literature-positioning-report.schema.json",
        "prose-prompt-contract.schema.json",
        "prose-generation-request.schema.json",
        "prose-generation-parse-result.schema.json",
        "prose-safety-report.schema.json",
        "manuscript-drafting-plan.schema.json",
        "section-drafting-task.schema.json",
        "section-drafting-result.schema.json",
        "section-draft-safety-summary.schema.json",
        "complete-markdown-draft.schema.json",
        "manuscript-drafting-report.schema.json",
        "manuscript-assembly-report.schema.json",
        "manuscript-draft-status.schema.json",
        "latex-export-contract.schema.json",
        "latex-source-map-entry.schema.json",
        "latex-source-map.schema.json",
        "latex-safety-report.schema.json",
        "latex-render-config.schema.json",
        "latex-render-result.schema.json",
        "latex-compile-check-report.schema.json",
        "latex-export-result.schema.json",
        "paper-critic-finding.schema.json",
        "paper-critic-report.schema.json",
        "paper-release-readiness-preview.schema.json",
        "section-revision-plan.schema.json",
        "paper-revision-plan.schema.json",
        "paper-revision-patch.schema.json",
        "revision-safety-report.schema.json",
        "paper-revision-result.schema.json",
        "quality-repair-report.schema.json",
        "claim-evidence-map-link.schema.json",
        "claim-evidence-map.schema.json",
        "human-review-artifact.schema.json",
        "proof-artifact.schema.json",
        "experiment-artifact.schema.json",
        "idea-node.schema.json",
        "idea-edge.schema.json",
        "idea-tree.schema.json",
        "idea-tree-inspection-report.schema.json",
        "idea-tree-export-report.schema.json",
        "full-paper-generation-config.schema.json",
        "full-paper-generation-step.schema.json",
        "full-paper-artifact-bundle.schema.json",
        "full-paper-generation-report.schema.json",
        "full-paper-generation-result.schema.json",
        "full-paper-generation-status.schema.json",
        "full-paper-generation-step-status.schema.json",
        "full-paper-release-gate-config.schema.json",
        "full-paper-release-check.schema.json",
        "full-paper-release-finding.schema.json",
        "full-paper-bundle-completeness-report.schema.json",
        "full-paper-evidence-boundary-report.schema.json",
        "full-paper-readiness-decision.schema.json",
        "full-paper-release-report.schema.json",
        "full-paper-release-status.schema.json",
        "full-paper-release-finding-severity.schema.json",
        "final-release-bundle-artifact.schema.json",
        "final-release-bundle-manifest.schema.json",
        "final-release-reproducibility-manifest.schema.json",
        "final-release-bundle.schema.json",
        "final-release-bundle-report.schema.json",
        "final-release-bundle-index.schema.json",
        "autonomous-paper-run-stage.schema.json",
        "autonomous-paper-run-handoff.schema.json",
        "autonomous-paper-run-report.schema.json",
        "autonomous-paper-run-index.schema.json",
        "autonomous-paper-checkpoint.schema.json",
        "autonomous-paper-checkpoint-index.schema.json",
        "autonomous-paper-resume-report.schema.json",
        "llm-budget-config.schema.json",
        "llm-budget-usage.schema.json",
        "llm-budget-decision.schema.json",
        "llm-call-accounting-record.schema.json",
        "llm-run-safety-report.schema.json",
        "llm-orchestration-config.schema.json",
        "llm-orchestration-step.schema.json",
        "llm-orchestration-report.schema.json",
        "llm-orchestration-result.schema.json",
        "llm-orchestration-status.schema.json",
        "llm-orchestration-step-status.schema.json",
        "llm-budget-decision-status.schema.json",
        "llm-call-status.schema.json",
        "adapter-backend.schema.json",
        "retrieval-backend.schema.json",
        "reviewer-backend.schema.json",
        "proof-backend.schema.json",
        "experiment-backend.schema.json",
        "prose-backend.schema.json",
        "experiment-kind.schema.json",
        "creative-mutation-operator.schema.json",
        "creative-mutation-candidate.schema.json",
        "creative-mutation-plan.schema.json",
        "creative-mutation-report.schema.json",
        "creative-mutation-inspection-report.schema.json",
        "mutation-tournament-spec.schema.json",
        "mutation-tournament-entry.schema.json",
        "mutation-tournament-result.schema.json",
        "mutation-tournament-comparison.schema.json",
        "mutation-tournament-inspection-report.schema.json",
        "creative-search-stop-reason.schema.json",
        "creative-search-controller-config.schema.json",
        "creative-search-lineage-entry.schema.json",
        "creative-search-cycle.schema.json",
        "creative-search-controller-report.schema.json",
        "creative-search-inspection-report.schema.json",
        "generation-mutation-operator.schema.json",
        "generation-mutation-context.schema.json",
        "generation-mutation-candidate.schema.json",
        "generation-mutation-diversity-check.schema.json",
        "generation-mutation-plan.schema.json",
        "generation-mutation-inspection-report.schema.json",
    } <= {path.name for path in first.schema_files}
    assert first_contents == second_contents
    assert len(first.schema_files) == len(get_protocol_definitions()) == 444
    for path in first.schema_files:
        schema = json.loads(path.read_text(encoding="utf-8"))
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["x-factori-protocol-version"] == PROTOCOL_VERSION
        assert schema["x-factori-verification-evidence"] is False


def test_protocol_version_and_examples_are_validated_by_source_models(tmp_path: Path) -> None:
    output_dir = tmp_path / "protocols" / "jsonschema"
    result = export_protocols(output_dir)

    metadata = json.loads(result.version_file.read_text(encoding="utf-8"))
    assert metadata == {
        "protocol_version": PROTOCOL_VERSION,
        "schema_format": "json-schema",
        "source": "factori-pydantic-models",
        "generated_by": "factori export-protocols",
    }
    assert {path.name for path in result.example_files} == set(EXAMPLE_PROTOCOLS)
    for path in result.example_files:
        payload = json.loads(path.read_text(encoding="utf-8"))
        definition = get_protocol_definition(EXAMPLE_PROTOCOLS[path.name])
        TypeAdapter(definition.model).validate_python(payload)


def test_kernel_request_schema_exposes_discriminated_operation_payloads() -> None:
    schema = build_protocol_schema(get_protocol_definition("KernelRequestEnvelope"))

    assert schema["discriminator"]["propertyName"] == "operation"
    assert set(schema["discriminator"]["mapping"]) == {
        "hash.canonical_json",
        "artifact.persist",
        "artifact.verify",
        "ledger.verify",
        "protocol.validate",
        "evidence.classify",
        "evidence.validate_bundle",
        "claim.resolve",
        "checkpoint.verify",
        "replay.verify_core",
        "ledger.append",
    }
    assert len(schema["oneOf"]) == 11


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
    assert "schemas=444" in exported.output
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
    assert len(protocol_examples()) == len(EXAMPLE_PROTOCOLS) == 51


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
