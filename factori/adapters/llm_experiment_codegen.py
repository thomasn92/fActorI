"""Gated structured LLM generation of bounded Python experiment scripts."""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import Field, ValidationError

from factori.adapters.errors import (
    AdapterExternalCallsDisabled,
    AdapterMissingCredentials,
    AdapterResponseParseError,
)
from factori.adapters.llm_real import LLMTransport, OpenAIResponsesTransport
from factori.schemas import BackendKind, StrictModel

_MAX_PATCH_CHANGED_SOURCE_BYTES = 64_000


class ExperimentCodeProposal(StrictModel):
    language: Literal["python"] = "python"
    entrypoint: Literal["experiment.py"] = "experiment.py"
    code: str = Field(min_length=1)
    expected_output_files: list[str] = Field(min_length=1)
    required_inputs: list[str] = Field(default_factory=list)
    declared_dependencies: list[str] = Field(default_factory=list)
    random_seed: int = Field(ge=0)
    timeout_seconds: int = Field(ge=1, le=300)
    network_required: Literal[False] = False
    filesystem_scope: Literal["sandbox_workdir_only"] = "sandbox_workdir_only"
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False


class ExperimentCodeProposalEnvelope(StrictModel):
    experiment: ExperimentCodeProposal


class ExperimentCodeTextEdit(StrictModel):
    old_text: str = Field(min_length=1, max_length=16_000)
    new_text: str = Field(max_length=16_000)


class ExperimentCodePatchProposal(StrictModel):
    edits: list[ExperimentCodeTextEdit] = Field(min_length=1, max_length=32)
    summary: str = Field(min_length=1, max_length=1_000)
    language: Literal["python"]
    entrypoint: Literal["experiment.py"]
    expected_output_files: list[str] = Field(min_length=1)
    required_inputs: list[str]
    declared_dependencies: list[str]
    random_seed: int = Field(ge=0)
    timeout_seconds: int = Field(ge=1, le=300)
    network_required: Literal[False]
    filesystem_scope: Literal["sandbox_workdir_only"]
    publication_ready: Literal[False]
    creates_scientific_validation: Literal[False]


class ExperimentCodePatchEnvelope(StrictModel):
    repair: ExperimentCodePatchProposal


@dataclass(frozen=True)
class ExperimentCodeGenerationResponse:
    prompt_text: str
    requested_output_schema: dict[str, Any]
    raw_response: dict[str, Any]
    accepted: ExperimentCodeProposal | None
    rejection_reasons: list[str]


class ExperimentCodeGenerationClient(Protocol):
    backend_name: str
    backend_kind: BackendKind
    model: str
    fallback_used: bool
    fallback_disclosed: bool

    def generate_code(
        self,
        *,
        spec_payload: dict[str, Any],
        substrate_payload: dict[str, Any],
        allowed_dependencies: list[str],
    ) -> ExperimentCodeGenerationResponse: ...

    def repair_code(
        self,
        *,
        spec_payload: dict[str, Any],
        substrate_payload: dict[str, Any],
        blocked_code: str,
        audit_payload: dict[str, Any],
        allowed_dependencies: list[str],
    ) -> ExperimentCodeGenerationResponse: ...


