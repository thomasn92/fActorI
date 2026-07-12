"""Gated structured LLM generation for retrieval-contextualized opportunities."""

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
from factori.schemas import BackendKind, StrictModel


class OpportunityProposal(StrictModel):
    """Adapter-local opportunity proposal before stage-owned identifiers are assigned."""

    research_question: str = Field(min_length=1)
    hypothesis: str = Field(min_length=1)
    theory_or_model_object: str = Field(min_length=1)
    mathematical_or_computational_form: str = Field(min_length=1)
    experiment_or_proof_plan: str = Field(min_length=1)
    benchmark_plan: str = Field(min_length=1)
    baseline_candidates: list[str] = Field(min_length=1)
    expected_metrics: list[str] = Field(min_length=1)
    failure_modes: list[str] = Field(min_length=1)
    negative_controls: list[str] = Field(min_length=1)
    data_regime: str = Field(min_length=1)
    verification_path: str = Field(min_length=1)
    paper_shape: str = Field(min_length=1)
    novelty_risk: str = Field(min_length=1)
    underuse_hypothesis: str = Field(min_length=1)
    retrieval_support_summary: str = Field(min_length=1)
    retrieval_contradictions: list[str] = Field(default_factory=list)
    false_bridge_risks: list[str] = Field(default_factory=list)
    tautology_risks: list[str] = Field(default_factory=list)
    recommended_next_stage: str = Field(min_length=1)


class OpportunityScoreProposal(StrictModel):
    """Adapter-local LLM score before stage-owned opportunity ID assignment."""

    scientific_fit: float = Field(ge=0.0, le=1.0)
    tractability: float = Field(ge=0.0, le=1.0)
    question_specificity: float = Field(ge=0.0, le=1.0)
    baseline_strength: float = Field(ge=0.0, le=1.0)
    verification_feasibility: float = Field(ge=0.0, le=1.0)
    expected_signal: float = Field(ge=0.0, le=1.0)
    failure_mode_value: float = Field(ge=0.0, le=1.0)
    paper_coherence: float = Field(ge=0.0, le=1.0)
    novelty_risk_penalty: float = Field(ge=0.0, le=1.0)
    false_bridge_penalty: float = Field(ge=0.0, le=1.0)
    tautology_penalty: float = Field(ge=0.0, le=1.0)
    retrieval_confidence: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)
    score_explanation: str = Field(min_length=1)


class OpportunityProposalItem(StrictModel):
    """One structured opportunity plus its LLM scientific judgment."""

    candidate: OpportunityProposal
    score: OpportunityScoreProposal


class OpportunityProposalEnvelope(StrictModel):
    """OpenAI structured-output envelope for one selected atlas pair."""

    opportunities: list[OpportunityProposalItem] = Field(min_length=1, max_length=4)


@dataclass(frozen=True)
class OpportunityGenerationResponse:
    """Parsed response plus rejected item diagnostics and raw provenance."""

    prompt_text: str
    requested_output_schema: dict[str, Any]
    raw_response: dict[str, Any]
    accepted: list[OpportunityProposalItem]
    rejected: list[dict[str, Any]]


class OpportunityDiscoveryClient(Protocol):
    """Narrow non-fake backend seam for scientific opportunity generation."""

    backend_name: str
    backend_kind: BackendKind
    model: str
    fallback_used: bool
    fallback_disclosed: bool

    def generate_for_pair(
        self,
        *,
        pair_payload: dict[str, Any],
        retrieval_payload: dict[str, Any],
        opportunities_per_pair: int,
    ) -> OpportunityGenerationResponse: ...


@dataclass
class OpenAIDeepOpportunityGenerator:
    """Explicitly gated OpenAI generator with no deterministic scientific fallback."""

    api_key: str = field(repr=False)
    model: str
    transport: LLMTransport = field(
        default_factory=lambda: OpenAIResponsesTransport(
            schema_name="factori_deep_opportunities",
            nullable_optional_fields=False,
        )
    )
    allow_external_calls: bool = False
    backend_name: str = field(default="llm-openai", init=False)
    backend_kind: BackendKind = field(default=BackendKind.LLM_OPENAI, init=False)
    fallback_used: bool = field(default=False, init=False)
    fallback_disclosed: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if not self.allow_external_calls:
            raise AdapterExternalCallsDisabled(
                "External calls are disabled. Set allow_external_calls=true for deep "
                "opportunity discovery."
            )
        if not self.api_key.strip():
            raise AdapterMissingCredentials(
                "OpenAI deep opportunity discovery requested but no API key is configured."
            )
        if not self.model.strip():
            raise ValueError("OpenAI deep opportunity discovery requires a non-empty model name.")

    def generate_for_pair(
        self,
        *,
        pair_payload: dict[str, Any],
        retrieval_payload: dict[str, Any],
        opportunities_per_pair: int,
    ) -> OpportunityGenerationResponse:
        prompt = build_deep_opportunity_prompt(
            pair_payload=pair_payload,
            retrieval_payload=retrieval_payload,
            opportunities_per_pair=opportunities_per_pair,
        )
        schema = OpportunityProposalEnvelope.model_json_schema()
        raw = self.transport.create_response(
            api_key=self.api_key,
            model=self.model,
            prompt=prompt,
            response_schema=schema,
        )
        payload = _json_object(raw)
        accepted, rejected = parse_opportunity_items(payload)
        return OpportunityGenerationResponse(
            prompt_text=prompt,
            requested_output_schema=schema,
            raw_response=payload,
            accepted=accepted,
            rejected=rejected,
        )


