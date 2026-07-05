"""Deterministic next-action routing for concrete ScientificSubstrates."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BranchRouteDecision,
    BranchRouteExecutionHint,
    BranchRouteInspectionReport,
    BranchRoutePlan,
    BranchRouteType,
    ControllerActionType,
    ScientificSubstrate,
    ScientificSubstrateBuildReport,
)

_ROUTE_RE = re.compile(r"^branch-route-plan-(\d{4})\.json$")
_BUILD_RE = re.compile(r"^scientific-substrate-build-(\d{4})\.json$")
_PLACEHOLDERS = {
    "",
    "none",
    "n a",
    "na",
    "not applicable",
    "not specified",
    "placeholder",
    "unknown",
}
_BENCHMARK_METHODS = {
    "kernel_methods",
    "matrix_factorization",
    "network_science",
    "spatial_statistics",
    "topological_data_analysis",
}
_APPLIED_MATH_METHODS = {
    "convex_duality",
    "distributionally_robust_optimization",
    "information_geometry",
    "optimal_transport",
    "robust_optimization",
}
_COMMANDS = {
    BranchRouteType.SYNTHETIC_EXPERIMENT: "generate-experiment-spec",
    BranchRouteType.BENCHMARK_TOURNAMENT: "run-benchmark-tournament",
    BranchRouteType.COUNTEREXAMPLE_SEARCH: "search-counterexamples",
    BranchRouteType.SYMBOLIC_DERIVATION: "derive-symbolic-reduction",
    BranchRouteType.APPLIED_MATH_REDUCTION: "derive-symbolic-reduction",
    BranchRouteType.PROOF_PLAN: "build-proof-plan",
    BranchRouteType.LITERATURE_NOVELTY_CHECK: "run-literature-novelty-check",
}
_REQUIRED_ARTIFACTS = {
    BranchRouteType.SYNTHETIC_EXPERIMENT: [
        "scientific_substrate",
        "bounded_experiment_spec",
        "approved_local_experiment_bundle",
    ],
    BranchRouteType.BENCHMARK_TOURNAMENT: [
        "scientific_substrate",
        "baseline_contract",
        "benchmark_comparison_spec",
    ],
    BranchRouteType.COUNTEREXAMPLE_SEARCH: [
        "scientific_substrate",
        "bounded_search_domain",
        "failure_criterion",
    ],
    BranchRouteType.SYMBOLIC_DERIVATION: [
        "scientific_substrate",
        "model_equations",
        "symbolic_derivation_contract",
    ],
    BranchRouteType.APPLIED_MATH_REDUCTION: [
        "scientific_substrate",
        "model_equations",
        "assumption_set",
        "finite_dimensional_reduction_target",
    ],
    BranchRouteType.PROOF_PLAN: [
        "scientific_substrate",
        "bounded_proposition",
        "assumption_set",
    ],
    BranchRouteType.LITERATURE_NOVELTY_CHECK: [
        "scientific_substrate",
        "accepted_source_registry",
        "bounded_retrieval_query_plan",
    ],
    BranchRouteType.DEFER_INSUFFICIENT_SUBSTRATE: ["scientific_substrate"],
    BranchRouteType.REJECT_FALSE_BRIDGE: ["scientific_substrate"],
}
_EXPECTED_OUTPUTS = {
    BranchRouteType.SYNTHETIC_EXPERIMENT: [
        "planned_experiment_spec",
        "bounded_metric_schema",
    ],
    BranchRouteType.BENCHMARK_TOURNAMENT: [
        "benchmark_tournament_spec",
        "comparison_table_schema",
    ],
    BranchRouteType.COUNTEREXAMPLE_SEARCH: [
        "counterexample_search_spec",
        "negative_or_inconclusive_result_schema",
    ],
    BranchRouteType.SYMBOLIC_DERIVATION: [
        "symbolic_reduction_plan",
        "derivation_obligation_list",
    ],
    BranchRouteType.APPLIED_MATH_REDUCTION: [
        "finite_dimensional_reduction_plan",
        "assumption_and_error_bound_obligations",
    ],
    BranchRouteType.PROOF_PLAN: ["proof_plan_spec", "formal_obligation_list"],
    BranchRouteType.LITERATURE_NOVELTY_CHECK: [
        "retrieval_plan",
        "bounded_literature_context_report",
    ],
    BranchRouteType.DEFER_INSUFFICIENT_SUBSTRATE: ["substrate_completion_requirements"],
    BranchRouteType.REJECT_FALSE_BRIDGE: ["false_bridge_rejection_record"],
}


class BranchRoutingError(RuntimeError):
    """Raised when substrate routes cannot be built or inspected safely."""


@dataclass(frozen=True)
class BranchRoutingResult:
    """Persisted branch-route plan and artifact references."""

    run_id: str
    plan: BranchRoutePlan
    persistence: PersistenceResult
    plan_artifact: ArtifactRef


def route_branches(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> BranchRoutingResult:
    """Classify every substrate in the latest build and persist an immutable plan."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    build_path, build_report, substrates = _load_latest_substrates(
        run_id=run_id,
        root=root_path,
        reports=reports,
    )
    route_number = _next_number(reports, _ROUTE_RE)
    routing_id = f"branch-route-plan-{route_number:04d}"
    decisions = [
        build_branch_route_decision(
            run_id=run_id,
            route_id=f"{routing_id}-{index:03d}",
            substrate=substrate,
        )
        for index, substrate in enumerate(substrates, start=1)
    ]
    counts = Counter(decision.route_type.value for decision in decisions)
    deferred_count = counts[BranchRouteType.DEFER_INSUFFICIENT_SUBSTRATE.value]
    rejected_count = counts[BranchRouteType.REJECT_FALSE_BRIDGE.value]
    plan = BranchRoutePlan(
        run_id=run_id,
        routing_id=routing_id,
        source_scientific_substrate_build_path=_relative(root_path, build_path),
        substrate_count=len(substrates),
        route_count=len(decisions),
        route_type_counts=dict(sorted(counts.items())),
        routed_count=len(decisions) - deferred_count - rejected_count,
        deferred_count=deferred_count,
        rejected_count=rejected_count,
        synthetic_experiment_count=counts[BranchRouteType.SYNTHETIC_EXPERIMENT.value],
        benchmark_tournament_count=counts[BranchRouteType.BENCHMARK_TOURNAMENT.value],
        counterexample_search_count=counts[BranchRouteType.COUNTEREXAMPLE_SEARCH.value],
        applied_math_reduction_count=counts[BranchRouteType.APPLIED_MATH_REDUCTION.value],
        proof_plan_count=counts[BranchRouteType.PROOF_PLAN.value],
        decisions=decisions,
        warnings=(
            ["Latest ScientificSubstrate build contains no substrates."]
            if not substrates
            else []
        ),
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )
    metadata = {
        "stage": "branch_routing",
        "artifact_role": "scientific_workflow_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(routing_id, ArtifactType.REPORT, plan, "json", metadata),
            ArtifactWriteSpec(
                f"{routing_id}-markdown",
                ArtifactType.REPORT,
                render_branch_route_markdown(plan),
                "markdown",
                metadata,
                filename_stem=routing_id,
            ),
        ],
        action_type=ControllerActionType.BRANCH_ROUTES_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "routing_id": routing_id,
            "source_build_id": build_report.build_id,
            "substrate_count": len(substrates),
            "route_type_counts": dict(sorted(counts.items())),
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    artifact = next(item for item in persistence.artifacts if item.id == routing_id)
    return BranchRoutingResult(
        run_id=run_id,
        plan=plan,
        persistence=persistence,
        plan_artifact=artifact,
    )


