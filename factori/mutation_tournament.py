"""Second-generation tournament for creative mutation substrates."""

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
    MutationTournamentComparison,
    MutationTournamentEntry,
    MutationTournamentInspectionReport,
    MutationTournamentResult,
    MutationTournamentSpec,
    PythonExperimentSandboxReport,
    ScientificSubstrate,
    ScientificSubstrateBuildReport,
    SubstrateExperimentResult,
    SubstrateExperimentSpec,
)
from factori.scientific_substrate import latest_scientific_substrate_build
from factori.substrate_tournament import latest_substrate_tournament_result

_DISTANCE_MODEL = "region_specific_distance_decay_gravity"
_HIERARCHICAL_MODEL = "hierarchical_region_cluster_distance_decay"
_HYBRID_MODEL = "gravity_low_rank_residual_hybrid"
_BOUNDARY_MODEL = "boundary_perturbation_distance_decay_robustness"

_DISTANCE_BUNDLE_ID = "distance_decay_spatial_interaction"
_HIERARCHICAL_BUNDLE_ID = "hierarchical_alpha_spatial_interaction"
_HYBRID_BUNDLE_ID = "gravity_low_rank_residual_hybrid"
_BOUNDARY_BUNDLE_ID = "boundary_perturbation_distance_decay"

_DISTANCE_BUNDLE = (
    "tests/fixtures/experiments/bundles/distance_decay_spatial_interaction"
)
_HIERARCHICAL_BUNDLE = (
    "tests/fixtures/experiments/bundles/hierarchical_alpha_spatial_interaction"
)
_HYBRID_BUNDLE = (
    "tests/fixtures/experiments/bundles/gravity_low_rank_residual_hybrid"
)
_BOUNDARY_BUNDLE = (
    "tests/fixtures/experiments/bundles/boundary_perturbation_distance_decay"
)

_RESULT_RE = re.compile(r"^mutation-tournament-result-(\d{4})\.json$")


class MutationTournamentError(RuntimeError):
    """Raised when a mutation tournament cannot run safely."""


@dataclass(frozen=True)
class MutationTournamentRunResult:
    """Persisted second-generation mutation tournament outcome."""

    run_id: str
    tournament_spec: MutationTournamentSpec
    result: MutationTournamentResult
    persistence: PersistenceResult
    spec_artifacts: list[ArtifactRef]
    tournament_spec_artifact: ArtifactRef
    result_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


