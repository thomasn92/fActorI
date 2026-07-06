"""Gated structured LLM generation of scientific variants."""

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
from factori.schemas import BackendKind, LLMVariancePrompt, StrictModel


class VarianceCandidateProposal(StrictModel):
    """Adapter-local scientific variant before stage-owned IDs and selection."""

    variant_family: str = Field(min_length=1)
    title: str = Field(min_length=1)
    research_question: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    theory_or_model_object: str = Field(min_length=1)
    mathematical_or_computational_form: str = Field(min_length=1)
    experiment_or_proof_plan: str = Field(min_length=1)
    benchmark_plan: str = Field(min_length=1)
    baseline_candidates: list[str] = Field(min_length=1)
    negative_controls: list[str] = Field(min_length=1)
    failure_modes: list[str] = Field(min_length=1)
    verification_path: str = Field(min_length=1)
    expected_metrics: list[str] = Field(min_length=1)
    data_regime: str = Field(min_length=1)
    paper_role: str = Field(min_length=1)
    scientific_rationale: str = Field(min_length=1)
    novelty_risk: str = Field(min_length=1)
    false_bridge_risk: str = Field(min_length=1)
    tautology_risk: str = Field(min_length=1)


class VarianceScoreProposal(StrictModel):
    """Adapter-local LLM score before stage-owned variant ID assignment."""

    specificity: float = Field(ge=0.0, le=1.0)
    branch_diversity: float = Field(ge=0.0, le=1.0)
    baseline_quality: float = Field(ge=0.0, le=1.0)
    verification_feasibility: float = Field(ge=0.0, le=1.0)
    failure_mode_value: float = Field(ge=0.0, le=1.0)
    paper_coherence: float = Field(ge=0.0, le=1.0)
    novelty_risk_penalty: float = Field(ge=0.0, le=1.0)
    false_bridge_penalty: float = Field(ge=0.0, le=1.0)
    tautology_penalty: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)
    score_explanation: str = Field(min_length=1)


class VarianceProposalItem(StrictModel):
    candidate: VarianceCandidateProposal
    score: VarianceScoreProposal


class VarianceProposalEnvelope(StrictModel):
    variants: list[VarianceProposalItem] = Field(min_length=1, max_length=7)


@dataclass(frozen=True)
class VarianceGenerationResponse:
    prompt: LLMVariancePrompt
    raw_response: dict[str, Any]
    accepted: list[VarianceProposalItem]
    rejected: list[dict[str, Any]]


class VarianceGenerationClient(Protocol):
    backend_name: str
    backend_kind: BackendKind
    model: str
    fallback_used: bool
    fallback_disclosed: bool

    def generate_variants(
        self,
        *,
        prompt_id: str,
        source_payload: dict[str, Any],
        retrieval_context_payload: dict[str, Any],
        variants_per_opportunity: int,
    ) -> VarianceGenerationResponse: ...


@dataclass
class OpenAILLMVarianceGenerator:
    """Explicitly gated OpenAI variance generator without deterministic fallback."""

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
                "External calls are disabled. Set allow_external_calls=true for LLM variance."
            )
        if not self.api_key.strip():
            raise AdapterMissingCredentials(
                "OpenAI LLM variance requested but no API key is configured."
            )
        if not self.model.strip():
            raise ValueError("OpenAI LLM variance requires a non-empty model name.")

    def generate_variants(
        self,
        *,
        prompt_id: str,
        source_payload: dict[str, Any],
        retrieval_context_payload: dict[str, Any],
        variants_per_opportunity: int,
    ) -> VarianceGenerationResponse:
        prompt = build_llm_variance_prompt(
            prompt_id=prompt_id,
            backend_name=self.backend_name,
            model=self.model,
            source_payload=source_payload,
            retrieval_context_payload=retrieval_context_payload,
            variants_per_opportunity=variants_per_opportunity,
        )
        raw = self.transport.create_response(
            api_key=self.api_key,
            model=self.model,
            prompt=prompt.prompt_text,
            response_schema=prompt.requested_output_schema,
        )
        payload = _json_object(raw)
        accepted, rejected = parse_variance_items(payload)
        return VarianceGenerationResponse(
            prompt=prompt,
            raw_response=payload,
            accepted=accepted,
            rejected=rejected,
        )


