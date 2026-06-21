"""Deterministic JSON Schema and example export for fActorI protocols."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from factori.protocols import (
    PROTOCOL_GENERATOR,
    PROTOCOL_SOURCE,
    PROTOCOL_VERSION,
    SCHEMA_FORMAT,
    ProtocolDefinition,
    get_protocol_definitions,
)
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    Candidate,
    Claim,
    ConstraintSet,
    DataRequirement,
    FakeExperimentResult,
    FakeProofResult,
    PipelineFailurePolicy,
    PipelineRunReport,
    PipelineRunStatus,
    PipelineStage,
    PipelineStageResult,
    RetrievalResult,
    SourceProvenance,
    VerificationLabel,
)

DEFAULT_PROTOCOL_OUTPUT_DIR = Path("protocols/jsonschema")
_HASH = "0" * 64


class ProtocolExportError(RuntimeError):
    """Raised when checked-in protocol files differ from generated contracts."""


@dataclass(frozen=True)
class ProtocolExportResult:
    """Summary of a deterministic protocol export or check."""

    output_dir: Path
    schema_files: tuple[Path, ...]
    example_files: tuple[Path, ...]
    version_file: Path
    stale_files: tuple[Path, ...] = ()

    @property
    def up_to_date(self) -> bool:
        return not self.stale_files


def export_protocols(output_dir: Path = DEFAULT_PROTOCOL_OUTPUT_DIR) -> ProtocolExportResult:
    """Write deterministic schemas, metadata, and small validated examples."""
    expected = _expected_files(output_dir)
    for path, content in expected.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    expected_schemas = {path for path in expected if path.parent == output_dir}
    if output_dir.is_dir():
        for existing in output_dir.glob("*.schema.json"):
            if existing not in expected_schemas:
                existing.unlink()
    return _result(output_dir)


def check_protocols(output_dir: Path = DEFAULT_PROTOCOL_OUTPUT_DIR) -> ProtocolExportResult:
    """Compare generated contracts with disk without writing any file."""
    expected = _expected_files(output_dir)
    stale = [
        path
        for path, content in expected.items()
        if not path.is_file() or path.read_text(encoding="utf-8") != content
    ]
    expected_schemas = {path for path in expected if path.parent == output_dir}
    if output_dir.is_dir():
        stale.extend(
            path
            for path in output_dir.glob("*.schema.json")
            if path not in expected_schemas
        )
    return _result(output_dir, stale_files=tuple(sorted(set(stale))))


def require_protocols_current(
    output_dir: Path = DEFAULT_PROTOCOL_OUTPUT_DIR,
) -> ProtocolExportResult:
    """Return a successful check or raise with deterministic stale paths."""
    result = check_protocols(output_dir)
    if not result.up_to_date:
        stale = ", ".join(path.as_posix() for path in result.stale_files)
        raise ProtocolExportError(f"Protocol files are stale or missing: {stale}")
    return result


def build_protocol_schema(definition: ProtocolDefinition) -> dict[str, Any]:
    """Generate one language-neutral schema from its existing typed model."""
    schema = TypeAdapter(definition.model).json_schema(mode="serialization")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["$id"] = (
        f"https://schemas.factori.local/{PROTOCOL_VERSION}/{definition.filename}"
    )
    schema["title"] = definition.name
    schema["description"] = definition.description
    schema["x-factori-protocol-version"] = PROTOCOL_VERSION
    schema["x-factori-source-model"] = definition.source_model
    schema["x-factori-verification-evidence"] = False
    return schema


def protocol_metadata() -> dict[str, str]:
    """Return stable metadata for consumers in any implementation language."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "schema_format": SCHEMA_FORMAT,
        "source": PROTOCOL_SOURCE,
        "generated_by": PROTOCOL_GENERATOR,
    }