def run_mutation_tournament(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    sandbox_backend: str = "uv_local",
) -> MutationTournamentRunResult:
    """Run the original winner and mutation substrates through bounded local experiments."""
    if sandbox_backend != "uv_local":
        raise MutationTournamentError("mutation tournament currently supports uv_local only")
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    if not reports.is_dir():
        raise MutationTournamentError(f"Reports directory not found for run_id={run_id}.")
    if validate_ledger_tip(run_id, root=root_path).blocking_findings:
        raise MutationTournamentError("Ledger validation blocks mutation tournament.")

    previous_tournament = latest_substrate_tournament_result(root_path, run_id)
    if previous_tournament is None or not previous_tournament.winner_substrate_id_optional:
        raise MutationTournamentError(
            "Run a substrate tournament with a selected winner before mutation tournament."
        )
    build, substrates, warnings = latest_scientific_substrate_build(root_path, run_id)
    if build is None or not substrates:
        raise MutationTournamentError("Build scientific substrates before mutation tournament.")

    number = _next_tournament_number(reports)
    tournament_id = f"mutation-tournament-{number:04d}"
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
            raise MutationTournamentError(
                "No bounded empirical target claim is available for mutation tournament."
            ) from exc
        target_claim = _target_claim_id(root_path, run_id)
    if target_claim is None:
        raise MutationTournamentError(
            "No bounded empirical target claim is available for mutation tournament."
        )

    substrate_paths = _substrate_paths(root_path, build)
    original = _original_winner(
        substrates,
        previous_tournament.winner_substrate_id_optional,
    )
    mutations = _mutation_substrates(substrates)
    if original is None:
        raise MutationTournamentError("Previous tournament winner substrate is unavailable.")

    routable = [original, *mutations]
    build_path = _latest_build_path(root_path, run_id, build)
    previous_tournament_path = _latest_previous_tournament_path(root_path, run_id)
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
    tournament_spec = MutationTournamentSpec(
        run_id=run_id,
        mutation_tournament_id=tournament_id,
        source_scientific_substrate_build_path_optional=build_path,
        source_substrate_tournament_result_path_optional=previous_tournament_path,
        original_winner_substrate_id=original.substrate_id,
        mutation_substrate_ids=[substrate.substrate_id for substrate in mutations],
        execution_backend="uv_local",
        selection_policy=(
            "Compare the prior bounded synthetic winner against selected mutation substrates. "
            "Score held-out MAE/RMSE improvement, branch-specific robustness or recovery, and "
            "apply a complexity penalty when mutation models add parameters."
        ),
        substrate_count=len(routable),
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

    entries: list[MutationTournamentEntry] = []
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
                original_substrate_id=original.substrate_id,
                spec=spec,
                spec_path=spec_artifact.path,
            )
        )

    original_entry = next(
        (entry for entry in entries if entry.branch_role == "original_winner"),
        None,
    )
    winner = _select_winner(entries)
    entries = _finalize_outcomes(entries, winner, original_entry)
    comparison = _comparison(entries, winner, original)
    completed = [entry for entry in entries if entry.status == "completed"]
    inconclusive = [
        entry for entry in entries if entry.status in {"negative_result", "inconclusive"}
    ]
    failed = [entry for entry in entries if entry.status in {"failed", "not_run"}]
    claim_map = _read_claim_map(root_path, run_id)
    unsupported = (
        len(claim_map.unsupported_non_scaffold_claim_ids) if claim_map is not None else 0
    )
    status = (
        "no_mutation_substrates"
        if not mutations
        else "completed_with_inconclusive_branches"
        if inconclusive or failed
        else "completed"
    )
    result_id = f"mutation-tournament-result-{number:04d}"
    result = MutationTournamentResult(
        run_id=run_id,
        mutation_tournament_id=tournament_id,
        tournament_status=status,
        source_scientific_substrate_build_path_optional=build_path,
        source_substrate_tournament_result_path_optional=previous_tournament_path,
        mutation_tournament_spec_path=(
            f"runs/{run_id}/reports/mutation-tournament-spec-{number:04d}.json"
        ),
        original_winner_substrate_id=original.substrate_id,
        original_winner_title=original.title,
        original_winner_included=original_entry is not None,
        mutation_substrate_count=len(mutations),
        completed_branch_count=len(completed),
        inconclusive_branch_count=len(inconclusive),
        failed_branch_count=len(failed),
        second_generation_winner_selected=winner is not None,
        second_generation_winner_substrate_id_optional=winner.substrate_id if winner else None,
        second_generation_winner_title_optional=winner.title if winner else None,
        second_generation_winner_reason_optional=_winner_reason(winner) if winner else None,
        mutation_improved_over_original=_mutation_improved(winner, original_entry),
        tournament_outcome=comparison.tournament_outcome,
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
    return MutationTournamentRunResult(
        run_id=run_id,
        tournament_spec=tournament_spec,
        result=result,
        persistence=result_persistence,
        spec_artifacts=[spec_artifact_by_id[spec.spec_id] for spec in specs],
        tournament_spec_artifact=spec_artifact_by_id[
            f"mutation-tournament-spec-{number:04d}"
        ],
        result_artifact=result_by_id[result_id],
        markdown_artifact=result_by_id[f"{result_id}-markdown"],
    )


def inspect_mutation_tournament(
    *, run_id: str, root: str | Path = "."
) -> MutationTournamentInspectionReport:
    """Inspect the latest mutation tournament without mutation."""
    root_path = Path(root)
    result = latest_mutation_tournament_result(root_path, run_id)
    if result is None:
        return MutationTournamentInspectionReport(
            run_id=run_id,
            mutation_tournament_present=False,
            warnings=["No mutation tournament report is present."],
            publication_ready=False,
        )
    return MutationTournamentInspectionReport(
        run_id=run_id,
        mutation_tournament_present=True,
        latest_mutation_tournament_id_optional=result.mutation_tournament_id,
        tournament_status_optional=result.tournament_status,
        original_winner_included=result.original_winner_included,
        hierarchical_alpha_branch_completed=any(
            entry.substrate_model_type == _HIERARCHICAL_MODEL
            and entry.status == "completed"
            for entry in result.entries
        ),
        hybrid_low_rank_branch_completed=any(
            entry.substrate_model_type == _HYBRID_MODEL and entry.status == "completed"
            for entry in result.entries
        ),
        boundary_robustness_branch_completed=any(
            entry.substrate_model_type == _BOUNDARY_MODEL and entry.status == "completed"
            for entry in result.entries
        ),
        second_generation_winner_selected=result.second_generation_winner_selected,
        second_generation_winner_title_optional=(
            result.second_generation_winner_title_optional
        ),
        mutation_improved_over_original=result.mutation_improved_over_original,
        comparison_table_present=result.comparison.comparison_table_present,
        entries=result.entries,
        result_optional=result,
        warnings=result.warnings,
        publication_ready=False,
    )


def latest_mutation_tournament_result(
    root: Path,
    run_id: str,
) -> MutationTournamentResult | None:
    """Load the latest immutable mutation tournament result."""
    reports = root / "runs" / run_id / "reports"
    paths = sorted(
        (int(match.group(1)), path)
        for path in reports.glob("mutation-tournament-result-*.json")
        if (match := _RESULT_RE.fullmatch(path.name))
    )
    if not paths:
        return None
    try:
        return MutationTournamentResult.model_validate_json(
            paths[-1][1].read_text(encoding="utf-8")
        )
    except (OSError, ValidationError):
        return None


def render_mutation_tournament_markdown(result: MutationTournamentResult) -> str:
    """Render a concise non-evidence second-generation tournament report."""
    lines = [
        "# Mutation Substrate Tournament",
        "",
        f"- Run ID: `{result.run_id}`",
        f"- Tournament ID: `{result.mutation_tournament_id}`",
        f"- Status: `{result.tournament_status}`",
        f"- Previous winner included: `{str(result.original_winner_included).lower()}`",
        f"- Second-generation winner: `{result.second_generation_winner_title_optional or 'none'}`",
        f"- Outcome: `{result.tournament_outcome}`",
        "- publication_ready: false",
        "",
        "## Comparison",
        "",
        "| branch | role | status | improvement | complexity penalty | robustness | score |",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for entry in result.entries:
        lines.append(
            f"| {entry.title} | {entry.branch_role} | {entry.status} | "
            f"{entry.improvement_ratio:.4f} | "
            f"{(entry.complexity_penalty_optional or 0.0):.4f} | "
            f"{(entry.robustness_metric_optional or 0.0):.4f} | {entry.score:.4f} |"
        )
    lines.extend(
        [
            "",
            "This tournament compares bounded synthetic branches only. It does not create "
            "real-world validation, novelty, correctness, or publication readiness.",
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
    model_type = substrate.concrete_model_object.model_type
    if model_type == _DISTANCE_MODEL:
        return _distance_decay_spec(
            run_id=run_id,
            tournament_id=tournament_id,
            substrate=substrate,
            substrate_path=substrate_path,
            target_claim_id=target_claim_id,
        )
    if model_type == _HIERARCHICAL_MODEL:
        return _hierarchical_alpha_spec(
            run_id=run_id,
            tournament_id=tournament_id,
            substrate=substrate,
            substrate_path=substrate_path,
            target_claim_id=target_claim_id,
        )
    if model_type == _HYBRID_MODEL:
        return _hybrid_spec(
            run_id=run_id,
            tournament_id=tournament_id,
            substrate=substrate,
            substrate_path=substrate_path,
            target_claim_id=target_claim_id,
        )
    if model_type == _BOUNDARY_MODEL:
        return _boundary_spec(
            run_id=run_id,
            tournament_id=tournament_id,
            substrate=substrate,
            substrate_path=substrate_path,
            target_claim_id=target_claim_id,
        )
    raise MutationTournamentError(
        "No approved local mutation tournament experiment supports model type "
        f"{model_type}."
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
        spec_id=f"experiment-spec-mutation-tournament-distance-decay-{tournament_id[-4:]}",
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
            "Generate synthetic OD flows with origin-specific alpha_i.",
            "Split OD pairs deterministically into train and test sets.",
            "Compare pooled-alpha and heterogeneous-alpha models.",
        ],
        baseline_model="pooled-alpha gravity model",
        method_model="heterogeneous-alpha spatial interaction model",
        metric_names=list(design.metrics),
        heterogeneity_settings=["low_heterogeneity", "high_heterogeneity"],
        ablation_or_stress_test=design.ablation_or_stress_test,
        bounded_claim_rule="method_mae < baseline_mae and method_rmse <= baseline_rmse",
        experiment_bundle_id=_DISTANCE_BUNDLE_ID,
    )


def _hierarchical_alpha_spec(
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
        spec_id=f"experiment-spec-mutation-tournament-hierarchical-alpha-{tournament_id[-4:]}",
        target_claim_id=target_claim_id,
        target_section="Bounded Empirical Demonstration",
        hypothesis_or_question=substrate.measurable_hypothesis,
        suggested_dataset="deterministic synthetic OD-flow matrix with alpha clusters",
        suggested_metrics=[
            "test_mae_baseline",
            "test_mae_method",
            "test_rmse_baseline",
            "test_rmse_method",
            "mae_improvement",
            "rmse_improvement",
            "parameter_count_baseline",
            "parameter_count_method",
            "complexity_penalized_score",
            "sample_count",
            "train_pair_count",
            "test_pair_count",
            "seed",
        ],
        suggested_baselines=["pooled-alpha baseline", "full origin-specific alpha model"],
        suggested_seed_policy=design.seed_plan,
        expected_output_artifacts=[
            "metrics.json",
            "outputs/comparison-table.json",
            "substrate-experiment-result.json",
        ],
        experiment_bundle_path_optional=_HIERARCHICAL_BUNDLE,
        template_id_optional="hierarchical_alpha_spatial_interaction_v1",
        template_family_optional="baseline_vs_method",
        sandbox_backend="uv_local",
        requested_dependencies=[],
        allow_network=False,
        seed=3141,
        timeout_seconds=30,
        source_substrate_id=substrate.substrate_id,
        source_substrate_path=substrate_path,
        model_equation=substrate.concrete_model_object.equations[0],
        dgp_steps=[
            "Generate synthetic origin clusters with shared alpha values.",
            "Generate OD flows from a cluster-level distance-decay DGP.",
            "Compare pooled alpha, cluster alpha, and full origin-specific alpha models.",
        ],
        baseline_model="pooled-alpha gravity model and full origin-specific alpha model",
        method_model="cluster-alpha distance-decay model",
        metric_names=list(design.metrics),
        heterogeneity_settings=[
            "low_cluster_alpha_heterogeneity",
            "high_cluster_alpha_heterogeneity",
        ],
        ablation_or_stress_test=design.ablation_or_stress_test,
        bounded_claim_rule="method_mae < baseline_mae and method_rmse <= baseline_rmse",
        experiment_bundle_id=_HIERARCHICAL_BUNDLE_ID,
    )


def _hybrid_spec(
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
        spec_id=f"experiment-spec-mutation-tournament-gravity-low-rank-hybrid-{tournament_id[-4:]}",
        target_claim_id=target_claim_id,
        target_section="Bounded Empirical Demonstration",
        hypothesis_or_question=substrate.measurable_hypothesis,
        suggested_dataset="deterministic synthetic OD-flow matrix with residual factors",
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
        suggested_baselines=["distance-decay winner without residual correction"],
        suggested_seed_policy=design.seed_plan,
        expected_output_artifacts=[
            "metrics.json",
            "outputs/comparison-table.json",
            "substrate-experiment-result.json",
        ],
        experiment_bundle_path_optional=_HYBRID_BUNDLE,
        template_id_optional="gravity_low_rank_residual_hybrid_v1",
        template_family_optional="baseline_vs_method",
        sandbox_backend="uv_local",
        requested_dependencies=[],
        allow_network=False,
        seed=4242,
        timeout_seconds=30,
        source_substrate_id=substrate.substrate_id,
        source_substrate_path=substrate_path,
        model_equation="; ".join(substrate.concrete_model_object.equations),
        dgp_steps=[
            "Generate OD flows with heterogeneous alpha_i and latent low-rank residuals.",
            "Fit the distance-decay winner as baseline.",
            "Fit a distance-decay plus low-rank residual correction method.",
        ],
        baseline_model="distance-decay winner without residual correction",
        method_model="distance-decay plus rank-k low-rank residual correction",
        metric_names=list(design.metrics),
        heterogeneity_settings=["low_residual_factor_strength", "high_residual_factor_strength"],
        ablation_or_stress_test=design.ablation_or_stress_test,
        bounded_claim_rule="method_mae < baseline_mae and method_rmse <= baseline_rmse",
        experiment_bundle_id=_HYBRID_BUNDLE_ID,
    )


def _boundary_spec(
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
        spec_id=f"experiment-spec-mutation-tournament-boundary-perturbation-{tournament_id[-4:]}",
        target_claim_id=target_claim_id,
        target_section="Bounded Empirical Demonstration",
        hypothesis_or_question=substrate.measurable_hypothesis,
        suggested_dataset="deterministic synthetic fine/coarse OD-flow boundaries",
        suggested_metrics=[
            "test_mae_baseline",
            "test_mae_method",
            "test_rmse_baseline",
            "test_rmse_method",
            "mae_improvement",
            "rmse_improvement",
            "robustness_ratio",
            "performance_degradation",
            "sample_count",
            "train_pair_count",
            "test_pair_count",
            "seed",
        ],
        suggested_baselines=["pooled-alpha model under the same boundary perturbation"],
        suggested_seed_policy=design.seed_plan,
        expected_output_artifacts=[
            "metrics.json",
            "outputs/comparison-table.json",
            "substrate-experiment-result.json",
        ],
        experiment_bundle_path_optional=_BOUNDARY_BUNDLE,
        template_id_optional="boundary_perturbation_distance_decay_v1",
        template_family_optional="robustness_sanity_check",
        sandbox_backend="uv_local",
        requested_dependencies=[],
        allow_network=False,
        seed=5151,
        timeout_seconds=30,
        source_substrate_id=substrate.substrate_id,
        source_substrate_path=substrate_path,
        model_equation="; ".join(substrate.concrete_model_object.equations),
        dgp_steps=[
            "Generate fine synthetic regions with origin-specific alpha_i.",
            "Aggregate or perturb boundaries into coarser regions.",
            "Compare pooled and heterogeneous alpha under original and perturbed boundaries.",
        ],
        baseline_model="pooled-alpha model under the same perturbed aggregation",
        method_model="heterogeneous-alpha model under the same perturbed aggregation",
        metric_names=list(design.metrics),
        heterogeneity_settings=["original_boundaries", "perturbed_boundaries"],
        ablation_or_stress_test=design.ablation_or_stress_test,
        bounded_claim_rule="method_mae < baseline_mae and method_rmse <= baseline_rmse",
        experiment_bundle_id=_BOUNDARY_BUNDLE_ID,
    )


def _execute_entry(
    *,
    root: Path,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    tournament_id: str,
    substrate: ScientificSubstrate,
    original_substrate_id: str,
    spec: SubstrateExperimentSpec,
    spec_path: str,
) -> MutationTournamentEntry:
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
    supported = bool(substrate_result.claim_support_satisfied if substrate_result else False)
    status = (
        "completed"
        if completed and supported
        else "negative_result"
        if completed and substrate_result is not None
        else "inconclusive"
        if completed
        else "failed"
    )
    score_payload = _score_metrics(
        metrics,
        model_type=substrate.concrete_model_object.model_type,
        completed=completed,
        supported=supported,
    )
    return MutationTournamentEntry(
        entry_id=f"{tournament_id}-{_safe_id(substrate.substrate_id)}",
        substrate_id=substrate.substrate_id,
        title=substrate.title,
        substrate_model_type=substrate.concrete_model_object.model_type,
        branch_role=(
            "original_winner"
            if substrate.substrate_id == original_substrate_id
            else "mutation"
        ),
        experiment_bundle_id=spec.experiment_bundle_id,
        status=status,
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
        result_label_optional=substrate_result.result_label if substrate_result else None,
        primary_metric=score_payload["primary_metric"],
        baseline_metric=score_payload["baseline_metric"],
        method_metric=score_payload["method_metric"],
        improvement_ratio=score_payload["improvement_ratio"],
        complexity_penalty_optional=score_payload["complexity_penalty"],
        robustness_metric_optional=score_payload["robustness_metric"],
        raw_score=score_payload["raw_score"],
        complexity_adjusted_score=score_payload["complexity_adjusted_score"],
        score=score_payload["score"],
        outcome_label="mutation_inconclusive",
        selected_as_second_generation_winner=False,
        warnings=warnings,
        publication_ready=False,
    )


def _score_metrics(
    metrics: dict[str, Any],
    *,
    model_type: str,
    completed: bool,
    supported: bool,
) -> dict[str, float]:
    baseline = _float_or_zero(metrics.get("test_mae_baseline"))
    method = _float_or_zero(metrics.get("test_mae_method"))
    mae_ratio = _improvement_ratio(
        metrics.get("test_mae_baseline"), metrics.get("test_mae_method")
    )
    rmse_ratio = _improvement_ratio(
        metrics.get("test_rmse_baseline"), metrics.get("test_rmse_method")
    )
    robustness = _robustness_metric(metrics, model_type)
    complexity_penalty = _complexity_penalty(metrics, model_type)
    failure_penalty = 0.0 if completed and supported else 1.0
    raw_score = mae_ratio + rmse_ratio + robustness
    adjusted = raw_score - complexity_penalty
    score = adjusted - failure_penalty
    return {
        "primary_metric": round(mae_ratio, 6),
        "baseline_metric": round(baseline, 6),
        "method_metric": round(method, 6),
        "improvement_ratio": round(mae_ratio, 6),
        "complexity_penalty": round(complexity_penalty, 6),
        "robustness_metric": round(robustness, 6),
        "raw_score": round(raw_score, 6),
        "complexity_adjusted_score": round(adjusted, 6),
        "score": round(score, 6),
    }


def _robustness_metric(metrics: dict[str, Any], model_type: str) -> float:
    if model_type == _HYBRID_MODEL:
        return (
            0.25 * max(0.0, _float_or_zero(metrics.get("latent_factor_recovery_correlation")))
            + 0.15 * max(0.0, _float_or_zero(metrics.get("explained_residual_variance")))
            + 0.2 * _ablation_sensitivity(metrics.get("comparison_table"))
        )
    if model_type == _BOUNDARY_MODEL:
        return (
            0.45 * max(0.0, _float_or_zero(metrics.get("robustness_ratio")))
            - 0.1 * max(0.0, _float_or_zero(metrics.get("performance_degradation")))
        )
    if model_type == _HIERARCHICAL_MODEL:
        return (
            0.2 * _ablation_sensitivity(metrics.get("comparison_table"))
            + 0.15 * max(0.0, _float_or_zero(metrics.get("complexity_penalized_score")))
        )
    return 0.2 * _ablation_sensitivity(metrics.get("comparison_table"))


def _complexity_penalty(metrics: dict[str, Any], model_type: str) -> float:
    baseline = _float_or_none(metrics.get("parameter_count_baseline"))
    method = _float_or_none(metrics.get("parameter_count_method"))
    if baseline is None or method is None:
        if model_type == _HYBRID_MODEL:
            return 0.08
        if model_type == _BOUNDARY_MODEL:
            return 0.03
        return 0.05 if model_type != _DISTANCE_MODEL else 0.04
    return min(0.35, max(0.0, method - baseline) * 0.008)


def _comparison(
    entries: list[MutationTournamentEntry],
    winner: MutationTournamentEntry | None,
    original: ScientificSubstrate,
) -> MutationTournamentComparison:
    rows = [
        {
            "substrate_id": entry.substrate_id,
            "title": entry.title,
            "branch_role": entry.branch_role,
            "bundle": entry.experiment_bundle_id,
            "status": entry.status,
            "primary_metric": entry.primary_metric,
            "baseline_metric": entry.baseline_metric,
            "method_metric": entry.method_metric,
            "improvement_ratio": entry.improvement_ratio,
            "complexity_penalty": entry.complexity_penalty_optional,
            "robustness_metric": entry.robustness_metric_optional,
            "raw_score": entry.raw_score,
            "complexity_adjusted_score": entry.complexity_adjusted_score,
            "score": entry.score,
            "selected_as_second_generation_winner": (
                winner is not None and entry.substrate_id == winner.substrate_id
            ),
        }
        for entry in entries
    ]
    return MutationTournamentComparison(
        comparison_policy=(
            "Within synthetic scope, compare each branch by held-out error improvement, "
            "branch-specific robustness/recovery, and complexity penalty. This selects the "
            "next manuscript focus only and is not real-world validation."
        ),
        metric_names=[
            "improvement_ratio",
            "complexity_penalty",
            "robustness_metric",
            "raw_score",
            "complexity_adjusted_score",
            "score",
        ],
        rows=rows,
        original_winner_substrate_id=original.substrate_id,
        second_generation_winner_substrate_id_optional=(
            winner.substrate_id if winner else None
        ),
        second_generation_winner_title_optional=winner.title if winner else None,
        tournament_outcome=_tournament_outcome(winner, original),
        mutation_improved_over_original=(
            winner is not None and winner.substrate_id != original.substrate_id
        ),
        comparison_table_present=bool(rows),
        publication_ready=False,
    )


def _persist_tournament_specs(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    tournament_id: str,
    tournament_spec: MutationTournamentSpec,
    specs: list[SubstrateExperimentSpec],
) -> PersistenceResult:
    metadata = _metadata("mutation_tournament", "mutation_tournament_context")
    number = tournament_id[-4:]
    artifact_specs = [
        ArtifactWriteSpec(spec.spec_id, ArtifactType.REPORT, spec, "json", metadata)
        for spec in specs
    ]
    artifact_specs.append(
        ArtifactWriteSpec(
            f"mutation-tournament-spec-{number}",
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
        action_type=ControllerActionType.MUTATION_TOURNAMENT_RUN,
        commit_payload={
            "run_id": run_id,
            "mutation_tournament_id": tournament_id,
            "spec_count": len(specs),
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )


def _persist_tournament_result(
    *,
    result: MutationTournamentResult,
    result_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PersistenceResult:
    metadata = _metadata("mutation_tournament", "mutation_tournament_context")
    return persist_artifacts_with_commit(
        run_id=result.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(result_id, ArtifactType.REPORT, result, "json", metadata),
            ArtifactWriteSpec(
                f"{result_id}-markdown",
                ArtifactType.REPORT,
                render_mutation_tournament_markdown(result),
                "markdown",
                metadata,
                filename_stem=result_id,
            ),
        ],
        action_type=ControllerActionType.MUTATION_TOURNAMENT_RUN,
        commit_payload={
            "run_id": result.run_id,
            "mutation_tournament_id": result.mutation_tournament_id,
            "tournament_status": result.tournament_status,
            "second_generation_winner_substrate_id": (
                result.second_generation_winner_substrate_id_optional
            ),
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )


def _finalize_outcomes(
    entries: list[MutationTournamentEntry],
    winner: MutationTournamentEntry | None,
    original: MutationTournamentEntry | None,
) -> list[MutationTournamentEntry]:
    original_score = original.score if original is not None else 0.0
    finalized: list[MutationTournamentEntry] = []
    for entry in entries:
        label = "original_winner_baseline"
        if entry.branch_role == "mutation":
            if entry.status in {"failed", "not_run", "inconclusive"}:
                label = "mutation_inconclusive"
            elif entry.status == "negative_result":
                label = "mutation_failed"
            elif entry.score > original_score + 0.01:
                label = "mutation_improved"
            elif abs(entry.score - original_score) <= 0.01:
                label = "mutation_matched"
            else:
                label = "mutation_failed"
        finalized.append(
            entry.model_copy(
                update={
                    "outcome_label": label,
                    "selected_as_second_generation_winner": (
                        winner is not None and entry.substrate_id == winner.substrate_id
                    ),
                }
            )
        )
    return finalized


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


def _original_winner(
    substrates: list[ScientificSubstrate],
    winner_id: str,
) -> ScientificSubstrate | None:
    return next((item for item in substrates if item.substrate_id == winner_id), None)


def _mutation_substrates(substrates: list[ScientificSubstrate]) -> list[ScientificSubstrate]:
    supported = {
        _HIERARCHICAL_MODEL,
        _HYBRID_MODEL,
        _BOUNDARY_MODEL,
    }
    ordered = [
        substrate
        for substrate in substrates
        if substrate.concrete_model_object.model_type in supported
    ]
    return sorted(ordered, key=lambda item: item.title)


def _substrate_paths(root: Path, build: ScientificSubstrateBuildReport) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in build.substrate_paths:
        if "scientific-substrate-" not in path or not path.endswith(".json"):
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


def _latest_previous_tournament_path(root: Path, run_id: str) -> str | None:
    reports = root / "runs" / run_id / "reports"
    paths = sorted(
        (int(match.group(1)), path)
        for path in reports.glob("substrate-tournament-result-*.json")
        if (match := re.fullmatch(r"substrate-tournament-result-(\d{4})\.json", path.name))
    )
    return _relative(paths[-1][1], root) if paths else None


def _select_winner(entries: list[MutationTournamentEntry]) -> MutationTournamentEntry | None:
    viable = [entry for entry in entries if entry.status == "completed"]
    if not viable:
        return None
    return sorted(
        viable,
        key=lambda entry: (-entry.score, entry.title, entry.substrate_id),
    )[0]


def _winner_reason(entry: MutationTournamentEntry | None) -> str | None:
    if entry is None:
        return None
    return (
        f"{entry.title} has the strongest bounded synthetic mutation-tournament score "
        f"({entry.score}) after complexity and robustness accounting; this selects the "
        "next manuscript focus only."
    )


def _mutation_improved(
    winner: MutationTournamentEntry | None,
    original: MutationTournamentEntry | None,
) -> bool:
    return bool(
        winner is not None
        and original is not None
        and winner.substrate_id != original.substrate_id
        and winner.score > original.score + 0.01
    )


def _tournament_outcome(
    winner: MutationTournamentEntry | None,
    original: ScientificSubstrate,
) -> str:
    if winner is None:
        return "all_mutations_inconclusive"
    if winner.substrate_id == original.substrate_id:
        return "original_winner_remains_best"
    if winner.substrate_model_type == _HIERARCHICAL_MODEL:
        return "hierarchical_alpha_wins_by_parsimony"
    if winner.substrate_model_type == _HYBRID_MODEL:
        return "hybrid_wins_when_low_rank_residual_structure_exists"
    if winner.substrate_model_type == _BOUNDARY_MODEL:
        return "robustness_branch_wins_by_stability"
    return "original_winner_remains_best"


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


def _float_or_zero(value: Any) -> float:
    parsed = _float_or_none(value)
    return parsed if parsed is not None else 0.0


def _next_tournament_number(reports: Path) -> int:
    numbers = [
        int(match.group(1))
        for path in reports.glob("mutation-tournament-result-*.json")
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


__all__ = [
    "MutationTournamentError",
    "MutationTournamentRunResult",
    "inspect_mutation_tournament",
    "latest_mutation_tournament_result",
    "render_mutation_tournament_markdown",
    "run_mutation_tournament",
]
