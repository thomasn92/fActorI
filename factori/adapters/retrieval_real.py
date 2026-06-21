"""Provider-isolated, explicitly gated OpenAlex retrieval adapter."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from factori.adapters.retrieval_safety import (
    RetrievalResponseError,
    parse_retrieval_response,
)
from factori.adapters.retrieval_sources import (
    OPENALEX_WORKS_ENDPOINT,
    build_retrieval_query,
    normalize_retrieved_document,
)
from factori.hashing import sha256_json
from factori.ledger import utc_timestamp
from factori.schemas import (
    BranchStatus,
    RetrievalAdequacyCertificate,
    RetrievalParseReport,
    RetrievalQuery,
    RetrievalResult,
    RetrievalRunTrace,
    RetrievedDocument,
)


class RetrievalTransport(Protocol):
    """Injectable transport that keeps provider calls out of tests."""

    def search(self, *, query: RetrievalQuery, api_key: str) -> Any: ...

    def fetch(self, *, source_id: str, api_key: str) -> Any: ...


@dataclass(frozen=True)
class OpenAlexTransport:
    """Minimal standard-library transport for OpenAlex works metadata."""

    endpoint: str = OPENALEX_WORKS_ENDPOINT
    timeout_seconds: float = 60.0

    def search(self, *, query: RetrievalQuery, api_key: str) -> Any:
        parameters = {**query.parameters, "api_key": api_key}
        return self._get(f"{self.endpoint}?{urlencode(parameters)}")

    def fetch(self, *, source_id: str, api_key: str) -> Any:
        normalized_id = source_id.rstrip("/").rsplit("/", maxsplit=1)[-1]
        if not normalized_id:
            raise ValueError("source_id must not be empty")
        url = f"{self.endpoint}/{quote(normalized_id, safe='')}?{urlencode({'api_key': api_key})}"
        return self._get(url)

    def _get(self, url: str) -> Any:
        request = Request(
            url,
            headers={"Accept": "application/json", "User-Agent": "factori/0.1"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"OpenAlex request failed with HTTP {exc.code}") from exc
        except (URLError, TimeoutError) as exc:
            raise RuntimeError("OpenAlex request failed before a response was received") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeError("OpenAlex returned invalid JSON") from exc


@dataclass
class OpenAlexRetrievalClient:
    """Real-but-gated source retrieval limited to metadata and abstracts."""

    api_key: str = field(repr=False)
    transport: RetrievalTransport = field(default_factory=OpenAlexTransport)
    default_limit: int = 5
    allow_external_calls: bool = False
    clock: Callable[[], str] = field(default=utc_timestamp, repr=False)
    backend_name: str = field(default="openalex", init=False)
    provider: str = field(default="openalex", init=False)
    is_fake: bool = field(default=False, init=False)
    generation_traces: list[RetrievalRunTrace] = field(default_factory=list, init=False)

    @property
    def external_calls_enabled(self) -> bool:
        return self.allow_external_calls

    def __post_init__(self) -> None:
        if not self.allow_external_calls:
            raise ValueError(
                "External calls are disabled. Set allow_external_calls=true to use real "
                "retrieval adapters."
            )
        if not self.api_key.strip():
            raise ValueError(
                "Real retrieval adapter requested but required credentials are not configured."
            )
        if self.default_limit < 1:
            raise ValueError("default_limit must be at least 1")

    def search(self, query: str, limit: int) -> list[RetrievalResult]:
        contract = build_retrieval_query(query, limit, self.provider)
        raw_response = _json_compatible(
            self.transport.search(query=contract, api_key=self.api_key)
        )
        retrieved_at = self.clock()
        try:
            results, parse_report = parse_retrieval_response(
                raw_response,
                provider=self.provider,
                query=contract.query,
                limit=contract.limit,
                retrieved_at=retrieved_at,
            )
        except RetrievalResponseError as exc:
            results = []
            parse_report = RetrievalParseReport(
                provider=self.provider,
                raw_response_hash=sha256_json(raw_response),
                rejected_results=[{"index": -1, "reasons": [str(exc)]}],
            )
        self.generation_traces.append(
            RetrievalRunTrace(
                query=contract,
                raw_response=raw_response,
                parse_report=parse_report,
                results=results,
            )
        )
        return results

    def fetch(self, source_id: str) -> RetrievedDocument:
        raw_payload = _json_compatible(
            self.transport.fetch(source_id=source_id, api_key=self.api_key)
        )
        if not isinstance(raw_payload, dict):
            raise RuntimeError("OpenAlex fetch response must be a JSON object")
        return normalize_retrieved_document(
            raw_payload,
            self.provider,
            retrieved_at=self.clock(),
        )

    def build_adequacy_certificate(
        self,
        query: str,
        results: list[RetrievalResult],
    ) -> RetrievalAdequacyCertificate:
        """Compute a bounded metadata-adequacy signal, never a novelty guarantee."""
        count = len(results)
        query_tokens = {token.lower() for token in query.split() if token}
        average_score = (
            sum(result.score or 0.0 for result in results) / count if count else 0.0
        )
        overlap = (
            sum(_keyword_overlap(query_tokens, result.title) for result in results) / count
            if count
            else 0.0
        )
        metadata_coverage = (
            sum(
                sum(bool(value) for value in [result.year, result.venue, result.doi]) / 3.0
                for result in results
            )
            / count
            if count
            else 0.0
        )
        diversity_ratio = _diversity_ratio(results)
        semantic = _bounded(0.65 + 0.35 * average_score) if count else 0.0
        keyword = _bounded(0.60 + 0.40 * overlap) if count else 0.0
        citation = _bounded(0.55 + 0.45 * metadata_coverage) if count else 0.0
        diversity = _bounded(0.55 + 0.45 * diversity_ratio) if count else 0.0
        adversarial = _bounded(0.50 + 0.06 * min(count, 8)) if count else 0.0
        weights = {
            "semantic": 0.20,
            "keyword": 0.20,
            "citation": 0.20,
            "diversity": 0.20,
            "adversarial": 0.20,
        }
        rho = round(
            weights["semantic"] * semantic
            + weights["keyword"] * keyword
            + weights["citation"] * citation
            + weights["diversity"] * diversity
            + weights["adversarial"] * adversarial,
            6,
        )
        threshold = 0.80
        passed = rho >= threshold
        return RetrievalAdequacyCertificate(
            semantic=semantic,
            keyword=keyword,
            citation=citation,
            diversity=diversity,
            adversarial=adversarial,
            weights=weights,
            rho_adequacy=rho,
            tau_adequacy=threshold,
            passed=passed,
            status=(
                BranchStatus.ACTIVE
                if passed
                else BranchStatus.INSUFFICIENT_RETRIEVAL_ADEQUACY
            ),
            fake=False,
            provider=self.provider,
            source_count=count,
            limitations=[
                "This is a bounded metadata adequacy signal, not proof of novelty.",
                "The retrieved result set is not a claim of complete literature coverage.",
                "Retrieved sources are literature context, not proof or experiment evidence.",
            ],
        )


def _keyword_overlap(query_tokens: set[str], title: str) -> float:
    if not query_tokens:
        return 0.0
    title_tokens = {token.lower().strip(".,:;()[]") for token in title.split()}
    return len(query_tokens & title_tokens) / len(query_tokens)


def _diversity_ratio(results: list[RetrievalResult]) -> float:
    if not results:
        return 0.0
    venues = {result.venue for result in results if result.venue}
    years = {result.year for result in results if result.year is not None}
    authors = {author for result in results for author in result.authors}
    count = len(results)
    venue_ratio = len(venues) / count
    year_ratio = len(years) / count
    author_ratio = min(1.0, len(authors) / max(1, count * 2))
    return _bounded((venue_ratio + year_ratio + author_ratio) / 3.0)


def _bounded(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 6)


def _json_compatible(value: Any) -> Any:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("retrieval transport returned a non-JSON-compatible response") from exc


__all__ = [
    "OpenAlexRetrievalClient",
    "OpenAlexTransport",
    "RetrievalTransport",
]