def protocol_examples() -> dict[str, dict[str, Any]]:
    """Return small deterministic examples validated by their source models."""
    candidate = Candidate(
        id="candidate-example",
        constraints=ConstraintSet(domain="machine learning", method="calibration"),
        domain="machine learning",
        method="calibration",
        question="Can calibration remain stable under a declared synthetic shift?",
        data_requirement=DataRequirement.SYNTHETIC_ONLY,
    )
    artifact = ArtifactRef(
        id="artifact-example",
        type=ArtifactType.REPORT,
        path="runs/example/reports/artifact-example.json",
        content_hash=_HASH,
        producing_commit_hash=_HASH,
        metadata={"is_verification_evidence": False},
    )
    stage_result = PipelineStageResult(
        stage_name=PipelineStage.RUN_STAGE_A,
        started_at="1970-01-01T00:00:00.000000Z",
        finished_at="1970-01-01T00:00:01.000000Z",
        status=PipelineRunStatus.PIPELINE_SUCCEEDED,
        created_artifacts=[artifact.path],
        summary={"stage_a_survivors": 1},
    )
    provenance = SourceProvenance(
        source_id="source-example",
        provider="fake",
        query="calibration synthetic shift",
        rank=0,
        retrieved_at="1970-01-01T00:00:00.000000Z",
        raw_metadata_hash=_HASH,
    )
    retrieval = RetrievalResult(
        source_id="source-example",
        title="Synthetic calibration context",
        authors=["Example Author"],
        year=1970,
        provider="fake",
        retrieved_at="1970-01-01T00:00:00.000000Z",
        query="calibration synthetic shift",
        rank=0,
        raw_metadata_hash=_HASH,
        source_provenance=provenance,
        fake=True,
    )
    proof = FakeProofResult(
        candidate_id=candidate.id,
        proof_attempt_id="proof-example",
        lean_exit_code_fake=1,
        forbidden_tokens_present=False,
        proof_score=0.75,
        label=VerificationLabel.CONJECTURE,
        evidence_artifact_type=ArtifactType.LEAN,
        reason="Deterministic example only; no real Lean execution occurred.",
    )
    experiment = FakeExperimentResult(
        candidate_id=candidate.id,
        experiment_id="experiment-example",
        generator_name="deterministic-example-generator",
        generator_parameters={"samples": 16},
        seed=0,
        metric_name="example_metric",
        metric_value=0.55,
        baseline_value=0.50,
        delta=0.05,
        predeclared_delta=0.10,
        lcb_95=-0.01,
        ablation_passed=False,
        baseline_strong=True,
        label=VerificationLabel.NEGATIVE_RESULT,
        reason="The deterministic example does not meet its declared threshold.",
    )
    claim = Claim(
        claim_id="claim-example",
        claim_text="The proposed relationship remains conjectural.",
        claim_label=VerificationLabel.CONJECTURE,
        candidate_id=candidate.id,
        allowed_in_main_text=False,
        allowed_section="Theory",
        reason="No proof evidence is linked.",
    )
    pipeline_report = PipelineRunReport(
        run_id="example",
        domain="machine learning",
        stage_results=[stage_result],
        started_at="1970-01-01T00:00:00.000000Z",
        finished_at="1970-01-01T00:00:01.000000Z",
        pipeline_status=PipelineRunStatus.PIPELINE_SUCCEEDED,
        failure_policy=PipelineFailurePolicy.CONTINUE_SAFE,
        pipeline_report_path="runs/example/reports/pipeline-run-report.md",
    )
    return {
        "candidate.example.json": candidate.model_dump(mode="json"),
        "artifact.example.json": artifact.model_dump(mode="json"),
        "stage-result.example.json": stage_result.model_dump(mode="json"),
        "retrieval-result.example.json": retrieval.model_dump(mode="json"),
        "proof-result.example.json": proof.model_dump(mode="json"),
        "experiment-result.example.json": experiment.model_dump(mode="json"),
        "claim.example.json": claim.model_dump(mode="json"),
        "pipeline-run-report.example.json": pipeline_report.model_dump(mode="json"),
    }


def _expected_files(output_dir: Path) -> dict[Path, str]:
    protocol_root = output_dir.parent
    files = {
        output_dir / definition.filename: _json_text(build_protocol_schema(definition))
        for definition in get_protocol_definitions()
    }
    files[protocol_root / "version.json"] = _json_text(protocol_metadata())
    for filename, example in protocol_examples().items():
        files[protocol_root / "examples" / filename] = _json_text(example)
    return files


def _result(
    output_dir: Path,
    *,
    stale_files: tuple[Path, ...] = (),
) -> ProtocolExportResult:
    protocol_root = output_dir.parent
    return ProtocolExportResult(
        output_dir=output_dir,
        schema_files=tuple(
            output_dir / definition.filename for definition in get_protocol_definitions()
        ),
        example_files=tuple(
            protocol_root / "examples" / filename for filename in protocol_examples()
        ),
        version_file=protocol_root / "version.json",
        stale_files=stale_files,
    )


def _json_text(value: Any) -> str:
    return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=True) + "\n"


__all__ = [
    "DEFAULT_PROTOCOL_OUTPUT_DIR",
    "ProtocolExportError",
    "ProtocolExportResult",
    "build_protocol_schema",
    "check_protocols",
    "export_protocols",
    "protocol_examples",
    "protocol_metadata",
    "require_protocols_current",
]
