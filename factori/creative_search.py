"""Bounded recursive controller over the deterministic creative-search stages."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import ValidationError

from factori.artifacts import ArtifactStore
from factori.creative_mutations import (
    CreativeMutationError,
    apply_creative_mutations,
    inspect_creative_mutations,
    latest_creative_mutation_reports,
    plan_creative_mutations,
)
from factori.final_bundle_verification import (
    FinalBundleVerificationError,
    verify_final_release_bundle,
)
from factori.final_manuscript_regeneration import (
    FinalManuscriptRegenerationError,
    regenerate_final_manuscript,
)
from factori.final_release_bundle import FinalReleaseBundleError, build_final_release_bundle
from factori.generation_mutations import GenerationMutationError
from factori.idea_space import IdeaSpaceError, inspect_idea_space
from factori.idea_tree import IdeaTreeError, inspect_idea_tree
from factori.ledger import ResearchLedger
from factori.mutation_tournament import (
    MutationTournamentError,
    latest_mutation_tournament_result,
    run_mutation_tournament,
)
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.rerun_policy import validate_ledger_tip
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    CreativeSearchControllerConfig,
    CreativeSearchControllerReport,
    CreativeSearchCycle,
    CreativeSearchInspectionReport,
    CreativeSearchLineageEntry,
    CreativeSearchStopReason,
    IdeaSpaceDiversityReport,
    MutationTournamentEntry,
    MutationTournamentResult,
    SubstrateTournamentEntry,
    SubstrateTournamentResult,
)
from factori.scientific_substrate import (
    ScientificSubstrateError,
    build_scientific_substrate,
    latest_scientific_substrate_build,
)
from factori.storage_protocols import Clock, SystemClock
from factori.substrate_tournament import (
    SubstrateTournamentError,
    latest_substrate_tournament_result,
    run_substrate_tournament,
)

_REPORT_RE = re.compile(r"^creative-search-controller-(\d{4})\.json$")


class CreativeSearchError(RuntimeError):
    """Raised when recursive creative search cannot proceed safely."""


@dataclass(frozen=True)
class CreativeSearchRunResult:
    """Persisted recursive creative-search outcome."""

    run_id: str
    report: CreativeSearchControllerReport
    cycle_persistence: list[PersistenceResult]
    report_persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


def run_creative_search(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    max_cycles: int = 3,
    min_improvement: float = 0.01,
    max_mutations_per_cycle: int = 3,
    max_substrates_per_cycle: int = 3,
    stop_if_no_improvement: bool = True,
    stop_if_diversity_collapses: bool = True,
    clock: Clock | None = None,
) -> CreativeSearchRunResult:
    """Run or safely reuse bounded creative stages and finalize the best current branch."""
    config = CreativeSearchControllerConfig(
        run_id=run_id,
        max_cycles=max_cycles,
        min_improvement=min_improvement,
        max_mutations_per_cycle=max_mutations_per_cycle,
        max_substrates_per_cycle=max_substrates_per_cycle,
        stop_if_no_improvement=stop_if_no_improvement,
        stop_if_diversity_collapses=stop_if_diversity_collapses,
        publication_ready=False,
    )
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    if not reports.is_dir():
        raise CreativeSearchError(f"Reports directory not found for run_id={run_id}.")
    if validate_ledger_tip(run_id, root=root_path).blocking_findings:
        raise CreativeSearchError("Ledger validation blocks recursive creative search.")

    timestamp = clock or SystemClock()
    search_number = _next_search_number(reports)
    search_id = f"creative-search-{search_number:04d}"
    started_at = timestamp.now()
    cycles: list[CreativeSearchCycle] = []
    cycle_persistence: list[PersistenceResult] = []
    warnings: list[str] = []
    no_improvement_cycles = 0
    stop_reason = CreativeSearchStopReason.MAX_CYCLES_REACHED
    stop_detail = "The configured creative-search cycle budget was reached."

    for cycle_index in range(1, config.max_cycles + 1):
        cycle_started = timestamp.now()
        steps_reused: list[str] = []
        steps_executed: list[str] = []
        artifact_paths: list[str] = []

        try:
            inspect_idea_tree(run_id=run_id, root=root_path)
            idea_before = _idea_space_before(root_path, run_id)
            if idea_before is None:
                idea_before = inspect_idea_space(run_id=run_id, root=root_path)
            steps_reused.extend(["idea_tree_inspection", "idea_space_diagnostic"])

            build, substrates, build_warnings = latest_scientific_substrate_build(
                root_path, run_id
            )
            if build_warnings:
                raise CreativeSearchError("; ".join(build_warnings))
            if build is None or not substrates:
                built = build_scientific_substrate(
                    run_id=run_id,
                    root=root_path,
                    store=store,
                    ledger=ledger,
                    max_substrates=config.max_substrates_per_cycle,
                )
                build = built.report
                substrates = built.substrates
                artifact_paths.append(built.report_artifact.path)
                steps_executed.append("scientific_substrate_build")
            else:
                steps_reused.append("scientific_substrate_build")

            first_tournament = latest_substrate_tournament_result(root_path, run_id)
            if first_tournament is None:
                first_run = run_substrate_tournament(
                    run_id=run_id,
                    root=root_path,
                    store=store,
                    ledger=ledger,
                )
                first_tournament = first_run.result
                artifact_paths.append(first_run.result_artifact.path)
                steps_executed.append("substrate_tournament")
            else:
                _validate_substrate_tournament(first_tournament)
                steps_reused.append("substrate_tournament")

            mutation_inspection = inspect_creative_mutations(
                run_id=run_id,
                root=root_path,
            )
            if mutation_inspection.latest_plan_optional is None:
                plan_creative_mutations(
                    run_id=run_id,
                    root=root_path,
                    max_mutations=max(4, config.max_mutations_per_cycle),
                    write_report=True,
                )
                mutation_inspection = inspect_creative_mutations(
                    run_id=run_id,
                    root=root_path,
                )
                steps_executed.append("creative_mutation_plan")
            else:
                steps_reused.append("creative_mutation_plan")
            plan = mutation_inspection.latest_plan_optional
            if plan is None:
                raise CreativeSearchError("Creative mutation planning produced no valid plan.")

            applied_ids = _applied_mutation_ids(root_path, run_id)
            selected_ids = {
                candidate.mutation_id
                for candidate in plan.candidates
                if candidate.selected_for_substrate_build
            }
            unapplied_ids = selected_ids - applied_ids
            new_idea_nodes = 0
            new_substrates = 0
            generation_mutations_applied = False
            if unapplied_ids:
                applied = apply_creative_mutations(
                    run_id=run_id,
                    root=root_path,
                    store=store,
                    ledger=ledger,
                    max_mutations=config.max_mutations_per_cycle,
                )
                new_idea_nodes = applied.report.new_idea_tree_node_count
                new_substrates = applied.report.new_scientific_substrate_count
                artifact_paths.append(applied.report_artifact.path)
                steps_executed.append("creative_mutation_apply")
            else:
                steps_reused.append("creative_mutation_apply")
                from factori.generation_mutations import (  # noqa: PLC0415
                    apply_generation_mutations,
                    inspect_generation_mutations,
                    latest_generation_mutation_applications,
                    plan_generation_mutations,
                )

                generation_inspection = inspect_generation_mutations(
                    run_id=run_id,
                    root=root_path,
                )
                generation_applied_ids = {
                    mutation_id
                    for application, _ in latest_generation_mutation_applications(
                        run_id=run_id,
                        root=root_path,
                    )
                    for mutation_id in application.applied_mutation_ids
                }
                generation_plan = generation_inspection.latest_plan_optional
                available_generation_ids = (
                    {
                        candidate.mutation_id
                        for candidate in generation_plan.candidates
                        if candidate.selected_for_substrate_build
                    }
                    - generation_applied_ids
                    if generation_plan is not None
                    else set()
                )
                if not available_generation_ids:
                    generation_plan = plan_generation_mutations(
                        run_id=run_id,
                        root=root_path,
                        cycle_index=_next_generation_cycle_index(
                            root_path,
                            run_id,
                            cycle_index,
                        ),
                        max_mutations=max(5, config.max_mutations_per_cycle),
                        write_report=True,
                    )
                    available_generation_ids = {
                        candidate.mutation_id
                        for candidate in generation_plan.candidates
                        if candidate.selected_for_substrate_build
                    }
                    steps_executed.append("generation_mutation_plan")
                else:
                    steps_reused.append("generation_mutation_plan")
                if available_generation_ids:
                    generation_apply = apply_generation_mutations(
                        run_id=run_id,
                        root=root_path,
                        store=store,
                        ledger=ledger,
                        max_mutations=config.max_mutations_per_cycle,
                    )
                    generation_mutations_applied = True
                    new_idea_nodes += generation_apply.report.new_idea_tree_node_count
                    new_substrates += (
                        generation_apply.report.new_scientific_substrate_count
                    )
                    artifact_paths.append(generation_apply.report_artifact.path)
                    steps_executed.append("generation_mutation_apply")

            mutation_tournament = latest_mutation_tournament_result(root_path, run_id)
            new_experiments = 0
            if mutation_tournament is None:
                mutation_run = run_mutation_tournament(
                    run_id=run_id,
                    root=root_path,
                    store=store,
                    ledger=ledger,
                )
                mutation_tournament = mutation_run.result
                new_experiments = mutation_tournament.completed_branch_count
                artifact_paths.append(mutation_run.result_artifact.path)
                steps_executed.append("mutation_tournament")
            else:
                _validate_mutation_tournament(mutation_tournament)
                steps_reused.append("mutation_tournament")

            idea_after = inspect_idea_space(run_id=run_id, root=root_path)
            tree_after = inspect_idea_tree(run_id=run_id, root=root_path)
            starting_entry = _first_generation_winner(first_tournament)
            ending_entry = _current_winner(mutation_tournament)
            starting_winner = (
                starting_entry.substrate_title
                if starting_entry is not None
                else first_tournament.winner_substrate_title_optional or "no winner"
            )
            starting_score = starting_entry.tournament_score if starting_entry else 0.0
            ending_winner = (
                ending_entry.title
                if ending_entry is not None
                else mutation_tournament.second_generation_winner_title_optional or starting_winner
            )
            ending_score = ending_entry.score if ending_entry else starting_score
            absolute = round(ending_score - starting_score, 6)
            relative = round(absolute / abs(starting_score), 6) if starting_score else 0.0
            improved = absolute >= config.min_improvement
            no_improvement_cycles = 0 if improved else no_improvement_cycles + 1
            diversity_collapsed = _diversity_collapsed(idea_before, idea_after)
            if len(idea_after.near_duplicate_node_pairs) > len(
                idea_before.near_duplicate_node_pairs
            ):
                warnings.append(
                    f"Cycle {cycle_index} increased near-duplicate idea pairs from "
                    f"{len(idea_before.near_duplicate_node_pairs)} to "
                    f"{len(idea_after.near_duplicate_node_pairs)}."
                )

            stop_reason, stop_detail = _cycle_stop_decision(
                cycle_index=cycle_index,
                config=config,
                no_improvement_cycles=no_improvement_cycles,
                no_new_mutations=not unapplied_ids and not generation_mutations_applied,
                all_mutations_inconclusive=(
                    mutation_tournament.tournament_outcome == "all_mutations_inconclusive"
                ),
                diversity_collapsed=diversity_collapsed,
            )
            cycle = CreativeSearchCycle(
                run_id=run_id,
                search_id=search_id,
                cycle_index=cycle_index,
                cycle_status="completed_with_warnings" if warnings else "completed",
                started_at=cycle_started,
                completed_at=timestamp.now(),
                starting_winner=starting_winner,
                starting_score=starting_score,
                ending_winner=ending_winner,
                ending_score=ending_score,
                absolute_improvement=absolute,
                relative_improvement=relative,
                new_idea_nodes_added=new_idea_nodes,
                new_substrates_added=new_substrates,
                new_experiments_run=new_experiments,
                diversity_score_before=idea_before.diversity_score,
                diversity_score_after=idea_after.diversity_score,
                near_duplicate_count_before=len(idea_before.near_duplicate_node_pairs),
                near_duplicate_count_after=len(idea_after.near_duplicate_node_pairs),
                stop_recommendation=stop_reason,
                steps_reused=steps_reused,
                steps_executed=steps_executed,
                artifact_paths=artifact_paths,
                warnings=list(warnings),
                publication_ready=False,
            )
            cycles.append(cycle)
            lineage = _build_lineage(
                tree=tree_after,
                first_tournament=first_tournament,
                mutation_tournament=mutation_tournament,
                cycle_index=cycle_index,
                root=root_path,
                run_id=run_id,
            )

            provisional = _build_controller_report(
                run_id=run_id,
                search_id=search_id,
                config=config,
                started_at=started_at,
                completed_at=timestamp.now(),
                cycles=cycles,
                stop_reason=stop_reason,
                stop_detail=stop_detail,
                lineage=lineage,
                warnings=warnings,
            )
            final = regenerate_final_manuscript(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                backend="deterministic",
                creative_search_report=provisional,
            )
            bundle = build_final_release_bundle(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
            )
            verification = verify_final_release_bundle(
                bundle_path=root_path / bundle.report.bundle_path,
                root=root_path,
                write_report=True,
            )
            if verification.verification_status not in {"verified", "verified_with_warnings"}:
                raise CreativeSearchError(
                    "Final bundle verification blocked creative-search finalization: "
                    f"{verification.verification_status}."
                )
            if verification.publication_ready or verification.unsupported_claim_count:
                raise CreativeSearchError(
                    "Creative-search finalization failed bounded claim or publication safety."
                )

            cycle = cycle.model_copy(
                update={
                    "artifact_paths": [
                        *cycle.artifact_paths,
                        final.manuscript_artifact.path,
                        bundle.report.bundle_path,
                    ],
                    "cycle_status": (
                        "completed_with_warnings"
                        if verification.verification_status == "verified_with_warnings" or warnings
                        else "completed"
                    ),
                }
            )
            cycles[-1] = cycle
            cycle_persistence.append(
                _persist_cycle(
                    cycle=cycle,
                    search_number=search_number,
                    store=store,
                    ledger=ledger,
                )
            )
            report = _build_controller_report(
                run_id=run_id,
                search_id=search_id,
                config=config,
                started_at=started_at,
                completed_at=timestamp.now(),
                cycles=cycles,
                stop_reason=stop_reason,
                stop_detail=stop_detail,
                lineage=lineage,
                warnings=warnings,
                final_manuscript_path=final.report.final_manuscript_path,
                final_bundle_path=bundle.report.bundle_path,
                verification_status=verification.verification_status,
            )
        except (
            CreativeMutationError,
            CreativeSearchError,
            FinalBundleVerificationError,
            FinalManuscriptRegenerationError,
            FinalReleaseBundleError,
            GenerationMutationError,
            IdeaSpaceError,
            IdeaTreeError,
            MutationTournamentError,
            ScientificSubstrateError,
            SubstrateTournamentError,
        ) as exc:
            raise CreativeSearchError(str(exc)) from exc

        if stop_reason is not CreativeSearchStopReason.MAX_CYCLES_REACHED:
            break

    if not cycles:
        raise CreativeSearchError("Creative search completed no inspectable cycles.")
    report_persistence = _persist_report(
        report=report,
        search_number=search_number,
        store=store,
        ledger=ledger,
    )
    by_id = {artifact.id: artifact for artifact in report_persistence.artifacts}
    report_id = f"creative-search-controller-{search_number:04d}"
    return CreativeSearchRunResult(
        run_id=run_id,
        report=report,
        cycle_persistence=cycle_persistence,
        report_persistence=report_persistence,
        report_artifact=by_id[report_id],
        markdown_artifact=by_id[f"{report_id}-markdown"],
    )


def inspect_creative_search(
    *, run_id: str, root: str | Path = "."
) -> CreativeSearchInspectionReport:
    """Inspect the latest recursive creative-search controller report."""
    report = latest_creative_search_report(Path(root), run_id)
    if report is None:
        return CreativeSearchInspectionReport(
            run_id=run_id,
            creative_search_present=False,
            warnings=["No creative-search controller report is present."],
            publication_ready=False,
        )
    return CreativeSearchInspectionReport(
        run_id=run_id,
        creative_search_present=True,
        latest_search_id_optional=report.search_id,
        controller_status_optional=report.controller_status,
        cycle_count=report.cycle_count,
        stop_reason_optional=report.stop_reason,
        lineage_present=report.lineage_present,
        starting_winner_optional=report.starting_winner,
        ending_winner_optional=report.ending_winner,
        starting_score_optional=report.starting_score,
        ending_score_optional=report.ending_score,
        score_improvement_recorded=report.score_improvement_recorded,
        best_current_winner_optional=report.best_current_winner,
        best_current_score_optional=report.best_current_score,
        final_bundle_verification_status_optional=(
            report.final_bundle_verification_status_optional
        ),
        report_optional=report,
        warnings=report.warnings,
        publication_ready=False,
    )


def latest_creative_search_report(
    root: Path, run_id: str
) -> CreativeSearchControllerReport | None:
    """Load the latest immutable creative-search controller report."""
    reports = root / "runs" / run_id / "reports"
    paths = sorted(
        (int(match.group(1)), path)
        for path in reports.glob("creative-search-controller-*.json")
        if (match := _REPORT_RE.fullmatch(path.name))
    )
    if not paths:
        return None
    try:
        return CreativeSearchControllerReport.model_validate_json(
            paths[-1][1].read_text(encoding="utf-8")
        )
    except (OSError, ValidationError):
        return None


def render_creative_search_markdown(report: CreativeSearchControllerReport) -> str:
    """Render a readable non-evidence recursive search report."""
    lines = [
        "# Recursive Creative Search",
        "",
        f"- Run ID: `{report.run_id}`",
        f"- Search ID: `{report.search_id}`",
        f"- Status: `{report.controller_status}`",
        f"- Stop reason: `{report.stop_reason.value}`",
        f"- Cycles: `{report.cycle_count}`",
        f"- Best current winner: `{report.best_current_winner}`",
        f"- Best current score: `{report.best_current_score}`",
        "- publication_ready: false",
        "",
        "## Cycles",
        "",
        "| cycle | starting winner | starting score | ending winner | ending score | improvement |",
        "|---:|---|---:|---|---:|---:|",
    ]
    for cycle in report.cycles:
        lines.append(
            f"| {cycle.cycle_index} | {cycle.starting_winner} | {cycle.starting_score} | "
            f"{cycle.ending_winner} | {cycle.ending_score} | "
            f"{cycle.absolute_improvement} |"
        )
    lines.extend(["", "## Winning Lineage", ""])
    for entry in report.lineage:
        score = f" score={entry.score_optional}" if entry.score_optional is not None else ""
        lines.append(f"- `{entry.lineage_role}`: {entry.title}{score}")
    lines.extend(
        [
            "",
            "## Evidence Boundary",
            "",
            "This controller report is workflow context only. It does not create scientific ",
            "validation, real-world evidence, novelty, broad correctness, or publication "
            "readiness.",
            "",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def _build_controller_report(
    *,
    run_id: str,
    search_id: str,
    config: CreativeSearchControllerConfig,
    started_at: str,
    completed_at: str,
    cycles: list[CreativeSearchCycle],
    stop_reason: CreativeSearchStopReason,
    stop_detail: str,
    lineage: list[CreativeSearchLineageEntry],
    warnings: list[str],
    final_manuscript_path: str | None = None,
    final_bundle_path: str | None = None,
    verification_status: str | None = None,
) -> CreativeSearchControllerReport:
    first = cycles[0]
    last = cycles[-1]
    total = round(last.ending_score - first.starting_score, 6)
    relative = round(total / abs(first.starting_score), 6) if first.starting_score else 0.0
    status = (
        "completed_with_warnings"
        if warnings or verification_status == "verified_with_warnings"
        else "completed"
    )
    return CreativeSearchControllerReport(
        run_id=run_id,
        search_id=search_id,
        controller_status=status,
        started_at=started_at,
        completed_at=completed_at,
        config=config,
        cycle_count=len(cycles),
        cycles=cycles,
        stop_reason=stop_reason,
        stop_detail=stop_detail,
        lineage=lineage,
        lineage_present=bool(lineage),
        starting_winner=first.starting_winner,
        starting_score=first.starting_score,
        ending_winner=last.ending_winner,
        ending_score=last.ending_score,
        total_absolute_improvement=total,
        total_relative_improvement=relative,
        score_improvement_recorded=total >= config.min_improvement,
        best_current_winner=last.ending_winner,
        best_current_score=last.ending_score,
        final_manuscript_path_optional=final_manuscript_path,
        final_bundle_path_optional=final_bundle_path,
        final_bundle_verification_status_optional=verification_status,
        unsupported_claim_count=0,
        warnings=list(warnings),
        publication_ready=False,
    )


def _build_lineage(
    *,
    tree: object,
    first_tournament: SubstrateTournamentResult,
    mutation_tournament: MutationTournamentResult,
    cycle_index: int,
    root: Path,
    run_id: str,
) -> list[CreativeSearchLineageEntry]:
    nodes = list(getattr(tree, "nodes", []))
    root_id = getattr(tree, "root_node_id", "")
    root_node = next((node for node in nodes if node.node_id == root_id), None)
    initial = next((node for node in nodes if node.selected_for_stage_c), None)
    first = _first_generation_winner(first_tournament)
    current = _current_winner(mutation_tournament)
    first_path = _latest_path(root, run_id, "substrate-tournament-result-*.json")
    mutation_path = _latest_path(root, run_id, "mutation-tournament-result-*.json")
    entries = [
        CreativeSearchLineageEntry(
            lineage_index=0,
            lineage_role="root_domain",
            title=(root_node.title if root_node is not None else "run domain"),
            idea_node_id_optional=(root_node.node_id if root_node is not None else None),
            outcome_summary="Root domain or opportunity from which candidate search began.",
            publication_ready=False,
        )
    ]
    if initial is not None:
        entries.append(
            CreativeSearchLineageEntry(
                lineage_index=len(entries),
                lineage_role="initial_selected_branch",
                title=initial.title,
                idea_node_id_optional=initial.node_id,
                outcome_summary="Stage C selected branch before substrate tournaments.",
                publication_ready=False,
            )
        )
    if first is not None:
        entries.append(
            CreativeSearchLineageEntry(
                lineage_index=len(entries),
                lineage_role="first_generation_winner",
                title=first.substrate_title,
                substrate_id_optional=first.substrate_id,
                source_report_path_optional=first_path,
                score_optional=first.tournament_score,
                outcome_summary="First-generation bounded synthetic tournament winner.",
                publication_ready=False,
            )
        )
        entries.append(
            CreativeSearchLineageEntry(
                lineage_index=len(entries),
                cycle_index_optional=cycle_index,
                lineage_role="mutation_source",
                title=first.substrate_title,
                substrate_id_optional=first.substrate_id,
                source_report_path_optional=mutation_path,
                score_optional=first.tournament_score,
                outcome_summary=(
                    "Winner retained as the source for refinement, hybrid, and robustness "
                    "mutations."
                ),
                publication_ready=False,
            )
        )
    if current is not None:
        entries.append(
            CreativeSearchLineageEntry(
                lineage_index=len(entries),
                cycle_index_optional=cycle_index,
                lineage_role="second_generation_winner",
                title=current.title,
                substrate_id_optional=current.substrate_id,
                parent_substrate_ids=[mutation_tournament.original_winner_substrate_id],
                source_report_path_optional=mutation_path,
                score_optional=current.score,
                outcome_summary=mutation_tournament.tournament_outcome.replace("_", " "),
                publication_ready=False,
            )
        )
        entries.append(
            CreativeSearchLineageEntry(
                lineage_index=len(entries),
                cycle_index_optional=cycle_index,
                lineage_role="current_winner",
                title=current.title,
                substrate_id_optional=current.substrate_id,
                parent_substrate_ids=[mutation_tournament.original_winner_substrate_id],
                source_report_path_optional=mutation_path,
                score_optional=current.score,
                outcome_summary="Best current branch within the declared synthetic scoring policy.",
                publication_ready=False,
            )
        )
    return entries


def _cycle_stop_decision(
    *,
    cycle_index: int,
    config: CreativeSearchControllerConfig,
    no_improvement_cycles: int,
    no_new_mutations: bool,
    all_mutations_inconclusive: bool,
    diversity_collapsed: bool,
) -> tuple[CreativeSearchStopReason, str]:
    if cycle_index >= config.max_cycles:
        return (
            CreativeSearchStopReason.MAX_CYCLES_REACHED,
            "The configured creative-search cycle budget was reached.",
        )
    if all_mutations_inconclusive:
        return (
            CreativeSearchStopReason.ALL_MUTATIONS_INCONCLUSIVE,
            "All routed mutation branches were inconclusive within synthetic scope.",
        )
    if no_new_mutations:
        return (
            CreativeSearchStopReason.NO_NEW_MUTATIONS,
            "All selected deterministic mutation identities were already applied.",
        )
    if config.stop_if_diversity_collapses and diversity_collapsed:
        return (
            CreativeSearchStopReason.DIVERSITY_COLLAPSE,
            "Idea-space diversity collapsed while near-duplicate branches increased.",
        )
    if config.stop_if_no_improvement and no_improvement_cycles >= 2:
        return (
            CreativeSearchStopReason.NO_SCORE_IMPROVEMENT,
            "Two consecutive cycles failed to meet the minimum score improvement.",
        )
    return (
        CreativeSearchStopReason.MAX_CYCLES_REACHED,
        "Continue until the configured cycle budget unless a bounded stop rule fires.",
    )


def _idea_space_before(root: Path, run_id: str) -> IdeaSpaceDiversityReport | None:
    reports = root / "runs" / run_id / "reports"
    paths = sorted(reports.glob("idea-space-report-*.json"))
    if not paths:
        return None
    try:
        return IdeaSpaceDiversityReport.model_validate_json(paths[-1].read_text(encoding="utf-8"))
    except (OSError, ValidationError):
        return None


def _first_generation_winner(
    tournament: SubstrateTournamentResult,
) -> SubstrateTournamentEntry | None:
    return next(
        (
            entry
            for entry in tournament.entries
            if entry.substrate_id == tournament.winner_substrate_id_optional
        ),
        None,
    )


def _current_winner(tournament: MutationTournamentResult) -> MutationTournamentEntry | None:
    return next(
        (
            entry
            for entry in tournament.entries
            if entry.substrate_id == tournament.second_generation_winner_substrate_id_optional
        ),
        None,
    )


def _validate_substrate_tournament(tournament: SubstrateTournamentResult) -> None:
    if tournament.publication_ready or tournament.unsupported_claim_count:
        raise CreativeSearchError("Existing substrate tournament fails bounded safety checks.")
    if not tournament.winner_selected:
        raise CreativeSearchError("Existing substrate tournament has no selected winner.")


def _validate_mutation_tournament(tournament: MutationTournamentResult) -> None:
    if tournament.publication_ready or tournament.unsupported_claim_count:
        raise CreativeSearchError("Existing mutation tournament fails bounded safety checks.")
    if not tournament.second_generation_winner_selected:
        raise CreativeSearchError("Existing mutation tournament has no selected winner.")


def _diversity_collapsed(before: object, after: object) -> bool:
    before_score = str(getattr(before, "diversity_score", "low"))
    after_score = str(getattr(after, "diversity_score", "low"))
    before_pairs = len(getattr(before, "near_duplicate_node_pairs", []))
    after_pairs = len(getattr(after, "near_duplicate_node_pairs", []))
    return before_score != "low" and after_score == "low" and after_pairs > before_pairs


def _applied_mutation_ids(root: Path, run_id: str) -> set[str]:
    return {
        candidate.mutation_id
        for report, _ in latest_creative_mutation_reports(run_id=run_id, root=root)
        for candidate in report.candidates
    }


def _next_generation_cycle_index(
    root: Path,
    run_id: str,
    controller_cycle_index: int,
) -> int:
    previous = latest_creative_search_report(root, run_id)
    previous_cycles = previous.cycle_count if previous is not None else 1
    return max(2, previous_cycles + controller_cycle_index)


def _persist_cycle(
    *,
    cycle: CreativeSearchCycle,
    search_number: int,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PersistenceResult:
    cycle_id = f"creative-search-cycle-{search_number:04d}-{cycle.cycle_index:04d}"
    return persist_artifacts_with_commit(
        run_id=cycle.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                cycle_id,
                ArtifactType.REPORT,
                cycle,
                "json",
                _metadata("creative_search_cycle"),
            )
        ],
        action_type=ControllerActionType.CREATIVE_SEARCH_CYCLE_WRITTEN,
        commit_payload={
            "run_id": cycle.run_id,
            "search_id": cycle.search_id,
            "cycle_index": cycle.cycle_index,
            "ending_winner": cycle.ending_winner,
            "ending_score": cycle.ending_score,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )


def _persist_report(
    *,
    report: CreativeSearchControllerReport,
    search_number: int,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PersistenceResult:
    report_id = f"creative-search-controller-{search_number:04d}"
    metadata = _metadata("creative_search_controller")
    return persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report_id}-markdown",
                ArtifactType.REPORT,
                render_creative_search_markdown(report),
                "markdown",
                metadata,
                filename_stem=report_id,
            ),
        ],
        action_type=ControllerActionType.CREATIVE_SEARCH_CONTROLLER_WRITTEN,
        commit_payload={
            "run_id": report.run_id,
            "search_id": report.search_id,
            "cycle_count": report.cycle_count,
            "stop_reason": report.stop_reason.value,
            "best_current_winner": report.best_current_winner,
            "best_current_score": report.best_current_score,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )


def _metadata(stage: str) -> dict[str, object]:
    return {
        "stage": stage,
        "artifact_role": "creative_search_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }


def _next_search_number(reports: Path) -> int:
    numbers = [
        int(match.group(1))
        for path in reports.glob("creative-search-controller-*.json")
        if (match := _REPORT_RE.fullmatch(path.name))
    ]
    return max(numbers, default=0) + 1


def _latest_path(root: Path, run_id: str, pattern: str) -> str | None:
    paths = sorted(
        path
        for path in (root / "runs" / run_id / "reports").glob(pattern)
        if path.is_file() and not path.name.endswith(".meta.json")
    )
    if not paths:
        return None
    try:
        return paths[-1].relative_to(root).as_posix()
    except ValueError:
        return paths[-1].as_posix()


__all__ = [
    "CreativeSearchError",
    "CreativeSearchRunResult",
    "inspect_creative_search",
    "latest_creative_search_report",
    "render_creative_search_markdown",
    "run_creative_search",
]
