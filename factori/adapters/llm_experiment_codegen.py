"""Gated structured LLM generation of bounded Python experiment scripts."""

from __future__ import annotations

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


def build_experiment_codegen_prompt(
    *,
    spec_payload: dict[str, Any],
    substrate_payload: dict[str, Any],
    allowed_dependencies: list[str],
) -> tuple[str, dict[str, Any]]:
    required_metrics = spec_payload["output_contract"]["required_metrics"]
    prompt = (
        "Generate exactly one self-contained Python experiment script implementing the supplied "
        "bounded execution specification. It must implement the declared baseline, proposed "
        "method, controls, and negative controls. Use deterministic random_seed control. Write "
        "exactly one JSON file named output.json in the current working directory. That JSON must "
        "contain: metrics (with every required metric), baseline_summary, control_summary, "
        "negative_control_summary, negative_controls_passed, success_criteria_satisfied, and "
        "failure_criteria_satisfied. Do not invent or hardcode result metrics: compute them from "
        "the script's generated data and algorithms. Do not read external files, access network, "
        "invoke subprocesses or shells, use eval/exec/compile, or write any other file. Prefer the "
        f"standard library; permitted optional dependencies are {allowed_dependencies}. Return "
        "only the structured response.\n\n"
        f"Required metrics: {json.dumps(required_metrics)}\n\n"
        f"Execution spec:\n{json.dumps(spec_payload, indent=2, sort_keys=True)}\n\n"
        f"Scientific substrate:\n{json.dumps(substrate_payload, indent=2, sort_keys=True)}"
    )
    return prompt, ExperimentCodeProposalEnvelope.model_json_schema()


def parse_experiment_codegen_response(
    payload: dict[str, Any],
    *,
    allowed_dependencies: list[str],
) -> tuple[ExperimentCodeProposal | None, list[str]]:
    try:
        envelope = ExperimentCodeProposalEnvelope.model_validate(payload)
    except ValidationError as exc:
        return None, [str(exc)]
    proposal = envelope.experiment
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
    "ExperimentCodeProposal",
    "ExperimentCodeProposalEnvelope",
    "OpenAILLMExperimentCodeGenerator",
    "build_experiment_codegen_prompt",
    "parse_experiment_codegen_response",
]
