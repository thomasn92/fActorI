"""Read-only reconstruction and context-only export of the research idea tree."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.ledger import LedgerError, ResearchLedger
from factori.schemas import (
    ArtifactType,
    Candidate,
    ControllerActionType,
    CreativeMutationReport,
    FinalManuscriptRegenerationReport,
    FinalNucleus,
    GenerationMutationInspectionReport,
    IdeaEdge,
    IdeaNode,
    IdeaTree,
    IdeaTreeExportReport,
    IdeaTreeInspectionReport,
    SubstratePromotionReport,
    VarianceAugmentationReport,
)

_FINAL_REGENERATION_RE = re.compile(r"^final-manuscript-regeneration-(\d{4})\.json$")
_IDEA_TREE_EXPORT_RE = re.compile(r"^idea-tree-(\d{4})\.(?:json|md)$")
_CREATIVE_MUTATION_RE = re.compile(r"^creative-mutation-report-(\d{4})\.json$")
_GENERATION_MUTATION_RE = re.compile(
    r"^generation-mutation-application-(\d{4})\.json$"
)
_VARIANCE_APPLICATION_RE = re.compile(r"^variance-augmentation-application-(\d{4})\.json$")
_SUBSTRATE_PROMOTION_RE = re.compile(r"^substrate-promotion-(\d{4})\.json$")
_DEFERRED_BRANCH_STATUSES = {
    "BudgetDeferred",
    "DeferredRealDataCandidate",
    "InsufficientRetrievalAdequacy",
    "RequiresRealData",
}


class IdeaTreeError(RuntimeError):
    """Raised when an idea tree cannot be reconstructed or exported."""


def build_idea_tree(*, run_id: str, root: str | Path = ".") -> IdeaTree:
    """Reconstruct a deterministic idea tree from existing run artifacts."""
    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    reports = run_path / "reports"
    candidates_path = run_path / "candidates"
    if not run_path.is_dir():
        raise IdeaTreeError(f"Run directory not found for run_id={run_id}.")

    warnings: list[str] = []
    source_paths: list[str] = []
    commits = _load_commits(run_path, run_id, warnings)
    candidate_entries = _load_candidates(
        root_path=root_path,
        candidates_path=candidates_path,
        warnings=warnings,
        source_paths=source_paths,
    )
    candidates = {candidate.id: candidate for candidate, _ in candidate_entries}
    candidate_paths = {candidate.id: path for candidate, path in candidate_entries}

    report_paths = {
        "stage_a": reports / "stage-a-report.md",
        "stage_b": reports / "stage-b-report.md",
        "stage_c": reports / "stage-c-selection-report.md",
        "final_nucleus": reports / "final-nucleus.json",
        "config": reports / "llm-orchestration-config.json",
    }
    for label in ("stage_a", "stage_b", "stage_c"):
        path = report_paths[label]
        if path.is_file():
            source_paths.append(_relative_path(root_path, path))
        else:
            warnings.append(
                f"{label.replace('_', ' ').title()} report is unavailable; "
                "the tree uses candidate artifacts and ledger decisions where available."
            )

    variance_applications = _load_variance_augmentation_applications(reports, root_path, warnings)
    substrate_promotions = _load_substrate_promotion_reports(reports, root_path, warnings)
    domain = _resolve_domain(report_paths["config"], candidates, warnings)
    if domain == "unknown domain" and variance_applications:
        domain = variance_applications[-1][0].domain
        warnings.append("Run domain was recovered from variance augmentation context.")
    if report_paths["config"].is_file():
        source_paths.append(_relative_path(root_path, report_paths["config"]))

    decisions = _decision_context(commits)
    final_nucleus = _load_final_nucleus(
        root_path=root_path,
        path=report_paths["final_nucleus"],
        warnings=warnings,
        source_paths=source_paths,
    )
    final_regeneration, final_report_path = _load_final_regeneration(reports, warnings)
    if final_report_path is not None:
        source_paths.append(_relative_path(root_path, final_report_path))

    context_paths = _latest_context_paths(reports)
    for context_name, path in context_paths.items():
        if path is None:
            warnings.append(f"Optional {context_name} artifact is unavailable.")
        else:
            source_paths.append(_relative_path(root_path, path))

    root_created_at = commits[0].timestamp if commits else None
    root_refs = [
        _relative_path(root_path, path)
        for key, path in report_paths.items()
        if key in {"stage_a", "stage_b", "stage_c", "config"} and path.is_file()
    ]
    nodes: list[IdeaNode] = [
        IdeaNode(
            node_id="idea-root",
            parent_id_optional=None,
            depth=0,
            stage_origin="domain_opportunity",
            title=domain,
            domain=domain,
            status="root",
            survivor_reason_optional="root domain or opportunity for the creative search",
            artifact_refs=sorted(root_refs),
            created_at=root_created_at,
        )
    ]
    edges: list[IdeaEdge] = []
    stage_a_candidates = sorted(
        (candidate for candidate in candidates.values() if candidate.parent_candidate_id is None),
        key=lambda item: item.id,
    )
    stage_b_candidates = sorted(
        (candidate for candidate in candidates.values() if candidate.parent_candidate_id),
        key=lambda item: item.id,
    )
    if not stage_a_candidates:
        warnings.append("No Stage A candidate artifacts were found.")
    if not stage_b_candidates:
        warnings.append("No Stage B child or variant artifacts were found.")

    child_ids_by_parent: dict[str, list[str]] = {}
    for candidate in stage_b_candidates:
        if candidate.parent_candidate_id:
            child_ids_by_parent.setdefault(candidate.parent_candidate_id, []).append(candidate.id)

    final_candidate_ids = set(final_nucleus.supporting_candidate_ids if final_nucleus else [])
    if final_nucleus and final_nucleus.candidate_id:
        final_candidate_ids.add(final_nucleus.candidate_id)
    selected_for_stage_c = set(decisions["stage_c_selected_ids"])
    if not selected_for_stage_c:
        selected_for_stage_c.update(final_candidate_ids)

    for candidate in stage_a_candidates:
        status, prune_reason, survivor_reason = _stage_a_disposition(
            candidate=candidate,
            decisions=decisions,
            expanded=bool(child_ids_by_parent.get(candidate.id)),
        )
        node = _candidate_node(
            candidate=candidate,
            candidate_path=candidate_paths[candidate.id],
            root_path=root_path,
            depth=1,
            stage_origin="stage_a",
            status=status,
            prune_reason=prune_reason,
            survivor_reason=survivor_reason,
            created_at=decisions["created_at"].get(candidate.id),
            selected_for_stage_c=candidate.id in selected_for_stage_c,
            selected_for_final=(
                final_regeneration is not None and candidate.id in final_candidate_ids
            ),
        )
        nodes.append(node)
        edge_type = "candidate_to_pruned" if status == "pruned" else "root_to_candidate"
        edges.append(
            _edge(
                edges,
                source="idea-root",
                target=candidate.id,
                edge_type=edge_type,
                rationale=prune_reason or survivor_reason or "Stage A candidate generation",
            )
        )

    for candidate in stage_b_candidates:
        parent_id = candidate.parent_candidate_id
        if parent_id not in candidates:
            warnings.append(
                f"Stage B variant {candidate.id} references missing parent {parent_id}."
            )
            parent_id = "idea-root"
        status, prune_reason, survivor_reason = _stage_b_disposition(
            candidate=candidate,
            decisions=decisions,
            selected_for_stage_c=candidate.id in selected_for_stage_c,
        )
        node = _candidate_node(
            candidate=candidate,
            candidate_path=candidate_paths[candidate.id],
            root_path=root_path,
            depth=2 if parent_id != "idea-root" else 1,
            stage_origin="stage_b",
            status=status,
            prune_reason=prune_reason,
            survivor_reason=survivor_reason,
            created_at=decisions["created_at"].get(candidate.id),
            selected_for_stage_c=candidate.id in selected_for_stage_c,
            selected_for_final=(
                final_regeneration is not None and candidate.id in final_candidate_ids
            ),
            parent_id=parent_id,
        )
        nodes.append(node)
        edge_type = "variant_to_pruned" if status == "pruned" else "candidate_to_variant"
        edges.append(
            _edge(
                edges,
                source=parent_id,
                target=candidate.id,
                edge_type=edge_type,
                mutation_operator=candidate.variant_type,
                rationale=prune_reason or survivor_reason or "Stage B variant expansion",
            )
        )

    final_node_id: str | None = None
    if final_regeneration is not None:
        selected_parent = _selected_final_parent(final_nucleus, nodes)
        if selected_parent is None:
            selected_parent = "idea-root"
            warnings.append(
                "Final manuscript exists without a resolvable selected candidate; "
                "the final node is attached to the root."
            )
        final_node_id = f"idea-final-{final_regeneration.regeneration_id}"
        final_refs = [
            final_regeneration.final_manuscript_path,
            final_regeneration.final_manuscript_structured_path,
            _relative_path(root_path, final_report_path),
        ]
        if report_paths["final_nucleus"].is_file():
            final_refs.append(_relative_path(root_path, report_paths["final_nucleus"]))
        final_refs.extend(
            _relative_path(root_path, path)
            for path in context_paths.values()
            if path is not None
        )
        selected_candidate = candidates.get(selected_parent)
        final_title = _final_title(
            root_path=root_path,
            report=final_regeneration,
            reports=reports,
            domain=domain,
        )
        nodes.append(
            IdeaNode(
                node_id=final_node_id,
                parent_id_optional=selected_parent,
                depth=_node_depth(nodes, selected_parent) + 1,
                stage_origin="final_manuscript_regeneration",
                title=final_title,
                domain=domain,
                method_optional=selected_candidate.method if selected_candidate else None,
                research_question_optional=(
                    selected_candidate.question if selected_candidate else None
                ),
                hypothesis_optional=(
                    selected_candidate.hypothesis if selected_candidate else None
                ),
                model_hint_optional=selected_candidate.theory if selected_candidate else None,
                experiment_hint_optional=(
                    selected_candidate.experiment if selected_candidate else None
                ),
                baseline_hint_optional=(
                    selected_candidate.baseline if selected_candidate else None
                ),
                data_regime_optional=(
                    selected_candidate.data_requirement.value if selected_candidate else None
                ),
                novelty_risk_optional=(
                    selected_candidate.literature.novelty_risk
                    if selected_candidate
                    else None
                ),
                scientific_interest_optional=None,
                status="final",
                survivor_reason_optional=(
                    final_nucleus.reason
                    if final_nucleus
                    else "preferred final manuscript regeneration"
                ),
                selected_for_stage_c=False,
                selected_for_final_manuscript=True,
                artifact_refs=sorted(set(final_refs)),
                created_at=decisions["final_selected_at"],
            )
        )
        edges.append(
            _edge(
                edges,
                source=selected_parent,
                target=final_node_id,
                edge_type="selected_to_final",
                rationale=(
                    final_nucleus.reason
                    if final_nucleus
                    else "selected branch regenerated as the final manuscript"
                ),
            )
        )
    else:
        warnings.append(
            "No final manuscript regeneration artifact is available; "
            "final_node_id_optional is null."
        )

    _append_creative_mutation_nodes(
        root_path=root_path,
        reports=reports,
        run_id=run_id,
        nodes=nodes,
        edges=edges,
        source_paths=source_paths,
        warnings=warnings,
        default_parent=final_node_id or "idea-root",
        domain=domain,
    )
    _append_generation_mutation_nodes(
        root_path=root_path,
        reports=reports,
        nodes=nodes,
        edges=edges,
        source_paths=source_paths,
        warnings=warnings,
        default_parent=final_node_id or "idea-root",
        domain=domain,
    )
    _append_variance_augmentation_nodes(
        root_path=root_path,
        reports_and_paths=variance_applications,
        nodes=nodes,
        edges=edges,
        source_paths=source_paths,
        domain=domain,
    )
    _link_promoted_substrates(
        root_path=root_path,
        reports_and_paths=substrate_promotions,
        nodes=nodes,
        source_paths=source_paths,
        warnings=warnings,
    )

    if candidates:
        warnings.append(
            "Candidate artifacts do not record a normalized scientific-interest score; "
            "scientific_interest_optional remains null."
        )
    return IdeaTree(
        run_id=run_id,
        root_node_id="idea-root",
        final_node_id_optional=final_node_id,
        nodes=nodes,
        edges=edges,
        source_artifact_paths=sorted(set(source_paths)),
        warnings=_deduplicate(warnings),
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
        publication_ready=False,
    )


def inspect_idea_tree(*, run_id: str, root: str | Path = ".") -> IdeaTreeInspectionReport:
    """Build and summarize an idea tree without writing run artifacts."""
    tree = build_idea_tree(run_id=run_id, root=root)
    return IdeaTreeInspectionReport(
        run_id=run_id,
        tree_present=bool(tree.nodes),
        node_count=len(tree.nodes),
        edge_count=len(tree.edges),
        root_node_id=tree.root_node_id,
        final_node_id_optional=tree.final_node_id_optional,
        stage_a_node_count=sum(node.stage_origin == "stage_a" for node in tree.nodes),
        stage_b_node_count=sum(node.stage_origin == "stage_b" for node in tree.nodes),
        stage_c_selected_count=sum(node.selected_for_stage_c for node in tree.nodes),
        pruned_node_count=sum(node.status == "pruned" for node in tree.nodes),
        surviving_node_count=sum(
            node.status in {"expanded", "survived", "selected", "final"}
            for node in tree.nodes
        ),
        warnings=tree.warnings,
        nodes=tree.nodes,
        edges=tree.edges,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
        publication_ready=False,
    )


def export_idea_tree(
    *,
    run_id: str,
    export_format: str,
    root: str | Path = ".",
) -> IdeaTreeExportReport:
    """Write one append-only context-only Markdown or JSON idea-tree export."""
    if export_format not in {"markdown", "json"}:
        raise IdeaTreeError("Idea-tree export format must be markdown or json.")
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    if not reports.is_dir():
        raise IdeaTreeError(f"Reports directory not found for run_id={run_id}.")
    inspection = inspect_idea_tree(run_id=run_id, root=root_path)
    number = _next_export_number(reports)
    export_id = f"idea-tree-{number:04d}"
    store = ArtifactStore(root_path)
    metadata = {
        "stage": "idea_tree_export",
        "format": export_format,
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }
    if export_format == "json":
        artifact = store.write_json(
            run_id=run_id,
            artifact_id=export_id,
            artifact_type=ArtifactType.REPORT,
            data=inspection,
            metadata=metadata,
        )
    else:
        artifact = store.write_markdown(
            run_id=run_id,
            artifact_id=export_id,
            artifact_type=ArtifactType.REPORT,
            markdown=render_idea_tree_markdown(inspection),
            metadata=metadata,
        )
    return IdeaTreeExportReport(
        run_id=run_id,
        export_id=export_id,
        export_format=export_format,
        export_path=artifact.path,
        node_count=inspection.node_count,
        edge_count=inspection.edge_count,
        warning_count=len(inspection.warnings),
        content_hash=artifact.content_hash,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
        publication_ready=False,
    )


def render_idea_tree_text(report: IdeaTreeInspectionReport) -> str:
    """Render the compact human-readable tree used by inspect and Markdown export."""
    by_parent: dict[str, list[IdeaNode]] = {}
    for node in report.nodes:
        if node.parent_id_optional is not None and node.status != "final":
            by_parent.setdefault(node.parent_id_optional, []).append(node)
    for children in by_parent.values():
        children.sort(key=lambda item: item.node_id)
    root = next(node for node in report.nodes if node.node_id == report.root_node_id)
    lines = [f"Root: {root.title}"]
    root_children = by_parent.get(root.node_id, [])
    stage_a = [node for node in root_children if node.stage_origin != "opportunity_seed"]
    opportunity_seeds = [node for node in root_children if node.stage_origin == "opportunity_seed"]
    has_final = report.final_node_id_optional is not None
    for candidate_index, candidate in enumerate(stage_a, start=1):
        candidate_last = candidate_index == len(stage_a) and not has_final
        candidate_branch = "└──" if candidate_last else "├──"
        lines.append(
            f"{candidate_branch} Candidate {candidate_index}: {candidate.title} "
            f"[{candidate.status}]"
        )
        variants = by_parent.get(candidate.node_id, [])
        prefix = "    " if candidate_last else "│   "
        for variant_index, variant in enumerate(variants, start=1):
            variant_branch = "└──" if variant_index == len(variants) else "├──"
            lines.append(
                f"{prefix}{variant_branch} Variant {candidate_index}.{variant_index}: "
                f"{variant.title} [{variant.status}]"
            )
    if opportunity_seeds:
        lines.append("├── Opportunity-seeded branches:")
        for seed_index, seed in enumerate(opportunity_seeds, start=1):
            seed_children = by_parent.get(seed.node_id, [])
            seed_branch = "└──" if seed_index == len(opportunity_seeds) else "├──"
            lines.append(f"│   {seed_branch} {seed.method_optional}: {len(seed_children)} branches")
            seed_prefix = "        " if seed_index == len(opportunity_seeds) else "│   │   "
            for child_index, child in enumerate(seed_children, start=1):
                child_branch = "└──" if child_index == len(seed_children) else "├──"
                substrate_label = (
                    " [substrate-linked]" if child.scientific_substrate_ids else ""
                )
                lines.append(
                    f"{seed_prefix}{child_branch} {child.title} "
                    f"[{child.status}]{substrate_label}"
                )
    final_node = next(
        (node for node in report.nodes if node.node_id == report.final_node_id_optional),
        None,
    )
    if final_node is not None:
        lines.append(f"└── Final selected branch: {final_node.title}")
        mutation_children = by_parent.get(final_node.node_id, [])
        for mutation_index, mutation in enumerate(mutation_children, start=1):
            mutation_branch = "└──" if mutation_index == len(mutation_children) else "├──"
            lines.append(
                f"    {mutation_branch} Mutation {mutation_index}: "
                f"{mutation.title} [{mutation.status}]"
            )
    return "\n".join(lines)


def _append_creative_mutation_nodes(
    *,
    root_path: Path,
    reports: Path,
    run_id: str,
    nodes: list[IdeaNode],
    edges: list[IdeaEdge],
    source_paths: list[str],
    warnings: list[str],
    default_parent: str,
    domain: str,
) -> None:
    node_ids = {node.node_id for node in nodes}
    reports_and_paths = _load_creative_mutation_reports(reports, root_path, warnings)
    if not reports_and_paths:
        return
    for report, report_path in reports_and_paths:
        source_paths.append(_relative_path(root_path, report_path))
        source_paths.extend(report.scientific_substrate_paths)
        for candidate in report.candidates:
            parent = next(
                (
                    source_id
                    for source_id in candidate.source_idea_node_ids
                    if source_id in node_ids
                ),
                default_parent,
            )
            node = IdeaNode(
                node_id=candidate.mutation_id,
                parent_id_optional=parent,
                depth=_node_depth(nodes, parent) + 1,
                stage_origin="creative_mutation",
                title=candidate.title,
                domain=candidate.domain or domain,
                method_optional=candidate.model_object,
                research_question_optional=candidate.research_question,
                hypothesis_optional=candidate.expected_result_pattern,
                model_hint_optional="; ".join(candidate.equations),
                experiment_hint_optional=candidate.experiment_design,
                baseline_hint_optional=candidate.baseline,
                data_regime_optional="SyntheticOnly",
                novelty_risk_optional=None,
                scientific_interest_optional=None,
                status="selected" if candidate.selected_for_substrate_build else "generated",
                survivor_reason_optional=candidate.why_scientifically_distinct,
                selected_for_stage_c=False,
                selected_for_final_manuscript=False,
                artifact_refs=sorted(
                    {
                        _relative_path(root_path, report_path),
                        *report.scientific_substrate_paths,
                    }
                ),
                created_at=None,
            )
            nodes.append(node)
            node_ids.add(node.node_id)
            edges.append(
                _edge(
                    edges,
                    source=parent,
                    target=node.node_id,
                    edge_type=_creative_edge_type(candidate.operator.value),
                    mutation_operator=candidate.operator.value,
                    rationale=candidate.why_scientifically_distinct,
                )
            )


def _load_creative_mutation_reports(
    reports: Path,
    root_path: Path,
    warnings: list[str],
) -> list[tuple[CreativeMutationReport, Path]]:
    loaded: list[tuple[CreativeMutationReport, Path]] = []
    for _, path in sorted(
        (int(match.group(1)), path)
        for path in reports.glob("creative-mutation-report-*.json")
        if (match := _CREATIVE_MUTATION_RE.fullmatch(path.name))
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
        except (OSError, ValueError):
            warnings.append(
                "Creative mutation report could not be parsed: "
                f"{_relative_path(root_path, path)}"
            )
    return loaded


def _creative_edge_type(operator: str) -> str:
    if operator == "winner_loser_hybrid":
        return "winner_loser_to_hybrid"
    if operator == "robustness_stress_test":
        return "winner_to_robustness"
    if operator == "missing_axis_injection":
        return "missing_axis_to_candidate"
    return "winner_to_refinement"


def _append_generation_mutation_nodes(
    *,
    root_path: Path,
    reports: Path,
    nodes: list[IdeaNode],
    edges: list[IdeaEdge],
    source_paths: list[str],
    warnings: list[str],
    default_parent: str,
    domain: str,
) -> None:
    node_ids = {node.node_id for node in nodes}
    loaded = _load_generation_mutation_reports(reports, root_path, warnings)
    for report, report_path in loaded:
        source_paths.append(_relative_path(root_path, report_path))
        source_paths.extend(report.scientific_substrate_paths)
        plan = report.latest_plan_optional
        if plan is None:
            continue
        applied_ids = set(report.applied_mutation_ids)
        for candidate in plan.candidates:
            if candidate.mutation_id not in applied_ids or candidate.mutation_id in node_ids:
                continue
            parent = next(
                (
                    source_id
                    for source_id in candidate.source_idea_node_ids
                    if source_id in node_ids
                ),
                default_parent,
            )
            nodes.append(
                IdeaNode(
                    node_id=candidate.mutation_id,
                    parent_id_optional=parent,
                    depth=_node_depth(nodes, parent) + 1,
                    stage_origin="generation_mutation",
                    title=candidate.title,
                    domain=candidate.domain or domain,
                    method_optional=candidate.model_object,
                    research_question_optional=candidate.research_question,
                    hypothesis_optional=candidate.expected_result_pattern,
                    model_hint_optional="; ".join(candidate.equations),
                    experiment_hint_optional=candidate.experiment_design,
                    baseline_hint_optional=candidate.baseline,
                    data_regime_optional="SyntheticOnly",
                    status=(
                        "selected" if candidate.selected_for_substrate_build else "generated"
                    ),
                    survivor_reason_optional=candidate.why_scientifically_distinct,
                    selected_for_stage_c=False,
                    selected_for_final_manuscript=False,
                    artifact_refs=sorted(
                        {
                            _relative_path(root_path, report_path),
                            *report.scientific_substrate_paths,
                        }
                    ),
                    created_at=None,
                )
            )
            node_ids.add(candidate.mutation_id)
            edges.append(
                _edge(
                    edges,
                    source=parent,
                    target=candidate.mutation_id,
                    edge_type=_generation_mutation_edge_type(candidate.operator.value),
                    mutation_operator=candidate.operator.value,
                    rationale=candidate.why_scientifically_distinct,
                )
            )


def _generation_mutation_edge_type(operator: str) -> str:
    if operator in {"robustness_refinement", "adversarial_boundary_stress"}:
        return "winner_to_robustness"
    if operator in {
        "robustness_parsimony_hybrid",
        "robustness_representation_hybrid",
    }:
        return "winner_loser_to_hybrid"
    if operator == "negative_control":
        return "winner_to_refinement"
    return "missing_axis_to_candidate"


def _load_generation_mutation_reports(
    reports: Path,
    root_path: Path,
    warnings: list[str],
) -> list[tuple[GenerationMutationInspectionReport, Path]]:
    loaded: list[tuple[GenerationMutationInspectionReport, Path]] = []
    for _, path in sorted(
        (int(match.group(1)), path)
        for path in reports.glob("generation-mutation-application-*.json")
        if (match := _GENERATION_MUTATION_RE.fullmatch(path.name))
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
        except (OSError, ValueError):
            warnings.append(
                "Generation mutation report could not be parsed: "
                f"{_relative_path(root_path, path)}"
            )
    return loaded


def _load_variance_augmentation_applications(
    reports: Path,
    root_path: Path,
    warnings: list[str],
) -> list[tuple[VarianceAugmentationReport, Path]]:
    loaded: list[tuple[VarianceAugmentationReport, Path]] = []
    for _, path in sorted(
        (int(match.group(1)), path)
        for path in reports.glob("variance-augmentation-application-*.json")
        if (match := _VARIANCE_APPLICATION_RE.fullmatch(path.name))
    ):
        try:
            report = VarianceAugmentationReport.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            warnings.append(
                "Variance augmentation application could not be parsed: "
                f"{_relative_path(root_path, path)}"
            )
            continue
        if report.applied_to_idea_tree:
            loaded.append((report, path))
    return loaded


def _append_variance_augmentation_nodes(
    *,
    root_path: Path,
    reports_and_paths: list[tuple[VarianceAugmentationReport, Path]],
    nodes: list[IdeaNode],
    edges: list[IdeaEdge],
    source_paths: list[str],
    domain: str,
) -> None:
    node_ids = {node.node_id for node in nodes}
    for report, report_path in reports_and_paths:
        report_ref = _relative_path(root_path, report_path)
        source_paths.extend(
            path
            for path in (
                report_ref,
                report.source_opportunity_discovery_path,
                report.source_augmentation_report_path_optional,
            )
            if path
        )
        applied_ids = set(report.applied_candidate_ids)
        selected = [
            candidate for candidate in report.candidates if candidate.candidate_id in applied_ids
        ]
        for method_id in sorted({candidate.source_method_lens_id for candidate in selected}):
            method_candidates = [
                candidate for candidate in selected if candidate.source_method_lens_id == method_id
            ]
            seed = method_candidates[0]
            seed_node_id = f"idea-{seed.source_seed_id}"
            if seed_node_id not in node_ids:
                nodes.append(
                    IdeaNode(
                        node_id=seed_node_id,
                        parent_id_optional="idea-root",
                        depth=1,
                        stage_origin="opportunity_seed",
                        title=f"{seed.method_lens} opportunity seed",
                        domain=seed.domain or domain,
                        method_optional=seed.method_lens,
                        status="expanded",
                        survivor_reason_optional=(
                            "Promoted by Stage 0 and expanded by deterministic "
                            "variance augmentation."
                        ),
                        source_opportunity_id_optional=seed.source_opportunity_id,
                        source_method_lens_id_optional=seed.source_method_lens_id,
                        artifact_refs=sorted(
                            {
                                report_ref,
                                report.source_opportunity_discovery_path,
                            }
                        ),
                    )
                )
                node_ids.add(seed_node_id)
                edges.append(
                    _edge(
                        edges,
                        source="idea-root",
                        target=seed_node_id,
                        edge_type="root_to_opportunity_seed",
                        rationale="Stage 0 promoted domain-method opportunity seed.",
                    )
                )
            for candidate in method_candidates:
                if candidate.candidate_id in node_ids:
                    continue
                nodes.append(
                    IdeaNode(
                        node_id=candidate.candidate_id,
                        parent_id_optional=seed_node_id,
                        depth=2,
                        stage_origin="variance_augmentation",
                        title=candidate.title,
                        domain=candidate.domain,
                        method_optional=candidate.method_lens,
                        research_question_optional=candidate.research_question,
                        hypothesis_optional=candidate.hypothesis,
                        model_hint_optional=candidate.model_hint,
                        experiment_hint_optional=candidate.experiment_or_proof_plan,
                        baseline_hint_optional=candidate.baseline,
                        data_regime_optional=candidate.data_regime,
                        novelty_risk_optional=candidate.novelty_risk,
                        scientific_interest_optional=candidate.scientific_interest_score,
                        status="generated",
                        survivor_reason_optional=(
                            "Selected by opportunity-seeded coverage and diversity policy."
                        ),
                        source_opportunity_id_optional=candidate.source_opportunity_id,
                        source_method_lens_id_optional=candidate.source_method_lens_id,
                        artifact_refs=[report_ref],
                    )
                )
                node_ids.add(candidate.candidate_id)
                edges.append(
                    _edge(
                        edges,
                        source=seed_node_id,
                        target=candidate.candidate_id,
                        edge_type="opportunity_seed_to_candidate",
                        mutation_operator=candidate.variant_family,
                        rationale=(
                            "Deterministic opportunity-seeded variation across question, "
                            "hypothesis, theory object, baseline, and verification path."
                        ),
                    )
                )


def _load_substrate_promotion_reports(
    reports: Path,
    root_path: Path,
    warnings: list[str],
) -> list[tuple[SubstratePromotionReport, Path]]:
    loaded: list[tuple[SubstratePromotionReport, Path]] = []
    for _, path in sorted(
        (int(match.group(1)), path)
        for path in reports.glob("substrate-promotion-*.json")
        if (match := _SUBSTRATE_PROMOTION_RE.fullmatch(path.name))
    ):
        try:
            loaded.append(
                (
                    SubstratePromotionReport.model_validate_json(
                        path.read_text(encoding="utf-8")
                    ),
                    path,
                )
            )
        except (OSError, ValueError):
            warnings.append(
                "Substrate promotion report could not be parsed: "
                f"{_relative_path(root_path, path)}"
            )
    return loaded


def _link_promoted_substrates(
    *,
    root_path: Path,
    reports_and_paths: list[tuple[SubstratePromotionReport, Path]],
    nodes: list[IdeaNode],
    source_paths: list[str],
    warnings: list[str],
) -> None:
    node_index = {node.node_id: index for index, node in enumerate(nodes)}
    for report, report_path in reports_and_paths:
        report_ref = _relative_path(root_path, report_path)
        source_paths.extend(
            [
                report_ref,
                report.scientific_substrate_build_report_path,
                *report.created_substrate_paths,
            ]
        )
        for decision in report.decisions:
            if not decision.promoted or not decision.created_substrate_id_optional:
                continue
            index = node_index.get(decision.candidate_id)
            if index is None:
                warnings.append(
                    "Promoted variance candidate is absent from IdeaTree: "
                    f"{decision.candidate_id}."
                )
                continue
            node = nodes[index]
            substrate_path = decision.created_substrate_path_optional
            nodes[index] = node.model_copy(
                update={
                    "scientific_substrate_ids": sorted(
                        {
                            *node.scientific_substrate_ids,
                            decision.created_substrate_id_optional,
                        }
                    ),
                    "scientific_substrate_paths": sorted(
                        {
                            *node.scientific_substrate_paths,
                            *([substrate_path] if substrate_path else []),
                        }
                    ),
                    "artifact_refs": sorted(
                        {
                            *node.artifact_refs,
                            report_ref,
                            *([substrate_path] if substrate_path else []),
                        }
                    ),
                }
            )


def render_idea_tree_markdown(report: IdeaTreeInspectionReport) -> str:
    """Render a context-only Markdown idea-tree export."""
    warnings = "\n".join(f"- {warning}" for warning in report.warnings) or "- none"
    return (
        "# Idea Tree\n\n"
        f"Run: `{report.run_id}`\n\n"
        "```text\n"
        f"{render_idea_tree_text(report)}\n"
        "```\n\n"
        "## Summary\n\n"
        f"- Nodes: {report.node_count}\n"
        f"- Edges: {report.edge_count}\n"
        f"- Stage A candidates: {report.stage_a_node_count}\n"
        f"- Stage B variants: {report.stage_b_node_count}\n"
        f"- Stage C selected branches: {report.stage_c_selected_count}\n"
        f"- Pruned nodes: {report.pruned_node_count}\n"
        f"- Surviving nodes: {report.surviving_node_count}\n"
        "- publication_ready=false\n\n"
        "## Reconstruction Warnings\n\n"
        f"{warnings}\n\n"
        "This export is provenance/context only. It is not verification evidence, does not "
        "create scientific validation, does not upgrade labels, and does not imply publication "
        "readiness.\n"
    )


def _load_commits(run_path: Path, run_id: str, warnings: list[str]) -> list[Any]:
    ledger_path = run_path / "ledger.sqlite"
    if not ledger_path.is_file():
        warnings.append("Ledger is unavailable; exact decision timestamps and reasons may be null.")
        return []
    try:
        ledger = ResearchLedger(ledger_path)
        return ledger.list_commits(run_id)
    except (LedgerError, OSError, ValueError) as exc:
        warnings.append(f"Ledger decisions could not be read: {type(exc).__name__}.")
        return []


def _load_candidates(
    *,
    root_path: Path,
    candidates_path: Path,
    warnings: list[str],
    source_paths: list[str],
) -> list[tuple[Candidate, Path]]:
    entries: list[tuple[Candidate, Path]] = []
    if not candidates_path.is_dir():
        return entries
    for path in sorted(candidates_path.glob("*.json")):
        if path.name.endswith(".meta.json"):
            continue
        try:
            candidate = Candidate.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            warnings.append(
                "Candidate artifact could not be parsed: "
                f"{_relative_path(root_path, path)}"
            )
            continue
        entries.append((candidate, path))
        source_paths.append(_relative_path(root_path, path))
    return entries


def _decision_context(commits: list[Any]) -> dict[str, Any]:
    context: dict[str, Any] = {
        "created_at": {},
        "stage_a_survivors": set(),
        "stage_b_survivors": set(),
        "stage_a_pruned": {},
        "stage_a_deferred": {},
        "stage_b_pruned": {},
        "stage_c_status": {},
        "stage_c_selected_ids": set(),
        "final_selected_at": None,
    }
    for commit in commits:
        action = commit.action_type
        candidate_id = commit.candidate_id
        if action in {
            ControllerActionType.STAGE_A_CANDIDATE_GENERATED,
            ControllerActionType.STAGE_B_CHILD_GENERATED,
        } and candidate_id:
            context["created_at"][candidate_id] = commit.timestamp
        elif action == ControllerActionType.STAGE_A_SURVIVORS_SELECTED:
            context["stage_a_survivors"] = set(commit.payload.get("survivor_ids", []))
        elif action == ControllerActionType.STAGE_B_SURVIVORS_SELECTED:
            context["stage_b_survivors"] = set(commit.payload.get("survivor_ids", []))
        elif action == ControllerActionType.STAGE_A_DUPLICATE_PRUNED and candidate_id:
            duplicate_of = commit.payload.get("duplicate_of")
            context["stage_a_pruned"][candidate_id] = (
                f"duplicate of {duplicate_of}" if duplicate_of else "Stage A duplicate pruning"
            )
        elif action == ControllerActionType.STAGE_A_GATE_PRUNED and candidate_id:
            context["stage_a_pruned"][candidate_id] = _status_reason(
                commit.payload, "Stage A gate pruning"
            )
        elif action == ControllerActionType.STAGE_A_DATA_GATE_DEFERRED and candidate_id:
            context["stage_a_deferred"][candidate_id] = _status_reason(
                commit.payload, "Stage A data gate deferral"
            )
        elif action == ControllerActionType.STAGE_B_GATE_PRUNED and candidate_id:
            context["stage_b_pruned"][candidate_id] = _status_reason(
                commit.payload, "Stage B gate pruning"
            )
        elif action == ControllerActionType.STAGE_C_SELECTION_DECIDED and candidate_id:
            context["stage_c_status"][candidate_id] = commit.payload
        elif action == ControllerActionType.FINAL_NUCLEUS_SELECTED:
            context["final_selected_at"] = commit.timestamp
    context["stage_c_selected_ids"] = {
        candidate_id
        for candidate_id, payload in context["stage_c_status"].items()
        if payload.get("to_status") == "StageCReady"
    }
    return context


def _stage_a_disposition(
    *,
    candidate: Candidate,
    decisions: dict[str, Any],
    expanded: bool,
) -> tuple[str, str | None, str | None]:
    if candidate.id in decisions["stage_a_deferred"]:
        return "deferred", decisions["stage_a_deferred"][candidate.id], None
    if candidate.status.value in _DEFERRED_BRANCH_STATUSES:
        return "deferred", f"candidate status is {candidate.status.value}", None
    if candidate.id in decisions["stage_a_pruned"]:
        return "pruned", decisions["stage_a_pruned"][candidate.id], None
    if candidate.id in decisions["stage_a_survivors"]:
        return (
            "expanded" if expanded else "survived",
            None,
            "selected as a Stage A survivor for Stage B expansion",
        )
    return "pruned", "not selected within the Stage A survivor set", None


def _stage_b_disposition(
    *,
    candidate: Candidate,
    decisions: dict[str, Any],
    selected_for_stage_c: bool,
) -> tuple[str, str | None, str | None]:
    if selected_for_stage_c:
        return "selected", None, "selected as Stage C-ready"
    stage_c = decisions["stage_c_status"].get(candidate.id)
    if stage_c and stage_c.get("to_status") in _DEFERRED_BRANCH_STATUSES:
        return "deferred", _status_reason(stage_c, "Stage C deferral"), None
    if candidate.id in decisions["stage_b_pruned"]:
        return "pruned", decisions["stage_b_pruned"][candidate.id], None
    if candidate.id in decisions["stage_b_survivors"]:
        return "survived", None, "selected as a Stage B survivor"
    return "pruned", "not selected within the Stage B survivor set", None


def _candidate_node(
    *,
    candidate: Candidate,
    candidate_path: Path,
    root_path: Path,
    depth: int,
    stage_origin: str,
    status: str,
    prune_reason: str | None,
    survivor_reason: str | None,
    created_at: str | None,
    selected_for_stage_c: bool,
    selected_for_final: bool,
    parent_id: str = "idea-root",
) -> IdeaNode:
    return IdeaNode(
        node_id=candidate.id,
        parent_id_optional=parent_id,
        depth=depth,
        stage_origin=stage_origin,
        title=candidate.question,
        domain=candidate.domain or candidate.constraints.domain or "unknown domain",
        method_optional=candidate.method,
        research_question_optional=candidate.question,
        hypothesis_optional=candidate.hypothesis,
        model_hint_optional=candidate.theory,
        experiment_hint_optional=candidate.experiment,
        baseline_hint_optional=candidate.baseline,
        data_regime_optional=candidate.data_requirement.value,
        novelty_risk_optional=candidate.literature.novelty_risk,
        scientific_interest_optional=None,
        status=status,
        prune_reason_optional=prune_reason,
        survivor_reason_optional=survivor_reason,
        selected_for_stage_c=selected_for_stage_c,
        selected_for_final_manuscript=selected_for_final,
        artifact_refs=[_relative_path(root_path, candidate_path)],
        created_at=created_at,
    )


def _edge(
    edges: list[IdeaEdge],
    *,
    source: str,
    target: str,
    edge_type: str,
    rationale: str,
    mutation_operator: str | None = None,
) -> IdeaEdge:
    return IdeaEdge(
        edge_id=f"idea-edge-{len(edges) + 1:04d}",
        source_node_id=source,
        target_node_id=target,
        edge_type=edge_type,
        mutation_operator_optional=mutation_operator,
        rationale=rationale,
    )


def _load_final_nucleus(
    *,
    root_path: Path,
    path: Path,
    warnings: list[str],
    source_paths: list[str],
) -> FinalNucleus | None:
    if not path.is_file():
        warnings.append(
            "Final nucleus artifact is unavailable; final branch selection may be null."
        )
        return None
    try:
        nucleus = FinalNucleus.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        warnings.append("Final nucleus artifact is corrupt or incompatible.")
        return None
    source_paths.append(_relative_path(root_path, path))
    return nucleus


def _load_final_regeneration(
    reports: Path,
    warnings: list[str],
) -> tuple[FinalManuscriptRegenerationReport | None, Path | None]:
    paths = sorted(
        (
            (int(match.group(1)), path)
            for path in reports.glob("final-manuscript-regeneration-*.json")
            if (match := _FINAL_REGENERATION_RE.fullmatch(path.name))
        ),
        key=lambda item: item[0],
    )
    if not paths:
        return None, None
    path = paths[-1][1]
    try:
        return (
            FinalManuscriptRegenerationReport.model_validate_json(
                path.read_text(encoding="utf-8")
            ),
            path,
        )
    except (OSError, ValueError):
        warnings.append("Latest final manuscript regeneration report is corrupt or incompatible.")
        return None, path


def _latest_context_paths(reports: Path) -> dict[str, Path | None]:
    return {
        "autonomous loop report": _latest_numbered(
            reports, "autonomous-loop-[0-9][0-9][0-9][0-9].json"
        ),
        "gap-attempt history": _latest_numbered(
            reports, "gap-attempt-history-[0-9][0-9][0-9][0-9].json"
        ),
    }


def _latest_numbered(reports: Path, pattern: str) -> Path | None:
    paths = sorted(path for path in reports.glob(pattern) if not path.name.endswith(".meta.json"))
    return paths[-1] if paths else None


def _resolve_domain(
    config_path: Path,
    candidates: dict[str, Candidate],
    warnings: list[str],
) -> str:
    config = _read_json_dict(config_path)
    domain = config.get("domain") if config else None
    if isinstance(domain, str) and domain.strip():
        return domain.strip()
    candidate_domain = next(
        (
            candidate.domain
            for candidate in sorted(candidates.values(), key=lambda item: item.id)
            if candidate.domain
        ),
        None,
    )
    if candidate_domain:
        warnings.append(
            "Run domain was recovered from candidate artifacts because config is absent."
        )
        return candidate_domain
    warnings.append(
        "Run domain is unavailable; root title uses an explicit unknown-domain boundary."
    )
    return "unknown domain"


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import json

        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _selected_final_parent(
    final_nucleus: FinalNucleus | None,
    nodes: list[IdeaNode],
) -> str | None:
    node_ids = {node.node_id for node in nodes}
    if final_nucleus is None:
        selected = [node.node_id for node in nodes if node.selected_for_stage_c]
        return sorted(selected)[0] if selected else None
    candidates = [final_nucleus.candidate_id, *final_nucleus.supporting_candidate_ids]
    return next((candidate_id for candidate_id in candidates if candidate_id in node_ids), None)


def _final_title(
    *,
    root_path: Path,
    report: FinalManuscriptRegenerationReport,
    reports: Path,
    domain: str,
) -> str:
    manuscript_path = root_path / report.final_manuscript_path
    if manuscript_path.is_file():
        try:
            for line in manuscript_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("# "):
                    return line[2:].strip()
        except OSError:
            pass
    plan = _read_json_dict(reports / "manuscript-plan.json")
    title = plan.get("title")
    return title.strip() if isinstance(title, str) and title.strip() else domain


def _node_depth(nodes: list[IdeaNode], node_id: str) -> int:
    return next((node.depth for node in nodes if node.node_id == node_id), 0)


def _status_reason(payload: dict[str, Any], fallback: str) -> str:
    reason = payload.get("reason")
    if isinstance(reason, str) and reason:
        return reason
    to_status = payload.get("to_status")
    return f"{fallback}: {to_status}" if to_status else fallback


def _relative_path(root: Path, path: Path | None) -> str:
    if path is None:
        return ""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _next_export_number(reports: Path) -> int:
    numbers = [
        int(match.group(1))
        for path in reports.iterdir()
        if path.is_file() and (match := _IDEA_TREE_EXPORT_RE.fullmatch(path.name))
    ]
    return max(numbers, default=0) + 1


def _deduplicate(values: list[str]) -> list[str]:
    return list(dict.fromkeys(values))


__all__ = [
    "IdeaTreeError",
    "build_idea_tree",
    "export_idea_tree",
    "inspect_idea_tree",
    "render_idea_tree_markdown",
    "render_idea_tree_text",
]
