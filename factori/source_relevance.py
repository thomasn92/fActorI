"""Bounded source-relevance adjudication for retrieval quality filtering."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from factori.adapters.errors import (
    AdapterExternalCallsDisabled,
    AdapterMissingCredentials,
    AdapterResponseParseError,
)
from factori.adapters.llm_real import LLMTransport, OpenAIResponsesTransport
from factori.schemas import RetrievalResult, SourceRelevanceAdjudication

_ACCEPTED_LABELS = {
    "highly_relevant_background",
    "partially_relevant_background",
}
_RELEVANCE_LABELS = [
    "highly_relevant_background",
    "partially_relevant_background",
    "weakly_relevant",
    "irrelevant",
    "metadata_insufficient",
    "duplicate",
    "unsafe_or_invalid_source",
]
_HUMAN_GEOGRAPHY_CONTEXT_TERMS = {
    "spatial",
    "urban",
    "regional",
    "migration",
    "mobility",
    "place",
    "territorial",
    "geography",
    "geographical",
    "interaction",
    "inequality",
    "planning",
}
_IRRELEVANT_DOMAIN_TERMS = {
    "coral",
    "reef",
    "marine",
    "biology",
    "ocean",
    "thermal",
    "enzyme",
    "protein",
    "astronomy",
    "galaxy",
}
_STOPWORDS = {
    "and",
    "are",
    "for",
    "from",
    "into",
    "only",
    "source",
    "study",
    "that",
    "the",
    "this",
    "with",
}


@dataclass(frozen=True)
class SourceRelevanceRequest:
    """Small non-secret source payload supplied to a relevance adjudicator."""

    source_id: str
    title: str
    source_hash: str
    query: str
    domain: str
    candidate_title_or_problem: str
    deterministic_relevance_score: float
    deterministic_topic_match_score: float
    source_quality_score: float
    source_summary: str
    supported_topics: list[str]
    provider: str
    year: int | None
    source_type: str


class SourceRelevanceAdjudicator(Protocol):
    """Relevance-only adjudicator; metadata and registry checks stay deterministic."""

    backend_name: str
    model: str | None
    call_count: int

    def adjudicate(
        self,
        requests: list[SourceRelevanceRequest],
    ) -> list[SourceRelevanceAdjudication]: ...


def source_relevance_request_from_result(
    result: RetrievalResult,
    *,
    query: str,
    domain: str,
    candidate_title_or_problem: str,
    deterministic_relevance_score: float,
    deterministic_topic_match_score: float,
    source_quality_score: float,
) -> SourceRelevanceRequest:
    """Build an adjudication request from existing normalized metadata."""
    metadata = dict(result.metadata or {})
    summary = (
        str(metadata.get("source_summary") or "")
        or result.abstract
        or result.snippet
        or ""
    )
    return SourceRelevanceRequest(
        source_id=result.source_id,
        title=result.title,
        source_hash=result.raw_metadata_hash,
        query=query,
        domain=domain,
        candidate_title_or_problem=candidate_title_or_problem,
        deterministic_relevance_score=deterministic_relevance_score,
        deterministic_topic_match_score=deterministic_topic_match_score,
        source_quality_score=source_quality_score,
        source_summary=summary,
        supported_topics=[str(item) for item in metadata.get("supported_topics", []) or []],
        provider=result.provider,
        year=result.year,
        source_type=str(metadata.get("source_type") or result.source_type),
    )


def deterministic_source_relevance_adjudication(
    request: SourceRelevanceRequest,
    *,
    backend: str = "deterministic_filter",
    model: str | None = None,
    forced_label: str | None = None,
    forced_rejection_reason: str | None = None,
) -> SourceRelevanceAdjudication:
    """Return a deterministic bounded source-relevance judgment."""
    if forced_label is not None:
        label = forced_label
        score = _score_for_label(label, request.deterministic_relevance_score)
        accepted = label in _ACCEPTED_LABELS
        reason = None if accepted else forced_rejection_reason or _reason_for_label(label)
        reasoning = _forced_reasoning(label, forced_rejection_reason)
        confidence = 1.0 if label in {"duplicate", "metadata_insufficient"} else 0.95
    else:
        label, score, accepted, reason, reasoning, confidence = _fake_relevance_decision(
            request
        )
    return SourceRelevanceAdjudication(
        source_id=request.source_id,
        title=request.title,
        source_hash=request.source_hash,
        query=request.query,
        domain=request.domain,
        candidate_title_or_problem=request.candidate_title_or_problem,
        deterministic_relevance_score=request.deterministic_relevance_score,
        adjudicated_relevance_label=label,  # type: ignore[arg-type]
        adjudicated_relevance_score=score,
        accepted_for_background_context=accepted,
        rejection_reason=reason,
        reasoning_brief=reasoning,
        confidence=confidence,
        adjudicator_backend=backend,
        adjudicator_model=model,
    )


@dataclass
class FakeSourceRelevanceAdjudicator:
    """Deterministic source-relevance adjudicator for tests and local smoke runs."""

    model: str | None = None
    backend_name: str = "fake"
    call_count: int = 0
    adjudication_requests: list[dict[str, Any]] = field(default_factory=list)
    raw_responses: list[Any] = field(default_factory=list)

    def adjudicate(
        self,
        requests: list[SourceRelevanceRequest],
    ) -> list[SourceRelevanceAdjudication]:
        if requests:
            self.call_count += 1
            payload = _request_payload(requests)
            self.adjudication_requests.append(payload)
        results = [
            deterministic_source_relevance_adjudication(
                request,
                backend=self.backend_name,
                model=self.model,
            )
            for request in requests
        ]
        if requests:
            self.raw_responses.append(
                {"adjudications": [item.model_dump(mode="json") for item in results]}
            )
        return results


@dataclass
class OpenAISourceRelevanceAdjudicator:
    """Explicitly gated OpenAI adjudicator for bounded source relevance only."""

    api_key: str = field(repr=False)
    model: str
    transport: LLMTransport = field(default_factory=OpenAIResponsesTransport)
    allow_external_calls: bool = False
    max_calls: int = 4
    batch_size: int = 12
    backend_name: str = field(default="openai", init=False)
    provider_name: str = field(default="openai", init=False)
    call_count: int = field(default=0, init=False)
    adjudication_requests: list[dict[str, Any]] = field(default_factory=list, init=False)
    raw_responses: list[Any] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if not self.allow_external_calls:
            raise AdapterExternalCallsDisabled(
                "External calls are disabled. Set allow_external_calls=true to use the "
                "OpenAI source relevance adjudicator."
            )
        if not self.api_key.strip():
            raise AdapterMissingCredentials(
                "OpenAI source relevance adjudicator requested but no API key is configured."
            )
        if not self.model.strip():
            raise ValueError("OpenAI source relevance adjudicator requires a model name.")
        if self.max_calls < 1:
            raise ValueError("OpenAI source relevance adjudicator requires max_calls >= 1.")

    def adjudicate(
        self,
        requests: list[SourceRelevanceRequest],
    ) -> list[SourceRelevanceAdjudication]:
        results: list[SourceRelevanceAdjudication] = []
        for start in range(0, len(requests), self.batch_size):
            batch = requests[start : start + self.batch_size]
            if self.call_count >= self.max_calls:
                results.extend(
                    deterministic_source_relevance_adjudication(
                        request,
                        backend="deterministic_fallback",
                        model=self.model,
                        forced_label=(
                            "partially_relevant_background"
                            if request.deterministic_relevance_score >= 0.35
                            else "weakly_relevant"
                        ),
                        forced_rejection_reason=(
                            None
                            if request.deterministic_relevance_score >= 0.35
                            else "source_relevance_call_budget_exceeded"
                        ),
                    )
                    for request in batch
                )
                continue
            payload = _request_payload(batch)
            raw = self.transport.create_response(
                api_key=self.api_key,
                model=self.model,
                prompt=_adjudicator_prompt(payload),
                response_schema=_response_schema(),
            )
            self.call_count += 1
            self.adjudication_requests.append(payload)
            self.raw_responses.append(raw)
            results.extend(_parse_response(raw, batch, self.model))
        return results


def _fake_relevance_decision(
    request: SourceRelevanceRequest,
) -> tuple[str, float, bool, str | None, str, float]:
    text = " ".join(
        [
            request.title,
            request.source_summary,
            " ".join(request.supported_topics),
            request.source_type,
        ]
    ).casefold()
    tokens = _tokens(text)
    query_tokens = _tokens(request.query)
    domain_tokens = _tokens(request.domain)
    problem_tokens = _tokens(request.candidate_title_or_problem)
    positive_tokens = query_tokens | domain_tokens | problem_tokens
    overlap = len(tokens & positive_tokens)
    has_human_geography_context = bool(tokens & _HUMAN_GEOGRAPHY_CONTEXT_TERMS)
    has_irrelevant_terms = bool(tokens & _IRRELEVANT_DOMAIN_TERMS)
    if has_irrelevant_terms and overlap <= 1:
        return (
            "irrelevant",
            min(0.24, request.deterministic_relevance_score),
            False,
            "low_relevance",
            "The source metadata points to a different domain than the bounded query.",
            0.92,
        )
    if request.deterministic_relevance_score >= 0.58 and overlap >= 2:
        return (
            "highly_relevant_background",
            max(request.deterministic_relevance_score, 0.78),
            True,
            None,
            "The title, summary, or supported topics overlap with the bounded query.",
            0.9,
        )
    if has_human_geography_context or overlap >= 2:
        return (
            "partially_relevant_background",
            max(request.deterministic_relevance_score, 0.62),
            True,
            None,
            "The metadata is relevant enough for bounded background context only.",
            0.84,
        )
    return (
        "weakly_relevant",
        min(max(request.deterministic_relevance_score, 0.2), 0.49),
        False,
        "low_relevance",
        "The metadata has too little topical overlap for registry background context.",
        0.86,
    )


def _request_payload(requests: list[SourceRelevanceRequest]) -> dict[str, Any]:
    return {
        "sources": [
            {
                "source_id": request.source_id,
                "title": request.title,
                "source_hash": request.source_hash,
                "query": request.query,
                "domain": request.domain,
                "candidate_title_or_problem": request.candidate_title_or_problem,
                "deterministic_relevance_score": request.deterministic_relevance_score,
                "deterministic_topic_match_score": request.deterministic_topic_match_score,
                "source_quality_score": request.source_quality_score,
                "source_summary": request.source_summary,
                "supported_topics": request.supported_topics,
                "provider": request.provider,
                "year": request.year,
                "source_type": request.source_type,
            }
            for request in requests
        ],
        "boundary": (
            "Source relevance is bounded background context only. It is not novelty, "
            "correctness, validation, publication readiness, or verification evidence."
        ),
    }


def _adjudicator_prompt(payload: dict[str, Any]) -> str:
    return (
        "Judge whether each supplied retrieval source is relevant enough for bounded "
        "background context. Use only the supplied title, summary, topics, and metadata. "
        "Do not invent missing source metadata. Do not claim exhaustive literature coverage, "
        "novelty, correctness, validation, proof, empirical support, human review, or "
        "publication readiness. Hard metadata and duplicate filters are handled by code; "
        "you are judging topical relevance only for the supplied candidate sources. "
        "Return one adjudication per source.\n\n"
        + json.dumps(payload, sort_keys=True, ensure_ascii=True)
    )


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "adjudications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "source_id": {"type": "string"},
                        "relevance_label": {"type": "string", "enum": _RELEVANCE_LABELS},
                        "relevance_score": {"type": "number", "minimum": 0, "maximum": 1},
                        "accepted_for_background_context": {"type": "boolean"},
                        "rejection_reason": {"type": ["string", "null"]},
                        "reasoning_brief": {"type": "string", "maxLength": 400},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": [
                        "source_id",
                        "relevance_label",
                        "relevance_score",
                        "accepted_for_background_context",
                        "rejection_reason",
                        "reasoning_brief",
                        "confidence",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["adjudications"],
        "additionalProperties": False,
    }


def _parse_response(
    raw: Any,
    requests: list[SourceRelevanceRequest],
    model: str,
) -> list[SourceRelevanceAdjudication]:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
        rows = payload["adjudications"]
        by_id = {request.source_id: request for request in requests}
        parsed = []
        for row in rows:
            request = by_id[row["source_id"]]
            parsed.append(
                _normalized_adjudication(
                    request=request,
                    label=row["relevance_label"],
                    score=row["relevance_score"],
                    accepted=row["accepted_for_background_context"],
                    rejection_reason=row["rejection_reason"],
                    reasoning_brief=row["reasoning_brief"],
                    confidence=row["confidence"],
                    model=model,
                )
            )
        if {item.source_id for item in parsed} != set(by_id):
            raise ValueError("response did not adjudicate every requested source")
        return parsed
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="source_relevance_adjudication",
            message=(
                "source relevance adjudicator returned invalid structured output "
                f"for model={model}"
            ),
            cause=exc,
        ) from exc


def _normalized_adjudication(
    *,
    request: SourceRelevanceRequest,
    label: str,
    score: float,
    accepted: bool,
    rejection_reason: str | None,
    reasoning_brief: str,
    confidence: float,
    model: str,
) -> SourceRelevanceAdjudication:
    normalized_label = label if label in _RELEVANCE_LABELS else "weakly_relevant"
    normalized_accepted = normalized_label in _ACCEPTED_LABELS and accepted
    normalized_reason = None if normalized_accepted else rejection_reason or _reason_for_label(
        normalized_label
    )
    return SourceRelevanceAdjudication(
        source_id=request.source_id,
        title=request.title,
        source_hash=request.source_hash,
        query=request.query,
        domain=request.domain,
        candidate_title_or_problem=request.candidate_title_or_problem,
        deterministic_relevance_score=request.deterministic_relevance_score,
        adjudicated_relevance_label=normalized_label,  # type: ignore[arg-type]
        adjudicated_relevance_score=max(0.0, min(1.0, float(score))),
        accepted_for_background_context=normalized_accepted,
        rejection_reason=normalized_reason,
        reasoning_brief=reasoning_brief[:400] or "No reasoning supplied.",
        confidence=max(0.0, min(1.0, float(confidence))),
        adjudicator_backend="openai",
        adjudicator_model=model,
    )


def _forced_reasoning(label: str, reason: str | None) -> str:
    if reason:
        return f"Deterministic source filter recorded {reason}."
    if label in _ACCEPTED_LABELS:
        return "Deterministic source filter accepted the source as bounded context."
    return "Deterministic source filter rejected the source for registry context."


def _score_for_label(label: str, fallback: float) -> float:
    if label == "highly_relevant_background":
        return max(fallback, 0.8)
    if label == "partially_relevant_background":
        return max(fallback, 0.6)
    if label == "weakly_relevant":
        return min(max(fallback, 0.25), 0.49)
    return min(fallback, 0.2)


def _reason_for_label(label: str) -> str:
    if label == "metadata_insufficient":
        return "metadata_incomplete"
    if label == "duplicate":
        return "duplicate_source"
    if label == "unsafe_or_invalid_source":
        return "unsafe_or_invalid_source"
    return "low_relevance"


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]*", value.casefold())
        if len(token) > 2 and token not in _STOPWORDS
    }


__all__ = [
    "FakeSourceRelevanceAdjudicator",
    "OpenAISourceRelevanceAdjudicator",
    "SourceRelevanceAdjudicator",
    "SourceRelevanceRequest",
    "deterministic_source_relevance_adjudication",
    "source_relevance_request_from_result",
]