@dataclass
class OpenAILLMExperimentCodeGenerator:
    """Explicitly gated OpenAI code generator without deterministic fallback."""

    api_key: str = field(repr=False)
    model: str
    transport: LLMTransport = field(default_factory=OpenAIResponsesTransport)
    allow_external_calls: bool = False
    backend_name: str = field(default="llm-openai", init=False)
    backend_kind: BackendKind = field(default=BackendKind.LLM_OPENAI, init=False)
    fallback_used: bool = field(default=False, init=False)
    fallback_disclosed: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if not self.allow_external_calls:
            raise AdapterExternalCallsDisabled(
                "External calls are disabled. Set allow_external_calls=true for experiment code."
            )
        if not self.api_key.strip():
            raise AdapterMissingCredentials(
                "OpenAI experiment-code generation requested without an API key."
            )
        if not self.model.strip():
            raise ValueError("OpenAI experiment-code generation requires a model name.")

    def generate_code(
        self,
        *,
        spec_payload: dict[str, Any],
        substrate_payload: dict[str, Any],
        allowed_dependencies: list[str],
    ) -> ExperimentCodeGenerationResponse:
        prompt, schema = build_experiment_codegen_prompt(
            spec_payload=spec_payload,
            substrate_payload=substrate_payload,
            allowed_dependencies=allowed_dependencies,
        )
        raw = self.transport.create_response(
            api_key=self.api_key,
            model=self.model,
            prompt=prompt,
            response_schema=schema,
        )
        payload = _json_object(raw)
        accepted, reasons = parse_experiment_codegen_response(
            payload,
            allowed_dependencies=allowed_dependencies,
        )
        return ExperimentCodeGenerationResponse(
            prompt_text=prompt,
            requested_output_schema=schema,
            raw_response=payload,
            accepted=accepted,
            rejection_reasons=reasons,
        )

    def repair_code(
        self,
        *,
        spec_payload: dict[str, Any],
        substrate_payload: dict[str, Any],
        blocked_code: str,
        audit_payload: dict[str, Any],
        allowed_dependencies: list[str],
    ) -> ExperimentCodeGenerationResponse:
        prompt, schema = build_experiment_codegen_repair_prompt(
            spec_payload=spec_payload,
            substrate_payload=substrate_payload,
            blocked_code=blocked_code,
            audit_payload=audit_payload,
            allowed_dependencies=allowed_dependencies,
        )
        raw = self.transport.create_response(
            api_key=self.api_key,
            model=self.model,
            prompt=prompt,
            response_schema=schema,
        )
        payload = _json_object(raw)
        accepted, reasons = parse_experiment_codegen_patch_response(
            payload,
            blocked_code=blocked_code,
            allowed_dependencies=allowed_dependencies,
        )
        return ExperimentCodeGenerationResponse(
            prompt_text=prompt,
            requested_output_schema=schema,
            raw_response=payload,
            accepted=accepted,
            rejection_reasons=reasons,
        )


def build_experiment_codegen_prompt(
    *,
    spec_payload: dict[str, Any],
    substrate_payload: dict[str, Any],
    allowed_dependencies: list[str],
) -> tuple[str, dict[str, Any]]:
    required_metrics = spec_payload["output_contract"]["required_metrics"]
    required_payload_fields = spec_payload["output_contract"].get(
        "required_payload_fields", []
    )
    required_logical_artifacts = spec_payload["output_contract"].get(
        "required_logical_artifacts", []
    )
    workload = spec_payload.get("workload_contract", {})
    output_limit_bytes = int(workload.get("output_limit_bytes", 1_048_576))
    workload_instructions = _workload_prompt_instructions(workload)
    required_role_functions = spec_payload.get("required_role_functions", [])
    prompt = (
        "Generate exactly one self-contained Python experiment script implementing the supplied "
        "bounded execution specification. It must implement the declared baseline, proposed "
        "method, controls, and negative controls. Use deterministic random_seed control. Write "
        "exactly one JSON file named output.json in the current working directory. That JSON must "
        "contain: metrics (with every required metric), baseline_summary, control_summary, "
        "negative_control_summary, negative_controls_passed, success_criteria_satisfied, and "
        "failure_criteria_satisfied. Do not invent or hardcode result metrics: compute them from "
        "the script's generated data and algorithms. Every metric value on every code path must "
        "be derived from non-empty execution records; do not provide numeric fallback metrics. "
        "Every required metric must be a finite int or float, or a nested dict or list whose "
        "leaves are finite ints or floats. Boolean metric leaves are invalid; encode counts and "
        "rates numerically and keep pass/fail labels outside metrics. "
        "Treat expected numerical failures such as optimizer non-convergence, singular fits, or "
        "unavailable method-cell estimates as retained failed observations: catch them at the "
        "smallest method/replicate/cell boundary, record attempted, valid, failed, convergence, "
        "and metric-availability counts, and continue independent evaluations. Do not abort the "
        "whole experiment for one expected fit failure. Never relabel a failed fit as a valid "
        "metric or silently omit it from denominators. "
        "For large full-profile workloads, process one replicate and grid cell at a time and "
        "retain bounded summaries or result records; do not hold raw samples for every cell in "
        "memory simultaneously. "
        "If no records, replications, or valid evaluations remain, raise an exception before "
        "constructing the metrics object and do not write output.json. Do not read external "
        "files, access network, "
        "invoke subprocesses or shells, use eval/exec/compile, or write any other file. Prefer the "
        f"standard library; permitted optional dependencies are {allowed_dependencies}. Return "
        "only the structured response.\n\n"
        f"Required metrics: {json.dumps(required_metrics)}\n\n"
        f"Required output fields: {json.dumps(required_payload_fields)}\n"
        "Every required output field must be a literal key in output.json itself or in a "
        "record nested inside output.json.\n\n"
        f"Execution profile and hard limits: {json.dumps(workload, sort_keys=True)}\n"
        f"{workload_instructions}\n\n"
        f"Required role functions: {json.dumps(required_role_functions)}\n"
        "Define and call every required role function. Bind metrics, baseline_summary, "
        "control_summary, and negative_control_summary to the corresponding function results; "
        "do not hardcode those summaries or success/failure booleans.\n\n"
        f"Required logical artifacts: {json.dumps(required_logical_artifacts)}\n"
        "Represent each required logical artifact as a non-empty entry in output.json under a "
        "literal logical_artifacts object. Store compact summaries, bounded samples, counts, "
        "digests, plot-ready aggregates, and audit details there; do not write additional files. "
        "Do not duplicate row-level records across summaries or logical artifacts. "
        f"The complete output.json must remain below {output_limit_bytes} bytes. "
        "A plot logical artifact is data for "
        "later deterministic rendering, not a claim that an image already exists.\n\n"
        f"Execution spec:\n{json.dumps(spec_payload, indent=2, sort_keys=True)}\n\n"
        f"Scientific substrate:\n{json.dumps(substrate_payload, indent=2, sort_keys=True)}"
    )
    return prompt, ExperimentCodeProposalEnvelope.model_json_schema()


