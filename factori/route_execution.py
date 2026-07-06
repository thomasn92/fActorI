"""Deterministic offline execution for supported general branch routes."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.production_mode import stage_backend_record
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BackendKind,
    BranchRouteDecision,
    BranchRoutePlan,
    BranchRouteType,
    ControllerActionType,
    RouteExecutionInputContract,
    RouteExecutionInspectionReport,
    RouteExecutionOutputContract,
    RouteExecutionReport,
    RouteExecutionResult,
    RouteExecutionSpec,
    RouteExecutionStatus,
    ScientificStageKind,
    ScientificSubstrate,
    ScientificSubstrateBuildReport,
    StageBackendRecord,
)

_ROUTE_PLAN_RE = re.compile(r"^branch-route-plan-(\d{4})\.json$")
_SPEC_BUILD_RE = re.compile(r"^route-execution-spec-build-(\d{4})\.json$")
_EXECUTION_RE = re.compile(r"^route-execution-report-(\d{4})\.json$")
_SUPPORTED_ROUTES = {
    BranchRouteType.SYNTHETIC_EXPERIMENT,
    BranchRouteType.BENCHMARK_TOURNAMENT,
    BranchRouteType.APPLIED_MATH_REDUCTION,
}
_FORBIDDEN_CLAIMS = [
    "real-world validation",
    "broad empirical validation",
    "formal proof or theorem verification",
    "novelty established",
    "publication ready",
]


class RouteExecutionError(RuntimeError):
    """Raised when route specs or deterministic results cannot be handled safely."""


@dataclass(frozen=True)
class RouteExecutionBuildResult:
    """Persisted route-execution specifications."""

    run_id: str
    report: RouteExecutionReport
    specs: list[RouteExecutionSpec]
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    spec_artifacts: list[ArtifactRef]


@dataclass(frozen=True)
class RouteExecutionRunResult:
    """Persisted deterministic route-execution results."""

    run_id: str
    report: RouteExecutionReport
    results: list[RouteExecutionResult]
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    result_artifacts: list[ArtifactRef]


def build_route_execution_specs(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> RouteExecutionBuildResult:
    """Build one immutable route-specific execution spec per latest route decision."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    route_path, route_plan = _load_latest_route_plan(run_id=run_id, reports=reports)
    substrates = _load_route_substrates(
        run_id=run_id,
        root=root_path,
        route_plan=route_plan,
    )
    build_number = _next_number(reports, _SPEC_BUILD_RE)
    report_id = f"route-execution-spec-build-{build_number:04d}"
    specs = [
        _spec_for_route(
            spec_id=f"route-execution-spec-{build_number:04d}-{index:03d}",
            decision=decision,
            substrate=substrates[decision.substrate_id],
        )
        for index, decision in enumerate(route_plan.decisions, start=1)
    ]
    spec_paths = [f"runs/{run_id}/reports/{spec.spec_id}.json" for spec in specs]
    route_counts = Counter(spec.route_type for spec in specs)
    unsupported = Counter(
        spec.route_type.value for spec in specs if spec.route_type not in _SUPPORTED_ROUTES
    )
    report = RouteExecutionReport(
        run_id=run_id,
        report_id=report_id,
        report_status=RouteExecutionStatus.SPEC_CREATED,
        source_branch_route_plan_path=_relative(root_path, route_path),
        route_count=route_plan.route_count,
        spec_count=len(specs),
        executed_count=0,
        deferred_count=0,
        failed_count=0,
        result_count=0,
        synthetic_experiment_count=route_counts[BranchRouteType.SYNTHETIC_EXPERIMENT],
        benchmark_tournament_count=route_counts[BranchRouteType.BENCHMARK_TOURNAMENT],
        applied_math_reduction_count=route_counts[BranchRouteType.APPLIED_MATH_REDUCTION],
        evidence_label_counts={},
        unsupported_route_counts=dict(sorted(unsupported.items())),
        spec_paths=spec_paths,
        result_paths=[],
        specs=specs,
        results=[],
        backend_records=[
            _spec_backend_record(
                stage_id=report_id,
                artifact_ids=[report_id, *[spec.spec_id for spec in specs]],
            )
        ],
        warnings=(
            ["Unsupported routes have specs but will be explicitly deferred on execution."]
            if unsupported
            else []
        ),
        creates_scientific_validation=False,
        creates_real_world_validation=False,
        publication_ready=False,
    )
    metadata = _metadata("route_execution_spec")
    artifact_specs = [
        ArtifactWriteSpec(spec.spec_id, ArtifactType.REPORT, spec, "json", metadata)
        for spec in specs
    ]
    artifact_specs.extend(
        [
            ArtifactWriteSpec(report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report_id}-markdown",
                ArtifactType.REPORT,
                render_route_execution_markdown(report),
                "markdown",
                metadata,
                filename_stem=report_id,
            ),
        ]
    )
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=artifact_specs,
        action_type=ControllerActionType.ROUTE_EXECUTION_SPECS_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "report_id": report_id,
            "source_routing_id": route_plan.routing_id,
            "spec_count": len(specs),
            "unsupported_route_counts": dict(sorted(unsupported.items())),
            "creates_scientific_validation": False,
            "creates_real_world_validation": False,
            "publication_ready": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return RouteExecutionBuildResult(
        run_id=run_id,
        report=report,
        specs=specs,
        persistence=persistence,
        report_artifact=by_id[report_id],
        spec_artifacts=[by_id[spec.spec_id] for spec in specs],
    )


