"""Deterministic routing from selected scientific substrates to local experiments."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factori.artifacts import ArtifactStore
from factori.claim_evidence import (
    BOUNDED_EMPIRICAL_CLAIM_CLASSES,
    BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID,
    latest_claim_evidence_map_path,
)
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.rerun_policy import validate_ledger_tip
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ClaimEvidenceMap,
    ControllerActionType,
    PythonExperimentSandboxReport,
    ScientificSubstrate,
    SubstrateExperimentRoutingReport,
    SubstrateExperimentSpec,
)
from factori.scientific_substrate import latest_scientific_substrate_build

_DISTANCE_MODEL = "region_specific_distance_decay_gravity"
_DISTANCE_BUNDLE_ID = "distance_decay_spatial_interaction"
_DISTANCE_BUNDLE = (
    "tests/fixtures/experiments/bundles/distance_decay_spatial_interaction"
)
_ROUTING_RE = re.compile(r"^substrate-experiment-routing-(\d{4})\.json$")


class SubstrateExperimentRoutingError(RuntimeError):
    """Raised when substrate experiment routing cannot proceed safely."""


@dataclass(frozen=True)
class SubstrateExperimentRoutingResult:
    """Persisted substrate routing report and optional generated spec."""

    run_id: str
    report: SubstrateExperimentRoutingReport
    spec: SubstrateExperimentSpec | None
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef
    spec_artifact: ArtifactRef | None


def route_substrate_experiment(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> SubstrateExperimentRoutingResult:
    """Route the selected substrate to a bounded approved uv-local bundle."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    if not reports.is_dir():
        raise SubstrateExperimentRoutingError(
            f"Reports directory not found for run_id={run_id}."
        )
    if validate_ledger_tip(run_id, root=root_path).blocking_findings:
        raise SubstrateExperimentRoutingError(
            "Ledger validation blocks substrate experiment routing."
        )
    number = _next_routing_number(reports)
    routing_id = f"substrate-experiment-routing-{number:04d}"
    build, substrates, warnings = latest_scientific_substrate_build(root_path, run_id)
    selected = next(
        (item for item in substrates if item.selected_for_next_experiment),
        None,
    )
    selected_index = substrates.index(selected) if selected in substrates else -1
    source_path = (
        build.substrate_paths[selected_index]
        if build is not None
        and selected_index >= 0
        and selected_index < len(build.substrate_paths)
        else None
    )
    spec: SubstrateExperimentSpec | None = None
    existing_path: str | None = None
    status = "no_selected_substrate"
    bundle: str | None = None
    target_claim: str | None = None
    if selected is not None:
        if selected.concrete_model_object.model_type != _DISTANCE_MODEL:
            status = "unsupported_substrate"
            warnings.append(
                "No approved local substrate experiment bundle supports model type "
                f"{selected.concrete_model_object.model_type}."
            )
        else:
            target_claim = _target_claim_id(root_path, run_id)
            if target_claim is None:
                status = "unsupported_substrate"
                warnings.append(
                    "No bounded experiment-support claim is present in the claim-evidence map."
                )
            else:
                bundle = _DISTANCE_BUNDLE
                spec = _distance_decay_spec(
                    run_id=run_id,
                    routing_id=routing_id,
                    substrate=selected,
                    substrate_path=source_path or "",
                    target_claim_id=target_claim,
                )
                existing_path = _existing_spec_path(reports, spec)
                status = "reused_existing_spec" if existing_path else "routed"
    generated_path = (
        f"runs/{run_id}/reports/{spec.spec_id}.json"
        if spec is not None and existing_path is None
        else None
    )
    report = SubstrateExperimentRoutingReport(
        run_id=run_id,
        routing_id=routing_id,
        routing_status=status,
        substrate_experiment_routed=status in {"routed", "reused_existing_spec"},
        selected_substrate_id_optional=selected.substrate_id if selected else None,
        selected_substrate_title_optional=selected.title if selected else None,
        source_substrate_path_optional=source_path,
        target_claim_id_optional=target_claim,
        experiment_bundle_optional=bundle,
        generated_experiment_spec_path_optional=generated_path,
        existing_experiment_spec_path_optional=existing_path,
        warnings=warnings,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )
    return _persist_routing(
        report=report,
        spec=spec if generated_path else None,
        number=number,
        root=root_path,
        store=store,
        ledger=ledger,
    )


