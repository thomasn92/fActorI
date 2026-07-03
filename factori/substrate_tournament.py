"""Deterministic multi-substrate experimental tournament."""

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
    persist_claim_evidence_map,
)
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.python_experiment_sandbox import run_python_experiment_sandbox
from factori.rerun_policy import validate_ledger_tip
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ClaimEvidenceMap,
    ControllerActionType,
    ExperimentArtifact,
    PythonExperimentSandboxReport,
    ScientificSubstrate,
    ScientificSubstrateBuildReport,
    SubstrateExperimentResult,
    SubstrateExperimentSpec,
    SubstrateTournamentComparison,
    SubstrateTournamentEntry,
    SubstrateTournamentInspectionReport,
    SubstrateTournamentResult,
    SubstrateTournamentSpec,
)
from factori.scientific_substrate import latest_scientific_substrate_build

_DISTANCE_MODEL = "region_specific_distance_decay_gravity"
_PCA_MODEL = "low_rank_gravity_residual_representation"
_DISTANCE_BUNDLE_ID = "distance_decay_spatial_interaction"
_PCA_BUNDLE_ID = "pca_low_rank_od_residual"
_DISTANCE_BUNDLE = (
    "tests/fixtures/experiments/bundles/distance_decay_spatial_interaction"
)
_PCA_BUNDLE = "tests/fixtures/experiments/bundles/pca_low_rank_od_residual"
_RESULT_RE = re.compile(r"^substrate-tournament-result-(\d{4})\.json$")


class SubstrateTournamentError(RuntimeError):
    """Raised when a substrate tournament cannot run safely."""


@dataclass(frozen=True)
class SubstrateTournamentRunResult:
    """Persisted substrate tournament outcome."""

    run_id: str
    tournament_spec: SubstrateTournamentSpec
    result: SubstrateTournamentResult
    persistence: PersistenceResult
    spec_artifacts: list[ArtifactRef]
    tournament_spec_artifact: ArtifactRef
    result_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