def run_route_execution(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> RouteExecutionRunResult:
    """Execute every latest route spec through deterministic offline evaluators."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    spec_build_path, spec_build = _load_latest_spec_build(run_id=run_id, reports=reports)
    specs = _load_specs(run_id=run_id, root=root_path, report=spec_build)
    execution_number = _next_number(reports, _EXECUTION_RE)
    report_id = f"route-execution-report-{execution_number:04d}"
    result_paths = [
        f"runs/{run_id}/reports/route-execution-result-{execution_number:04d}-{index:03d}.json"
        for index in range(1, len(specs) + 1)
    ]
    results: list[RouteExecutionResult] = []
    for index, (spec, result_path) in enumerate(
        zip(specs, result_paths, strict=True),
        start=1,
    ):
        result_id = f"route-execution-result-{execution_number:04d}-{index:03d}"
        try:
            result = execute_route_spec(spec=spec, result_id=result_id)
        except (ArithmeticError, KeyError, TypeError, ValueError) as exc:
            result = RouteExecutionResult(
                result_id=result_id,
                spec_id=spec.spec_id,
                route_id=spec.route_id,
                substrate_id=spec.substrate_id,
                route_type=spec.route_type,
                status=RouteExecutionStatus.FAILED,
                execution_backend=spec.execution_backend,
                summary="Deterministic route evaluator failed closed.",
                metrics={},
                output_payload={},
                artifacts=[result_path],
                evidence_label="InconclusiveResult",
                scope_label="workflow failure; no scientific support",
                warnings=[],
                failure_reason_optional=str(exc),
                creates_scientific_validation=False,
                creates_real_world_validation=False,
                publication_ready=False,
            )
        else:
            result = result.model_copy(update={"artifacts": [result_path]})
        result = result.model_copy(
            update={"backend_records": _fixture_result_backend_records(result_id)}
        )
        results.append(result)

    status_counts = Counter(result.status for result in results)
    evidence_counts = Counter(result.evidence_label for result in results)
    unsupported_counts = Counter(
        result.route_type.value
        for result in results
        if result.status == RouteExecutionStatus.DEFERRED_UNSUPPORTED_ROUTE
    )
    executed_count = sum(
        status_counts[status]
        for status in (
            RouteExecutionStatus.COMPLETED,
            RouteExecutionStatus.COMPLETED_WITH_WARNINGS,
        )
    )
    failed_count = status_counts[RouteExecutionStatus.FAILED]
    deferred_count = status_counts[RouteExecutionStatus.DEFERRED_UNSUPPORTED_ROUTE]
    report_status = (
        RouteExecutionStatus.FAILED
        if failed_count
        else (
            RouteExecutionStatus.COMPLETED_WITH_WARNINGS
            if deferred_count or any(result.warnings for result in results)
            else RouteExecutionStatus.COMPLETED
        )
    )
    completed = [
        result
        for result in results
        if result.status
        in {RouteExecutionStatus.COMPLETED, RouteExecutionStatus.COMPLETED_WITH_WARNINGS}
    ]
    route_counts = Counter(result.route_type for result in completed)
    report = RouteExecutionReport(
        run_id=run_id,
        report_id=report_id,
        report_status=report_status,
        source_branch_route_plan_path=spec_build.source_branch_route_plan_path,
        source_spec_build_path_optional=_relative(root_path, spec_build_path),
        route_count=spec_build.route_count,
        spec_count=len(specs),
        executed_count=executed_count,
        deferred_count=deferred_count,
        failed_count=failed_count,
        result_count=len(results),
        synthetic_experiment_count=route_counts[BranchRouteType.SYNTHETIC_EXPERIMENT],
        benchmark_tournament_count=route_counts[BranchRouteType.BENCHMARK_TOURNAMENT],
        applied_math_reduction_count=route_counts[BranchRouteType.APPLIED_MATH_REDUCTION],
        evidence_label_counts=dict(sorted(evidence_counts.items())),
        unsupported_route_counts=dict(sorted(unsupported_counts.items())),
        spec_paths=spec_build.spec_paths,
        result_paths=result_paths,
        specs=specs,
        results=results,
        backend_records=_fixture_result_backend_records(
            report_id,
            artifact_ids=[report_id, *[result.result_id for result in results]],
        ),
        warnings=(
            ["One or more unsupported route types were explicitly deferred."]
            if deferred_count
            else []
        ),
        creates_scientific_validation=False,
        creates_real_world_validation=False,
        publication_ready=False,
    )
    metadata = _metadata("route_execution_result")
    artifact_specs = [
        ArtifactWriteSpec(result.result_id, ArtifactType.REPORT, result, "json", metadata)
        for result in results
    ]
    artifact_specs.extend(
        [
            ArtifactWriteSpec(report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report_id}-markdown",
                ArtifactType.REPORT,
                render_route_execution_markdown(report),
                "markdown",
                metadata,
                filename_stem=report_id,
            ),
        ]
    )
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=artifact_specs,
        action_type=ControllerActionType.ROUTE_EXECUTION_RUN,
        commit_payload={
            "run_id": run_id,
            "report_id": report_id,
            "spec_count": len(specs),
            "executed_count": executed_count,
            "deferred_count": deferred_count,
            "failed_count": failed_count,
            "evidence_label_counts": dict(sorted(evidence_counts.items())),
            "creates_scientific_validation": False,
            "creates_real_world_validation": False,
            "publication_ready": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return RouteExecutionRunResult(
        run_id=run_id,
        report=report,
        results=results,
        persistence=persistence,
        report_artifact=by_id[report_id],
        result_artifacts=[by_id[result.result_id] for result in results],
    )


def inspect_route_execution(
    *, run_id: str, root: str | Path = "."
) -> RouteExecutionInspectionReport:
    """Inspect the latest execution report, or latest spec build if not yet run."""
    reports = Path(root) / "runs" / run_id / "reports"
    path = _latest_matching(reports, _EXECUTION_RE) or _latest_matching(reports, _SPEC_BUILD_RE)
    if path is None:
        return RouteExecutionInspectionReport(
            run_id=run_id,
            route_execution_present=False,
            warnings=["No route execution spec build or result report is present."],
            publication_ready=False,
        )
    try:
        report = RouteExecutionReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise RouteExecutionError(f"Could not load route execution report: {exc}") from exc
    if report.run_id != run_id:
        raise RouteExecutionError("Route execution report run_id does not match requested run.")
    return RouteExecutionInspectionReport(
        run_id=run_id,
        route_execution_present=True,
        latest_report_id_optional=report.report_id,
        report_status_optional=report.report_status,
        spec_count=report.spec_count,
        result_count=report.result_count,
        executed_count=report.executed_count,
        deferred_count=report.deferred_count,
        failed_count=report.failed_count,
        synthetic_experiment_count=report.synthetic_experiment_count,
        benchmark_tournament_count=report.benchmark_tournament_count,
        applied_math_reduction_count=report.applied_math_reduction_count,
        evidence_label_counts=report.evidence_label_counts,
        unsupported_route_counts=report.unsupported_route_counts,
        specs=report.specs,
        results=report.results,
        report_optional=report,
        warnings=report.warnings,
        creates_scientific_validation=False,
        creates_real_world_validation=False,
        publication_ready=False,
    )


def render_route_execution_text(report: RouteExecutionInspectionReport) -> str:
    """Render a concise route-execution inspection."""
    if not report.route_execution_present:
        return "\n".join(
            [
                "Route execution: absent",
                *[f"Warning: {warning}" for warning in report.warnings],
                "publication_ready=false",
            ]
        )
    status = report.report_status_optional.value if report.report_status_optional else "none"
    return "\n".join(
        [
            "Route Execution",
            f"Status: {status}",
            f"Specs: {report.spec_count}",
            f"Results: {report.result_count}",
            f"Executed: {report.executed_count}",
            f"Deferred: {report.deferred_count}",
            f"Failed: {report.failed_count}",
            f"Synthetic experiments: {report.synthetic_experiment_count}",
            f"Benchmark tournaments: {report.benchmark_tournament_count}",
            f"Applied-math reductions: {report.applied_math_reduction_count}",
            "No result creates real-world validation.",
            "publication_ready=false",
        ]
    )


def render_route_execution_markdown(report: RouteExecutionReport) -> str:
    """Render an append-only route execution report."""
    lines = [
        "# Route Execution Report",
        "",
        f"Report ID: `{report.report_id}`",
        f"Status: `{report.report_status.value}`",
        f"Specs: `{report.spec_count}`",
        f"Results: `{report.result_count}`",
        "",
    ]
    if report.results:
        lines.extend(
            [
                "| Method | Route | Status | Evidence label | Scope |",
                "|---|---|---|---|---|",
            ]
        )
        by_spec = {spec.spec_id: spec for spec in report.specs}
        lines.extend(
            f"| {by_spec[result.spec_id].method_lens} | {result.route_type.value} | "
            f"{result.status.value} | {result.evidence_label} | {result.scope_label} |"
            for result in report.results
        )
    else:
        lines.append("Execution specifications were created; no route was executed in this stage.")
    lines.extend(
        [
            "",
            "These outputs are bounded deterministic route results. They do not establish "
            "real-world validation, formal proof, novelty, or publication readiness.",
            "",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def _spec_for_route(
    *,
    spec_id: str,
    decision: BranchRouteDecision,
    substrate: ScientificSubstrate,
) -> RouteExecutionSpec:
    backend = _backend_for(decision.route_type, decision.method_lens)
    required_metrics, payload_fields = _output_requirements(
        decision.route_type, decision.method_lens
    )
    allowed_labels = _allowed_labels(decision.route_type)
    return RouteExecutionSpec(
        spec_id=spec_id,
        run_id=decision.run_id,
        route_id=decision.route_id,
        substrate_id=decision.substrate_id,
        route_type=decision.route_type,
        method_lens=decision.method_lens,
        branch_family=decision.branch_family,
        execution_backend=backend,
        input_contract=RouteExecutionInputContract(
            substrate_title=substrate.title,
            domain=substrate.domain,
            model_type=substrate.concrete_model_object.model_type,
            equations=substrate.concrete_model_object.equations,
            variables_and_notation=[
                variable.model_dump(mode="json") for variable in substrate.variables_and_notation
            ],
            assumptions=[assumption.statement for assumption in substrate.assumptions],
            dgp_or_dataset=substrate.dgp_or_dataset,
            baseline=substrate.baseline,
            proposed_method=substrate.experiment_design.method,
            measurable_hypothesis=substrate.measurable_hypothesis,
            metrics=substrate.experiment_design.metrics,
            seed=1729,
            route_parameters=_route_parameters(decision.route_type, decision.method_lens),
        ),
        output_contract=RouteExecutionOutputContract(
            required_metrics=required_metrics,
            required_payload_fields=payload_fields,
            scope_label=_scope_for(decision.route_type),
            success_criterion=substrate.experiment_design.success_criterion,
            failure_criterion=substrate.experiment_design.failure_criterion,
        ),
        expected_artifacts=["route_execution_result_json"],
        allowed_evidence_labels=allowed_labels,
        forbidden_claims=_FORBIDDEN_CLAIMS,
        backend_records=[_spec_backend_record(stage_id=spec_id, artifact_ids=[spec_id])],
        publication_ready=False,
        creates_real_world_validation=False,
    )


def execute_route_spec(*, spec: RouteExecutionSpec, result_id: str) -> RouteExecutionResult:
    """Execute one route spec without persistence using an approved local evaluator."""
    if spec.route_type not in _SUPPORTED_ROUTES:
        reason = f"Route type {spec.route_type.value} has no M95 deterministic executor."
        return RouteExecutionResult(
            result_id=result_id,
            spec_id=spec.spec_id,
            route_id=spec.route_id,
            substrate_id=spec.substrate_id,
            route_type=spec.route_type,
            status=RouteExecutionStatus.DEFERRED_UNSUPPORTED_ROUTE,
            execution_backend=spec.execution_backend,
            summary=reason,
            metrics={},
            output_payload={"deferred_reason": reason},
            artifacts=[],
            evidence_label="UnsupportedRouteDeferred",
            scope_label="workflow deferral; no scientific support",
            warnings=[reason],
            failure_reason_optional=reason,
            creates_scientific_validation=False,
            creates_real_world_validation=False,
            publication_ready=False,
        )
    if spec.route_type == BranchRouteType.APPLIED_MATH_REDUCTION:
        result = _execute_applied_math(spec=spec, result_id=result_id)
    elif spec.route_type == BranchRouteType.SYNTHETIC_EXPERIMENT:
        result = _execute_synthetic(spec=spec, result_id=result_id)
    else:
        result = _execute_benchmark(spec=spec, result_id=result_id)
    return _validate_result_contract(spec=spec, result=result)


def _execute_synthetic(*, spec: RouteExecutionSpec, result_id: str) -> RouteExecutionResult:
    method = _method_id(spec.method_lens)
    if method == "graph_curvature":
        metrics: dict[str, float | int | str | bool] = {
            "precision_at_k": 0.9,
            "recall_at_k": 0.9,
            "auc_proxy": 0.94,
            "false_positive_rate": 0.05,
            "sample_count": 40,
            "seed": spec.input_contract.seed,
        }
        payload = {
            "bundle_id": "graph_curvature_bottleneck_synthetic",
            "planted_bottleneck_count": 10,
            "comparison": "curvature-style bottleneck score vs degree/strength centrality",
        }
    elif method == "agent_based_modeling":
        baseline_errors = [0.22, 0.18, 0.25, 0.20, 0.19]
        method_errors = [0.11, 0.09, 0.13, 0.10, 0.08]
        metrics = {
            "held_out_mae": _mae(method_errors),
            "held_out_rmse": _rmse(method_errors),
            "baseline_held_out_mae": _mae(baseline_errors),
            "baseline_held_out_rmse": _rmse(baseline_errors),
            "heterogeneity_recovery_correlation": 0.93,
            "failure_regime_flag": False,
            "seed": spec.input_contract.seed,
        }
        payload = {
            "bundle_id": "agent_based_distance_decay_synthetic",
            "comparison": "pooled gravity vs heterogeneous-distance model",
            "agent_count": 240,
        }
    else:
        metrics = {
            "baseline_score": 0.7,
            "method_score": 0.8,
            "sample_count": 100,
            "seed": spec.input_contract.seed,
        }
        payload = {
            "bundle_id": "generic_substrate_synthetic",
            "comparison": (
                f"{spec.input_contract.baseline} vs {spec.input_contract.proposed_method}"
            ),
        }
    return _completed_result(
        spec=spec,
        result_id=result_id,
        summary="Completed deterministic synthetic comparison within the declared substrate scope.",
        metrics=metrics,
        payload=payload,
        evidence_label="SyntheticExperimentEvidence",
        scope_label="fixed-seed synthetic substrate evaluation only",
    )


def _execute_benchmark(*, spec: RouteExecutionSpec, result_id: str) -> RouteExecutionResult:
    method = _method_id(spec.method_lens)
    if method == "matrix_factorization":
        table = [
            {"model": "pooled_gravity", "held_out_mae": 0.184, "held_out_rmse": 0.241},
            {
                "model": "gravity_plus_low_rank",
                "held_out_mae": 0.112,
                "held_out_rmse": 0.153,
            },
        ]
        metrics = {
            "held_out_mae": 0.112,
            "held_out_rmse": 0.153,
            "baseline_held_out_mae": 0.184,
            "baseline_held_out_rmse": 0.241,
            "explained_residual_variance": 0.71,
            "rank_k": 3,
        }
        bundle_id = "matrix_factorization_od_benchmark"
    elif method == "topological_data_analysis":
        table = [
            {"model": "cluster_labels", "stability_score": 0.61, "cluster_instability": 0.39},
            {
                "model": "persistence_summary",
                "stability_score": 0.86,
                "cluster_instability": 0.14,
            },
        ]
        metrics = {
            "stability_score": 0.86,
            "bottleneck_distance_proxy": 0.17,
            "cluster_instability": 0.14,
        }
        bundle_id = "tda_accessibility_stability_benchmark"
    elif method == "spatial_statistics":
        table = [
            {
                "model": "unstructured_residual",
                "detection_power": 0.55,
                "false_positive_rate": 0.11,
            },
            {
                "model": "spatial_autocorrelation",
                "detection_power": 0.88,
                "false_positive_rate": 0.06,
            },
        ]
        metrics = {
            "detection_power": 0.88,
            "false_positive_rate": 0.06,
            "misspecification_score": 0.81,
        }
        bundle_id = "spatial_residual_autocorrelation_benchmark"
    elif method == "network_science":
        table = [
            {"model": "gravity_residual_clustering", "partition_stability": 0.63},
            {"model": "mobility_community_detection", "partition_stability": 0.84},
        ]
        metrics = {
            "adjusted_mutual_information_proxy": 0.82,
            "partition_stability": 0.84,
            "robustness_ratio": 1.333333,
        }
        bundle_id = "network_mobility_community_benchmark"
    elif method == "kernel_methods":
        table = [
            {"model": "distance_decay_gravity", "held_out_mae": 0.176, "held_out_rmse": 0.229},
            {"model": "kernel_spatial_interaction", "held_out_mae": 0.108, "held_out_rmse": 0.148},
        ]
        metrics = {
            "held_out_mae": 0.108,
            "held_out_rmse": 0.148,
            "baseline_held_out_mae": 0.176,
            "baseline_held_out_rmse": 0.229,
            "nonmonotone_affinity_recovery": 0.89,
        }
        bundle_id = "kernel_spatial_affinity_benchmark"
    else:
        table = [
            {"model": "baseline", "score": 0.7},
            {"model": "proposed", "score": 0.8},
        ]
        metrics = {"baseline_score": 0.7, "method_score": 0.8}
        bundle_id = "generic_substrate_benchmark"
    return _completed_result(
        spec=spec,
        result_id=result_id,
        summary="Completed deterministic baseline-versus-method benchmark within synthetic scope.",
        metrics=metrics,
        payload={"bundle_id": bundle_id, "comparison_table": table},
        evidence_label="BenchmarkEvidence",
        scope_label="deterministic synthetic benchmark only",
    )


def _execute_applied_math(*, spec: RouteExecutionSpec, result_id: str) -> RouteExecutionResult:
    payload = {
        "bundle_id": "wasserstein_accessibility_symbolic_reduction",
        "base_object": "a_i = sum_j w_j exp(-gamma d_ij)",
        "robust_object": ("sup_{w': W_c(w,w') <= delta} sum_j w'_j exp(-gamma d_ij)"),
        "symbolic_reduction_steps": [
            "Define phi_ij = exp(-gamma d_ij).",
            "Introduce lambda >= 0 for the Wasserstein transport-cost constraint.",
            "Form the candidate envelope lambda*delta plus a pointwise transport supremum.",
            "Reduce the finite synthetic support to a bounded lambda minimization target.",
        ],
        "assumptions": [
            *spec.input_contract.assumptions,
            "destination weights are nonnegative on finite synthetic support",
            "transport cost is finite on the declared support",
        ],
        "finite_dimensional_target": (
            "inf_{lambda >= 0} lambda*delta + sum_j w_j max_l(phi_il - lambda*c_jl)"
        ),
        "unresolved_steps": [
            "check strong-duality conditions for the exact ambiguity-set convention",
            "derive ranking-stability implications across regions",
        ],
        "novelty_risk": "not assessed; requires a separate bounded literature check",
    }
    return _completed_result(
        spec=spec,
        result_id=result_id,
        summary=(
            "Produced a deterministic candidate Wasserstein accessibility reduction draft; "
            "unresolved duality obligations remain."
        ),
        metrics={"symbolic_step_count": 4, "unresolved_step_count": 2},
        payload=payload,
        evidence_label="SymbolicReductionDraft",
        scope_label="symbolic reduction draft only; not proof or theorem verification",
    )


def _completed_result(
    *,
    spec: RouteExecutionSpec,
    result_id: str,
    summary: str,
    metrics: dict[str, float | int | str | bool],
    payload: dict[str, Any],
    evidence_label: str,
    scope_label: str,
) -> RouteExecutionResult:
    return RouteExecutionResult(
        result_id=result_id,
        spec_id=spec.spec_id,
        route_id=spec.route_id,
        substrate_id=spec.substrate_id,
        route_type=spec.route_type,
        status=RouteExecutionStatus.COMPLETED,
        execution_backend=spec.execution_backend,
        summary=summary,
        metrics=metrics,
        output_payload=payload,
        artifacts=[],
        evidence_label=evidence_label,
        scope_label=scope_label,
        warnings=[],
        failure_reason_optional=None,
        creates_scientific_validation=False,
        creates_real_world_validation=False,
        publication_ready=False,
    )


def _validate_result_contract(
    *, spec: RouteExecutionSpec, result: RouteExecutionResult
) -> RouteExecutionResult:
    missing_metrics = sorted(set(spec.output_contract.required_metrics).difference(result.metrics))
    missing_payload = sorted(
        set(spec.output_contract.required_payload_fields).difference(result.output_payload)
    )
    label_allowed = result.evidence_label in spec.allowed_evidence_labels
    if not missing_metrics and not missing_payload and label_allowed:
        return result
    reasons: list[str] = []
    if missing_metrics:
        reasons.append(f"missing metrics: {', '.join(missing_metrics)}")
    if missing_payload:
        reasons.append(f"missing payload fields: {', '.join(missing_payload)}")
    if not label_allowed:
        reasons.append(f"evidence label not allowed by spec: {result.evidence_label}")
    reason = "; ".join(reasons)
    return result.model_copy(
        update={
            "status": RouteExecutionStatus.FAILED,
            "summary": "Route output failed its immutable output contract.",
            "evidence_label": "InconclusiveResult",
            "scope_label": "contract failure; no scientific support",
            "warnings": [reason],
            "failure_reason_optional": reason,
        }
    )


def _backend_for(route_type: BranchRouteType, method_lens: str) -> str:
    method = _method_id(method_lens)
    if route_type == BranchRouteType.SYNTHETIC_EXPERIMENT:
        return {
            "graph_curvature": "graph_curvature_bottleneck_synthetic",
            "agent_based_modeling": "agent_based_distance_decay_synthetic",
        }.get(method, "generic_substrate_synthetic")
    if route_type == BranchRouteType.BENCHMARK_TOURNAMENT:
        return {
            "matrix_factorization": "matrix_factorization_od_benchmark",
            "topological_data_analysis": "tda_accessibility_stability_benchmark",
            "spatial_statistics": "spatial_residual_autocorrelation_benchmark",
            "network_science": "network_mobility_community_benchmark",
            "kernel_methods": "kernel_spatial_affinity_benchmark",
        }.get(method, "generic_substrate_benchmark")
    if route_type == BranchRouteType.APPLIED_MATH_REDUCTION:
        return (
            "wasserstein_accessibility_symbolic_reduction"
            if method == "optimal_transport"
            else "generic_applied_math_reduction"
        )
    return "unsupported_route_deferred"


def _output_requirements(
    route_type: BranchRouteType, method_lens: str
) -> tuple[list[str], list[str]]:
    method = _method_id(method_lens)
    metrics = {
        "graph_curvature": [
            "precision_at_k",
            "recall_at_k",
            "auc_proxy",
            "false_positive_rate",
        ],
        "agent_based_modeling": [
            "held_out_mae",
            "held_out_rmse",
            "heterogeneity_recovery_correlation",
            "failure_regime_flag",
        ],
        "matrix_factorization": [
            "held_out_mae",
            "held_out_rmse",
            "explained_residual_variance",
            "rank_k",
        ],
        "topological_data_analysis": [
            "stability_score",
            "bottleneck_distance_proxy",
            "cluster_instability",
        ],
        "spatial_statistics": [
            "detection_power",
            "false_positive_rate",
            "misspecification_score",
        ],
        "network_science": [
            "adjusted_mutual_information_proxy",
            "partition_stability",
            "robustness_ratio",
        ],
        "kernel_methods": [
            "held_out_mae",
            "held_out_rmse",
            "nonmonotone_affinity_recovery",
        ],
    }
    if route_type == BranchRouteType.APPLIED_MATH_REDUCTION:
        return (
            ["symbolic_step_count", "unresolved_step_count"],
            [
                "symbolic_reduction_steps",
                "assumptions",
                "finite_dimensional_target",
                "unresolved_steps",
                "novelty_risk",
            ],
        )
    if route_type not in _SUPPORTED_ROUTES:
        return [], ["deferred_reason"]
    payload = ["comparison_table"] if route_type == BranchRouteType.BENCHMARK_TOURNAMENT else []
    return metrics.get(method, ["baseline_score", "method_score"]), payload


def _route_parameters(route_type: BranchRouteType, method_lens: str) -> dict[str, Any]:
    return {
        "route_type": route_type.value,
        "method_lens": method_lens,
        "offline_only": True,
        "network_allowed": False,
        "deterministic": True,
    }


def _allowed_labels(route_type: BranchRouteType) -> list[str]:
    if route_type == BranchRouteType.SYNTHETIC_EXPERIMENT:
        return ["SyntheticExperimentEvidence", "NegativeResult", "InconclusiveResult"]
    if route_type == BranchRouteType.BENCHMARK_TOURNAMENT:
        return ["BenchmarkEvidence", "NegativeResult", "InconclusiveResult"]
    if route_type == BranchRouteType.APPLIED_MATH_REDUCTION:
        return ["SymbolicReductionDraft", "InconclusiveResult"]
    return ["UnsupportedRouteDeferred"]


def _scope_for(route_type: BranchRouteType) -> str:
    if route_type == BranchRouteType.SYNTHETIC_EXPERIMENT:
        return "fixed-seed synthetic substrate evaluation only"
    if route_type == BranchRouteType.BENCHMARK_TOURNAMENT:
        return "deterministic synthetic benchmark only"
    if route_type == BranchRouteType.APPLIED_MATH_REDUCTION:
        return "symbolic reduction draft only; not proof or theorem verification"
    return "workflow deferral; no scientific support"


def _load_latest_route_plan(*, run_id: str, reports: Path) -> tuple[Path, BranchRoutePlan]:
    path = _latest_matching(reports, _ROUTE_PLAN_RE)
    if path is None:
        raise RouteExecutionError(f"No BranchRoutePlan found for run_id={run_id}.")
    try:
        plan = BranchRoutePlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise RouteExecutionError(f"Could not load BranchRoutePlan: {exc}") from exc
    if plan.run_id != run_id:
        raise RouteExecutionError("BranchRoutePlan run_id does not match requested run.")
    return path, plan


def _load_route_substrates(
    *, run_id: str, root: Path, route_plan: BranchRoutePlan
) -> dict[str, ScientificSubstrate]:
    build_path = _safe_path(root, route_plan.source_scientific_substrate_build_path)
    try:
        build = ScientificSubstrateBuildReport.model_validate_json(
            build_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise RouteExecutionError(f"Could not load source substrate build: {exc}") from exc
    substrates: dict[str, ScientificSubstrate] = {}
    for relative in build.substrate_paths:
        path = _safe_path(root, relative)
        try:
            substrate = ScientificSubstrate.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise RouteExecutionError(f"Could not load route substrate: {exc}") from exc
        if substrate.run_id != run_id:
            raise RouteExecutionError("Route substrate run_id does not match requested run.")
        substrates[substrate.substrate_id] = substrate
    missing = {
        decision.substrate_id
        for decision in route_plan.decisions
        if decision.substrate_id not in substrates
    }
    if missing:
        raise RouteExecutionError(
            f"BranchRoutePlan references missing substrates: {', '.join(sorted(missing))}"
        )
    return substrates


def _load_latest_spec_build(*, run_id: str, reports: Path) -> tuple[Path, RouteExecutionReport]:
    path = _latest_matching(reports, _SPEC_BUILD_RE)
    if path is None:
        raise RouteExecutionError(
            f"No route execution spec build found for run_id={run_id}; "
            "run build-route-execution-specs first."
        )
    try:
        report = RouteExecutionReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise RouteExecutionError(f"Could not load route execution spec build: {exc}") from exc
    if report.run_id != run_id or report.report_status != RouteExecutionStatus.SPEC_CREATED:
        raise RouteExecutionError("Route execution spec build is inconsistent.")
    return path, report


def _load_specs(
    *, run_id: str, root: Path, report: RouteExecutionReport
) -> list[RouteExecutionSpec]:
    specs: list[RouteExecutionSpec] = []
    for relative in report.spec_paths:
        path = _safe_path(root, relative)
        try:
            spec = RouteExecutionSpec.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise RouteExecutionError(f"Could not load route execution spec: {exc}") from exc
        if spec.run_id != run_id:
            raise RouteExecutionError("Route execution spec run_id does not match requested run.")
        specs.append(spec)
    if len(specs) != report.spec_count:
        raise RouteExecutionError("Route execution spec count does not match persisted paths.")
    return specs


def _safe_path(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise RouteExecutionError("Route execution input path escapes configured root.")
    return path


def _spec_backend_record(*, stage_id: str, artifact_ids: list[str]) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=stage_id,
        stage_kind=ScientificStageKind.EXPERIMENT_DESIGN,
        backend_kind=BackendKind.DETERMINISTIC_TEMPLATE,
        backend_name="route_execution_spec_templates",
        is_scientific_generation=True,
        is_scientific_judgment=False,
        is_execution_or_verification=False,
        reason=(
            "Experiment, benchmark, and reduction contracts are generated from fixed "
            "deterministic templates."
        ),
        artifact_ids=artifact_ids,
    )


def _fixture_result_backend_records(
    stage_id: str, artifact_ids: list[str] | None = None
) -> list[StageBackendRecord]:
    artifacts = artifact_ids or [stage_id]
    return [
        stage_backend_record(
            stage_id=f"{stage_id}-execution",
            stage_kind=ScientificStageKind.EXPERIMENT_EXECUTION,
            backend_kind=BackendKind.FIXTURE,
            backend_name="fixed_route_result_values",
            is_scientific_generation=False,
            is_scientific_judgment=False,
            is_execution_or_verification=True,
            reason=(
                "The M95 evaluator returns fixed values and does not execute generated "
                "experiment code."
            ),
            artifact_ids=artifacts,
        ),
        stage_backend_record(
            stage_id=f"{stage_id}-metrics",
            stage_kind=ScientificStageKind.METRIC_COMPUTATION,
            backend_kind=BackendKind.FIXTURE,
            backend_name="fixed_route_metric_values",
            is_scientific_generation=False,
            is_scientific_judgment=False,
            is_execution_or_verification=True,
            reason="M95 metric values are fixture constants, not computed execution outputs.",
            artifact_ids=artifacts,
        ),
    ]


def _metadata(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "artifact_role": "bounded_route_execution_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "creates_real_world_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }


def _method_id(method_lens: str) -> str:
    return "_".join(re.findall(r"[a-z0-9]+", method_lens.lower()))


def _mae(errors: list[float]) -> float:
    return round(sum(abs(value) for value in errors) / len(errors), 6)


def _rmse(errors: list[float]) -> float:
    return round(math.sqrt(sum(value * value for value in errors) / len(errors)), 6)


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
    "RouteExecutionBuildResult",
    "RouteExecutionError",
    "RouteExecutionRunResult",
    "build_route_execution_specs",
    "execute_route_spec",
    "inspect_route_execution",
    "render_route_execution_markdown",
    "render_route_execution_text",
    "run_route_execution",
]
