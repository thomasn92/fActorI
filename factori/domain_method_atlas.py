"""Curated domain/method atlas, exclusion-only filtering, and LLM pair ranking."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factori.adapters.atlas_ranking import PairRankingClient
from factori.adapters.errors import AdapterError
from factori.artifacts import ArtifactStore
from factori.commands import ensure_run_initialized
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.production_mode import evaluate_production_mode, stage_backend_record
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AtlasScanInspectionReport,
    AtlasScanReport,
    BackendKind,
    CompatibilityExclusion,
    CompatibilityFilterReport,
    ControllerActionType,
    DomainAtlasEntry,
    DomainMethodPair,
    LLMPairRankingPrompt,
    LLMPairRankingReport,
    LLMPairRankingResult,
    MethodAtlasEntry,
    ProductionModePolicy,
    ScientificStageKind,
    StageBackendRecord,
)

_ATLAS_RE = re.compile(r"^domain-method-atlas-(\d{4})\.json$")
_FILTER_RE = re.compile(r"^compatibility-filter-(\d{4})\.json$")
_RANKING_RE = re.compile(r"^llm-pair-ranking-(\d{4})\.json$")
_SCAN_RE = re.compile(r"^atlas-scan-(\d{4})\.json$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class AtlasScanError(RuntimeError):
    """Raised when atlas construction or strict pair ranking cannot proceed safely."""


@dataclass(frozen=True)
class AtlasBuildResult:
    run_id: str
    report: AtlasScanReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


@dataclass(frozen=True)
class AtlasPairScanResult:
    run_id: str
    report: AtlasScanReport
    compatibility_report: CompatibilityFilterReport
    ranking_report: LLMPairRankingReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef


@dataclass(frozen=True)
class _DomainSeed:
    name: str
    family: str
    objects: tuple[str, ...]


@dataclass(frozen=True)
class _MethodSeed:
    name: str
    family: str
    objects: tuple[str, ...]


_DOMAIN_SEEDS = (
    _DomainSeed(
        "mathematical finance",
        "finance",
        ("stochastic_process", "time_series", "optimization", "extreme_tail"),
    ),
    _DomainSeed(
        "insurance risk", "finance", ("distribution", "extreme_tail", "survival", "optimization")
    ),
    _DomainSeed(
        "market microstructure", "finance", ("point_process", "queue", "time_series", "network")
    ),
    _DomainSeed("option surfaces", "finance", ("surface", "stochastic_process", "pde", "low_rank")),
    _DomainSeed(
        "portfolio optimization",
        "finance",
        ("optimization", "distribution", "time_series", "online_decision"),
    ),
    _DomainSeed(
        "energy markets",
        "energy_climate",
        ("time_series", "network", "optimization", "stochastic_process"),
    ),
    _DomainSeed(
        "climate risk", "energy_climate", ("extreme_tail", "spatial", "distribution", "causal")
    ),
    _DomainSeed("transportation", "spatial_systems", ("network", "flow", "queue", "spatial")),
    _DomainSeed("urban systems", "spatial_systems", ("spatial", "network", "agents", "causal")),
    _DomainSeed(
        "human geography", "spatial_systems", ("spatial", "flow", "network", "distribution")
    ),
    _DomainSeed("epidemiology", "health", ("survival", "network", "causal", "stochastic_process")),
    _DomainSeed("healthcare operations", "health", ("queue", "optimization", "survival", "causal")),
    _DomainSeed(
        "supply chains", "operations", ("network", "queue", "optimization", "extreme_tail")
    ),
    _DomainSeed(
        "queueing systems", "operations", ("queue", "stochastic_process", "control", "network")
    ),
    _DomainSeed(
        "telecommunications", "infrastructure", ("network", "queue", "control", "online_decision")
    ),
    _DomainSeed(
        "cybersecurity", "infrastructure", ("network", "point_process", "game", "extreme_tail")
    ),
    _DomainSeed("sports analytics", "social_data", ("time_series", "network", "causal", "spatial")),
    _DomainSeed(
        "education analytics", "social_data", ("causal", "survival", "network", "distribution")
    ),
    _DomainSeed("labor markets", "social_data", ("causal", "network", "distribution", "matching")),
    _DomainSeed(
        "causal policy evaluation",
        "social_data",
        ("causal", "distribution", "time_series", "spatial"),
    ),
    _DomainSeed(
        "ecology", "life_science", ("network", "spatial", "stochastic_process", "topology")
    ),
    _DomainSeed(
        "neuroscience", "life_science", ("network", "time_series", "point_process", "low_rank")
    ),
    _DomainSeed(
        "bioinformatics", "life_science", ("graph", "distribution", "low_rank", "topology")
    ),
    _DomainSeed(
        "materials science", "physical_science", ("graph", "pde", "topology", "simulation")
    ),
    _DomainSeed(
        "power grids",
        "infrastructure",
        ("network", "control", "optimization", "stochastic_process"),
    ),
    _DomainSeed(
        "robotics", "engineering", ("control", "online_decision", "geometry", "simulation")
    ),
    _DomainSeed(
        "control systems", "engineering", ("control", "pde", "optimization", "stochastic_process")
    ),
    _DomainSeed(
        "reinforcement learning", "ai_ml", ("online_decision", "control", "game", "simulation")
    ),
    _DomainSeed(
        "LLM evaluation", "ai_ml", ("distribution", "causal", "uncertainty", "representation")
    ),
    _DomainSeed(
        "mechanistic interpretability", "ai_ml", ("graph", "representation", "causal", "low_rank")
    ),
    _DomainSeed("knowledge graphs", "ai_ml", ("graph", "network", "representation", "causal")),
    _DomainSeed(
        "recommender systems", "ai_ml", ("low_rank", "online_decision", "network", "causal")
    ),
    _DomainSeed("scientific ML", "ai_ml", ("pde", "simulation", "representation", "uncertainty")),
    _DomainSeed(
        "computational social science",
        "social_data",
        ("network", "agents", "causal", "distribution"),
    ),
    _DomainSeed(
        "agriculture systems", "environment", ("spatial", "causal", "time_series", "optimization")
    ),
    _DomainSeed("water systems", "environment", ("network", "pde", "control", "extreme_tail")),
    _DomainSeed("disaster response", "operations", ("network", "queue", "spatial", "optimization")),
    _DomainSeed(
        "manufacturing systems", "operations", ("queue", "control", "optimization", "survival")
    ),
    _DomainSeed("ocean systems", "physical_science", ("pde", "spatial", "time_series", "topology")),
    _DomainSeed(
        "public health surveillance",
        "health",
        ("point_process", "spatial", "causal", "time_series"),
    ),
    _DomainSeed("migration studies", "spatial_systems", ("flow", "network", "spatial", "causal")),
    _DomainSeed("housing markets", "social_data", ("spatial", "time_series", "causal", "network")),
)

_METHOD_SEEDS = (
    _MethodSeed("optimal transport", "transport_geometry", ("distribution", "flow", "geometry")),
    _MethodSeed(
        "Wasserstein robustness",
        "transport_geometry",
        ("distribution", "optimization", "uncertainty"),
    ),
    _MethodSeed("copulas", "dependence", ("distribution", "extreme_tail", "time_series")),
    _MethodSeed(
        "extreme value theory", "dependence", ("extreme_tail", "distribution", "time_series")
    ),
    _MethodSeed(
        "point processes",
        "stochastic_models",
        ("point_process", "time_series", "stochastic_process"),
    ),
    _MethodSeed(
        "Hawkes processes", "stochastic_models", ("point_process", "network", "time_series")
    ),
    _MethodSeed(
        "stochastic control",
        "control_optimization",
        ("control", "stochastic_process", "optimization"),
    ),
    _MethodSeed(
        "convex duality", "control_optimization", ("optimization", "geometry", "uncertainty")
    ),
    _MethodSeed(
        "distributionally robust optimization",
        "control_optimization",
        ("optimization", "distribution", "uncertainty"),
    ),
    _MethodSeed("kernel methods", "representation", ("kernel", "spatial", "representation")),
    _MethodSeed("spectral graph theory", "graph_topology", ("graph", "network", "low_rank")),
    _MethodSeed("topological data analysis", "graph_topology", ("topology", "graph", "spatial")),
    _MethodSeed(
        "information geometry", "transport_geometry", ("geometry", "distribution", "representation")
    ),
    _MethodSeed("causal inference", "causal", ("causal", "distribution", "time_series")),
    _MethodSeed("mean-field limits", "dynamics", ("agents", "stochastic_process", "pde")),
    _MethodSeed("PDE/diffusion models", "dynamics", ("pde", "spatial", "network")),
    _MethodSeed(
        "martingale methods", "probability", ("stochastic_process", "time_series", "proof_object")
    ),
    _MethodSeed(
        "low-rank factorization", "representation", ("low_rank", "matrix", "representation")
    ),
    _MethodSeed("random matrix theory", "probability", ("matrix", "low_rank", "distribution")),
    _MethodSeed(
        "Bayesian nonparametrics", "bayesian", ("distribution", "uncertainty", "time_series")
    ),
    _MethodSeed(
        "conformal prediction", "uncertainty", ("uncertainty", "distribution", "time_series")
    ),
    _MethodSeed("survival analysis", "survival", ("survival", "time_series", "causal")),
    _MethodSeed("queueing theory", "operations", ("queue", "stochastic_process", "network")),
    _MethodSeed("game theory", "strategic", ("game", "agents", "optimization")),
    _MethodSeed("mechanism design", "strategic", ("game", "matching", "agents")),
    _MethodSeed(
        "online learning", "sequential_learning", ("online_decision", "time_series", "uncertainty")
    ),
    _MethodSeed("bandits", "sequential_learning", ("online_decision", "causal", "uncertainty")),
    _MethodSeed(
        "simulation-based inference", "inference", ("simulation", "distribution", "uncertainty")
    ),
    _MethodSeed(
        "normalizing flows", "generative", ("distribution", "representation", "simulation")
    ),
    _MethodSeed("agent-based modeling", "simulation", ("agents", "simulation", "network")),
    _MethodSeed("network science", "graph_topology", ("network", "graph", "flow")),
    _MethodSeed("spatial statistics", "spatial", ("spatial", "distribution", "causal")),
)


def domain_atlas() -> list[DomainAtlasEntry]:
    """Return the curated deterministic domain catalog in stable order."""
    return [_domain_entry(seed) for seed in _DOMAIN_SEEDS]


def method_atlas() -> list[MethodAtlasEntry]:
    """Return the curated deterministic method-lens catalog in stable order."""
    return [_method_entry(seed) for seed in _METHOD_SEEDS]


def build_domain_method_atlas(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> AtlasBuildResult:
    """Persist curated atlas metadata without scientific ranking authority."""
    ensure_run_initialized(run_id=run_id, root=Path(root), store=store, ledger=ledger)
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    number = _next_number(reports, _ATLAS_RE)
    scan_id = f"domain-method-atlas-{number:04d}"
    domains = domain_atlas()
    methods = method_atlas()
    backend_record = _atlas_backend_record(scan_id)
    report = AtlasScanReport(
        run_id=run_id,
        scan_id=scan_id,
        scan_status="atlas_built",
        domain_count=len(domains),
        method_count=len(methods),
        raw_pair_count=0,
        excluded_pair_count=0,
        surviving_pair_count=0,
        llm_ranked_pair_count=0,
        selected_pair_count=0,
        domain_family_coverage=len({entry.domain_family for entry in domains}),
        method_family_coverage=len({entry.method_family for entry in methods}),
        domains=domains,
        methods=methods,
        backend_records=[backend_record],
        production_ready=True,
        publication_ready=False,
    )
    metadata = _metadata("domain_method_atlas")
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(scan_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{scan_id}-markdown",
                ArtifactType.REPORT,
                render_atlas_scan_markdown(report),
                "markdown",
                metadata,
                filename_stem=scan_id,
            ),
        ],
        action_type=ControllerActionType.DOMAIN_METHOD_ATLAS_BUILT,
        commit_payload={
            "run_id": run_id,
            "scan_id": scan_id,
            "domain_count": len(domains),
            "method_count": len(methods),
            "production_ready": True,
            "publication_ready": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return AtlasBuildResult(
        run_id=run_id,
        report=report,
        persistence=persistence,
        report_artifact=by_id[scan_id],
        markdown_artifact=by_id[f"{scan_id}-markdown"],
    )


def evaluate_pair_compatibility(
    *,
    domain: DomainAtlasEntry,
    method: MethodAtlasEntry,
    no_data_mode: bool = True,
) -> tuple[DomainMethodPair, CompatibilityExclusion]:
    """Apply negative compatibility rules without scoring scientific opportunity."""
    pair_id = f"pair-{domain.domain_id}--{method.method_id}"
    domain_objects = {_normalize_tag(item): item for item in domain.canonical_objects}
    method_objects = {_normalize_tag(item): item for item in method.canonical_objects}
    shared_objects = sorted(set(domain_objects).intersection(method_objects))
    object_mappings = [
        f"{domain_objects[tag]} maps to {method_objects[tag]}" for tag in shared_objects
    ]
    baselines = (
        [f"{domain.natural_baselines[0]} vs {method.natural_baselines[0]}"]
        if domain.natural_baselines and method.natural_baselines
        else []
    )
    domain_modes = {_normalize_tag(item): item for item in domain.verification_modes}
    method_modes = {_normalize_tag(item): item for item in method.verification_modes}
    verification_tags = sorted(set(domain_modes).intersection(method_modes))
    verification_paths = [domain_modes[tag] for tag in verification_tags]
    domain_data = {_normalize_tag(item): item for item in domain.data_types}
    method_inputs = {_normalize_tag(item): item for item in method.required_inputs}
    data_tags = sorted(set(domain_data).intersection(method_inputs))
    data_paths = [domain_data[tag] for tag in data_tags]
    if not data_paths and "synthetic_data" in domain_data:
        data_paths = [domain_data["synthetic_data"]]
    if no_data_mode:
        data_paths = [
            value
            for value in data_paths
            if _normalize_tag(value) not in {"private_data", "real_world_data_only"}
        ]

    missing_object = not object_mappings
    missing_baseline = not baselines
    missing_verification = not verification_paths
    missing_data = not data_paths
    decorative = missing_object
    reasons: list[str] = []
    if missing_object:
        reasons.append("no canonical domain object maps to a canonical method object")
    if missing_baseline:
        reasons.append("no plausible baseline contract is available")
    if missing_verification:
        reasons.append("no shared verification path is available")
    if missing_data:
        reasons.append("no admissible data, simulation, or proof input path is available")
    if decorative:
        reasons.append("method vocabulary would be decorative without an object mapping")
    excluded = bool(reasons)
    status = (
        "excluded"
        if excluded
        else ("weak_compatible" if len(shared_objects) == 1 else "compatible")
    )
    pair = DomainMethodPair(
        pair_id=pair_id,
        domain_id=domain.domain_id,
        method_id=method.method_id,
        domain_family=domain.domain_family,
        method_family=method.method_family,
        object_mapping_candidates=object_mappings,
        baseline_candidates=baselines,
        verification_path_candidates=verification_paths,
        data_or_simulation_candidates=data_paths,
        compatibility_status=status,
    )
    exclusion = CompatibilityExclusion(
        pair_id=pair_id,
        excluded=excluded,
        exclusion_reasons=reasons,
        missing_object_mapping=missing_object,
        missing_baseline=missing_baseline,
        missing_verification_path=missing_verification,
        missing_data_or_simulation_path=missing_data,
        decorative_vocabulary_risk=decorative,
    )
    return pair, exclusion


def build_compatibility_filter_report(
    *,
    run_id: str,
    filter_id: str,
    source_atlas_path: str,
    domains: list[DomainAtlasEntry],
    methods: list[MethodAtlasEntry],
    no_data_mode: bool = True,
) -> CompatibilityFilterReport:
    """Build the complete Cartesian pair set and deterministic exclusions."""
    evaluated = [
        evaluate_pair_compatibility(domain=domain, method=method, no_data_mode=no_data_mode)
        for domain in domains
        for method in methods
    ]
    pairs = [item[0] for item in evaluated]
    exclusions = [item[1] for item in evaluated]
    counts = Counter(pair.compatibility_status for pair in pairs)
    excluded_count = counts["excluded"]
    return CompatibilityFilterReport(
        run_id=run_id,
        filter_id=filter_id,
        source_atlas_path=source_atlas_path,
        raw_pair_count=len(pairs),
        excluded_pair_count=excluded_count,
        surviving_pair_count=len(pairs) - excluded_count,
        compatibility_status_counts=dict(sorted(counts.items())),
        pairs=pairs,
        exclusions=exclusions,
        backend_records=[_compatibility_backend_record(filter_id)],
        warnings=[],
        publication_ready=False,
    )


def scan_domain_method_pairs(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    ranker: PairRankingClient,
    top_pairs: int = 30,
    require_non_fake_backends: bool = False,
    minimum_domain_families: int = 8,
    minimum_method_families: int = 8,
    max_pairs_per_domain_family: int = 5,
    max_pairs_per_method_family: int = 5,
    batch_size: int = 20,
    max_ranking_calls: int = 50,
) -> AtlasPairScanResult:
    """Filter atlas pairs, obtain non-fake LLM rankings, and select diverse pairs."""
    if top_pairs < 1 or batch_size < 1 or max_ranking_calls < 1:
        raise ValueError("top_pairs, batch_size, and max_ranking_calls must be positive")
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    atlas_path, atlas = _load_latest_atlas(run_id=run_id, reports=reports)
    if require_non_fake_backends and ranker.backend_kind not in {
        BackendKind.LLM_OPENAI,
        BackendKind.LLM_OTHER,
    }:
        raise AtlasScanError("Strict production mode requires a non-fake LLM pair-ranking backend.")
    if require_non_fake_backends and ranker.fallback_used:
        raise AtlasScanError("Strict production mode forbids fallback during LLM pair ranking.")

    scan_number = _next_number(reports, _SCAN_RE)
    filter_id = f"compatibility-filter-{_next_number(reports, _FILTER_RE):04d}"
    ranking_id = f"llm-pair-ranking-{_next_number(reports, _RANKING_RE):04d}"
    scan_id = f"atlas-scan-{scan_number:04d}"
    compatibility = build_compatibility_filter_report(
        run_id=run_id,
        filter_id=filter_id,
        source_atlas_path=_relative(root_path, atlas_path),
        domains=atlas.domains,
        methods=atlas.methods,
    )
    surviving = [pair for pair in compatibility.pairs if pair.compatibility_status != "excluded"]
    required_calls = math.ceil(len(surviving) / batch_size)
    if required_calls > max_ranking_calls:
        raise AtlasScanError(
            f"Ranking {len(surviving)} surviving pairs requires {required_calls} calls, "
            f"above max_ranking_calls={max_ranking_calls}."
        )

    domain_by_id = {entry.domain_id: entry for entry in atlas.domains}
    method_by_id = {entry.method_id: entry for entry in atlas.methods}
    prompts: list[LLMPairRankingPrompt] = []
    rankings: list[LLMPairRankingResult] = []
    for batch_index, start in enumerate(range(0, len(surviving), batch_size), start=1):
        batch = surviving[start : start + batch_size]
        payloads = [
            _ranking_payload(
                pair=pair,
                domain=domain_by_id[pair.domain_id],
                method=method_by_id[pair.method_id],
            )
            for pair in batch
        ]
        try:
            prompt, batch_results = ranker.rank_batch(
                pair_payloads=payloads,
                batch_index=batch_index,
                prompt_id=f"{ranking_id}-prompt-{batch_index:03d}",
            )
        except AdapterError as exc:
            raise AtlasScanError(str(exc)) from exc
        prompts.append(prompt)
        rankings.extend(batch_results)
    if len(rankings) != len(surviving) or {
        result.pair_id for result in rankings
    } != {pair.pair_id for pair in surviving}:
        raise AtlasScanError("LLM pair ranking did not cover every surviving pair exactly once.")

    ranking_record = _ranking_backend_record(ranking_id, ranker)
    ranking_report = LLMPairRankingReport(
        run_id=run_id,
        ranking_id=ranking_id,
        source_compatibility_filter_path=f"runs/{run_id}/reports/{filter_id}.json",
        backend_name=ranker.backend_name,
        model=ranker.model,
        batch_count=len(prompts),
        surviving_pair_count=len(surviving),
        ranked_pair_count=len(rankings),
        prompts=prompts,
        results=rankings,
        backend_records=[ranking_record],
        publication_ready=False,
    )
    selected_pairs, selected_rankings, duplicate_count = select_diverse_ranked_pairs(
        pairs=surviving,
        rankings=rankings,
        top_pairs=top_pairs,
        minimum_domain_families=minimum_domain_families,
        minimum_method_families=minimum_method_families,
        max_pairs_per_domain_family=max_pairs_per_domain_family,
        max_pairs_per_method_family=max_pairs_per_method_family,
    )
    selector_record = _selector_backend_record(scan_id)
    atlas_record = (
        atlas.backend_records[0] if atlas.backend_records else _atlas_backend_record(atlas.scan_id)
    )
    policy_report = evaluate_production_mode(
        run_id=run_id,
        records=[
            atlas_record,
            *compatibility.backend_records,
            ranking_record,
            selector_record,
        ],
        policy=ProductionModePolicy(require_non_fake_backends=require_non_fake_backends),
        expected_stage_kinds=[
            ScientificStageKind.ATLAS_CONSTRUCTION,
            ScientificStageKind.COMPATIBILITY_FILTER,
            ScientificStageKind.PAIR_RANKING,
            ScientificStageKind.DIVERSITY_SELECTION,
        ],
        report_id=f"{scan_id}-production-evaluation",
    )
    if require_non_fake_backends and policy_report.blocking_violation_count:
        messages = "; ".join(item.message for item in policy_report.violations)
        raise AtlasScanError(f"Strict production-mode atlas scan blocked: {messages}")
    warnings: list[str] = []
    if duplicate_count:
        warnings.append(f"Suppressed {duplicate_count} duplicate pair rankings.")
    domain_coverage = len({pair.domain_family for pair in selected_pairs})
    method_coverage = len({pair.method_family for pair in selected_pairs})
    if domain_coverage < min(minimum_domain_families, len({p.domain_family for p in surviving})):
        warnings.append("Selected pairs did not reach requested domain-family coverage.")
    if method_coverage < min(minimum_method_families, len({p.method_family for p in surviving})):
        warnings.append("Selected pairs did not reach requested method-family coverage.")
    report = AtlasScanReport(
        run_id=run_id,
        scan_id=scan_id,
        scan_status="completed_with_warnings" if warnings else "completed",
        source_atlas_path_optional=_relative(root_path, atlas_path),
        domain_count=atlas.domain_count,
        method_count=atlas.method_count,
        raw_pair_count=compatibility.raw_pair_count,
        excluded_pair_count=compatibility.excluded_pair_count,
        surviving_pair_count=compatibility.surviving_pair_count,
        llm_ranked_pair_count=ranking_report.ranked_pair_count,
        selected_pair_count=len(selected_pairs),
        domain_family_coverage=domain_coverage,
        method_family_coverage=method_coverage,
        compatibility_filter_path_optional=f"runs/{run_id}/reports/{filter_id}.json",
        llm_pair_ranking_path_optional=f"runs/{run_id}/reports/{ranking_id}.json",
        selected_pairs=selected_pairs,
        selected_rankings=selected_rankings,
        backend_records=[
            *compatibility.backend_records,
            ranking_record,
            selector_record,
        ],
        warnings=warnings,
        production_ready=(require_non_fake_backends and not policy_report.blocking_violation_count),
        publication_ready=False,
    )
    metadata = _metadata("atlas_pair_scan")
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(filter_id, ArtifactType.REPORT, compatibility, "json", metadata),
            ArtifactWriteSpec(ranking_id, ArtifactType.REPORT, ranking_report, "json", metadata),
            ArtifactWriteSpec(scan_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{scan_id}-markdown",
                ArtifactType.REPORT,
                render_atlas_scan_markdown(report),
                "markdown",
                metadata,
                filename_stem=scan_id,
            ),
        ],
        action_type=ControllerActionType.ATLAS_SCAN_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "scan_id": scan_id,
            "raw_pair_count": report.raw_pair_count,
            "excluded_pair_count": report.excluded_pair_count,
            "selected_pair_count": report.selected_pair_count,
            "production_ready": report.production_ready,
            "publication_ready": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return AtlasPairScanResult(
        run_id=run_id,
        report=report,
        compatibility_report=compatibility,
        ranking_report=ranking_report,
        persistence=persistence,
        report_artifact=by_id[scan_id],
    )


def select_diverse_ranked_pairs(
    *,
    pairs: list[DomainMethodPair],
    rankings: list[LLMPairRankingResult],
    top_pairs: int,
    minimum_domain_families: int = 8,
    minimum_method_families: int = 8,
    max_pairs_per_domain_family: int = 5,
    max_pairs_per_method_family: int = 5,
    false_bridge_risk_cap: float = 0.65,
) -> tuple[list[DomainMethodPair], list[LLMPairRankingResult], int]:
    """Select LLM-ranked pairs with coverage, risk caps, and duplicate suppression."""
    pair_by_id = {pair.pair_id: pair for pair in pairs}
    ordered = sorted(rankings, key=lambda item: (-item.rank_score, item.pair_id))
    unique: list[LLMPairRankingResult] = []
    seen: set[str] = set()
    duplicate_count = 0
    for ranking in ordered:
        pair = pair_by_id.get(ranking.pair_id)
        if pair is None:
            continue
        fingerprint = _pair_fingerprint(pair)
        if fingerprint in seen:
            duplicate_count += 1
            continue
        seen.add(fingerprint)
        if (
            ranking.recommended_for_deep_discovery
            and ranking.false_bridge_risk <= false_bridge_risk_cap
        ):
            unique.append(ranking)

    selected: list[LLMPairRankingResult] = []
    domain_counts: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()

    def can_add(ranking: LLMPairRankingResult) -> bool:
        pair = pair_by_id[ranking.pair_id]
        return (
            ranking not in selected
            and domain_counts[pair.domain_family] < max_pairs_per_domain_family
            and method_counts[pair.method_family] < max_pairs_per_method_family
        )

    def add(ranking: LLMPairRankingResult) -> None:
        pair = pair_by_id[ranking.pair_id]
        selected.append(ranking)
        domain_counts[pair.domain_family] += 1
        method_counts[pair.method_family] += 1

    for ranking in unique:
        if len(domain_counts) >= minimum_domain_families or len(selected) >= top_pairs:
            break
        pair = pair_by_id[ranking.pair_id]
        if pair.domain_family not in domain_counts and can_add(ranking):
            add(ranking)
    for ranking in unique:
        if len(method_counts) >= minimum_method_families or len(selected) >= top_pairs:
            break
        pair = pair_by_id[ranking.pair_id]
        if pair.method_family not in method_counts and can_add(ranking):
            add(ranking)
    for ranking in unique:
        if len(selected) >= top_pairs:
            break
        if can_add(ranking):
            add(ranking)
    selected_pairs = [pair_by_id[item.pair_id] for item in selected]
    return selected_pairs, selected, duplicate_count


def inspect_atlas_scan(*, run_id: str, root: str | Path = ".") -> AtlasScanInspectionReport:
    """Inspect the latest ranked scan, falling back to the latest atlas build."""
    reports = Path(root) / "runs" / run_id / "reports"
    path = _latest_matching(reports, _SCAN_RE) or _latest_matching(reports, _ATLAS_RE)
    if path is None:
        return AtlasScanInspectionReport(run_id=run_id, atlas_scan_present=False)
    try:
        report = AtlasScanReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise AtlasScanError(f"Could not inspect atlas scan: {exc}") from exc
    return AtlasScanInspectionReport(
        run_id=run_id,
        atlas_scan_present=True,
        latest_scan_id_optional=report.scan_id,
        latest_scan_status_optional=report.scan_status,
        domain_count=report.domain_count,
        method_count=report.method_count,
        raw_pair_count=report.raw_pair_count,
        excluded_pair_count=report.excluded_pair_count,
        surviving_pair_count=report.surviving_pair_count,
        llm_ranked_pair_count=report.llm_ranked_pair_count,
        selected_pair_count=report.selected_pair_count,
        domain_family_coverage=report.domain_family_coverage,
        method_family_coverage=report.method_family_coverage,
        selected_pairs=report.selected_pairs,
        selected_rankings=report.selected_rankings,
        report_optional=report,
        warnings=report.warnings,
        production_ready=report.production_ready,
        publication_ready=False,
    )


def render_atlas_scan_text(report: AtlasScanInspectionReport) -> str:
    lines = [
        f"Atlas scan: {'present' if report.atlas_scan_present else 'absent'}",
        f"Domains/methods: {report.domain_count}/{report.method_count}",
        "Raw/excluded/surviving pairs: "
        f"{report.raw_pair_count}/{report.excluded_pair_count}/"
        f"{report.surviving_pair_count}",
        f"LLM ranked/selected: {report.llm_ranked_pair_count}/{report.selected_pair_count}",
        f"Domain-family coverage: {report.domain_family_coverage}",
        f"Method-family coverage: {report.method_family_coverage}",
        "Selected pairs:",
    ]
    rankings = {item.pair_id: item for item in report.selected_rankings}
    lines.extend(
        f"- {pair.domain_id} x {pair.method_id}: {rankings[pair.pair_id].rank_score:.3f}"
        for pair in report.selected_pairs
        if pair.pair_id in rankings
    )
    lines.extend(
        [
            f"Production ready: {str(report.production_ready).lower()}",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def render_atlas_scan_markdown(report: AtlasScanReport) -> str:
    lines = [
        "# Domain/Method Atlas Scan",
        "",
        f"Status: `{report.scan_status}`",
        f"Domains: `{report.domain_count}`",
        f"Methods: `{report.method_count}`",
        f"Raw pairs: `{report.raw_pair_count}`",
        f"Excluded pairs: `{report.excluded_pair_count}`",
        f"Surviving pairs: `{report.surviving_pair_count}`",
        f"LLM-ranked pairs: `{report.llm_ranked_pair_count}`",
        f"Selected pairs: `{report.selected_pair_count}`",
        "",
        "## Selected Pairs",
        "",
    ]
    rankings = {item.pair_id: item for item in report.selected_rankings}
    lines.extend(
        f"- **{pair.domain_id} x {pair.method_id}**: {rankings[pair.pair_id].rank_score:.3f}"
        for pair in report.selected_pairs
        if pair.pair_id in rankings
    )
    lines.extend(
        [
            "",
            "Novelty and underuse statements are LLM hypotheses only; no literature retrieval "
            "was performed.",
            "",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def _domain_entry(seed: _DomainSeed) -> DomainAtlasEntry:
    domain_id = _slug(seed.name)
    return DomainAtlasEntry(
        domain_id=domain_id,
        name=seed.name,
        domain_family=seed.family,
        description=f"Curated research basin for {seed.name}.",
        canonical_objects=list(seed.objects),
        data_types=[*seed.objects, "synthetic_data", "public_data"],
        natural_baselines=[f"standard_{domain_id}_baseline", "null_model"],
        verification_modes=["synthetic_experiment", "benchmark", "simulation"],
        standard_metrics=["held_out_error", "stability", "calibration"],
        common_failure_modes=["nonstationarity", "misspecification", "weak_identification"],
        example_questions=[
            f"How stable is a declared {seed.objects[0]} mechanism under perturbation?"
        ],
    )


def _method_entry(seed: _MethodSeed) -> MethodAtlasEntry:
    method_id = _slug(seed.name)
    verification = ["synthetic_experiment", "benchmark", "simulation"]
    if seed.family in {"probability", "control_optimization", "dynamics"}:
        verification.append("proof")
    return MethodAtlasEntry(
        method_id=method_id,
        name=seed.name,
        method_family=seed.family,
        description=f"Curated method lens based on {seed.name}.",
        canonical_objects=list(seed.objects),
        natural_problem_types=["estimation", "comparison", "robustness"],
        required_inputs=[*seed.objects, "synthetic_data"],
        typical_outputs=["model", "diagnostic", "bounded_comparison"],
        verification_modes=verification,
        natural_baselines=["null_model", f"standard_{method_id}_baseline"],
        false_bridge_patterns=[
            f"using {seed.name} terminology without mapping a canonical object",
            "claiming novelty without retrieval evidence",
        ],
    )


def _ranking_payload(
    *, pair: DomainMethodPair, domain: DomainAtlasEntry, method: MethodAtlasEntry
) -> dict[str, Any]:
    return {
        "pair_id": pair.pair_id,
        "domain": domain.model_dump(mode="json"),
        "method": method.model_dump(mode="json"),
        "compatibility": pair.model_dump(mode="json"),
        "evidence_boundary": (
            "No literature retrieval has been performed. Novelty and underuse must "
            "remain hypotheses."
        ),
    }


def _atlas_backend_record(stage_id: str) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=stage_id,
        stage_kind=ScientificStageKind.ATLAS_CONSTRUCTION,
        backend_kind=BackendKind.CURATED_CATALOG,
        backend_name="curated_domain_method_catalog",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=False,
        allowed_in_production=True,
        reason="Curated search metadata is infrastructure and makes no opportunity assertion.",
        artifact_ids=[stage_id],
    )


def _compatibility_backend_record(stage_id: str) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=stage_id,
        stage_kind=ScientificStageKind.COMPATIBILITY_FILTER,
        backend_kind=BackendKind.HEURISTIC,
        backend_name="deterministic_exclusion_only_filter",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=False,
        allowed_in_production=True,
        reason="Deterministic exclusion only; no positive opportunity or novelty judgment.",
        artifact_ids=[stage_id],
    )


def _ranking_backend_record(stage_id: str, ranker: PairRankingClient) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=stage_id,
        stage_kind=ScientificStageKind.PAIR_RANKING,
        backend_kind=ranker.backend_kind,
        backend_name=ranker.backend_name,
        is_scientific_generation=False,
        is_scientific_judgment=True,
        is_execution_or_verification=False,
        reason="Scientific promise ranking is supplied by the recorded non-fake LLM backend.",
        artifact_ids=[stage_id],
        fallback_used=ranker.fallback_used,
        fallback_disclosed=ranker.fallback_disclosed,
    )


def _selector_backend_record(stage_id: str) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{stage_id}-diversity-selection",
        stage_kind=ScientificStageKind.DIVERSITY_SELECTION,
        backend_kind=BackendKind.HEURISTIC,
        backend_name="coverage_constrained_selector",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=False,
        allowed_in_production=True,
        reason="Selection preserves LLM ranking while enforcing coverage, caps, and deduplication.",
        artifact_ids=[stage_id],
    )


def _load_latest_atlas(*, run_id: str, reports: Path) -> tuple[Path, AtlasScanReport]:
    path = _latest_matching(reports, _ATLAS_RE)
    if path is None:
        raise AtlasScanError(
            f"No domain/method atlas found for run_id={run_id}; "
            "run build-domain-method-atlas first."
        )
    try:
        report = AtlasScanReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise AtlasScanError(f"Could not load domain/method atlas: {exc}") from exc
    if report.run_id != run_id or report.scan_status != "atlas_built":
        raise AtlasScanError("Latest domain/method atlas is inconsistent with the requested run.")
    return path, report


def _pair_fingerprint(pair: DomainMethodPair) -> str:
    return f"{_slug(pair.domain_id)}::{_slug(pair.method_id)}"


def _slug(value: str) -> str:
    return "_".join(_TOKEN_RE.findall(value.lower()))


def _normalize_tag(value: str) -> str:
    return _slug(value)


def _metadata(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "artifact_role": "atlas_search_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }


def _latest_matching(directory: Path, pattern: re.Pattern[str]) -> Path | None:
    if not directory.is_dir():
        return None
    matches = [path for path in directory.iterdir() if pattern.match(path.name)]
    return max(matches, key=lambda path: path.name) if matches else None


def _next_number(directory: Path, pattern: re.Pattern[str]) -> int:
    if not directory.is_dir():
        return 1
    numbers = [
        int(match.group(1))
        for path in directory.iterdir()
        if (match := pattern.match(path.name)) is not None
    ]
    return max(numbers, default=0) + 1


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


__all__ = [
    "AtlasBuildResult",
    "AtlasPairScanResult",
    "AtlasScanError",
    "build_compatibility_filter_report",
    "build_domain_method_atlas",
    "domain_atlas",
    "evaluate_pair_compatibility",
    "inspect_atlas_scan",
    "method_atlas",
    "render_atlas_scan_markdown",
    "render_atlas_scan_text",
    "scan_domain_method_pairs",
    "select_diverse_ranked_pairs",
]
