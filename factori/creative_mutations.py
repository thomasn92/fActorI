"""Tournament-driven creative mutation planning and application."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from factori.artifacts import ArtifactStore
from factori.idea_space import IdeaSpaceError, inspect_idea_space
from factori.idea_tree import IdeaTreeError, inspect_idea_tree
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.rerun_policy import validate_ledger_tip
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    CreativeMutationCandidate,
    CreativeMutationInspectionReport,
    CreativeMutationOperator,
    CreativeMutationPlan,
    CreativeMutationReport,
    ScientificSubstrate,
    ScientificSubstrateAssumption,
    ScientificSubstrateBuildReport,
    ScientificSubstrateExperimentDesign,
    ScientificSubstrateModelObject,
    ScientificSubstrateResultSchema,
    ScientificSubstrateVariable,
    SubstrateTournamentResult,
)
from factori.scientific_substrate import (
    latest_scientific_substrate_build,
    render_scientific_substrate_build_markdown,
)
from factori.substrate_tournament import latest_substrate_tournament_result

_PLAN_RE = re.compile(r"^creative-mutation-plan-(\d{4})\.json$")
_REPORT_RE = re.compile(r"^creative-mutation-report-(\d{4})\.json$")
_SUBSTRATE_BUILD_RE = re.compile(r"^scientific-substrate-build-(\d{4})\.json$")


class CreativeMutationError(RuntimeError):
    """Raised when creative mutations cannot be planned or applied."""


@dataclass(frozen=True)
class CreativeMutationApplyResult:
    """Persisted result of applying creative mutations."""

    run_id: str
    plan: CreativeMutationPlan
    report: CreativeMutationReport
    new_substrates: list[ScientificSubstrate]
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    substrate_artifacts: list[ArtifactRef]


def plan_creative_mutations(
    *,
    run_id: str,
    root: str | Path = ".",
    max_mutations: int = 5,
    write_report: bool = True,
) -> CreativeMutationPlan:
    """Plan deterministic tournament-driven scientific mutations."""
    if max_mutations < 1:
        raise CreativeMutationError("max_mutations must be at least 1.")
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    if not reports.is_dir():
        raise CreativeMutationError(f"Reports directory not found for run_id={run_id}.")

    warnings: list[str] = []
    idea_tree = None
    try:
        idea_tree = inspect_idea_tree(run_id=run_id, root=root_path)
    except IdeaTreeError as exc:
        warnings.append(f"IdeaTree inspection unavailable: {exc}")
    idea_space_path = _latest_report_path(reports, "idea-space-report-*.json")
    try:
        idea_space = inspect_idea_space(run_id=run_id, root=root_path)
    except IdeaSpaceError as exc:
        idea_space = None
        warnings.append(f"Idea-space inspection unavailable: {exc}")

    build, substrates, substrate_warnings = latest_scientific_substrate_build(root_path, run_id)
    warnings.extend(substrate_warnings)
    tournament = latest_substrate_tournament_result(root_path, run_id)
    if tournament is None:
        raise CreativeMutationError("Run a substrate tournament before planning mutations.")
    if not substrates:
        raise CreativeMutationError("Build scientific substrates before planning mutations.")

    substrate_build_path = _latest_scientific_substrate_build_path(root_path, run_id)
    tournament_path = _latest_tournament_path(root_path, run_id)
    domain = _domain_from_sources(substrates, idea_tree)
    candidates = _candidate_mutations(
        run_id=run_id,
        domain=domain,
        substrates=substrates,
        tournament=tournament,
        idea_tree_final_node=(
            idea_tree.final_node_id_optional if idea_tree is not None else None
        ),
        idea_space_recommendations=(
            idea_space.recommended_mutation_axes if idea_space is not None else []
        ),
    )[:max_mutations]
    plan_number = _next_number(reports, _PLAN_RE)
    plan = CreativeMutationPlan(
        run_id=run_id,
        plan_id=f"creative-mutation-plan-{plan_number:04d}",
        planning_status="completed_with_warnings" if warnings else "completed",
        source_idea_tree_present=idea_tree is not None,
        source_idea_space_report_path_optional=(
            _relative(idea_space_path, root_path) if idea_space_path else None
        ),
        source_scientific_substrate_build_path_optional=(
            _relative(substrate_build_path, root_path) if substrate_build_path else None
        ),
        source_substrate_tournament_result_path_optional=(
            _relative(tournament_path, root_path) if tournament_path else None
        ),
        max_mutations=max_mutations,
        mutation_count=len(candidates),
        selected_for_substrate_build_count=sum(
            candidate.selected_for_substrate_build for candidate in candidates
        ),
        operators_used=[candidate.operator for candidate in candidates],
        candidates=candidates,
        warnings=warnings,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )
    if write_report:
        _write_plan(root_path=root_path, plan=plan)
    return plan


def apply_creative_mutations(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    max_mutations: int = 3,
) -> CreativeMutationApplyResult:
    """Apply selected creative mutations as new IdeaTree nodes and substrates."""
    if max_mutations < 1:
        raise CreativeMutationError("max_mutations must be at least 1.")
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    if not reports.is_dir():
        raise CreativeMutationError(f"Reports directory not found for run_id={run_id}.")
    if validate_ledger_tip(run_id, root=root_path).blocking_findings:
        raise CreativeMutationError("Ledger validation blocks creative mutation application.")

    plan_path = _latest_plan_path(root_path, run_id)
    if plan_path is None:
        plan = plan_creative_mutations(
            run_id=run_id,
            root=root_path,
            max_mutations=max(5, max_mutations),
            write_report=True,
        )
        plan_path = _latest_plan_path(root_path, run_id)
    else:
        plan = _load_plan(plan_path)

    previously_applied = {
        candidate.mutation_id
        for previous_report, _ in latest_creative_mutation_reports(
            run_id=run_id,
            root=root_path,
        )
        for candidate in previous_report.candidates
    }
    selected = [
        candidate
        for candidate in plan.candidates
        if candidate.selected_for_substrate_build
        and candidate.mutation_id not in previously_applied
    ][:max_mutations]
    if not selected:
        raise CreativeMutationError(
            "No new selected creative mutations are available to apply; all selected "
            "mutation identities were already applied."
        )

    report_number = _next_number(reports, _REPORT_RE)
    report_id = f"creative-mutation-report-{report_number:04d}"
    build_number = _next_number(reports, _SUBSTRATE_BUILD_RE)
    build_id = f"scientific-substrate-build-{build_number:04d}"

    previous_build, previous_substrates, warnings = latest_scientific_substrate_build(
        root_path, run_id
    )
    old_paths = previous_build.substrate_paths if previous_build else []
    new_substrates = [
        _substrate_from_mutation(run_id=run_id, candidate=candidate)
        for candidate in selected
    ]
    new_paths = [
        f"runs/{run_id}/reports/{_substrate_artifact_id(build_number, index, substrate)}.json"
        for index, substrate in enumerate(new_substrates, start=1)
    ]
    all_substrates = [*previous_substrates, *new_substrates]
    build_report = ScientificSubstrateBuildReport(
        run_id=run_id,
        build_id=build_id,
        build_status="completed_with_warnings" if warnings else "completed",
        source_idea_space_report_path_optional=plan.source_idea_space_report_path_optional,
        source_idea_tree_report_path_optional=None,
        requested_mutation_axis_optional="tournament-driven creative mutations",
        max_substrates=len(all_substrates) or 1,
        recommended_mutation_axes=[
            candidate.why_scientifically_distinct for candidate in plan.candidates
        ],
        built_mutation_axes=[candidate.title for candidate in selected],
        substrate_paths=[*old_paths, *new_paths],
        substrate_count=len(all_substrates),
        selected_substrate_id_optional=(
            new_substrates[0].substrate_id if new_substrates else None
        ),
        selected_substrate_title_optional=(
            new_substrates[0].title if new_substrates else None
        ),
        pca_low_rank_substrate_id_optional=next(
            (
                substrate.substrate_id
                for substrate in all_substrates
                if "low_rank" in substrate.concrete_model_object.model_type
                or "low-rank" in (substrate.source_mutation_axis_optional or "").lower()
            ),
            None,
        ),
        warnings=warnings,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )
    report = CreativeMutationReport(
        run_id=run_id,
        mutation_report_id=report_id,
        apply_status="completed_with_warnings" if warnings else "completed",
        source_plan_path=_relative(plan_path, root_path) if plan_path else "",
        source_tournament_result_id_optional=_tournament_id_from_plan(plan),
        applied_mutation_count=len(selected),
        selected_for_substrate_build_count=len(selected),
        new_idea_tree_node_count=len(selected),
        new_scientific_substrate_count=len(new_substrates),
        idea_tree_node_ids=[candidate.mutation_id for candidate in selected],
        scientific_substrate_paths=new_paths,
        scientific_substrate_build_report_path_optional=(
            f"runs/{run_id}/reports/{build_id}.json"
        ),
        candidates=selected,
        warnings=warnings,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )
    metadata = {
        "stage": "creative_mutation_apply",
        "artifact_role": "creative_mutation_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }
    specs: list[ArtifactWriteSpec] = []
    for index, substrate in enumerate(new_substrates, start=1):
        specs.append(
            ArtifactWriteSpec(
                _substrate_artifact_id(build_number, index, substrate),
                ArtifactType.REPORT,
                substrate,
                "json",
                metadata,
            )
        )
    specs.extend(
        [
            ArtifactWriteSpec(build_id, ArtifactType.REPORT, build_report, "json", metadata),
            ArtifactWriteSpec(
                f"{build_id}-markdown",
                ArtifactType.REPORT,
                render_scientific_substrate_build_markdown(build_report, all_substrates),
                "markdown",
                metadata,
                filename_stem=build_id,
            ),
            ArtifactWriteSpec(report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report_id}-markdown",
                ArtifactType.REPORT,
                render_creative_mutation_report_markdown(report),
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
        artifact_specs=specs,
        action_type=ControllerActionType.CREATIVE_MUTATIONS_APPLIED,
        commit_payload={
            "run_id": run_id,
            "mutation_report_id": report_id,
            "applied_mutation_count": len(selected),
            "new_scientific_substrate_count": len(new_substrates),
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return CreativeMutationApplyResult(
        run_id=run_id,
        plan=plan,
        report=report,
        new_substrates=new_substrates,
        persistence=persistence,
        report_artifact=by_id[report_id],
        substrate_artifacts=[
            by_id[_substrate_artifact_id(build_number, index, substrate)]
            for index, substrate in enumerate(new_substrates, start=1)
        ],
    )


def inspect_creative_mutations(
    *,
    run_id: str,
    root: str | Path = ".",
) -> CreativeMutationInspectionReport:
    """Inspect the latest creative mutation plan and application without mutation."""
    root_path = Path(root)
    warnings: list[str] = []
    plan_path = _latest_plan_path(root_path, run_id)
    report_path = _latest_apply_report_path(root_path, run_id)
    plan = _safe_load_plan(plan_path, warnings) if plan_path else None
    report = _safe_load_report(report_path, warnings) if report_path else None
    candidates = plan.candidates if plan else []
    return CreativeMutationInspectionReport(
        run_id=run_id,
        creative_mutation_plan_present=plan is not None,
        latest_plan_id_optional=plan.plan_id if plan else None,
        latest_apply_report_id_optional=report.mutation_report_id if report else None,
        mutation_count=len(candidates),
        selected_for_substrate_build_count=sum(
            candidate.selected_for_substrate_build for candidate in candidates
        ),
        applied_mutation_count=report.applied_mutation_count if report else 0,
        new_idea_tree_nodes_added=bool(report and report.new_idea_tree_node_count > 0),
        new_scientific_substrates_created=bool(
            report and report.new_scientific_substrate_count > 0
        ),
        includes_hierarchical_alpha_mutation=any(
            "Hierarchical Region-Cluster Distance Decay" in candidate.title
            for candidate in candidates
        ),
        includes_gravity_low_rank_hybrid=any(
            "Gravity Plus Low-Rank Residual Correction" in candidate.title
            for candidate in candidates
        ),
        includes_boundary_perturbation_robustness=any(
            "Boundary-Perturbation Robustness" in candidate.title for candidate in candidates
        ),
        includes_kernelized_spatial_interaction=any(
            "Kernelized Spatial Interaction" in candidate.title for candidate in candidates
        ),
        candidates=candidates,
        latest_plan_optional=plan,
        latest_report_optional=report,
        warnings=warnings,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def latest_creative_mutation_reports(
    *,
    run_id: str,
    root: str | Path = ".",
) -> list[tuple[CreativeMutationReport, Path]]:
    """Load all applied creative mutation reports for IdeaTree reconstruction."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    loaded: list[tuple[CreativeMutationReport, Path]] = []
    for _, path in sorted(
        (int(match.group(1)), path)
        for path in reports.glob("creative-mutation-report-*.json")
        if (match := _REPORT_RE.fullmatch(path.name))
    ):
        try:
            loaded.append(
                (
                    CreativeMutationReport.model_validate_json(
                        path.read_text(encoding="utf-8")
                    ),
                    path,
                )
            )
        except (OSError, ValidationError):
            continue
    return loaded