def build_branch_route_decision(
    *,
    run_id: str,
    route_id: str,
    substrate: ScientificSubstrate,
) -> BranchRouteDecision:
    """Build one deterministic route decision without persistence or execution."""
    method_lens = _method_lens(substrate)
    branch_family = _branch_family(substrate)
    route_type, confidence, reason, disposition = _classify_route(
        substrate=substrate,
        method_lens=method_lens,
        branch_family=branch_family,
    )
    command = _COMMANDS.get(route_type)
    ready = command is not None
    return BranchRouteDecision(
        route_id=route_id,
        run_id=run_id,
        substrate_id=substrate.substrate_id,
        idea_node_id_optional=substrate.source_idea_node_id_optional,
        method_lens=method_lens.replace("_", " "),
        branch_family=branch_family,
        route_type=route_type,
        route_confidence=confidence,
        reason=reason,
        required_artifacts=_REQUIRED_ARTIFACTS[route_type],
        expected_outputs=_EXPECTED_OUTPUTS[route_type],
        execution_hint=BranchRouteExecutionHint(
            command_class_optional=command,
            ready_for_execution=ready,
            suggested_arguments=(
                ["--run-id", run_id, "--substrate-id", substrate.substrate_id]
                if ready
                else []
            ),
            safety_notes=[
                "This hint does not execute the action or create evidence.",
                "Any later output must pass its existing evidence and safety intake rules.",
            ],
            executes_now=False,
            network_required=False,
        ),
        defer_or_reject_reason_optional=disposition,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def inspect_branch_routes(
    *, run_id: str, root: str | Path = "."
) -> BranchRouteInspectionReport:
    """Inspect the latest persisted route plan without mutating the run."""
    reports = Path(root) / "runs" / run_id / "reports"
    path = _latest_matching(reports, _ROUTE_RE)
    if path is None:
        return BranchRouteInspectionReport(
            run_id=run_id,
            branch_routes_present=False,
            warnings=["No branch route plan is present."],
            publication_ready=False,
        )
    try:
        plan = BranchRoutePlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise BranchRoutingError(f"Could not load branch route plan: {exc}") from exc
    if plan.run_id != run_id:
        raise BranchRoutingError("Branch route plan run_id does not match requested run.")
    return BranchRouteInspectionReport(
        run_id=run_id,
        branch_routes_present=True,
        latest_routing_id_optional=plan.routing_id,
        substrate_count=plan.substrate_count,
        route_count=plan.route_count,
        route_type_counts=plan.route_type_counts,
        routed_count=plan.routed_count,
        deferred_count=plan.deferred_count,
        rejected_count=plan.rejected_count,
        decisions=plan.decisions,
        plan_optional=plan,
        warnings=plan.warnings,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def render_branch_route_text(report: BranchRouteInspectionReport) -> str:
    """Render a concise human-readable branch-route inspection."""
    if not report.branch_routes_present:
        return "\n".join(
            [
                "Branch routes: absent",
                *[f"Warning: {warning}" for warning in report.warnings],
                "publication_ready=false",
            ]
        )
    lines = [
        "Branch Routes",
        f"Substrates: {report.substrate_count}",
        f"Routes: {report.route_count}",
        f"Routed: {report.routed_count}",
        f"Deferred: {report.deferred_count}",
        f"Rejected: {report.rejected_count}",
        "Route type counts:",
    ]
    lines.extend(
        f"- {route_type}: {count}"
        for route_type, count in sorted(report.route_type_counts.items())
    )
    lines.append("Decisions:")
    lines.extend(
        f"- {decision.method_lens} / {decision.branch_family}: "
        f"{decision.route_type.value} ({decision.route_confidence:.2f})"
        for decision in report.decisions
    )
    lines.extend(
        [
            "Routing is planning context only and does not execute verification.",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def render_branch_route_markdown(plan: BranchRoutePlan) -> str:
    """Render the append-only Markdown companion for a route plan."""
    lines = [
        "# General Branch Route Plan",
        "",
        f"Routing ID: `{plan.routing_id}`",
        f"Substrates routed: `{plan.route_count}`",
        "",
        "## Decisions",
        "",
        "| Method lens | Branch family | Route | Confidence | Reason |",
        "|---|---|---|---:|---|",
    ]
    lines.extend(
        f"| {decision.method_lens} | {decision.branch_family} | "
        f"{decision.route_type.value} | {decision.route_confidence:.2f} | "
        f"{decision.reason} |"
        for decision in plan.decisions
    )
    lines.extend(
        [
            "",
            "This plan is workflow context only. It executes no experiment, benchmark, search, "
            "derivation, proof, or retrieval action and creates no evidence.",
            "",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def _classify_route(
    *,
    substrate: ScientificSubstrate,
    method_lens: str,
    branch_family: str,
) -> tuple[BranchRouteType, float, str, str | None]:
    text = _substrate_text(substrate)
    has_equation = any(_meaningful(value) for value in substrate.concrete_model_object.equations)
    has_model = _meaningful(substrate.concrete_model_object.model_type)
    has_dgp = _meaningful(substrate.dgp_or_dataset) and _meaningful(
        substrate.experiment_design.dgp
    )
    has_baseline = _meaningful(substrate.baseline) and _meaningful(
        substrate.experiment_design.baseline
    )
    has_metrics = any(_meaningful(metric) for metric in substrate.experiment_design.metrics)

    if _contains_any(
        text,
        "decorative method",
        "false bridge",
        "no primitive mapping",
        "no primitive-to-object mapping",
        "unverifiable bridge",
    ) or _normalized(substrate.concrete_model_object.model_type) in {
        "decorative",
        "false bridge",
    }:
        reason = "Method-domain bridge is explicitly decorative or unverifiable."
        return BranchRouteType.REJECT_FALSE_BRIDGE, 0.99, reason, reason

    if _contains_any(text, "theorem", "conjecture", "proof obligation", "prove that"):
        return (
            BranchRouteType.PROOF_PLAN,
            0.94,
            "The substrate states a theorem, conjecture, or explicit proof obligation.",
            None,
        )

    if _contains_any(text, "literature novelty", "novelty check", "literature only"):
        return (
            BranchRouteType.LITERATURE_NOVELTY_CHECK,
            0.93,
            "The branch requires bounded literature positioning rather than model execution.",
            None,
        )

    if branch_family == "counterexample_variant" or _contains_any(
        text,
        "counterexample",
        "find failure",
        "when does",
        "misorders",
    ):
        return (
            BranchRouteType.COUNTEREXAMPLE_SEARCH,
            0.92,
            "The branch is framed around a bounded failure regime or counterexample.",
            None,
        )

    if not has_model or not has_equation or not has_baseline:
        missing = [
            label
            for label, present in (
                ("concrete model", has_model),
                ("equation", has_equation),
                ("baseline", has_baseline),
            )
            if not present
        ]
        reason = f"Insufficient substrate: missing meaningful {', '.join(missing)}."
        return BranchRouteType.DEFER_INSUFFICIENT_SUBSTRATE, 0.98, reason, reason

    if method_lens in _APPLIED_MATH_METHODS and _contains_any(
        text,
        "wasserstein",
        "duality",
        "ambiguity set",
        "transport",
        "finite-dimensional",
        "envelope",
    ):
        return (
            BranchRouteType.APPLIED_MATH_REDUCTION,
            0.90,
            "The model has explicit equations and an applied-math object suitable for a "
            "bounded reduction before execution.",
            None,
        )

    if method_lens in _BENCHMARK_METHODS and has_dgp and has_metrics:
        return (
            BranchRouteType.BENCHMARK_TOURNAMENT,
            0.91,
            "The substrate defines a DGP, baseline, method metrics, and a "
            "comparison-oriented method lens.",
            None,
        )

    if _contains_any(
        text,
        "symbolic derivation",
        "closed form",
        "finite-dimensional reduction",
        "change of variables",
        "analytic transform",
    ):
        return (
            BranchRouteType.SYMBOLIC_DERIVATION,
            0.88,
            "The equation and transform target support a deterministic symbolic derivation plan.",
            None,
        )

    if has_dgp and has_baseline and has_metrics:
        return (
            BranchRouteType.SYNTHETIC_EXPERIMENT,
            0.90,
            "The substrate supplies a bounded DGP, baseline, metrics, and failure criterion.",
            None,
        )

    reason = "Substrate lacks a complete locally evaluable DGP and metric contract."
    return BranchRouteType.DEFER_INSUFFICIENT_SUBSTRATE, 0.96, reason, reason


def _load_latest_substrates(
    *,
    run_id: str,
    root: Path,
    reports: Path,
) -> tuple[Path, ScientificSubstrateBuildReport, list[ScientificSubstrate]]:
    build_path = _latest_matching(reports, _BUILD_RE)
    if build_path is None:
        raise BranchRoutingError(
            f"No ScientificSubstrate build report found for run_id={run_id}."
        )
    try:
        build = ScientificSubstrateBuildReport.model_validate_json(
            build_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise BranchRoutingError(f"Could not load ScientificSubstrate build: {exc}") from exc
    if build.run_id != run_id:
        raise BranchRoutingError("ScientificSubstrate build run_id does not match requested run.")
    substrates: list[ScientificSubstrate] = []
    root_resolved = root.resolve()
    for relative_path in build.substrate_paths:
        path = (root / relative_path).resolve()
        if not path.is_relative_to(root_resolved):
            raise BranchRoutingError("ScientificSubstrate path escapes the configured root.")
        try:
            substrate = ScientificSubstrate.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError) as exc:
            raise BranchRoutingError(f"Could not load ScientificSubstrate {path}: {exc}") from exc
        if substrate.run_id != run_id:
            raise BranchRoutingError(
                f"ScientificSubstrate {substrate.substrate_id} has a mismatched run_id."
            )
        substrates.append(substrate)
    if len(substrates) != build.substrate_count:
        raise BranchRoutingError(
            "ScientificSubstrate build count does not match its persisted substrate paths."
        )
    return build_path, build, substrates


def _method_lens(substrate: ScientificSubstrate) -> str:
    if substrate.source_method_lens_id_optional:
        return substrate.source_method_lens_id_optional
    text = _substrate_text(substrate)
    mappings = (
        ("optimal_transport", ("wasserstein", "optimal transport")),
        ("matrix_factorization", ("low-rank", "matrix factorization", "svd")),
        ("graph_curvature", ("curvature",)),
        ("topological_data_analysis", ("persistent homology", "filtration")),
        ("agent_based_modeling", ("agent", "destination-choice")),
        ("spatial_statistics", ("moran", "spatial autocorrelation")),
        ("network_science", ("community partition", "mobility communit")),
        ("kernel_methods", ("kernel",)),
        ("convex_duality", ("duality",)),
    )
    for method, tokens in mappings:
        if _contains_any(text, *tokens):
            return method
    return "unspecified"


def _branch_family(substrate: ScientificSubstrate) -> str:
    axis = substrate.source_mutation_axis_optional or ""
    for family in (
        "mechanism_variant",
        "robustness_variant",
        "counterexample_variant",
        "benchmark_variant",
        "representation_variant",
    ):
        if family in axis:
            return family
    text = _substrate_text(substrate)
    if _contains_any(text, "counterexample", "find failure", "misorders"):
        return "counterexample_variant"
    if _contains_any(text, "robustness", "perturbation", "stability"):
        return "robustness_variant"
    if _contains_any(text, "benchmark", "compare"):
        return "benchmark_variant"
    if _contains_any(text, "representation", "low-rank", "factor"):
        return "representation_variant"
    return "mechanism_variant"


def _substrate_text(substrate: ScientificSubstrate) -> str:
    values = [
        substrate.title,
        substrate.domain_problem,
        substrate.central_tension,
        substrate.mechanism,
        substrate.dgp_or_dataset,
        substrate.baseline,
        substrate.measurable_hypothesis,
        substrate.concrete_model_object.model_type,
        substrate.concrete_model_object.what_would_falsify_it,
        substrate.experiment_design.target_claim,
        substrate.experiment_design.dgp,
        substrate.experiment_design.baseline,
        substrate.experiment_design.method,
        substrate.experiment_design.ablation_or_stress_test,
        substrate.evidence_boundary,
        substrate.source_mutation_axis_optional or "",
        *substrate.concrete_model_object.equations,
        *substrate.experiment_design.metrics,
        *substrate.limitations,
        *substrate.failure_modes,
    ]
    return " ".join(values).lower()


def _contains_any(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)


def _meaningful(value: str) -> bool:
    return _normalized(value) not in _PLACEHOLDERS


def _normalized(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.lower()))


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
    "BranchRoutingError",
    "BranchRoutingResult",
    "build_branch_route_decision",
    "inspect_branch_routes",
    "render_branch_route_markdown",
    "render_branch_route_text",
    "route_branches",
]