def build_experiment_codegen_repair_prompt(
    *,
    spec_payload: dict[str, Any],
    substrate_payload: dict[str, Any],
    blocked_code: str,
    audit_payload: dict[str, Any],
    allowed_dependencies: list[str],
) -> tuple[str, dict[str, Any]]:
    """Build one bounded repair request from observed static-audit findings."""
    required_metrics = spec_payload["output_contract"]["required_metrics"]
    required_payload_fields = spec_payload["output_contract"].get(
        "required_payload_fields", []
    )
    required_logical_artifacts = spec_payload["output_contract"].get(
        "required_logical_artifacts", []
    )
    workload = spec_payload.get("workload_contract", {})
    output_limit_bytes = int(workload.get("output_limit_bytes", 1_048_576))
    workload_instructions = _workload_prompt_instructions(workload)
    required_role_functions = spec_payload.get("required_role_functions", [])
    repair_kind = str(audit_payload.get("repair_kind", "static_safety_audit"))
    scientific_repair_boundary = (
        "This is a scientific-quality implementation repair. Do not change the central question, "
        "data-generating process, primary metrics, success or failure thresholds, baselines, "
        "controls, negative controls, workload limits, or seeds in response to observed results. "
        "Fix only the supplied implementation, formula, convergence, diagnostic, or "
        "output-contract "
        "defects. An honest negative result is acceptable.\n\n"
        if repair_kind == "scientific_quality_repair"
        else ""
    )
    prompt = (
        "Repair the Python experiment script below so it resolves every supplied "
        f"{repair_kind} finding while preserving the execution specification. Return only a "
        "bounded list of exact-text edits, not a replacement script and not prose. "
        "The patch may contain at most 32 edits and may replace at most 64,000 aggregate source "
        "bytes. Each old_text must occur exactly once in the blocked script (or in the result of "
        "the preceding edit); "
        "new_text is its complete replacement and may be empty. Keep edits as small and local as "
        "possible. Do not weaken, bypass, or suppress the audit. Every "
        "required metric must be computed from non-empty execution records produced by the script. "
        "Every required metric must be a finite int or float, or a nested dict or list whose "
        "leaves are finite ints or floats. Boolean metric leaves are invalid; encode counts and "
        "rates numerically and keep pass/fail labels outside metrics. "
        "No required metric key may map to a numeric literal on any normal, exceptional, empty, or "
        "fallback code path. If no records, replications, or valid evaluations remain, raise "
        "RuntimeError before constructing metrics and do not write output.json. Preserve the "
        "handling of expected numerical failures at the smallest method/replicate/cell boundary: "
        "record failed and unavailable outcomes with complete denominators, continue independent "
        "evaluations, and do not abort the whole experiment for one optimizer or convergence "
        "failure. Do not convert failed fits into fallback metrics or exclude them silently. "
        "For large full-profile workloads, process one replicate and grid cell at a time and "
        "retain bounded summaries or result records; do not hold raw samples for every cell in "
        "memory simultaneously. "
        "Preserve the "
        "baseline, proposed method, controls, negative controls, deterministic seed, and declared "
        "output contract. Write only output.json after successful computation. Keep output.json "
        f"below {output_limit_bytes} bytes using compact aggregates, counts, bounded samples, and "
        "deterministic digests. Do not duplicate row-level records across summaries or logical "
        "artifacts. Do not access the "
        "network, external files, subprocesses, shells, eval, exec, or compile. "
        f"Permitted optional dependencies are {allowed_dependencies}. Return only the structured "
        "response.\n\n"
        f"{scientific_repair_boundary}"
        f"Required metrics: {json.dumps(required_metrics)}\n\n"
        f"Required output fields: {json.dumps(required_payload_fields)}\n\n"
        f"Execution profile and hard limits: {json.dumps(workload, sort_keys=True)}\n"
        f"{workload_instructions}\n\n"
        f"Required role functions: {json.dumps(required_role_functions)}\n"
        "Define and call every required role function, and bind the output summaries and metrics "
        "to their computed return values.\n\n"
        f"Required logical artifacts: {json.dumps(required_logical_artifacts)}\n"
        "Include every item as a non-empty literal key under output.json logical_artifacts. "
        "Write no additional files.\n\n"
        f"Exact safety audit:\n{json.dumps(audit_payload, indent=2, sort_keys=True)}\n\n"
        f"Blocked script:\n```python\n{blocked_code}\n```\n\n"
        f"Execution spec:\n{json.dumps(spec_payload, indent=2, sort_keys=True)}\n\n"
        f"Scientific substrate:\n{json.dumps(substrate_payload, indent=2, sort_keys=True)}"
    )
    return prompt, ExperimentCodePatchEnvelope.model_json_schema()


