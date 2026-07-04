"""Deterministic opportunity-seeded variance augmentation."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from factori.artifacts import ArtifactStore
from factori.commands import ensure_run_initialized
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    OpportunityCandidate,
    OpportunityDiscoveryReport,
    OpportunitySeedConstraint,
    VarianceAugmentationBatch,
    VarianceAugmentationConfig,
    VarianceAugmentationInspectionReport,
    VarianceAugmentationReport,
    VarianceAugmentedCandidate,
    VarianceDiversityDiagnostic,
)

_AUGMENTATION_RE = re.compile(r"^variance-augmentation-(\d{4})\.json$")
_APPLICATION_RE = re.compile(r"^variance-augmentation-application-(\d{4})\.json$")
_OPPORTUNITY_RE = re.compile(r"^opportunity-discovery-(\d{4})\.json$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_FAMILY_ORDER = (
    "mechanism_variant",
    "robustness_variant",
    "counterexample_variant",
    "benchmark_variant",
    "representation_variant",
)

_FAMILY_DIFFICULTY = {
    "mechanism_variant": 0.58,
    "robustness_variant": 0.54,
    "counterexample_variant": 0.45,
    "benchmark_variant": 0.40,
    "representation_variant": 0.62,
}

_FAMILY_INTEREST = {
    "mechanism_variant": 0.82,
    "robustness_variant": 0.78,
    "counterexample_variant": 0.74,
    "benchmark_variant": 0.70,
    "representation_variant": 0.80,
}

# title, family, concrete object, baseline, scientific focus
_HUMAN_GEOGRAPHY_BRANCHES: dict[str, list[tuple[str, str, str, str, str]]] = {
    "optimal_transport": [
        (
            "Optimal-Transport Barycenters for Regional Mobility Profiles",
            "mechanism_variant",
            "Wasserstein barycenter of regional origin-destination profiles",
            "Euclidean profile mean",
            "regional mobility-profile geometry",
        ),
        (
            "Wasserstein Robustness of Spatial Accessibility Rankings",
            "robustness_variant",
            "transport perturbation ball around accessibility distributions",
            "rank stability under Euclidean perturbations",
            "accessibility-rank sensitivity",
        ),
        (
            "When Euclidean Distance Misorders Spatial Accessibility",
            "counterexample_variant",
            "paired accessibility distributions with equal Euclidean distance",
            "Euclidean accessibility ordering",
            "a constructed accessibility-ordering failure",
        ),
        (
            "Transport-Cost Sensitivity of Commuting-Flow Clusters",
            "benchmark_variant",
            "commuting clusters induced by alternative transport costs",
            "geographic-distance clustering",
            "commuting-flow cluster stability",
        ),
    ],
    "matrix_factorization": [
        (
            "Low-Rank Residual Structure in Regional OD Flows",
            "mechanism_variant",
            "rank-k factorization of gravity-model residuals",
            "pooled gravity residuals",
            "latent regional flow structure",
        ),
        (
            "Nonnegative Factors for Regional Mobility Profiles",
            "representation_variant",
            "nonnegative origin and destination mobility factors",
            "unconstrained low-rank factors",
            "interpretable regional mobility profiles",
        ),
        (
            "Rank Stability Under Boundary Perturbation",
            "robustness_variant",
            "low-rank OD representation across perturbed partitions",
            "fixed-boundary factorization",
            "boundary-sensitive latent rank",
        ),
        (
            "Gravity Baseline Versus Low-Rank Residual Correction",
            "benchmark_variant",
            "gravity prediction plus rank-k residual correction",
            "pooled distance-decay gravity model",
            "held-out OD reconstruction",
        ),
    ],
    "graph_curvature": [
        (
            "Mobility-Network Curvature as a Bounded Segregation Proxy",
            "mechanism_variant",
            "edge curvature on a weighted mobility network",
            "degree-based segregation proxy",
            "mobility bottlenecks and segregation",
        ),
        (
            "Curvature Robustness Under Mobility Edge Noise",
            "robustness_variant",
            "curvature distribution under deterministic edge perturbations",
            "degree-centrality stability",
            "network-geometric noise sensitivity",
        ),
        (
            "A Bottleneck Network Missed by Degree Centrality",
            "counterexample_variant",
            "matched-degree networks with different bridge geometry",
            "degree centrality",
            "a constructed mobility bottleneck",
        ),
        (
            "Curvature Versus Shortest Paths for Regional Bottlenecks",
            "benchmark_variant",
            "curvature-ranked mobility bottlenecks",
            "shortest-path betweenness",
            "synthetic bottleneck detection",
        ),
    ],
    "topological_data_analysis": [
        (
            "Persistent Homology of Spatial Accessibility Level Sets",
            "mechanism_variant",
            "persistence diagram of accessibility filtrations",
            "connected-component count",
            "multi-scale accessibility structure",
        ),
        (
            "Topological Mobility Stability Under Boundary Perturbation",
            "robustness_variant",
            "bottleneck distance between partition-specific persistence diagrams",
            "cluster-count stability",
            "boundary-sensitive topology",
        ),
        (
            "Topological Axes of Regional Accessibility",
            "representation_variant",
            "persistence-image representation of accessibility level sets",
            "raw accessibility quantiles",
            "compact topological representation",
        ),
        (
            "Persistent Accessibility Versus Graph Clustering",
            "benchmark_variant",
            "persistent components across accessibility thresholds",
            "single-resolution graph clustering",
            "recovery of synthetic accessibility basins",
        ),
    ],
    "agent_based_modeling": [
        (
            "Synthetic Agents with Heterogeneous Distance Sensitivity",
            "mechanism_variant",
            "agent choice rule with origin-specific distance sensitivity",
            "homogeneous-distance agent rule",
            "emergent commuting flows",
        ),
        (
            "Aggregate Flow Laws Under Preference Heterogeneity",
            "robustness_variant",
            "agent mobility law across preference mixtures",
            "single-preference population",
            "aggregate-flow stability",
        ),
        (
            "Local Accessibility Rules That Break a Gravity Approximation",
            "counterexample_variant",
            "thresholded agent destination-choice process",
            "pooled gravity approximation",
            "non-gravity emergent OD flows",
        ),
        (
            "Emergent OD Flows Versus a Distance-Decay Baseline",
            "benchmark_variant",
            "simulated agent OD matrix",
            "pooled distance-decay gravity model",
            "held-out synthetic flow reconstruction",
        ),
    ],
    "spatial_statistics": [
        (
            "Spatial Autocorrelation in Gravity-Model Residuals",
            "mechanism_variant",
            "spatially weighted residual autocorrelation statistic",
            "independent residual null",
            "regional dependence after distance decay",
        ),
        (
            "Boundary Robustness of Regional Residual Clusters",
            "robustness_variant",
            "residual clusters across alternative regional partitions",
            "fixed-boundary residual clusters",
            "boundary-sensitive residual dependence",
        ),
        (
            "A Moran-Style Diagnostic for Misspecified Distance Decay",
            "counterexample_variant",
            "synthetic residual field induced by heterogeneous decay",
            "independent-noise residual diagnostic",
            "detectable distance-decay misspecification",
        ),
        (
            "Spatial Residual Diagnostics Versus Pooled Error Summaries",
            "benchmark_variant",
            "Moran-style and variogram residual diagnostics",
            "global RMSE only",
            "synthetic misspecification detection",
        ),
    ],
    "network_science": [
        (
            "Community Structure in Regional OD-Flow Networks",
            "mechanism_variant",
            "weighted mobility-community partition",
            "geographic regional partition",
            "functional mobility regions",
        ),
        (
            "Mobility-Community Robustness Under Regional Aggregation",
            "robustness_variant",
            "community assignments across aggregation scales",
            "single-scale community partition",
            "aggregation-sensitive communities",
        ),
        (
            "A Flow Network with Stable Communities but Weak Gravity Residual Clusters",
            "counterexample_variant",
            "block-structured synthetic OD network",
            "gravity residual clustering",
            "a constructed community/reconstruction mismatch",
        ),
        (
            "Mobility Communities Versus Gravity Residual Clusters",
            "benchmark_variant",
            "flow-network community recovery",
            "gravity-residual clustering",
            "recovery of planted regional groups",
        ),
    ],
    "kernel_methods": [
        (
            "Kernelized Spatial Interaction Under Regional Heterogeneity",
            "mechanism_variant",
            "positive-definite spatial affinity kernel",
            "monotone distance-decay gravity model",
            "nonlinear regional interaction",
        ),
        (
            "Nonmonotone Spatial Affinity Kernels",
            "representation_variant",
            "multi-scale radial and regional affinity kernel",
            "single-scale radial kernel",
            "nonmonotone spatial affinity",
        ),
        (
            "Kernel Stability Under Boundary and Distance Perturbations",
            "robustness_variant",
            "kernel Gram matrix under spatial perturbations",
            "unperturbed kernel fit",
            "kernel sensitivity",
        ),
        (
            "Kernel Interaction Versus Monotone Distance Decay",
            "benchmark_variant",
            "kernelized OD-flow predictor",
            "pooled distance-decay gravity model",
            "held-out synthetic OD reconstruction",
        ),
    ],
}


class VarianceAugmentationError(RuntimeError):
    """Raised when variance augmentation cannot be built or applied."""


@dataclass(frozen=True)
class VarianceAugmentationResult:
    """Persisted variance augmentation result."""

    run_id: str
    report: VarianceAugmentationReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


def augment_variance(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    candidates_per_seed: int = 4,
    max_total_candidates: int = 40,
) -> VarianceAugmentationResult:
    """Generate and persist deterministic candidates from promoted opportunity seeds."""
    root_path = Path(root)
    ensure_run_initialized(root=root_path, run_id=run_id, store=store, ledger=ledger)
    opportunity_path, opportunity_report = _load_latest_opportunity_report(root_path, run_id)
    reports_path = root_path / "runs" / run_id / "reports"
    number = _next_number(reports_path, _AUGMENTATION_RE)
    config = VarianceAugmentationConfig(
        candidates_per_seed=candidates_per_seed,
        max_total_candidates=max_total_candidates,
    )
    report = build_variance_augmentation_report(
        run_id=run_id,
        opportunity_report=opportunity_report,
        source_opportunity_path=_relative(root_path, opportunity_path),
        augmentation_id=f"variance-augmentation-{number:04d}",
        config=config,
    )
    return _persist_report(
        report=report,
        store=store,
        ledger=ledger,
        action_type=ControllerActionType.VARIANCE_AUGMENTATION_WRITTEN,
        filename_stem=report.augmentation_id,
    )


def build_variance_augmentation_report(
    *,
    run_id: str,
    opportunity_report: OpportunityDiscoveryReport,
    source_opportunity_path: str,
    augmentation_id: str,
    config: VarianceAugmentationConfig,
) -> VarianceAugmentationReport:
    """Build a deterministic, coverage-preserving variance report."""
    opportunity_by_id = {
        opportunity.opportunity_id: opportunity
        for opportunity in opportunity_report.opportunities
        if opportunity.score_breakdown.promoted
    }
    seeds = [
        seed
        for seed in opportunity_report.seed_constraints
        if seed.opportunity_id in opportunity_by_id
    ]
    candidates: list[VarianceAugmentedCandidate] = []
    batch_seed_candidates: list[tuple[OpportunitySeedConstraint, list[str]]] = []
    for seed in seeds:
        remaining = config.max_total_candidates - len(candidates)
        if remaining <= 0:
            break
        opportunity = opportunity_by_id[seed.opportunity_id]
        generated = _generate_seed_candidates(
            run_id=run_id,
            seed=seed,
            opportunity=opportunity,
            count=min(config.candidates_per_seed, remaining),
        )
        candidates.extend(generated)
        batch_seed_candidates.append((seed, [candidate.candidate_id for candidate in generated]))

    selected_ids = _select_candidates(
        candidates,
        max_selected=config.max_selected_candidates,
    )
    candidates = [
        candidate.model_copy(
            update={"selected_for_idea_tree": candidate.candidate_id in selected_ids}
        )
        for candidate in candidates
    ]
    candidate_by_id = {candidate.candidate_id: candidate for candidate in candidates}
    batches: list[VarianceAugmentationBatch] = []
    for seed, candidate_ids in batch_seed_candidates:
        batch_candidates = [candidate_by_id[item] for item in candidate_ids]
        batches.append(
            VarianceAugmentationBatch(
                source_seed_id=seed.seed_id,
                source_opportunity_id=seed.opportunity_id,
                source_method_lens_id=seed.method_id,
                method_lens=seed.method_name,
                opportunity_score=seed.opportunity_score,
                candidate_count=len(batch_candidates),
                selected_candidate_count=sum(
                    candidate.selected_for_idea_tree for candidate in batch_candidates
                ),
                candidates=batch_candidates,
            )
        )
    diagnostic = build_variance_diversity_diagnostic(
        candidates=candidates,
        expected_method_lenses=[seed.method_name for seed in seeds],
    )
    warnings: list[str] = []
    if len(batches) < len(seeds):
        warnings.append("The total candidate cap prevented one or more seeds from expanding.")
    if diagnostic.diversity_score == "low":
        warnings.append("Generated branches remain low-diversity and require later mutation.")
    return VarianceAugmentationReport(
        run_id=run_id,
        augmentation_id=augmentation_id,
        source_opportunity_discovery_path=source_opportunity_path,
        domain=opportunity_report.domain,
        config=config,
        seed_count=len(batches),
        candidate_count=len(candidates),
        selected_candidate_count=len(selected_ids),
        method_lens_candidate_counts={
            batch.method_lens: batch.candidate_count for batch in batches
        },
        batches=batches,
        candidates=candidates,
        diversity_diagnostic=diagnostic,
        warnings=warnings,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def apply_variance_augmentation(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> VarianceAugmentationResult:
    """Append an application record consumed by derived IdeaTree reconstruction."""
    root_path = Path(root)
    ensure_run_initialized(root=root_path, run_id=run_id, store=store, ledger=ledger)
    source_path, source = _load_latest_augmentation_report(root_path, run_id)
    reports_path = root_path / "runs" / run_id / "reports"
    if _application_exists(reports_path, source.augmentation_id):
        raise VarianceAugmentationError(
            f"Variance augmentation {source.augmentation_id} was already applied."
        )
    selected = [
        candidate.candidate_id
        for candidate in source.candidates
        if candidate.selected_for_idea_tree
    ]
    number = _next_number(reports_path, _APPLICATION_RE)
    report = source.model_copy(
        update={
            "augmentation_id": f"variance-augmentation-application-{number:04d}",
            "source_augmentation_report_path_optional": _relative(root_path, source_path),
            "applied_to_idea_tree": True,
            "applied_candidate_ids": selected,
            "idea_tree_nodes_added": len(selected),
        }
    )
    return _persist_report(
        report=report,
        store=store,
        ledger=ledger,
        action_type=ControllerActionType.VARIANCE_AUGMENTATION_APPLIED,
        filename_stem=report.augmentation_id,
    )


def inspect_variance_augmentation(
    *, run_id: str, root: str | Path = "."
) -> VarianceAugmentationInspectionReport:
    """Inspect the latest generated or applied variance augmentation report."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    generated_path = _latest_matching(reports, _AUGMENTATION_RE)
    application_path = _latest_matching(reports, _APPLICATION_RE)
    path = generated_path
    if application_path is not None:
        try:
            application = VarianceAugmentationReport.model_validate_json(
                application_path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise VarianceAugmentationError(
                f"Could not load variance application report: {exc}"
            ) from exc
        source_stem = (
            Path(application.source_augmentation_report_path_optional).stem
            if application.source_augmentation_report_path_optional
            else None
        )
        if generated_path is None or source_stem == generated_path.stem:
            path = application_path
    if path is None:
        return VarianceAugmentationInspectionReport(
            run_id=run_id,
            variance_augmentation_present=False,
            warnings=["No variance augmentation report is present."],
            publication_ready=False,
        )
    try:
        report = VarianceAugmentationReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise VarianceAugmentationError(f"Could not load variance report: {exc}") from exc
    return VarianceAugmentationInspectionReport(
        run_id=run_id,
        variance_augmentation_present=True,
        latest_augmentation_id_optional=report.augmentation_id,
        domain_optional=report.domain,
        seed_count=report.seed_count,
        candidate_count=report.candidate_count,
        selected_candidate_count=report.selected_candidate_count,
        method_lens_coverage=report.diversity_diagnostic.method_lens_coverage,
        diversity_score_optional=report.diversity_diagnostic.diversity_score,
        idea_tree_nodes_added=report.idea_tree_nodes_added,
        applied_to_idea_tree=report.applied_to_idea_tree,
        report_optional=report,
        warnings=report.warnings,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def build_variance_diversity_diagnostic(
    *,
    candidates: list[VarianceAugmentedCandidate],
    expected_method_lenses: list[str],
) -> VarianceDiversityDiagnostic:
    """Measure deterministic field duplication and method/family coverage."""
    question_duplicates = _duplicate_count(candidate.research_question for candidate in candidates)
    model_duplicates = _duplicate_count(candidate.theory_object for candidate in candidates)
    baseline_duplicates = _duplicate_count(candidate.baseline for candidate in candidates)
    methods = {candidate.method_lens for candidate in candidates}
    families = {candidate.variant_family for candidate in candidates}
    duplicate_ratio = (question_duplicates + model_duplicates + baseline_duplicates) / max(
        1, 3 * len(candidates)
    )
    coverage_ratio = len(methods) / max(1, len(set(expected_method_lenses)))
    family_ratio = len(families) / len(_FAMILY_ORDER)
    if coverage_ratio >= 0.75 and family_ratio >= 0.60 and duplicate_ratio < 0.15:
        score = "high"
    elif coverage_ratio >= 0.50 and duplicate_ratio < 0.35:
        score = "moderate"
    else:
        score = "low"
    counts = Counter(candidate.method_lens for candidate in candidates)
    underrepresented = sorted(
        method for method in set(expected_method_lenses) if counts[method] < 2
    )
    return VarianceDiversityDiagnostic(
        candidate_count=len(candidates),
        seed_count=len(set(expected_method_lenses)),
        method_lens_coverage=len(methods),
        research_question_duplicate_count=question_duplicates,
        model_object_duplicate_count=model_duplicates,
        baseline_duplicate_count=baseline_duplicates,
        diversity_score=score,
        underrepresented_method_lenses=underrepresented,
        overrepresented_phrases=_overrepresented_title_tokens(candidates),
        selected_candidate_count=sum(candidate.selected_for_idea_tree for candidate in candidates),
    )


def render_variance_augmentation_text(
    report: VarianceAugmentationInspectionReport,
) -> str:
    """Render a concise human-readable augmentation inspection."""
    if not report.variance_augmentation_present or report.report_optional is None:
        return "\n".join(
            [
                "Variance augmentation: absent",
                *[f"Warning: {warning}" for warning in report.warnings],
                "Publication ready: false",
            ]
        )
    payload = report.report_optional
    lines = [
        "Opportunity-Seeded Variance Augmentation",
        f"Domain: {payload.domain}",
        f"Seeds consumed: {payload.seed_count}",
        f"Candidates generated: {payload.candidate_count}",
        f"Candidates selected: {payload.selected_candidate_count}",
        f"Method-lens coverage: {payload.diversity_diagnostic.method_lens_coverage}",
        f"Diversity score: {payload.diversity_diagnostic.diversity_score}",
        f"Applied to IdeaTree: {str(payload.applied_to_idea_tree).lower()}",
        "Candidates by method lens:",
    ]
    lines.extend(
        f"- {method}: {count}"
        for method, count in sorted(payload.method_lens_candidate_counts.items())
    )
    lines.extend(
        [
            "This report is creative-search context only and creates no evidence.",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def render_variance_augmentation_markdown(report: VarianceAugmentationReport) -> str:
    """Render the append-only Markdown companion for a variance report."""
    lines = [
        "# Opportunity-Seeded Variance Augmentation",
        "",
        f"Domain: {report.domain}",
        f"Seeds consumed: {report.seed_count}",
        f"Candidates generated: {report.candidate_count}",
        f"Candidates selected for IdeaTree: {report.selected_candidate_count}",
        f"Diversity score: {report.diversity_diagnostic.diversity_score}",
        f"Applied to IdeaTree: {str(report.applied_to_idea_tree).lower()}",
        "",
    ]
    for batch in report.batches:
        lines.extend([f"## {batch.method_lens}", ""])
        for candidate in batch.candidates:
            marker = "selected" if candidate.selected_for_idea_tree else "retained"
            lines.extend(
                [
                    f"- **{candidate.title}** [{candidate.variant_family}; {marker}]",
                    f"  - Question: {candidate.research_question}",
                    f"  - Theory object: {candidate.theory_object}",
                    f"  - Baseline: {candidate.baseline}",
                ]
            )
        lines.append("")
    lines.extend(
        [
            "This artifact is provenance/context only. It creates no scientific validation, "
            "verification evidence, or publication readiness.",
            "",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def _generate_seed_candidates(
    *,
    run_id: str,
    seed: OpportunitySeedConstraint,
    opportunity: OpportunityCandidate,
    count: int,
) -> list[VarianceAugmentedCandidate]:
    entries = _branch_entries(seed, opportunity)
    generated: list[VarianceAugmentedCandidate] = []
    for index, (title, family, theory, baseline, focus) in enumerate(entries[:count], start=1):
        question = _question(family, seed.method_name, focus, theory)
        hypothesis = _hypothesis(family, seed.method_name, focus, baseline)
        generated.append(
            VarianceAugmentedCandidate(
                candidate_id=(f"variance-{_slug(seed.method_id)}-{index:02d}-{_slug(family)}"),
                run_id=run_id,
                source_seed_id=seed.seed_id,
                source_opportunity_id=seed.opportunity_id,
                source_method_lens_id=seed.method_id,
                domain=seed.domain,
                method_lens=seed.method_name,
                variant_family=family,
                title=title,
                research_question=question,
                hypothesis=hypothesis,
                theory_object=theory,
                model_hint=_model_hint(seed.method_name, theory, focus),
                experiment_or_proof_plan=_verification_plan(family, theory, baseline, focus),
                baseline=baseline,
                failure_mode=_failure_mode(family, seed.method_name, baseline),
                paper_shape=_paper_shape(family),
                verification_path=_verification_path(family),
                data_regime=(
                    "SyntheticOnly"
                    if family != "representation_variant"
                    else "SyntheticOrPublicAggregate"
                ),
                expected_substrate_type=f"{seed.method_id}_{family}",
                novelty_risk=_bounded(0.38 + 0.05 * index),
                difficulty_score=_FAMILY_DIFFICULTY[family],
                scientific_interest_score=_bounded(
                    _FAMILY_INTEREST[family] + 0.08 * seed.opportunity_score
                ),
                easy_win_score=seed.opportunity_score,
                diversity_tags=sorted(
                    {
                        seed.method_id,
                        family,
                        *(_tokens(focus) - {"regional", "spatial", "synthetic"}),
                    }
                ),
                selected_for_idea_tree=False,
                publication_ready=False,
                creates_scientific_validation=False,
                implies_publication_readiness=False,
                is_verification_evidence=False,
            )
        )
    return generated


def _branch_entries(
    seed: OpportunitySeedConstraint,
    opportunity: OpportunityCandidate,
) -> list[tuple[str, str, str, str, str]]:
    if _domain_is_human_geography(seed.domain) and seed.method_id in _HUMAN_GEOGRAPHY_BRANCHES:
        return _HUMAN_GEOGRAPHY_BRANCHES[seed.method_id]
    objects = opportunity.possible_theory_objects or [f"{seed.method_name} model object"]
    baselines = opportunity.possible_baselines or ["descriptive null baseline"]
    primitives = [primitive.name for primitive in opportunity.matched_primitives]
    focus = ", ".join(primitives[:2]) or seed.domain
    labels = {
        "mechanism_variant": "Mechanism Model",
        "robustness_variant": "Robustness Stress Test",
        "counterexample_variant": "Boundary Counterexample",
        "benchmark_variant": "Baseline Benchmark",
        "representation_variant": "Alternative Representation",
    }
    return [
        (
            f"{seed.method_name.title()} {labels[family]} for {seed.domain.title()}",
            family,
            f"{objects[index % len(objects)]} for {focus}",
            baselines[index % len(baselines)],
            focus,
        )
        for index, family in enumerate(_FAMILY_ORDER)
    ]


def _select_candidates(
    candidates: list[VarianceAugmentedCandidate], *, max_selected: int
) -> set[str]:
    by_method: dict[str, list[VarianceAugmentedCandidate]] = {}
    for candidate in candidates:
        by_method.setdefault(candidate.source_method_lens_id, []).append(candidate)
    for values in by_method.values():
        values.sort(key=_candidate_rank)
    selected: list[VarianceAugmentedCandidate] = []
    # Coverage first: one branch from every seed, then a second distinct family.
    for round_index in range(2):
        for method_id in sorted(by_method):
            values = by_method[method_id]
            if round_index < len(values) and len(selected) < max_selected:
                selected.append(values[round_index])
    selected_ids = {candidate.candidate_id for candidate in selected}
    selected_families = {candidate.variant_family for candidate in selected}
    for family in _FAMILY_ORDER:
        if family in selected_families or len(selected_ids) >= max_selected:
            continue
        family_candidate = next(
            (
                candidate
                for candidate in sorted(candidates, key=_candidate_rank)
                if candidate.variant_family == family
                and candidate.candidate_id not in selected_ids
            ),
            None,
        )
        if family_candidate is not None:
            selected_ids.add(family_candidate.candidate_id)
            selected_families.add(family)
    remaining = sorted(
        (candidate for candidate in candidates if candidate.candidate_id not in selected_ids),
        key=_candidate_rank,
    )
    for candidate in remaining:
        if len(selected_ids) >= max_selected:
            break
        selected_ids.add(candidate.candidate_id)
    return selected_ids


def _candidate_rank(candidate: VarianceAugmentedCandidate) -> tuple[float, float, str]:
    return (
        -round(
            0.45 * candidate.scientific_interest_score
            + 0.40 * candidate.easy_win_score
            + 0.15 * (1.0 - candidate.difficulty_score),
            6,
        ),
        candidate.novelty_risk,
        candidate.candidate_id,
    )


def _question(family: str, method: str, focus: str, theory: str) -> str:
    templates = {
        "mechanism_variant": f"How can {theory} explain bounded structure in {focus}?",
        "robustness_variant": (
            f"How stable is the {method} account of {focus} under controlled perturbations?"
        ),
        "counterexample_variant": (
            f"Can a constructed {focus} regime expose a failure of the declared baseline?"
        ),
        "benchmark_variant": (
            f"Does {theory} outperform its declared baseline on a bounded synthetic benchmark?"
        ),
        "representation_variant": (
            f"Does an alternative {method} representation make {focus} more recoverable "
            "or interpretable?"
        ),
    }
    return templates[family]


def _hypothesis(family: str, method: str, focus: str, baseline: str) -> str:
    templates = {
        "mechanism_variant": (
            f"A concrete {method} object captures a bounded synthetic pattern in {focus} "
            f"that {baseline} omits."
        ),
        "robustness_variant": (
            f"The declared {method} pattern remains measurable across bounded perturbations "
            "while uncertainty increases visibly."
        ),
        "counterexample_variant": (
            f"There exists a deterministic synthetic case in which {baseline} fails on "
            f"{focus} without implying general failure."
        ),
        "benchmark_variant": (
            f"The scoped {method} method improves its declared metric over {baseline} on "
            "the configured synthetic benchmark only."
        ),
        "representation_variant": (
            f"The alternative {method} representation recovers a configured latent structure "
            f"more clearly than {baseline} within synthetic scope."
        ),
    }
    return templates[family]


def _model_hint(method: str, theory: str, focus: str) -> str:
    return f"Instantiate {theory} as the concrete {method} object over {focus}."


def _verification_plan(family: str, theory: str, baseline: str, focus: str) -> str:
    if family == "counterexample_variant":
        return (
            f"Construct a deterministic {focus} fixture where {baseline} and {theory} make "
            "distinguishable predictions; retain negative outcomes."
        )
    if family == "robustness_variant":
        return (
            f"Generate fixed-seed {focus} fixtures, perturb the declared boundary/noise "
            f"axis, and compare {theory} with {baseline}."
        )
    if family == "representation_variant":
        return (
            f"Generate known latent {focus} structure, fit {theory}, and compare recovery "
            f"and held-out error with {baseline}."
        )
    return (
        f"Generate a fixed-seed {focus} fixture and compare {theory} with {baseline} "
        "using a declared held-out metric."
    )


def _failure_mode(family: str, method: str, baseline: str) -> str:
    if family == "counterexample_variant":
        return f"No bounded construction separates {method} from {baseline}."
    if family == "robustness_variant":
        return f"The apparent {method} pattern disappears under small declared perturbations."
    if family == "representation_variant":
        return f"The representation is unstable or no more informative than {baseline}."
    return (
        f"The declared metric does not improve over {baseline}, producing a negative or "
        "inconclusive result."
    )


def _paper_shape(family: str) -> str:
    shapes = {
        "mechanism_variant": (
            "Concrete object -> mechanism -> synthetic DGP -> bounded result -> limitations."
        ),
        "robustness_variant": (
            "Base mechanism -> perturbation axis -> stability curve -> failure boundary."
        ),
        "counterexample_variant": (
            "Baseline claim -> minimal construction -> counterexample -> scope boundary."
        ),
        "benchmark_variant": (
            "Model -> baseline -> fixed benchmark -> metric comparison -> limitations."
        ),
        "representation_variant": (
            "Representation -> latent DGP -> recovery metric -> baseline -> interpretation limits."
        ),
    }
    return shapes[family]


def _verification_path(family: str) -> str:
    return {
        "mechanism_variant": "bounded synthetic mechanism experiment",
        "robustness_variant": "fixed-seed perturbation stress test",
        "counterexample_variant": "deterministic counterexample construction",
        "benchmark_variant": "held-out synthetic benchmark",
        "representation_variant": "latent-structure recovery experiment",
    }[family]


def _persist_report(
    *,
    report: VarianceAugmentationReport,
    store: ArtifactStore,
    ledger: ResearchLedger,
    action_type: ControllerActionType,
    filename_stem: str,
) -> VarianceAugmentationResult:
    metadata = {
        "stage": "variance_augmentation",
        "artifact_role": "creative_search_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }
    persistence = persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                filename_stem,
                ArtifactType.REPORT,
                report,
                "json",
                metadata,
            ),
            ArtifactWriteSpec(
                f"{filename_stem}-markdown",
                ArtifactType.REPORT,
                render_variance_augmentation_markdown(report),
                "markdown",
                metadata,
                filename_stem=filename_stem,
            ),
        ],
        action_type=action_type,
        commit_payload={
            "run_id": report.run_id,
            "augmentation_id": report.augmentation_id,
            "seed_count": report.seed_count,
            "candidate_count": report.candidate_count,
            "selected_candidate_count": report.selected_candidate_count,
            "idea_tree_nodes_added": report.idea_tree_nodes_added,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return VarianceAugmentationResult(
        run_id=report.run_id,
        report=report,
        persistence=persistence,
        report_artifact=by_id[filename_stem],
        markdown_artifact=by_id[f"{filename_stem}-markdown"],
    )


def _load_latest_opportunity_report(
    root: Path, run_id: str
) -> tuple[Path, OpportunityDiscoveryReport]:
    reports = root / "runs" / run_id / "reports"
    path = _latest_matching(reports, _OPPORTUNITY_RE)
    if path is None:
        raise VarianceAugmentationError(
            "No opportunity discovery report found. Run discover-opportunities first."
        )
    try:
        return path, OpportunityDiscoveryReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise VarianceAugmentationError(
            f"Could not load opportunity discovery report: {exc}"
        ) from exc


def _load_latest_augmentation_report(
    root: Path, run_id: str
) -> tuple[Path, VarianceAugmentationReport]:
    reports = root / "runs" / run_id / "reports"
    path = _latest_matching(reports, _AUGMENTATION_RE)
    if path is None:
        raise VarianceAugmentationError(
            "No variance augmentation report found. Run augment-variance first."
        )
    try:
        return path, VarianceAugmentationReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise VarianceAugmentationError(f"Could not load variance report: {exc}") from exc


def _application_exists(reports: Path, source_augmentation_id: str) -> bool:
    for path in reports.glob("variance-augmentation-application-*.json"):
        try:
            report = VarianceAugmentationReport.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError):
            continue
        if report.source_augmentation_report_path_optional and (
            Path(report.source_augmentation_report_path_optional).stem == source_augmentation_id
        ):
            return True
    return False


def _latest_matching(reports: Path, pattern: re.Pattern[str]) -> Path | None:
    matches = sorted(
        (int(match.group(1)), path)
        for path in reports.glob("*.json")
        if (match := pattern.fullmatch(path.name))
    )
    return matches[-1][1] if matches else None


def _next_number(reports: Path, pattern: re.Pattern[str]) -> int:
    latest = _latest_matching(reports, pattern)
    if latest is None:
        return 1
    match = pattern.fullmatch(latest.name)
    return int(match.group(1)) + 1 if match else 1


def _duplicate_count(values: Iterable[str]) -> int:
    normalized = [_normalize(value) for value in values]
    return sum(count - 1 for count in Counter(normalized).values() if count > 1)


def _overrepresented_title_tokens(
    candidates: list[VarianceAugmentedCandidate],
) -> list[str]:
    stop = {"a", "an", "and", "for", "in", "of", "the", "to", "under", "versus"}
    counts = Counter(
        token for candidate in candidates for token in _tokens(candidate.title) if token not in stop
    )
    threshold = max(3, len(candidates) // 3)
    return [token for token, count in counts.most_common(8) if count >= threshold]


def _normalize(value: str) -> str:
    return " ".join(sorted(_tokens(value)))


def _tokens(value: str) -> set[str]:
    return set(_TOKEN_RE.findall(value.lower()))


def _slug(value: str) -> str:
    return "-".join(_TOKEN_RE.findall(value.lower()))


def _domain_is_human_geography(domain: str) -> bool:
    lowered = domain.lower()
    return "human geography" in lowered or "spatial heterogeneity" in lowered


def _bounded(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 6)


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


__all__ = [
    "VarianceAugmentationError",
    "VarianceAugmentationResult",
    "apply_variance_augmentation",
    "augment_variance",
    "build_variance_augmentation_report",
    "build_variance_diversity_diagnostic",
    "inspect_variance_augmentation",
    "render_variance_augmentation_markdown",
    "render_variance_augmentation_text",
]