def run_substrate_tournament(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    sandbox_backend: str = "uv_local",
) -> SubstrateTournamentRunResult:
    """Run all supported serious substrate branches through bounded local experiments."""
    if sandbox_backend != "uv_local":
        raise SubstrateTournamentError("substrate tournament currently supports uv_local only")
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    if not reports.is_dir():
        raise SubstrateTournamentError(f"Reports directory not found for run_id={run_id}.")
    if validate_ledger_tip(run_id, root=root_path).blocking_findings:
        raise SubstrateTournamentError("Ledger validation blocks substrate tournament.")

    number = _next_tournament_number(reports)
    tournament_id = f"substrate-tournament-{number:04d}"
    build, substrates, warnings = latest_scientific_substrate_build(root_path, run_id)
    if build is None or not substrates:
        raise SubstrateTournamentError("Build scientific substrates before running a tournament.")
    target_claim = _target_claim_id(root_path, run_id)
    if target_claim is None:
        try:
            persist_claim_evidence_map(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                enable_empirical_demonstration_gaps=True,
            )
        except Exception as exc:
            raise SubstrateTournamentError(
                "No bounded empirical target claim is available for the tournament."
            ) from exc
        target_claim = _target_claim_id(root_path, run_id)
    if target_claim is None:
        raise SubstrateTournamentError(
            "No bounded empirical target claim is available for the tournament."
        )

    build_path = _latest_build_path(root_path, run_id, build)
    substrate_paths = _substrate_paths(root_path, build)
    routable = _routable_substrates(substrates)
    specs = [
        _spec_for_substrate(
            run_id=run_id,
            tournament_id=tournament_id,
            substrate=substrate,
            substrate_path=substrate_paths.get(substrate.substrate_id, ""),
            target_claim_id=target_claim,
        )
        for substrate in routable
    ]
    spec_paths = [f"runs/{run_id}/reports/{spec.spec_id}.json" for spec in specs]
    tournament_spec = SubstrateTournamentSpec(
        run_id=run_id,
        tournament_id=tournament_id,
        source_scientific_substrate_build_path_optional=build_path,
        execution_backend="uv_local",
        selection_policy=(
            "Score completed synthetic branches by declared MAE/RMSE improvement ratios, "
            "ablation sensitivity, and substrate-specific recovery metrics; penalize "
            "inconclusive branches and keep scope synthetic-only."
        ),
        substrate_count=len(routable),
        routed_substrate_ids=[substrate.substrate_id for substrate in routable],
        experiment_spec_paths=spec_paths,
        publication_ready=False,
    )
    spec_persistence = _persist_tournament_specs(
        run_id=run_id,
        store=store,
        ledger=ledger,
        tournament_id=tournament_id,
        tournament_spec=tournament_spec,
        specs=specs,
    )
    spec_artifact_by_id = {artifact.id: artifact for artifact in spec_persistence.artifacts}

    entries: list[SubstrateTournamentEntry] = []
    for substrate, spec in zip(routable, specs, strict=True):
        spec_artifact = spec_artifact_by_id[spec.spec_id]
        entries.append(
            _execute_entry(
                root=root_path,
                run_id=run_id,
                store=store,
                ledger=ledger,
                tournament_id=tournament_id,
                substrate=substrate,
                spec=spec,
                spec_path=spec_artifact.path,
            )
        )

    winner = _select_winner(entries)
    entries = [
        entry.model_copy(
            update={
                "selected_as_winner": winner is not None
                and entry.substrate_id == winner.substrate_id,
                "winner_reason_optional": (
                    _winner_reason(entry)
                    if winner and entry.substrate_id == winner.substrate_id
                    else None
                ),
            }
        )
        for entry in entries
    ]
    comparison = _comparison(entries, winner)
    completed = [entry for entry in entries if entry.sandbox_status == "completed"]
    inconclusive = [
        entry
        for entry in entries
        if entry.result_status in {"negative_result", "inconclusive"}
    ]
    failed = [
        entry
        for entry in entries
        if entry.sandbox_status not in {"completed"} and entry.result_status == "not_run"
    ]
    claim_map = _read_claim_map(root_path, run_id)
    unsupported = (
        len(claim_map.unsupported_non_scaffold_claim_ids) if claim_map is not None else 0
    )
    status = (
        "no_routable_substrates"
        if not entries
        else "completed_with_inconclusive_branches"
        if inconclusive or failed
        else "completed"
    )
    result_id = f"substrate-tournament-result-{number:04d}"
    result = SubstrateTournamentResult(
        run_id=run_id,
        tournament_id=tournament_id,
        tournament_status=status,
        source_scientific_substrate_build_path_optional=build_path,
        tournament_spec_path=f"runs/{run_id}/reports/substrate-tournament-spec-{number:04d}.json",
        substrate_count=len(entries),
        completed_branch_count=len(completed),
        inconclusive_branch_count=len(inconclusive),
        failed_branch_count=len(failed),
        winner_selected=winner is not None,
        winner_substrate_id_optional=winner.substrate_id if winner else None,
        winner_substrate_title_optional=winner.substrate_title if winner else None,
        winner_reason_optional=_winner_reason(winner) if winner else None,
        entries=entries,
        comparison=comparison,
        generated_experiment_spec_paths=spec_paths,
        sandbox_report_paths=[
            entry.sandbox_report_path_optional
            for entry in entries
            if entry.sandbox_report_path_optional
        ],
        experiment_artifact_paths=[
            entry.experiment_artifact_path_optional
            for entry in entries
            if entry.experiment_artifact_path_optional
        ],
        unsupported_claim_count=unsupported,
        warnings=warnings,
        publication_ready=False,
    )
    result_persistence = _persist_tournament_result(
        result=result,
        result_id=result_id,
        store=store,
        ledger=ledger,
    )
    result_by_id = {artifact.id: artifact for artifact in result_persistence.artifacts}
    return SubstrateTournamentRunResult(
        run_id=run_id,
        tournament_spec=tournament_spec,
        result=result,
        persistence=result_persistence,
        spec_artifacts=[spec_artifact_by_id[spec.spec_id] for spec in specs],
        tournament_spec_artifact=spec_artifact_by_id[
            f"substrate-tournament-spec-{number:04d}"
        ],
        result_artifact=result_by_id[result_id],
        markdown_artifact=result_by_id[f"{result_id}-markdown"],
    )