def _workload_prompt_instructions(workload: dict[str, Any]) -> str:
    profile = str(workload.get("execution_profile", "full"))
    common = (
        "Define the exact top-level literals EXECUTION_PROFILE, REPLICATIONS, RESAMPLES, and "
        "GRID_CELLS. EXECUTION_PROFILE must be a string literal; REPLICATIONS, RESAMPLES, and "
        "GRID_CELLS must each be positive integer literals and must not be collections, calls, "
        "or derived expressions. GRID_CELLS is reserved for the integer workload value; store "
        "actual grid definitions in a top-level literal list named GRID_CONFIGS."
    )
    if profile == "full":
        return (
            f"{common} This is a full profile: REPLICATIONS, RESAMPLES, and GRID_CELLS must "
            "exactly equal their configured max_* values, GRID_CONFIGS must contain exactly "
            "GRID_CELLS configurations, and the experiment must iterate over every item in "
            "GRID_CONFIGS. Use every declared grid value as written: do not silently reduce, "
            "subsample, or min/max-cap configured sample sizes or budgets. When model, method, "
            "algorithm, or estimator values vary across the grid, implement genuine computational "
            "dispatch; changing output labels while running the same implementation is invalid. "
            "Do not silently reduce, cap, or replace the configured workload."
        )
    return (
        f"{common} Their values must not exceed the supplied limits, and "
        "len(GRID_CONFIGS) <= GRID_CELLS. For a smoke profile, prefer the smallest meaningful "
        "deterministic run."
    )


def parse_experiment_codegen_response(
    payload: dict[str, Any],
    *,
    allowed_dependencies: list[str],
) -> tuple[ExperimentCodeProposal | None, list[str]]:
    normalized_payload = _normalize_transport_metadata(payload)
    try:
        envelope = ExperimentCodeProposalEnvelope.model_validate(normalized_payload)
    except ValidationError as exc:
        return None, [str(exc)]
    return _validate_experiment_codegen_proposal(
        envelope.experiment,
        allowed_dependencies=allowed_dependencies,
    )