def render_creative_mutation_plan_markdown(plan: CreativeMutationPlan) -> str:
    """Render a human-readable mutation plan."""
    lines = [
        "# Creative Mutation Plan",
        "",
        f"- Run ID: `{plan.run_id}`",
        f"- Plan ID: `{plan.plan_id}`",
        f"- Status: `{plan.planning_status}`",
        f"- Mutations: `{plan.mutation_count}`",
        f"- Selected for substrate build: `{plan.selected_for_substrate_build_count}`",
        "- publication_ready: false",
        "",
        "## Candidates",
        "",
    ]
    for candidate in plan.candidates:
        lines.extend(_candidate_markdown(candidate))
    lines.extend(_boundary_markdown())
    return "\n".join(lines)


def render_creative_mutation_report_markdown(report: CreativeMutationReport) -> str:
    """Render a human-readable applied mutation report."""
    lines = [
        "# Creative Mutation Application Report",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Report ID: `{report.mutation_report_id}`",
        f"- Status: `{report.apply_status}`",
        f"- Applied mutations: `{report.applied_mutation_count}`",
        f"- New IdeaTree nodes: `{report.new_idea_tree_node_count}`",
        f"- New ScientificSubstrates: `{report.new_scientific_substrate_count}`",
        "- publication_ready: false",
        "",
        "## Applied Candidates",
        "",
    ]
    for candidate in report.candidates:
        lines.extend(_candidate_markdown(candidate))
    lines.extend(_boundary_markdown())
    return "\n".join(lines)


