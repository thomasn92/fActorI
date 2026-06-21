"""Deterministic query construction and source normalization for retrieval adapters."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit, urlunsplit

from factori.hashing import sha256_json
from factori.schemas import (
    RetrievalQuery,
    RetrievalResult,
    RetrievedDocument,
    SourceProvenance,
)

OPENALEX_WORKS_ENDPOINT = "https://api.openalex.org/works"
NORMALIZATION_EPOCH = "1970-01-01T00:00:00Z"


def build_retrieval_query(
    query: str,
    limit: int,
    provider: str = "openalex",
) -> RetrievalQuery:
    """Build a stable provider query without credentials or mutable timestamps."""
    normalized_query = " ".join(query.split())
    normalized_provider = provider.strip().lower()
    if not normalized_query:
        raise ValueError("retrieval query must not be empty")
    if limit < 1:
        raise ValueError("retrieval limit must be at least 1")
    if normalized_provider != "openalex":
        raise ValueError(f"unknown retrieval provider: {normalized_provider}")
    identity = {
        "query": normalized_query,
        "limit": limit,
        "provider": normalized_provider,
    }
    return RetrievalQuery(
        query_id=sha256_json(identity)[:16],
        query=normalized_query,
        provider=normalized_provider,
        limit=limit,
        endpoint=OPENALEX_WORKS_ENDPOINT,
        parameters={
            "search": normalized_query,
            "per-page": limit,
            "select": (
                "id,display_name,authorships,publication_year,primary_location,doi,"
                "abstract_inverted_index,relevance_score"
            ),
        },
        requires_credentials=True,
    )


def normalize_retrieval_result(
    raw_result: dict[str, Any],
    provider: str,
) -> RetrievalResult:
    """Normalize one provider result into the closed retrieval schema."""
    if not isinstance(raw_result, dict):
        raise TypeError("retrieval result must be a JSON object")
    normalized_provider = provider.strip().lower()
    if normalized_provider != "openalex":
        raise ValueError(f"unknown retrieval provider: {normalized_provider}")
    source_id = _normalize_openalex_id(raw_result.get("id"))
    if not source_id:
        raise ValueError("retrieval result is missing source_id")
    abstract = _openalex_abstract(raw_result.get("abstract_inverted_index"))
    doi = normalize_doi(raw_result.get("doi"))
    title_value = raw_result.get("display_name") or raw_result.get("title")
    title = _optional_text(title_value)
    if title is None and not any([abstract, doi, raw_result.get("authorships")]):
        raise ValueError("retrieval result has no title or useful metadata")
    title = title or f"Untitled source {source_id}"
    authors = _openalex_authors(raw_result.get("authorships"))
    year = raw_result.get("publication_year")
    if not isinstance(year, int) or year < 0:
        year = None
    venue, landing_page = _openalex_location(raw_result.get("primary_location"))
    url = normalize_url(raw_result.get("url") or landing_page)
    query = _optional_text(raw_result.get("_query")) or "unspecified query"
    rank_value = raw_result.get("_rank", 0)
    if not isinstance(rank_value, int) or rank_value < 0:
        raise ValueError("retrieval rank must be a nonnegative integer")
    retrieved_at = _optional_text(raw_result.get("_retrieved_at")) or NORMALIZATION_EPOCH
    score = _normalized_score(raw_result.get("_normalized_score"))
    raw_payload = _provider_payload(raw_result)
    raw_hash = sha256_json(raw_payload)
    provenance = SourceProvenance(
        source_id=source_id,
        provider=normalized_provider,
        query=query,
        rank=rank_value,
        retrieved_at=retrieved_at,
        raw_metadata_hash=raw_hash,
        url=url,
        doi=doi,
    )
    return RetrievalResult(
        source_id=source_id,
        title=title,
        authors=authors,
        year=year,
        venue=venue,
        abstract=abstract,
        url=url,
        doi=doi,
        provider=normalized_provider,
        retrieved_at=retrieved_at,
        query=query,
        rank=rank_value,
        score=score,
        raw_metadata_hash=raw_hash,
        source_provenance=provenance,
        snippet=(abstract[:280] if abstract else title),
        metadata={
            "provider_record_type": raw_result.get("type"),
            "cited_by_count": raw_result.get("cited_by_count"),
        },
        fake=False,
    )


def normalize_retrieved_document(
    raw_payload: dict[str, Any],
    provider: str,
    *,
    retrieved_at: str = NORMALIZATION_EPOCH,
) -> RetrievedDocument:
    """Normalize fetched provider metadata or abstract without claiming full text."""
    enriched = dict(raw_payload)
    enriched.setdefault("_query", "direct fetch")
    enriched.setdefault("_rank", 0)
    enriched.setdefault("_retrieved_at", retrieved_at)
    result = normalize_retrieval_result(enriched, provider)
    raw_hash = sha256_json(_provider_payload(raw_payload))
    text = result.abstract
    return RetrievedDocument(
        source_id=result.source_id,
        provider=result.provider,
        title=result.title,
        metadata={
            "authors": result.authors,
            "year": result.year,
            "venue": result.venue,
            "url": result.url,
            "doi": result.doi,
        },
        text_or_abstract=text,
        content=text or result.title,
        raw_payload_hash=raw_hash,
        retrieved_at=retrieved_at,
        fetch_status="MetadataOrAbstractFetched",
        fake=False,
    )


def normalize_url(value: Any) -> str | None:
    """Return a normalized HTTP(S) URL or None for unsupported input."""
    text = _optional_text(value)
    if text is None:
        return None
    parsed = urlsplit(text)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return urlunsplit(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path,
            parsed.query,
            "",
        )
    )


def normalize_doi(value: Any) -> str | None:
    """Return a lowercase DOI without a resolver prefix."""
    text = _optional_text(value)
    if text is None:
        return None
    lowered = text.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if lowered.startswith(prefix):
            lowered = lowered[len(prefix) :]
            break
    return lowered if lowered.startswith("10.") and "/" in lowered else None


def _normalize_openalex_id(value: Any) -> str | None:
    text = _optional_text(value)
    if text is None:
        return None
    source_id = text.rstrip("/").rsplit("/", maxsplit=1)[-1]
    return source_id if source_id else None


def _openalex_authors(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    authors = []
    for authorship in value:
        if not isinstance(authorship, dict):
            continue
        author = authorship.get("author")
        if isinstance(author, dict):
            name = _optional_text(author.get("display_name"))
            if name:
                authors.append(name)
    return list(dict.fromkeys(authors))


def _openalex_location(value: Any) -> tuple[str | None, str | None]:
    if not isinstance(value, dict):
        return None, None
    source = value.get("source")
    venue = (
        _optional_text(source.get("display_name")) if isinstance(source, dict) else None
    )
    return venue, _optional_text(value.get("landing_page_url"))


def _openalex_abstract(value: Any) -> str | None:
    if not isinstance(value, dict) or not value:
        return None
    positions: dict[int, str] = {}
    for token, indexes in value.items():
        if not isinstance(token, str) or not isinstance(indexes, list):
            continue
        for index in indexes:
            if isinstance(index, int) and index >= 0:
                positions[index] = token
    if not positions:
        return None
    return " ".join(positions[index] for index in sorted(positions))


def _normalized_score(value: Any) -> float | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return round(min(1.0, max(0.0, float(value))), 6)
    return None


def _optional_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.split())
    return normalized or None


def _provider_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in sorted(value) if not key.startswith("_")}


__all__ = [
    "NORMALIZATION_EPOCH",
    "OPENALEX_WORKS_ENDPOINT",
    "build_retrieval_query",
    "normalize_doi",
    "normalize_retrieval_result",
    "normalize_retrieved_document",
    "normalize_url",
]
