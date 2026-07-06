"""Gated structured LLM construction of concrete scientific substrates."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import Field, ValidationError

from factori.adapters.errors import (
    AdapterExternalCallsDisabled,
    AdapterMissingCredentials,
    AdapterResponseParseError,
)
from factori.adapters.llm_real import LLMTransport, OpenAIResponsesTransport
from factori.schemas import (
    BackendKind,
    LLMSubstratePrompt,
    ScientificSubstrateAssumption,
    ScientificSubstrateExperimentDesign,
    ScientificSubstrateModelObject,
    ScientificSubstrateResultSchema,
    ScientificSubstrateVariable,
    StrictModel,
)


class SubstrateCandidateProposal(StrictModel):
    """Adapter-local substrate before stage-owned identifiers are assigned."""

    title: str = Field(min_length=1)
    domain_problem: str = Field(min_length=1)
    central_tension: str = Field(min_length=1)
    concrete_model_object: ScientificSubstrateModelObject
    mathematical_or_computational_form: list[str] = Field(min_length=1)
    variables_and_notation: list[ScientificSubstrateVariable] = Field(min_length=1)
    assumptions: list[ScientificSubstrateAssumption] = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    baseline_candidates: list[str] = Field(min_length=1)
    experiment_or_proof_design: ScientificSubstrateExperimentDesign
    benchmark_design: str = Field(min_length=1)
    negative_controls: list[str] = Field(min_length=1)
    result_schema: ScientificSubstrateResultSchema
    expected_metrics: list[str] = Field(min_length=1)
    failure_modes: list[str] = Field(min_length=1)
    limitations: list[str] = Field(min_length=1)
    scope_boundary: str = Field(min_length=1)
    verification_path: str = Field(min_length=1)
    route_hint: str = Field(min_length=1)
    novelty_risk: str = Field(min_length=1)
    false_bridge_risk: str = Field(min_length=1)
    tautology_risk: str = Field(min_length=1)


class SubstrateScoreProposal(StrictModel):
    model_concreteness: float = Field(ge=0.0, le=1.0)
    baseline_quality: float = Field(ge=0.0, le=1.0)
    verification_feasibility: float = Field(ge=0.0, le=1.0)
    assumption_clarity: float = Field(ge=0.0, le=1.0)
    metric_clarity: float = Field(ge=0.0, le=1.0)
    negative_control_quality: float = Field(ge=0.0, le=1.0)
    failure_mode_quality: float = Field(ge=0.0, le=1.0)
    paper_coherence: float = Field(ge=0.0, le=1.0)
    false_bridge_penalty: float = Field(ge=0.0, le=1.0)
    tautology_penalty: float = Field(ge=0.0, le=1.0)
    scope_risk_penalty: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)
    score_explanation: str = Field(min_length=1)


class SubstrateProposalItem(StrictModel):
    candidate: SubstrateCandidateProposal
    score: SubstrateScoreProposal


class SubstrateProposalEnvelope(StrictModel):
    substrates: list[SubstrateProposalItem] = Field(min_length=1, max_length=1)


@dataclass(frozen=True)
class SubstrateGenerationResponse:
    prompt: LLMSubstratePrompt
    raw_response: dict[str, Any]
    accepted: SubstrateProposalItem | None
    rejection_reasons: list[str]


class SubstrateGenerationClient(Protocol):
    backend_name: str
    backend_kind: BackendKind
    model: str
    fallback_used: bool
    fallback_disclosed: bool

    def construct_substrate(
        self,
        *,
        prompt_id: str,
        source_payload: dict[str, Any],
        opportunity_payload: dict[str, Any],
        retrieval_context_payload: dict[str, Any],
    ) -> SubstrateGenerationResponse: ...


@dataclass
class OpenAILLMSubstrateGenerator:
    """Explicitly gated OpenAI substrate generator without deterministic fallback."""

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
                "External calls are disabled. Set allow_external_calls=true for LLM substrates."
            )
        if not self.api_key.strip():
            raise AdapterMissingCredentials(
                "OpenAI LLM substrate construction requested without an API key."
            )
        if not self.model.strip():
            raise ValueError("OpenAI LLM substrate construction requires a model name.")

    def construct_substrate(
        self,
        *,
        prompt_id: str,
        source_payload: dict[str, Any],
        opportunity_payload: dict[str, Any],
        retrieval_context_payload: dict[str, Any],
    ) -> SubstrateGenerationResponse:
        prompt = build_llm_substrate_prompt(
            prompt_id=prompt_id,
            backend_name=self.backend_name,
            model=self.model,
            source_payload=source_payload,
            opportunity_payload=opportunity_payload,
            retrieval_context_payload=retrieval_context_payload,
        )
        raw = self.transport.create_response(
            api_key=self.api_key,
            model=self.model,
            prompt=prompt.prompt_text,
            response_schema=prompt.requested_output_schema,
        )
        payload = _json_object(raw)
        accepted, reasons = parse_substrate_response(payload)
        return SubstrateGenerationResponse(
            prompt=prompt,
            raw_response=payload,
            accepted=accepted,
            rejection_reasons=reasons,
        )


def build_llm_substrate_prompt(
    *,
    prompt_id: str,
    backend_name: str,
    model: str,
    source_payload: dict[str, Any],
    opportunity_payload: dict[str, Any],
    retrieval_context_payload: dict[str, Any],
) -> LLMSubstratePrompt:
    schema = SubstrateProposalEnvelope.model_json_schema()
    prompt_text = (
        "Construct exactly one concrete scientific substrate from the selected LLM variance "
        "branch. Supply a model object with equations or algorithm, defined variables, explicit "
        "assumptions, falsifiable hypothesis, baselines, experiment/proof design, benchmark, "
        "negative controls, result schema, metrics, failure modes, limitations, scope boundary, "
        "verification path, and advisory route hint. Method vocabulary must map to the concrete "
        "object rather than decorate the topic. Prefix novelty_risk with 'Hypothesis:'. Do not "
        "claim proof, verification, novelty, real-world validation, complete literature coverage, "
        "or publication readiness as established. Return only the structured response.\n\n"
        f"Selected variant:\n{json.dumps(source_payload, indent=2, sort_keys=True)}\n\n"
        f"Source opportunity:\n{json.dumps(opportunity_payload, indent=2, sort_keys=True)}\n\n"
        "Retrieval context:\n"
        f"{json.dumps(retrieval_context_payload, indent=2, sort_keys=True)}"
    )
    return LLMSubstratePrompt(
        prompt_id=prompt_id,
        backend_name=backend_name,
        model=model,
        source_variant_id=str(source_payload["variant_id"]),
        source_payload=source_payload,
        opportunity_payload=opportunity_payload,
        retrieval_context_payload=retrieval_context_payload,
        prompt_text=prompt_text,
        requested_output_schema=schema,
    )


def parse_substrate_response(
    payload: dict[str, Any],
) -> tuple[SubstrateProposalItem | None, list[str]]:
    raw_items = payload.get("substrates")
    if not isinstance(raw_items, list) or len(raw_items) != 1:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="llm_substrate_construction",
            message="substrate response must contain exactly one substrate",
        )
    try:
        item = SubstrateProposalItem.model_validate(raw_items[0])
    except ValidationError as exc:
        return None, [str(exc)]
    reasons = _boundary_reasons(item.candidate)
    if reasons:
        return None, reasons
    return (
        item.model_copy(
            update={
                "candidate": item.candidate.model_copy(
                    update={"novelty_risk": _hypothesis_scope(item.candidate.novelty_risk)}
                )
            }
        ),
        [],
    )


def _boundary_reasons(candidate: SubstrateCandidateProposal) -> list[str]:
    allowed_routes = {
        "synthetic_experiment",
        "benchmark_tournament",
        "counterexample_search",
        "symbolic_derivation",
        "applied_math_reduction",
        "proof_plan",
        "literature_novelty_check",
        "defer_insufficient_substrate",
        "reject_false_bridge",
    }
    reasons = []
    if candidate.route_hint not in allowed_routes:
        reasons.append(f"unsupported route hint: {candidate.route_hint}")
    combined = " ".join(
        [
            candidate.title,
            candidate.domain_problem,
            candidate.central_tension,
            candidate.hypothesis,
            candidate.scope_boundary,
            candidate.verification_path,
        ]
    ).lower()
    forbidden = {
        "publication_ready=true": "substrate asserts publication readiness",
        "publication ready": "substrate asserts publication readiness",
        "real-world validation": "substrate asserts real-world validation",
        "real world validation": "substrate asserts real-world validation",
        "has been proven": "substrate claims proof without evidence",
        "is proven": "substrate claims proof without evidence",
        "is verified": "substrate claims verification without evidence",
        "proves novelty": "substrate asserts novelty as fact",
        "establishes novelty": "substrate asserts novelty as fact",
        " is novel": "substrate asserts novelty as fact",
    }
    reasons.extend(message for phrase, message in forbidden.items() if phrase in combined)
    return reasons


def _json_object(raw: Any) -> dict[str, Any]:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="llm_substrate_construction",
            message="substrate response was not valid JSON",
            cause=exc,
        ) from exc
    if not isinstance(payload, dict):
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="llm_substrate_construction",
            message="substrate response must be a JSON object",
        )
    return payload


def _hypothesis_scope(value: str) -> str:
    normalized = " ".join(value.split())
    if normalized.lower().startswith("hypothesis:"):
        return normalized
    return f"Hypothesis: {normalized}"


__all__ = [
    "OpenAILLMSubstrateGenerator",
    "SubstrateCandidateProposal",
    "SubstrateGenerationClient",
    "SubstrateGenerationResponse",
    "SubstrateProposalEnvelope",
    "SubstrateProposalItem",
    "SubstrateScoreProposal",
    "build_llm_substrate_prompt",
    "parse_substrate_response",
]