def _candidate_mutations(
    *,
    run_id: str,
    domain: str,
    substrates: list[ScientificSubstrate],
    tournament: SubstrateTournamentResult,
    idea_tree_final_node: str | None,
    idea_space_recommendations: list[str],
) -> list[CreativeMutationCandidate]:
    by_id = {substrate.substrate_id: substrate for substrate in substrates}
    winner_id = tournament.winner_substrate_id_optional or ""
    winner = by_id.get(winner_id) or substrates[0]
    loser_entries = [
        entry for entry in tournament.entries if entry.substrate_id != winner.substrate_id
    ]
    loser = by_id.get(loser_entries[0].substrate_id) if loser_entries else None
    winner_node_ids = _source_nodes(winner, idea_tree_final_node)
    loser_node_ids = _source_nodes(loser, idea_tree_final_node) if loser else []
    tournament_id = tournament.tournament_id
    idea_space_axis_context = (
        "; ".join(idea_space_recommendations[:3])
        if idea_space_recommendations
        else "no persisted idea-space recommendation context"
    )
    return [
        CreativeMutationCandidate(
            mutation_id="creative-mutation-hierarchical-alpha",
            source_substrate_ids=[winner.substrate_id],
            source_idea_node_ids=winner_node_ids,
            operator=CreativeMutationOperator.WINNER_REFINEMENT,
            title="Hierarchical Region-Cluster Distance Decay in Synthetic Spatial Interaction",
            domain=domain,
            research_question=(
                "Can a cluster-level alpha model recover most of the heterogeneous-alpha "
                "benefit with fewer parameters?"
            ),
            model_object=(
                "Region-cluster distance-decay model with shared alpha values by origin "
                "cluster g(i)."
            ),
            equations=["F_ij = A_i B_j d_ij^{-alpha_{g(i)}} exp(epsilon_ij)"],
            baseline=(
                "pooled-alpha gravity model and full origin-specific alpha model"
            ),
            experiment_design=(
                "Generate origin clusters with shared alpha values. Compare pooled alpha, "
                "cluster alpha, and full alpha on held-out OD-flow reconstruction."
            ),
            expected_result_pattern=(
                "Cluster alpha should approach the full heterogeneous-alpha benefit with fewer "
                "parameters when the DGP has cluster-level distance-decay structure."
            ),
            why_scientifically_distinct=(
                "It changes the parameterization from individual origin effects to a hierarchical "
                "cluster mechanism, testing parsimony rather than only raw fit."
            ),
            risk_or_failure_mode=(
                "If cluster assignments are weak or alpha_i varies continuously, the cluster "
                "model can underfit both pooled and full-alpha alternatives."
            ),
            parent_tournament_result_id_optional=tournament_id,
            selected_for_substrate_build=True,
            publication_ready=False,
        ),
        CreativeMutationCandidate(
            mutation_id="creative-mutation-gravity-low-rank-hybrid",
            source_substrate_ids=[
                winner.substrate_id,
                *([loser.substrate_id] if loser else []),
            ],
            source_idea_node_ids=_dedupe([*winner_node_ids, *loser_node_ids]),
            operator=CreativeMutationOperator.WINNER_LOSER_HYBRID,
            title="Gravity Plus Low-Rank Residual Correction for Synthetic OD Heterogeneity",
            domain=domain,
            research_question=(
                "Does adding a low-rank residual correction improve the distance-decay winner "
                "when residual latent structure is present?"
            ),
            model_object=(
                "Heterogeneous-alpha gravity model augmented with a low-rank residual term."
            ),
            equations=[
                (
                    "log F_ij = log A_i + log B_j - alpha_i log d_ij "
                    "+ (U_k S_k V_k^T)_ij + epsilon_ij"
                )
            ],
            baseline="distance-decay winner without residual correction",
            experiment_design=(
                "Generate OD flows with both heterogeneous alpha_i and latent low-rank "
                "residual factors, then compare the winner against the residual-corrected "
                "hybrid."
            ),
            expected_result_pattern=(
                "The hybrid should improve when latent residual structure is present and shrink "
                "toward the distance-decay winner when residual factor strength is zero."
            ),
            why_scientifically_distinct=(
                "It combines mechanism-level distance decay with representation-level residual "
                "axes from the PCA/low-rank branch."
            ),
            risk_or_failure_mode=(
                "The low-rank correction may overfit synthetic residual noise or obscure the "
                "interpretability of alpha_i."
            ),
            parent_tournament_result_id_optional=tournament_id,
            selected_for_substrate_build=True,
            publication_ready=False,
        ),
        CreativeMutationCandidate(
            mutation_id="creative-mutation-boundary-perturbation-robustness",
            source_substrate_ids=[winner.substrate_id],
            source_idea_node_ids=winner_node_ids,
            operator=CreativeMutationOperator.ROBUSTNESS_STRESS_TEST,
            title="Boundary-Perturbation Robustness of Region-Specific Distance Decay",
            domain=domain,
            research_question=(
                "Does the heterogeneous-alpha advantage persist under region aggregation or "
                "boundary perturbation?"
            ),
            model_object=(
                "Region-specific distance-decay model evaluated under perturbed spatial "
                "aggregation boundaries."
            ),
            equations=[
                "F_ij = A_i B_j d_ij^{-alpha_i} exp(epsilon_ij)",
                "F_GH = sum_{i in G, j in H} F_ij",
            ],
            baseline="pooled-alpha model under the same perturbed aggregation",
            experiment_design=(
                "Generate fine regions, aggregate them into perturbed coarser regions, and "
                "compare pooled versus heterogeneous alpha performance."
            ),
            expected_result_pattern=(
                "The heterogeneous-alpha advantage should persist under moderate aggregation but "
                "weaken when boundary perturbations erase origin-specific structure."
            ),
            why_scientifically_distinct=(
                "It turns a known limitation of spatial units into an explicit stress-test axis."
            ),
            risk_or_failure_mode=(
                "Aggregation can collapse heterogeneity enough that the flexible model no longer "
                "has a measurable advantage."
            ),
            parent_tournament_result_id_optional=tournament_id,
            selected_for_substrate_build=True,
            publication_ready=False,
        ),
        CreativeMutationCandidate(
            mutation_id="creative-mutation-kernelized-spatial-interaction",
            source_substrate_ids=[winner.substrate_id],
            source_idea_node_ids=winner_node_ids,
            operator=CreativeMutationOperator.MISSING_AXIS_INJECTION,
            title="Kernelized Spatial Interaction Under Synthetic Regional Heterogeneity",
            domain=domain,
            research_question=(
                "Can a kernel spatial interaction model capture nonmonotone spatial "
                "heterogeneity not explained by distance decay?"
            ),
            model_object=(
                "Kernelized spatial interaction model over region coordinates and covariates."
            ),
            equations=["F_ij = A_i B_j K_theta(x_i, x_j) exp(epsilon_ij)"],
            baseline="distance-decay gravity model",
            experiment_design=(
                "Generate synthetic regions with nonmonotone interaction pockets and compare a "
                "kernel interaction model against the distance-decay gravity baseline."
            ),
            expected_result_pattern=(
                "Kernel interaction should help when synthetic flows include nonmonotone spatial "
                "structure and should not help on pure monotone distance-decay DGPs."
            ),
            why_scientifically_distinct=(
                "It injects the underexplored kernel/model-object axis from idea-space "
                f"diagnostics. Current axis context: {idea_space_axis_context}."
            ),
            risk_or_failure_mode=(
                "A flexible kernel may overfit, lose interpretability, or fail to extrapolate "
                "outside the synthetic coordinate regime."
            ),
            parent_tournament_result_id_optional=tournament_id,
            selected_for_substrate_build=False,
            publication_ready=False,
        ),
    ]


