"""Non-fake LLM route adjudication and non-executing spec construction."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factori.adapters.errors import AdapterError
from factori.adapters.llm_route_planning import RoutePlanningClient
from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.production_mode import evaluate_production_mode, stage_backend_record
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BackendKind,
    BranchRouteDecision,
    BranchRouteExecutionHint,
    BranchRoutePlan,
    BranchRouteType,
    ControllerActionType,
    DeepOpportunityDiscoveryReport,
    LLMExecutionSpecCandidate,
    LLMRouteDecisionCandidate,
    LLMRoutePlanningConfig,
    LLMRoutePlanningInspectionReport,
    LLMRoutePlanningRawArtifact,
    LLMRoutePlanningReport,
    LLMRoutePlanningScore,
    LLMScientificSubstrateCandidate,
    LLMSubstrateConstructionReport,
    LLMVarianceGenerationReport,
    ProductionModePolicy,
    RetrievalContext,
    RouteExecutionReport,
    RouteExecutionSpec,
    RouteExecutionStatus,
    ScientificStageKind,
    ScientificSubstrate,
    StageBackendRecord,
)

_SUBSTRATE_REPORT_RE = re.compile(r"^llm-substrate-construction-report-(\d{4})\.json$")
_REPORT_RE = re.compile(r"^llm-route-planning-report-(\d{4})\.json$")
_RAW_RE = re.compile(r"^llm-route-planning-raw-(\d{4})\.json$")
_COMPAT_ROUTE_RE = re.compile(r"^branch-route-plan-llm-(\d{4})\.json$")
_COMPAT_SPEC_RE = re.compile(r"^route-execution-specs-llm-(\d{4})\.json$")

_COMMANDS = {
    BranchRouteType.SYNTHETIC_EXPERIMENT: "generate-experiment-spec",
    BranchRouteType.BENCHMARK_TOURNAMENT: "run-benchmark-tournament",
    BranchRouteType.COUNTEREXAMPLE_SEARCH: "search-counterexamples",
    BranchRouteType.SYMBOLIC_DERIVATION: "derive-symbolic-reduction",
    BranchRouteType.APPLIED_MATH_REDUCTION: "derive-symbolic-reduction",
    BranchRouteType.PROOF_PLAN: "build-proof-plan",
    BranchRouteType.LITERATURE_NOVELTY_CHECK: "run-literature-novelty-check",
}


class LLMRoutePlanningError(RuntimeError):
    """Raised when production-safe LLM route planning cannot proceed."""


@dataclass(frozen=True)
class LLMRoutePlanningResult:
    run_id: str
    report: LLMRoutePlanningReport
    compatibility_route_plan: BranchRoutePlan
    compatibility_spec_report: RouteExecutionReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


def plan_llm_routes(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    planner: RoutePlanningClient,
    config: LLMRoutePlanningConfig,
) -> LLMRoutePlanningResult:
    """Plan one scientific route and one non-executing spec per selected substrate."""
    if config.run_id != run_id:
        raise LLMRoutePlanningError("LLM route config run_id does not match run_id.")
    if planner.backend_kind not in {BackendKind.LLM_OPENAI, BackendKind.LLM_OTHER}:
        raise LLMRoutePlanningError("LLM route planning requires a non-fake LLM backend.")
    if planner.fallback_used:
        raise LLMRoutePlanningError("LLM route planning forbids deterministic fallback.")

    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    substrate_report_path, substrate_report = _load_latest_substrate_report(
        run_id=run_id,
        reports=reports,
    )
    if config.require_non_fake_backends and not substrate_report.production_ready:
        raise LLMRoutePlanningError(
            "Strict route planning requires a production-eligible M100 substrate report."
        )
    variance_path, variance = _load_variance(root_path, substrate_report)
    deep_path, deep = _load_deep(root_path, substrate_report)
    retrieval_by_pair = _load_retrieval_contexts(root_path, deep)
    standard_by_id = _load_standard_substrates(root_path, substrate_report)
    candidate_by_id = {item.substrate_id: item for item in substrate_report.candidates}
    variant_by_id = {item.variant_id: item for item in variance.candidates}
    opportunity_by_id = {item.opportunity_id: item for item in deep.candidates}
    selected = [
        candidate_by_id[substrate_id]
        for substrate_id in substrate_report.selected_substrate_ids
        if substrate_id in candidate_by_id
    ]
    selected = selected[: min(config.max_source_substrates, config.max_planning_calls)]
    if not selected:
        raise LLMRoutePlanningError("No selected M100 substrates are available for route planning.")

    report_number = _next_number(reports, _REPORT_RE)
    raw_number = _next_number(reports, _RAW_RE)
    report_id = f"llm-route-planning-report-{report_number:04d}"
    decisions: list[LLMRouteDecisionCandidate] = []
    specs: list[LLMExecutionSpecCandidate] = []
    scores: list[LLMRoutePlanningScore] = []
    raw_artifacts: list[LLMRoutePlanningRawArtifact] = []
    warnings: list[str] = []
    repaired_count = 0

    for index, substrate in enumerate(selected, start=1):
        variant = variant_by_id.get(substrate.source_variant_id)
        opportunity = opportunity_by_id.get(substrate.source_opportunity_id)
        standard = standard_by_id.get(substrate.substrate_id)
        retrieval = retrieval_by_pair.get(substrate.source_pair_id)
        if variant is None or opportunity is None or standard is None or retrieval is None:
            raise LLMRoutePlanningError(
                f"Source metadata is incomplete for substrate {substrate.substrate_id}."
            )
        route_id = f"llm-route-{report_number:04d}-{index:03d}"
        spec_id = f"llm-execution-spec-{report_number:04d}-{index:03d}"
        try:
            response = planner.plan_route(
                prompt_id=f"{report_id}-prompt-{index:03d}",
                substrate_payload=substrate.model_dump(mode="json"),
                source_metadata_payload={
                    "variant": variant.model_dump(mode="json"),
                    "opportunity": opportunity.model_dump(mode="json"),
                    "standard_scientific_substrate": standard.model_dump(mode="json"),
                },
                retrieval_context_payload=retrieval.model_dump(mode="json"),
            )
        except (AdapterError, ValueError) as exc:
            raise LLMRoutePlanningError(
                f"LLM route planning failed for {substrate.substrate_id}: {exc}"
            ) from exc

        rejection_reasons = list(response.rejection_reasons)
        accepted_route_id: str | None = None
        accepted_spec_id: str | None = None
        if response.accepted is not None and not rejection_reasons:
            proposal = response.accepted
            try:
                decision = LLMRouteDecisionCandidate(
                    route_id=route_id,
                    run_id=run_id,
                    source_substrate_id=substrate.substrate_id,
                    source_idea_node_id=substrate.source_idea_node_id,
                    domain_id=substrate.domain_id,
                    method_id=substrate.method_id,
                    **proposal.decision.model_dump(mode="python"),
                )
                spec = LLMExecutionSpecCandidate(
                    spec_id=spec_id,
                    route_id=route_id,
                    source_substrate_id=substrate.substrate_id,
                    **proposal.execution_spec.model_dump(mode="python"),
                )
                score = LLMRoutePlanningScore(
                    route_id=route_id,
                    substrate_id=substrate.substrate_id,
                    **proposal.score.model_dump(mode="python"),
                )
            except ValidationError as exc:
                rejection_reasons.append(str(exc))
            else:
                decisions.append(decision)
                specs.append(spec)
                scores.append(score)
                accepted_route_id = route_id
                accepted_spec_id = spec_id
                repaired_count += int(bool(response.repair_actions))
                if response.repair_actions:
                    warnings.append(
                        f"Applied policy-only claim-boundary repairs for {substrate.substrate_id}: "
                        + "; ".join(response.repair_actions)
                    )
        if rejection_reasons:
            warnings.append(
                f"Rejected route/spec output for {substrate.substrate_id}: "
                + "; ".join(rejection_reasons)
            )
        raw_id = f"llm-route-planning-raw-{raw_number + index - 1:04d}"
        raw_artifacts.append(
            LLMRoutePlanningRawArtifact(
                raw_artifact_id=raw_id,
                run_id=run_id,
                source_substrate_id=substrate.substrate_id,
                backend_name=planner.backend_name,
                model=planner.model,
                prompt=response.prompt,
                raw_response=response.raw_response,
                accepted_route_id_optional=accepted_route_id,
                accepted_spec_id_optional=accepted_spec_id,
                rejection_reasons=rejection_reasons,
                fallback_used=planner.fallback_used,
            )
        )

    rejected_count = len(selected) - len(decisions)
    if rejected_count and config.require_non_fake_backends:
        raise LLMRoutePlanningError(
            "Strict LLM route planning requires one valid route/spec for every selected substrate: "
            + " | ".join(warnings)
        )
    if not decisions:
        raise LLMRoutePlanningError("No valid route/spec plans remained after validation.")

    backend_records = [
        _route_backend_record(report_id, planner, [item.raw_artifact_id for item in raw_artifacts]),
        _design_backend_record(report_id, planner, [item.spec_id for item in specs]),
        _validation_backend_record(report_id, [item.spec_id for item in specs]),
    ]
    production = evaluate_production_mode(
        run_id=run_id,
        records=[*substrate_report.backend_records, *backend_records],
        policy=ProductionModePolicy(
            require_non_fake_backends=config.require_non_fake_backends,
            fail_on_silent_fallback=config.fail_on_silent_fallback,
        ),
        expected_stage_kinds=[
            ScientificStageKind.SUBSTRATE_CONSTRUCTION,
            ScientificStageKind.BRANCH_ROUTING,
            ScientificStageKind.EXPERIMENT_DESIGN,
            ScientificStageKind.SPEC_VALIDATION,
        ],
        report_id=f"{report_id}-production-evaluation",
    )
    if config.require_non_fake_backends and production.blocking_violation_count:
        details = "; ".join(item.message for item in production.violations)
        raise LLMRoutePlanningError(f"Strict LLM route planning blocked: {details}")

    compatibility_route_plan = _compatibility_route_plan(
        run_id=run_id,
        report_number=report_number,
        substrate_report_path=_relative(root_path, substrate_report_path),
        decisions=decisions,
        specs=specs,
        candidates=candidate_by_id,
        variants=variant_by_id,
        backend_records=backend_records,
    )
    compatibility_specs = _compatibility_specs(
        run_id=run_id,
        report_number=report_number,
        decisions=decisions,
        specs=specs,
        candidates=candidate_by_id,
        variants=variant_by_id,
        backend_records=backend_records,
    )
    compatibility_spec_report = _compatibility_spec_report(
        run_id=run_id,
        report_number=report_number,
        route_plan=compatibility_route_plan,
        specs=compatibility_specs,
        backend_records=backend_records,
    )
    route_counts = Counter(item.route_type.value for item in decisions)
    report = LLMRoutePlanningReport(
        run_id=run_id,
        report_id=report_id,
        planning_status="completed_with_warnings" if warnings else "completed",
        config=config,
        source_substrate_report_path=_relative(root_path, substrate_report_path),
        source_scientific_substrate_paths=substrate_report.scientific_substrate_paths,
        source_variant_report_path=_relative(root_path, variance_path),
        source_deep_opportunity_report_path=_relative(root_path, deep_path),
        selected_substrate_count=len(selected),
        route_decision_count=len(decisions),
        execution_spec_count=len(specs),
        rejected_output_count=rejected_count,
        repaired_output_count=repaired_count,
        route_type_coverage=len(route_counts),
        route_type_counts=dict(sorted(route_counts.items())),
        raw_artifact_paths=[
            f"runs/{run_id}/reports/{item.raw_artifact_id}.json" for item in raw_artifacts
        ],
        compatibility_branch_route_plan_path=(
            f"runs/{run_id}/reports/{compatibility_route_plan.routing_id}.json"
        ),
        compatibility_route_execution_specs_path=(
            f"runs/{run_id}/reports/{compatibility_spec_report.report_id}.json"
        ),
        decisions=decisions,
        execution_specs=specs,
        scores=scores,
        backend_records=backend_records,
        warnings=warnings,
        production_ready=(
            config.require_non_fake_backends and not production.blocking_violation_count
        ),
    )
    persistence = _persist(
        report=report,
        raw_artifacts=raw_artifacts,
        compatibility_route_plan=compatibility_route_plan,
        compatibility_spec_report=compatibility_spec_report,
        compatibility_specs=compatibility_specs,
        store=store,
        ledger=ledger,
    )
    by_id = {item.id: item for item in persistence.artifacts}
    return LLMRoutePlanningResult(
        run_id=run_id,
        report=report,
        compatibility_route_plan=compatibility_route_plan,
        compatibility_spec_report=compatibility_spec_report,
        persistence=persistence,
        report_artifact=by_id[report_id],
        markdown_artifact=by_id[f"{report_id}-markdown"],
    )


def inspect_llm_routes(
    *, run_id: str, root: str | Path = "."
) -> LLMRoutePlanningInspectionReport:
    reports = Path(root) / "runs" / run_id / "reports"
    path = _latest_matching(reports, _REPORT_RE)
    if path is None:
        return LLMRoutePlanningInspectionReport(
            run_id=run_id,
            llm_route_planning_present=False,
        )
    report = _load_route_report(path)
    return LLMRoutePlanningInspectionReport(
        run_id=run_id,
        llm_route_planning_present=True,
        latest_report_id_optional=report.report_id,
        planning_status_optional=report.planning_status,
        selected_substrate_count=report.selected_substrate_count,
        route_decision_count=report.route_decision_count,
        execution_spec_count=report.execution_spec_count,
        rejected_output_count=report.rejected_output_count,
        repaired_output_count=report.repaired_output_count,
        route_type_coverage=report.route_type_coverage,
        route_type_counts=report.route_type_counts,
        decisions=report.decisions,
        execution_specs=report.execution_specs,
        scores=report.scores,
        backend_records=report.backend_records,
        warnings=report.warnings,
        production_ready=report.production_ready,
    )


def render_llm_route_text(report: LLMRoutePlanningInspectionReport) -> str:
    return "\n".join(
        [
            "LLM route planning: "
            f"{'present' if report.llm_route_planning_present else 'absent'}",
            f"Status: {report.planning_status_optional or 'not available'}",
            f"Selected substrates: {report.selected_substrate_count}",
            f"Route decisions/specs: {report.route_decision_count}/{report.execution_spec_count}",
            f"Rejected/repaired: {report.rejected_output_count}/{report.repaired_output_count}",
            f"Route type coverage: {report.route_type_coverage}",
            f"Production ready: {str(report.production_ready).lower()}",
            "publication_ready=false",
        ]
    )


def render_llm_route_markdown(report: LLMRoutePlanningReport) -> str:
    lines = [
        "# LLM Route and Execution-Spec Planning",
        "",
        f"Status: `{report.planning_status}`",
        f"Routes/specs: `{report.route_decision_count}/{report.execution_spec_count}`",
        "",
        "| Substrate | Route | Score |",
        "|---|---|---:|",
    ]
    score_by_route = {item.route_id: item.final_score for item in report.scores}
    lines.extend(
        f"| {item.source_substrate_id} | {item.route_type.value} | "
        f"{score_by_route.get(item.route_id, 0.0):.3f} |"
        for item in report.decisions
    )
    lines.extend(
        [
            "",
            "Allowed evidence labels are future execution boundaries only. This plan executes "
            "nothing and creates no scientific evidence or publication readiness.",
            "",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def _compatibility_route_plan(
    *,
    run_id: str,
    report_number: int,
    substrate_report_path: str,
    decisions: list[LLMRouteDecisionCandidate],
    specs: list[LLMExecutionSpecCandidate],
    candidates: dict[str, LLMScientificSubstrateCandidate],
    variants: dict[str, Any],
    backend_records: list[StageBackendRecord],
) -> BranchRoutePlan:
    spec_by_route = {item.route_id: item for item in specs}
    converted = []
    for item in decisions:
        candidate = candidates[item.source_substrate_id]
        variant = variants[candidate.source_variant_id]
        command = _COMMANDS.get(item.route_type)
        spec = spec_by_route[item.route_id]
        converted.append(
            BranchRouteDecision(
                route_id=item.route_id,
                run_id=run_id,
                substrate_id=item.source_substrate_id,
                idea_node_id_optional=item.source_idea_node_id,
                method_lens=item.method_id.replace("_", " ").replace("-", " "),
                branch_family=variant.variant_family,
                route_type=item.route_type,
                route_confidence=item.route_confidence,
                reason=item.scientific_reason,
                required_artifacts=item.required_artifacts,
                expected_outputs=spec.expected_artifacts,
                execution_hint=BranchRouteExecutionHint(
                    command_class_optional=command,
                    ready_for_execution=False,
                    suggested_arguments=[],
                    safety_notes=[
                        "M101 creates a plan only; M102 must generate and validate execution code.",
                        "Allowed labels are boundaries and are not evidence results.",
                    ],
                    executes_now=False,
                    network_required=spec.requires_literature_retrieval,
                ),
                defer_or_reject_reason_optional=item.defer_or_reject_reason_optional,
            )
        )
    counts = Counter(item.route_type.value for item in converted)
    deferred = counts[BranchRouteType.DEFER_INSUFFICIENT_SUBSTRATE.value]
    rejected = counts[BranchRouteType.REJECT_FALSE_BRIDGE.value]
    return BranchRoutePlan(
        run_id=run_id,
        routing_id=f"branch-route-plan-llm-{report_number:04d}",
        routing_backend="llm_openai",
        source_scientific_substrate_build_path=substrate_report_path,
        substrate_count=len(converted),
        route_count=len(converted),
        route_type_counts=dict(sorted(counts.items())),
        routed_count=len(converted) - deferred - rejected,
        deferred_count=deferred,
        rejected_count=rejected,
        synthetic_experiment_count=counts[BranchRouteType.SYNTHETIC_EXPERIMENT.value],
        benchmark_tournament_count=counts[BranchRouteType.BENCHMARK_TOURNAMENT.value],
        counterexample_search_count=counts[BranchRouteType.COUNTEREXAMPLE_SEARCH.value],
        applied_math_reduction_count=counts[BranchRouteType.APPLIED_MATH_REDUCTION.value],
        proof_plan_count=counts[BranchRouteType.PROOF_PLAN.value],
        decisions=converted,
        backend_records=backend_records,
        warnings=["Compatibility view of non-fake LLM route planning; no action was executed."],
    )


def _compatibility_specs(
    *,
    run_id: str,
    report_number: int,
    decisions: list[LLMRouteDecisionCandidate],
    specs: list[LLMExecutionSpecCandidate],
    candidates: dict[str, LLMScientificSubstrateCandidate],
    variants: dict[str, Any],
    backend_records: list[StageBackendRecord],
) -> list[RouteExecutionSpec]:
    decision_by_route = {item.route_id: item for item in decisions}
    converted = []
    for index, item in enumerate(specs, start=1):
        decision = decision_by_route[item.route_id]
        candidate = candidates[item.source_substrate_id]
        variant = variants[candidate.source_variant_id]
        converted.append(
            RouteExecutionSpec(
                spec_id=f"route-execution-spec-llm-{report_number:04d}-{index:03d}",
                run_id=run_id,
                route_id=item.route_id,
                substrate_id=item.source_substrate_id,
                route_type=item.route_type,
                method_lens=decision.method_id.replace("_", " ").replace("-", " "),
                branch_family=variant.variant_family,
                execution_backend=item.execution_backend_required,
                input_contract=item.input_contract,
                output_contract=item.output_contract,
                expected_artifacts=item.expected_artifacts,
                allowed_evidence_labels=item.allowed_evidence_labels,
                forbidden_claims=item.forbidden_claims,
                backend_records=backend_records,
            )
        )
    return converted


def _compatibility_spec_report(
    *,
    run_id: str,
    report_number: int,
    route_plan: BranchRoutePlan,
    specs: list[RouteExecutionSpec],
    backend_records: list[StageBackendRecord],
) -> RouteExecutionReport:
    counts = Counter(item.route_type for item in specs)
    return RouteExecutionReport(
        run_id=run_id,
        report_id=f"route-execution-specs-llm-{report_number:04d}",
        report_status=RouteExecutionStatus.SPEC_CREATED,
        source_branch_route_plan_path=f"runs/{run_id}/reports/{route_plan.routing_id}.json",
        route_count=len(specs),
        spec_count=len(specs),
        executed_count=0,
        deferred_count=0,
        failed_count=0,
        result_count=0,
        synthetic_experiment_count=counts[BranchRouteType.SYNTHETIC_EXPERIMENT],
        benchmark_tournament_count=counts[BranchRouteType.BENCHMARK_TOURNAMENT],
        applied_math_reduction_count=counts[BranchRouteType.APPLIED_MATH_REDUCTION],
        evidence_label_counts={},
        unsupported_route_counts={},
        spec_paths=[f"runs/{run_id}/reports/{item.spec_id}.json" for item in specs],
        result_paths=[],
        specs=specs,
        results=[],
        backend_records=backend_records,
        warnings=["M101 compatibility specs are not execution results and contain no evidence."],
    )


def _persist(
    *,
    report: LLMRoutePlanningReport,
    raw_artifacts: list[LLMRoutePlanningRawArtifact],
    compatibility_route_plan: BranchRoutePlan,
    compatibility_spec_report: RouteExecutionReport,
    compatibility_specs: list[RouteExecutionSpec],
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PersistenceResult:
    metadata = _metadata("llm_route_planning")
    artifact_specs = [
        ArtifactWriteSpec(item.raw_artifact_id, ArtifactType.REPORT, item, "json", metadata)
        for item in raw_artifacts
    ]
    artifact_specs.extend(
        ArtifactWriteSpec(item.spec_id, ArtifactType.REPORT, item, "json", metadata)
        for item in compatibility_specs
    )
    artifact_specs.extend(
        [
            ArtifactWriteSpec(
                compatibility_route_plan.routing_id,
                ArtifactType.REPORT,
                compatibility_route_plan,
                "json",
                metadata,
            ),
            ArtifactWriteSpec(
                compatibility_spec_report.report_id,
                ArtifactType.REPORT,
                compatibility_spec_report,
                "json",
                metadata,
            ),
            ArtifactWriteSpec(report.report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report.report_id}-markdown",
                ArtifactType.REPORT,
                render_llm_route_markdown(report),
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
        artifact_specs=artifact_specs,
        action_type=ControllerActionType.LLM_ROUTE_PLANNING_WRITTEN,
        commit_payload={
            "run_id": report.run_id,
            "report_id": report.report_id,
            "route_decision_count": report.route_decision_count,
            "execution_spec_count": report.execution_spec_count,
            "production_ready": report.production_ready,
            "publication_ready": False,
        },
    )


def _route_backend_record(
    report_id: str, planner: RoutePlanningClient, raw_ids: list[str]
) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-route-planning",
        stage_kind=ScientificStageKind.BRANCH_ROUTING,
        backend_kind=planner.backend_kind,
        backend_name=planner.backend_name,
        is_scientific_generation=False,
        is_scientific_judgment=True,
        is_execution_or_verification=False,
        reason="Scientific route choice and route rationale come from the recorded non-fake LLM.",
        artifact_ids=[report_id, *raw_ids],
        fallback_used=planner.fallback_used,
        fallback_disclosed=planner.fallback_disclosed,
    )


def _design_backend_record(
    report_id: str, planner: RoutePlanningClient, spec_ids: list[str]
) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-execution-spec-planning",
        stage_kind=ScientificStageKind.EXPERIMENT_DESIGN,
        backend_kind=planner.backend_kind,
        backend_name=planner.backend_name,
        is_scientific_generation=True,
        is_scientific_judgment=True,
        is_execution_or_verification=False,
        reason=(
            "Baselines, controls, metrics, robustness checks, failure criteria, and execution "
            "contracts come from the recorded non-fake LLM."
        ),
        artifact_ids=[report_id, *spec_ids],
        fallback_used=planner.fallback_used,
        fallback_disclosed=planner.fallback_disclosed,
    )


def _validation_backend_record(report_id: str, spec_ids: list[str]) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-spec-validation",
        stage_kind=ScientificStageKind.SPEC_VALIDATION,
        backend_kind=BackendKind.LOCAL_EXECUTION,
        backend_name="route_spec_contract_validator",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=True,
        allowed_in_production=True,
        reason=(
            "Local validation enforces schema completeness, allowed-label policy, executable "
            "contracts, and authority boundaries without choosing scientific content."
        ),
        artifact_ids=[report_id, *spec_ids],
    )


def _load_latest_substrate_report(
    *, run_id: str, reports: Path
) -> tuple[Path, LLMSubstrateConstructionReport]:
    path = _latest_matching(reports, _SUBSTRATE_REPORT_RE)
    if path is None:
        raise LLMRoutePlanningError(f"No M100 substrate report found for run_id={run_id}.")
    try:
        report = LLMSubstrateConstructionReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise LLMRoutePlanningError(f"Could not load M100 substrate report: {exc}") from exc
    if report.run_id != run_id:
        raise LLMRoutePlanningError("M100 substrate report run_id is inconsistent.")
    return path, report


def _load_variance(
    root_path: Path, report: LLMSubstrateConstructionReport
) -> tuple[Path, LLMVarianceGenerationReport]:
    path = root_path / report.source_variance_report_path
    try:
        return path, LLMVarianceGenerationReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise LLMRoutePlanningError(f"Could not load source variance report: {exc}") from exc


def _load_deep(
    root_path: Path, report: LLMSubstrateConstructionReport
) -> tuple[Path, DeepOpportunityDiscoveryReport]:
    path = root_path / report.source_deep_opportunity_report_path
    try:
        return path, DeepOpportunityDiscoveryReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise LLMRoutePlanningError(f"Could not load deep opportunity report: {exc}") from exc


def _load_retrieval_contexts(
    root_path: Path, report: DeepOpportunityDiscoveryReport
) -> dict[str, RetrievalContext]:
    result = {}
    for relative_path in report.retrieval_context_paths:
        path = root_path / relative_path
        try:
            context = RetrievalContext.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise LLMRoutePlanningError(f"Could not load retrieval context {path}: {exc}") from exc
        result[context.source_pair_id] = context
    return result


def _load_standard_substrates(
    root_path: Path, report: LLMSubstrateConstructionReport
) -> dict[str, ScientificSubstrate]:
    result = {}
    for relative_path in report.scientific_substrate_paths:
        path = root_path / relative_path
        try:
            substrate = ScientificSubstrate.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise LLMRoutePlanningError(
                f"Could not load ScientificSubstrate {path}: {exc}"
            ) from exc
        result[substrate.substrate_id] = substrate
    return result


def _load_route_report(path: Path) -> LLMRoutePlanningReport:
    try:
        return LLMRoutePlanningReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise LLMRoutePlanningError(f"Could not load LLM route report: {exc}") from exc


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


def _relative(root_path: Path, path: Path) -> str:
    try:
        return path.relative_to(root_path).as_posix()
    except ValueError:
        return path.as_posix()


def _metadata(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "artifact_role": "scientific_workflow_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "creates_real_world_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }


__all__ = [
    "LLMRoutePlanningError",
    "LLMRoutePlanningResult",
    "inspect_llm_routes",
    "plan_llm_routes",
    "render_llm_route_markdown",
    "render_llm_route_text",
]
