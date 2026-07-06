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
    RetrievedSourceSummary,
    ScientificStageKind,
    StageBackendRecord,
)

_ATLAS_RE = re.compile(r"^domain-method-atlas-(\d{4})\.json$")
_SCAN_RE = re.compile(r"^atlas-scan-(\d{4})\.json$")
_REPORT_RE = re.compile(r"^deep-opportunity-discovery-report-(\d{4})\.json$")
_RAW_RE = re.compile(r"^llm-deep-opportunity-raw-(\d{4})\.json$")
_RETRIEVAL_RE = re.compile(r"^retrieval-context-(\d{4})\.json$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class DeepOpportunityDiscoveryError(RuntimeError):
    """Raised when deep discovery cannot proceed without weakening its policy."""


@dataclass(frozen=True)
class DeepOpportunityDiscoveryResult:
    run_id: str
    report: DeepOpportunityDiscoveryReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


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
        query = _retrieval_query(domain=domain, method=method)
        results = self.client.search(query, limit)
        sources = [
            RetrievedSourceSummary(
                source_id=result.source_id,
                title=result.title,
                authors=result.authors,
                year=result.year,
                venue=result.venue,
                abstract_or_snippet=result.abstract or result.snippet,
                doi=result.doi,
                relevance_score=result.score,
                provider=result.provider,
                fake_or_mocked=False,
            )
            for result in results
        ]
        confidence = _retrieval_confidence(sources)
        limitations = [
            "Retrieved metadata and abstracts are bounded literature context only.",
            "The result set does not establish novelty, underuse, or complete coverage.",
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
            query=query,
            sources=sources,
            retrieval_confidence=confidence,
            limitations=limitations,
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
    scan_path, scan = _load_latest_ranked_scan(run_id=run_id, reports=reports)
    _, atlas = _load_latest_atlas(run_id=run_id, reports=reports)
    if not scan.selected_pairs:
        raise DeepOpportunityDiscoveryError("Latest atlas scan has no selected pairs.")
    domain_by_id = {item.domain_id: item for item in atlas.domains}
    method_by_id = {item.method_id: item for item in atlas.methods}
    ranking_by_pair = {item.pair_id: item for item in scan.selected_rankings}
    selected_pairs = scan.selected_pairs[: config.max_pairs]
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
            raise DeepOpportunityDiscoveryError(
                f"LLM produced no valid opportunities for selected pair {pair.pair_id}."
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
        source_atlas_scan_path=_relative(root_path, scan_path),
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
            "retrieval_mode": config.retrieval_mode,
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
    return (
        f'"{domain.name}" "{method.name}" '
        f"{domain.canonical_objects[0]} {method.canonical_objects[0]} baseline"
    )


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
    "OpportunityRetrievalProvider",
    "discover_deep_opportunities",
    "inspect_deep_opportunities",
    "render_deep_opportunity_markdown",
    "render_deep_opportunity_text",
    "select_diverse_opportunities",
]