def _substrate_from_mutation(
    *,
    run_id: str,
    candidate: CreativeMutationCandidate,
) -> ScientificSubstrate:
    variables = _variables_for_mutation(candidate)
    return ScientificSubstrate(
        substrate_id=f"scientific-substrate-{candidate.mutation_id.removeprefix('creative-mutation-')}",
        run_id=run_id,
        source_idea_node_id_optional=(
            candidate.source_idea_node_ids[0] if candidate.source_idea_node_ids else None
        ),
        source_mutation_axis_optional=candidate.operator.value,
        title=candidate.title,
        domain=candidate.domain,
        domain_problem=(
            "The previous substrate tournament identified a bounded winner and a serious "
            "alternative; this mutation turns that feedback into a new testable branch."
        ),
        central_tension=candidate.why_scientifically_distinct,
        concrete_model_object=ScientificSubstrateModelObject(
            model_type=_model_type_for_mutation(candidate),
            equations=candidate.equations,
            algorithm_optional=candidate.experiment_design,
            parameter_interpretation=[
                f"{variable.symbol} is {variable.definition}." for variable in variables
            ],
            identifiability_notes=(
                "Identifiability remains a synthetic-scope design question until the mutation is "
                "routed to an accepted experiment artifact."
            ),
            what_would_falsify_it=candidate.risk_or_failure_mode,
        ),
        variables_and_notation=variables,
        assumptions=[
            ScientificSubstrateAssumption(
                assumption_id="synthetic-scope",
                statement="The mutation is evaluated only in a synthetic local DGP.",
                rationale="M87 creates creative branches, not real-world validation.",
                violation_consequence=(
                    "If external data or broad claims are required, the branch must remain "
                    "deferred."
                ),
            ),
            ScientificSubstrateAssumption(
                assumption_id="tournament-feedback",
                statement=(
                    "The mutation is motivated by the latest bounded tournament result and "
                    "idea-space missing axes."
                ),
                rationale="Creative search should preserve useful prior evidence boundaries.",
                violation_consequence=(
                    "The mutation may collapse back into an uninformative duplicate."
                ),
            ),
        ],
        mechanism=candidate.model_object,
        dgp_or_dataset=candidate.experiment_design,
        baseline=candidate.baseline,
        measurable_hypothesis=candidate.research_question,
        experiment_design=ScientificSubstrateExperimentDesign(
            target_claim=candidate.expected_result_pattern,
            data_regime="SyntheticOnly",
            dgp=candidate.experiment_design,
            train_test_split_optional="Deterministic synthetic train/test split if routed.",
            baseline=candidate.baseline,
            method=candidate.model_object,
            metrics=["MAE", "RMSE", "ablation_sensitivity"],
            seed_plan="Fixed deterministic seeds recorded by the future experiment artifact.",
            ablation_or_stress_test=candidate.experiment_design,
            success_criterion=candidate.expected_result_pattern,
            failure_criterion=candidate.risk_or_failure_mode,
        ),
        result_schema=ScientificSubstrateResultSchema(
            baseline_metric_names=["baseline_MAE", "baseline_RMSE"],
            method_metric_names=["method_MAE", "method_RMSE", "ablation_sensitivity"],
            comparison_direction="lower errors and stronger bounded ablation pattern are better",
            required_table_columns=[
                "seed",
                "setting",
                "baseline_MAE",
                "baseline_RMSE",
                "method_MAE",
                "method_RMSE",
                "ablation_sensitivity",
            ],
            claim_supported_if=candidate.expected_result_pattern,
            claim_not_supported_if=candidate.risk_or_failure_mode,
        ),
        limitations=[
            "This mutation is a scientific planning object until routed to an experiment.",
            "Synthetic support cannot establish real-world empirical validation.",
            "Tournament feedback selects research focus only, not publication readiness.",
        ],
        failure_modes=[candidate.risk_or_failure_mode],
        evidence_boundary=(
            "Creative mutation substrates are context for future bounded experiments. They do "
            "not create verification evidence, scientific validation, or publication readiness."
        ),
        selected_for_next_experiment=True,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def scientific_substrate_from_creative_mutation(
    *,
    run_id: str,
    candidate: CreativeMutationCandidate,
) -> ScientificSubstrate:
    """Convert a validated mutation candidate into a context-only substrate."""
    return _substrate_from_mutation(run_id=run_id, candidate=candidate)


def _variables_for_mutation(
    candidate: CreativeMutationCandidate,
) -> list[ScientificSubstrateVariable]:
    title = candidate.title.lower()
    common = [
        ScientificSubstrateVariable(symbol="i,j", definition="regions", role="indices"),
        ScientificSubstrateVariable(
            symbol="F_ij", definition="origin-destination flow", role="response"
        ),
        ScientificSubstrateVariable(symbol="A_i", definition="origin mass", role="covariate"),
        ScientificSubstrateVariable(
            symbol="B_j", definition="destination attractiveness", role="covariate"
        ),
    ]
    if "hierarchical" in title or "clustered" in title or "cluster-level" in title:
        return [
            *common,
            ScientificSubstrateVariable(
                symbol="g(i)", definition="origin-region cluster", role="cluster assignment"
            ),
            ScientificSubstrateVariable(
                symbol="alpha_{g(i)}",
                definition="cluster-level distance-decay parameter",
                role="heterogeneity parameter",
            ),
        ]
    if "low-rank" in title:
        return [
            *common,
            ScientificSubstrateVariable(
                symbol="U_k S_k V_k^T",
                definition="rank-k residual correction",
                role="representation term",
            ),
            ScientificSubstrateVariable(
                symbol="alpha_i",
                definition="origin-specific distance-decay parameter",
                role="heterogeneity parameter",
            ),
        ]
    if "boundary" in title:
        return [
            *common,
            ScientificSubstrateVariable(
                symbol="G,H", definition="aggregated regions", role="perturbed units"
            ),
            ScientificSubstrateVariable(
                symbol="F_GH", definition="aggregated origin-destination flow", role="response"
            ),
        ]
    return [
        *common,
        ScientificSubstrateVariable(
            symbol="K_theta(x_i, x_j)",
            definition="kernel interaction between region coordinates",
            role="interaction kernel",
        ),
        ScientificSubstrateVariable(
            symbol="theta", definition="kernel parameter vector", role="parameter"
        ),
    ]


def _model_type_for_mutation(candidate: CreativeMutationCandidate) -> str:
    mapping = {
        "creative-mutation-hierarchical-alpha": "hierarchical_region_cluster_distance_decay",
        "creative-mutation-gravity-low-rank-hybrid": "gravity_low_rank_residual_hybrid",
        "creative-mutation-boundary-perturbation-robustness": (
            "boundary_perturbation_distance_decay_robustness"
        ),
        "creative-mutation-kernelized-spatial-interaction": "kernelized_spatial_interaction",
    }
    return mapping.get(candidate.mutation_id, _slug(candidate.title).replace("-", "_"))


def _write_plan(*, root_path: Path, plan: CreativeMutationPlan) -> None:
    store = ArtifactStore(root_path)
    metadata = {
        "stage": "creative_mutation_plan",
        "artifact_role": "creative_mutation_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }
    store.write_json(
        run_id=plan.run_id,
        artifact_id=plan.plan_id,
        artifact_type=ArtifactType.REPORT,
        data=plan,
        metadata=metadata,
    )
    store.write_markdown(
        run_id=plan.run_id,
        artifact_id=f"{plan.plan_id}-markdown",
        artifact_type=ArtifactType.REPORT,
        markdown=render_creative_mutation_plan_markdown(plan),
        metadata=metadata,
        filename_stem=plan.plan_id,
    )


def _candidate_markdown(candidate: CreativeMutationCandidate) -> list[str]:
    return [
        f"### {candidate.title}",
        "",
        f"- Mutation ID: `{candidate.mutation_id}`",
        f"- Operator: `{candidate.operator.value}`",
        f"- Selected for substrate build: `{str(candidate.selected_for_substrate_build).lower()}`",
        f"- Question: {candidate.research_question}",
        f"- Equations: {'; '.join(candidate.equations)}",
        f"- Baseline: {candidate.baseline}",
        f"- Experiment: {candidate.experiment_design}",
        f"- Distinctness: {candidate.why_scientifically_distinct}",
        f"- Failure mode: {candidate.risk_or_failure_mode}",
        "",
    ]


def _boundary_markdown() -> list[str]:
    return [
        "## Evidence Boundary",
        "",
        "Creative mutation artifacts are context and planning only. They do not create "
        "scientific validation, verification evidence, or publication readiness.",
        "",
        "- publication_ready: false",
        "- creates_scientific_validation: false",
        "- implies_publication_readiness: false",
        "- is_verification_evidence: false",
        "",
    ]


def _latest_plan_path(root: Path, run_id: str) -> Path | None:
    reports = root / "runs" / run_id / "reports"
    paths = sorted(
        (int(match.group(1)), path)
        for path in reports.glob("creative-mutation-plan-*.json")
        if (match := _PLAN_RE.fullmatch(path.name))
    )
    return paths[-1][1] if paths else None


def _latest_apply_report_path(root: Path, run_id: str) -> Path | None:
    reports = root / "runs" / run_id / "reports"
    paths = sorted(
        (int(match.group(1)), path)
        for path in reports.glob("creative-mutation-report-*.json")
        if (match := _REPORT_RE.fullmatch(path.name))
    )
    return paths[-1][1] if paths else None


def _load_plan(path: Path) -> CreativeMutationPlan:
    try:
        return CreativeMutationPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise CreativeMutationError(f"Creative mutation plan is corrupt: {path}") from exc


def _safe_load_plan(
    path: Path | None,
    warnings: list[str],
) -> CreativeMutationPlan | None:
    if path is None:
        return None
    try:
        return CreativeMutationPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        warnings.append("Latest creative mutation plan is corrupt.")
        return None


def _safe_load_report(
    path: Path | None,
    warnings: list[str],
) -> CreativeMutationReport | None:
    if path is None:
        return None
    try:
        return CreativeMutationReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        warnings.append("Latest creative mutation report is corrupt.")
        return None


def _latest_report_path(reports: Path, pattern: str) -> Path | None:
    paths = sorted(
        path
        for path in reports.glob(pattern)
        if path.is_file() and not path.name.endswith(".meta.json")
    )
    return paths[-1] if paths else None


def _latest_tournament_path(root: Path, run_id: str) -> Path | None:
    return _latest_report_path(
        root / "runs" / run_id / "reports",
        "substrate-tournament-result-*.json",
    )


def _latest_scientific_substrate_build_path(root: Path, run_id: str) -> Path | None:
    return _latest_report_path(
        root / "runs" / run_id / "reports",
        "scientific-substrate-build-*.json",
    )


def _next_number(reports: Path, regex: re.Pattern[str]) -> int:
    numbers = [
        int(match.group(1))
        for path in reports.iterdir()
        if path.is_file() and (match := regex.fullmatch(path.name))
    ]
    return max(numbers, default=0) + 1


def _domain_from_sources(
    substrates: list[ScientificSubstrate],
    idea_tree: object | None,
) -> str:
    if substrates:
        return substrates[0].domain
    if idea_tree is not None:
        nodes = getattr(idea_tree, "nodes", [])
        root_id = getattr(idea_tree, "root_node_id", "")
        root = next((node for node in nodes if node.node_id == root_id), None)
        if root is not None:
            return root.domain
    return "human geography"


def _source_nodes(
    substrate: ScientificSubstrate | None,
    fallback_node_id: str | None,
) -> list[str]:
    if substrate is not None and substrate.source_idea_node_id_optional:
        return [substrate.source_idea_node_id_optional]
    return [fallback_node_id] if fallback_node_id else []


def _tournament_id_from_plan(plan: CreativeMutationPlan) -> str | None:
    path = plan.source_substrate_tournament_result_path_optional or ""
    match = re.search(r"substrate-tournament-result-(\d{4})", path)
    if match:
        return f"substrate-tournament-{match.group(1)}"
    for candidate in plan.candidates:
        if candidate.parent_tournament_result_id_optional:
            return candidate.parent_tournament_result_id_optional
    return None


def _substrate_artifact_id(
    build_number: int,
    index: int,
    substrate: ScientificSubstrate,
) -> str:
    return f"scientific-substrate-{build_number:04d}-{index:02d}-{_slug(substrate.title)}"


def _slug(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.lower())
    return "-".join(words[:10]) or "mutation"


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    deduped: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            deduped.append(item)
    return deduped


__all__ = [
    "CreativeMutationApplyResult",
    "CreativeMutationError",
    "apply_creative_mutations",
    "inspect_creative_mutations",
    "latest_creative_mutation_reports",
    "plan_creative_mutations",
    "render_creative_mutation_plan_markdown",
    "render_creative_mutation_report_markdown",
    "scientific_substrate_from_creative_mutation",
]
