"""Deterministic scientific-substrate generation from idea-space mutation axes."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from factori.artifacts import ArtifactStore
from factori.idea_space import IdeaSpaceError, inspect_idea_space
from factori.idea_tree import IdeaTreeError, inspect_idea_tree
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    IdeaSpaceInspectionReport,
    ScientificSubstrate,
    ScientificSubstrateAssumption,
    ScientificSubstrateBuildReport,
    ScientificSubstrateExperimentDesign,
    ScientificSubstrateInspectionReport,
    ScientificSubstrateModelObject,
    ScientificSubstrateResultSchema,
    ScientificSubstrateVariable,
)

_BUILD_RE = re.compile(r"^scientific-substrate-build-(\d{4})\.json$")
_SUBSTRATE_RE = re.compile(r"^scientific-substrate-(\d{4})-.+\.json$")
_DISTANCE_AXIS = "region-specific distance-decay gravity model"
_PCA_AXIS = "PCA/low-rank OD-flow representation model"


class ScientificSubstrateError(RuntimeError):
    """Raised when scientific substrates cannot be built or inspected."""


@dataclass(frozen=True)
class ScientificSubstrateBuildResult:
    """Persisted scientific-substrate build result."""

    run_id: str
    report: ScientificSubstrateBuildReport
    substrates: list[ScientificSubstrate]
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    substrate_artifacts: list[ArtifactRef]


def build_scientific_substrate(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    max_substrates: int = 2,
    mutation_axis: str | None = None,
) -> ScientificSubstrateBuildResult:
    """Build and persist concrete scientific substrates from idea-space axes."""
    if max_substrates < 1:
        raise ScientificSubstrateError("max_substrates must be at least 1.")
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    if not reports.is_dir():
        raise ScientificSubstrateError(f"Reports directory not found for run_id={run_id}.")

    build_number = _next_build_number(reports)
    build_id = f"scientific-substrate-build-{build_number:04d}"
    idea_space, idea_space_path, warnings = _load_or_build_idea_space(run_id, root_path)
    idea_tree = None
    try:
        idea_tree = inspect_idea_tree(run_id=run_id, root=root_path)
    except IdeaTreeError as exc:
        warnings.append(f"IdeaTree inspection unavailable: {exc}")
    domain = _resolve_domain(idea_space, idea_tree)
    source_node_id = _source_node_id(idea_tree)
    axes = _select_axes(
        recommended_axes=idea_space.recommended_mutation_axes,
        domain=domain,
        max_substrates=max_substrates,
        mutation_axis=mutation_axis,
    )
    substrates = [
        _substrate_for_axis(
            run_id=run_id,
            axis=axis,
            domain=domain,
            source_node_id=source_node_id,
            selected=(index == 0 and _axis_is_distance(axis)),
        )
        for index, axis in enumerate(axes)
    ]
    if substrates and not any(substrate.selected_for_next_experiment for substrate in substrates):
        substrates[0] = substrates[0].model_copy(
            update={"selected_for_next_experiment": True}
        )

    substrate_paths = [
        f"runs/{run_id}/reports/{_substrate_artifact_id(build_number, index, substrate)}.json"
        for index, substrate in enumerate(substrates, start=1)
    ]
    selected = next(
        (substrate for substrate in substrates if substrate.selected_for_next_experiment),
        None,
    )
    report = ScientificSubstrateBuildReport(
        run_id=run_id,
        build_id=build_id,
        build_status="completed_with_warnings" if warnings else "completed",
        source_idea_space_report_path_optional=(
            _relative(idea_space_path, root_path) if idea_space_path else None
        ),
        source_idea_tree_report_path_optional=None,
        requested_mutation_axis_optional=mutation_axis,
        max_substrates=max_substrates,
        recommended_mutation_axes=idea_space.recommended_mutation_axes,
        built_mutation_axes=axes,
        substrate_paths=substrate_paths,
        substrate_count=len(substrates),
        selected_substrate_id_optional=selected.substrate_id if selected else None,
        selected_substrate_title_optional=selected.title if selected else None,
        pca_low_rank_substrate_id_optional=next(
            (
                substrate.substrate_id
                for substrate in substrates
                if _axis_is_pca(substrate.source_mutation_axis_optional or "")
            ),
            None,
        ),
        warnings=warnings,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )
    metadata = {
        "stage": "scientific_substrate_build",
        "artifact_role": "scientific_substrate_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }
    specs: list[ArtifactWriteSpec] = []
    for index, substrate in enumerate(substrates, start=1):
        specs.append(
            ArtifactWriteSpec(
                _substrate_artifact_id(build_number, index, substrate),
                ArtifactType.REPORT,
                substrate,
                "json",
                metadata,
            )
        )
    specs.append(ArtifactWriteSpec(build_id, ArtifactType.REPORT, report, "json", metadata))
    specs.append(
        ArtifactWriteSpec(
            f"{build_id}-markdown",
            ArtifactType.REPORT,
            render_scientific_substrate_build_markdown(report, substrates),
            "markdown",
            metadata,
            filename_stem=build_id,
        )
    )
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.SCIENTIFIC_SUBSTRATE_BUILT,
        commit_payload={
            "run_id": run_id,
            "build_id": build_id,
            "substrate_count": len(substrates),
            "selected_substrate_id": selected.substrate_id if selected else None,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return ScientificSubstrateBuildResult(
        run_id=run_id,
        report=report,
        substrates=substrates,
        persistence=persistence,
        report_artifact=by_id[build_id],
        substrate_artifacts=[
            by_id[_substrate_artifact_id(build_number, index, substrate)]
            for index, substrate in enumerate(substrates, start=1)
        ],
    )


def inspect_scientific_substrate(
    *,
    run_id: str,
    root: str | Path = ".",
) -> ScientificSubstrateInspectionReport:
    """Inspect the latest generated scientific substrates without mutation."""
    root_path = Path(root)
    report, substrates, warnings = latest_scientific_substrate_build(root_path, run_id)
    if report is None:
        return ScientificSubstrateInspectionReport(
            run_id=run_id,
            scientific_substrate_present=False,
            latest_build_id_optional=None,
            latest_build_status_optional=None,
            substrate_count=0,
            selected_substrate_id_optional=None,
            selected_substrate_title_optional=None,
            selected_mutation_axis_optional=None,
            pca_low_rank_substrate_present=False,
            equation_present=False,
            baseline_present=False,
            experiment_design_present=False,
            result_schema_present=False,
            substrate_paths=[],
            source_mutation_axes=[],
            substrates=[],
            warnings=warnings,
            publication_ready=False,
            creates_scientific_validation=False,
            implies_publication_readiness=False,
            is_verification_evidence=False,
        )
    selected = next(
        (substrate for substrate in substrates if substrate.selected_for_next_experiment),
        None,
    )
    return ScientificSubstrateInspectionReport(
        run_id=run_id,
        scientific_substrate_present=True,
        latest_build_id_optional=report.build_id,
        latest_build_status_optional=report.build_status,
        substrate_count=len(substrates),
        selected_substrate_id_optional=selected.substrate_id if selected else None,
        selected_substrate_title_optional=selected.title if selected else None,
        selected_mutation_axis_optional=(
            selected.source_mutation_axis_optional if selected else None
        ),
        pca_low_rank_substrate_present=any(
            _axis_is_pca(substrate.source_mutation_axis_optional or "")
            for substrate in substrates
        ),
        equation_present=any(
            substrate.concrete_model_object.equations for substrate in substrates
        ),
        baseline_present=all(bool(substrate.baseline) for substrate in substrates),
        experiment_design_present=all(
            bool(substrate.experiment_design.target_claim) for substrate in substrates
        ),
        result_schema_present=all(
            bool(substrate.result_schema.required_table_columns) for substrate in substrates
        ),
        substrate_paths=report.substrate_paths,
        source_mutation_axes=[
            substrate.source_mutation_axis_optional or "" for substrate in substrates
        ],
        substrates=substrates,
        warnings=[*report.warnings, *warnings],
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def latest_selected_scientific_substrate(
    root: Path,
    run_id: str,
) -> tuple[ScientificSubstrate | None, list[ScientificSubstrate]]:
    """Return the latest selected substrate and all latest substrates."""
    _, substrates, _ = latest_scientific_substrate_build(root, run_id)
    selected = next(
        (substrate for substrate in substrates if substrate.selected_for_next_experiment),
        None,
    )
    return selected, substrates


def latest_scientific_substrate_build(
    root: Path,
    run_id: str,
) -> tuple[ScientificSubstrateBuildReport | None, list[ScientificSubstrate], list[str]]:
    """Load the latest substrate build report and substrates."""
    reports = root / "runs" / run_id / "reports"
    warnings: list[str] = []
    paths = sorted(
        (int(match.group(1)), path)
        for path in reports.glob("scientific-substrate-build-*.json")
        if (match := _BUILD_RE.fullmatch(path.name))
    )
    if not paths:
        return None, [], []
    report_path = paths[-1][1]
    try:
        report = ScientificSubstrateBuildReport.model_validate_json(
            report_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None, [], ["Latest scientific substrate build report is corrupt."]
    substrates: list[ScientificSubstrate] = []
    for substrate_path in report.substrate_paths:
        path = root / substrate_path
        try:
            substrates.append(
                ScientificSubstrate.model_validate_json(path.read_text(encoding="utf-8"))
            )
        except (OSError, ValueError):
            warnings.append(f"Scientific substrate artifact is unavailable: {substrate_path}")
    return report, substrates, warnings


def render_scientific_substrate_build_markdown(
    report: ScientificSubstrateBuildReport,
    substrates: list[ScientificSubstrate],
) -> str:
    """Render a concise context-only substrate build report."""
    lines = [
        "# Scientific Substrate Build Report",
        "",
        f"Run ID: `{report.run_id}`",
        f"Build ID: `{report.build_id}`",
        f"Status: `{report.build_status}`",
        f"Substrates: `{report.substrate_count}`",
        f"Selected substrate: `{report.selected_substrate_title_optional or 'none'}`",
        "",
        "## Substrates",
        "",
    ]
    for substrate in substrates:
        lines.extend(
            [
                f"### {substrate.title}",
                "",
                f"- Mutation axis: `{substrate.source_mutation_axis_optional}`",
                (
                    "- Selected for next experiment: "
                    f"`{str(substrate.selected_for_next_experiment).lower()}`"
                ),
                f"- Model type: `{substrate.concrete_model_object.model_type}`",
                f"- Equations: {'; '.join(substrate.concrete_model_object.equations)}",
                f"- Baseline: {substrate.baseline}",
                f"- Metrics: {', '.join(substrate.experiment_design.metrics)}",
                "",
            ]
        )
    lines.extend(
        [
            "## Evidence Boundary",
            "",
            "Substrate artifacts are context for future scientific work. They do not create "
            "proof, experiment evidence, validation, or publication readiness.",
            "",
            "- publication_ready: false",
            "- creates_scientific_validation: false",
            "- implies_publication_readiness: false",
            "- is_verification_evidence: false",
            "",
        ]
    )
    return "\n".join(lines)


def _load_or_build_idea_space(
    run_id: str,
    root: Path,
) -> tuple[IdeaSpaceInspectionReport, Path | None, list[str]]:
    reports = root / "runs" / run_id / "reports"
    paths = sorted(
        path
        for path in reports.glob("idea-space-report-*.json")
        if not path.name.endswith(".meta.json")
    )
    if paths:
        try:
            return (
                IdeaSpaceInspectionReport.model_validate_json(
                    paths[-1].read_text(encoding="utf-8")
                ),
                paths[-1],
                [],
            )
        except (OSError, ValueError):
            pass
    try:
        return inspect_idea_space(run_id=run_id, root=root), None, [
            "No persisted idea-space JSON report was available; built a read-only diagnostic."
        ]
    except IdeaSpaceError as exc:
        raise ScientificSubstrateError(str(exc)) from exc


def _select_axes(
    *,
    recommended_axes: list[str],
    domain: str,
    max_substrates: int,
    mutation_axis: str | None,
) -> list[str]:
    if mutation_axis:
        return [mutation_axis]
    axes = list(recommended_axes)
    if "human geography" in domain.lower() or "spatial" in domain.lower():
        axes = _prioritize(_DISTANCE_AXIS, axes)
        axes = _prioritize(_PCA_AXIS, axes, after=_DISTANCE_AXIS)
    if not axes:
        axes = [_DISTANCE_AXIS, _PCA_AXIS]
    return axes[:max_substrates]


def _substrate_for_axis(
    *,
    run_id: str,
    axis: str,
    domain: str,
    source_node_id: str | None,
    selected: bool,
) -> ScientificSubstrate:
    if _axis_is_pca(axis):
        return _pca_substrate(run_id, axis, domain, source_node_id, selected)
    if _axis_is_distance(axis) or "gravity" in axis.lower():
        return _distance_decay_substrate(run_id, axis, domain, source_node_id, selected)
    return _fallback_substrate(run_id, axis, domain, source_node_id, selected)


def _distance_decay_substrate(
    run_id: str,
    axis: str,
    domain: str,
    source_node_id: str | None,
    selected: bool,
) -> ScientificSubstrate:
    return ScientificSubstrate(
        substrate_id="scientific-substrate-distance-decay-gravity",
        run_id=run_id,
        source_idea_node_id_optional=source_node_id,
        source_mutation_axis_optional=axis,
        title="Region-Specific Distance Decay in a Synthetic Spatial Interaction Model",
        domain=domain,
        domain_problem=(
            "Spatial heterogeneity in origin-destination flows may arise because regions differ "
            "in how strongly distance suppresses interaction."
        ),
        central_tension=(
            "A pooled gravity model is simple and interpretable, but it can erase region-specific "
            "distance-decay structure."
        ),
        concrete_model_object=ScientificSubstrateModelObject(
            model_type="region_specific_distance_decay_gravity",
            equations=["F_ij = A_i B_j d_ij^{-alpha_i} exp(epsilon_ij)"],
            algorithm_optional=(
                "Estimate a pooled-alpha gravity baseline and a heterogeneous-alpha model on "
                "synthetic training OD pairs, then compare held-out reconstruction metrics."
            ),
            parameter_interpretation=[
                "A_i is origin mass.",
                "B_j is destination attractiveness.",
                "alpha_i is the origin-specific distance-decay parameter.",
                "epsilon_ij is multiplicative noise on the OD flow.",
            ],
            identifiability_notes=(
                "The synthetic DGP must vary alpha_i enough to distinguish heterogeneity from "
                "noise and mass terms; masses and attractiveness are generated rather than "
                "estimated from private data."
            ),
            what_would_falsify_it=(
                "If the heterogeneous-alpha model does not improve held-out MAE or RMSE over the "
                "pooled-alpha baseline when alpha_i varies, the bounded hypothesis is not "
                "supported."
            ),
        ),
        variables_and_notation=[
            ScientificSubstrateVariable(symbol="i,j", definition="regions", role="indices"),
            ScientificSubstrateVariable(
                symbol="F_ij", definition="origin-destination flow", role="response"
            ),
            ScientificSubstrateVariable(symbol="A_i", definition="origin mass", role="covariate"),
            ScientificSubstrateVariable(
                symbol="B_j", definition="destination attractiveness", role="covariate"
            ),
            ScientificSubstrateVariable(symbol="d_ij", definition="distance", role="covariate"),
            ScientificSubstrateVariable(
                symbol="alpha_i",
                definition="origin-specific distance-decay parameter",
                role="heterogeneity parameter",
            ),
            ScientificSubstrateVariable(symbol="epsilon_ij", definition="noise", role="noise"),
        ],
        assumptions=[
            ScientificSubstrateAssumption(
                assumption_id="distance-positive",
                statement="Distances d_ij are positive for all modeled OD pairs.",
                rationale="The power-law distance-decay term requires positive distances.",
                violation_consequence="Zero or negative distances make the model undefined.",
            ),
            ScientificSubstrateAssumption(
                assumption_id="synthetic-alpha-variation",
                statement="The synthetic DGP includes controlled variation in alpha_i.",
                rationale="The experiment tests recovery of region-specific distance decay.",
                violation_consequence=(
                    "If alpha_i is constant, the heterogeneous model should not win."
                ),
            ),
        ],
        mechanism=(
            "Origin-specific alpha_i values encode heterogeneous friction of distance across "
            "regions while masses and attractiveness control gross flow scale."
        ),
        dgp_or_dataset=(
            "Generate synthetic regions, distances, origin masses, destination attractiveness, "
            "heterogeneous alpha_i values, and noisy OD flows under fixed seeds."
        ),
        baseline="pooled-alpha gravity baseline",
        measurable_hypothesis=(
            "A heterogeneous-alpha spatial interaction model improves held-out OD-flow "
            "reconstruction error over a pooled-alpha gravity baseline on a synthetic "
            "OD-flow matrix."
        ),
        experiment_design=ScientificSubstrateExperimentDesign(
            target_claim=(
                "The heterogeneous-alpha spatial interaction model reports lower held-out MAE and "
                "RMSE than the pooled-alpha baseline for the configured synthetic OD-flow run."
            ),
            data_regime="SyntheticOnly",
            dgp=(
                "Generate synthetic regions, distances, origin masses, destination attractiveness, "
                "heterogeneous alpha_i values, and noisy OD flows."
            ),
            train_test_split_optional=(
                "Deterministic split of OD pairs into train and held-out sets."
            ),
            baseline="pooled-alpha gravity baseline",
            method="heterogeneous-alpha spatial interaction model",
            metrics=["MAE", "RMSE"],
            seed_plan="Fixed deterministic seeds recorded in the experiment artifact.",
            ablation_or_stress_test=(
                "Vary heterogeneity strength or noise level and check whether the "
                "heterogeneous-alpha advantage weakens when alpha_i is nearly constant."
            ),
            success_criterion=(
                "Held-out MAE and RMSE are lower for the heterogeneous-alpha model than for the "
                "pooled-alpha gravity baseline in the configured synthetic run."
            ),
            failure_criterion=(
                "The heterogeneous-alpha model fails to improve either MAE or RMSE, or the "
                "advantage disappears outside the intended heterogeneity regime."
            ),
        ),
        result_schema=ScientificSubstrateResultSchema(
            baseline_metric_names=["baseline_MAE", "baseline_RMSE"],
            method_metric_names=["method_MAE", "method_RMSE"],
            comparison_direction="lower_is_better",
            required_table_columns=[
                "seed",
                "n_regions",
                "n_od_pairs",
                "alpha_heterogeneity_strength",
                "noise_level",
                "baseline_MAE",
                "baseline_RMSE",
                "method_MAE",
                "method_RMSE",
            ],
            claim_supported_if=(
                "method_MAE < baseline_MAE and method_RMSE < baseline_RMSE for the configured "
                "synthetic run, with sample count and seed recorded."
            ),
            claim_not_supported_if=(
                "The method does not improve MAE or RMSE, metrics are missing, or the run is "
                "failed, inconclusive, or outside the declared synthetic DGP."
            ),
        ),
        limitations=[
            "Synthetic OD flows do not establish real-world empirical validity.",
            (
                "The model assumes generated masses, attractiveness, and distances are "
                "adequate for a bounded demonstration."
            ),
            "A lower held-out error in this synthetic setting is not a broad validation claim.",
        ],
        failure_modes=[
            "alpha_i variation is too small to identify heterogeneous distance decay.",
            "Noise dominates distance-decay structure.",
            "The flexible model overfits training OD pairs and fails on held-out flows.",
        ],
        evidence_boundary=(
            "This substrate is a planned scientific object only. It becomes scoped experiment "
            "evidence only if a completed artifact passes intake for the mapped claim."
        ),
        selected_for_next_experiment=selected,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _pca_substrate(
    run_id: str,
    axis: str,
    domain: str,
    source_node_id: str | None,
    selected: bool,
) -> ScientificSubstrate:
    return ScientificSubstrate(
        substrate_id="scientific-substrate-low-rank-od-flow-residuals",
        run_id=run_id,
        source_idea_node_id_optional=source_node_id,
        source_mutation_axis_optional=axis,
        title="Low-Rank Residual Axes for Synthetic Spatial Heterogeneity in OD Flows",
        domain=domain,
        domain_problem=(
            "Spatial heterogeneity can remain in OD flows after a pooled gravity baseline removes "
            "the dominant distance-decay pattern."
        ),
        central_tension=(
            "Raw residual inspection is transparent but noisy; a low-rank residual representation "
            "can expose latent heterogeneity axes while risking overinterpretation."
        ),
        concrete_model_object=ScientificSubstrateModelObject(
            model_type="low_rank_gravity_residual_representation",
            equations=[
                "\\hat F^{(0)}_{ij} = \\hat A_i \\hat B_j d_{ij}^{-\\hat\\alpha}",
                "R_{ij} = log(F_{ij} + c) - log(\\hat F^{(0)}_{ij} + c)",
                "R ≈ U_k S_k V_k^T",
            ],
            algorithm_optional=(
                "Fit a pooled gravity baseline, compute log-flow residual matrix R, apply "
                "rank-k PCA/SVD, and compare latent-factor recovery against direct "
                "residual inspection."
            ),
            parameter_interpretation=[
                "R_ij is the log-flow residual after pooled gravity adjustment.",
                "U_k and V_k encode retained origin and destination residual factors.",
                "S_k gives the strength of each retained heterogeneity axis.",
                "k is the number of retained latent axes.",
            ],
            identifiability_notes=(
                "Latent factors are identifiable only up to rotation and sign; recovery should use "
                "correlation or subspace metrics rather than exact component names."
            ),
            what_would_falsify_it=(
                "If low-rank components do not recover known latent factors or improve residual "
                "reconstruction over direct pooled-gravity residuals, the bounded "
                "hypothesis is not supported."
            ),
        ),
        variables_and_notation=[
            ScientificSubstrateVariable(
                symbol="F_ij", definition="observed or synthetic OD flow", role="response"
            ),
            ScientificSubstrateVariable(
                symbol="\\hat F^{(0)}_{ij}",
                definition="pooled gravity baseline prediction",
                role="baseline prediction",
            ),
            ScientificSubstrateVariable(
                symbol="R_ij",
                definition="log-flow residual after pooled gravity baseline",
                role="residual matrix entry",
            ),
            ScientificSubstrateVariable(
                symbol="c", definition="small positive offset", role="log offset"
            ),
            ScientificSubstrateVariable(
                symbol="U_k, S_k, V_k",
                definition="rank-k residual factors",
                role="low-rank representation",
            ),
            ScientificSubstrateVariable(
                symbol="k", definition="number of retained heterogeneity axes", role="rank"
            ),
        ],
        assumptions=[
            ScientificSubstrateAssumption(
                assumption_id="latent-factor-dgp",
                statement="Synthetic OD flows include known latent origin and destination factors.",
                rationale="The experiment needs ground truth for factor recovery metrics.",
                violation_consequence=(
                    "Recovery correlation cannot be interpreted without known latent factors."
                ),
            ),
            ScientificSubstrateAssumption(
                assumption_id="pooled-baseline-fit",
                statement="A pooled gravity baseline is fitted before residual factorization.",
                rationale=(
                    "The low-rank model is meant to represent residual heterogeneity "
                    "beyond distance decay."
                ),
                violation_consequence=(
                    "Components may reflect ordinary distance decay rather than "
                    "residual heterogeneity."
                ),
            ),
        ],
        mechanism=(
            "Distance decay is removed by a pooled gravity baseline; remaining log-flow residuals "
            "are approximated by a low-rank matrix whose leading axes represent latent "
            "spatial heterogeneity."
        ),
        dgp_or_dataset=(
            "Generate synthetic OD flows with known latent origin/destination factors in addition "
            "to distance decay, then recover those factors from the residual matrix."
        ),
        baseline="pooled gravity residuals without low-rank factorization",
        measurable_hypothesis=(
            "A low-rank residual representation recovers latent spatial heterogeneity axes in "
            "synthetic OD-flow data better than inspecting pooled gravity residuals directly."
        ),
        experiment_design=ScientificSubstrateExperimentDesign(
            target_claim=(
                "Rank-k PCA/SVD on pooled-gravity residuals reports higher latent-factor recovery "
                "correlation and residual reconstruction quality than direct residual inspection."
            ),
            data_regime="SyntheticOnly",
            dgp=(
                "Generate synthetic OD flows with known latent origin/destination factors in "
                "addition to distance decay."
            ),
            train_test_split_optional=(
                "Fit baseline and factors on training OD pairs and evaluate held-out "
                "reconstruction."
            ),
            baseline="pooled gravity residuals without low-rank factorization",
            method="rank-k PCA/SVD residual representation",
            metrics=[
                "held-out reconstruction MAE",
                "residual RMSE",
                "latent-factor recovery correlation",
                "explained residual variance",
            ],
            seed_plan="Fixed deterministic seeds recorded in the experiment artifact.",
            ablation_or_stress_test=(
                "Vary latent rank, noise level, and factor strength. The PCA advantage should "
                "weaken when the latent factor strength approaches zero."
            ),
            success_criterion=(
                "The low-rank residual method improves held-out reconstruction MAE/residual RMSE "
                "and reports positive latent-factor recovery correlation under the configured DGP."
            ),
            failure_criterion=(
                "The method fails to improve reconstruction or recover latent factors, or the "
                "explained residual variance is not concentrated in retained axes."
            ),
        ),
        result_schema=ScientificSubstrateResultSchema(
            baseline_metric_names=[
                "baseline_residual_RMSE",
                "baseline_latent_recovery_correlation",
            ],
            method_metric_names=[
                "method_reconstruction_MAE",
                "method_residual_RMSE",
                "method_latent_recovery_correlation",
                "method_explained_residual_variance",
            ],
            comparison_direction=(
                "lower reconstruction errors and higher recovery correlation/explained "
                "variance are better"
            ),
            required_table_columns=[
                "seed",
                "n_regions",
                "latent_rank",
                "noise_level",
                "factor_strength",
                "baseline_residual_RMSE",
                "method_residual_RMSE",
                "method_reconstruction_MAE",
                "method_latent_recovery_correlation",
                "method_explained_residual_variance",
            ],
            claim_supported_if=(
                "method_residual_RMSE is lower than the pooled residual baseline and "
                "method_latent_recovery_correlation is positive for the configured synthetic DGP."
            ),
            claim_not_supported_if=(
                "Residual reconstruction does not improve, latent recovery correlation is absent "
                "or non-positive, metrics are missing, or the run is failed/inconclusive."
            ),
        ),
        limitations=[
            "Component signs and rotations are not directly interpretable without alignment.",
            "Synthetic factor recovery does not establish real-world spatial heterogeneity.",
            "A low-rank representation can compress residuals without proving causal mechanisms.",
        ],
        failure_modes=[
            "Latent factor strength is too weak relative to noise.",
            (
                "Pooled gravity baseline is misspecified and leaves structured "
                "distance-decay artifacts."
            ),
            "Chosen rank k overfits residual noise.",
        ],
        evidence_boundary=(
            "This PCA/low-rank branch is a serious alternative substrate, but it is not evidence "
            "until a completed scoped experiment artifact passes intake."
        ),
        selected_for_next_experiment=selected,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _fallback_substrate(
    run_id: str,
    axis: str,
    domain: str,
    source_node_id: str | None,
    selected: bool,
) -> ScientificSubstrate:
    safe_axis = axis or "selected deterministic branch"
    return ScientificSubstrate(
        substrate_id=f"scientific-substrate-{_slug(safe_axis)}",
        run_id=run_id,
        source_idea_node_id_optional=source_node_id,
        source_mutation_axis_optional=safe_axis,
        title=f"Bounded Scientific Substrate for {safe_axis}",
        domain=domain,
        domain_problem=f"The selected branch needs a concrete bounded model for {domain}.",
        central_tension="The branch should become testable without broad validation language.",
        concrete_model_object=ScientificSubstrateModelObject(
            model_type="bounded_deterministic_substrate",
            equations=["Y = f(X; theta) + epsilon"],
            algorithm_optional=(
                "Specify variables, generate synthetic inputs, compare to a baseline."
            ),
            parameter_interpretation=["theta is the bounded model parameter vector."],
            identifiability_notes="Identifiability is not established by this fallback substrate.",
            what_would_falsify_it="The method fails to improve the declared metric over baseline.",
        ),
        variables_and_notation=[
            ScientificSubstrateVariable(symbol="Y", definition="target outcome", role="response"),
            ScientificSubstrateVariable(symbol="X", definition="input features", role="covariate"),
            ScientificSubstrateVariable(
                symbol="theta",
                definition="model parameters",
                role="parameter",
            ),
        ],
        assumptions=[
            ScientificSubstrateAssumption(
                assumption_id="synthetic-boundedness",
                statement="The fallback uses synthetic bounded inputs only.",
                rationale="No external data or broad empirical claim is allowed.",
                violation_consequence="The substrate cannot support a completed experiment claim.",
            )
        ],
        mechanism="A deterministic model is compared against a simple baseline on synthetic data.",
        dgp_or_dataset="Synthetic fixture generated with fixed seeds.",
        baseline="simple deterministic baseline",
        measurable_hypothesis="The bounded method improves the configured metric over baseline.",
        experiment_design=ScientificSubstrateExperimentDesign(
            target_claim=(
                "The bounded method reports improved metrics for the configured synthetic run."
            ),
            data_regime="SyntheticOnly",
            dgp="Synthetic fixture generated with fixed seeds.",
            train_test_split_optional="Deterministic train/test split if applicable.",
            baseline="simple deterministic baseline",
            method="bounded deterministic method",
            metrics=["MAE", "RMSE"],
            seed_plan="Fixed deterministic seeds.",
            ablation_or_stress_test="Vary noise level and compare metric stability.",
            success_criterion="Method metric improves over baseline in the configured run.",
            failure_criterion="Method metric does not improve or run is failed/inconclusive.",
        ),
        result_schema=ScientificSubstrateResultSchema(
            baseline_metric_names=["baseline_MAE", "baseline_RMSE"],
            method_metric_names=["method_MAE", "method_RMSE"],
            comparison_direction="lower_is_better",
            required_table_columns=[
                "seed",
                "baseline_MAE",
                "baseline_RMSE",
                "method_MAE",
                "method_RMSE",
            ],
            claim_supported_if="method_MAE < baseline_MAE and method_RMSE < baseline_RMSE.",
            claim_not_supported_if="Metrics are missing or method does not improve over baseline.",
        ),
        limitations=[
            "Fallback substrate is generic and should be replaced by a domain-specific model."
        ],
        failure_modes=["The fallback remains too generic to guide scientific exploration."],
        evidence_boundary="This fallback is planning context only and not evidence.",
        selected_for_next_experiment=selected,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _resolve_domain(
    idea_space: IdeaSpaceInspectionReport,
    idea_tree: object | None,
) -> str:
    if idea_tree is not None:
        root = next(
            (
                node
                for node in getattr(idea_tree, "nodes", [])
                if node.node_id == getattr(idea_tree, "root_node_id", "")
            ),
            None,
        )
        if root is not None and root.title:
            return root.title
    for vector in idea_space.feature_vectors:
        if vector.stage_origin == "domain_opportunity":
            return vector.title
    return "unknown domain"


def _source_node_id(idea_tree: object | None) -> str | None:
    if idea_tree is None:
        return None
    final_node_id = getattr(idea_tree, "final_node_id_optional", None)
    if final_node_id:
        return final_node_id
    selected = [
        node.node_id
        for node in getattr(idea_tree, "nodes", [])
        if getattr(node, "selected_for_stage_c", False)
    ]
    return sorted(selected)[0] if selected else None


def _prioritize(axis: str, axes: list[str], *, after: str | None = None) -> list[str]:
    axes = [item for item in axes if item != axis]
    if after is None or after not in axes:
        return [axis, *axes]
    index = axes.index(after)
    return [*axes[: index + 1], axis, *axes[index + 1 :]]


def _axis_is_distance(axis: str) -> bool:
    lower = axis.lower()
    return "distance-decay" in lower or ("distance" in lower and "gravity" in lower)


def _axis_is_pca(axis: str) -> bool:
    lower = axis.lower()
    return "pca" in lower or "low-rank" in lower or "od-flow representation" in lower


def _substrate_artifact_id(
    build_number: int,
    index: int,
    substrate: ScientificSubstrate,
) -> str:
    return f"scientific-substrate-{build_number:04d}-{index:02d}-{_slug(substrate.title)}"


def _next_build_number(reports: Path) -> int:
    numbers = [
        int(match.group(1))
        for path in reports.iterdir()
        if path.is_file() and (match := _BUILD_RE.fullmatch(path.name))
    ]
    return max(numbers, default=0) + 1


def _slug(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.lower())
    return "-".join(words[:10]) or "substrate"


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


__all__ = [
    "ScientificSubstrateBuildResult",
    "ScientificSubstrateError",
    "build_scientific_substrate",
    "inspect_scientific_substrate",
    "latest_scientific_substrate_build",
    "latest_selected_scientific_substrate",
    "render_scientific_substrate_build_markdown",
]