def inspect_substrate_tournament(
    *, run_id: str, root: str | Path = "."
) -> SubstrateTournamentInspectionReport:
    """Inspect the latest substrate tournament without mutation."""
    root_path = Path(root)
    result = latest_substrate_tournament_result(root_path, run_id)
    if result is None:
        return SubstrateTournamentInspectionReport(
            run_id=run_id,
            tournament_present=False,
            substrate_count=0,
            warnings=["No substrate tournament report is present."],
            publication_ready=False,
        )
    return SubstrateTournamentInspectionReport(
        run_id=run_id,
        tournament_present=True,
        latest_tournament_id_optional=result.tournament_id,
        tournament_status_optional=result.tournament_status,
        substrate_count=result.substrate_count,
        distance_decay_branch_completed=any(
            entry.substrate_model_type == _DISTANCE_MODEL
            and entry.sandbox_status == "completed"
            for entry in result.entries
        ),
        pca_low_rank_branch_completed=any(
            entry.substrate_model_type == _PCA_MODEL
            and entry.sandbox_status == "completed"
            for entry in result.entries
        ),
        winner_selected=result.winner_selected,
        winner_substrate_id_optional=result.winner_substrate_id_optional,
        winner_substrate_title_optional=result.winner_substrate_title_optional,
        comparison_table_present=result.comparison.comparison_table_present,
        entries=result.entries,
        result_optional=result,
        warnings=result.warnings,
        publication_ready=False,
    )


def latest_substrate_tournament_result(
    root: Path,
    run_id: str,
) -> SubstrateTournamentResult | None:
    """Load the latest immutable substrate tournament result."""
    reports = root / "runs" / run_id / "reports"
    paths = sorted(
        (int(match.group(1)), path)
        for path in reports.glob("substrate-tournament-result-*.json")
        if (match := _RESULT_RE.fullmatch(path.name))
    )
    if not paths:
        return None
    try:
        return SubstrateTournamentResult.model_validate_json(
            paths[-1][1].read_text(encoding="utf-8")
        )
    except (OSError, ValidationError):
        return None


