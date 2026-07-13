"""Non-fake LLM ScientificSubstrate construction and compatibility persistence."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factori.adapters.errors import AdapterError
from factori.adapters.llm_substrate import SubstrateGenerationClient
from factori.artifacts import ArtifactStore
from factori.idea_tree import IdeaTreeError, inspect_idea_tree
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
    DeepOpportunityDiscoveryReport,
    DomainMethodPair,
    IdeaTreeConstructionReport,
    LLMScientificSubstrateCandidate,
    LLMSubstrateConstructionConfig,
    LLMSubstrateConstructionInspectionReport,
    LLMSubstrateConstructionReport,
    LLMSubstrateConstructionScore,
    LLMSubstrateRawArtifact,
    LLMVarianceCandidate,
    LLMVarianceGenerationReport,
    ProductionModePolicy,
    RetrievalContext,
    ScientificStageKind,
    ScientificSubstrate,
    ScientificSubstrateBuildReport,
    StageBackendRecord,
)

_VARIANCE_RE = re.compile(r"^llm-variance-generation-report-(\d{4})\.json$")
_TREE_RE = re.compile(r"^idea-tree-construction-report-(\d{4})\.json$")
_DEEP_RE = re.compile(r"^deep-opportunity-discovery-report-(\d{4})\.json$")
_ATLAS_SCAN_RE = re.compile(r"^atlas-scan-(\d{4})\.json$")
_REPORT_RE = re.compile(r"^llm-substrate-construction-report-(\d{4})\.json$")
_RAW_RE = re.compile(r"^llm-substrate-raw-(\d{4})\.json$")
_BUILD_RE = re.compile(r"^scientific-substrate-build-(\d{4})\.json$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Method IDs are catalog identifiers, not necessarily words that belong in an
# equation.  These anchors keep the bridge check strict while accepting the
# standard object vocabulary for a method family.
_METHOD_OBJECT_ANCHORS: dict[str, tuple[str, ...]] = {
    "causal_inference": (
        "causal",
        "treatment",
        "counterfactual",
        "potential outcome",
        "propensity",
        "confounding",
        "interference",
        "spillover",
        "doubly robust",
        "policy effect",
        "identified set",
    ),
    "optimal_transport": (
        "transport",
        "wasserstein",
        "coupling",
        "barycenter",
        "sinkhorn",
        "earth mover",
    ),
    "wasserstein_robustness": (
        "wasserstein",
        "transport cost",
        "ambiguity set",
        "distributionally robust",
        "perturbation budget",
    ),
    "matrix_factorization": (
        "matrix",
        "factor",
        "low-rank",
        "low rank",
        "singular value",
        "svd",
        "rank-k",
    ),
    "graph_curvature": ("curvature", "graph", "edge", "bottleneck"),
    "topological_data_analysis": (
        "topolog",
        "persistent",
        "homology",
        "filtration",
        "simplicial",
    ),
    "agent_based_modeling": (
        "agent",
        "individual",
        "agent-based",
        "emergent",
        "micro-rule",
    ),
    "spatial_statistics": (
        "spatial",
        "autocorrelation",
        "moran",
        "variogram",
        "point pattern",
    ),
    "network_science": ("network", "graph", "community", "centrality", "edge"),
    "kernel_methods": ("kernel", "rkhs", "similarity", "feature map"),
    "spectral_graph_theory": ("spectral", "eigenvalue", "graph laplacian", "graph"),
    "pde_diffusion_models": ("pde", "diffusion", "partial differential", "laplacian"),
}


class LLMSubstrateError(RuntimeError):
    """Raised when production-safe LLM substrate construction cannot proceed."""


@dataclass(frozen=True)
class LLMSubstrateResult:
    run_id: str
    report: LLMSubstrateConstructionReport
    substrates: list[ScientificSubstrate]
    compatibility_build_report: ScientificSubstrateBuildReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef
    substrate_artifacts: list[ArtifactRef]


def construct_llm_substrates(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    generator: SubstrateGenerationClient,
    config: LLMSubstrateConstructionConfig,
) -> LLMSubstrateResult:
    """Construct, validate, select, and persist non-fake LLM scientific substrates."""
    if config.run_id != run_id:
        raise LLMSubstrateError("LLM substrate config run_id does not match run_id.")
    if generator.backend_kind not in {BackendKind.LLM_OPENAI, BackendKind.LLM_OTHER}:
        raise LLMSubstrateError("LLM substrate construction requires a non-fake LLM backend.")
    if generator.fallback_used:
        raise LLMSubstrateError("LLM substrate construction forbids deterministic fallback.")

    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    variance_path, variance = _load_latest_variance(run_id=run_id, reports=reports)
    if config.require_non_fake_backends and not variance.production_ready:
        raise LLMSubstrateError(
            "Strict substrate construction requires a production-eligible LLM variance report."
        )
    tree_path, tree_report = _load_latest_tree_construction(run_id=run_id, reports=reports)
    deep_path, deep = _load_deep_from_variance(root_path=root_path, variance=variance)
    _, atlas = _load_latest_atlas_scan(run_id=run_id, reports=reports)
    pair_by_id = {item.pair_id: item for item in atlas.selected_pairs}
    opportunity_by_id = {item.opportunity_id: item for item in deep.candidates}
    retrieval_by_pair = _load_retrieval_contexts(root_path=root_path, deep=deep)
    try:
        idea_tree = inspect_idea_tree(run_id=run_id, root=root_path)
    except IdeaTreeError as exc:
        raise LLMSubstrateError(f"M99 IdeaTree inspection failed: {exc}") from exc
    tree_node_ids = {item.node_id for item in idea_tree.nodes}

    variant_by_id = {item.variant_id: item for item in variance.candidates}
    selected_variants = [
        variant_by_id[item]
        for item in variance.selected_variant_ids
        if item in variant_by_id
    ][: config.max_source_variants]
    effective_limit = min(
        len(selected_variants),
        config.max_constructed_substrates,
        config.max_generation_calls,
    )
    selected_variants = selected_variants[:effective_limit]
    if not selected_variants:
        raise LLMSubstrateError("No selected LLM variance candidates are available.")
    missing_nodes = sorted(
        item.variant_id for item in selected_variants if item.variant_id not in tree_node_ids
    )
    if missing_nodes:
        raise LLMSubstrateError(
            f"Selected variants are missing from the M99 IdeaTree: {missing_nodes}"
        )

    report_number = _next_number(reports, _REPORT_RE)
    report_id = f"llm-substrate-construction-report-{report_number:04d}"
    raw_number = _next_number(reports, _RAW_RE)
    candidates: list[LLMScientificSubstrateCandidate] = []
    scores: list[LLMSubstrateConstructionScore] = []
    raw_artifacts: list[LLMSubstrateRawArtifact] = []
    warnings: list[str] = []
    repaired_count = 0

    for source_index, variant in enumerate(selected_variants):
        opportunity = opportunity_by_id.get(variant.source_opportunity_id)
        context = retrieval_by_pair.get(variant.source_pair_id)
        if opportunity is None or context is None:
            raise LLMSubstrateError(
                f"Source opportunity or retrieval context missing for {variant.variant_id}."
            )
        prompt_id = f"{report_id}-prompt-{source_index + 1:03d}"
        try:
            response = generator.construct_substrate(
                prompt_id=prompt_id,
                source_payload=variant.model_dump(mode="json"),
                opportunity_payload=opportunity.model_dump(mode="json"),
                retrieval_context_payload=context.model_dump(mode="json"),
            )
        except (AdapterError, ValueError) as exc:
            raise LLMSubstrateError(
                f"LLM substrate construction failed for {variant.variant_id}: {exc}"
            ) from exc
        substrate_id = f"llm-substrate-{report_number:04d}-{_slug(variant.variant_id)}"
        rejection_reasons = list(response.rejection_reasons)
        if response.repair_reasons:
            repaired_count += 1
            warnings.append(
                f"Repaired substrate format for {variant.variant_id}: "
                + "; ".join(response.repair_reasons)
            )
        candidate: LLMScientificSubstrateCandidate | None = None
        score: LLMSubstrateConstructionScore | None = None
        if response.accepted is not None:
            try:
                candidate = LLMScientificSubstrateCandidate(
                    substrate_id=substrate_id,
                    run_id=run_id,
                    source_idea_node_id=variant.variant_id,
                    source_variant_id=variant.variant_id,
                    source_opportunity_id=variant.source_opportunity_id,
                    source_pair_id=variant.source_pair_id,
                    domain_id=variant.domain_id,
                    method_id=variant.method_id,
                    **response.accepted.candidate.model_dump(mode="python"),
                )
                rejection_reasons.extend(
                    _decorative_method_reasons(candidate=candidate, variant=variant)
                )
                if not rejection_reasons:
                    score = LLMSubstrateConstructionScore(
                        substrate_id=substrate_id,
                        **response.accepted.score.model_dump(mode="python"),
                    )
            except ValidationError as exc:
                rejection_reasons.append(str(exc))
        if rejection_reasons:
            candidate = None
            score = None
            warnings.append(
                f"Rejected substrate output for {variant.variant_id}: "
                + "; ".join(rejection_reasons)
            )
        if candidate is not None and score is not None:
            candidates.append(candidate)
            scores.append(score)
        raw_id = f"llm-substrate-raw-{raw_number + source_index:04d}"
        raw_artifacts.append(
            LLMSubstrateRawArtifact(
                raw_artifact_id=raw_id,
                run_id=run_id,
                source_variant_id=variant.variant_id,
                backend_name=generator.backend_name,
                model=generator.model,
                prompt=response.prompt,
                raw_response=response.raw_response,
                accepted_substrate_id_optional=(
                    candidate.substrate_id if candidate is not None else None
                ),
                rejection_reasons=rejection_reasons,
                fallback_used=generator.fallback_used,
            )
        )
    if not candidates:
        failure_warnings = [
            "No valid substrates remained after schema and bridge validation.",
            *warnings,
        ]
        failure_record = _generation_backend_record(
            report_id=report_id,
            generator=generator,
            raw_ids=[item.raw_artifact_id for item in raw_artifacts],
        )
        failed_report = LLMSubstrateConstructionReport(
            run_id=run_id,
            report_id=report_id,
            construction_status="failed",
            config=config,
            source_variance_report_path=_relative(root_path, variance_path),
            source_idea_tree_construction_report_path=_relative(root_path, tree_path),
            source_deep_opportunity_report_path=_relative(root_path, deep_path),
            source_variant_count=len(selected_variants),
            constructed_substrate_count=0,
            rejected_substrate_count=len(raw_artifacts),
            repaired_substrate_count=0,
            selected_substrate_count=0,
            domain_family_coverage=0,
            method_family_coverage=0,
            route_hint_coverage=0,
            near_duplicate_suppressed_count=0,
            raw_artifact_paths=[
                f"runs/{run_id}/reports/{item.raw_artifact_id}.json"
                for item in raw_artifacts
            ],
            scientific_substrate_paths=[],
            candidates=[],
            scores=[],
            selected_substrate_ids=[],
            backend_records=[failure_record],
            warnings=failure_warnings,
            production_ready=False,
        )
        _persist_failed(
            report=failed_report,
            raw_artifacts=raw_artifacts,
            store=store,
            ledger=ledger,
        )
        raise LLMSubstrateError("; ".join(failure_warnings))

    selected, _, duplicate_count = select_llm_substrates(
        candidates=candidates,
        scores=scores,
        pairs=list(pair_by_id.values()),
        max_selected=config.max_selected_substrates,
        min_domain_families=config.min_domain_family_coverage,
        min_method_families=config.min_method_family_coverage,
        min_route_hints=config.min_route_hint_coverage,
        suppress_duplicates=config.near_duplicate_suppression,
    )
    if not selected:
        raise LLMSubstrateError("No substrates remained after diversity selection.")
    if duplicate_count:
        warnings.append(f"Suppressed {duplicate_count} near-duplicate substrates.")

    generation_record = _generation_backend_record(
        report_id=report_id,
        generator=generator,
        raw_ids=[item.raw_artifact_id for item in raw_artifacts],
    )
    selection_record = _selection_backend_record(report_id)
    backend_records = [generation_record, selection_record]
    production = evaluate_production_mode(
        run_id=run_id,
        records=[*variance.backend_records, *backend_records],
        policy=ProductionModePolicy(
            require_non_fake_backends=config.require_non_fake_backends,
            fail_on_silent_fallback=config.fail_on_silent_fallback,
        ),
        expected_stage_kinds=[
            ScientificStageKind.VARIANCE_GENERATION,
            ScientificStageKind.SUBSTRATE_CONSTRUCTION,
            ScientificStageKind.SUBSTRATE_SELECTION,
        ],
        report_id=f"{report_id}-production-evaluation",
    )
    if config.require_non_fake_backends and production.blocking_violation_count:
        details = "; ".join(item.message for item in production.violations)
        raise LLMSubstrateError(f"Strict LLM substrate construction blocked: {details}")

    selected_pairs = [pair_by_id[item.source_pair_id] for item in selected]
    domain_coverage = len({item.domain_family for item in selected_pairs})
    method_coverage = len({item.method_family for item in selected_pairs})
    route_coverage = len({item.route_hint for item in selected})
    build_number = _next_number(reports, _BUILD_RE)
    standard_substrates = [
        _to_scientific_substrate(
            candidate=item,
            opportunity=opportunity_by_id[item.source_opportunity_id],
            selected=(index == 0),
        )
        for index, item in enumerate(selected)
    ]
    substrate_paths = [
        f"runs/{run_id}/reports/llm-scientific-substrate-{build_number:04d}-{index:02d}.json"
        for index in range(1, len(standard_substrates) + 1)
    ]
    selected_standard = standard_substrates[0]
    build_report = ScientificSubstrateBuildReport(
        run_id=run_id,
        build_id=f"scientific-substrate-build-{build_number:04d}",
        build_status="completed_with_warnings" if warnings else "completed",
        source_idea_tree_report_path_optional=_relative(root_path, tree_path),
        max_substrates=config.max_selected_substrates,
        recommended_mutation_axes=[],
        built_mutation_axes=[item.route_hint for item in selected],
        substrate_paths=substrate_paths,
        substrate_count=len(standard_substrates),
        selected_substrate_id_optional=selected_standard.substrate_id,
        selected_substrate_title_optional=selected_standard.title,
        warnings=[
            "Compatibility report over non-fake LLM substrate artifacts; conversion creates no "
            "scientific evidence or publication readiness."
        ],
    )
    report = LLMSubstrateConstructionReport(
        run_id=run_id,
        report_id=report_id,
        construction_status="completed_with_warnings" if warnings else "completed",
        config=config,
        source_variance_report_path=_relative(root_path, variance_path),
        source_idea_tree_construction_report_path=_relative(root_path, tree_path),
        source_deep_opportunity_report_path=_relative(root_path, deep_path),
        source_variant_count=len(selected_variants),
        constructed_substrate_count=len(candidates),
        rejected_substrate_count=sum(bool(item.rejection_reasons) for item in raw_artifacts),
        repaired_substrate_count=repaired_count,
        selected_substrate_count=len(selected),
        domain_family_coverage=domain_coverage,
        method_family_coverage=method_coverage,
        route_hint_coverage=route_coverage,
        near_duplicate_suppressed_count=duplicate_count,
        raw_artifact_paths=[
            f"runs/{run_id}/reports/{item.raw_artifact_id}.json" for item in raw_artifacts
        ],
        scientific_substrate_paths=substrate_paths,
        candidates=candidates,
        scores=scores,
        selected_substrate_ids=[item.substrate_id for item in selected],
        backend_records=backend_records,
        warnings=warnings,
        production_ready=(
            config.require_non_fake_backends and not production.blocking_violation_count
        ),
    )
    persistence = _persist(
        report=report,
        raw_artifacts=raw_artifacts,
        standard_substrates=standard_substrates,
        build_report=build_report,
        build_number=build_number,
        store=store,
        ledger=ledger,
    )
    by_id = {item.id: item for item in persistence.artifacts}
    return LLMSubstrateResult(
        run_id=run_id,
        report=report,
        substrates=standard_substrates,
        compatibility_build_report=build_report,
        persistence=persistence,
        report_artifact=by_id[report_id],
        markdown_artifact=by_id[f"{report_id}-markdown"],
        substrate_artifacts=[
            by_id[f"llm-scientific-substrate-{build_number:04d}-{index:02d}"]
            for index in range(1, len(standard_substrates) + 1)
        ],
    )


def select_llm_substrates(
    *,
    candidates: list[LLMScientificSubstrateCandidate],
    scores: list[LLMSubstrateConstructionScore],
    pairs: list[DomainMethodPair],
    max_selected: int,
    min_domain_families: int,
    min_method_families: int,
    min_route_hints: int,
    suppress_duplicates: bool = True,
) -> tuple[
    list[LLMScientificSubstrateCandidate],
    list[LLMSubstrateConstructionScore],
    int,
]:
    candidate_by_id = {item.substrate_id: item for item in candidates}
    pair_by_id = {item.pair_id: item for item in pairs}
    ordered = sorted(scores, key=lambda item: (-item.final_score, item.substrate_id))
    unique: list[LLMSubstrateConstructionScore] = []
    seen: set[str] = set()
    duplicate_count = 0
    for score in ordered:
        candidate = candidate_by_id.get(score.substrate_id)
        if candidate is None or candidate.source_pair_id not in pair_by_id:
            continue
        fingerprint = _substrate_fingerprint(candidate)
        if suppress_duplicates and fingerprint in seen:
            duplicate_count += 1
            continue
        seen.add(fingerprint)
        unique.append(score)

    selected: list[LLMSubstrateConstructionScore] = []
    domain_counts: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()
    route_counts: Counter[str] = Counter()

    def add(score: LLMSubstrateConstructionScore) -> None:
        candidate = candidate_by_id[score.substrate_id]
        pair = pair_by_id[candidate.source_pair_id]
        selected.append(score)
        domain_counts[pair.domain_family] += 1
        method_counts[pair.method_family] += 1
        route_counts[candidate.route_hint] += 1

    for score in unique:
        if len(route_counts) >= min_route_hints or len(selected) >= max_selected:
            break
        candidate = candidate_by_id[score.substrate_id]
        if candidate.route_hint not in route_counts:
            add(score)
    for score in unique:
        if len(domain_counts) >= min_domain_families or len(selected) >= max_selected:
            break
        candidate = candidate_by_id[score.substrate_id]
        pair = pair_by_id[candidate.source_pair_id]
        if pair.domain_family not in domain_counts and score not in selected:
            add(score)
    for score in unique:
        if len(method_counts) >= min_method_families or len(selected) >= max_selected:
            break
        candidate = candidate_by_id[score.substrate_id]
        pair = pair_by_id[candidate.source_pair_id]
        if pair.method_family not in method_counts and score not in selected:
            add(score)
    for score in unique:
        if len(selected) >= max_selected:
            break
        if score not in selected:
            add(score)
    return [candidate_by_id[item.substrate_id] for item in selected], selected, duplicate_count


def inspect_llm_substrates(
    *, run_id: str, root: str | Path = "."
) -> LLMSubstrateConstructionInspectionReport:
    reports = Path(root) / "runs" / run_id / "reports"
    path = _latest_matching(reports, _REPORT_RE)
    if path is None:
        return LLMSubstrateConstructionInspectionReport(
            run_id=run_id,
            llm_substrate_construction_present=False,
        )
    report = _load_report(path)
    candidate_by_id = {item.substrate_id: item for item in report.candidates}
    score_by_id = {item.substrate_id: item for item in report.scores}
    return LLMSubstrateConstructionInspectionReport(
        run_id=run_id,
        llm_substrate_construction_present=True,
        latest_report_id_optional=report.report_id,
        construction_status_optional=report.construction_status,
        source_variant_count=report.source_variant_count,
        constructed_substrate_count=report.constructed_substrate_count,
        rejected_substrate_count=report.rejected_substrate_count,
        repaired_substrate_count=report.repaired_substrate_count,
        selected_substrate_count=report.selected_substrate_count,
        domain_family_coverage=report.domain_family_coverage,
        method_family_coverage=report.method_family_coverage,
        route_hint_coverage=report.route_hint_coverage,
        near_duplicate_suppressed_count=report.near_duplicate_suppressed_count,
        selected_substrates=[
            candidate_by_id[item]
            for item in report.selected_substrate_ids
            if item in candidate_by_id
        ],
        selected_scores=[
            score_by_id[item] for item in report.selected_substrate_ids if item in score_by_id
        ],
        backend_records=report.backend_records,
        warnings=report.warnings,
        production_ready=report.production_ready,
    )


def render_llm_substrate_text(report: LLMSubstrateConstructionInspectionReport) -> str:
    return "\n".join(
        [
            "LLM substrate construction: "
            f"{'present' if report.llm_substrate_construction_present else 'absent'}",
            f"Status: {report.construction_status_optional or 'not available'}",
            f"Source variants: {report.source_variant_count}",
            f"Constructed substrates: {report.constructed_substrate_count}",
            f"Selected substrates: {report.selected_substrate_count}",
            f"Rejected/repaired: {report.rejected_substrate_count}/"
            f"{report.repaired_substrate_count}",
            f"Domain/method/route coverage: {report.domain_family_coverage}/"
            f"{report.method_family_coverage}/{report.route_hint_coverage}",
            f"Duplicates suppressed: {report.near_duplicate_suppressed_count}",
            f"Production ready: {str(report.production_ready).lower()}",
            "publication_ready=false",
        ]
    )


def render_llm_substrate_markdown(report: LLMSubstrateConstructionReport) -> str:
    candidate_by_id = {item.substrate_id: item for item in report.candidates}
    lines = [
        "# LLM ScientificSubstrate Construction",
        "",
        f"Status: `{report.construction_status}`",
        f"Constructed: `{report.constructed_substrate_count}`",
        f"Selected: `{report.selected_substrate_count}`",
        "",
        "## Selected Substrates",
        "",
    ]
    lines.extend(
        f"- **{candidate_by_id[item].title}** (`{candidate_by_id[item].route_hint}`)"
        for item in report.selected_substrate_ids
        if item in candidate_by_id
    )
    lines.extend(
        [
            "",
            "Substrates are planning context only and create no proof, validation, or publication "
            "readiness.",
            "",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def _to_scientific_substrate(
    *,
    candidate: LLMScientificSubstrateCandidate,
    opportunity: DeepOpportunityCandidate,
    selected: bool,
) -> ScientificSubstrate:
    return ScientificSubstrate(
        substrate_id=candidate.substrate_id,
        run_id=candidate.run_id,
        source_idea_node_id_optional=candidate.source_idea_node_id,
        source_variance_candidate_id_optional=candidate.source_variant_id,
        source_opportunity_id_optional=candidate.source_opportunity_id,
        source_method_lens_id_optional=candidate.method_id,
        title=candidate.title,
        domain=opportunity.domain_name,
        domain_problem=candidate.domain_problem,
        central_tension=candidate.central_tension,
        concrete_model_object=candidate.concrete_model_object,
        variables_and_notation=candidate.variables_and_notation,
        assumptions=candidate.assumptions,
        mechanism="; ".join(candidate.mathematical_or_computational_form),
        dgp_or_dataset=candidate.experiment_or_proof_design.dgp,
        baseline="; ".join(candidate.baseline_candidates),
        measurable_hypothesis=candidate.hypothesis,
        experiment_design=candidate.experiment_or_proof_design,
        result_schema=candidate.result_schema,
        limitations=candidate.limitations,
        failure_modes=candidate.failure_modes,
        evidence_boundary=candidate.scope_boundary,
        selected_for_next_experiment=selected,
    )


def _persist(
    *,
    report: LLMSubstrateConstructionReport,
    raw_artifacts: list[LLMSubstrateRawArtifact],
    standard_substrates: list[ScientificSubstrate],
    build_report: ScientificSubstrateBuildReport,
    build_number: int,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PersistenceResult:
    metadata = _metadata("llm_substrate_construction")
    specs = [
        ArtifactWriteSpec(
            item.raw_artifact_id,
            ArtifactType.REPORT,
            item,
            "json",
            _metadata("llm_substrate_raw"),
        )
        for item in raw_artifacts
    ]
    specs.extend(
        ArtifactWriteSpec(
            f"llm-scientific-substrate-{build_number:04d}-{index:02d}",
            ArtifactType.REPORT,
            substrate,
            "json",
            _metadata("llm_scientific_substrate_compatibility"),
        )
        for index, substrate in enumerate(standard_substrates, start=1)
    )
    specs.extend(
        [
            ArtifactWriteSpec(
                build_report.build_id,
                ArtifactType.REPORT,
                build_report,
                "json",
                _metadata("llm_scientific_substrate_build_compatibility"),
            ),
            ArtifactWriteSpec(report.report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report.report_id}-markdown",
                ArtifactType.REPORT,
                render_llm_substrate_markdown(report),
                "markdown",
                metadata,
                filename_stem=report.report_id,
            ),
        ]
    )
    return persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.LLM_SUBSTRATE_CONSTRUCTION_WRITTEN,
        commit_payload={
            "run_id": report.run_id,
            "report_id": report.report_id,
            "constructed_substrate_count": report.constructed_substrate_count,
            "selected_substrate_count": report.selected_substrate_count,
            "production_ready": report.production_ready,
            "publication_ready": False,
        },
    )


def _persist_failed(
    *,
    report: LLMSubstrateConstructionReport,
    raw_artifacts: list[LLMSubstrateRawArtifact],
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PersistenceResult:
    """Persist rejection diagnostics without creating substrate artifacts."""
    metadata = _metadata("llm_substrate_construction")
    specs = [
        ArtifactWriteSpec(
            item.raw_artifact_id,
            ArtifactType.REPORT,
            item,
            "json",
            _metadata("llm_substrate_raw"),
        )
        for item in raw_artifacts
    ]
    specs.extend(
        [
            ArtifactWriteSpec(report.report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report.report_id}-markdown",
                ArtifactType.REPORT,
                render_llm_substrate_markdown(report),
                "markdown",
                metadata,
                filename_stem=report.report_id,
            ),
        ]
    )
    return persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.LLM_SUBSTRATE_CONSTRUCTION_WRITTEN,
        commit_payload={
            "run_id": report.run_id,
            "report_id": report.report_id,
            "construction_status": "failed",
            "rejected_substrate_count": report.rejected_substrate_count,
            "production_ready": False,
            "publication_ready": False,
        },
    )


def _generation_backend_record(
    *, report_id: str, generator: SubstrateGenerationClient, raw_ids: list[str]
) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-llm-generation",
        stage_kind=ScientificStageKind.SUBSTRATE_CONSTRUCTION,
        backend_kind=generator.backend_kind,
        backend_name=generator.backend_name,
        is_scientific_generation=True,
        is_scientific_judgment=True,
        is_execution_or_verification=False,
        reason=(
            "Model objects, assumptions, hypotheses, baselines, designs, failure modes, "
            "limitations, route hints, and scores come from the recorded non-fake LLM backend."
        ),
        artifact_ids=[report_id, *raw_ids],
        fallback_used=generator.fallback_used,
        fallback_disclosed=generator.fallback_disclosed,
    )


def _selection_backend_record(report_id: str) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-selection",
        stage_kind=ScientificStageKind.SUBSTRATE_SELECTION,
        backend_kind=BackendKind.HEURISTIC,
        backend_name="substrate_diversity_capacity_selector",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=False,
        allowed_in_production=True,
        reason=(
            "Deterministic diversity/capacity selection over LLM-generated substrate candidates."
        ),
        artifact_ids=[report_id],
    )


def _decorative_method_reasons(
    *, candidate: LLMScientificSubstrateCandidate, variant: LLMVarianceCandidate
) -> list[str]:
    method_tokens = {
        item
        for item in _TOKEN_RE.findall(variant.method_id.lower())
        if len(item) > 2 and item not in {"methods", "modeling", "theory"}
    }
    model_text = " ".join(
        [
            candidate.concrete_model_object.model_type,
            *candidate.concrete_model_object.equations,
            candidate.concrete_model_object.algorithm_optional or "",
            *candidate.mathematical_or_computational_form,
        ]
    ).lower()
    model_tokens = set(_TOKEN_RE.findall(model_text))
    method_id = variant.method_id.lower().replace("-", "_").replace(" ", "_")
    anchors = _METHOD_OBJECT_ANCHORS.get(method_id)
    if anchors:
        has_anchor = any(anchor in model_text for anchor in anchors)
    else:
        has_anchor = bool(method_tokens.intersection(model_tokens))
    if method_tokens and not has_anchor:
        return [
            "method vocabulary is decorative: no recognized method-object anchor appears in "
            "the concrete model"
        ]
    return []


def _substrate_fingerprint(candidate: LLMScientificSubstrateCandidate) -> str:
    return " ".join(
        _TOKEN_RE.findall(
            " ".join(
                [
                    candidate.title,
                    candidate.concrete_model_object.model_type,
                    *candidate.concrete_model_object.equations,
                    *candidate.baseline_candidates,
                    candidate.verification_path,
                ]
            ).lower()
        )
    )


def _load_latest_variance(
    *, run_id: str, reports: Path
) -> tuple[Path, LLMVarianceGenerationReport]:
    path = _latest_matching(reports, _VARIANCE_RE)
    if path is None:
        raise LLMSubstrateError(f"No M99 LLM variance report found for run_id={run_id}.")
    try:
        report = LLMVarianceGenerationReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise LLMSubstrateError(f"Could not load LLM variance report: {exc}") from exc
    return path, report


def _load_latest_tree_construction(
    *, run_id: str, reports: Path
) -> tuple[Path, IdeaTreeConstructionReport]:
    path = _latest_matching(reports, _TREE_RE)
    if path is None:
        raise LLMSubstrateError("No M99 IdeaTree construction report found.")
    try:
        report = IdeaTreeConstructionReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise LLMSubstrateError(f"Could not load IdeaTree construction report: {exc}") from exc
    if report.run_id != run_id:
        raise LLMSubstrateError("IdeaTree construction report run_id is inconsistent.")
    return path, report


def _load_deep_from_variance(
    *, root_path: Path, variance: LLMVarianceGenerationReport
) -> tuple[Path, DeepOpportunityDiscoveryReport]:
    path = root_path / variance.source_deep_opportunity_report_path
    try:
        report = DeepOpportunityDiscoveryReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise LLMSubstrateError(f"Could not load source deep opportunity report: {exc}") from exc
    return path, report


def _load_latest_atlas_scan(*, run_id: str, reports: Path) -> tuple[Path, AtlasScanReport]:
    path = _latest_matching(reports, _ATLAS_SCAN_RE)
    if path is None:
        raise LLMSubstrateError("No atlas scan found for substrate diversity metadata.")
    try:
        report = AtlasScanReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise LLMSubstrateError(f"Could not load atlas scan: {exc}") from exc
    if report.run_id != run_id:
        raise LLMSubstrateError("Atlas scan run_id is inconsistent.")
    return path, report


def _load_retrieval_contexts(
    *, root_path: Path, deep: DeepOpportunityDiscoveryReport
) -> dict[str, RetrievalContext]:
    contexts = {}
    for relative_path in deep.retrieval_context_paths:
        path = root_path / relative_path
        try:
            context = RetrievalContext.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise LLMSubstrateError(f"Could not load retrieval context {path}: {exc}") from exc
        contexts[context.source_pair_id] = context
    return contexts


def _load_report(path: Path) -> LLMSubstrateConstructionReport:
    try:
        return LLMSubstrateConstructionReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise LLMSubstrateError(f"Could not load LLM substrate report: {exc}") from exc


def _metadata(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "artifact_role": "llm_scientific_substrate_context",
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
    paths = [item for item in directory.iterdir() if pattern.match(item.name)]
    return max(paths, key=lambda item: item.name) if paths else None


def _next_number(directory: Path, pattern: re.Pattern[str]) -> int:
    if not directory.is_dir():
        return 1
    numbers = [
        int(match.group(1))
        for item in directory.iterdir()
        if (match := pattern.match(item.name)) is not None
    ]
    return max(numbers, default=0) + 1


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


__all__ = [
    "LLMSubstrateError",
    "LLMSubstrateResult",
    "construct_llm_substrates",
    "inspect_llm_substrates",
    "render_llm_substrate_markdown",
    "render_llm_substrate_text",
    "select_llm_substrates",
]
