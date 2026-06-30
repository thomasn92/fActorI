"""Deterministic retrieval adequacy certificate skeleton."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from factori.adapters.retrieval_sources import normalize_retrieval_result
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
from factori.source_relevance import (
    SourceRelevanceAdjudicator,
    SourceRelevanceRequest,
    deterministic_source_relevance_adjudication,
    source_relevance_request_from_result,
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
LOCAL_RETRIEVAL_AMBIGUITY_MARGIN = 0.12
_ACCEPTED_SOURCE_RELEVANCE_LABELS = {
    "highly_relevant_background",
    "partially_relevant_background",
}
_ALLOWED_LOCAL_SOURCE_TYPES = frozenset(
    {
        "article",
        "book",
        "journal-article",
        "local_fixture",
        "local_source_metadata",
        "openalex_style_metadata",
        "openalex_work",
        "report",
        "retrieval_metadata",
    }
)
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


@dataclass(frozen=True)
class _SourceScoringDecision:
    result: RetrievalResult
    metadata: dict[str, object]
    topic_score: float
    quality_score: float
    relevance_score: float
    hard_rejection_reason: str | None
    requires_adjudication: bool
    request: SourceRelevanceRequest


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
    source_relevance_adjudicator: SourceRelevanceAdjudicator | None = None,
    source_relevance_adjudicator_model: str | None = None,
    domain: str | None = None,
    candidate_title_or_problem: str | None = None,
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
        source_relevance_adjudicator=source_relevance_adjudicator,
        source_relevance_adjudicator_model=source_relevance_adjudicator_model,
        domain=domain or normalized_query,
        candidate_title_or_problem=candidate_title_or_problem or normalized_query,
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
    source_relevance_adjudicator: SourceRelevanceAdjudicator | None = None,
    source_relevance_adjudicator_model: str | None = None,
    domain: str | None = None,
    candidate_title_or_problem: str | None = None,
) -> tuple[list[RetrievalResult], RetrievalQualityReport]:
    """Score retrieval results and mark only usable background sources as accepted."""
    seen: set[tuple[str, int | None]] = set()
    decisions: list[_SourceScoringDecision] = []
    duplicate_count = 0
    low_relevance_count = 0
    metadata_incomplete_count = 0
    hard_reject_count = 0
    deterministic_accept_count = 0
    deterministic_reject_count = 0
    llm_accepted_count = 0
    llm_rejected_count = 0
    rejection_reasons: dict[str, str] = {}
    adjudication_items = []
    adjudication_enabled = source_relevance_adjudicator is not None
    normalized_domain = " ".join((domain or query).split()) or query
    normalized_problem = (
        " ".join((candidate_title_or_problem or normalized_domain).split())
        or normalized_domain
    )
    for result in results:
        metadata = dict(result.metadata or {})
        title_key = (_normalize_title(result.title), result.year)
        duplicate = title_key in seen
        seen.add(title_key)
        topic_score = _topic_match_score(query, result, metadata)
        quality_score = _metadata_quality_score(result)
        relevance_score = round((topic_score * 0.72) + (quality_score * 0.28), 3)
        hard_reason = _hard_rejection_reason(
            result,
            metadata,
            duplicate=duplicate,
            quality_threshold=quality_threshold,
        )
        requires_adjudication = bool(
            adjudication_enabled
            and hard_reason is None
            and _requires_source_relevance_adjudication(
                result=result,
                metadata=metadata,
                relevance_score=relevance_score,
                topic_score=topic_score,
                quality_score=quality_score,
                relevance_threshold=relevance_threshold,
            )
        )
        decisions.append(
            _SourceScoringDecision(
                result=result,
                metadata=metadata,
                topic_score=topic_score,
                quality_score=quality_score,
                relevance_score=relevance_score,
                hard_rejection_reason=hard_reason,
                requires_adjudication=requires_adjudication,
                request=source_relevance_request_from_result(
                    result,
                    query=query,
                    domain=normalized_domain,
                    candidate_title_or_problem=normalized_problem,
                    deterministic_relevance_score=relevance_score,
                    deterministic_topic_match_score=topic_score,
                    source_quality_score=quality_score,
                ),
            )
        )
    initial_adjudication_calls = (
        source_relevance_adjudicator.call_count
        if source_relevance_adjudicator is not None
        else 0
    )
    adjudicated_by_id = {}
    if source_relevance_adjudicator is not None:
        requests = [
            decision.request
            for decision in decisions
            if decision.requires_adjudication
        ]
        adjudicated = source_relevance_adjudicator.adjudicate(requests) if requests else []
        adjudicated_by_id = {item.source_id: item for item in adjudicated}

    scored: list[RetrievalResult] = []
    for decision in decisions:
        result = decision.result
        metadata = decision.metadata
        reason = ""
        accepted = False
        adjudication = adjudicated_by_id.get(result.source_id)
        if decision.hard_rejection_reason:
            reason = decision.hard_rejection_reason
            hard_reject_count += 1
            if reason == "duplicate_source":
                duplicate_count += 1
            elif reason in {
                "metadata_incomplete",
                "missing_title",
                "missing_year",
                "missing_snippet_or_summary",
                "missing_authors",
            }:
                metadata_incomplete_count += 1
            else:
                low_relevance_count += 1
            if adjudication_enabled:
                adjudication_items.append(
                    deterministic_source_relevance_adjudication(
                        decision.request,
                        backend="deterministic_hard_filter",
                        model=source_relevance_adjudicator_model,
                        forced_label=_label_for_hard_reason(reason),
                        forced_rejection_reason=reason,
                    )
                )
        elif adjudication is not None:
            accepted = (
                adjudication.accepted_for_background_context
                and adjudication.adjudicated_relevance_label
                in _ACCEPTED_SOURCE_RELEVANCE_LABELS
            )
            reason = "" if accepted else adjudication.rejection_reason or "low_relevance"
            adjudication_items.append(adjudication)
            if accepted:
                llm_accepted_count += 1
            else:
                llm_rejected_count += 1
                low_relevance_count += 1
        else:
            if decision.quality_score < quality_threshold:
                metadata_incomplete_count += 1
                reason = "metadata_incomplete"
            elif decision.relevance_score < relevance_threshold:
                low_relevance_count += 1
                reason = "low_relevance"
            accepted = not reason
            if accepted:
                deterministic_accept_count += 1
            else:
                deterministic_reject_count += 1
            if adjudication_enabled:
                adjudication_items.append(
                    deterministic_source_relevance_adjudication(
                        decision.request,
                        backend="deterministic_filter",
                        model=source_relevance_adjudicator_model,
                        forced_label=(
                            _deterministic_accept_label(decision.relevance_score)
                            if accepted
                            else "weakly_relevant"
                        ),
                        forced_rejection_reason=reason or None,
                    )
                )
        if not accepted:
            rejection_reasons[result.source_id] = reason
        status = "retrieved" if accepted else "rejected"
        updated_metadata = {
            **metadata,
            "backend": retrieval_backend,
            "source_status": status,
            "accepted_for_registry": accepted,
            "hard_rejected": bool(decision.hard_rejection_reason),
            "rejection_reason": reason or None,
            "relevance_score": decision.relevance_score,
            "topic_match_score": decision.topic_score,
            "source_quality_score": decision.quality_score,
            "source_relevance_adjudicated": adjudication is not None,
            "source_relevance_adjudicator_backend": (
                adjudication.adjudicator_backend
                if adjudication is not None
                else "deterministic_hard_filter"
                if decision.hard_rejection_reason
                else "deterministic_filter"
            ),
            "source_relevance_label": (
                adjudication.adjudicated_relevance_label
                if adjudication is not None
                else _label_for_hard_reason(reason)
                if decision.hard_rejection_reason
                else _deterministic_accept_label(decision.relevance_score)
                if accepted
                else "weakly_relevant"
            ),
            "trust_level": metadata.get("trust_level", "metadata_only"),
            "may_support_background_context": bool(
                metadata.get("may_support_background_context", True)
            )
            and accepted,
            "may_support_empirical_claims": False,
            "may_support_proof_claims": False,
            "may_support_novelty_claims": False,
        }
        scored.append(
            result.model_copy(
                update={
                    "relevance_score": decision.relevance_score,
                    "topic_match_score": decision.topic_score,
                    "source_quality_score": decision.quality_score,
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
    adjudication_calls = (
        source_relevance_adjudicator.call_count - initial_adjudication_calls
        if source_relevance_adjudicator is not None
        else 0
    )
    adjudicated_source_count = sum(
        1
        for item in adjudication_items
        if item.adjudicator_backend in {"fake", "openai"}
    )
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
            "Source relevance adjudication, when enabled, judges topical fit only; "
            "deterministic code still controls metadata, provenance, duplicate, "
            "registry, citation, and evidence-boundary checks.",
        ],
        adequacy_status=adequacy_status,
        source_relevance_adjudication_enabled=adjudication_enabled,
        source_relevance_adjudicator_backend=(
            source_relevance_adjudicator.backend_name
            if source_relevance_adjudicator is not None
            else "off"
        ),
        source_relevance_adjudicator_model=(
            source_relevance_adjudicator.model
            if source_relevance_adjudicator is not None
            else source_relevance_adjudicator_model
        ),
        source_relevance_adjudication_calls=adjudication_calls,
        adjudicated_source_count=adjudicated_source_count,
        deterministic_accept_count=deterministic_accept_count,
        deterministic_reject_count=deterministic_reject_count,
        llm_accepted_count=llm_accepted_count,
        llm_rejected_count=llm_rejected_count,
        hard_reject_count=hard_reject_count,
        adjudication_items=adjudication_items,
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
    if _looks_like_openalex_record(record):
        return _openalex_style_local_source_result(record, query=query, rank=rank)
    source_id = str(record.get("source_id") or f"local-src-{rank + 1:03d}")
    title_missing = not str(record.get("title") or "").strip()
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
        "title_missing": title_missing,
        "explicit_invalid_marker": bool(
            record.get("invalid")
            or record.get("unsafe")
            or record.get("test_invalid_marker")
            or record.get("explicit_invalid_marker")
        ),
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


def _looks_like_openalex_record(record: dict[str, object]) -> bool:
    return any(
        key in record
        for key in (
            "display_name",
            "publication_year",
            "authorships",
            "abstract_inverted_index",
            "host_venue",
            "concepts",
        )
    )


def _openalex_style_local_source_result(
    record: dict[str, object],
    *,
    query: str,
    rank: int,
) -> RetrievalResult:
    enriched: dict[str, object] = dict(record)
    enriched.setdefault("id", record.get("source_id") or f"local-openalex-{rank + 1:03d}")
    enriched.setdefault("_query", query)
    enriched.setdefault("_rank", rank)
    enriched.setdefault("_retrieved_at", _DETERMINISTIC_RETRIEVED_AT)
    enriched.setdefault("_normalized_score", max(0.0, 1.0 - (0.08 * rank)))
    if "primary_location" not in enriched and isinstance(record.get("host_venue"), dict):
        host = record["host_venue"]
        enriched["primary_location"] = {
            "source": {
                "display_name": str(host.get("display_name") or host.get("name") or "")
            },
            "landing_page_url": host.get("url"),
        }
    result = normalize_retrieval_result(enriched, "openalex", backend="local")
    concepts = record.get("concepts")
    supported_topics = _openalex_supported_topics(concepts)
    metadata = {
        **dict(result.metadata or {}),
        "backend": "local",
        "source_type": str(record.get("source_type") or "openalex_style_metadata"),
        "source_status": "retrieved",
        "trust_level": str(record.get("trust_level") or "metadata_only"),
        "supported_topics": supported_topics,
        "source_summary": result.abstract or result.snippet or result.title,
        "source_snippet": result.snippet,
        "title_missing": not str(record.get("display_name") or record.get("title") or "").strip(),
        "explicit_invalid_marker": bool(
            record.get("invalid")
            or record.get("unsafe")
            or record.get("test_invalid_marker")
            or record.get("explicit_invalid_marker")
        ),
        "fixture_only": bool(record.get("fixture_only", False)),
        "may_support_background_context": bool(
            record.get("may_support_background_context", True)
        ),
        "may_support_method_context": bool(record.get("may_support_method_context", False)),
        "may_support_empirical_claims": False,
        "may_support_proof_claims": False,
        "may_support_novelty_claims": False,
    }
    return result.model_copy(
        update={
            "retrieval_backend": "local",
            "source_type": str(metadata["source_type"]),
            "metadata": metadata,
            "fixture_only": bool(metadata["fixture_only"]),
        }
    )


def _openalex_supported_topics(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    topics = []
    for item in value:
        if isinstance(item, dict):
            name = item.get("display_name") or item.get("name")
            if name:
                topics.append(str(name))
        elif item:
            topics.append(str(item))
    return list(dict.fromkeys(topics))


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


def _hard_rejection_reason(
    result: RetrievalResult,
    metadata: dict[str, object],
    *,
    duplicate: bool,
    quality_threshold: float,
) -> str | None:
    del quality_threshold
    if bool(metadata.get("explicit_invalid_marker")):
        return "unsafe_or_invalid_source"
    source_type = str(metadata.get("source_type") or result.source_type or "").strip().lower()
    if source_type and source_type not in _ALLOWED_LOCAL_SOURCE_TYPES:
        return "invalid_source_type"
    if bool(metadata.get("title_missing")) or not result.title.strip():
        return "metadata_incomplete"
    if duplicate:
        return "duplicate_source"
    if not result.authors:
        return "metadata_incomplete"
    if result.year is None:
        return "metadata_incomplete"
    text = " ".join(part for part in (result.abstract or "", result.snippet or "") if part)
    if len(_tokens(text)) < 8:
        return "metadata_incomplete"
    return None


def _requires_source_relevance_adjudication(
    *,
    result: RetrievalResult,
    metadata: dict[str, object],
    relevance_score: float,
    topic_score: float,
    quality_score: float,
    relevance_threshold: float,
) -> bool:
    near_threshold = (
        relevance_threshold - LOCAL_RETRIEVAL_AMBIGUITY_MARGIN
        <= relevance_score
        <= relevance_threshold + LOCAL_RETRIEVAL_AMBIGUITY_MARGIN
    )
    good_metadata_weak_overlap = quality_score >= 0.82 and topic_score < 0.25
    partial_overlap = 0.15 <= topic_score < 0.45 and quality_score >= 0.70
    high_overlap_generic = topic_score >= 0.55 and _title_is_generic(result.title)
    title_abstract_mismatch = _title_abstract_mismatch(result, metadata)
    return bool(
        near_threshold
        or good_metadata_weak_overlap
        or partial_overlap
        or high_overlap_generic
        or title_abstract_mismatch
    )


def _label_for_hard_reason(reason: str) -> str:
    if reason == "duplicate_source":
        return "duplicate"
    if reason in {"unsafe_or_invalid_source", "invalid_source_type"}:
        return "unsafe_or_invalid_source"
    if reason:
        return "metadata_insufficient"
    return "weakly_relevant"


def _deterministic_accept_label(relevance_score: float) -> str:
    return (
        "highly_relevant_background"
        if relevance_score >= 0.70
        else "partially_relevant_background"
    )


def _title_is_generic(title: str) -> bool:
    tokens = _tokens(title)
    generic = {"analysis", "approach", "context", "framework", "study", "survey"}
    return bool(tokens & generic) and len(tokens) <= 5


def _title_abstract_mismatch(
    result: RetrievalResult,
    metadata: dict[str, object],
) -> bool:
    abstract = result.abstract or result.snippet or str(metadata.get("source_summary") or "")
    title_tokens = _tokens(result.title)
    abstract_tokens = _tokens(abstract)
    if not title_tokens or not abstract_tokens:
        return False
    overlap = len(title_tokens & abstract_tokens) / len(title_tokens)
    return overlap < 0.20 and len(abstract_tokens) >= 12


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
