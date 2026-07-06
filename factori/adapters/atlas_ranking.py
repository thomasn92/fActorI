"""Gated structured LLM ranking for compatible domain-method atlas pairs."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import ValidationError

from factori.adapters.errors import (
    AdapterExternalCallsDisabled,
    AdapterMissingCredentials,
    AdapterResponseParseError,
)
from factori.adapters.llm_real import LLMTransport, OpenAIResponsesTransport
from factori.schemas import (
    BackendKind,
    LLMPairRankingPrompt,
    LLMPairRankingResult,
    StrictModel,
)


class PairRankingEnvelope(StrictModel):
    """Adapter-local OpenAI structured-output envelope."""

    results: list[LLMPairRankingResult]


class PairRankingClient(Protocol):
    """Narrow backend seam for scientific pair-ranking judgment."""

    backend_name: str
    backend_kind: BackendKind
    model: str
    fallback_used: bool
    fallback_disclosed: bool

    def rank_batch(
        self,
        *,
        pair_payloads: list[dict[str, Any]],
        batch_index: int,
        prompt_id: str,
    ) -> tuple[LLMPairRankingPrompt, list[LLMPairRankingResult]]: ...


@dataclass
class OpenAIAtlasPairRanker:
    """Explicitly gated OpenAI pair ranker with no deterministic fallback."""

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
                "External calls are disabled. Set allow_external_calls=true for atlas ranking."
            )
        if not self.api_key.strip():
            raise AdapterMissingCredentials(
                "OpenAI atlas ranking requested but no API key is configured."
            )
        if not self.model.strip():
            raise ValueError("OpenAI atlas ranking requires a non-empty model name.")

    def rank_batch(
        self,
        *,
        pair_payloads: list[dict[str, Any]],
        batch_index: int,
        prompt_id: str,
    ) -> tuple[LLMPairRankingPrompt, list[LLMPairRankingResult]]:
        prompt = build_pair_ranking_prompt(
            pair_payloads=pair_payloads,
            batch_index=batch_index,
            prompt_id=prompt_id,
            backend_name=self.backend_name,
            model=self.model,
        )
        raw = self.transport.create_response(
            api_key=self.api_key,
            model=self.model,
            prompt=prompt.prompt_text,
            response_schema=prompt.requested_output_schema,
        )
        envelope = _parse_envelope(raw)
        _validate_exact_pair_coverage(
            requested_ids=prompt.pair_ids,
            results=envelope.results,
        )
        return prompt, envelope.results


def build_pair_ranking_prompt(
    *,
    pair_payloads: list[dict[str, Any]],
    batch_index: int,
    prompt_id: str,
    backend_name: str,
    model: str,
) -> LLMPairRankingPrompt:
    """Build the strict scientific-judgment contract for one ranking batch."""
    if not pair_payloads:
        raise ValueError("pair_payloads must not be empty")
    pair_ids = [str(payload["pair_id"]) for payload in pair_payloads]
    prompt_text = (
        "Rank every supplied domain-method pair for deep opportunity discovery. "
        "Score scientific_fit, tractability, question_abundance, baseline_clarity, "
        "verification_feasibility, paper_shape_clarity, false_bridge_risk, and "
        "tautology_risk from 0 to 1. rank_score must summarize those judgments. "
        "Novelty and underuse are not established because no literature retrieval is "
        "available: both fields must begin with 'Hypothesis:' and remain explicitly "
        "tentative. Return exactly one result for every pair_id, with no extra IDs.\n\n"
        f"Pairs:\n{json.dumps(pair_payloads, indent=2, sort_keys=True)}"
    )
    return LLMPairRankingPrompt(
        prompt_id=prompt_id,
        batch_index=batch_index,
        backend_name=backend_name,
        model=model,
        pair_ids=pair_ids,
        pair_payloads=pair_payloads,
        prompt_text=prompt_text,
        requested_output_schema=PairRankingEnvelope.model_json_schema(),
        novelty_underuse_are_hypotheses=True,
    )


def _parse_envelope(raw: Any) -> PairRankingEnvelope:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
        return PairRankingEnvelope.model_validate(payload)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="atlas_pair_ranking",
            message="pair-ranking response did not satisfy the structured contract",
            cause=exc,
        ) from exc


def _validate_exact_pair_coverage(
    *,
    requested_ids: list[str],
    results: list[LLMPairRankingResult],
) -> None:
    returned_ids = [result.pair_id for result in results]
    if len(returned_ids) != len(set(returned_ids)):
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="atlas_pair_ranking",
            message="pair-ranking response contains duplicate pair IDs",
        )
    missing = sorted(set(requested_ids).difference(returned_ids))
    unexpected = sorted(set(returned_ids).difference(requested_ids))
    if missing or unexpected:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="atlas_pair_ranking",
            message=(
                "pair-ranking response coverage mismatch; "
                f"missing={missing}; unexpected={unexpected}"
            ),
        )


__all__ = [
    "OpenAIAtlasPairRanker",
    "PairRankingClient",
    "PairRankingEnvelope",
    "build_pair_ranking_prompt",
]
