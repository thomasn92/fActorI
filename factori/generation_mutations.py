"""Generation-dependent mutation planning and substrate application."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from factori.artifacts import ArtifactStore
from factori.creative_mutations import (
    latest_creative_mutation_reports,
    scientific_substrate_from_creative_mutation,
)
from factori.hashing import sha256_json
from factori.idea_space import IdeaSpaceError, inspect_idea_space
from factori.idea_tree import IdeaTreeError, inspect_idea_tree
from factori.ledger import ResearchLedger
from factori.mutation_tournament import latest_mutation_tournament_result
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.rerun_policy import validate_ledger_tip
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    CreativeMutationCandidate,
    CreativeMutationOperator,
    GenerationMutationCandidate,
    GenerationMutationContext,
    GenerationMutationDiversityCheck,
    GenerationMutationInspectionReport,
    GenerationMutationOperator,
    GenerationMutationPlan,
    ScientificSubstrate,
    ScientificSubstrateBuildReport,
)
from factori.scientific_substrate import (
    latest_scientific_substrate_build,
    render_scientific_substrate_build_markdown,
)

_PLAN_RE = re.compile(r"^generation-mutation-plan-(\d{4})\.json$")
_APPLICATION_RE = re.compile(r"^generation-mutation-application-(\d{4})\.json$")
_SUBSTRATE_BUILD_RE = re.compile(r"^scientific-substrate-build-(\d{4})\.json$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


class GenerationMutationError(RuntimeError):
    """Raised when generation-dependent mutations cannot proceed safely."""


@dataclass(frozen=True)
class GenerationMutationApplyResult:
    """Persisted result of applying selected generation mutations."""

    run_id: str
    plan: GenerationMutationPlan
    report: GenerationMutationInspectionReport
    new_substrates: list[ScientificSubstrate]
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    substrate_artifacts: list[ArtifactRef]


def plan_generation_mutations(
    *,
    run_id: str,
    root: str | Path = ".",
    cycle_index: int | None = None,
    max_mutations: int = 5,
    write_report: bool = True,
) -> GenerationMutationPlan:
    """Plan fresh deterministic mutations conditioned on the current bounded winner."""
    if max_mutations < 1:
        raise GenerationMutationError("max_mutations must be at least 1.")
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    if not reports.is_dir():
        raise GenerationMutationError(f"Reports directory not found for run_id={run_id}.")

    from factori.creative_search import latest_creative_search_report  # noqa: PLC0415

    search = latest_creative_search_report(root_path, run_id)
    tournament = latest_mutation_tournament_result(root_path, run_id)
    if tournament is None or not tournament.second_generation_winner_selected:
        raise GenerationMutationError(
            "A mutation tournament winner is required for generation mutations."
        )
    requested_cycle = cycle_index or max(2, (search.cycle_count + 1) if search else 2)
    if requested_cycle < 2:
        raise GenerationMutationError("cycle_index must be at least 2.")

    warnings: list[str] = []
    if search is None:
        warnings.append(
            "No prior creative-search report was present; generation context was derived "
            "from the validated mutation tournament."
        )
    try:
        idea_space = inspect_idea_space(run_id=run_id, root=root_path)
    except IdeaSpaceError as exc:
        idea_space = None
        warnings.append(f"Idea-space inspection unavailable: {exc}")
    try:
        idea_tree = inspect_idea_tree(run_id=run_id, root=root_path)
    except IdeaTreeError as exc:
        idea_tree = None
        warnings.append(f"IdeaTree inspection unavailable: {exc}")

    build, substrates, substrate_warnings = latest_scientific_substrate_build(
        root_path, run_id
    )
    warnings.extend(substrate_warnings)
    if build is None or not substrates:
        raise GenerationMutationError(
            "Scientific substrates are required for generation mutation planning."
        )
    by_id = {substrate.substrate_id: substrate for substrate in substrates}
    current_id = tournament.second_generation_winner_substrate_id_optional
    previous_id = tournament.original_winner_substrate_id
    current = by_id.get(current_id or "")
    previous = by_id.get(previous_id)
    current_entry = next(
        (entry for entry in tournament.entries if entry.substrate_id == current_id),
        None,
    )
    previous_entry = next(
        (entry for entry in tournament.entries if entry.substrate_id == previous_id),
        None,
    )
    current_title = (
        current.title
        if current is not None
        else tournament.second_generation_winner_title_optional or "current bounded winner"
    )
    previous_title = (
        previous.title
        if previous is not None
        else next(
            (
                entry.title
                for entry in tournament.entries
                if entry.substrate_id == previous_id
            ),
            "previous bounded winner",
        )
    )
    source_node_ids = _source_node_ids(idea_tree, current_title)
    prior = _prior_semantic_records(root_path, run_id)
    context = GenerationMutationContext(
        run_id=run_id,
        cycle_index=requested_cycle,
        current_winner_title=current_title,
        current_winner_substrate_id_optional=current_id,
        current_winner_score=(
            current_entry.score
            if current_entry
            else search.ending_score
            if search is not None
            else 0.0
        ),
        previous_winner_title=previous_title,
        previous_winner_substrate_id_optional=previous_id,
        previous_winner_score=(
            previous_entry.score
            if previous_entry
            else search.starting_score
            if search is not None
            else 0.0
        ),
        losing_branch_titles=[
            entry.title
            for entry in tournament.entries
            if entry.substrate_id != current_id
        ],
        tournament_metric_rows=tournament.comparison.rows,
        idea_space_missing_axes=(
            [
                *idea_space.missing_axis_warnings,
                *idea_space.underexplored_scientific_axes,
            ]
            if idea_space is not None
            else []
        ),
        prior_mutation_fingerprints=sorted(record["fingerprint"] for record in prior),
        source_creative_search_report_path=_latest_optional_path(
            reports, "creative-search-controller-*.json"
        ),
        source_mutation_tournament_path=_latest_path(
            reports, "mutation-tournament-result-*.json"
        ),
        publication_ready=False,
    )
    proposed = _generation_candidates(
        run_id=run_id,
        cycle_index=requested_cycle,
        domain=current.domain if current is not None else _domain(idea_tree),
        current_substrate_id=current_id,
        previous_substrate_id=previous_id,
        source_node_ids=source_node_ids,
    )
    candidates, duplicate_ids = _deduplicate_candidates(proposed, prior)
    candidates = candidates[:max_mutations]
    selected_count = sum(candidate.selected_for_substrate_build for candidate in candidates)
    diversity = GenerationMutationDiversityCheck(
        candidate_count_before_dedup=len(proposed),
        new_candidate_count=len(candidates),
        duplicate_candidate_count=len(duplicate_ids),
        duplicate_mutation_ids=duplicate_ids,
        compared_prior_candidate_count=len(prior),
        normalized_title_check_passed=not duplicate_ids,
        model_equation_token_check_passed=not duplicate_ids,
        experiment_design_token_check_passed=not duplicate_ids,
        diversity_check_passed=bool(candidates),
        warnings=(
            ["All deterministic generation candidates were semantic duplicates."]
            if not candidates
            else []
        ),
        publication_ready=False,
    )
    number = _next_number(reports, _PLAN_RE)
    status = (
        "no_new_generation_mutations"
        if not candidates
        else "completed_with_warnings"
        if warnings or duplicate_ids
        else "completed"
    )
    plan = GenerationMutationPlan(
        run_id=run_id,
        plan_id=f"generation-mutation-plan-{number:04d}",
        planning_status=status,
        context=context,
        max_mutations=max_mutations,
        mutation_count=len(candidates),
        selected_for_substrate_build_count=selected_count,
        operators_used=[candidate.operator for candidate in candidates],
        candidates=candidates,
        diversity_check=diversity,
        warnings=[*warnings, *diversity.warnings],
        publication_ready=False,
    )
    if write_report:
        _write_plan(root_path, plan)
    return plan


def apply_generation_mutations(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    max_mutations: int = 3,
) -> GenerationMutationApplyResult:
    """Append selected, previously unapplied generation mutations and substrates."""
    if max_mutations < 1:
        raise GenerationMutationError("max_mutations must be at least 1.")
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    if validate_ledger_tip(run_id, root=root_path).blocking_findings:
        raise GenerationMutationError("Ledger validation blocks generation mutation application.")
    plan_path = _latest_plan_path(reports)
    if plan_path is None:
        plan = plan_generation_mutations(
            run_id=run_id,
            root=root_path,
            max_mutations=max(5, max_mutations),
            write_report=True,
        )
        plan_path = _latest_plan_path(reports)
    else:
        plan = _load_plan(plan_path)

    applied_ids = _applied_generation_mutation_ids(reports)
    selected = [
        candidate
        for candidate in plan.candidates
        if candidate.selected_for_substrate_build
        and candidate.mutation_id not in applied_ids
    ][:max_mutations]
    if not selected:
        raise GenerationMutationError(
            "No new selected generation mutations are available to apply."
        )

    previous_build, previous_substrates, warnings = latest_scientific_substrate_build(
        root_path, run_id
    )
    if previous_build is None or warnings:
        raise GenerationMutationError(
            "; ".join(warnings) or "Latest scientific substrate build is unavailable."
        )
    build_number = _next_number(reports, _SUBSTRATE_BUILD_RE)
    build_id = f"scientific-substrate-build-{build_number:04d}"
    application_number = _next_number(reports, _APPLICATION_RE)
    application_id = f"generation-mutation-application-{application_number:04d}"
    new_substrates = [
        _substrate_from_generation(run_id=run_id, candidate=candidate)
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
        build_status="completed",
        source_idea_space_report_path_optional=None,
        source_idea_tree_report_path_optional=None,
        requested_mutation_axis_optional="generation-dependent winner mutations",
        max_substrates=len(all_substrates),
        recommended_mutation_axes=[
            candidate.why_scientifically_distinct for candidate in plan.candidates
        ],
        built_mutation_axes=[candidate.title for candidate in selected],
        substrate_paths=[*previous_build.substrate_paths, *new_paths],
        substrate_count=len(all_substrates),
        selected_substrate_id_optional=new_substrates[0].substrate_id,
        selected_substrate_title_optional=new_substrates[0].title,
        pca_low_rank_substrate_id_optional=next(
            (
                substrate.substrate_id
                for substrate in all_substrates
                if "low_rank" in substrate.concrete_model_object.model_type
                or "low-rank" in (substrate.source_mutation_axis_optional or "").lower()
            ),
            None,
        ),
        warnings=[],
        publication_ready=False,
    )
    report = _inspection_report(
        plan=plan,
        applied_mutation_count=len(selected),
        new_idea_tree_node_count=len(selected),
        new_scientific_substrate_count=len(new_substrates),
        applied_mutation_ids=[candidate.mutation_id for candidate in selected],
        scientific_substrate_paths=new_paths,
        build_report_path=f"runs/{run_id}/reports/{build_id}.json",
    )
    metadata = _metadata("generation_mutation_apply")
    specs = [
        ArtifactWriteSpec(
            _substrate_artifact_id(build_number, index, substrate),
            ArtifactType.REPORT,
            substrate,
            "json",
            metadata,
        )
        for index, substrate in enumerate(new_substrates, start=1)
    ]
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
            ArtifactWriteSpec(application_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{application_id}-markdown",
                ArtifactType.REPORT,
                render_generation_mutation_application_markdown(report),
                "markdown",
                metadata,
                filename_stem=application_id,
            ),
        ]
    )
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.GENERATION_MUTATIONS_APPLIED,
        commit_payload={
            "run_id": run_id,
            "plan_id": plan.plan_id,
            "cycle_index": plan.context.cycle_index,
            "applied_mutation_count": len(selected),
            "new_scientific_substrate_count": len(new_substrates),
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return GenerationMutationApplyResult(
        run_id=run_id,
        plan=plan,
        report=report,
        new_substrates=new_substrates,
        persistence=persistence,
        report_artifact=by_id[application_id],
        substrate_artifacts=[
            by_id[_substrate_artifact_id(build_number, index, substrate)]
            for index, substrate in enumerate(new_substrates, start=1)
        ],
    )


def inspect_generation_mutations(
    *, run_id: str, root: str | Path = "."
) -> GenerationMutationInspectionReport:
    """Inspect the latest generation mutation plan and application."""
    reports = Path(root) / "runs" / run_id / "reports"
    plan_path = _latest_plan_path(reports)
    if plan_path is None:
        return GenerationMutationInspectionReport(
            run_id=run_id,
            generation_mutation_plan_present=False,
            warnings=["No generation mutation plan is present."],
            publication_ready=False,
        )
    plan = _load_plan(plan_path)
    application_path = _latest_application_path(reports)
    if application_path is not None:
        try:
            application = GenerationMutationInspectionReport.model_validate_json(
                application_path.read_text(encoding="utf-8")
            )
            if application.latest_plan_id_optional == plan.plan_id:
                return application
        except (OSError, ValidationError):
            pass
    return _inspection_report(plan=plan)


def latest_generation_mutation_applications(
    *, run_id: str, root: str | Path = "."
) -> list[tuple[GenerationMutationInspectionReport, Path]]:
    """Load valid append-only generation mutation application reports."""
    reports = Path(root) / "runs" / run_id / "reports"
    loaded: list[tuple[GenerationMutationInspectionReport, Path]] = []
    for _, path in sorted(
        (int(match.group(1)), path)
        for path in reports.glob("generation-mutation-application-*.json")
        if (match := _APPLICATION_RE.fullmatch(path.name))
    ):
        try:
            loaded.append(
                (
                    GenerationMutationInspectionReport.model_validate_json(
                        path.read_text(encoding="utf-8")
                    ),
                    path,
                )
            )
        except (OSError, ValidationError):
            continue
    return loaded


def render_generation_mutation_plan_markdown(plan: GenerationMutationPlan) -> str:
    """Render one readable generation mutation plan."""
    lines = [
        "# Generation-Dependent Mutation Plan",
        "",
        f"- Run ID: `{plan.run_id}`",
        f"- Plan ID: `{plan.plan_id}`",
        f"- Cycle: `{plan.context.cycle_index}`",
        f"- Current winner: `{plan.context.current_winner_title}`",
        f"- Status: `{plan.planning_status}`",
        f"- New mutations: `{plan.mutation_count}`",
        f"- Selected: `{plan.selected_for_substrate_build_count}`",
        "- publication_ready: false",
        "",
    ]
    for candidate in plan.candidates:
        lines.extend(_candidate_markdown(candidate))
    lines.extend(_boundary_markdown())
    return "\n".join(lines)


def render_generation_mutation_application_markdown(
    report: GenerationMutationInspectionReport,
) -> str:
    """Render one generation mutation application summary."""
    return "\n".join(
        [
            "# Generation Mutation Application",
            "",
            f"- Run ID: `{report.run_id}`",
            f"- Plan ID: `{report.latest_plan_id_optional or 'none'}`",
            f"- Current winner: `{report.current_winner_optional or 'none'}`",
            f"- Applied mutations: `{report.applied_mutation_count}`",
            f"- New IdeaTree nodes: `{report.new_idea_tree_node_count}`",
            f"- New ScientificSubstrates: `{report.new_scientific_substrate_count}`",
            "- publication_ready: false",
            "",
            *_boundary_markdown(),
        ]
    )


def generation_mutation_semantic_fingerprint(
    *, title: str, equations: list[str], experiment_design: str
) -> str:
    """Return a stable semantic identity over title, equation, and experiment tokens."""
    return sha256_json(
        {
            "title_tokens": sorted(_tokens(title)),
            "equation_tokens": sorted(_tokens(" ".join(equations))),
            "experiment_tokens": sorted(_tokens(experiment_design)),
        }
    )


def _generation_candidates(
    *,
    run_id: str,
    cycle_index: int,
    domain: str,
    current_substrate_id: str | None,
    previous_substrate_id: str,
    source_node_ids: list[str],
) -> list[GenerationMutationCandidate]:
    sources = [value for value in [current_substrate_id, previous_substrate_id] if value]
    definitions = [
        {
            "slug": "multi-scale-boundary-robustness",
            "operator": GenerationMutationOperator.ROBUSTNESS_REFINEMENT,
            "title": "Multi-Scale Boundary Robustness for Region-Specific Distance Decay",
            "question": (
                "Does the heterogeneous-alpha advantage persist across multiple aggregation "
                "scales rather than one perturbation level?"
            ),
            "model": "Multi-scale aggregation of region-specific distance-decay flows.",
            "equations": [
                "F^{(s)}_{ab} = sum_{i in a_s} sum_{j in b_s} A_i B_j "
                "d_ij^{-alpha_i} exp(epsilon_ij)"
            ],
            "baseline": "single-scale boundary perturbation and pooled-alpha gravity model",
            "experiment": (
                "Generate fine regions, aggregate into multiple coarse partitions, and measure "
                "MAE/RMSE performance degradation across scales."
            ),
            "expected": (
                "The heterogeneous-alpha advantage remains positive across more than one "
                "deterministic aggregation scale."
            ),
            "distinct": (
                "It refines the current robustness winner from one perturbation level to an "
                "explicit scale response curve."
            ),
            "risk": "The advantage may vanish at coarse scales that erase origin heterogeneity.",
            "selected": True,
        },
        {
            "slug": "clustered-alpha-boundary-perturbation",
            "operator": GenerationMutationOperator.ROBUSTNESS_PARSIMONY_HYBRID,
            "title": "Clustered Distance Decay Under Boundary Perturbation",
            "question": (
                "Can cluster-level alpha retain boundary robustness with fewer parameters than "
                "full origin-specific alpha?"
            ),
            "model": "Cluster-level distance decay evaluated under boundary perturbations.",
            "equations": ["F_ij = A_i B_j d_ij^{-alpha_{g(i)}} exp(epsilon_ij)"],
            "baseline": "pooled alpha and full origin-specific alpha under identical boundaries",
            "experiment": (
                "Compare pooled alpha, full alpha, and cluster alpha under deterministic boundary "
                "perturbations using MAE, RMSE, and parameter count."
            ),
            "expected": (
                "Cluster alpha retains most boundary robustness with fewer parameters than full "
                "origin-specific alpha."
            ),
            "distinct": (
                "It hybridizes the robustness winner with the prior parsimony branch rather than "
                "repeating either experiment."
            ),
            "risk": "Incorrect cluster structure may underfit both boundary and alpha variation.",
            "selected": True,
        },
        {
            "slug": "adversarial-boundary-stress",
            "operator": GenerationMutationOperator.ADVERSARIAL_BOUNDARY_STRESS,
            "title": "Adversarial Boundary Perturbation Stress Test for Distance-Decay Models",
            "question": (
                "Can an adversarial aggregation of regions erase the heterogeneous-alpha "
                "advantage?"
            ),
            "model": "Worst-case deterministic boundary search over distance-decay models.",
            "equations": [
                "s* = argmax_s degradation(F^{(s)}, heterogeneous_alpha)"
            ],
            "baseline": "random or fixed boundary perturbations from the current winner",
            "experiment": (
                "Search over a fixed deterministic set of boundary perturbations and report "
                "worst-case MAE/RMSE degradation."
            ),
            "expected": (
                "Worst-case degradation is bounded and explicitly identifies the perturbation "
                "that most weakens the heterogeneous-alpha advantage."
            ),
            "distinct": (
                "It changes average robustness evaluation into a worst-case spatial stress test."
            ),
            "risk": "An adversarial partition may erase the measured advantage entirely.",
            "selected": True,
        },
        {
            "slug": "low-rank-boundary-residual-diagnostic",
            "operator": GenerationMutationOperator.ROBUSTNESS_REPRESENTATION_HYBRID,
            "title": "Low-Rank Residual Diagnostics for Boundary-Induced Spatial Heterogeneity",
            "question": (
                "Do boundary perturbations leave structured residual axes after region-specific "
                "distance decay?"
            ),
            "model": "Low-rank diagnostic of residual boundary structure after alpha_i fitting.",
            "equations": [
                "R^{boundary}_{ij} = log(F^{perturbed}_{ij}+c) - "
                "log(Fhat^{alpha_i}_{ij}+c)",
                "R^{boundary} approx U_k S_k V_k^T",
            ],
            "baseline": "region-specific distance decay without residual factor diagnostics",
            "experiment": (
                "Fit heterogeneous alpha under boundary perturbation, then apply low-rank "
                "residual analysis and report explained variance and held-out residual error."
            ),
            "expected": (
                "Leading residual axes recover structured boundary effects only when latent "
                "boundary-induced heterogeneity remains."
            ),
            "distinct": (
                "It uses the losing PCA representation as a diagnostic layer on the robustness "
                "winner rather than as a competing predictor."
            ),
            "risk": "Residual axes may capture noise or aggregation artifacts without stability.",
            "selected": False,
        },
        {
            "slug": "null-heterogeneity-boundary-control",
            "operator": GenerationMutationOperator.NEGATIVE_CONTROL,
            "title": "Null Heterogeneity Boundary Stress Test",
            "question": (
                "When alpha_i is constant, does the heterogeneous-alpha model lose its advantage "
                "as expected?"
            ),
            "model": "Negative-control boundary DGP with constant distance-decay alpha.",
            "equations": ["alpha_i = alpha for all origins i"],
            "baseline": "pooled-alpha gravity model under the same boundary perturbations",
            "experiment": (
                "Run the boundary perturbation test with alpha_i equal across all origins and "
                "compare pooled and heterogeneous parameterizations."
            ),
            "expected": (
                "The heterogeneous-alpha advantage approaches zero under the null heterogeneity "
                "DGP."
            ),
            "distinct": (
                "It adds a falsifying negative control rather than another positive robustness "
                "variant."
            ),
            "risk": "Finite synthetic noise may create a spurious flexible-model advantage.",
            "selected": False,
        },
    ]
    candidates = []
    for definition in definitions:
        mutation_id = f"generation-mutation-cycle-{cycle_index:04d}-{definition['slug']}"
        equations = list(definition["equations"])
        experiment = str(definition["experiment"])
        candidates.append(
            GenerationMutationCandidate(
                mutation_id=mutation_id,
                cycle_index=cycle_index,
                source_substrate_ids=sources,
                source_idea_node_ids=source_node_ids,
                operator=definition["operator"],
                title=str(definition["title"]),
                domain=domain,
                research_question=str(definition["question"]),
                model_object=str(definition["model"]),
                equations=equations,
                baseline=str(definition["baseline"]),
                experiment_design=experiment,
                expected_result_pattern=str(definition["expected"]),
                why_scientifically_distinct=str(definition["distinct"]),
                risk_or_failure_mode=str(definition["risk"]),
                semantic_fingerprint=generation_mutation_semantic_fingerprint(
                    title=str(definition["title"]),
                    equations=equations,
                    experiment_design=experiment,
                ),
                selected_for_substrate_build=bool(definition["selected"]),
                publication_ready=False,
            )
        )
    return candidates


def _deduplicate_candidates(
    candidates: list[GenerationMutationCandidate],
    prior: list[dict[str, object]],
) -> tuple[list[GenerationMutationCandidate], list[str]]:
    accepted: list[GenerationMutationCandidate] = []
    duplicate_ids: list[str] = []
    comparisons = list(prior)
    for candidate in candidates:
        record = _semantic_record(
            mutation_id=candidate.mutation_id,
            title=candidate.title,
            equations=candidate.equations,
            experiment=candidate.experiment_design,
            fingerprint=candidate.semantic_fingerprint,
        )
        if any(_semantic_duplicate(record, previous) for previous in comparisons):
            duplicate_ids.append(candidate.mutation_id)
            continue
        accepted.append(candidate)
        comparisons.append(record)
    return accepted, duplicate_ids


def _prior_semantic_records(root: Path, run_id: str) -> list[dict[str, object]]:
    records: list[dict[str, object]] = []
    for report, _ in latest_creative_mutation_reports(run_id=run_id, root=root):
        for candidate in report.candidates:
            records.append(
                _semantic_record(
                    mutation_id=candidate.mutation_id,
                    title=candidate.title,
                    equations=candidate.equations,
                    experiment=candidate.experiment_design,
                )
            )
    reports = root / "runs" / run_id / "reports"
    for _, path in sorted(
        (int(match.group(1)), path)
        for path in reports.glob("generation-mutation-plan-*.json")
        if (match := _PLAN_RE.fullmatch(path.name))
    ):
        try:
            plan = GenerationMutationPlan.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError):
            continue
        for candidate in plan.candidates:
            records.append(
                _semantic_record(
                    mutation_id=candidate.mutation_id,
                    title=candidate.title,
                    equations=candidate.equations,
                    experiment=candidate.experiment_design,
                    fingerprint=candidate.semantic_fingerprint,
                )
            )
    unique: dict[str, dict[str, object]] = {}
    for record in records:
        unique[str(record["fingerprint"])] = record
    return list(unique.values())


def _semantic_record(
    *,
    mutation_id: str,
    title: str,
    equations: list[str],
    experiment: str,
    fingerprint: str | None = None,
) -> dict[str, object]:
    return {
        "mutation_id": mutation_id,
        "fingerprint": fingerprint
        or generation_mutation_semantic_fingerprint(
            title=title,
            equations=equations,
            experiment_design=experiment,
        ),
        "title_tokens": _tokens(title),
        "equation_tokens": _tokens(" ".join(equations)),
        "experiment_tokens": _tokens(experiment),
    }


def _semantic_duplicate(left: dict[str, object], right: dict[str, object]) -> bool:
    if left["fingerprint"] == right["fingerprint"]:
        return True
    title_similarity = _jaccard(left["title_tokens"], right["title_tokens"])
    equation_similarity = _jaccard(left["equation_tokens"], right["equation_tokens"])
    experiment_similarity = _jaccard(
        left["experiment_tokens"], right["experiment_tokens"]
    )
    return title_similarity >= 0.92 or (
        equation_similarity >= 0.9 and experiment_similarity >= 0.82
    )


def _substrate_from_generation(
    *, run_id: str, candidate: GenerationMutationCandidate
) -> ScientificSubstrate:
    creative = CreativeMutationCandidate(
        mutation_id=candidate.mutation_id,
        source_substrate_ids=candidate.source_substrate_ids,
        source_idea_node_ids=candidate.source_idea_node_ids,
        operator=_creative_operator(candidate.operator),
        title=candidate.title,
        domain=candidate.domain,
        research_question=candidate.research_question,
        model_object=candidate.model_object,
        equations=candidate.equations,
        baseline=candidate.baseline,
        experiment_design=candidate.experiment_design,
        expected_result_pattern=candidate.expected_result_pattern,
        why_scientifically_distinct=candidate.why_scientifically_distinct,
        risk_or_failure_mode=candidate.risk_or_failure_mode,
        parent_tournament_result_id_optional=None,
        selected_for_substrate_build=candidate.selected_for_substrate_build,
        publication_ready=False,
    )
    substrate = scientific_substrate_from_creative_mutation(
        run_id=run_id, candidate=creative
    )
    return substrate.model_copy(
        update={
            "substrate_id": "scientific-substrate-" + candidate.mutation_id,
            "source_mutation_axis_optional": candidate.operator.value,
            "selected_for_next_experiment": candidate.selected_for_substrate_build,
        }
    )


def _creative_operator(operator: GenerationMutationOperator) -> CreativeMutationOperator:
    if operator is GenerationMutationOperator.ROBUSTNESS_REFINEMENT:
        return CreativeMutationOperator.WINNER_REFINEMENT
    if operator in {
        GenerationMutationOperator.ROBUSTNESS_PARSIMONY_HYBRID,
        GenerationMutationOperator.ROBUSTNESS_REPRESENTATION_HYBRID,
    }:
        return CreativeMutationOperator.WINNER_LOSER_HYBRID
    return CreativeMutationOperator.ROBUSTNESS_STRESS_TEST


def _inspection_report(
    *,
    plan: GenerationMutationPlan,
    applied_mutation_count: int = 0,
    new_idea_tree_node_count: int = 0,
    new_scientific_substrate_count: int = 0,
    applied_mutation_ids: list[str] | None = None,
    scientific_substrate_paths: list[str] | None = None,
    build_report_path: str | None = None,
) -> GenerationMutationInspectionReport:
    titles = [candidate.title for candidate in plan.candidates]
    return GenerationMutationInspectionReport(
        run_id=plan.run_id,
        generation_mutation_plan_present=True,
        latest_plan_id_optional=plan.plan_id,
        planning_status_optional=plan.planning_status,
        cycle_index_optional=plan.context.cycle_index,
        current_winner_optional=plan.context.current_winner_title,
        mutation_count=plan.mutation_count,
        selected_for_substrate_build_count=plan.selected_for_substrate_build_count,
        applied_mutation_count=applied_mutation_count,
        new_idea_tree_node_count=new_idea_tree_node_count,
        new_scientific_substrate_count=new_scientific_substrate_count,
        applied_mutation_ids=applied_mutation_ids or [],
        scientific_substrate_paths=scientific_substrate_paths or [],
        scientific_substrate_build_report_path_optional=build_report_path,
        includes_multi_scale_boundary_robustness=any(
            "Multi-Scale Boundary Robustness" in title for title in titles
        ),
        includes_clustered_alpha_boundary_perturbation=any(
            "Clustered Distance Decay Under Boundary Perturbation" in title
            for title in titles
        ),
        includes_low_rank_boundary_diagnostic=any(
            "Low-Rank Residual Diagnostics" in title for title in titles
        ),
        includes_adversarial_boundary_stress=any(
            "Adversarial Boundary Perturbation" in title for title in titles
        ),
        includes_null_heterogeneity_control=any(
            "Null Heterogeneity Boundary Stress Test" in title for title in titles
        ),
        latest_plan_optional=plan,
        warnings=plan.warnings,
        publication_ready=False,
    )


def _source_node_ids(idea_tree: object | None, current_title: str) -> list[str]:
    if idea_tree is None:
        return []
    nodes = getattr(idea_tree, "nodes", [])
    matches = [node for node in nodes if node.title == current_title]
    stage_priority = {
        "generation_mutation": 0,
        "creative_mutation": 1,
        "stage_c": 2,
        "final_manuscript_regeneration": 3,
    }
    match = min(
        matches,
        key=lambda node: stage_priority.get(node.stage_origin, 2),
        default=None,
    )
    if match is not None:
        return [match.node_id]
    final_id = getattr(idea_tree, "final_node_id_optional", None)
    return [final_id] if final_id else []


def _domain(idea_tree: object | None) -> str:
    if idea_tree is None:
        return "human geography"
    nodes = getattr(idea_tree, "nodes", [])
    root_id = getattr(idea_tree, "root_node_id", None)
    root = next((node for node in nodes if node.node_id == root_id), None)
    return root.domain if root is not None else "human geography"


def _write_plan(root: Path, plan: GenerationMutationPlan) -> None:
    store = ArtifactStore(root)
    metadata = _metadata("generation_mutation_plan")
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
        markdown=render_generation_mutation_plan_markdown(plan),
        metadata=metadata,
        filename_stem=plan.plan_id,
    )


def _candidate_markdown(candidate: GenerationMutationCandidate) -> list[str]:
    return [
        f"## {candidate.title}",
        "",
        f"- ID: `{candidate.mutation_id}`",
        f"- Operator: `{candidate.operator.value}`",
        f"- Selected: `{str(candidate.selected_for_substrate_build).lower()}`",
        f"- Question: {candidate.research_question}",
        f"- Model: {'; '.join(candidate.equations)}",
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
        "Generation mutations are planning context only. They create no experiment evidence, "
        "scientific validation, or publication readiness.",
        "",
        "publication_ready=false",
    ]


def _load_plan(path: Path) -> GenerationMutationPlan:
    try:
        return GenerationMutationPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise GenerationMutationError(f"Generation mutation plan is corrupt: {path}") from exc


def _latest_plan_path(reports: Path) -> Path | None:
    paths = sorted(
        (int(match.group(1)), path)
        for path in reports.glob("generation-mutation-plan-*.json")
        if (match := _PLAN_RE.fullmatch(path.name))
    )
    return paths[-1][1] if paths else None


def _latest_application_path(reports: Path) -> Path | None:
    paths = sorted(
        (int(match.group(1)), path)
        for path in reports.glob("generation-mutation-application-*.json")
        if (match := _APPLICATION_RE.fullmatch(path.name))
    )
    return paths[-1][1] if paths else None


def _applied_generation_mutation_ids(reports: Path) -> set[str]:
    applied: set[str] = set()
    for _, path in sorted(
        (int(match.group(1)), path)
        for path in reports.glob("generation-mutation-application-*.json")
        if (match := _APPLICATION_RE.fullmatch(path.name))
    ):
        try:
            report = GenerationMutationInspectionReport.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValidationError):
            continue
        applied.update(report.applied_mutation_ids)
    return applied


def _latest_path(reports: Path, pattern: str) -> str:
    paths = sorted(
        path
        for path in reports.glob(pattern)
        if path.is_file() and not path.name.endswith(".meta.json")
    )
    if not paths:
        raise GenerationMutationError(f"Required source report is missing: {pattern}")
    root = reports.parents[2]
    return paths[-1].relative_to(root).as_posix()


def _latest_optional_path(reports: Path, pattern: str) -> str | None:
    paths = sorted(
        path
        for path in reports.glob(pattern)
        if path.is_file() and not path.name.endswith(".meta.json")
    )
    if not paths:
        return None
    return paths[-1].relative_to(reports.parents[2]).as_posix()


def _next_number(reports: Path, regex: re.Pattern[str]) -> int:
    numbers = [
        int(match.group(1))
        for path in reports.iterdir()
        if path.is_file() and (match := regex.fullmatch(path.name))
    ]
    return max(numbers, default=0) + 1


def _substrate_artifact_id(
    build_number: int, index: int, substrate: ScientificSubstrate
) -> str:
    return (
        f"scientific-substrate-{build_number:04d}-{index:02d}-"
        f"{_slug(substrate.title)}"
    )


def _slug(value: str) -> str:
    return "-".join(_TOKEN_RE.findall(value.lower())[:10]) or "generation-mutation"


def _tokens(value: str) -> set[str]:
    return set(_TOKEN_RE.findall(value.lower()))


def _jaccard(left: object, right: object) -> float:
    left_set = set(left) if isinstance(left, (set, list, tuple)) else set()
    right_set = set(right) if isinstance(right, (set, list, tuple)) else set()
    union = left_set | right_set
    return len(left_set & right_set) / len(union) if union else 1.0


def _metadata(stage: str) -> dict[str, object]:
    return {
        "stage": stage,
        "artifact_role": "generation_mutation_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }


__all__ = [
    "GenerationMutationApplyResult",
    "GenerationMutationError",
    "apply_generation_mutations",
    "generation_mutation_semantic_fingerprint",
    "inspect_generation_mutations",
    "latest_generation_mutation_applications",
    "plan_generation_mutations",
    "render_generation_mutation_application_markdown",
    "render_generation_mutation_plan_markdown",
]