def build_deep_opportunity_prompt(
    *,
    pair_payload: dict[str, Any],
    retrieval_payload: dict[str, Any],
    opportunities_per_pair: int,
) -> str:
    """Build the scientific-generation contract for one selected atlas pair."""
    if opportunities_per_pair < 2 or opportunities_per_pair > 4:
        raise ValueError("opportunities_per_pair must be between 2 and 4")
    return (
        f"Generate exactly {opportunities_per_pair} concrete, scientifically distinct research "
        "opportunities for the supplied domain-method pair. Every opportunity must contain a "
        "specific research question, falsifiable hypothesis, concrete mathematical or "
        "computational object, explicit form, experiment/proof plan, benchmark plan, at least "
        "one baseline, expected metrics, failure modes, negative controls, data regime, "
        "verification path, and coherent paper shape. Score each opportunity using every score "
        "field in the schema. Use retrieval only as bounded literature context. Prefix "
        "novelty_risk and underuse_hypothesis with 'Hypothesis:'. Never assert novelty, "
        "underuse, complete literature coverage, real-world validation, proof, or publication "
        "readiness as established. Reject decorative method vocabulary by mapping the method "
        "to a concrete domain object. Do not return a literature summary without a testable "
        "scientific object. Return only the structured response.\n\n"
        f"Pair metadata:\n{json.dumps(pair_payload, indent=2, sort_keys=True)}\n\n"
        f"Retrieval context:\n{json.dumps(retrieval_payload, indent=2, sort_keys=True)}"
    )


def parse_opportunity_items(
    payload: dict[str, Any],
) -> tuple[list[OpportunityProposalItem], list[dict[str, Any]]]:
    """Validate candidates independently so one malformed proposal does not hide the rest."""
    raw_items = payload.get("opportunities")
    if not isinstance(raw_items, list):
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="deep_opportunity_discovery",
            message="opportunity response must contain an opportunities list",
        )
    accepted: list[OpportunityProposalItem] = []
    rejected: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_items):
        try:
            item = OpportunityProposalItem.model_validate(raw_item)
        except ValidationError as exc:
            rejected.append({"index": index, "reasons": [str(exc)]})
            continue
        reasons = _scientific_boundary_reasons(item.candidate)
        if reasons:
            rejected.append({"index": index, "reasons": reasons})
            continue
        accepted.append(
            item.model_copy(
                update={
                    "candidate": item.candidate.model_copy(
                        update={
                            "novelty_risk": _hypothesis_scope(
                                item.candidate.novelty_risk
                            ),
                            "underuse_hypothesis": _hypothesis_scope(
                                item.candidate.underuse_hypothesis
                            ),
                        }
                    )
                }
            )
        )
    return accepted, rejected


def _json_object(raw: Any) -> dict[str, Any]:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="deep_opportunity_discovery",
            message="opportunity response was not valid JSON",
            cause=exc,
        ) from exc
    if not isinstance(payload, dict):
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="deep_opportunity_discovery",
            message="opportunity response must be a JSON object",
        )
    return payload


def _hypothesis_scope(value: str) -> str:
    normalized = " ".join(value.split())
    if normalized.lower().startswith("hypothesis:"):
        return normalized
    return f"Hypothesis: {normalized}"


def _scientific_boundary_reasons(candidate: OpportunityProposal) -> list[str]:
    combined = " ".join(
        [
            candidate.research_question,
            candidate.hypothesis,
            candidate.theory_or_model_object,
            candidate.experiment_or_proof_plan,
            candidate.benchmark_plan,
            candidate.retrieval_support_summary,
        ]
    ).lower()
    forbidden = {
        "publication_ready=true": "opportunity asserts publication readiness",
        "publication ready": "opportunity asserts publication readiness",
        "real-world validation": "opportunity asserts real-world validation",
        "real world validation": "opportunity asserts real-world validation",
        "proves novelty": "opportunity asserts novelty as fact",
        "establishes novelty": "opportunity asserts novelty as fact",
        " is novel": "opportunity asserts novelty as fact",
        "novel contribution": "opportunity asserts novelty as fact",
        " is underused": "opportunity asserts underuse as fact",
        " is underexplored": "opportunity asserts underuse as fact",
        "complete literature coverage": "opportunity asserts complete literature coverage",
    }
    return [message for phrase, message in forbidden.items() if phrase in combined]


__all__ = [
    "OpenAIDeepOpportunityGenerator",
    "OpportunityDiscoveryClient",
    "OpportunityGenerationResponse",
    "OpportunityProposal",
    "OpportunityProposalEnvelope",
    "OpportunityProposalItem",
    "OpportunityScoreProposal",
    "build_deep_opportunity_prompt",
    "parse_opportunity_items",
]