def inspect_substrate_experiment_routing(
    *, run_id: str, root: str | Path = "."
) -> dict[str, Any]:
    """Inspect the latest route and any subsequent sandbox result without mutation."""
    root_path = Path(root)
    report = latest_substrate_experiment_routing_report(root_path, run_id)
    if report is None:
        raise SubstrateExperimentRoutingError(
            f"No substrate experiment routing report found for run_id={run_id}."
        )
    spec_path = (
        report.generated_experiment_spec_path_optional
        or report.existing_experiment_spec_path_optional
    )
    spec = _read_spec(root_path / spec_path) if spec_path else None
    sandbox = _latest_sandbox_for_spec(root_path, run_id, spec.spec_id if spec else None)
    metrics = _sandbox_metrics(root_path, sandbox)
    claim_linked = _claim_linked(root_path, run_id, sandbox)
    return {
        **report.model_dump(mode="json"),
        "substrate_experiment_routing_present": True,
        "generated_experiment_spec_path": spec_path,
        "sandbox_status": sandbox.sandbox_status if sandbox else None,
        "comparison_table_present": bool(metrics.get("comparison_table")),
        "heterogeneity_ablation_present": bool(
            metrics.get("heterogeneity_ablation_present")
        ),
        "baseline_mae": metrics.get("test_mae_baseline"),
        "method_mae": metrics.get("test_mae_method"),
        "baseline_rmse": metrics.get("test_rmse_baseline"),
        "method_rmse": metrics.get("test_rmse_method"),
        "method_beat_baseline": metrics.get("claim_support_satisfied"),
        "claim_evidence_linked": claim_linked,
        "publication_ready": False,
    }


def latest_substrate_experiment_routing_report(
    root: Path, run_id: str
) -> SubstrateExperimentRoutingReport | None:
    """Load the latest immutable substrate routing report."""
    reports = root / "runs" / run_id / "reports"
    paths = sorted(
        (int(match.group(1)), path)
        for path in reports.glob("substrate-experiment-routing-*.json")
        if (match := _ROUTING_RE.fullmatch(path.name))
    )
    if not paths:
        return None
    try:
        return SubstrateExperimentRoutingReport.model_validate_json(
            paths[-1][1].read_text(encoding="utf-8")
        )
    except (OSError, ValidationError):
        return None


def render_substrate_experiment_routing_markdown(
    report: SubstrateExperimentRoutingReport,
) -> str:
    """Render a concise context-only routing report."""
    return "\n".join(
        [
            "# Substrate Experiment Routing",
            "",
            f"- Run ID: `{report.run_id}`",
            f"- Routing ID: `{report.routing_id}`",
            f"- Status: `{report.routing_status}`",
            f"- Selected substrate: `{report.selected_substrate_title_optional or 'none'}`",
            f"- Bundle: `{report.experiment_bundle_optional or 'none'}`",
            "- publication_ready: false",
            "",
            "Routing and planned specs are context only. Completed bounded support requires a "
            "successful sandbox artifact that passes experiment intake.",
            "",
        ]
    )


def _distance_decay_spec(
    *,
    run_id: str,
    routing_id: str,
    substrate: ScientificSubstrate,
    substrate_path: str,
    target_claim_id: str,
) -> SubstrateExperimentSpec:
    design = substrate.experiment_design
    return SubstrateExperimentSpec(
        run_id=run_id,
        spec_id=f"experiment-spec-substrate-distance-decay-{routing_id}",
        target_claim_id=target_claim_id,
        target_section="Bounded Empirical Demonstration",
        hypothesis_or_question=substrate.measurable_hypothesis,
        suggested_dataset="deterministic synthetic origin-destination flow matrix",
        suggested_metrics=[
            "test_mae_baseline",
            "test_mae_method",
            "test_rmse_baseline",
            "test_rmse_method",
            "mae_improvement",
            "rmse_improvement",
            "sample_count",
            "train_pair_count",
            "test_pair_count",
            "seed",
        ],
        suggested_baselines=["pooled-alpha gravity model"],
        suggested_seed_policy=design.seed_plan,
        expected_output_artifacts=[
            "metrics.json",
            "outputs/comparison-table.json",
            "substrate-experiment-result.json",
        ],
        experiment_bundle_path_optional=_DISTANCE_BUNDLE,
        template_id_optional="distance_decay_spatial_interaction_v1",
        template_family_optional="baseline_vs_method",
        sandbox_backend="uv_local",
        requested_dependencies=[],
        allow_network=False,
        seed=1729,
        timeout_seconds=30,
        source_substrate_id=substrate.substrate_id,
        source_substrate_path=substrate_path,
        model_equation=substrate.concrete_model_object.equations[0],
        dgp_steps=[
            "Generate n regions and coordinates x_i in [0,1]^2.",
            "Compute positive pairwise distances d_ij.",
            "Sample positive origin masses A_i and destination attractiveness B_j.",
            "Sample origin-specific distance-decay parameters alpha_i.",
            "Generate noisy OD flows from the substrate equation.",
            "Split OD pairs deterministically into train and test sets.",
        ],
        baseline_model="pooled-alpha gravity model",
        method_model="heterogeneous-alpha spatial interaction model",
        metric_names=list(design.metrics),
        heterogeneity_settings=["low_heterogeneity", "high_heterogeneity"],
        ablation_or_stress_test=design.ablation_or_stress_test,
        bounded_claim_rule=(
            "method_mae < baseline_mae and method_rmse <= baseline_rmse"
        ),
        experiment_bundle_id=_DISTANCE_BUNDLE_ID,
    )