def build_llm_variance_prompt(
    *,
    prompt_id: str,
    backend_name: str,
    model: str,
    source_payload: dict[str, Any],
    retrieval_context_payload: dict[str, Any],
    variants_per_opportunity: int,
) -> LLMVariancePrompt:
    if variants_per_opportunity < 3 or variants_per_opportunity > 7:
        raise ValueError("variants_per_opportunity must be between 3 and 7")
    schema = VarianceProposalEnvelope.model_json_schema()
    source_id = str(source_payload["opportunity_id"])
    prompt_text = (
        f"Generate exactly {variants_per_opportunity} scientifically distinct variants around "
        "the supplied research opportunity. Use only these variant_family values: mechanism, "
        "robustness, counterexample, benchmark, representation, baseline_strengthening, "
        "negative_control. Include at least one benchmark or baseline_strengthening variant and "
        "at least one robustness or negative_control variant. Every variant needs a concrete "
        "question, falsifiable hypothesis, model object and form, experiment/proof plan, "
        "benchmark plan, baseline, negative control, failure mode, verification path, metrics, "
        "paper role, and scientific rationale. It must change scientific substance rather than "
        "repeat the source. Prefix novelty_risk with 'Hypothesis:'. Do not assert novelty, proof, "
        "verification, real-world validation, complete literature coverage, or publication "
        "readiness as established. Return only the structured response.\n\n"
        f"Source opportunity:\n{json.dumps(source_payload, indent=2, sort_keys=True)}\n\n"
        "Retrieval context:\n"
        f"{json.dumps(retrieval_context_payload, indent=2, sort_keys=True)}"
    )
    return LLMVariancePrompt(
        prompt_id=prompt_id,
        backend_name=backend_name,
        model=model,
        source_opportunity_id=source_id,
        requested_variant_count=variants_per_opportunity,
        source_payload=source_payload,
        retrieval_context_payload=retrieval_context_payload,
        prompt_text=prompt_text,
        requested_output_schema=schema,
    )


def parse_variance_items(
    payload: dict[str, Any],
) -> tuple[list[VarianceProposalItem], list[dict[str, Any]]]:
    raw_items = payload.get("variants")
    if not isinstance(raw_items, list):
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="llm_variance_generation",
            message="variance response must contain a variants list",
        )
    accepted: list[VarianceProposalItem] = []
    rejected: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_items):
        try:
            item = VarianceProposalItem.model_validate(raw_item)
        except ValidationError as exc:
            rejected.append({"index": index, "reasons": [str(exc)]})
            continue
        reasons = _boundary_reasons(item.candidate)
        if reasons:
            rejected.append({"index": index, "reasons": reasons})
            continue
        accepted.append(
            item.model_copy(
                update={
                    "candidate": item.candidate.model_copy(
                        update={"novelty_risk": _hypothesis_scope(item.candidate.novelty_risk)}
                    )
                }
            )
        )
    return accepted, rejected


def _boundary_reasons(candidate: VarianceCandidateProposal) -> list[str]:
    allowed_families = {
        "mechanism",
        "robustness",
        "counterexample",
        "benchmark",
        "representation",
        "baseline_strengthening",
        "negative_control",
    }
    reasons = []
    if candidate.variant_family not in allowed_families:
        reasons.append(f"unsupported variant family: {candidate.variant_family}")
    combined = " ".join(
        [
            candidate.title,
            candidate.research_question,
            candidate.hypothesis,
            candidate.theory_or_model_object,
            candidate.scientific_rationale,
        ]
    ).lower()
    forbidden = {
        "publication_ready=true": "variant asserts publication readiness",
        "publication ready": "variant asserts publication readiness",
        "real-world validation": "variant asserts real-world validation",
        "real world validation": "variant asserts real-world validation",
        "has been proven": "variant asserts proof without evidence",
        "is verified": "variant asserts verification without evidence",
        "proves novelty": "variant asserts novelty as fact",
        "establishes novelty": "variant asserts novelty as fact",
        " is novel": "variant asserts novelty as fact",
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
            operation="llm_variance_generation",
            message="variance response was not valid JSON",
            cause=exc,
        ) from exc
    if not isinstance(payload, dict):
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="llm_variance_generation",
            message="variance response must be a JSON object",
        )
    return payload


def _hypothesis_scope(value: str) -> str:
    normalized = " ".join(value.split())
    if normalized.lower().startswith("hypothesis:"):
        return normalized
    return f"Hypothesis: {normalized}"


__all__ = [
    "OpenAILLMVarianceGenerator",
    "VarianceCandidateProposal",
    "VarianceGenerationClient",
    "VarianceGenerationResponse",
    "VarianceProposalEnvelope",
    "VarianceProposalItem",
    "VarianceScoreProposal",
    "build_llm_variance_prompt",
    "parse_variance_items",
]
