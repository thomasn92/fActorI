"""LLM experiment-code generation, audited local execution, and metric extraction."""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factori.adapters.errors import AdapterError
from factori.adapters.llm_experiment_codegen import ExperimentCodeGenerationClient
from factori.artifacts import ArtifactStore
from factori.generated_experiment_safety import audit_generated_experiment_code
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.production_mode import evaluate_production_mode, stage_backend_record
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BackendKind,
    BranchRouteType,
    ControllerActionType,
    ExperimentCodeSafetyAudit,
    GeneratedExperimentExecutionReport,
    GeneratedExperimentInspectionReport,
    GeneratedExperimentResult,
    LLMExecutionSpecCandidate,
    LLMExperimentCodeArtifact,
    LLMExperimentCodegenConfig,
    LLMExperimentCodeRawArtifact,
    LLMRoutePlanningReport,
    LLMSubstrateConstructionReport,
    MetricExtractionResult,
    ProductionModePolicy,
    SandboxExecutionConfig,
    SandboxExecutionResult,
    ScientificStageKind,
    StageBackendRecord,
)

_ROUTE_REPORT_RE = re.compile(r"^llm-route-planning-report-(\d{4})\.json$")
_CODEGEN_REPORT_RE = re.compile(r"^experiment-code-generation-report-(\d{4})\.json$")
_EXECUTION_REPORT_RE = re.compile(r"^generated-experiment-execution-report-(\d{4})\.json$")
_RAW_RE = re.compile(r"^llm-experiment-code-raw-(\d{4})\.json$")
_CODE_RE = re.compile(r"^experiment-code-artifact-(\d{4})\.py$")
_AUDIT_RE = re.compile(r"^experiment-code-safety-audit-(\d{4})\.json$")
_SANDBOX_RE = re.compile(r"^sandbox-execution-result-(\d{4})\.json$")
_METRIC_RE = re.compile(r"^metric-extraction-result-(\d{4})\.json$")
_RESULT_RE = re.compile(r"^generated-experiment-result-(\d{4})\.json$")

_EXECUTABLE_ROUTES = {
    BranchRouteType.SYNTHETIC_EXPERIMENT,
    BranchRouteType.BENCHMARK_TOURNAMENT,
    BranchRouteType.COUNTEREXAMPLE_SEARCH,
}
_SUCCESS_LABELS = {
    BranchRouteType.SYNTHETIC_EXPERIMENT: "SyntheticExperimentEvidence",
    BranchRouteType.BENCHMARK_TOURNAMENT: "BenchmarkEvidence",
    BranchRouteType.COUNTEREXAMPLE_SEARCH: "CounterexampleEvidence",
}


class GeneratedExperimentError(RuntimeError):
    """Raised when generated experiment handling cannot proceed safely."""


@dataclass(frozen=True)
class GeneratedExperimentStageResult:
    run_id: str
    report: GeneratedExperimentExecutionReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


@dataclass(frozen=True)
class _ObservedExecution:
    result: SandboxExecutionResult
    stdout: str
    stderr: str
    output_payload: dict[str, Any] | None
    output_text: str | None


def generate_experiment_code(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    generator: ExperimentCodeGenerationClient,
    config: LLMExperimentCodegenConfig,
) -> GeneratedExperimentStageResult:
    """Generate and statically audit one Python script per executable M101 spec."""
    if config.run_id != run_id:
        raise GeneratedExperimentError("Experiment codegen config run_id does not match run_id.")
    if generator.backend_kind not in {BackendKind.LLM_OPENAI, BackendKind.LLM_OTHER}:
        raise GeneratedExperimentError("Experiment code generation requires a non-fake LLM.")
    if generator.fallback_used:
        raise GeneratedExperimentError("Experiment code generation forbids deterministic fallback.")

    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    route_path, route_report = _load_latest_route_report(run_id, reports)
    if config.require_non_fake_backends and not route_report.production_ready:
        raise GeneratedExperimentError(
            "Strict code generation requires a production-eligible M101 route report."
        )
    substrate_report = _load_substrate_report(root_path, route_report)
    substrate_by_id = {item.substrate_id: item for item in substrate_report.candidates}
    executable_specs = [
        item for item in route_report.execution_specs if item.route_type in _EXECUTABLE_ROUTES
    ]
    non_executable_specs = [
        item for item in route_report.execution_specs if item.route_type not in _EXECUTABLE_ROUTES
    ]
    selected_specs = executable_specs[: min(config.max_executable_specs, config.max_codegen_calls)]
    warnings: list[str] = []
    if len(selected_specs) < len(executable_specs):
        warnings.append(
            f"Code-generation budget deferred {len(executable_specs) - len(selected_specs)} "
            "executable specs."
        )

    report_number = _next_number(reports, _CODEGEN_REPORT_RE)
    raw_number = _next_number(reports, _RAW_RE)
    code_number = _next_number(root_path / "runs" / run_id / "experiments", _CODE_RE)
    audit_number = _next_number(reports, _AUDIT_RE)
    result_number = _next_number(reports, _RESULT_RE)
    report_id = f"experiment-code-generation-report-{report_number:04d}"
    code_artifacts: list[LLMExperimentCodeArtifact] = []
    audits: list[ExperimentCodeSafetyAudit] = []
    raw_artifacts: list[LLMExperimentCodeRawArtifact] = []
    safety_repair_attempt_count = 0
    safety_repair_success_count = 0

    for spec in selected_specs:
        substrate = substrate_by_id.get(spec.source_substrate_id)
        if substrate is None:
            raise GeneratedExperimentError(
                f"M100 substrate is unavailable for spec {spec.spec_id}."
            )
        try:
            response = generator.generate_code(
                spec_payload=spec.model_dump(mode="json"),
                substrate_payload=substrate.model_dump(mode="json"),
                allowed_dependencies=config.allowed_dependencies,
            )
        except (AdapterError, ValueError) as exc:
            raise GeneratedExperimentError(
                f"LLM experiment-code generation failed for {spec.spec_id}: {exc}"
            ) from exc
        code_id = f"experiment-code-artifact-{code_number + len(code_artifacts):04d}"
        rejection_reasons = list(response.rejection_reasons)
        accepted_code_id: str | None = None
        blocked_artifact: LLMExperimentCodeArtifact | None = None
        blocked_audit: ExperimentCodeSafetyAudit | None = None
        if response.accepted is not None and not rejection_reasons:
            proposal = response.accepted
            artifact = LLMExperimentCodeArtifact(
                code_artifact_id=code_id,
                generation_attempt=1,
                run_id=run_id,
                source_spec_id=spec.spec_id,
                source_route_id=spec.route_id,
                source_substrate_id=spec.source_substrate_id,
                route_type=spec.route_type,
                backend_kind=generator.backend_kind,
                language=proposal.language,
                entrypoint=proposal.entrypoint,
                code=proposal.code,
                expected_output_files=proposal.expected_output_files,
                required_inputs=proposal.required_inputs,
                declared_dependencies=proposal.declared_dependencies,
                random_seed=proposal.random_seed,
                timeout_seconds=min(proposal.timeout_seconds, config.default_timeout_seconds),
                network_required=False,
                filesystem_scope=proposal.filesystem_scope,
            )
            audit = audit_generated_experiment_code(
                artifact=artifact,
                required_metrics=spec.output_contract.required_metrics,
                negative_controls_required=bool(spec.negative_control_plan),
                allowed_dependencies=config.allowed_dependencies,
            )
            code_artifacts.append(artifact)
            audits.append(audit)
            accepted_code_id = code_id
            if audit.blocked:
                blocked_artifact = artifact
                blocked_audit = audit
                warnings.append(f"Safety audit blocked {code_id}: " + "; ".join(audit.reasons))
        elif config.require_non_fake_backends:
            raise GeneratedExperimentError(
                f"Strict code generation requires a valid code artifact for {spec.spec_id}: "
                + "; ".join(rejection_reasons)
            )
        else:
            warnings.append(
                f"Rejected code proposal for {spec.spec_id}: " + "; ".join(rejection_reasons)
            )
        raw_id = f"llm-experiment-code-raw-{raw_number + len(raw_artifacts):04d}"
        raw_artifacts.append(
            LLMExperimentCodeRawArtifact(
                raw_artifact_id=raw_id,
                generation_attempt=1,
                run_id=run_id,
                source_spec_id=spec.spec_id,
                backend_name=generator.backend_name,
                model=generator.model,
                prompt_text=response.prompt_text,
                requested_output_schema=response.requested_output_schema,
                raw_response=response.raw_response,
                accepted_code_artifact_id_optional=accepted_code_id,
                rejection_reasons=rejection_reasons,
                fallback_used=generator.fallback_used,
            )
        )
        if (
            blocked_artifact is not None
            and blocked_audit is not None
            and safety_repair_attempt_count < config.max_safety_repair_calls
        ):
            safety_repair_attempt_count += 1
            try:
                repair = generator.repair_code(
                    spec_payload=spec.model_dump(mode="json"),
                    substrate_payload=substrate.model_dump(mode="json"),
                    blocked_code=blocked_artifact.code,
                    audit_payload=blocked_audit.model_dump(mode="json"),
                    allowed_dependencies=config.allowed_dependencies,
                )
            except (AdapterError, ValueError) as exc:
                warnings.append(
                    f"Safety repair call failed for {blocked_artifact.code_artifact_id}: {exc}"
                )
                continue
            repair_reasons = list(repair.rejection_reasons)
            repaired_code_id: str | None = None
            if repair.accepted is not None and not repair_reasons:
                proposal = repair.accepted
                repaired_code_id = (
                    f"experiment-code-artifact-{code_number + len(code_artifacts):04d}"
                )
                repaired_artifact = LLMExperimentCodeArtifact(
                    code_artifact_id=repaired_code_id,
                    repair_of_code_artifact_id_optional=blocked_artifact.code_artifact_id,
                    generation_attempt=2,
                    run_id=run_id,
                    source_spec_id=spec.spec_id,
                    source_route_id=spec.route_id,
                    source_substrate_id=spec.source_substrate_id,
                    route_type=spec.route_type,
                    backend_kind=generator.backend_kind,
                    language=proposal.language,
                    entrypoint=proposal.entrypoint,
                    code=proposal.code,
                    expected_output_files=proposal.expected_output_files,
                    required_inputs=proposal.required_inputs,
                    declared_dependencies=proposal.declared_dependencies,
                    random_seed=proposal.random_seed,
                    timeout_seconds=min(
                        proposal.timeout_seconds, config.default_timeout_seconds
                    ),
                    network_required=False,
                    filesystem_scope=proposal.filesystem_scope,
                )
                repaired_audit = audit_generated_experiment_code(
                    artifact=repaired_artifact,
                    required_metrics=spec.output_contract.required_metrics,
                    negative_controls_required=bool(spec.negative_control_plan),
                    allowed_dependencies=config.allowed_dependencies,
                )
                code_artifacts.append(repaired_artifact)
                audits.append(repaired_audit)
                if repaired_audit.blocked:
                    warnings.append(
                        f"Safety audit blocked repair {repaired_code_id}: "
                        + "; ".join(repaired_audit.reasons)
                    )
                else:
                    safety_repair_success_count += 1
                    warnings.append(
                        f"Safety repair {repaired_code_id} supersedes blocked artifact "
                        f"{blocked_artifact.code_artifact_id}."
                    )
            else:
                warnings.append(
                    f"Rejected safety repair for {blocked_artifact.code_artifact_id}: "
                    + "; ".join(repair_reasons)
                )
            repair_raw_id = f"llm-experiment-code-raw-{raw_number + len(raw_artifacts):04d}"
            raw_artifacts.append(
                LLMExperimentCodeRawArtifact(
                    raw_artifact_id=repair_raw_id,
                    repair_of_code_artifact_id_optional=blocked_artifact.code_artifact_id,
                    generation_attempt=2,
                    run_id=run_id,
                    source_spec_id=spec.spec_id,
                    backend_name=generator.backend_name,
                    model=generator.model,
                    prompt_text=repair.prompt_text,
                    requested_output_schema=repair.requested_output_schema,
                    raw_response=repair.raw_response,
                    accepted_code_artifact_id_optional=repaired_code_id,
                    rejection_reasons=repair_reasons,
                    fallback_used=generator.fallback_used,
                )
            )

    deferred_results = [
        _deferred_result(
            spec=spec,
            result_id=f"generated-experiment-result-{result_number + index:04d}",
        )
        for index, spec in enumerate(non_executable_specs)
    ]
    backend_records = [
        _codegen_backend_record(
            report_id,
            generator,
            [item.code_artifact_id for item in code_artifacts],
        ),
        _audit_backend_record(report_id, [item.code_artifact_id for item in code_artifacts]),
    ]
    production = evaluate_production_mode(
        run_id=run_id,
        records=[*route_report.backend_records, *backend_records],
        policy=ProductionModePolicy(
            require_non_fake_backends=config.require_non_fake_backends,
            fail_on_silent_fallback=config.fail_on_silent_fallback,
        ),
        expected_stage_kinds=[
            ScientificStageKind.BRANCH_ROUTING,
            ScientificStageKind.EXPERIMENT_DESIGN,
            ScientificStageKind.SPEC_VALIDATION,
            ScientificStageKind.EXPERIMENT_CODE_GENERATION,
            ScientificStageKind.CODE_SAFETY_AUDIT,
        ],
        report_id=f"{report_id}-production-evaluation",
    )
    if config.require_non_fake_backends and production.blocking_violation_count:
        raise GeneratedExperimentError(
            "Strict experiment code generation blocked: "
            + "; ".join(item.message for item in production.violations)
        )
    code_paths = [
        f"runs/{run_id}/experiments/{item.code_artifact_id}.py" for item in code_artifacts
    ]
    audit_paths = [
        f"runs/{run_id}/reports/experiment-code-safety-audit-{audit_number + index:04d}.json"
        for index in range(len(audits))
    ]
    deferred_paths = [
        f"runs/{run_id}/reports/{item.result_id}.json" for item in deferred_results
    ]
    report = GeneratedExperimentExecutionReport(
        run_id=run_id,
        report_id=report_id,
        phase="code_generation",
        report_status="completed_with_warnings" if warnings else "code_generated",
        source_route_planning_report_path=_relative(root_path, route_path),
        config=config,
        executable_spec_count=len(executable_specs),
        non_executable_spec_count=len(non_executable_specs),
        code_artifact_count=len(code_artifacts),
        safety_audit_count=len(audits),
        blocked_code_count=sum(item.blocked for item in audits),
        safety_repair_attempt_count=safety_repair_attempt_count,
        safety_repair_success_count=safety_repair_success_count,
        executed_code_count=0,
        failed_execution_count=0,
        metric_extraction_count=0,
        deferred_non_executable_route_count=len(deferred_results),
        raw_artifact_paths=[
            f"runs/{run_id}/reports/{item.raw_artifact_id}.json" for item in raw_artifacts
        ],
        code_artifact_paths=code_paths,
        safety_audit_paths=audit_paths,
        generated_result_paths=deferred_paths,
        code_artifacts=code_artifacts,
        safety_audits=audits,
        generated_results=deferred_results,
        backend_records=backend_records,
        warnings=warnings,
        production_ready=(
            config.require_non_fake_backends and not production.blocking_violation_count
        ),
    )
    persistence = _persist_codegen(
        report=report,
        raw_artifacts=raw_artifacts,
        code_artifacts=code_artifacts,
        audits=audits,
        audit_paths=audit_paths,
        deferred_results=deferred_results,
        store=store,
        ledger=ledger,
    )
    return _stage_result(report, persistence)


def run_generated_experiments(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    require_non_fake_backends: bool = False,
) -> GeneratedExperimentStageResult:
    """Execute audited code and derive metrics exclusively from sandbox output JSON."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    codegen_path, codegen = _load_latest_codegen_report(run_id, reports)
    if require_non_fake_backends and not codegen.production_ready:
        raise GeneratedExperimentError(
            "Strict generated execution requires a production-eligible codegen report."
        )
    route_path = root_path / codegen.source_route_planning_report_path
    route_report = _load_route_report_path(route_path)
    spec_by_id = {item.spec_id: item for item in route_report.execution_specs}
    audit_by_code = {item.code_artifact_id: item for item in codegen.safety_audits}
    execution_artifacts = _select_execution_artifacts(codegen, audit_by_code)
    sandbox_number = _next_number(reports, _SANDBOX_RE)
    metric_number = _next_number(reports, _METRIC_RE)
    result_number = _next_number(reports, _RESULT_RE)
    execution_number = _next_number(reports, _EXECUTION_REPORT_RE)
    report_id = f"generated-experiment-execution-report-{execution_number:04d}"
    sandbox_configs: list[SandboxExecutionConfig] = []
    observations: list[_ObservedExecution] = []
    metric_results: list[MetricExtractionResult] = []
    generated_results: list[GeneratedExperimentResult] = list(codegen.generated_results)
    warnings: list[str] = []

    for index, artifact in enumerate(execution_artifacts):
        spec = spec_by_id.get(artifact.source_spec_id)
        audit = audit_by_code.get(artifact.code_artifact_id)
        if spec is None or audit is None:
            raise GeneratedExperimentError(
                f"Codegen report is inconsistent for {artifact.code_artifact_id}."
            )
        execution_id = f"sandbox-execution-result-{sandbox_number + index:04d}"
        generated_id = f"generated-experiment-result-{result_number + index:04d}"
        sandbox_config = SandboxExecutionConfig(
            entrypoint=artifact.entrypoint,
            output_json_filename="output.json",
            timeout_seconds=artifact.timeout_seconds,
            memory_limit_mb=codegen.config.memory_limit_mb,
            network_disabled=True,
            seed=artifact.random_seed,
            allowed_dependencies=artifact.declared_dependencies,
        )
        sandbox_configs.append(sandbox_config)
        if audit.blocked:
            observation = _blocked_observation(
                run_id=run_id,
                execution_id=execution_id,
                artifact=artifact,
                config=sandbox_config,
                reasons=audit.reasons,
            )
        else:
            observation = _execute_in_sandbox(
                run_id=run_id,
                artifact=artifact,
                execution_id=execution_id,
                config=sandbox_config,
            )
        observations.append(observation)
        extraction = extract_metrics_from_output(
            execution=observation.result,
            output_payload=observation.output_payload,
            required_metrics=spec.output_contract.required_metrics,
            output_json_path=observation.result.output_json_path,
        )
        extraction = extraction.model_copy(update={"execution_id": execution_id})
        if not audit.blocked:
            metric_results.append(extraction)
        generated = _build_generated_result(
            result_id=generated_id,
            spec=spec,
            execution=observation.result,
            extraction=extraction,
            output_payload=observation.output_payload,
        )
        generated_results.append(generated)
        if generated.status in {"failed", "inconclusive", "blocked_safety_audit"}:
            warnings.append(
                f"{artifact.code_artifact_id} produced {generated.status}: "
                f"{generated.failure_reason_optional or '; '.join(generated.warnings)}"
            )

    execution_records = [
        _execution_backend_record(report_id, [item.result.execution_id for item in observations]),
        _metric_backend_record(report_id, [item.execution_id for item in metric_results]),
    ]
    production = evaluate_production_mode(
        run_id=run_id,
        records=[*route_report.backend_records, *codegen.backend_records, *execution_records],
        policy=ProductionModePolicy(require_non_fake_backends=require_non_fake_backends),
        expected_stage_kinds=[
            ScientificStageKind.BRANCH_ROUTING,
            ScientificStageKind.EXPERIMENT_DESIGN,
            ScientificStageKind.SPEC_VALIDATION,
            ScientificStageKind.EXPERIMENT_CODE_GENERATION,
            ScientificStageKind.CODE_SAFETY_AUDIT,
            ScientificStageKind.EXPERIMENT_EXECUTION,
            ScientificStageKind.METRIC_COMPUTATION,
        ],
        report_id=f"{report_id}-production-evaluation",
    )
    if require_non_fake_backends and production.blocking_violation_count:
        raise GeneratedExperimentError(
            "Strict generated execution blocked: "
            + "; ".join(item.message for item in production.violations)
        )
    sandbox_paths = [
        f"runs/{run_id}/reports/{item.result.execution_id}.json" for item in observations
    ]
    metric_paths = [
        f"runs/{run_id}/reports/metric-extraction-result-{metric_number + index:04d}.json"
        for index in range(len(metric_results))
    ]
    new_results = generated_results[len(codegen.generated_results) :]
    result_paths = [
        *codegen.generated_result_paths,
        *[f"runs/{run_id}/reports/{item.result_id}.json" for item in new_results],
    ]
    report = GeneratedExperimentExecutionReport(
        run_id=run_id,
        report_id=report_id,
        phase="execution",
        report_status="completed_with_warnings" if warnings else "completed",
        source_route_planning_report_path=codegen.source_route_planning_report_path,
        source_codegen_report_path_optional=_relative(root_path, codegen_path),
        config=codegen.config,
        executable_spec_count=codegen.executable_spec_count,
        non_executable_spec_count=codegen.non_executable_spec_count,
        code_artifact_count=codegen.code_artifact_count,
        safety_audit_count=codegen.safety_audit_count,
        blocked_code_count=codegen.blocked_code_count,
        safety_repair_attempt_count=codegen.safety_repair_attempt_count,
        safety_repair_success_count=codegen.safety_repair_success_count,
        executed_code_count=sum(
            not audit_by_code[item.code_artifact_id].blocked
            for item in execution_artifacts
        ),
        failed_execution_count=sum(
            item.result.status in {"failed", "timed_out"} for item in observations
        ),
        metric_extraction_count=len(metric_results),
        deferred_non_executable_route_count=codegen.deferred_non_executable_route_count,
        raw_artifact_paths=codegen.raw_artifact_paths,
        code_artifact_paths=codegen.code_artifact_paths,
        safety_audit_paths=codegen.safety_audit_paths,
        sandbox_execution_paths=sandbox_paths,
        metric_extraction_paths=metric_paths,
        generated_result_paths=result_paths,
        code_artifacts=codegen.code_artifacts,
        safety_audits=codegen.safety_audits,
        sandbox_configs=sandbox_configs,
        sandbox_executions=[item.result for item in observations],
        metric_extractions=metric_results,
        generated_results=generated_results,
        backend_records=execution_records,
        warnings=warnings,
        production_ready=(require_non_fake_backends and not production.blocking_violation_count),
    )
    persistence = _persist_execution(
        report=report,
        observations=observations,
        metric_results=metric_results,
        metric_paths=metric_paths,
        new_results=new_results,
        store=store,
        ledger=ledger,
    )
    return _stage_result(report, persistence)


def _select_execution_artifacts(
    codegen: GeneratedExperimentExecutionReport,
    audit_by_code: dict[str, ExperimentCodeSafetyAudit],
) -> list[LLMExperimentCodeArtifact]:
    """Choose one artifact per spec, preferring the newest passing repair."""
    grouped: dict[str, list[LLMExperimentCodeArtifact]] = {}
    spec_order: list[str] = []
    for artifact in codegen.code_artifacts:
        if artifact.source_spec_id not in grouped:
            grouped[artifact.source_spec_id] = []
            spec_order.append(artifact.source_spec_id)
        grouped[artifact.source_spec_id].append(artifact)
    selected: list[LLMExperimentCodeArtifact] = []
    for spec_id in spec_order:
        attempts = grouped[spec_id]
        passing = [
            item
            for item in attempts
            if item.code_artifact_id in audit_by_code
            and not audit_by_code[item.code_artifact_id].blocked
        ]
        selected.append(passing[-1] if passing else attempts[-1])
    return selected


def extract_metrics_from_output(
    *,
    execution: SandboxExecutionResult,
    output_payload: dict[str, Any] | None,
    required_metrics: list[str],
    output_json_path: str | None,
    allow_nested_numeric_metrics: bool = False,
) -> MetricExtractionResult:
    """Extract finite numeric metrics only from an observed output JSON payload."""
    metrics: dict[str, float | int] = {}
    invalid: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []
    if execution.status != "completed" or output_payload is None:
        missing = list(required_metrics)
        warnings.append("No metrics accepted because sandbox execution did not complete.")
    else:
        raw_metrics = output_payload.get("metrics")
        if not isinstance(raw_metrics, dict):
            missing = list(required_metrics)
            warnings.append("output.json does not contain a metrics object.")
        else:
            for name in required_metrics:
                value = raw_metrics.get(name)
                if value is None:
                    missing.append(name)
                elif allow_nested_numeric_metrics and isinstance(value, dict):
                    nested = _flatten_numeric_metric(value, prefix=name)
                    if nested is None:
                        invalid.append(name)
                    else:
                        metrics.update(nested)
                elif (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(float(value))
                ):
                    invalid.append(name)
                else:
                    metrics[name] = value
    valid = not missing and not invalid and bool(metrics)
    source = output_json_path or "missing:output.json"
    return MetricExtractionResult(
        execution_id=execution.execution_id,
        metrics_extracted=bool(metrics),
        metrics=metrics if valid else {},
        metric_sources=(
            {name: f"{source}#metrics.{name}" for name in metrics} if valid else {}
        ),
        schema_valid=valid,
        missing_metrics=missing,
        invalid_metrics=invalid,
        extraction_warnings=warnings,
    )


def _flatten_numeric_metric(
    value: dict[str, Any] | list[Any],
    *,
    prefix: str,
) -> dict[str, float | int] | None:
    flattened: dict[str, float | int] = {}
    items = value.items() if isinstance(value, dict) else enumerate(value)
    for key, item in items:
        metric_name = f"{prefix}.{key}"
        if isinstance(item, bool):
            return None
        if isinstance(item, (int, float)) and math.isfinite(float(item)):
            flattened[metric_name] = item
        elif isinstance(item, (dict, list)):
            nested = _flatten_numeric_metric(item, prefix=metric_name)
            if nested is None:
                return None
            flattened.update(nested)
        else:
            return None
    return flattened or None


def inspect_experiment_code(
    *, run_id: str, root: str | Path = "."
) -> GeneratedExperimentInspectionReport:
    return _inspect_phase(run_id=run_id, root=root, pattern=_CODEGEN_REPORT_RE)


def inspect_generated_experiment_results(
    *, run_id: str, root: str | Path = "."
) -> GeneratedExperimentInspectionReport:
    return _inspect_phase(run_id=run_id, root=root, pattern=_EXECUTION_REPORT_RE)


def render_generated_experiment_text(report: GeneratedExperimentInspectionReport) -> str:
    return "\n".join(
        [
            "Generated experiments: "
            f"{'present' if report.generated_experiment_present else 'absent'}",
            f"Phase/status: {report.phase_optional or 'none'} / "
            f"{report.report_status_optional or 'none'}",
            f"Executable/code/audits: {report.executable_spec_count}/"
            f"{report.code_artifact_count}/{report.safety_audit_count}",
            f"Blocked/executed/failed: {report.blocked_code_count}/"
            f"{report.executed_code_count}/{report.failed_execution_count}",
            f"Safety repairs attempted/succeeded: {report.safety_repair_attempt_count}/"
            f"{report.safety_repair_success_count}",
            f"Metric extractions: {report.metric_extraction_count}",
            f"Deferred non-executable routes: {report.deferred_non_executable_route_count}",
            f"Production ready: {str(report.production_ready).lower()}",
            "publication_ready=false",
        ]
    )


def render_generated_experiment_markdown(report: GeneratedExperimentExecutionReport) -> str:
    lines = [
        "# Generated Experiment Execution",
        "",
        f"- Phase: `{report.phase}`",
        f"- Status: `{report.report_status}`",
        f"- Code artifacts: `{report.code_artifact_count}`",
        f"- Blocked: `{report.blocked_code_count}`",
        f"- Executed: `{report.executed_code_count}`",
        f"- Metric extractions: `{report.metric_extraction_count}`",
        "",
        "Metrics, when present, were parsed only from successful sandbox output JSON. LLM prose "
        "and route specifications are not metric sources.",
        "",
        "publication_ready=false",
    ]
    return "\n".join(lines)


def _execute_in_sandbox(
    *,
    run_id: str,
    artifact: LLMExperimentCodeArtifact,
    execution_id: str,
    config: SandboxExecutionConfig,
) -> _ObservedExecution:
    stdout = ""
    stderr = ""
    output_payload: dict[str, Any] | None = None
    output_text: str | None = None
    status = "failed"
    exit_code: int | None = None
    timed_out = False
    failure: str | None = None
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="factori-generated-experiment-") as temp:
        workdir = Path(temp)
        code_path = workdir / artifact.entrypoint
        code_path.write_text(artifact.code, encoding="utf-8")
        stdout_path = workdir / "stdout.txt"
        stderr_path = workdir / "stderr.txt"
        try:
            with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
                "w", encoding="utf-8"
            ) as stderr_handle:
                process = subprocess.run(
                    [sys.executable, "-I", artifact.entrypoint],
                    cwd=workdir,
                    env=_sandbox_env(workdir, config.seed),
                    stdout=stdout_handle,
                    stderr=stderr_handle,
                    timeout=config.timeout_seconds,
                    check=False,
                    shell=False,
                    preexec_fn=_resource_limiter(config),
                )
            exit_code = process.returncode
            stdout = _bounded_read(stdout_path, config.output_limit_bytes)
            stderr = _bounded_read(stderr_path, config.output_limit_bytes)
            if process.returncode != 0:
                failure = f"generated experiment exited with code {process.returncode}"
            else:
                output_path = workdir / config.output_json_filename
                if not output_path.is_file():
                    failure = "generated experiment did not write output.json"
                elif output_path.stat().st_size > config.output_limit_bytes:
                    failure = "generated output.json exceeds the output-size limit"
                else:
                    output_text = output_path.read_text(encoding="utf-8")
                    try:
                        parsed = json.loads(output_text)
                    except json.JSONDecodeError:
                        failure = "generated output.json is invalid JSON"
                    else:
                        if isinstance(parsed, dict):
                            output_payload = parsed
                            status = "completed"
                        else:
                            failure = "generated output.json must contain an object"
        except subprocess.TimeoutExpired:
            timed_out = True
            status = "timed_out"
            failure = "generated experiment exceeded its timeout"
            stdout = _bounded_read(stdout_path, config.output_limit_bytes)
            stderr = _bounded_read(stderr_path, config.output_limit_bytes)
        except OSError as exc:
            failure = f"generated experiment could not start: {exc}"
        if status != "completed" and not timed_out:
            status = "failed"
    runtime = max(0.0, time.monotonic() - started)
    stdout_artifact = f"{execution_id}-stdout"
    stderr_artifact = f"{execution_id}-stderr"
    output_artifact = f"{execution_id}-output" if output_text is not None else None
    result = SandboxExecutionResult(
        execution_id=execution_id,
        code_artifact_id=artifact.code_artifact_id,
        status=status,
        exit_code=exit_code,
        stdout_path=f"runs/{run_id}/logs/{stdout_artifact}.txt",
        stderr_path=f"runs/{run_id}/logs/{stderr_artifact}.txt",
        output_json_path=(
            f"runs/{run_id}/experiments/{output_artifact}.json" if output_artifact else None
        ),
        artifact_paths=[
            f"runs/{run_id}/logs/{stdout_artifact}.txt",
            f"runs/{run_id}/logs/{stderr_artifact}.txt",
            *(
                [f"runs/{run_id}/experiments/{output_artifact}.json"]
                if output_artifact
                else []
            ),
        ],
        runtime_seconds=runtime,
        timeout=timed_out,
        memory_limit_mb=config.memory_limit_mb,
        network_disabled=True,
        seed=config.seed,
        failure_reason_optional=failure,
    )
    return _ObservedExecution(
        result=result,
        stdout=stdout,
        stderr=stderr,
        output_payload=output_payload,
        output_text=output_text,
    )


def _blocked_observation(
    *,
    run_id: str,
    execution_id: str,
    artifact: LLMExperimentCodeArtifact,
    config: SandboxExecutionConfig,
    reasons: list[str],
) -> _ObservedExecution:
    reason = "Safety audit blocked execution: " + "; ".join(reasons)
    return _ObservedExecution(
        result=SandboxExecutionResult(
            execution_id=execution_id,
            code_artifact_id=artifact.code_artifact_id,
            status="blocked",
            exit_code=None,
            stdout_path=f"runs/{run_id}/logs/{execution_id}-stdout.txt",
            stderr_path=f"runs/{run_id}/logs/{execution_id}-stderr.txt",
            artifact_paths=[
                f"runs/{run_id}/logs/{execution_id}-stdout.txt",
                f"runs/{run_id}/logs/{execution_id}-stderr.txt",
            ],
            runtime_seconds=0.0,
            timeout=False,
            memory_limit_mb=config.memory_limit_mb,
            network_disabled=True,
            seed=config.seed,
            failure_reason_optional=reason,
        ),
        stdout="",
        stderr=reason,
        output_payload=None,
        output_text=None,
    )


def _build_generated_result(
    *,
    result_id: str,
    spec: LLMExecutionSpecCandidate,
    execution: SandboxExecutionResult,
    extraction: MetricExtractionResult,
    output_payload: dict[str, Any] | None,
) -> GeneratedExperimentResult:
    unavailable = "Unavailable because execution did not produce a valid bounded output."
    baseline_summary = _output_string(output_payload, "baseline_summary", unavailable)
    control_summary = _output_string(output_payload, "control_summary", unavailable)
    negative_summary = _output_string(output_payload, "negative_control_summary", unavailable)
    success = _output_bool(output_payload, "success_criteria_satisfied")
    failure = _output_bool(output_payload, "failure_criteria_satisfied")
    negative_passed = _output_bool(output_payload, "negative_controls_passed")
    warnings: list[str] = []
    if execution.status == "blocked":
        status = "blocked_safety_audit"
        label = "InconclusiveResult"
    elif execution.status != "completed" or not extraction.schema_valid:
        status = "failed" if execution.status in {"failed", "timed_out"} else "inconclusive"
        label = "InconclusiveResult"
    elif negative_passed is not True:
        status = "inconclusive"
        label = "InconclusiveResult"
        warnings.append("Negative controls did not pass; support was downgraded.")
    elif failure is True:
        status = "negative_result"
        label = "NegativeResult"
    elif success is True and failure is False:
        status = "completed"
        label = _SUCCESS_LABELS[spec.route_type]
    else:
        status = "inconclusive"
        label = "InconclusiveResult"
        warnings.append("Output did not resolve success and failure criteria consistently.")
    return GeneratedExperimentResult(
        result_id=result_id,
        source_spec_id=spec.spec_id,
        source_route_id=spec.route_id,
        source_substrate_id=spec.source_substrate_id,
        route_type=spec.route_type,
        status=status,
        evidence_label=label,
        scope_label="bounded local synthetic execution only; no real-world validation",
        metrics=extraction.metrics if extraction.schema_valid else {},
        metric_sources=extraction.metric_sources if extraction.schema_valid else {},
        baseline_summary=baseline_summary,
        control_summary=control_summary,
        negative_control_summary=negative_summary,
        success_criteria_satisfied=success,
        failure_criteria_satisfied=failure,
        warnings=[*warnings, *extraction.extraction_warnings],
        failure_reason_optional=execution.failure_reason_optional,
    )


def _deferred_result(
    *, spec: LLMExecutionSpecCandidate, result_id: str
) -> GeneratedExperimentResult:
    return GeneratedExperimentResult(
        result_id=result_id,
        source_spec_id=spec.spec_id,
        source_route_id=spec.route_id,
        source_substrate_id=spec.source_substrate_id,
        route_type=spec.route_type,
        status="deferred_non_executable_route",
        evidence_label="InconclusiveResult",
        scope_label="non-executable in M102; no scientific evidence created",
        metrics={},
        metric_sources={},
        baseline_summary="Not executed by M102.",
        control_summary="Not executed by M102.",
        negative_control_summary="Not executed by M102.",
        warnings=[
            "Route requires later symbolic, retrieval, proof, defer, or rejection handling."
        ],
        failure_reason_optional=(
            "route requires M103/M104/M105-style symbolic, retrieval, or proof handling"
        ),
    )


def _persist_codegen(
    *,
    report: GeneratedExperimentExecutionReport,
    raw_artifacts: list[LLMExperimentCodeRawArtifact],
    code_artifacts: list[LLMExperimentCodeArtifact],
    audits: list[ExperimentCodeSafetyAudit],
    audit_paths: list[str],
    deferred_results: list[GeneratedExperimentResult],
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PersistenceResult:
    metadata = _metadata("llm_experiment_codegen")
    specs = [
        ArtifactWriteSpec(item.raw_artifact_id, ArtifactType.REPORT, item, "json", metadata)
        for item in raw_artifacts
    ]
    specs.extend(
        ArtifactWriteSpec(
            item.code_artifact_id,
            ArtifactType.EXPERIMENT,
            item.code,
            "text",
            metadata,
            extension="py",
            format_label="python",
        )
        for item in code_artifacts
    )
    specs.extend(
        ArtifactWriteSpec(
            Path(path).stem,
            ArtifactType.REPORT,
            audit,
            "json",
            metadata,
        )
        for path, audit in zip(audit_paths, audits, strict=True)
    )
    specs.extend(
        ArtifactWriteSpec(item.result_id, ArtifactType.REPORT, item, "json", metadata)
        for item in deferred_results
    )
    specs.extend(_report_specs(report, metadata))
    return persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.LLM_EXPERIMENT_CODE_WRITTEN,
        commit_payload={
            "run_id": report.run_id,
            "report_id": report.report_id,
            "code_artifact_count": report.code_artifact_count,
            "blocked_code_count": report.blocked_code_count,
            "publication_ready": False,
        },
    )


def _persist_execution(
    *,
    report: GeneratedExperimentExecutionReport,
    observations: list[_ObservedExecution],
    metric_results: list[MetricExtractionResult],
    metric_paths: list[str],
    new_results: list[GeneratedExperimentResult],
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PersistenceResult:
    metadata = _metadata("generated_experiment_execution")
    specs: list[ArtifactWriteSpec] = []
    for observation in observations:
        execution = observation.result
        specs.extend(
            [
                ArtifactWriteSpec(
                    f"{execution.execution_id}-stdout",
                    ArtifactType.LOG,
                    observation.stdout,
                    "text",
                    metadata,
                ),
                ArtifactWriteSpec(
                    f"{execution.execution_id}-stderr",
                    ArtifactType.LOG,
                    observation.stderr,
                    "text",
                    metadata,
                ),
            ]
        )
        if observation.output_text is not None:
            specs.append(
                ArtifactWriteSpec(
                    f"{execution.execution_id}-output",
                    ArtifactType.EXPERIMENT,
                    observation.output_payload or {"invalid_output": observation.output_text},
                    "json",
                    metadata,
                )
            )
        specs.append(
            ArtifactWriteSpec(
                execution.execution_id,
                ArtifactType.REPORT,
                execution,
                "json",
                metadata,
            )
        )
    specs.extend(
        ArtifactWriteSpec(Path(path).stem, ArtifactType.REPORT, item, "json", metadata)
        for path, item in zip(metric_paths, metric_results, strict=True)
    )
    specs.extend(
        ArtifactWriteSpec(item.result_id, ArtifactType.REPORT, item, "json", metadata)
        for item in new_results
    )
    specs.extend(_report_specs(report, metadata))
    return persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.GENERATED_EXPERIMENT_EXECUTION_WRITTEN,
        commit_payload={
            "run_id": report.run_id,
            "report_id": report.report_id,
            "executed_code_count": report.executed_code_count,
            "metric_extraction_count": report.metric_extraction_count,
            "fixture_metric_count": 0,
            "publication_ready": False,
        },
    )


def _report_specs(
    report: GeneratedExperimentExecutionReport, metadata: dict[str, Any]
) -> list[ArtifactWriteSpec]:
    return [
        ArtifactWriteSpec(report.report_id, ArtifactType.REPORT, report, "json", metadata),
        ArtifactWriteSpec(
            f"{report.report_id}-markdown",
            ArtifactType.REPORT,
            render_generated_experiment_markdown(report),
            "markdown",
            metadata,
            filename_stem=report.report_id,
        ),
    ]


def _stage_result(
    report: GeneratedExperimentExecutionReport, persistence: PersistenceResult
) -> GeneratedExperimentStageResult:
    by_id = {item.id: item for item in persistence.artifacts}
    return GeneratedExperimentStageResult(
        run_id=report.run_id,
        report=report,
        persistence=persistence,
        report_artifact=by_id[report.report_id],
        markdown_artifact=by_id[f"{report.report_id}-markdown"],
    )


def _inspect_phase(
    *, run_id: str, root: str | Path, pattern: re.Pattern[str]
) -> GeneratedExperimentInspectionReport:
    reports = Path(root) / "runs" / run_id / "reports"
    path = _latest_matching(reports, pattern)
    if path is None:
        return GeneratedExperimentInspectionReport(
            run_id=run_id,
            generated_experiment_present=False,
        )
    report = _load_generated_report(path)
    return GeneratedExperimentInspectionReport(
        run_id=run_id,
        generated_experiment_present=True,
        phase_optional=report.phase,
        latest_report_id_optional=report.report_id,
        report_status_optional=report.report_status,
        executable_spec_count=report.executable_spec_count,
        non_executable_spec_count=report.non_executable_spec_count,
        code_artifact_count=report.code_artifact_count,
        safety_audit_count=report.safety_audit_count,
        blocked_code_count=report.blocked_code_count,
        safety_repair_attempt_count=report.safety_repair_attempt_count,
        safety_repair_success_count=report.safety_repair_success_count,
        executed_code_count=report.executed_code_count,
        failed_execution_count=report.failed_execution_count,
        metric_extraction_count=report.metric_extraction_count,
        deferred_non_executable_route_count=report.deferred_non_executable_route_count,
        fixture_metric_count=report.fixture_metric_count,
        code_artifacts=report.code_artifacts,
        safety_audits=report.safety_audits,
        sandbox_configs=report.sandbox_configs,
        sandbox_executions=report.sandbox_executions,
        metric_extractions=report.metric_extractions,
        generated_results=report.generated_results,
        backend_records=report.backend_records,
        warnings=report.warnings,
        production_ready=report.production_ready,
    )


def _codegen_backend_record(
    report_id: str,
    generator: ExperimentCodeGenerationClient,
    code_ids: list[str],
) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-code-generation",
        stage_kind=ScientificStageKind.EXPERIMENT_CODE_GENERATION,
        backend_kind=generator.backend_kind,
        backend_name=generator.backend_name,
        is_scientific_generation=True,
        is_scientific_judgment=False,
        is_execution_or_verification=False,
        reason="Experiment implementations come from the recorded non-fake LLM backend.",
        artifact_ids=[report_id, *code_ids],
        fallback_used=generator.fallback_used,
        fallback_disclosed=generator.fallback_disclosed,
    )


def _audit_backend_record(report_id: str, code_ids: list[str]) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-code-safety-audit",
        stage_kind=ScientificStageKind.CODE_SAFETY_AUDIT,
        backend_kind=BackendKind.LOCAL_EXECUTION,
        backend_name="generated_experiment_ast_policy",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=True,
        allowed_in_production=True,
        reason="Local AST and contract checks block unsafe generated code before execution.",
        artifact_ids=[report_id, *code_ids],
    )


def _execution_backend_record(report_id: str, execution_ids: list[str]) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-sandbox-execution",
        stage_kind=ScientificStageKind.EXPERIMENT_EXECUTION,
        backend_kind=BackendKind.LOCAL_EXECUTION,
        backend_name="isolated_generated_python_runner",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=True,
        allowed_in_production=True,
        reason="Audited Python runs locally with fixed seeds, timeout, and resource limits.",
        artifact_ids=[report_id, *execution_ids],
    )


def _metric_backend_record(report_id: str, execution_ids: list[str]) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-metric-extraction",
        stage_kind=ScientificStageKind.METRIC_COMPUTATION,
        backend_kind=BackendKind.LOCAL_EXECUTION,
        backend_name="sandbox_output_json_metric_extractor",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=True,
        allowed_in_production=True,
        reason="Finite numeric metrics are parsed only from successful sandbox output JSON.",
        artifact_ids=[report_id, *execution_ids],
    )


def _load_latest_route_report(
    run_id: str, reports: Path
) -> tuple[Path, LLMRoutePlanningReport]:
    path = _latest_matching(reports, _ROUTE_REPORT_RE)
    if path is None:
        raise GeneratedExperimentError(f"No M101 route report found for run_id={run_id}.")
    report = _load_route_report_path(path)
    if report.run_id != run_id:
        raise GeneratedExperimentError("M101 route report run_id is inconsistent.")
    return path, report


def _load_route_report_path(path: Path) -> LLMRoutePlanningReport:
    try:
        return LLMRoutePlanningReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise GeneratedExperimentError(f"Could not load M101 route report: {exc}") from exc


def _load_substrate_report(
    root_path: Path, route_report: LLMRoutePlanningReport
) -> LLMSubstrateConstructionReport:
    path = root_path / route_report.source_substrate_report_path
    try:
        return LLMSubstrateConstructionReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise GeneratedExperimentError(f"Could not load M100 substrate report: {exc}") from exc


def _load_latest_codegen_report(
    run_id: str, reports: Path
) -> tuple[Path, GeneratedExperimentExecutionReport]:
    path = _latest_matching(reports, _CODEGEN_REPORT_RE)
    if path is None:
        raise GeneratedExperimentError(f"No generated experiment code found for run_id={run_id}.")
    report = _load_generated_report(path)
    if report.run_id != run_id or report.phase != "code_generation":
        raise GeneratedExperimentError("Generated experiment code report is inconsistent.")
    return path, report


def _load_generated_report(path: Path) -> GeneratedExperimentExecutionReport:
    try:
        return GeneratedExperimentExecutionReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise GeneratedExperimentError(
            f"Could not load generated experiment report: {exc}"
        ) from exc


def _sandbox_env(workdir: Path, seed: int) -> dict[str, str]:
    return {
        "HOME": str(workdir),
        "PYTHONHASHSEED": str(seed),
        "FACTORI_EXPERIMENT_SEED": str(seed),
        "BLIS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
        "NUMEXPR_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "VECLIB_MAXIMUM_THREADS": "1",
        "NO_PROXY": "*",
        "no_proxy": "*",
    }


def _resource_limiter(config: SandboxExecutionConfig):
    if os.name != "posix":
        return None

    def apply_limits() -> None:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (config.timeout_seconds, config.timeout_seconds))
        resource.setrlimit(
            resource.RLIMIT_FSIZE,
            (config.output_limit_bytes, config.output_limit_bytes),
        )
        with contextlib.suppress(ValueError, OSError):
            memory = config.memory_limit_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (memory, memory))

    return apply_limits


def _bounded_read(path: Path, limit: int) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as handle:
        return handle.read(limit).decode("utf-8", errors="replace")


def _output_string(payload: dict[str, Any] | None, key: str, fallback: str) -> str:
    if payload is None or not isinstance(payload.get(key), str) or not payload[key].strip():
        return fallback
    return str(payload[key]).strip()


def _output_bool(payload: dict[str, Any] | None, key: str) -> bool | None:
    if payload is None or not isinstance(payload.get(key), bool):
        return None
    return bool(payload[key])


def _metadata(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "artifact_role": "bounded_generated_experiment_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "creates_real_world_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }


def _latest_matching(directory: Path, pattern: re.Pattern[str]) -> Path | None:
    if not directory.is_dir():
        return None
    matches = [path for path in directory.iterdir() if pattern.match(path.name)]
    return max(matches, key=lambda path: path.name) if matches else None


def _next_number(directory: Path, pattern: re.Pattern[str]) -> int:
    if not directory.is_dir():
        return 1
    numbers = [
        int(match.group(1))
        for path in directory.iterdir()
        if (match := pattern.match(path.name)) is not None
    ]
    return max(numbers, default=0) + 1


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


__all__ = [
    "GeneratedExperimentError",
    "GeneratedExperimentStageResult",
    "extract_metrics_from_output",
    "generate_experiment_code",
    "inspect_experiment_code",
    "inspect_generated_experiment_results",
    "render_generated_experiment_markdown",
    "render_generated_experiment_text",
    "run_generated_experiments",
]