def parse_experiment_codegen_patch_response(
    payload: dict[str, Any],
    *,
    blocked_code: str,
    allowed_dependencies: list[str],
) -> tuple[ExperimentCodeProposal | None, list[str]]:
    """Validate and apply bounded exact-text edits to a prior generated script."""
    try:
        envelope = ExperimentCodePatchEnvelope.model_validate(payload)
    except ValidationError as exc:
        return None, [str(exc)]

    proposal = envelope.repair
    repaired_code = blocked_code
    reasons: list[str] = []
    changed_source_bytes = 0
    for index, edit in enumerate(proposal.edits, start=1):
        if edit.old_text == edit.new_text:
            continue
        if edit.old_text == blocked_code:
            reasons.append(f"repair edit {index} must not replace the complete script")
            continue
        occurrence_count = repaired_code.count(edit.old_text)
        if occurrence_count != 1:
            reasons.append(
                f"repair edit {index} old_text must occur exactly once; found "
                f"{occurrence_count} occurrences"
            )
            continue
        changed_source_bytes += len(edit.old_text.encode("utf-8"))
        repaired_code = repaired_code.replace(edit.old_text, edit.new_text, 1)

    if changed_source_bytes > _MAX_PATCH_CHANGED_SOURCE_BYTES:
        reasons.append("repair edits exceed the bounded 64000-byte source-change limit")
    if not reasons:
        if repaired_code == blocked_code:
            reasons.append("repair patch did not change the blocked script")
        try:
            ast.parse(repaired_code, filename="experiment.py")
        except SyntaxError as exc:
            reasons.append(f"repaired Python is invalid: {exc.msg}")
    if reasons:
        return None, reasons

    experiment = ExperimentCodeProposal(
        language=proposal.language,
        entrypoint=proposal.entrypoint,
        code=repaired_code,
        expected_output_files=proposal.expected_output_files,
        required_inputs=proposal.required_inputs,
        declared_dependencies=proposal.declared_dependencies,
        random_seed=proposal.random_seed,
        timeout_seconds=proposal.timeout_seconds,
        network_required=proposal.network_required,
        filesystem_scope=proposal.filesystem_scope,
        publication_ready=proposal.publication_ready,
        creates_scientific_validation=proposal.creates_scientific_validation,
    )
    return _validate_experiment_codegen_proposal(
        experiment,
        allowed_dependencies=allowed_dependencies,
    )


def _validate_experiment_codegen_proposal(
    proposal: ExperimentCodeProposal,
    *,
    allowed_dependencies: list[str],
) -> tuple[ExperimentCodeProposal | None, list[str]]:
    reasons: list[str] = []
    if proposal.expected_output_files != ["output.json"]:
        reasons.append("generated code must declare exactly output.json")
    allowed = {item.casefold().replace("-", "_") for item in allowed_dependencies}
    dependencies = {item.casefold().replace("-", "_") for item in proposal.declared_dependencies}
    unsupported = sorted(dependencies - allowed)
    if unsupported:
        reasons.append(
            "generated code declares unsupported dependencies: " + ", ".join(unsupported)
        )
    if proposal.required_inputs:
        reasons.append("M102 generated experiments must be self-contained without input files")
    if reasons:
        return None, reasons
    return proposal, []


def _normalize_transport_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    """Normalize bounded transport metadata without changing the persisted raw response."""
    experiment = payload.get("experiment")
    if not isinstance(experiment, dict):
        return payload
    normalized_experiment = dict(experiment)
    for field_name in ("required_inputs", "declared_dependencies"):
        if normalized_experiment.get(field_name) is None:
            normalized_experiment[field_name] = []
    timeout_seconds = normalized_experiment.get("timeout_seconds")
    if isinstance(timeout_seconds, int) and not isinstance(timeout_seconds, bool):
        normalized_experiment["timeout_seconds"] = max(1, min(timeout_seconds, 300))
    normalized_payload = dict(payload)
    normalized_payload["experiment"] = normalized_experiment
    return normalized_payload


def _json_object(raw: Any) -> dict[str, Any]:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="llm_experiment_codegen",
            message="experiment-code response was not valid JSON",
            cause=exc,
        ) from exc
    if not isinstance(payload, dict):
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="llm_experiment_codegen",
            message="experiment-code response must be a JSON object",
        )
    return payload


__all__ = [
    "ExperimentCodeGenerationClient",
    "ExperimentCodeGenerationResponse",
    "ExperimentCodePatchEnvelope",
    "ExperimentCodePatchProposal",
    "ExperimentCodeProposal",
    "ExperimentCodeProposalEnvelope",
    "ExperimentCodeTextEdit",
    "OpenAILLMExperimentCodeGenerator",
    "build_experiment_codegen_prompt",
    "build_experiment_codegen_repair_prompt",
    "parse_experiment_codegen_patch_response",
    "parse_experiment_codegen_response",
]