def render_substrate_tournament_markdown(result: SubstrateTournamentResult) -> str:
    """Render a concise non-evidence tournament report."""
    lines = [
        "# Substrate Tournament",
        "",
        f"- Run ID: `{result.run_id}`",
        f"- Tournament ID: `{result.tournament_id}`",
        f"- Status: `{result.tournament_status}`",
        f"- Winner: `{result.winner_substrate_title_optional or 'none'}`",
        "- publication_ready: false",
        "",
        "## Comparison",
        "",
        "| substrate | status | MAE ratio | RMSE ratio | ablation | score |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for entry in result.entries:
        lines.append(
            f"| {entry.substrate_title} | {entry.result_status} | "
            f"{entry.mae_improvement_ratio:.4f} | {entry.rmse_improvement_ratio:.4f} | "
            f"{entry.ablation_sensitivity:.4f} | {entry.tournament_score:.4f} |"
        )
    lines.extend(
        [
            "",
            "The tournament compares bounded synthetic support within each substrate's declared "
            "result schema. It does not create real-world validation, novelty, correctness, or "
            "publication readiness.",
            "",
        ]
    )
    return "\n".join(lines)


def _spec_for_substrate(
    *,
    run_id: str,
    tournament_id: str,
    substrate: ScientificSubstrate,
    substrate_path: str,
    target_claim_id: str,
) -> SubstrateExperimentSpec:
    if substrate.concrete_model_object.model_type == _DISTANCE_MODEL:
        return _distance_decay_spec(
            run_id=run_id,
            tournament_id=tournament_id,
            substrate=substrate,
            substrate_path=substrate_path,
            target_claim_id=target_claim_id,
        )
    if substrate.concrete_model_object.model_type == _PCA_MODEL:
        return _pca_spec(
            run_id=run_id,
            tournament_id=tournament_id,
            substrate=substrate,
            substrate_path=substrate_path,
            target_claim_id=target_claim_id,
        )
    raise SubstrateTournamentError(
        "No approved local tournament experiment supports model type "
        f"{substrate.concrete_model_object.model_type}."
    )


def _distance_decay_spec(
    *,
    run_id: str,
    tournament_id: str,
    substrate: ScientificSubstrate,
    substrate_path: str,
    target_claim_id: str,
) -> SubstrateExperimentSpec:
    design = substrate.experiment_design
    return SubstrateExperimentSpec(
        run_id=run_id,
        spec_id=f"experiment-spec-substrate-tournament-distance-decay-{tournament_id[-4:]}",
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
        bounded_claim_rule="method_mae < baseline_mae and method_rmse <= baseline_rmse",
        experiment_bundle_id=_DISTANCE_BUNDLE_ID,
    )


def _pca_spec(
    *,
    run_id: str,
    tournament_id: str,
    substrate: ScientificSubstrate,
    substrate_path: str,
    target_claim_id: str,
) -> SubstrateExperimentSpec:
    design = substrate.experiment_design
    return SubstrateExperimentSpec(
        run_id=run_id,
        spec_id=f"experiment-spec-substrate-tournament-pca-low-rank-{tournament_id[-4:]}",
        target_claim_id=target_claim_id,
        target_section="Bounded Empirical Demonstration",
        hypothesis_or_question=substrate.measurable_hypothesis,
        suggested_dataset="deterministic synthetic OD-flow matrix with latent residual factors",
        suggested_metrics=[
            "test_mae_baseline",
            "test_mae_method",
            "test_rmse_baseline",
            "test_rmse_method",
            "mae_improvement",
            "rmse_improvement",
            "latent_factor_recovery_correlation",
            "explained_residual_variance",
            "sample_count",
            "train_pair_count",
            "test_pair_count",
            "seed",
        ],
        suggested_baselines=["pooled gravity model without low-rank residual correction"],
        suggested_seed_policy=design.seed_plan,
        expected_output_artifacts=[
            "metrics.json",
            "outputs/comparison-table.json",
            "substrate-experiment-result.json",
        ],
        experiment_bundle_path_optional=_PCA_BUNDLE,
        template_id_optional="pca_low_rank_od_residual_v1",
        template_family_optional="baseline_vs_method",
        sandbox_backend="uv_local",
        requested_dependencies=[],
        allow_network=False,
        seed=2718,
        timeout_seconds=30,
        source_substrate_id=substrate.substrate_id,
        source_substrate_path=substrate_path,
        model_equation="; ".join(substrate.concrete_model_object.equations),
        dgp_steps=[
            "Generate n regions.",
            "Generate distances and a pooled gravity component.",
            "Generate latent origin/destination heterogeneity factors.",
            "Generate OD flows from the gravity component plus low-rank residual structure.",
            "Split OD pairs into train/test.",
            "Fit a pooled gravity baseline.",
            "Compute the train residual matrix R_ij.",
            "Fit a rank-k low-rank residual model.",
            "Predict held-out OD flows using gravity plus low-rank residual correction.",
        ],
        baseline_model="pooled gravity model without low-rank residual correction",
        method_model="pooled gravity model plus rank-k residual correction",
        metric_names=list(design.metrics),
        heterogeneity_settings=["low_latent_factor_strength", "high_latent_factor_strength"],
        ablation_or_stress_test=design.ablation_or_stress_test,
        bounded_claim_rule=(
            "method_mae < baseline_mae and method_rmse <= baseline_rmse and "
            "latent_factor_recovery_correlation > 0"
        ),
        experiment_bundle_id=_PCA_BUNDLE_ID,
    )


def _execute_entry(
    *,
    root: Path,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    tournament_id: str,
    substrate: ScientificSubstrate,
    spec: SubstrateExperimentSpec,
    spec_path: str,
) -> SubstrateTournamentEntry:
    warnings: list[str] = []
    sandbox_report: PythonExperimentSandboxReport | None = None
    experiment: ExperimentArtifact | None = None
    substrate_result: SubstrateExperimentResult | None = None
    try:
        sandbox = run_python_experiment_sandbox(
            run_id=run_id,
            root=root,
            store=store,
            ledger=ledger,
            experiment_spec=root / spec_path,
            sandbox_backend="uv_local",
            execution_mode="apply",
        )
        sandbox_report = sandbox.report
        if sandbox.report.ingested_experiment_artifact_path_optional:
            experiment = ExperimentArtifact.model_validate_json(
                (root / sandbox.report.ingested_experiment_artifact_path_optional).read_text(
                    encoding="utf-8"
                )
            )
            substrate_result = _read_substrate_result(root, experiment)
    except Exception as exc:  # noqa: BLE001 - fail branch closed, not whole tournament
        warnings.append(str(exc))

    metrics = experiment.metrics if experiment is not None else {}
    completed = sandbox_report is not None and sandbox_report.sandbox_status == "completed"
    result_status = (
        substrate_result.result_status
        if substrate_result is not None
        else "not_run"
        if not completed
        else "inconclusive"
    )
    score_payload = _score_metrics(metrics, completed=completed, supported=bool(
        substrate_result.claim_support_satisfied if substrate_result else False
    ))
    return SubstrateTournamentEntry(
        entry_id=f"{tournament_id}-{_safe_id(substrate.substrate_id)}",
        substrate_id=substrate.substrate_id,
        substrate_title=substrate.title,
        substrate_model_type=substrate.concrete_model_object.model_type,
        source_substrate_path_optional=spec.source_substrate_path,
        experiment_bundle_id_optional=spec.experiment_bundle_id,
        experiment_spec_path_optional=spec_path,
        sandbox_report_path_optional=(
            f"runs/{run_id}/reports/{sandbox_report.sandbox_run_id}.json"
            if sandbox_report is not None
            else None
        ),
        experiment_artifact_path_optional=(
            sandbox_report.ingested_experiment_artifact_path_optional
            if sandbox_report is not None
            else None
        ),
        sandbox_status=sandbox_report.sandbox_status if sandbox_report else "not_run",
        result_status=result_status,
        result_label_optional=substrate_result.result_label if substrate_result else None,
        mae_improvement_ratio=score_payload["mae_improvement_ratio"],
        rmse_improvement_ratio=score_payload["rmse_improvement_ratio"],
        ablation_sensitivity=score_payload["ablation_sensitivity"],
        latent_factor_recovery_correlation_optional=score_payload[
            "latent_factor_recovery_correlation"
        ],
        explained_residual_variance_optional=score_payload["explained_residual_variance"],
        claim_scope_penalty=score_payload["claim_scope_penalty"],
        failure_or_inconclusive_penalty=score_payload["failure_or_inconclusive_penalty"],
        tournament_score=score_payload["tournament_score"],
        selected_as_winner=False,
        warnings=warnings,
        publication_ready=False,
    )


def _score_metrics(
    metrics: dict[str, Any],
    *,
    completed: bool,
    supported: bool,
) -> dict[str, float | None]:
    mae_ratio = _improvement_ratio(
        metrics.get("test_mae_baseline"), metrics.get("test_mae_method")
    )
    rmse_ratio = _improvement_ratio(
        metrics.get("test_rmse_baseline"), metrics.get("test_rmse_method")
    )
    ablation = _ablation_sensitivity(metrics.get("comparison_table"))
    latent = _float_or_none(metrics.get("latent_factor_recovery_correlation"))
    explained = _float_or_none(metrics.get("explained_residual_variance"))
    scope_penalty = 0.05
    failure_penalty = 0.0 if completed and supported else 1.0
    score = (
        mae_ratio
        + rmse_ratio
        + 0.2 * ablation
        + 0.2 * max(0.0, latent or 0.0)
        + 0.1 * max(0.0, explained or 0.0)
        - scope_penalty
        - failure_penalty
    )
    return {
        "mae_improvement_ratio": round(mae_ratio, 6),
        "rmse_improvement_ratio": round(rmse_ratio, 6),
        "ablation_sensitivity": round(ablation, 6),
        "latent_factor_recovery_correlation": round(latent, 6) if latent is not None else None,
        "explained_residual_variance": round(explained, 6) if explained is not None else None,
        "claim_scope_penalty": scope_penalty,
        "failure_or_inconclusive_penalty": failure_penalty,
        "tournament_score": round(score, 6),
    }


def _comparison(
    entries: list[SubstrateTournamentEntry],
    winner: SubstrateTournamentEntry | None,
) -> SubstrateTournamentComparison:
    rows = [
        {
            "substrate_id": entry.substrate_id,
            "substrate_title": entry.substrate_title,
            "sandbox_status": entry.sandbox_status,
            "result_status": entry.result_status,
            "mae_improvement_ratio": entry.mae_improvement_ratio,
            "rmse_improvement_ratio": entry.rmse_improvement_ratio,
            "ablation_sensitivity": entry.ablation_sensitivity,
            "latent_factor_recovery_correlation": (
                entry.latent_factor_recovery_correlation_optional
            ),
            "explained_residual_variance": entry.explained_residual_variance_optional,
            "claim_scope_penalty": entry.claim_scope_penalty,
            "failure_or_inconclusive_penalty": entry.failure_or_inconclusive_penalty,
            "tournament_score": entry.tournament_score,
            "selected_as_winner": winner is not None
            and entry.substrate_id == winner.substrate_id,
        }
        for entry in entries
    ]
    return SubstrateTournamentComparison(
        comparison_policy=(
            "Within synthetic scope, higher normalized improvement and substrate-specific "
            "recovery metrics indicate stronger declared support; this is not a cross-domain "
            "or real-world validation claim."
        ),
        metric_names=[
            "mae_improvement_ratio",
            "rmse_improvement_ratio",
            "ablation_sensitivity",
            "latent_factor_recovery_correlation",
            "explained_residual_variance",
            "claim_scope_penalty",
            "failure_or_inconclusive_penalty",
            "tournament_score",
        ],
        rows=rows,
        winner_substrate_id_optional=winner.substrate_id if winner else None,
        winner_substrate_title_optional=winner.substrate_title if winner else None,
        winner_reason_optional=_winner_reason(winner) if winner else None,
        comparison_table_present=bool(rows),
        publication_ready=False,
    )


def _persist_tournament_specs(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    tournament_id: str,
    tournament_spec: SubstrateTournamentSpec,
    specs: list[SubstrateExperimentSpec],
) -> PersistenceResult:
    metadata = _metadata("substrate_tournament", "substrate_tournament_context")
    number = tournament_id[-4:]
    artifact_specs = [
        ArtifactWriteSpec(spec.spec_id, ArtifactType.REPORT, spec, "json", metadata)
        for spec in specs
    ]
    artifact_specs.append(
        ArtifactWriteSpec(
            f"substrate-tournament-spec-{number}",
            ArtifactType.REPORT,
            tournament_spec,
            "json",
            metadata,
        )
    )
    return persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=artifact_specs,
        action_type=ControllerActionType.SUBSTRATE_TOURNAMENT_RUN,
        commit_payload={
            "run_id": run_id,
            "tournament_id": tournament_id,
            "spec_count": len(specs),
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )


def _persist_tournament_result(
    *,
    result: SubstrateTournamentResult,
    result_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PersistenceResult:
    metadata = _metadata("substrate_tournament", "substrate_tournament_context")
    return persist_artifacts_with_commit(
        run_id=result.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(result_id, ArtifactType.REPORT, result, "json", metadata),
            ArtifactWriteSpec(
                f"{result_id}-markdown",
                ArtifactType.REPORT,
                render_substrate_tournament_markdown(result),
                "markdown",
                metadata,
                filename_stem=result_id,
            ),
        ],
        action_type=ControllerActionType.SUBSTRATE_TOURNAMENT_RUN,
        commit_payload={
            "run_id": result.run_id,
            "tournament_id": result.tournament_id,
            "tournament_status": result.tournament_status,
            "winner_substrate_id": result.winner_substrate_id_optional,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )


def _target_claim_id(root: Path, run_id: str) -> str | None:
    claim_map = _read_claim_map(root, run_id)
    if claim_map is None:
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


def _read_claim_map(root: Path, run_id: str) -> ClaimEvidenceMap | None:
    path = latest_claim_evidence_map_path(root, run_id)
    if path is None:
        return None
    try:
        return ClaimEvidenceMap.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def _read_substrate_result(
    root: Path,
    experiment: ExperimentArtifact,
) -> SubstrateExperimentResult | None:
    for artifact_path in experiment.artifact_paths:
        if not artifact_path.endswith("substrate-experiment-result.json"):
            continue
        try:
            return SubstrateExperimentResult.model_validate_json(
                (root / artifact_path).read_text(encoding="utf-8")
            )
        except (OSError, ValidationError):
            return None
    return None


def _routable_substrates(substrates: list[ScientificSubstrate]) -> list[ScientificSubstrate]:
    supported = [
        substrate
        for substrate in substrates
        if substrate.concrete_model_object.model_type in {_DISTANCE_MODEL, _PCA_MODEL}
    ]
    selected = [substrate for substrate in supported if substrate.selected_for_next_experiment]
    alternatives = [
        substrate for substrate in supported if substrate.substrate_id not in {
            item.substrate_id for item in selected
        }
    ]
    return [*selected, *alternatives]


def _substrate_paths(root: Path, build: ScientificSubstrateBuildReport) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in build.substrate_paths:
        match = re.search(r"scientific-substrate-\d{4}-.+\.json$", path)
        if not match:
            continue
        try:
            payload = ScientificSubstrate.model_validate_json(
                (root / path).read_text(encoding="utf-8")
            )
        except (OSError, ValidationError):
            continue
        result[payload.substrate_id] = path
    return result


def _latest_build_path(
    root: Path,
    run_id: str,
    build: ScientificSubstrateBuildReport,
) -> str | None:
    reports = root / "runs" / run_id / "reports"
    path = reports / f"{build.build_id}.json"
    return _relative(path, root) if path.is_file() else None


def _select_winner(entries: list[SubstrateTournamentEntry]) -> SubstrateTournamentEntry | None:
    viable = [entry for entry in entries if entry.result_status == "supported"]
    if not viable:
        return None
    return sorted(
        viable,
        key=lambda entry: (
            -entry.tournament_score,
            entry.substrate_title,
            entry.substrate_id,
        ),
    )[0]


def _winner_reason(entry: SubstrateTournamentEntry | None) -> str | None:
    if entry is None:
        return None
    return (
        f"{entry.substrate_title} has the strongest declared synthetic-scope score "
        f"({entry.tournament_score}) with MAE ratio {entry.mae_improvement_ratio} and "
        f"RMSE ratio {entry.rmse_improvement_ratio}; this selects manuscript focus only."
    )


def _improvement_ratio(baseline: Any, method: Any) -> float:
    left = _float_or_none(baseline)
    right = _float_or_none(method)
    if left is None or right is None or left <= 0.0:
        return 0.0
    return max(0.0, (left - right) / abs(left))


def _ablation_sensitivity(table: Any) -> float:
    if not isinstance(table, list) or len(table) < 2:
        return 0.0
    ratios = []
    for row in table:
        if not isinstance(row, dict):
            continue
        ratios.append(
            (
                str(row.get("setting", "")),
                _improvement_ratio(row.get("baseline_mae"), row.get("method_mae")),
            )
        )
    low = next((ratio for setting, ratio in ratios if "low" in setting), None)
    high = next((ratio for setting, ratio in ratios if "high" in setting), None)
    if low is None or high is None:
        return 0.0
    return max(0.0, high - low)


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _next_tournament_number(reports: Path) -> int:
    numbers = [
        int(match.group(1))
        for path in reports.glob("substrate-tournament-result-*.json")
        if (match := _RESULT_RE.fullmatch(path.name))
    ]
    return max(numbers, default=0) + 1


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", value).strip("-").lower() or "substrate"


def _metadata(stage: str, role: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "artifact_role": role,
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }


def _relative(path: Path, root: Path) -> str:
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