def _target_claim_id(root: Path, run_id: str) -> str | None:
    path = latest_claim_evidence_map_path(root, run_id)
    if path is None:
        return None
    try:
        claim_map = ClaimEvidenceMap.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None
    by_id = {link.claim_id: link for link in claim_map.links}
    bounded = by_id.get(BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID)
    if bounded is not None and bounded.claim_class in BOUNDED_EMPIRICAL_CLAIM_CLASSES:
        return bounded.claim_id
    return next(
        (
            link.claim_id
            for link in claim_map.links
            if link.claim_class in BOUNDED_EMPIRICAL_CLAIM_CLASSES
        ),
        None,
    )


def _existing_spec_path(reports: Path, spec: SubstrateExperimentSpec) -> str | None:
    for path in sorted(reports.glob("experiment-spec-substrate-*.json")):
        if path.name.endswith(".meta.json"):
            continue
        existing = _read_spec(path)
        if existing and existing.source_substrate_id == spec.source_substrate_id:
            return path.relative_to(reports.parents[2]).as_posix()
    return None


def _read_spec(path: Path) -> SubstrateExperimentSpec | None:
    try:
        return SubstrateExperimentSpec.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def _latest_sandbox_for_spec(
    root: Path, run_id: str, spec_id: str | None
) -> PythonExperimentSandboxReport | None:
    if spec_id is None:
        return None
    reports = root / "runs" / run_id / "reports"
    matches: list[PythonExperimentSandboxReport] = []
    for path in sorted(reports.glob("python-experiment-sandbox-run-*.json")):
        if path.name.endswith(".meta.json"):
            continue
        try:
            report = PythonExperimentSandboxReport.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError):
            continue
        if report.experiment_spec_id == spec_id:
            matches.append(report)
    return matches[-1] if matches else None


def _sandbox_metrics(root: Path, report: PythonExperimentSandboxReport | None) -> dict[str, Any]:
    if report is None or report.sandbox_status != "completed":
        return {}
    try:
        import json

        payload = json.loads((root / report.metrics_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _claim_linked(
    root: Path, run_id: str, sandbox: PythonExperimentSandboxReport | None
) -> bool:
    if sandbox is None or not sandbox.ingested_experiment_artifact_path_optional:
        return False
    map_path = latest_claim_evidence_map_path(root, run_id)
    if map_path is None:
        return False
    try:
        claim_map = ClaimEvidenceMap.model_validate_json(map_path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return False
    return any(
        link.support_status == "supported_within_scope"
        and bool(link.supporting_experiment_artifact_ids)
        for link in claim_map.links
    )


def _persist_routing(
    *,
    report: SubstrateExperimentRoutingReport,
    spec: SubstrateExperimentSpec | None,
    number: int,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> SubstrateExperimentRoutingResult:
    metadata = {
        "stage": "substrate_experiment_routing",
        "artifact_role": "substrate_experiment_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }
    specs = []
    if spec is not None:
        specs.append(ArtifactWriteSpec(spec.spec_id, ArtifactType.REPORT, spec, "json", metadata))
    specs.extend(
        [
            ArtifactWriteSpec(
                report.routing_id, ArtifactType.REPORT, report, "json", metadata
            ),
            ArtifactWriteSpec(
                f"{report.routing_id}-markdown",
                ArtifactType.REPORT,
                render_substrate_experiment_routing_markdown(report),
                "markdown",
                metadata,
                filename_stem=report.routing_id,
            ),
        ]
    )
    persistence = persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.SUBSTRATE_EXPERIMENT_ROUTED,
        commit_payload={
            "run_id": report.run_id,
            "routing_id": report.routing_id,
            "routing_status": report.routing_status,
            "substrate_experiment_routed": report.substrate_experiment_routed,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {item.id: item for item in persistence.artifacts}
    return SubstrateExperimentRoutingResult(
        run_id=report.run_id,
        report=report,
        spec=spec,
        persistence=persistence,
        report_artifact=by_id[report.routing_id],
        markdown_artifact=by_id[f"{report.routing_id}-markdown"],
        spec_artifact=by_id.get(spec.spec_id) if spec else None,
    )


def _next_routing_number(reports: Path) -> int:
    values = [
        int(match.group(1))
        for path in reports.glob("substrate-experiment-routing-*.json")
        if (match := _ROUTING_RE.fullmatch(path.name))
    ]
    return max(values, default=0) + 1
