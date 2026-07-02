"""Deterministic routing from experiment gaps to approved local templates."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factori.artifacts import ArtifactStore
from factori.autonomous_evidence_plan import latest_autonomous_evidence_gap_plan_path
from factori.claim_evidence import (
    BOUNDED_EMPIRICAL_CLAIM_CLASSES,
    BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID,
    latest_claim_evidence_map_path,
)
from factori.gap_attempts import find_existing_planned_spec, gap_fingerprint_for_plan_item
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.rerun_policy import validate_ledger_tip
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AutonomousEvidenceGapPlan,
    AutonomousEvidenceGapPlanItem,
    ClaimEvidenceMap,
    ClaimEvidenceMapLink,
    ControllerActionType,
    ExperimentGapRoutingIndex,
    ExperimentGapRoutingItem,
    ExperimentGapRoutingReport,
    ExperimentTemplate,
    ExperimentTemplateRegistry,
    PlannedExperimentSpec,
    SandboxBudgetPolicy,
    SandboxBudgetReport,
)

_ROUTING_BACKENDS = {"deterministic", "fake", "openai"}
_SYNTHETIC_CALIBRATION_TEMPLATE_ID = "synthetic_calibration_v1"
_DEFAULT_TEMPLATE_BUNDLE = "tests/fixtures/experiments/bundles/synthetic_calibration"
_EXPERIMENT_GAP_TYPES = {"needs_python_experiment"}
_FORBIDDEN_AUTHORITY_WORDS = (
    "publication",
    "publication-ready",
    "validated",
    "validation",
    "novelty",
    "correctness",
)


class ExperimentGapRoutingError(RuntimeError):
    """Raised when experiment-gap routing cannot proceed safely."""


@dataclass(frozen=True)
class ExperimentGapRoutingResult:
    """Persisted experiment-gap routing report and derived index."""

    run_id: str
    report: ExperimentGapRoutingReport
    index: ExperimentGapRoutingIndex
    registry: ExperimentTemplateRegistry
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef
    index_artifact: ArtifactRef
    registry_artifact: ArtifactRef
    reviewer_summary_artifact: ArtifactRef
    reviewer_summary_markdown_artifact: ArtifactRef


def build_default_experiment_template_registry(
    *,
    run_id: str,
    root: str | Path = ".",
    registry_id: str = "experiment-template-registry",
) -> ExperimentTemplateRegistry:
    """Build the deterministic approved local experiment template registry."""
    root_path = Path(root)
    bundle = (root_path / _DEFAULT_TEMPLATE_BUNDLE).resolve()
    templates: list[ExperimentTemplate] = []
    if bundle.is_dir():
        templates.append(
            ExperimentTemplate(
                template_id=_SYNTHETIC_CALIBRATION_TEMPLATE_ID,
                template_name="Synthetic calibration fixture",
                template_family="synthetic_calibration",
                bundle_path=_DEFAULT_TEMPLATE_BUNDLE,
                supported_gap_types=["needs_python_experiment"],
                supported_claim_classes=[
                    "experiment_claim",
                    "pipeline_status_claim",
                    "demonstration_claim",
                    "result_claim",
                    "external_factual_claim",
                    *sorted(BOUNDED_EMPIRICAL_CLAIM_CLASSES),
                ],
                required_inputs=[
                    "target claim ID",
                    "target section",
                    "bounded hypothesis or question",
                ],
                default_metrics=[
                    "baseline_mae",
                    "method_mae",
                    "bounded_improvement",
                ],
                default_baselines=["synthetic identity baseline"],
                dependency_profile=[],
                network_required=False,
                arbitrary_code_required=False,
            )
        )
    return ExperimentTemplateRegistry(
        run_id=run_id,
        registry_id=registry_id,
        templates=templates,
        network_required_template_count=sum(template.network_required for template in templates),
        arbitrary_code_required_template_count=sum(
            template.arbitrary_code_required for template in templates
        ),
    )


def route_experiment_gaps(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    routing_backend: str = "deterministic",
    sandbox_budget_remaining: int | None = None,
) -> ExperimentGapRoutingResult:
    """Persist deterministic experiment-gap routing and sandbox-compatible specs."""
    if routing_backend not in _ROUTING_BACKENDS:
        raise ExperimentGapRoutingError(
            "experiment-gap routing backend must be deterministic, fake, or openai"
        )
    if routing_backend == "openai":
        raise ExperimentGapRoutingError(
            "OpenAI experiment-gap routing is schema-gated but not implemented in M73."
        )
    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    reports = run_path / "reports"
    if not run_path.is_dir():
        raise ExperimentGapRoutingError(f"No run directory found for run_id={run_id}.")
    ledger_check = validate_ledger_tip(run_id, root=root_path)
    if ledger_check.blocking_findings:
        raise ExperimentGapRoutingError(
            "Ledger validation has blocking findings; experiment-gap routing cannot append safely."
        )

    routing_number = _next_routing_number(reports)
    routing_id = f"experiment-gap-routing-{routing_number:04d}"
    plan_path = latest_autonomous_evidence_gap_plan_path(root_path, run_id)
    map_path = latest_claim_evidence_map_path(root_path, run_id)
    if plan_path is None or map_path is None:
        raise ExperimentGapRoutingError(
            "Experiment-gap routing requires a claim-evidence map and autonomous evidence plan."
        )
    plan = _read_plan(plan_path)
    claim_map = _read_claim_map(map_path)
    registry_id = f"experiment-template-registry-{routing_number:04d}"
    registry = build_default_experiment_template_registry(
        run_id=run_id,
        root=root_path,
        registry_id=registry_id,
    )
    links_by_id = {link.claim_id: link for link in claim_map.links}
    candidate_items = [
        item
        for item in plan.plan_items
        if item.gap_type in _EXPERIMENT_GAP_TYPES and item.target_claim_id_optional
    ]
    remaining_budget = sandbox_budget_remaining
    report_items: list[ExperimentGapRoutingItem] = []
    specs: list[ArtifactWriteSpec] = []
    for index, item in enumerate(candidate_items, start=1):
        link = links_by_id.get(item.target_claim_id_optional or "")
        routed, created_spec = _route_item(
            run_id=run_id,
            root=root_path,
            routing_id=routing_id,
            index=index,
            item=item,
            link=link,
            registry=registry,
            remaining_budget=remaining_budget,
        )
        report_items.append(routed)
        if created_spec is not None:
            specs.append(created_spec)
            if remaining_budget is not None and remaining_budget > 0:
                remaining_budget -= 1

    report = ExperimentGapRoutingReport(
        run_id=run_id,
        routing_id=routing_id,
        routing_backend=routing_backend,
        routing_status=(
            "routed"
            if any(item.routing_status == "routed" for item in report_items)
            else "no_routable_gaps"
        ),
        claim_evidence_map_path=map_path.relative_to(root_path).as_posix(),
        autonomous_plan_path=plan_path.relative_to(root_path).as_posix(),
        template_registry_path=f"runs/{run_id}/reports/{registry_id}.json",
        gap_count=len(candidate_items),
        routed_gap_count=sum(item.routing_status == "routed" for item in report_items),
        unrouted_gap_count=sum(item.routing_status != "routed" for item in report_items),
        created_experiment_spec_count=len(specs),
        bounded_empirical_gaps_routed=sum(
            item.routing_status == "routed"
            and item.target_claim_id == BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID
            for item in report_items
        ),
        synthetic_template_specs_created=sum(
            item.routing_status == "routed"
            and item.selected_template_id_optional == _SYNTHETIC_CALIBRATION_TEMPLATE_ID
            for item in report_items
        ),
        selected_template_count=len(
            {
                item.selected_template_id_optional
                for item in report_items
                if item.selected_template_id_optional
            }
        ),
        rejected_template_count=sum(
            1 for item in report_items if item.routing_status == "rejected_policy"
        ),
        items=report_items,
        requires_human_intervention=False,
    )
    return _persist_routing(
        report=report,
        registry=registry,
        root=root_path,
        store=store,
        ledger=ledger,
        routing_number=routing_number,
        specs=specs,
    )


def inspect_experiment_gap_routing(*, run_id: str, root: str | Path = ".") -> dict[str, Any]:
    """Inspect the latest experiment-gap routing report without mutation."""
    root_path = Path(root)
    report, index = latest_experiment_gap_routing_report(root_path, run_id)
    if report is None or index is None:
        raise ExperimentGapRoutingError(
            f"No experiment-gap routing report found for run_id={run_id}."
        )
    return {
        **report.model_dump(mode="json"),
        **experiment_gap_routing_summary_fields(report, index),
        "experiment_gap_routing_report_path": index.latest_report_path,
        "experiment_gap_routing_index": index.model_dump(mode="json"),
    }


def latest_experiment_gap_routing_report(
    root: Path,
    run_id: str,
) -> tuple[ExperimentGapRoutingReport | None, ExperimentGapRoutingIndex | None]:
    """Load the latest immutable experiment-gap routing report and index."""
    reports = root / "runs" / run_id / "reports"
    indexes = sorted(
        path
        for path in reports.glob("experiment-gap-routing-index-*.json")
        if not path.name.endswith(".meta.json")
    )
    if not indexes:
        return None, None
    try:
        index = ExperimentGapRoutingIndex.model_validate_json(
            indexes[-1].read_text(encoding="utf-8")
        )
        report = ExperimentGapRoutingReport.model_validate_json(
            (root / index.latest_report_path).read_text(encoding="utf-8")
        )
    except (OSError, ValidationError):
        return None, None
    return report, index


def experiment_gap_routing_summary_fields(
    report: ExperimentGapRoutingReport | None,
    index: ExperimentGapRoutingIndex | None = None,
) -> dict[str, Any]:
    """Return stable inspect, lint, and reviewer summary fields."""
    if report is None:
        return {
            "experiment_gap_routing_present": False,
            "experiment_gap_routing_count": 0,
            "routed_experiment_gap_count": 0,
            "unrouted_experiment_gap_count": 0,
            "created_experiment_spec_count": 0,
            "routed_empirical_gap_count": 0,
            "bounded_empirical_gaps_routed": 0,
            "synthetic_template_specs_created": 0,
        }
    return {
        "experiment_gap_routing_present": True,
        "experiment_gap_routing_count": index.routing_count if index else 1,
        "routed_experiment_gap_count": (
            index.routed_gap_count if index else report.routed_gap_count
        ),
        "unrouted_experiment_gap_count": (
            index.unrouted_gap_count if index else report.unrouted_gap_count
        ),
        "created_experiment_spec_count": (
            index.created_experiment_spec_count
            if index
            else report.created_experiment_spec_count
        ),
        "routed_empirical_gap_count": (
            index.bounded_empirical_gaps_routed
            if index
            else report.bounded_empirical_gaps_routed
        ),
        "bounded_empirical_gaps_routed": (
            index.bounded_empirical_gaps_routed
            if index
            else report.bounded_empirical_gaps_routed
        ),
        "synthetic_template_specs_created": (
            index.synthetic_template_specs_created
            if index
            else report.synthetic_template_specs_created
        ),
    }


def render_experiment_gap_routing_markdown(report: ExperimentGapRoutingReport) -> str:
    """Render a concise non-evidence experiment routing report."""
    lines = [
        "# Experiment Gap Routing",
        "",
        f"Run ID: `{report.run_id}`",
        f"Routing ID: `{report.routing_id}`",
        f"Backend: `{report.routing_backend}`",
        f"Status: `{report.routing_status}`",
        f"Gaps routed/unrouted: `{report.routed_gap_count}/{report.unrouted_gap_count}`",
        f"Experiment specs created: `{report.created_experiment_spec_count}`",
        f"Bounded empirical gaps routed: `{report.bounded_empirical_gaps_routed}`",
        f"Synthetic template specs created: `{report.synthetic_template_specs_created}`",
        "",
        "## Items",
    ]
    for item in report.items:
        lines.append(
            f"- `{item.item_id}` / `{item.target_claim_id}`: "
            f"`{item.routing_status}` via `{item.selected_template_id_optional or 'none'}`"
        )
    lines.extend(
        [
            "",
            "## Non-Evidence Boundary",
            "- Template selection is workflow routing only.",
            "- Planned experiment specs are not completed experiment evidence.",
            "- Completed sandbox artifacts support only mapped bounded result claims.",
            "- publication_ready: false",
            "- creates_scientific_validation: false",
            "- implies_publication_readiness: false",
            "- is_verification_evidence: false",
            "",
        ]
    )
    return "\n".join(lines)


def build_sandbox_budget_report(
    *,
    run_id: str,
    budget_id: str,
    policy: SandboxBudgetPolicy,
    runs_used: int,
    failures_used: int,
    experiment_artifacts_created: int,
    seconds_used: int = 0,
    deferred_reason: str | None = None,
) -> SandboxBudgetReport:
    """Build deterministic sandbox budget accounting for a loop."""
    remaining = {
        "sandbox_runs": max(policy.max_sandbox_runs_per_loop - runs_used, 0),
        "sandbox_failures": max(policy.max_sandbox_failures_per_loop - failures_used, 0),
        "sandbox_seconds": max(policy.max_sandbox_seconds_per_loop - seconds_used, 0),
        "experiment_artifacts": max(
            policy.max_experiment_artifacts_per_loop - experiment_artifacts_created,
            0,
        ),
    }
    used = {
        "sandbox_runs": runs_used,
        "sandbox_failures": failures_used,
        "sandbox_seconds": seconds_used,
        "experiment_artifacts": experiment_artifacts_created,
    }
    exhausted = any(value == 0 for value in remaining.values())
    return SandboxBudgetReport(
        run_id=run_id,
        budget_id=budget_id,
        policy=policy,
        sandbox_budget_used=used,
        sandbox_budget_remaining=remaining,
        budget_exhausted=exhausted,
        deferred_reason_optional=deferred_reason if exhausted else None,
    )


def latest_sandbox_budget_report(root: Path, run_id: str) -> SandboxBudgetReport | None:
    """Load the latest sandbox budget report if present."""
    reports = root / "runs" / run_id / "reports"
    paths = sorted(
        path
        for path in reports.glob("sandbox-budget-report-*.json")
        if not path.name.endswith(".meta.json")
    )
    if not paths:
        return None
    try:
        return SandboxBudgetReport.model_validate_json(paths[-1].read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def sandbox_budget_summary_fields(report: SandboxBudgetReport | None) -> dict[str, Any]:
    """Return stable lint/inspect fields for sandbox budget accounting."""
    if report is None:
        return {
            "sandbox_budget_exhausted": False,
            "sandbox_budget_runs_used": 0,
            "sandbox_budget_runs_remaining": 0,
            "sandbox_budget_remaining": {},
        }
    return {
        "sandbox_budget_exhausted": report.budget_exhausted,
        "sandbox_budget_runs_used": int(report.sandbox_budget_used.get("sandbox_runs", 0)),
        "sandbox_budget_runs_remaining": int(
            report.sandbox_budget_remaining.get("sandbox_runs", 0)
        ),
        "sandbox_budget_remaining": dict(report.sandbox_budget_remaining),
    }


def render_sandbox_budget_markdown(report: SandboxBudgetReport) -> str:
    """Render a concise sandbox budget report."""
    return "\n".join(
        [
            "# Sandbox Budget Report",
            "",
            f"Run ID: `{report.run_id}`",
            f"Budget ID: `{report.budget_id}`",
            f"Budget exhausted: `{str(report.budget_exhausted).lower()}`",
            f"Runs used/remaining: `{report.sandbox_budget_used.get('sandbox_runs', 0)}/"
            f"{report.sandbox_budget_remaining.get('sandbox_runs', 0)}`",
            "",
            "This report is local execution accounting only and is not evidence.",
            "",
        ]
    )


def _route_item(
    *,
    run_id: str,
    root: Path,
    routing_id: str,
    index: int,
    item: AutonomousEvidenceGapPlanItem,
    link: ClaimEvidenceMapLink | None,
    registry: ExperimentTemplateRegistry,
    remaining_budget: int | None,
) -> tuple[ExperimentGapRoutingItem, ArtifactWriteSpec | None]:
    gap_fp = item.gap_fingerprint or gap_fingerprint_for_plan_item(run_id=run_id, item=item)
    common = {
        "item_id": f"routing-item-{index:04d}",
        "target_claim_id": item.target_claim_id_optional or "unknown",
        "target_section_optional": item.target_section_optional,
        "gap_fingerprint": gap_fp,
        "claim_class": link.claim_class if link else "unknown",
        "gap_type": item.gap_type,
        "safety_notes": [
            "Experiment routing selects only approved local templates.",
            "Template routing is not experiment evidence.",
            "The routed spec must still pass uv_local sandbox and intake validation.",
        ],
    }
    if remaining_budget is not None and remaining_budget <= 0:
        return (
            ExperimentGapRoutingItem(
                **common,
                routing_decision="defer",
                routing_status="deferred_budget_exhausted",
                rejection_reason_optional="Sandbox run budget is exhausted.",
            ),
            None,
        )
    if link is None:
        return (
            ExperimentGapRoutingItem(
                **common,
                routing_decision="defer",
                routing_status="deferred_requires_inputs",
                rejection_reason_optional="Target claim is missing from the claim-evidence map.",
            ),
            None,
        )
    if _forbidden_claim(link):
        return (
            ExperimentGapRoutingItem(
                **common,
                routing_decision="reject",
                routing_status="rejected_policy",
                rejection_reason_optional=(
                    "Forbidden novelty, validation, correctness, or publication-readiness "
                    "claims are not routed to Python experiments."
                ),
            ),
            None,
        )
    template = _select_template(registry, item, link)
    if template is None:
        return (
            ExperimentGapRoutingItem(
                **common,
                routing_decision="defer",
                routing_status="unrouted_no_template",
                rejection_reason_optional="No approved local template supports this gap.",
            ),
            None,
        )
    if template.network_required or template.arbitrary_code_required:
        return (
            ExperimentGapRoutingItem(
                **common,
                routing_decision="reject",
                routing_status="rejected_policy",
                selected_template_id_optional=template.template_id,
                template_family_optional=template.template_family,
                rejection_reason_optional=(
                    "Selected template requires network or arbitrary code execution."
                ),
            ),
            None,
        )
    spec_id = _spec_id(routing_id, item, index)
    spec = PlannedExperimentSpec(
        run_id=run_id,
        spec_id=spec_id,
        target_claim_id=item.target_claim_id_optional or link.claim_id,
        target_section=item.target_section_optional or link.section_name,
        hypothesis_or_question=_hypothesis_for(item, link),
        suggested_dataset="deterministic synthetic calibration grid",
        suggested_metrics=list(template.default_metrics),
        suggested_baselines=list(template.default_baselines),
        suggested_seed_policy="fixed seed 1729 recorded by uv_local sandbox",
        expected_output_artifacts=["metrics.json", "outputs/summary.json"],
        experiment_bundle_path_optional=template.bundle_path,
        template_id_optional=template.template_id,
        template_family_optional=template.template_family,
        sandbox_backend="uv_local",
        requested_dependencies=list(template.dependency_profile),
        allow_network=False,
        seed=1729,
        timeout_seconds=30,
    )
    existing = find_existing_planned_spec(run_id=run_id, root=root, spec=spec)
    if existing is not None:
        existing_path, _ = existing
        return (
            ExperimentGapRoutingItem(
                **common,
                routing_decision="reuse_existing_spec",
                routing_status="routed",
                selected_template_id_optional=template.template_id,
                template_family_optional=template.template_family,
                existing_experiment_spec_path_optional=existing_path,
            ),
            None,
        )
    path = f"runs/{run_id}/reports/{spec_id}.json"
    return (
        ExperimentGapRoutingItem(
            **common,
            routing_decision="create_experiment_spec",
            routing_status="routed",
            selected_template_id_optional=template.template_id,
            template_family_optional=template.template_family,
            created_experiment_spec_path_optional=path,
        ),
        ArtifactWriteSpec(
            spec_id,
            ArtifactType.REPORT,
            spec,
            "json",
            _metadata("routed_planned_experiment_spec_context"),
        ),
    )


def _select_template(
    registry: ExperimentTemplateRegistry,
    item: AutonomousEvidenceGapPlanItem,
    link: ClaimEvidenceMapLink,
) -> ExperimentTemplate | None:
    for template in registry.templates:
        if item.gap_type not in template.supported_gap_types:
            continue
        if link.claim_class not in template.supported_claim_classes:
            continue
        if template.network_required or template.arbitrary_code_required:
            continue
        return template
    return None


def _forbidden_claim(link: ClaimEvidenceMapLink) -> bool:
    text = " ".join(
        [
            link.claim_class,
            link.support_scope,
            link.unsupported_reason or "",
            link.classification,
        ]
    ).casefold()
    return any(marker in text for marker in _FORBIDDEN_AUTHORITY_WORDS)


def _hypothesis_for(item: AutonomousEvidenceGapPlanItem, link: ClaimEvidenceMapLink) -> str:
    if link.support_scope:
        return (
            f"Can the approved synthetic calibration template produce bounded metrics "
            f"for claim `{link.claim_id}` in section `{link.section_name}`?"
        )
    return item.recommended_action


def _persist_routing(
    *,
    report: ExperimentGapRoutingReport,
    registry: ExperimentTemplateRegistry,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    routing_number: int,
    specs: list[ArtifactWriteSpec],
) -> ExperimentGapRoutingResult:
    from factori.full_paper_generation import (  # noqa: PLC0415
        build_reviewer_bundle_summary,
        render_reviewer_bundle_summary_markdown,
    )

    report_id = report.routing_id
    index_id = f"experiment-gap-routing-index-{routing_number:04d}"
    registry_id = registry.registry_id
    reviewer_id = f"reviewer-bundle-summary-after-experiment-routing-{routing_number:04d}"
    _, previous_index = latest_experiment_gap_routing_report(root, report.run_id)
    index = ExperimentGapRoutingIndex(
        run_id=report.run_id,
        latest_routing_id=report.routing_id,
        routing_count=routing_number,
        latest_routing_status=report.routing_status,
        routed_gap_count=(
            (previous_index.routed_gap_count if previous_index else 0)
            + report.routed_gap_count
        ),
        unrouted_gap_count=(
            (previous_index.unrouted_gap_count if previous_index else 0)
            + report.unrouted_gap_count
        ),
        created_experiment_spec_count=(
            (previous_index.created_experiment_spec_count if previous_index else 0)
            + report.created_experiment_spec_count
        ),
        bounded_empirical_gaps_routed=(
            (previous_index.bounded_empirical_gaps_routed if previous_index else 0)
            + report.bounded_empirical_gaps_routed
        ),
        synthetic_template_specs_created=(
            (previous_index.synthetic_template_specs_created if previous_index else 0)
            + report.synthetic_template_specs_created
        ),
        latest_report_path=f"runs/{report.run_id}/reports/{report_id}.json",
        latest_template_registry_path=f"runs/{report.run_id}/reports/{registry_id}.json",
        latest_requires_human_intervention=report.requires_human_intervention,
    )
    reviewer = build_reviewer_bundle_summary(
        run_id=report.run_id,
        root=root,
        experiment_gap_routing_report=report,
    )
    artifact_specs = [
        ArtifactWriteSpec(
            registry_id,
            ArtifactType.REPORT,
            registry,
            "json",
            _metadata("experiment_template_registry_context"),
        ),
        *specs,
        ArtifactWriteSpec(
            report_id,
            ArtifactType.REPORT,
            report,
            "json",
            _metadata("experiment_gap_routing_context"),
        ),
        ArtifactWriteSpec(
            f"{report_id}-markdown",
            ArtifactType.REPORT,
            render_experiment_gap_routing_markdown(report),
            "markdown",
            _metadata("experiment_gap_routing_context"),
            filename_stem=report_id,
        ),
        ArtifactWriteSpec(
            index_id,
            ArtifactType.REPORT,
            index,
            "json",
            _metadata("experiment_gap_routing_index_context"),
        ),
        ArtifactWriteSpec(
            reviewer_id,
            ArtifactType.REPORT,
            reviewer,
            "json",
            _metadata("reviewer_bundle_summary_context"),
        ),
        ArtifactWriteSpec(
            f"{reviewer_id}-markdown",
            ArtifactType.REPORT,
            render_reviewer_bundle_summary_markdown(reviewer),
            "markdown",
            _metadata("reviewer_bundle_summary_context"),
            filename_stem=reviewer_id,
        ),
    ]
    persistence = persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=artifact_specs,
        action_type=ControllerActionType.EXPERIMENT_GAP_ROUTING_WRITTEN,
        commit_payload={
            "run_id": report.run_id,
            "routing_id": report.routing_id,
            "routing_backend": report.routing_backend,
            "routed_gap_count": report.routed_gap_count,
            "created_experiment_spec_count": report.created_experiment_spec_count,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return ExperimentGapRoutingResult(
        run_id=report.run_id,
        report=report,
        index=index,
        registry=registry,
        persistence=persistence,
        report_artifact=by_id[report_id],
        markdown_artifact=by_id[f"{report_id}-markdown"],
        index_artifact=by_id[index_id],
        registry_artifact=by_id[registry_id],
        reviewer_summary_artifact=by_id[reviewer_id],
        reviewer_summary_markdown_artifact=by_id[f"{reviewer_id}-markdown"],
    )


def _read_plan(path: Path) -> AutonomousEvidenceGapPlan:
    try:
        return AutonomousEvidenceGapPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ExperimentGapRoutingError("Autonomous evidence plan is unreadable.") from exc


def _read_claim_map(path: Path) -> ClaimEvidenceMap:
    try:
        return ClaimEvidenceMap.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise ExperimentGapRoutingError("Claim-evidence map is unreadable.") from exc


def _next_routing_number(reports: Path) -> int:
    values = []
    for path in reports.glob("experiment-gap-routing-*.json"):
        if path.name.endswith(".meta.json") or path.name.startswith("experiment-gap-routing-index"):
            continue
        match = re.fullmatch(r"experiment-gap-routing-(\d+)\.json", path.name)
        if match:
            values.append(int(match.group(1)))
    return max(values, default=0) + 1


def _spec_id(routing_id: str, item: AutonomousEvidenceGapPlanItem, index: int) -> str:
    target = item.target_claim_id_optional or item.item_id
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", target).strip("-").casefold() or f"item-{index}"
    return f"experiment-spec-routed-{safe}-{routing_id}"


def _metadata(role: str) -> dict[str, Any]:
    return {
        "stage": "experiment_gap_routing",
        "artifact_role": role,
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }
