"""Deterministic retrieval adequacy certificate skeleton."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from factori.artifacts import ArtifactStore
from factori.hashing import sha256_json
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BranchStatus,
    ControllerActionType,
    LiteratureState,
    RetrievalAdequacyCertificate,
    RetrievalParseReport,
    RetrievalQualityReport,
    RetrievalQuery,
    RetrievalResult,
    RetrievalRunReport,
    SourceProvenance,
)

if TYPE_CHECKING:
    from factori.adapters.base import RetrievalClient

TAU_ADEQUACY = 0.80
DEFAULT_RETRIEVAL_WEIGHTS = {
    "semantic": 0.20,
    "keyword": 0.20,
    "citation": 0.20,
    "diversity": 0.20,
    "adversarial": 0.20,
}
LOCAL_RETRIEVAL_RELEVANCE_THRESHOLD = 0.35
LOCAL_RETRIEVAL_QUALITY_THRESHOLD = 0.55
_DETERMINISTIC_RETRIEVED_AT = "1970-01-01T00:00:00.000000Z"
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "by",
        "for",
        "from",
        "in",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
)


@dataclass(frozen=True)
class RetrievalExecutionResult:
    """One ledgered real retrieval execution and its context artifacts."""

    report: RetrievalRunReport
    artifacts: dict[str, ArtifactRef]
    commit_hash: str


def compute_retrieval_adequacy(
    literature_state: LiteratureState,
    *,
    tau_adequacy: float = TAU_ADEQUACY,
    weights: dict[str, float] | None = None,
    retrieval_client: RetrievalClient | None = None,
    query: str = "",
    results: list[RetrievalResult] | None = None,
) -> RetrievalAdequacyCertificate:
    """Compute a deterministic placeholder retrieval adequacy certificate."""
    if retrieval_client is not None:
        adapter_results = (
            results
            if results is not None
            else retrieval_client.search(query, max(literature_state.k, 5))
        )
        certificate = retrieval_client.build_adequacy_certificate(query, adapter_results)
        if certificate.proves_novelty or certificate.claims_literature_coverage:
            raise ValueError(
                "retrieval adequacy cannot prove novelty or complete literature coverage"
            )
        return certificate

    weights = weights or DEFAULT_RETRIEVAL_WEIGHTS
    rho_adequacy = round(
        weights["semantic"] * literature_state.semantic
        + weights["keyword"] * literature_state.keyword
        + weights["citation"] * literature_state.citation
        + weights["diversity"] * literature_state.diversity
        + weights["adversarial"] * literature_state.adversarial,
        6,
    )
    passed = rho_adequacy >= tau_adequacy
    return RetrievalAdequacyCertificate(
        semantic=literature_state.semantic,
        keyword=literature_state.keyword,
        citation=literature_state.citation,
        diversity=literature_state.diversity,
        adversarial=literature_state.adversarial,
        weights=dict(weights),
        rho_adequacy=rho_adequacy,
        tau_adequacy=tau_adequacy,
        passed=passed,
        status=BranchStatus.ACTIVE
        if passed
        else BranchStatus.INSUFFICIENT_RETRIEVAL_ADEQUACY,
    )


def run_retrieval_with_provenance(
    *,
    run_id: str,
    query: str,
    limit: int,
    retrieval_client: RetrievalClient,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> RetrievalExecutionResult:
    """Run gated retrieval and ledger its non-verification context artifacts."""
    if retrieval_client.is_fake:
        raise ValueError("real retrieval provenance requires a non-fake retrieval client")
    trace_count = len(getattr(retrieval_client, "generation_traces", []))
    results = retrieval_client.search(query, limit)
    traces = list(getattr(retrieval_client, "generation_traces", []))
    if len(traces) != trace_count + 1:
        raise RuntimeError("retrieval client did not expose one sanitized generation trace")
    trace = traces[-1]
    certificate = compute_retrieval_adequacy(
        LiteratureState(k=len(results)),
        retrieval_client=retrieval_client,
        query=query,
        results=results,
    )
    report = RetrievalRunReport(
        query=trace.query,
        results=results,
        parse_report=trace.parse_report,
        certificate=certificate,
        backend=retrieval_client.backend_name,
        provider=str(getattr(retrieval_client, "provider", retrieval_client.backend_name)),
        config_metadata={
            "limit": limit,
            "external_calls_enabled": retrieval_client.external_calls_enabled,
            "credentials_recorded": False,
        },
    )
    query_hash = trace.query.query_id
    metadata = {
        "stage": "stage_b",
        "adapter_backend": retrieval_client.backend_name,
        "provider": report.provider,
        "artifact_role": "retrieval_context",
        "is_verification_evidence": False,
        "proves_novelty": False,
        "claims_literature_coverage": False,
        "fake": False,
    }
    result = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                artifact_id=f"retrieval-query-{query_hash}",
                artifact_type=ArtifactType.REPORT,
                payload=trace.query,
                artifact_format="json",
                metadata=metadata,
            ),
            ArtifactWriteSpec(
                artifact_id=f"retrieval-response-{query_hash}",
                artifact_type=ArtifactType.REPORT,
                payload=trace.raw_response,
                artifact_format="json",
                metadata=metadata,
            ),
            ArtifactWriteSpec(
                artifact_id=f"retrieval-normalized-results-{query_hash}",
                artifact_type=ArtifactType.REPORT,
                payload={
                    "results": results,
                    "parse_report": trace.parse_report,
                    "is_verification_evidence": False,
                },
                artifact_format="json",
                metadata=metadata,
            ),
            ArtifactWriteSpec(
                artifact_id=f"retrieval-adequacy-{query_hash}",
                artifact_type=ArtifactType.REPORT,
                payload=certificate,
                artifact_format="json",
                metadata=metadata,
            ),
        ],
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.RETRIEVAL_RUN_RECORDED,
        commit_payload=report.model_dump(mode="json"),
    )
    linked = dict(
        zip(
            ["query", "response", "normalized_results", "adequacy"],
            result.artifacts,
            strict=True,
        )
    )
    return RetrievalExecutionResult(
        report=report,
        artifacts=linked,
        commit_hash=result.commit.commit_hash,
    )


def run_fixture_retrieval_with_provenance(
    *,
    run_id: str,
    query: str,
    limit: int,
    retrieval_client: RetrievalClient,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> RetrievalExecutionResult:
    """Ledger one bounded deterministic fixture retrieval without external calls."""
    if not retrieval_client.is_fake:
        raise ValueError("fixture retrieval requires the deterministic fake retrieval client")
    normalized_query = " ".join(query.split()) or "unspecified domain"
    results = retrieval_client.search(normalized_query, limit)
    quality_report = _quality_report_for_fixture_sources(
        run_id=run_id,
        query=normalized_query,
        results=results,
    )
    query_id = sha256_json(
        {"run_id": run_id, "query": normalized_query, "limit": limit, "backend": "fake"}
    )[:16]
    query_contract = RetrievalQuery(
        query_id=query_id,
        query=normalized_query,
        provider="fake",
        limit=limit,
        endpoint="fixture://local",
        parameters={"source_status": "fixture", "network_access": False},
        requires_credentials=False,
        fake=True,
    )
    parse_report = RetrievalParseReport(
        provider="fake",
        raw_response_hash=sha256_json(
            [result.model_dump(mode="json") for result in results]
        ),
        accepted_source_ids=[result.source_id for result in results],
        rejected_results=[],
        fake=True,
    )
    certificate = retrieval_client.build_adequacy_certificate(normalized_query, results)
    report = RetrievalRunReport(
        query=query_contract,
        results=results,
        parse_report=parse_report,
        certificate=certificate,
        backend="fake",
        provider="fake",
        config_metadata={
            "limit": limit,
            "retrieval_scope": "bounded-fixture",
            "citation_policy": "registry-only",
            "external_calls_enabled": False,
            "fixture_sources": True,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
        fake=True,
    )
    metadata = {
        "stage": "llm_orchestration_retrieval",
        "adapter_backend": "fake",
        "provider": "fake",
        "artifact_role": "retrieval_citation_context",
        "source_status": "fixture",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "proves_novelty": False,
        "claims_literature_coverage": False,
        "fake": True,
    }
    normalized_id = f"retrieval-normalized-results-{query_id}"
    persisted = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                artifact_id="retrieval-report",
                artifact_type=ArtifactType.REPORT,
                payload=report,
                artifact_format="json",
                metadata=metadata,
            ),
            ArtifactWriteSpec(
                artifact_id="retrieval-quality-report",
                artifact_type=ArtifactType.REPORT,
                payload=quality_report,
                artifact_format="json",
                metadata=metadata,
            ),
            ArtifactWriteSpec(
                artifact_id=normalized_id,
                artifact_type=ArtifactType.REPORT,
                payload={
                    "results": results,
                    "parse_report": parse_report,
                    "source_status": "fixture",
                    "is_verification_evidence": False,
                    "creates_scientific_validation": False,
                    "implies_publication_readiness": False,
                },
                artifact_format="json",
                metadata=metadata,
            ),
        ],
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.RETRIEVAL_RUN_RECORDED,
        commit_payload=report.model_dump(mode="json"),
    )
    artifacts = {artifact.id: artifact for artifact in persisted.artifacts}
    return RetrievalExecutionResult(
        report=report,
        artifacts={
            "report": artifacts["retrieval-report"],
            "quality_report": artifacts["retrieval-quality-report"],
            "normalized_results": artifacts[normalized_id],
        },
        commit_hash=persisted.commit.commit_hash,
    )


def run_local_retrieval_with_provenance(
    *,
    run_id: str,
    query: str,
    limit: int,
    local_path: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> RetrievalExecutionResult:
    """Load deterministic local source metadata and ledger quality-filtered retrieval."""
    normalized_query = " ".join(query.split()) or "unspecified domain"
    source_path = Path(local_path)
    source_records = _load_local_source_records(source_path)
    limited_records = source_records[:limit]
    results = [
        _local_source_result(
            record,
            query=normalized_query,
            rank=index,
        )
        for index, record in enumerate(limited_records)
    ]
    scored_results, quality_report = score_retrieval_sources(
        run_id=run_id,
        retrieval_backend="local",
        query=normalized_query,
        results=results,
    )
    query_id = sha256_json(
        {
            "run_id": run_id,
            "query": normalized_query,
            "limit": limit,
            "backend": "local",
            "local_path": source_path.as_posix(),
        }
    )[:16]
    query_contract = RetrievalQuery(
        query_id=query_id,
        query=normalized_query,
        provider="local",
        limit=limit,
        endpoint=f"file://{source_path.as_posix()}",
        parameters={
            "network_access": False,
            "source_path_hash": sha256_json(source_path.as_posix()),
        },
        requires_credentials=False,
        fake=False,
    )
    rejected_results = [
        {"source_id": result.source_id, "reason": result.rejection_reason}
        for result in scored_results
        if not result.accepted_for_registry
    ]
    parse_report = RetrievalParseReport(
        provider="local",
        raw_response_hash=sha256_json(limited_records),
        accepted_source_ids=[
            result.source_id for result in scored_results if result.accepted_for_registry
        ],
        rejected_results=rejected_results,
        fake=False,
    )
    certificate = compute_retrieval_adequacy(
        LiteratureState(k=quality_report.accepted_source_count)
    )
    report = RetrievalRunReport(
        query=query_contract,
        results=scored_results,
        parse_report=parse_report,
        certificate=certificate,
        backend="local",
        provider="local",
        config_metadata={
            "limit": limit,
            "retrieval_scope": "bounded-local-source-metadata",
            "citation_policy": "registry-only",
            "external_calls_enabled": False,
            "source_path_hash": sha256_json(source_path.as_posix()),
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
        fake=False,
    )
    metadata = {
        "stage": "llm_orchestration_retrieval",
        "adapter_backend": "local",
        "provider": "local",
        "artifact_role": "retrieval_citation_context",
        "source_status": "local_quality_filtered",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "proves_novelty": False,
        "claims_literature_coverage": False,
        "fake": False,
    }
    normalized_id = f"retrieval-normalized-results-{query_id}"
    persisted = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                artifact_id="retrieval-report",
                artifact_type=ArtifactType.REPORT,
                payload=report,
                artifact_format="json",
                metadata=metadata,
            ),
            ArtifactWriteSpec(
                artifact_id="retrieval-quality-report",
                artifact_type=ArtifactType.REPORT,
                payload=quality_report,
                artifact_format="json",
                metadata=metadata,
            ),
            ArtifactWriteSpec(
                artifact_id=normalized_id,
                artifact_type=ArtifactType.REPORT,
                payload={
                    "results": scored_results,
                    "parse_report": parse_report,
                    "retrieval_quality_report": quality_report,
                    "is_verification_evidence": False,
                    "creates_scientific_validation": False,
                    "implies_publication_readiness": False,
                },
                artifact_format="json",
                metadata=metadata,
            ),
        ],
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.RETRIEVAL_RUN_RECORDED,
        commit_payload=report.model_dump(mode="json"),
    )
    artifacts = {artifact.id: artifact for artifact in persisted.artifacts}
    return RetrievalExecutionResult(
        report=report,
        artifacts={
            "report": artifacts["retrieval-report"],
            "quality_report": artifacts["retrieval-quality-report"],
            "normalized_results": artifacts[normalized_id],
        },
        commit_hash=persisted.commit.commit_hash,
    )


def score_retrieval_sources(
    *,
    run_id: str,
    retrieval_backend: str,
    query: str,
    results: list[RetrievalResult],
    relevance_threshold: float = LOCAL_RETRIEVAL_RELEVANCE_THRESHOLD,
    quality_threshold: float = LOCAL_RETRIEVAL_QUALITY_THRESHOLD,
) -> tuple[list[RetrievalResult], RetrievalQualityReport]:
    """Score retrieval results and mark only usable background sources as accepted."""
    seen: set[tuple[str, int | None]] = set()
    scored: list[RetrievalResult] = []
    duplicate_count = 0
    low_relevance_count = 0
    metadata_incomplete_count = 0
    rejection_reasons: dict[str, str] = {}
    for result in results:
        metadata = dict(result.metadata or {})
        title_key = (_normalize_title(result.title), result.year)
        duplicate = title_key in seen
        seen.add(title_key)
        topic_score = _topic_match_score(query, result, metadata)
        quality_score = _metadata_quality_score(result)
        relevance_score = round((topic_score * 0.72) + (quality_score * 0.28), 3)
        reason = ""
        if duplicate:
            duplicate_count += 1
            reason = "duplicate_source"
        elif quality_score < quality_threshold:
            metadata_incomplete_count += 1
            reason = "metadata_incomplete"
        elif relevance_score < relevance_threshold:
            low_relevance_count += 1
            reason = "low_relevance"
        accepted = not reason
        if not accepted:
            rejection_reasons[result.source_id] = reason
        status = "retrieved" if accepted else "rejected"
        updated_metadata = {
            **metadata,
            "backend": retrieval_backend,
            "source_status": status,
            "accepted_for_registry": accepted,
            "rejection_reason": reason or None,
            "relevance_score": relevance_score,
            "topic_match_score": topic_score,
            "source_quality_score": quality_score,
            "trust_level": metadata.get("trust_level", "metadata_only"),
            "may_support_background_context": bool(
                metadata.get("may_support_background_context", True)
            ),
            "may_support_empirical_claims": False,
            "may_support_proof_claims": False,
            "may_support_novelty_claims": False,
        }
        scored.append(
            result.model_copy(
                update={
                    "relevance_score": relevance_score,
                    "topic_match_score": topic_score,
                    "source_quality_score": quality_score,
                    "accepted_for_registry": accepted,
                    "rejection_reason": reason or None,
                    "retrieval_backend": retrieval_backend,
                    "source_type": str(metadata.get("source_type", "local_source_metadata")),
                    "metadata": updated_metadata,
                    "fixture_only": bool(metadata.get("fixture_only", False)),
                }
            )
        )
    accepted_results = [result for result in scored if result.accepted_for_registry]
    rejected_results = [result for result in scored if not result.accepted_for_registry]
    relevance_scores = [result.relevance_score or 0.0 for result in scored]
    adequacy_status = (
        "insufficient_sources"
        if not accepted_results
        else "bounded_context_only"
        if len(accepted_results) < 3
        else "adequate_for_background_context"
    )
    quality_report = RetrievalQualityReport(
        run_id=run_id,
        retrieval_backend=retrieval_backend,
        total_retrieved_sources=len(scored),
        accepted_source_count=len(accepted_results),
        rejected_source_count=len(rejected_results),
        duplicate_count=duplicate_count,
        low_relevance_count=low_relevance_count,
        metadata_incomplete_count=metadata_incomplete_count,
        mean_relevance_score=round(
            sum(relevance_scores) / len(relevance_scores), 3
        )
        if relevance_scores
        else 0.0,
        min_relevance_score=round(min(relevance_scores), 3) if relevance_scores else 0.0,
        queries_used=[query],
        coverage_limitations=[
            "Retrieval quality is bounded to the supplied local source set.",
            "Accepted sources are background context only, not novelty proof, "
            "claim verification, correctness evidence, or publication readiness.",
        ],
        adequacy_status=adequacy_status,
        accepted_source_ids=[result.source_id for result in accepted_results],
        rejected_source_ids=[result.source_id for result in rejected_results],
        rejection_reasons=rejection_reasons,
    )
    return scored, quality_report


def _load_local_source_records(path: Path) -> list[dict[str, object]]:
    if not path.is_file():
        raise ValueError(f"Local retrieval source file not found: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    records = data.get("sources") if isinstance(data, dict) else data
    if not isinstance(records, list):
        raise ValueError("Local retrieval source file must contain a list or {'sources': [...]}.")
    normalized: list[dict[str, object]] = []
    for index, item in enumerate(records):
        if not isinstance(item, dict):
            raise ValueError(f"Local retrieval source #{index + 1} is not an object.")
        normalized.append(dict(item))
    return normalized


def _local_source_result(
    record: dict[str, object],
    *,
    query: str,
    rank: int,
) -> RetrievalResult:
    source_id = str(record.get("source_id") or f"local-src-{rank + 1:03d}")
    title = str(record.get("title") or f"Untitled local source {rank + 1}")
    authors_value = record.get("authors") or []
    authors = (
        [str(author) for author in authors_value]
        if isinstance(authors_value, list)
        else [str(authors_value)]
        if authors_value
        else []
    )
    year_value = record.get("year")
    year = (
        int(year_value)
        if isinstance(year_value, int | str) and str(year_value).isdigit()
        else None
    )
    venue = (
        record.get("venue_or_source")
        or record.get("venue")
        or record.get("source")
    )
    abstract = record.get("abstract_or_summary") or record.get("abstract") or record.get("summary")
    snippet = str(record.get("snippet") or abstract or "")
    url_or_doi = record.get("url_or_doi_optional") or record.get("url") or record.get("doi")
    url = str(record.get("url") or "") or (
        str(url_or_doi) if str(url_or_doi or "").startswith("http") else None
    )
    doi = str(record.get("doi") or "") or (
        str(url_or_doi) if str(url_or_doi or "").startswith("10.") else None
    )
    metadata = {
        "backend": "local",
        "source_type": str(record.get("source_type") or "local_source_metadata"),
        "source_status": "retrieved",
        "trust_level": str(record.get("trust_level") or "metadata_only"),
        "supported_topics": list(record.get("supported_topics") or []),
        "source_summary": str(abstract or snippet or ""),
        "source_snippet": snippet,
        "fixture_only": bool(record.get("fixture_only", False)),
        "may_support_background_context": bool(
            record.get("may_support_background_context", True)
        ),
        "may_support_method_context": bool(record.get("may_support_method_context", False)),
        "may_support_empirical_claims": False,
        "may_support_proof_claims": False,
        "may_support_novelty_claims": False,
    }
    raw_hash = sha256_json(record)
    provenance = SourceProvenance(
        source_id=source_id,
        provider="local",
        query=query,
        rank=rank,
        retrieved_at=_DETERMINISTIC_RETRIEVED_AT,
        raw_metadata_hash=raw_hash,
        url=url,
        doi=doi,
    )
    return RetrievalResult(
        source_id=source_id,
        title=title,
        authors=authors,
        year=year,
        venue=str(venue) if venue else None,
        abstract=str(abstract) if abstract else None,
        url=url,
        doi=doi,
        provider="local",
        retrieved_at=_DETERMINISTIC_RETRIEVED_AT,
        query=query,
        rank=rank,
        score=None,
        raw_metadata_hash=raw_hash,
        source_provenance=provenance,
        snippet=snippet,
        metadata=metadata,
        fake=False,
        source_type=str(metadata["source_type"]),
        retrieval_backend="local",
        fixture_only=bool(metadata["fixture_only"]),
    )


def _quality_report_for_fixture_sources(
    *,
    run_id: str,
    query: str,
    results: list[RetrievalResult],
) -> RetrievalQualityReport:
    source_ids = [result.source_id for result in results]
    return RetrievalQualityReport(
        run_id=run_id,
        retrieval_backend="fake",
        total_retrieved_sources=len(results),
        accepted_source_count=len(results),
        rejected_source_count=0,
        duplicate_count=0,
        low_relevance_count=0,
        metadata_incomplete_count=0,
        mean_relevance_score=1.0 if results else 0.0,
        min_relevance_score=1.0 if results else 0.0,
        queries_used=[query],
        coverage_limitations=[
            "Fixture retrieval validates citation plumbing only.",
            "Fixture sources are not real literature coverage, novelty proof, "
            "claim verification, or publication readiness.",
        ],
        adequacy_status="bounded_context_only" if results else "insufficient_sources",
        accepted_source_ids=source_ids,
        rejected_source_ids=[],
        rejection_reasons={},
    )


def _topic_match_score(
    query: str,
    result: RetrievalResult,
    metadata: dict[str, object],
) -> float:
    query_tokens = _tokens(query)
    source_text = " ".join(
        str(value or "")
        for value in (
            result.title,
            result.abstract,
            result.snippet,
            " ".join(str(item) for item in metadata.get("supported_topics", []) or []),
        )
    )
    source_tokens = _tokens(source_text)
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & source_tokens) / len(query_tokens)
    title_overlap = len(query_tokens & _tokens(result.title)) / len(query_tokens)
    return round(min(1.0, (0.75 * overlap) + (0.25 * title_overlap)), 3)


def _metadata_quality_score(result: RetrievalResult) -> float:
    score = 0.0
    score += 0.22 if result.title and not result.title.casefold().startswith("untitled") else 0.0
    score += 0.18 if result.authors else 0.0
    score += 0.16 if result.year is not None else 0.0
    score += 0.14 if result.venue else 0.0
    text = " ".join(part for part in (result.abstract or "", result.snippet or "") if part)
    score += 0.22 if len(_tokens(text)) >= 8 else 0.0
    score += 0.08 if result.url or result.doi else 0.0
    return round(min(1.0, score), 3)


def _tokens(value: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]*", value.casefold())
        if token not in _STOPWORDS and len(token) > 2
    }


def _normalize_title(title: str) -> str:
    return " ".join(sorted(_tokens(title))) or title.casefold().strip()


__all__ = [
    "DEFAULT_RETRIEVAL_WEIGHTS",
    "RetrievalExecutionResult",
    "TAU_ADEQUACY",
    "compute_retrieval_adequacy",
    "run_local_retrieval_with_provenance",
    "run_fixture_retrieval_with_provenance",
    "run_retrieval_with_provenance",
    "score_retrieval_sources",
]
