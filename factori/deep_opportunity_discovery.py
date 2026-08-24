"""Retrieval-contextualized non-fake LLM opportunity discovery for selected atlas pairs."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import ValidationError

from factori.adapters.deep_opportunity import OpportunityDiscoveryClient
from factori.adapters.errors import AdapterError
from factori.adapters.retrieval_real import OpenAlexRetrievalClient
from factori.artifacts import ArtifactStore
from factori.domain_method_atlas import AtlasScanError
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.production_mode import evaluate_production_mode, stage_backend_record
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AtlasScanReport,
    BackendKind,
    ControllerActionType,
    DeepOpportunityCandidate,
    DeepOpportunityDiscoveryConfig,
    DeepOpportunityDiscoveryInspectionReport,
    DeepOpportunityDiscoveryReport,
    DeepOpportunityScore,
    DomainAtlasEntry,
    DomainMethodPair,
    LLMOpportunityDiscoveryRawArtifact,
    MethodAtlasEntry,
    ProductionModePolicy,
    RetrievalContext,
    RetrievalResult,
    RetrievedSourceSummary,
    ScientificStageKind,
    StageBackendRecord,
    TargetedResearchBrief,
)

_ATLAS_RE = re.compile(r"^domain-method-atlas-(\d{4})\.json$")
_SCAN_RE = re.compile(r"^atlas-scan-(\d{4})\.json$")
_REPORT_RE = re.compile(r"^deep-opportunity-discovery-report-(\d{4})\.json$")
_RAW_RE = re.compile(r"^llm-deep-opportunity-raw-(\d{4})\.json$")
_RETRIEVAL_RE = re.compile(r"^retrieval-context-(\d{4})\.json$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_OPENALEX_SAFE_QUERY_CHARS = 1000
_RETRIEVAL_TERM_LIMIT = 18
_RETRIEVAL_QUERY_VARIANT_LIMIT = 4
_RETRIEVAL_MIN_TOPIC_OVERLAP = 2
_RETRIEVAL_STOPWORDS = {
    "a",
    "an",
    "and",
    "at",
    "both",
    "can",
    "compare",
    "compared",
    "comparing",
    "controlled",
    "data",
    "dataset",
    "datasets",
    "each",
    "enforce",
    "evaluate",
    "evaluated",
    "evaluating",
    "every",
    "exclude",
    "for",
    "from",
    "in",
    "include",
    "including",
    "into",
    "known",
    "levels",
    "method",
    "methods",
    "of",
    "on",
    "one",
    "only",
    "or",
    "proposed",
    "require",
    "required",
    "study",
    "the",
    "their",
    "these",
    "this",
    "to",
    "under",
    "use",
    "used",
    "using",
    "versus",
    "via",
    "with",
    "without",
}


class DeepOpportunityDiscoveryError(RuntimeError):
    """Raised when deep discovery cannot proceed without weakening its policy."""


@dataclass(frozen=True)
class DeepOpportunityDiscoveryResult:
    run_id: str
    report: DeepOpportunityDiscoveryReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


@dataclass(frozen=True)
class OpportunityLiteratureRefreshResult:
    run_id: str
    contexts: list[RetrievalContext]
    persistence: PersistenceResult


class OpportunityRetrievalProvider(Protocol):
    """Narrow retrieval seam used before scientific generation."""

    backend_name: str
    backend_kind: BackendKind
    retrieval_mode: str
    fallback_used: bool
    fallback_disclosed: bool

    def retrieve(
        self,
        *,
        run_id: str,
        context_id: str,
        pair: DomainMethodPair,
        domain: DomainAtlasEntry,
        method: MethodAtlasEntry,
        limit: int,
    ) -> RetrievalContext: ...


@dataclass
class MockedOpportunityRetriever:
    """Development-only bounded retrieval fixture with explicit non-production authority."""

    backend_name: str = "mocked_retrieval"
    backend_kind: BackendKind = BackendKind.FIXTURE
    retrieval_mode: str = "mocked_retrieval"
    fallback_used: bool = False
    fallback_disclosed: bool = True

    def retrieve(
        self,
        *,
        run_id: str,
        context_id: str,
        pair: DomainMethodPair,
        domain: DomainAtlasEntry,
        method: MethodAtlasEntry,
        limit: int,
    ) -> RetrievalContext:
        del limit
        query = _retrieval_query(domain=domain, method=method)
        source = RetrievedSourceSummary(
            source_id=f"mock-{pair.pair_id}",
            title=f"Mock retrieval context for {domain.name} and {method.name}",
            abstract_or_snippet=(
                "Development-only source context. It cannot establish novelty, underuse, "
                "literature coverage, or scientific validation."
            ),
            relevance_score=0.25,
            provider="mocked_retrieval",
            fake_or_mocked=True,
        )
        return RetrievalContext(
            context_id=context_id,
            run_id=run_id,
            source_pair_id=pair.pair_id,
            retrieval_mode="mocked_retrieval",
            backend_name=self.backend_name,
            query=query,
            sources=[source],
            retrieval_confidence=0.2,
            limitations=[
                "Mocked retrieval is development context only and is not production eligible.",
                "Novelty and underuse remain hypotheses.",
            ],
        )


@dataclass
class OpenAlexOpportunityRetriever:
    """Production-eligible real retrieval wrapper over the gated OpenAlex adapter."""

    client: OpenAlexRetrievalClient
    backend_name: str = "openalex"
    backend_kind: BackendKind = BackendKind.RETRIEVAL_REAL
    retrieval_mode: str = "real_retrieval"
    fallback_used: bool = False
    fallback_disclosed: bool = True

    def retrieve(
        self,
        *,
        run_id: str,
        context_id: str,
        pair: DomainMethodPair,
        domain: DomainAtlasEntry,
        method: MethodAtlasEntry,
        limit: int,
    ) -> RetrievalContext:
        queries = _retrieval_queries(domain=domain, method=method)
        results = _retrieve_query_variants(
            client=self.client,
            queries=queries,
            domain=domain,
            method=method,
            limit=limit,
        )
        sources = [
            RetrievedSourceSummary(
                source_id=result.source_id,
                title=result.title,
                authors=result.authors,
                year=result.year,
                venue=result.venue,
                abstract_or_snippet=result.abstract or result.snippet,
                doi=result.doi,
                relevance_score=_source_topic_relevance(
                    result=result,
                    topic_terms=_retrieval_topic_terms(domain=domain, method=method),
                ),
                provider=result.provider,
                fake_or_mocked=False,
            )
            for result in results
        ]
        confidence = _retrieval_confidence(sources)
        limitations = [
            "Retrieved metadata and abstracts are bounded literature context only.",
            "The result set does not establish novelty, underuse, or complete coverage.",
            f"Executed {len(queries)} bounded query variants and deduplicated accepted sources.",
        ]
        if not sources:
            limitations.append(
                "No accepted source metadata was returned; retrieval confidence is low."
            )
        return RetrievalContext(
            context_id=context_id,
            run_id=run_id,
            source_pair_id=pair.pair_id,
            retrieval_mode="real_retrieval",
            backend_name=self.backend_name,
            query=" | ".join(queries),
            sources=sources,
            retrieval_confidence=confidence,
            limitations=limitations,
        )


def refresh_deep_opportunity_literature(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    retriever: OpportunityRetrievalProvider,
    max_pairs: int = 1,
    max_sources_per_pair: int = 5,
    require_non_fake_backends: bool = True,
) -> OpportunityLiteratureRefreshResult:
    """Append fresh literature context without regenerating the scientific branch."""
    if max_pairs < 1:
        raise DeepOpportunityDiscoveryError("max_pairs must be at least 1.")
    if max_sources_per_pair < 1:
        raise DeepOpportunityDiscoveryError("max_sources_per_pair must be at least 1.")
    if require_non_fake_backends and retriever.backend_kind != BackendKind.RETRIEVAL_REAL:
        raise DeepOpportunityDiscoveryError(
            "Strict literature refresh requires a real retrieval backend."
        )
    if require_non_fake_backends and retriever.fallback_used:
        raise DeepOpportunityDiscoveryError(
            "Strict literature refresh forbids retrieval fallback."
        )

    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    report_path = _latest_matching(reports, _REPORT_RE)
    if report_path is None:
        raise DeepOpportunityDiscoveryError(
            f"No deep opportunity discovery report found for {run_id}."
        )
    try:
        report = DeepOpportunityDiscoveryReport.model_validate_json(
            report_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise DeepOpportunityDiscoveryError(
            f"Could not load deep opportunity discovery context: {exc}"
        ) from exc

    domain_by_id = {item.domain_id: item for item in report.source_domains}
    method_by_id = {item.method_id: item for item in report.source_methods}
    pairs = report.source_pairs[:max_pairs]
    if not pairs:
        raise DeepOpportunityDiscoveryError(
            "Deep opportunity discovery report contains no source pairs."
        )
    retrieval_number = _next_number(reports, _RETRIEVAL_RE)
    contexts: list[RetrievalContext] = []
    for pair_index, pair in enumerate(pairs):
        domain = domain_by_id.get(pair.domain_id)
        method = method_by_id.get(pair.method_id)
        if domain is None or method is None:
            raise DeepOpportunityDiscoveryError(
                f"Source metadata is missing for selected pair {pair.pair_id}."
            )
        try:
            context = retriever.retrieve(
                run_id=run_id,
                context_id=f"retrieval-context-{retrieval_number + pair_index:04d}",
                pair=pair,
                domain=domain,
                method=method,
                limit=max_sources_per_pair,
            )
        except (AdapterError, ValueError) as exc:
            raise DeepOpportunityDiscoveryError(
                f"Literature refresh failed for {pair.pair_id}: {exc}"
            ) from exc
        if context.run_id != run_id or context.source_pair_id != pair.pair_id:
            raise DeepOpportunityDiscoveryError(
                f"Retrieval context identity mismatch for {pair.pair_id}."
            )
        contexts.append(context)

    accepted_source_count = sum(
        not source.fake_or_mocked for context in contexts for source in context.sources
    )
    metadata = {
        **_metadata("deep_opportunity_literature_refresh"),
        "adapter_backend": retriever.backend_name,
        "artifact_role": "bounded_literature_context",
    }
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                item.context_id,
                ArtifactType.REPORT,
                item,
                "json",
                metadata,
            )
            for item in contexts
        ],
        action_type=ControllerActionType.RETRIEVAL_RUN_RECORDED,
        commit_payload={
            "run_id": run_id,
            "operation": "deep_opportunity_literature_refresh",
            "source_report_path": _relative(root_path, report_path),
            "context_ids": [item.context_id for item in contexts],
            "source_pair_ids": [item.source_pair_id for item in contexts],
            "backend_name": retriever.backend_name,
            "backend_kind": retriever.backend_kind.value,
            "accepted_source_count": accepted_source_count,
            "fallback_used": retriever.fallback_used,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "is_verification_evidence": False,
        },
    )
    if require_non_fake_backends and accepted_source_count == 0:
        raise DeepOpportunityDiscoveryError(
            "Literature refresh returned no accepted real sources; the empty context was "
            "recorded for provenance."
        )
    return OpportunityLiteratureRefreshResult(
        run_id=run_id,
        contexts=contexts,
        persistence=persistence,
    )


def discover_deep_opportunities(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    generator: OpportunityDiscoveryClient,
    retriever: OpportunityRetrievalProvider,
    config: DeepOpportunityDiscoveryConfig,
) -> DeepOpportunityDiscoveryResult:
    """Generate, validate, select, and persist concrete opportunities without fallback."""
    if config.run_id != run_id:
        raise DeepOpportunityDiscoveryError("Deep discovery config run_id does not match run_id.")
    if config.retrieval_mode != retriever.retrieval_mode:
        raise DeepOpportunityDiscoveryError(
            "Configured retrieval mode does not match the retrieval backend."
        )
    if generator.backend_kind not in {BackendKind.LLM_OPENAI, BackendKind.LLM_OTHER}:
        raise DeepOpportunityDiscoveryError(
            "Deep opportunity discovery requires a recorded non-fake LLM backend."
        )
    if generator.fallback_used:
        raise DeepOpportunityDiscoveryError(
            "Deep opportunity discovery forbids deterministic or fake generation fallback."
        )
    if config.require_non_fake_backends and retriever.backend_kind != BackendKind.RETRIEVAL_REAL:
        raise DeepOpportunityDiscoveryError(
            "Strict production mode requires real retrieval; mocked retrieval is non-production."
        )
    if config.require_non_fake_backends and retriever.fallback_used:
        raise DeepOpportunityDiscoveryError("Strict production mode forbids retrieval fallback.")

    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    source_brief_path: Path | None = None
    if config.source_mode == "targeted_brief":
        if not config.targeted_brief_path_optional:
            raise DeepOpportunityDiscoveryError(
                "targeted_brief source mode requires targeted_brief_path_optional."
            )
        source_brief_path, brief = _load_targeted_brief(
            root_path=root_path,
            configured_path=config.targeted_brief_path_optional,
        )
        selected_pairs, domains, methods = _targeted_source_metadata(brief)
        scan_path = None
        ranking_by_pair: dict[str, Any] = {}
    else:
        scan_path, scan = _load_latest_ranked_scan(run_id=run_id, reports=reports)
        _, atlas = _load_latest_atlas(run_id=run_id, reports=reports)
        if not scan.selected_pairs:
            raise DeepOpportunityDiscoveryError("Latest atlas scan has no selected pairs.")
        selected_pairs = scan.selected_pairs[: config.max_pairs]
        domains = atlas.domains
        methods = atlas.methods
        ranking_by_pair = {item.pair_id: item for item in scan.selected_rankings}
    domain_by_id = {item.domain_id: item for item in domains}
    method_by_id = {item.method_id: item for item in methods}
    if len(selected_pairs) > config.max_generation_calls:
        raise DeepOpportunityDiscoveryError(
            f"Deep discovery requires {len(selected_pairs)} LLM calls, above "
            f"max_generation_calls={config.max_generation_calls}."
        )

    discovery_number = _next_number(reports, _REPORT_RE)
    discovery_id = f"deep-opportunity-discovery-report-{discovery_number:04d}"
    raw_number = _next_number(reports, _RAW_RE)
    retrieval_number = _next_number(reports, _RETRIEVAL_RE)
    candidates: list[DeepOpportunityCandidate] = []
    scores: list[DeepOpportunityScore] = []
    raw_artifacts: list[LLMOpportunityDiscoveryRawArtifact] = []
    retrieval_contexts: list[RetrievalContext] = []
    warnings: list[str] = []

    for pair_index, pair in enumerate(selected_pairs):
        domain = domain_by_id.get(pair.domain_id)
        method = method_by_id.get(pair.method_id)
        if domain is None or method is None:
            raise DeepOpportunityDiscoveryError(
                f"Atlas metadata is missing for selected pair {pair.pair_id}."
            )
        context_id = f"retrieval-context-{retrieval_number + pair_index:04d}"
        try:
            context = retriever.retrieve(
                run_id=run_id,
                context_id=context_id,
                pair=pair,
                domain=domain,
                method=method,
                limit=config.max_retrieval_sources_per_pair,
            )
            response = generator.generate_for_pair(
                pair_payload=_pair_payload(
                    pair=pair,
                    domain=domain,
                    method=method,
                    ranking=ranking_by_pair.get(pair.pair_id),
                ),
                retrieval_payload=context.model_dump(mode="json"),
                opportunities_per_pair=config.opportunities_per_pair,
            )
        except (AdapterError, AtlasScanError, ValueError) as exc:
            raise DeepOpportunityDiscoveryError(
                f"Deep opportunity generation failed for {pair.pair_id}: {exc}"
            ) from exc
        if context.run_id != run_id or context.source_pair_id != pair.pair_id:
            raise DeepOpportunityDiscoveryError(
                f"Retrieval context identity mismatch for {pair.pair_id}."
            )
        if not response.accepted:
            raw_id = f"llm-deep-opportunity-raw-{raw_number + pair_index:04d}"
            raw_artifacts.append(
                LLMOpportunityDiscoveryRawArtifact(
                    raw_artifact_id=raw_id,
                    run_id=run_id,
                    source_pair_id=pair.pair_id,
                    backend_name=generator.backend_name,
                    model=generator.model,
                    prompt_text=response.prompt_text,
                    requested_output_schema=response.requested_output_schema,
                    raw_response=response.raw_response,
                    rejected_outputs=response.rejected,
                    fallback_used=generator.fallback_used,
                )
            )
            retrieval_contexts.append(context)
            rejection_summary = _rejection_summary(response.rejected)
            warnings.append(
                f"All opportunities were rejected for {pair.pair_id}: {rejection_summary}"
            )
            failed_report = DeepOpportunityDiscoveryReport(
                run_id=run_id,
                discovery_id=discovery_id,
                discovery_status="failed",
                config=config,
                source_context_kind=config.source_mode,
                source_atlas_scan_path=(
                    _relative(root_path, scan_path) if scan_path is not None else None
                ),
                source_targeted_brief_path_optional=(
                    _relative(root_path, source_brief_path)
                    if source_brief_path is not None
                    else None
                ),
                source_pairs=selected_pairs,
                source_domains=list(domain_by_id.values()),
                source_methods=list(method_by_id.values()),
                selected_pair_count=len(selected_pairs),
                attempted_pair_count=pair_index + 1,
                generated_opportunity_count=len(candidates),
                rejected_opportunity_count=sum(
                    len(item.rejected_outputs) for item in raw_artifacts
                ),
                selected_opportunity_count=0,
                domain_family_coverage=0,
                method_family_coverage=0,
                near_duplicate_suppressed_count=0,
                retrieval_context_paths=[
                    f"runs/{run_id}/reports/{item.context_id}.json"
                    for item in retrieval_contexts
                ],
                raw_artifact_paths=[
                    f"runs/{run_id}/reports/{item.raw_artifact_id}.json"
                    for item in raw_artifacts
                ],
                candidates=candidates,
                scores=scores,
                backend_records=[
                    _generation_backend_record(
                        discovery_id=discovery_id,
                        generator=generator,
                        raw_ids=[item.raw_artifact_id for item in raw_artifacts],
                    ),
                    _retrieval_backend_record(
                        discovery_id=discovery_id,
                        retriever=retriever,
                        context_ids=[item.context_id for item in retrieval_contexts],
                    ),
                ],
                warnings=warnings,
                production_ready=False,
            )
            _persist_discovery_artifacts(
                run_id=run_id,
                store=store,
                ledger=ledger,
                report=failed_report,
                retrieval_contexts=retrieval_contexts,
                raw_artifacts=raw_artifacts,
            )
            raise DeepOpportunityDiscoveryError(
                f"LLM produced no valid opportunities for selected pair {pair.pair_id}: "
                f"{rejection_summary}"
            )

        accepted_ids: list[str] = []
        for item_index, item in enumerate(response.accepted, start=1):
            opportunity_id = (
                f"deep-opportunity-{discovery_number:04d}-"
                f"{_slug(pair.domain_id)}-{_slug(pair.method_id)}-{item_index:02d}"
            )
            candidate = DeepOpportunityCandidate(
                opportunity_id=opportunity_id,
                run_id=run_id,
                source_pair_id=pair.pair_id,
                domain_id=pair.domain_id,
                method_id=pair.method_id,
                domain_name=domain.name,
                method_name=method.name,
                **item.candidate.model_dump(mode="python"),
            )
            score = DeepOpportunityScore(
                opportunity_id=opportunity_id,
                **item.score.model_copy(
                    update={"retrieval_confidence": context.retrieval_confidence}
                ).model_dump(mode="python"),
            )
            candidates.append(candidate)
            scores.append(score)
            accepted_ids.append(opportunity_id)
        raw_id = f"llm-deep-opportunity-raw-{raw_number + pair_index:04d}"
        raw_artifacts.append(
            LLMOpportunityDiscoveryRawArtifact(
                raw_artifact_id=raw_id,
                run_id=run_id,
                source_pair_id=pair.pair_id,
                backend_name=generator.backend_name,
                model=generator.model,
                prompt_text=response.prompt_text,
                requested_output_schema=response.requested_output_schema,
                raw_response=response.raw_response,
                accepted_opportunity_ids=accepted_ids,
                rejected_outputs=response.rejected,
                fallback_used=generator.fallback_used,
            )
        )
        retrieval_contexts.append(context)
        if response.rejected:
            warnings.append(
                f"Rejected {len(response.rejected)} malformed or unsafe opportunities for "
                f"{pair.pair_id}."
            )
        if context.retrieval_confidence < 0.4:
            warnings.append(f"Retrieval confidence is low for {pair.pair_id}.")

    selected, selected_scores, duplicate_count = select_diverse_opportunities(
        candidates=candidates,
        scores=scores,
        pairs=selected_pairs,
        max_selected=config.max_selected_opportunities,
        min_domain_families=config.min_domain_family_coverage,
        min_method_families=config.min_method_family_coverage,
        max_per_domain_family=config.max_opportunities_per_domain_family,
        max_per_method_family=config.max_opportunities_per_method_family,
        suppress_duplicates=config.near_duplicate_suppression,
    )
    if not selected:
        raise DeepOpportunityDiscoveryError(
            "No valid opportunities remained after diversity and safety selection."
        )
    if duplicate_count:
        warnings.append(f"Suppressed {duplicate_count} near-duplicate opportunities.")

    generation_record = _generation_backend_record(
        discovery_id=discovery_id,
        generator=generator,
        raw_ids=[item.raw_artifact_id for item in raw_artifacts],
    )
    retrieval_record = _retrieval_backend_record(
        discovery_id=discovery_id,
        retriever=retriever,
        context_ids=[item.context_id for item in retrieval_contexts],
    )
    selector_record = _selector_backend_record(discovery_id)
    backend_records = [generation_record, retrieval_record, selector_record]
    production = evaluate_production_mode(
        run_id=run_id,
        records=backend_records,
        policy=ProductionModePolicy(
            require_non_fake_backends=config.require_non_fake_backends,
            fail_on_silent_fallback=config.fail_on_silent_fallback,
        ),
        expected_stage_kinds=[
            ScientificStageKind.OPPORTUNITY_DISCOVERY,
            ScientificStageKind.LITERATURE_RETRIEVAL,
            ScientificStageKind.DIVERSITY_SELECTION,
        ],
        report_id=f"{discovery_id}-production-evaluation",
    )
    if config.require_non_fake_backends and production.blocking_violation_count:
        details = "; ".join(item.message for item in production.violations)
        raise DeepOpportunityDiscoveryError(
            f"Strict production-mode deep discovery blocked: {details}"
        )

    pair_by_id = {pair.pair_id: pair for pair in selected_pairs}
    selected_pair_metadata = [pair_by_id[item.source_pair_id] for item in selected]
    domain_coverage = len({item.domain_family for item in selected_pair_metadata})
    method_coverage = len({item.method_family for item in selected_pair_metadata})
    if domain_coverage < min(
        config.min_domain_family_coverage,
        len({pair.domain_family for pair in selected_pairs}),
    ):
        warnings.append("Selected opportunities did not reach requested domain-family coverage.")
    if method_coverage < min(
        config.min_method_family_coverage,
        len({pair.method_family for pair in selected_pairs}),
    ):
        warnings.append("Selected opportunities did not reach requested method-family coverage.")

    report = DeepOpportunityDiscoveryReport(
        run_id=run_id,
        discovery_id=discovery_id,
        discovery_status="completed_with_warnings" if warnings else "completed",
        config=config,
        source_context_kind=config.source_mode,
        source_atlas_scan_path=(
            _relative(root_path, scan_path) if scan_path is not None else None
        ),
        source_targeted_brief_path_optional=(
            _relative(root_path, source_brief_path)
            if source_brief_path is not None
            else None
        ),
        source_pairs=selected_pairs,
        source_domains=list(domain_by_id.values()),
        source_methods=list(method_by_id.values()),
        selected_pair_count=len(selected_pairs),
        attempted_pair_count=len(selected_pairs),
        generated_opportunity_count=len(candidates),
        rejected_opportunity_count=sum(len(item.rejected_outputs) for item in raw_artifacts),
        selected_opportunity_count=len(selected),
        domain_family_coverage=domain_coverage,
        method_family_coverage=method_coverage,
        near_duplicate_suppressed_count=duplicate_count,
        retrieval_context_paths=[
            f"runs/{run_id}/reports/{item.context_id}.json" for item in retrieval_contexts
        ],
        raw_artifact_paths=[
            f"runs/{run_id}/reports/{item.raw_artifact_id}.json" for item in raw_artifacts
        ],
        candidates=candidates,
        scores=scores,
        selected_opportunity_ids=[item.opportunity_id for item in selected],
        backend_records=backend_records,
        warnings=warnings,
        production_ready=(
            config.require_non_fake_backends and not production.blocking_violation_count
        ),
    )

    return _persist_discovery_artifacts(
        run_id=run_id,
        store=store,
        ledger=ledger,
        report=report,
        retrieval_contexts=retrieval_contexts,
        raw_artifacts=raw_artifacts,
    )


def _persist_discovery_artifacts(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    report: DeepOpportunityDiscoveryReport,
    retrieval_contexts: list[RetrievalContext],
    raw_artifacts: list[LLMOpportunityDiscoveryRawArtifact],
) -> DeepOpportunityDiscoveryResult:
    discovery_id = report.discovery_id
    metadata = _metadata("deep_opportunity_discovery")
    specs: list[ArtifactWriteSpec] = []
    specs.extend(
        ArtifactWriteSpec(
            item.context_id,
            ArtifactType.REPORT,
            item,
            "json",
            _metadata("deep_opportunity_retrieval_context"),
        )
        for item in retrieval_contexts
    )
    specs.extend(
        ArtifactWriteSpec(
            item.raw_artifact_id,
            ArtifactType.REPORT,
            item,
            "json",
            _metadata("deep_opportunity_llm_raw"),
        )
        for item in raw_artifacts
    )
    specs.extend(
        [
            ArtifactWriteSpec(discovery_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{discovery_id}-markdown",
                ArtifactType.REPORT,
                render_deep_opportunity_markdown(report),
                "markdown",
                metadata,
                filename_stem=discovery_id,
            ),
        ]
    )
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.DEEP_OPPORTUNITY_DISCOVERY_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "discovery_id": discovery_id,
            "selected_pair_count": report.selected_pair_count,
            "generated_opportunity_count": report.generated_opportunity_count,
            "selected_opportunity_count": report.selected_opportunity_count,
            "retrieval_mode": report.config.retrieval_mode,
            "production_ready": report.production_ready,
            "publication_ready": False,
        },
    )
    by_id = {item.id: item for item in persistence.artifacts}
    return DeepOpportunityDiscoveryResult(
        run_id=run_id,
        report=report,
        persistence=persistence,
        report_artifact=by_id[discovery_id],
        markdown_artifact=by_id[f"{discovery_id}-markdown"],
    )


def _rejection_summary(rejected: list[dict[str, Any]]) -> str:
    if not rejected:
        return "the adapter returned no accepted candidates and no rejection diagnostics"
    summaries: list[str] = []
    for item in rejected[:3]:
        index = item.get("index", "unknown")
        raw_reasons = item.get("reasons", [])
        reasons = raw_reasons if isinstance(raw_reasons, list) else [raw_reasons]
        detail = "; ".join(" ".join(str(reason).split()) for reason in reasons)
        summaries.append(f"candidate {index}: {detail[:500]}")
    if len(rejected) > len(summaries):
        summaries.append(f"and {len(rejected) - len(summaries)} more rejected candidate(s)")
    return " | ".join(summaries)


def select_diverse_opportunities(
    *,
    candidates: list[DeepOpportunityCandidate],
    scores: list[DeepOpportunityScore],
    pairs: list[DomainMethodPair],
    max_selected: int,
    min_domain_families: int,
    min_method_families: int,
    max_per_domain_family: int,
    max_per_method_family: int,
    suppress_duplicates: bool = True,
) -> tuple[list[DeepOpportunityCandidate], list[DeepOpportunityScore], int]:
    """Preserve LLM scores while enforcing coverage, caps, and duplicate suppression."""
    candidate_by_id = {item.opportunity_id: item for item in candidates}
    pair_by_id = {item.pair_id: item for item in pairs}
    ordered_scores = sorted(scores, key=lambda item: (-item.final_score, item.opportunity_id))
    unique_scores: list[DeepOpportunityScore] = []
    seen: set[str] = set()
    duplicate_count = 0
    for score in ordered_scores:
        candidate = candidate_by_id.get(score.opportunity_id)
        if candidate is None or candidate.source_pair_id not in pair_by_id:
            continue
        fingerprint = _opportunity_fingerprint(candidate)
        if suppress_duplicates and fingerprint in seen:
            duplicate_count += 1
            continue
        seen.add(fingerprint)
        unique_scores.append(score)

    selected: list[DeepOpportunityScore] = []
    domain_counts: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()

    def can_add(score: DeepOpportunityScore) -> bool:
        candidate = candidate_by_id[score.opportunity_id]
        pair = pair_by_id[candidate.source_pair_id]
        return (
            score not in selected
            and domain_counts[pair.domain_family] < max_per_domain_family
            and method_counts[pair.method_family] < max_per_method_family
        )

    def add(score: DeepOpportunityScore) -> None:
        candidate = candidate_by_id[score.opportunity_id]
        pair = pair_by_id[candidate.source_pair_id]
        selected.append(score)
        domain_counts[pair.domain_family] += 1
        method_counts[pair.method_family] += 1

    for score in unique_scores:
        if len(domain_counts) >= min_domain_families or len(selected) >= max_selected:
            break
        candidate = candidate_by_id[score.opportunity_id]
        pair = pair_by_id[candidate.source_pair_id]
        if pair.domain_family not in domain_counts and can_add(score):
            add(score)
    for score in unique_scores:
        if len(method_counts) >= min_method_families or len(selected) >= max_selected:
            break
        candidate = candidate_by_id[score.opportunity_id]
        pair = pair_by_id[candidate.source_pair_id]
        if pair.method_family not in method_counts and can_add(score):
            add(score)
    for score in unique_scores:
        if len(selected) >= max_selected:
            break
        if can_add(score):
            add(score)
    return [candidate_by_id[item.opportunity_id] for item in selected], selected, duplicate_count


def inspect_deep_opportunities(
    *, run_id: str, root: str | Path = "."
) -> DeepOpportunityDiscoveryInspectionReport:
    """Read the latest deep opportunity report without mutating the run."""
    reports = Path(root) / "runs" / run_id / "reports"
    path = _latest_matching(reports, _REPORT_RE)
    if path is None:
        return DeepOpportunityDiscoveryInspectionReport(
            run_id=run_id,
            deep_opportunity_discovery_present=False,
        )
    try:
        report = DeepOpportunityDiscoveryReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise DeepOpportunityDiscoveryError(
            f"Could not inspect deep opportunity discovery: {exc}"
        ) from exc
    candidate_by_id = {item.opportunity_id: item for item in report.candidates}
    score_by_id = {item.opportunity_id: item for item in report.scores}
    selected = [
        candidate_by_id[item]
        for item in report.selected_opportunity_ids
        if item in candidate_by_id
    ]
    selected_scores = [
        score_by_id[item] for item in report.selected_opportunity_ids if item in score_by_id
    ]
    return DeepOpportunityDiscoveryInspectionReport(
        run_id=run_id,
        deep_opportunity_discovery_present=True,
        discovery_id_optional=report.discovery_id,
        discovery_status_optional=report.discovery_status,
        selected_pair_count=report.selected_pair_count,
        generated_opportunity_count=report.generated_opportunity_count,
        selected_opportunity_count=report.selected_opportunity_count,
        rejected_opportunity_count=report.rejected_opportunity_count,
        domain_family_coverage=report.domain_family_coverage,
        method_family_coverage=report.method_family_coverage,
        retrieval_mode_optional=report.config.retrieval_mode,
        selected_opportunities=selected,
        selected_scores=selected_scores,
        backend_records=report.backend_records,
        warnings=report.warnings,
        production_ready=report.production_ready,
    )


def render_deep_opportunity_text(report: DeepOpportunityDiscoveryInspectionReport) -> str:
    lines = [
        "Deep opportunity discovery: "
        f"{'present' if report.deep_opportunity_discovery_present else 'absent'}",
        f"Status: {report.discovery_status_optional or 'not available'}",
        f"Selected pairs: {report.selected_pair_count}",
        f"Generated opportunities: {report.generated_opportunity_count}",
        f"Selected opportunities: {report.selected_opportunity_count}",
        f"Rejected opportunities: {report.rejected_opportunity_count}",
        f"Retrieval mode: {report.retrieval_mode_optional or 'not available'}",
        f"Domain-family coverage: {report.domain_family_coverage}",
        f"Method-family coverage: {report.method_family_coverage}",
        "Top selected opportunities:",
    ]
    score_by_id = {item.opportunity_id: item for item in report.selected_scores}
    lines.extend(
        f"- {item.domain_name} x {item.method_name}: {item.research_question} "
        f"[{score_by_id[item.opportunity_id].final_score:.3f}]"
        for item in report.selected_opportunities[:10]
        if item.opportunity_id in score_by_id
    )
    lines.extend(
        [
            f"Production ready: {str(report.production_ready).lower()}",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def render_deep_opportunity_markdown(report: DeepOpportunityDiscoveryReport) -> str:
    score_by_id = {item.opportunity_id: item for item in report.scores}
    candidate_by_id = {item.opportunity_id: item for item in report.candidates}
    lines = [
        "# Deep Opportunity Discovery",
        "",
        f"Status: `{report.discovery_status}`",
        f"Retrieval mode: `{report.config.retrieval_mode}`",
        f"Selected pairs: `{report.selected_pair_count}`",
        f"Generated opportunities: `{report.generated_opportunity_count}`",
        f"Selected opportunities: `{report.selected_opportunity_count}`",
        "",
        "## Selected Opportunities",
        "",
    ]
    for opportunity_id in report.selected_opportunity_ids:
        candidate = candidate_by_id.get(opportunity_id)
        score = score_by_id.get(opportunity_id)
        if candidate is None or score is None:
            continue
        lines.extend(
            [
                f"### {candidate.domain_name} x {candidate.method_name}",
                "",
                f"- Question: {candidate.research_question}",
                f"- Hypothesis: {candidate.hypothesis}",
                f"- Object: {candidate.theory_or_model_object}",
                f"- Baselines: {', '.join(candidate.baseline_candidates)}",
                f"- Verification: {candidate.verification_path}",
                f"- Final LLM score: {score.final_score:.3f}",
                "",
            ]
        )
    lines.extend(
        [
            "Retrieval is bounded context only. Novelty and underuse remain hypotheses; this "
            "report creates no scientific validation.",
            "",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def _generation_backend_record(
    *, discovery_id: str, generator: OpportunityDiscoveryClient, raw_ids: list[str]
) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{discovery_id}-llm-generation",
        stage_kind=ScientificStageKind.OPPORTUNITY_DISCOVERY,
        backend_kind=generator.backend_kind,
        backend_name=generator.backend_name,
        is_scientific_generation=True,
        is_scientific_judgment=True,
        is_execution_or_verification=False,
        reason=(
            "Research questions, hypotheses, model objects, baselines, verification paths, "
            "failure modes, and scientific scores come from the recorded non-fake LLM backend."
        ),
        artifact_ids=[discovery_id, *raw_ids],
        fallback_used=generator.fallback_used,
        fallback_disclosed=generator.fallback_disclosed,
    )


def _retrieval_backend_record(
    *,
    discovery_id: str,
    retriever: OpportunityRetrievalProvider,
    context_ids: list[str],
) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{discovery_id}-retrieval",
        stage_kind=ScientificStageKind.LITERATURE_RETRIEVAL,
        backend_kind=retriever.backend_kind,
        backend_name=retriever.backend_name,
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=True,
        allowed_in_production=retriever.backend_kind == BackendKind.RETRIEVAL_REAL,
        reason=(
            "Retrieval supplies bounded metadata/abstract context only; mocked retrieval is "
            "development-only and real retrieval does not establish novelty or coverage."
        ),
        artifact_ids=context_ids,
        fallback_used=retriever.fallback_used,
        fallback_disclosed=retriever.fallback_disclosed,
    )


def _selector_backend_record(discovery_id: str) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{discovery_id}-diversity-selection",
        stage_kind=ScientificStageKind.DIVERSITY_SELECTION,
        backend_kind=BackendKind.HEURISTIC,
        backend_name="coverage_and_duplicate_selector",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=False,
        allowed_in_production=True,
        reason=(
            "Deterministic selection preserves LLM scores while enforcing family coverage, "
            "caps, and duplicate suppression; it makes no new scientific judgment."
        ),
        artifact_ids=[discovery_id],
    )


def _pair_payload(
    *,
    pair: DomainMethodPair,
    domain: DomainAtlasEntry,
    method: MethodAtlasEntry,
    ranking: Any,
) -> dict[str, Any]:
    return {
        "pair": pair.model_dump(mode="json"),
        "domain": domain.model_dump(mode="json"),
        "method": method.model_dump(mode="json"),
        "m97_ranking": ranking.model_dump(mode="json") if ranking is not None else None,
        "scientific_boundary": (
            "Generate hypotheses and testable objects only. Retrieval cannot establish novelty, "
            "underuse, proof, validation, or publication readiness."
        ),
    }


def _retrieval_query(*, domain: DomainAtlasEntry, method: MethodAtlasEntry) -> str:
    return _retrieval_queries(domain=domain, method=method)[0]


def _retrieval_queries(
    *, domain: DomainAtlasEntry, method: MethodAtlasEntry
) -> list[str]:
    """Build a small, generic query portfolio from structured scientific metadata."""
    domain_terms = _compact_retrieval_terms(
        " ".join([domain.name, domain.description, *domain.example_questions]),
        limit=12,
    )
    method_terms = _compact_retrieval_terms(
        " ".join([method.name, method.description, *method.natural_problem_types]),
        limit=14,
    )
    baseline_terms = _compact_retrieval_terms(
        " ".join([*domain.natural_baselines, *method.natural_baselines]),
        limit=10,
    )
    frequent_terms = _frequent_retrieval_terms(
        " ".join(
            [
                domain.name,
                domain.description,
                method.name,
                method.description,
                *domain.natural_baselines,
                *method.natural_baselines,
            ]
        ),
        limit=12,
    )
    candidates = [
        [*domain_terms[:9], *method_terms[:9]],
        method_terms,
        [*frequent_terms, *baseline_terms[:4]],
        [*domain_terms[:8], *baseline_terms[:8]],
    ]
    queries: list[str] = []
    for index, terms in enumerate(candidates):
        query_terms = (
            [*terms[: _RETRIEVAL_TERM_LIMIT - 1], "baseline"]
            if index == 0
            else terms
        )
        query = _bounded_query(query_terms)
        if query and query not in queries:
            queries.append(query)
        if len(queries) >= _RETRIEVAL_QUERY_VARIANT_LIMIT:
            break
    return queries or ["scientific method baseline"]


def _retrieve_query_variants(
    *,
    client: OpenAlexRetrievalClient,
    queries: list[str],
    domain: DomainAtlasEntry,
    method: MethodAtlasEntry,
    limit: int,
) -> list[RetrievalResult]:
    topic_terms = _retrieval_topic_terms(domain=domain, method=method)
    merged: dict[str, tuple[float, int, RetrievalResult]] = {}
    for query_index, query in enumerate(queries):
        for result in client.search(query, limit):
            relevance = _source_topic_relevance(result=result, topic_terms=topic_terms)
            overlap = _source_topic_overlap(result=result, topic_terms=topic_terms)
            if overlap < _RETRIEVAL_MIN_TOPIC_OVERLAP:
                continue
            identity = (result.doi or result.source_id).casefold()
            candidate = (relevance, -query_index, result)
            previous = merged.get(identity)
            if previous is None or candidate[:2] > previous[:2]:
                merged[identity] = candidate
    ranked = sorted(
        merged.values(),
        key=lambda item: (-item[0], -item[1], item[2].source_id),
    )
    return [item[2] for item in ranked[:limit]]


def _retrieval_topic_terms(
    *, domain: DomainAtlasEntry, method: MethodAtlasEntry
) -> set[str]:
    return set(
        _compact_retrieval_terms(
            " ".join(
                [
                    domain.name,
                    domain.description,
                    method.name,
                    method.description,
                    *domain.natural_baselines,
                    *method.natural_baselines,
                ]
            ),
            limit=48,
        )
    )


def _source_topic_overlap(
    *, result: RetrievalResult, topic_terms: set[str]
) -> int:
    source_terms = set(
        _compact_retrieval_terms(
            " ".join([result.title, result.abstract or "", result.snippet or ""]),
            limit=256,
        )
    )
    return len(topic_terms & source_terms)


def _source_topic_relevance(
    *, result: RetrievalResult, topic_terms: set[str]
) -> float:
    overlap = _source_topic_overlap(result=result, topic_terms=topic_terms)
    denominator = max(1, min(12, len(topic_terms)))
    return round(min(1.0, overlap / denominator), 6)


def _compact_retrieval_terms(text: str, *, limit: int) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for term in _TOKEN_RE.findall(text.casefold()):
        if (
            term in _RETRIEVAL_STOPWORDS
            or term in seen
            or len(term) < 2
            or term.isdigit()
        ):
            continue
        terms.append(term)
        seen.add(term)
        if len(terms) >= limit:
            break
    return terms


def _frequent_retrieval_terms(text: str, *, limit: int) -> list[str]:
    ordered = _compact_retrieval_terms(text, limit=256)
    counts = Counter(
        term
        for term in _TOKEN_RE.findall(text.casefold())
        if term not in _RETRIEVAL_STOPWORDS and len(term) >= 2 and not term.isdigit()
    )
    order = {term: index for index, term in enumerate(ordered)}
    return sorted(ordered, key=lambda term: (-counts[term], order[term]))[:limit]


def _bounded_query(terms: list[str]) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for term in terms:
        if term in seen:
            continue
        candidate = " ".join([*values, term])
        if len(candidate) > _OPENALEX_SAFE_QUERY_CHARS:
            break
        values.append(term)
        seen.add(term)
        if len(values) >= _RETRIEVAL_TERM_LIMIT:
            break
    return " ".join(values)


def _retrieval_confidence(sources: list[RetrievedSourceSummary]) -> float:
    if not sources:
        return 0.05
    metadata = sum(
        bool(item.year) + bool(item.venue) + bool(item.doi) + bool(item.abstract_or_snippet)
        for item in sources
    ) / (4 * len(sources))
    count = min(1.0, len(sources) / 5.0)
    return round(min(0.85, 0.2 + 0.4 * count + 0.25 * metadata), 6)


def _opportunity_fingerprint(candidate: DeepOpportunityCandidate) -> str:
    text = " ".join(
        [
            candidate.research_question,
            candidate.theory_or_model_object,
            candidate.mathematical_or_computational_form,
            " ".join(candidate.baseline_candidates),
        ]
    )
    tokens = _TOKEN_RE.findall(text.lower())
    return " ".join(tokens)


def _load_latest_ranked_scan(*, run_id: str, reports: Path) -> tuple[Path, AtlasScanReport]:
    path = _latest_matching(reports, _SCAN_RE)
    if path is None:
        raise DeepOpportunityDiscoveryError(
            f"No ranked atlas scan found for run_id={run_id}; run scan-domain-method-pairs first."
        )
    report = _load_atlas_report(path)
    if report.run_id != run_id or report.scan_status not in {
        "completed",
        "completed_with_warnings",
    }:
        raise DeepOpportunityDiscoveryError("Latest ranked atlas scan is inconsistent.")
    return path, report


def _load_latest_atlas(*, run_id: str, reports: Path) -> tuple[Path, AtlasScanReport]:
    path = _latest_matching(reports, _ATLAS_RE)
    if path is None:
        raise DeepOpportunityDiscoveryError(f"No domain/method atlas found for run_id={run_id}.")
    report = _load_atlas_report(path)
    if report.run_id != run_id or report.scan_status != "atlas_built":
        raise DeepOpportunityDiscoveryError("Latest domain/method atlas is inconsistent.")
    return path, report


def _load_atlas_report(path: Path) -> AtlasScanReport:
    try:
        return AtlasScanReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise DeepOpportunityDiscoveryError(f"Could not load atlas report {path}: {exc}") from exc


def _load_targeted_brief(
    *, root_path: Path, configured_path: str
) -> tuple[Path, TargetedResearchBrief]:
    path = Path(configured_path)
    if not path.is_absolute():
        path = root_path / path
    try:
        brief = TargetedResearchBrief.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise DeepOpportunityDiscoveryError(
            f"Could not load targeted research brief {path}: {exc}"
        ) from exc
    return path, brief


def _targeted_source_metadata(
    brief: TargetedResearchBrief,
) -> tuple[list[DomainMethodPair], list[DomainAtlasEntry], list[MethodAtlasEntry]]:
    """Map a user-selected brief to neutral source metadata without opportunity scoring."""
    domain_id = _slug(brief.domain) or "targeted_domain"
    method_id = _slug(brief.method) or "targeted_method"
    object_text = brief.theory_or_model_object_optional or brief.central_question
    verification_text = (
        brief.experiment_or_proof_direction_optional
        or "Construct an explicit verification plan before making scientific claims."
    )
    baselines = brief.baseline_candidates or [
        "A prespecified conventional comparator appropriate to the research question."
    ]
    metrics = brief.expected_metrics or [
        "Prespecified outcome metrics tied to the bounded research question."
    ]
    risks = brief.known_risks or [
        "False bridges and unsupported scope expansion must be checked explicitly."
    ]
    domain = DomainAtlasEntry(
        domain_id=domain_id,
        name=brief.domain,
        domain_family=f"targeted:{domain_id}",
        description=brief.central_question,
        canonical_objects=[object_text],
        data_types=[brief.data_regime],
        natural_baselines=baselines,
        verification_modes=[verification_text],
        standard_metrics=metrics,
        common_failure_modes=risks,
        example_questions=[brief.central_question],
    )
    method = MethodAtlasEntry(
        method_id=method_id,
        name=brief.method,
        method_family=f"targeted:{method_id}",
        description=brief.method,
        canonical_objects=[object_text],
        natural_problem_types=[brief.central_question],
        required_inputs=[brief.data_regime],
        typical_outputs=metrics,
        verification_modes=[verification_text],
        natural_baselines=baselines,
        false_bridge_patterns=risks,
    )
    pair = DomainMethodPair(
        pair_id=f"targeted-pair-{domain_id}--{method_id}",
        domain_id=domain_id,
        method_id=method_id,
        domain_family=domain.domain_family,
        method_family=method.method_family,
        object_mapping_candidates=[object_text],
        baseline_candidates=baselines,
        verification_path_candidates=[verification_text],
        data_or_simulation_candidates=[brief.data_regime],
        compatibility_status="compatible",
    )
    return [pair], [domain], [method]


def _metadata(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "artifact_role": "deep_opportunity_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }


def _slug(value: str) -> str:
    return "_".join(_TOKEN_RE.findall(value.lower()))


def _latest_matching(directory: Path, pattern: re.Pattern[str]) -> Path | None:
    if not directory.is_dir():
        return None
    matches = [item for item in directory.iterdir() if pattern.match(item.name)]
    return max(matches, key=lambda item: item.name) if matches else None


def _next_number(directory: Path, pattern: re.Pattern[str]) -> int:
    if not directory.is_dir():
        return 1
    values = [
        int(match.group(1))
        for item in directory.iterdir()
        if (match := pattern.match(item.name)) is not None
    ]
    return max(values, default=0) + 1


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


__all__ = [
    "DeepOpportunityDiscoveryError",
    "DeepOpportunityDiscoveryResult",
    "MockedOpportunityRetriever",
    "OpenAlexOpportunityRetriever",
    "OpportunityLiteratureRefreshResult",
    "OpportunityRetrievalProvider",
    "discover_deep_opportunities",
    "inspect_deep_opportunities",
    "refresh_deep_opportunity_literature",
    "render_deep_opportunity_markdown",
    "render_deep_opportunity_text",
    "select_diverse_opportunities",
]
