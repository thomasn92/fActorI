"""Deterministic JSON Schema and example export for fActorI protocols."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from factori.adapters.config import AdapterConfig
from factori.protocols import (
    PROTOCOL_GENERATOR,
    PROTOCOL_SOURCE,
    PROTOCOL_VERSION,
    SCHEMA_FORMAT,
    ProtocolDefinition,
    get_protocol_definitions,
)
from factori.schemas import (
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactRef,
    ArtifactType,
    Candidate,
    Claim,
    ConstraintSet,
    DataRequirement,
    DryRunStatus,
    ExperimentKind,
    ExperimentRunContract,
    ExperimentRunResult,
    GeneratedSectionDraft,
    LedgerTipStatus,
    LedgerTipValidationReport,
    LLMCandidateParseReport,
    LLMPromptContract,
    NextStageRecommendation,
    PipelineDryRunPlan,
    PipelineFailurePolicy,
    PipelineRunReport,
    PipelineRunStatus,
    PipelineStage,
    PipelineStageResult,
    PlannedOutput,
    PlannedStage,
    PlannedStageStatus,
    ProofVerificationContract,
    ProofVerificationResult,
    ProseGenerationParseResult,
    ProseGenerationRequest,
    ProsePromptContract,
    ProseSafetyReport,
    ProseSectionContract,
    ResearchObjectManifest,
    ResumeValidationReport,
    ResumeValidationStatus,
    RetrievalQuery,
    RetrievalResult,
    RunCompletenessStatus,
    RunStatusReport,
    SourceProvenance,
    StageCheckpoint,
    StagePrerequisite,
    VerificationLabel,
)

DEFAULT_PROTOCOL_OUTPUT_DIR = Path("protocols/jsonschema")
_HASH = "0" * 64
_TIMESTAMP_PROPERTY_NAMES = frozenset(
    {
        "created_at",
        "finished_at",
        "retrieved_at",
        "started_at",
        "timestamp",
    }
)

EXAMPLE_PROTOCOLS: dict[str, str] = {
    "adapter-config.example.json": "AdapterConfig",
    "artifact.example.json": "ArtifactRecord",
    "artifact-manifest.example.json": "ArtifactManifest",
    "candidate.example.json": "Candidate",
    "claim.example.json": "Claim",
    "experiment-result.example.json": "ExperimentRunResult",
    "experiment-contract.example.json": "ExperimentRunContract",
    "ledger-tip-validation-report.example.json": "LedgerTipValidationReport",
    "llm-candidate-request.example.json": "LLMPromptContract",
    "llm-candidate-response.example.json": "LLMCandidateParseReport",
    "pipeline-dry-run-plan.example.json": "PipelineDryRunPlan",
    "pipeline-run-report.example.json": "PipelineRunReport",
    "prose-generation-parse-result.example.json": "ProseGenerationParseResult",
    "prose-generation-request.example.json": "ProseGenerationRequest",
    "prose-prompt-contract.example.json": "ProsePromptContract",
    "prose-safety-report.example.json": "ProseSafetyReport",
    "prose-section-contract.example.json": "ProseSectionContract",
    "proof-contract.example.json": "ProofVerificationContract",
    "proof-result.example.json": "ProofVerificationResult",
    "research-object-manifest.example.json": "ResearchObjectManifest",
    "resume-validation-report.example.json": "ResumeValidationReport",
    "retrieval-query.example.json": "RetrievalQuery",
    "retrieval-result.example.json": "RetrievalResult",
    "run-status-report.example.json": "RunStatusReport",
    "stage-result.example.json": "StageResult",
}


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
    _normalize_protocol_schema(schema)
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
    artifact_manifest = ArtifactManifest(
        run_id="example",
        artifacts=[
            ArtifactManifestEntry(
                artifact_id=artifact.id,
                artifact_type=artifact.type,
                path=artifact.path,
                content_hash=artifact.content_hash,
                producing_commit_hash=artifact.producing_commit_hash,
                is_evidence=False,
                is_presentation=True,
                metadata={"example": True},
            )
        ],
        evidence_artifact_count=0,
        presentation_artifact_count=1,
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
    retrieval_query = RetrievalQuery(
        query_id="query-example",
        query="calibration synthetic shift",
        provider="fake",
        limit=2,
        endpoint="fake://retrieval",
        parameters={"query": "calibration synthetic shift"},
        requires_credentials=False,
        fake=True,
    )
    experiment_contract = ExperimentRunContract(
        candidate_id=candidate.id,
        claim_id="claim-candidate-example",
        experiment_id="experiment-example",
        experiment_kind=ExperimentKind.SYNTHETIC_SIMULATION,
        data_regime=DataRequirement.SYNTHETIC_ONLY,
        synthetic_data_spec={"samples": 16, "external_data": False},
        model_spec={"claim": "synthetic calibration behavior"},
        algorithm_spec={"runner": "deterministic-example"},
        metrics=["delta", "lcb_95"],
        acceptance_criteria={"delta": {"min": 0.1}, "lcb_95": {"min": 0.0}},
        random_seed=0,
        replications=3,
        backend="fake",
        runner_name="fake",
    )
    experiment = ExperimentRunResult(
        candidate_id=candidate.id,
        claim_id="claim-candidate-example",
        experiment_id="experiment-example",
        backend="local_synthetic",
        provider="local",
        experiment_kind=ExperimentKind.SYNTHETIC_SIMULATION,
        data_regime=DataRequirement.SYNTHETIC_ONLY,
        runner_name="local_synthetic",
        exit_code=1,
        stdout_hash=_HASH,
        stderr_hash=_HASH,
        input_spec_hash=_HASH,
        output_payload_hash=_HASH,
        metrics={"delta": 0.05, "lcb_95": -0.01},
        acceptance_criteria={"delta": {"min": 0.1}, "lcb_95": {"min": 0.0}},
        passed=False,
        label=VerificationLabel.NEGATIVE_RESULT,
        reason="The deterministic example does not meet its declared threshold.",
        fake=False,
    )
    proof_contract = ProofVerificationContract(
        candidate_id=candidate.id,
        claim_id="claim-candidate-example",
        claim_text="The example proof claim remains conjectural.",
        proof_language="Lean",
        backend="fake",
        proof_payload_text="theorem factori_example : True := by\n  trivial\n",
        proof_payload={"attempt": "deterministic-placeholder"},
        allow_external_calls=False,
        allow_external_tools=False,
    )
    proof_result = ProofVerificationResult(
        candidate_id=candidate.id,
        claim_id="claim-candidate-example",
        backend="lean",
        provider="lean",
        proof_language="Lean",
        tool_name="lean",
        exit_code=1,
        stdout_hash=_HASH,
        stderr_hash=_HASH,
        proof_payload_hash=_HASH,
        forbidden_tokens_present=False,
        verified=False,
        label=VerificationLabel.CONJECTURE,
        reason="Deterministic example only; no external proof tool was executed.",
        fake=False,
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
    prose_section_contract = ProseSectionContract(
        run_id="example",
        section_id="introduction",
        section_title="Introduction",
        section_role="Introduction",
        allowed_claim_ids=[claim.claim_id],
        allowed_evidence_artifact_ids=[artifact.id],
        allowed_citation_ids=[retrieval.source_id],
        forbidden_claims=[],
        forbidden_labels=[VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED],
        evidence_boundary_instructions=[
            "Use only allowed claim IDs.",
            "Generated prose is not verification evidence.",
        ],
        style_instructions=["Use placeholder-grade prose only."],
        max_words=120,
        source_contract_hashes={"claim_table": _HASH},
    )
    prose_prompt_contract = ProsePromptContract(
        run_id="example",
        section_id=prose_section_contract.section_id,
        section_contract=prose_section_contract,
        allowed_claims=[claim.model_dump(mode="json")],
        evidence_map={
            artifact.id: {
                "artifact_id": artifact.id,
                "claim_id": claim.claim_id,
                "is_verification_evidence": False,
            }
        },
        narrative_context={"central_message": "Bounded example only."},
        requested_output_schema={"type": "object"},
        forbidden_outputs=["Do not invent citations or verification labels."],
        evidence_boundary_instructions=[
            "Draft prose only.",
            "Do not upgrade claim labels.",
        ],
        prompt_text="Draft the introduction using only claim-example and artifact-example.",
    )
    prose_request = ProseGenerationRequest(
        run_id="example",
        section_id=prose_section_contract.section_id,
        prompt_contract=prose_prompt_contract,
    )
    generated_section = GeneratedSectionDraft(
        section_id=prose_section_contract.section_id,
        title=prose_section_contract.section_title,
        content=(
            "[FAKE PROSE DRAFT] This section summarizes claim-example using "
            "artifact-example. No scientific label is upgraded."
        ),
        claim_ids=[claim.claim_id],
        used_claim_ids=[claim.claim_id],
        used_evidence_artifact_ids=[artifact.id],
        used_citation_ids=[retrieval.source_id],
    )
    prose_parse_result = ProseGenerationParseResult(
        section_draft=generated_section,
        raw_response_type="dict",
        fake=True,
    )
    prose_safety_report = ProseSafetyReport(
        section_id=prose_section_contract.section_id,
        safe=True,
        rejected=False,
        used_claim_ids=[claim.claim_id],
        used_evidence_artifact_ids=[artifact.id],
        used_citation_ids=[retrieval.source_id],
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
    checkpoint = StageCheckpoint(
        stage_name=PipelineStage.RUN_STAGE_A,
        completed=True,
        required_artifacts_present=["runs/example/reports/stage-a-report.md"],
        completion_evidence=["stage_a_report"],
    )
    next_stage = NextStageRecommendation(
        stage_name=PipelineStage.RUN_STAGE_B,
        command="uv run factori run-stage-b --run-id example",
        reason="Stage A example checkpoint is complete.",
    )
    run_status = RunStatusReport(
        run_id="example",
        run_exists=True,
        completed_stages=[PipelineStage.RUN_STAGE_A],
        missing_stages=[PipelineStage.RUN_STAGE_B],
        next_recommended_stage=next_stage,
        last_completed_stage=PipelineStage.RUN_STAGE_A,
        required_artifacts_present=["runs/example/reports/stage-a-report.md"],
        stage_checkpoints=[checkpoint],
        ledger_exists=True,
        ledger_commit_count=3,
        artifact_manifest_exists=False,
        research_object_exists=False,
        paper_skeleton_exists=False,
        final_audit_exists=False,
        export_preparation_exists=False,
        replay_report_exists=False,
        diagnostic_report_exists=False,
        completeness_status=RunCompletenessStatus.PARTIAL_RUN,
    )
    prerequisite = StagePrerequisite(
        stage_name=PipelineStage.RUN_STAGE_B,
        required_prior_stage=PipelineStage.RUN_STAGE_A,
        required_artifact_path_or_kind="reports/stage-a-report.md",
        required_report="stage-a-report",
        message="Stage B requires Stage A survivor output.",
    )
    resume_validation = ResumeValidationReport(
        run_id="example",
        start_at_stage=PipelineStage.RUN_STAGE_B,
        resume_status=ResumeValidationStatus.RESUME_ALLOWED,
        prerequisites=[prerequisite],
        next_recommended_stage=next_stage,
        run_exists=True,
        ledger_exists=True,
        ledger_commit_count=3,
    )
    ledger_tip = LedgerTipValidationReport(
        run_id="example",
        status=LedgerTipStatus.VALID,
        commit_count=3,
        tip_hashes=[_HASH],
        ledger_exists=True,
    )
    llm_prompt = LLMPromptContract(
        domain="machine learning",
        method="calibration",
        constraints=ConstraintSet(
            domain="machine learning",
            method="calibration",
            data_requirement=DataRequirement.SYNTHETIC_ONLY,
        ).model_dump(mode="json"),
        data_regime_policy=list(DataRequirement),
        mvp_data_gate={
            "allowed": [DataRequirement.NO_DATA, DataRequirement.SYNTHETIC_ONLY],
            "deferred": [
                DataRequirement.PUBLIC_DOWNLOAD,
                DataRequirement.USER_PROVIDED,
            ],
        },
        requested_output_schema={
            "type": "object",
            "properties": {"candidates": {"type": "array"}},
        },
        forbidden_claims=["Do not claim verification labels."],
        evidence_boundary_instructions=["Candidate proposals are not evidence."],
        max_candidates=2,
        prompt_text="Deterministic example prompt contract.",
    )
    llm_response = LLMCandidateParseReport(
        accepted_candidate_ids=[candidate.id],
        rejected_candidates=[],
        max_candidates=2,
        truncated=False,
        fake=True,
    )
    planned_output = PlannedOutput(
        output_kind="stage_a_report",
        path="runs/example/reports/stage-a-report.md",
        description="Stage A ranked report.",
    )
    planned_stage = PlannedStage(
        stage_name=PipelineStage.RUN_STAGE_A.value,
        status=PlannedStageStatus.WOULD_RUN,
        reason="No prior run artifacts are required.",
        expected_outputs=[planned_output],
    )
    dry_run_plan = PipelineDryRunPlan(
        run_id="example",
        domain="machine learning",
        method="calibration",
        dry_run_status=DryRunStatus.DRY_RUN_RUNNABLE,
        planned_stages=[planned_stage],
        planned_outputs=[planned_output],
        next_stage=PipelineStage.RUN_STAGE_A,
        selected_stages=[PipelineStage.RUN_STAGE_A],
        warnings_count=0,
        blocking_findings_count=0,
    )
    research_manifest = ResearchObjectManifest(
        research_object_json=artifact,
        research_object_markdown=artifact,
        artifact_manifest=artifact,
        ledger_summary=artifact,
        branch_outcomes=artifact,
        reproducibility_manifest=artifact,
    )
    adapter_config = AdapterConfig()
    return {
        "adapter-config.example.json": adapter_config.model_dump(mode="json"),
        "candidate.example.json": candidate.model_dump(mode="json"),
        "artifact.example.json": artifact.model_dump(mode="json"),
        "artifact-manifest.example.json": artifact_manifest.model_dump(mode="json"),
        "stage-result.example.json": stage_result.model_dump(mode="json"),
        "run-status-report.example.json": run_status.model_dump(mode="json"),
        "resume-validation-report.example.json": resume_validation.model_dump(mode="json"),
        "ledger-tip-validation-report.example.json": ledger_tip.model_dump(mode="json"),
        "llm-candidate-request.example.json": llm_prompt.model_dump(mode="json"),
        "llm-candidate-response.example.json": llm_response.model_dump(mode="json"),
        "retrieval-query.example.json": retrieval_query.model_dump(mode="json"),
        "retrieval-result.example.json": retrieval.model_dump(mode="json"),
        "proof-contract.example.json": proof_contract.model_dump(mode="json"),
        "proof-result.example.json": proof_result.model_dump(mode="json"),
        "experiment-contract.example.json": experiment_contract.model_dump(mode="json"),
        "experiment-result.example.json": experiment.model_dump(mode="json"),
        "claim.example.json": claim.model_dump(mode="json"),
        "prose-section-contract.example.json": prose_section_contract.model_dump(mode="json"),
        "prose-prompt-contract.example.json": prose_prompt_contract.model_dump(mode="json"),
        "prose-generation-request.example.json": prose_request.model_dump(mode="json"),
        "prose-generation-parse-result.example.json": prose_parse_result.model_dump(mode="json"),
        "prose-safety-report.example.json": prose_safety_report.model_dump(mode="json"),
        "research-object-manifest.example.json": research_manifest.model_dump(mode="json"),
        "pipeline-dry-run-plan.example.json": dry_run_plan.model_dump(mode="json"),
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


def _normalize_protocol_schema(schema: dict[str, Any]) -> None:
    """Normalize generated JSON Schema for language-neutral consumers."""
    _normalize_schema_node(schema, property_name=None)


def _normalize_schema_node(node: Any, *, property_name: str | None) -> None:
    if isinstance(node, list):
        for item in node:
            _normalize_schema_node(item, property_name=property_name)
        return
    if not isinstance(node, dict):
        return

    if node.get("format") == "password":
        node.pop("format", None)
        node.pop("writeOnly", None)
        node["x-factori-sensitive"] = True
    elif node.get("format") == "path":
        node.pop("format", None)
        node["x-factori-format"] = "portable-path-string"

    if property_name in _TIMESTAMP_PROPERTY_NAMES:
        _apply_date_time_format(node)

    properties = node.get("properties")
    if isinstance(properties, dict):
        for child_name, child_schema in properties.items():
            _normalize_schema_node(child_schema, property_name=child_name)

    for key, value in node.items():
        if key == "properties":
            continue
        _normalize_schema_node(value, property_name=property_name)


def _apply_date_time_format(node: dict[str, Any]) -> None:
    if node.get("type") == "string":
        node["format"] = "date-time"
        return
    for key in ("anyOf", "oneOf"):
        branches = node.get(key)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, dict) and branch.get("type") == "string":
                    branch["format"] = "date-time"


__all__ = [
    "DEFAULT_PROTOCOL_OUTPUT_DIR",
    "EXAMPLE_PROTOCOLS",
    "ProtocolExportError",
    "ProtocolExportResult",
    "build_protocol_schema",
    "check_protocols",
    "export_protocols",
    "protocol_examples",
    "protocol_metadata",
    "require_protocols_current",
]
