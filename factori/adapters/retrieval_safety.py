"""Safety validation and response parsing for untrusted retrieval provider data."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from factori.adapters.capabilities import known_retrieval_providers
from factori.adapters.retrieval_sources import (
    normalize_doi,
    normalize_retrieval_result,
    normalize_url,
)
from factori.hashing import sha256_json
from factori.schemas import (
    HASH_RE,
    RetrievalParseReport,
    RetrievalResult,
    RetrievalValidationResult,
)

KNOWN_RETRIEVAL_PROVIDERS = known_retrieval_providers()


class RetrievalResponseError(ValueError):
    """Raised when a provider response does not contain a result collection."""


def validate_retrieval_result(result: RetrievalResult) -> RetrievalValidationResult:
    """Validate source provenance and enforce the non-verification boundary."""
    reasons: list[str] = []
    if not result.source_id.strip():
        reasons.append("retrieval result is missing source_id")
    if not result.title.strip() and not any([result.abstract, result.doi, result.authors]):
        reasons.append("retrieval result has no title or useful metadata")
    if result.provider not in KNOWN_RETRIEVAL_PROVIDERS:
        reasons.append(f"unknown retrieval provider: {result.provider}")
    if result.rank < 0:
        reasons.append("retrieval rank must be nonnegative")
    if not HASH_RE.fullmatch(result.raw_metadata_hash):
        reasons.append("raw metadata hash is missing or invalid")
    if result.url is not None and normalize_url(result.url) != result.url:
        reasons.append("retrieval URL is not normalized")
    if result.doi is not None and normalize_doi(result.doi) != result.doi:
        reasons.append("retrieval DOI is not normalized")
    if result.is_verification_evidence:
        reasons.append("retrieval output cannot be verification evidence")
    if result.proves_novelty or result.claims_literature_coverage:
        reasons.append("retrieval output cannot prove novelty or complete literature coverage")
    provenance = result.source_provenance
    if provenance.source_id != result.source_id or provenance.provider != result.provider:
        reasons.append("source provenance does not match the normalized result")
    if provenance.raw_metadata_hash != result.raw_metadata_hash:
        reasons.append("source provenance raw hash does not match the result")
    return RetrievalValidationResult(
        source_id=result.source_id,
        valid=not reasons,
        reasons=sorted(set(reasons)),
    )


def parse_retrieval_response(
    raw_response: Any,
    *,
    provider: str,
    backend: str | None = None,
    query: str,
    limit: int,
    retrieved_at: str,
) -> tuple[list[RetrievalResult], RetrievalParseReport]:
    """Normalize valid provider results and report rejected records deterministically."""
    if isinstance(raw_response, dict):
        raw_results = raw_response.get("results")
    elif isinstance(raw_response, list):
        raw_results = raw_response
    else:
        raw_results = None
    if not isinstance(raw_results, list):
        raise RetrievalResponseError("retrieval response must contain a results list")

    accepted: list[RetrievalResult] = []
    rejected: list[dict[str, Any]] = []
    for index, raw_result in enumerate(raw_results[:limit]):
        if not isinstance(raw_result, dict):
            rejected.append({"index": index, "reasons": ["result must be a JSON object"]})
            continue
        enriched = {
            **raw_result,
            "_query": query,
            "_rank": index,
            "_retrieved_at": retrieved_at,
            "_normalized_score": max(0.0, 1.0 - 0.08 * index),
        }
        try:
            result = normalize_retrieval_result(enriched, provider, backend=backend)
        except (TypeError, ValueError, ValidationError) as exc:
            rejected.append({"index": index, "reasons": [str(exc)]})
            continue
        validation = validate_retrieval_result(result)
        if validation.valid:
            accepted.append(result)
        else:
            rejected.append({"index": index, "reasons": validation.reasons})
    return accepted, RetrievalParseReport(
        provider=provider,
        raw_response_hash=sha256_json(raw_response),
        accepted_source_ids=[result.source_id for result in accepted],
        rejected_results=rejected,
        truncated=len(raw_results) > limit,
    )


__all__ = [
    "KNOWN_RETRIEVAL_PROVIDERS",
    "RetrievalResponseError",
    "parse_retrieval_response",
    "validate_retrieval_result",
]
