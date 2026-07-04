"""Deterministic final manuscript regeneration from scoped evidence state."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.autonomous_loop import latest_autonomous_loop_report
from factori.capability_escalation import latest_capability_escalation_report
from factori.citations import (
    build_claim_support_audit,
    repair_missing_required_citations_with_accepted_sources,
    validate_citation_usage,
)
from factori.claim_evidence import (
    build_claim_evidence_map,
    claim_evidence_summary_fields,
    latest_claim_evidence_map_path,
)
from factori.evidence_artifact_intake import (
    _load_experiment_artifacts,
    _load_proof_artifacts,
)
from factori.full_paper_generation import (
    build_reviewer_bundle_summary,
    lint_paper_bundle_summary,
    render_reviewer_bundle_summary_markdown,
)
from factori.full_paper_release import evaluate_full_paper_release
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.reports import render_full_paper_release_summary
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AutonomousLoopRunReport,
    CapabilityEscalationReport,
    CitationRegistry,
    ClaimEvidenceMap,
    ControllerActionType,
    CreativeSearchControllerReport,
    ExperimentArtifact,
    FinalManuscriptClaimSummary,
    FinalManuscriptRegenerationIndex,
    FinalManuscriptRegenerationReport,
    FinalManuscriptSection,
    FinalManuscriptStructuredDocument,
    FullPaperReleaseGateConfig,
    HumanReviewArtifact,
    MutationTournamentResult,
    ProofArtifact,
    RetrievalQualityReport,
    ScientificSubstrate,
    SubstrateTournamentResult,
)
from factori.scientific_substrate import latest_selected_scientific_substrate

_FORMAL_PROOF_TYPES = {"lean_verified", "formal_verified", "external_certificate"}
_SAFE_SECTION_PLAN = (
    "Abstract",
    "Introduction",
    "Related Work and Source Boundaries",
    "Research Question / Hypothesis",
    "Method or Model",
    "Bounded Empirical Demonstration",
    "Results Within Scope",
    "Limitations and Deferred Evidence",
    "Conclusion",
    "Appendix A: Claim-Evidence Map",
    "Appendix B: Autonomous Execution and Provenance",
)


class FinalManuscriptRegenerationError(RuntimeError):
    """Raised when deterministic final regeneration cannot complete safely."""


@dataclass(frozen=True)
class FinalManuscriptRegenerationResult:
    """Persisted final manuscript regeneration result."""

    run_id: str
    report: FinalManuscriptRegenerationReport
    index: FinalManuscriptRegenerationIndex
    structured_manuscript: FinalManuscriptStructuredDocument
    manuscript_markdown: str
    persistence: PersistenceResult
    manuscript_artifact: ArtifactRef
    report_artifact: ArtifactRef
    structured_artifact: ArtifactRef
    index_artifact: ArtifactRef


def regenerate_final_manuscript(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    backend: str = "deterministic",
    allow_external_calls: bool = False,
    creative_search_report: CreativeSearchControllerReport | None = None,
) -> FinalManuscriptRegenerationResult:
    """Regenerate and persist a coherent manuscript from the final evidence state."""
    if backend not in {"deterministic", "fake", "openai"}:
        raise FinalManuscriptRegenerationError(
            "regeneration backend must be deterministic, fake, or openai"
        )
    if backend == "openai":
        if not allow_external_calls:
            raise FinalManuscriptRegenerationError(
                "OpenAI final regeneration requires explicit external-call permission."
            )
        raise FinalManuscriptRegenerationError(
            "OpenAI final regeneration is gated but not implemented in M77."
        )

    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    reports = run_path / "reports"
    if not reports.is_dir():
        raise FinalManuscriptRegenerationError(
            f"No run reports directory found for run_id={run_id}."
        )

    number = _next_regeneration_number(reports)
    regeneration_id = f"final-manuscript-regeneration-{number:04d}"
    manuscript_id = f"final-manuscript-{number:04d}"
    structured_id = f"final-manuscript-structured-{number:04d}"
    index_id = f"final-manuscript-regeneration-index-{number:04d}"

    source_map_path = latest_claim_evidence_map_path(root_path, run_id)
    if source_map_path is None:
        raise FinalManuscriptRegenerationError(
            "No claim-evidence map found; build the final claim-evidence map first."
        )
    source_map = _read_model(source_map_path, ClaimEvidenceMap)
    registry_path = reports / "citation-registry.json"
    registry = _read_model(registry_path, CitationRegistry)
    retrieval_path = _preferred_retrieval_report_path(reports)
    retrieval = _read_model(retrieval_path, RetrievalQualityReport)
    loop, _ = latest_autonomous_loop_report(root_path, run_id)
    loop_path = _latest_loop_report_path(root_path, run_id, loop)
    escalation, _ = latest_capability_escalation_report(root_path, run_id)
    escalation_path = _latest_escalation_report_path(root_path, run_id, escalation)
    if source_map is None or registry is None or retrieval is None:
        raise FinalManuscriptRegenerationError(
            "Final regeneration requires a valid claim map, citation registry, "
            "and retrieval report."
        )
    if loop is None or loop_path is None:
        raise FinalManuscriptRegenerationError(
            "Final regeneration requires a completed autonomous loop report."
        )
    _require_safe_registry(registry)

    proofs = _load_proof_artifacts(run_id=run_id, root=root_path)
    experiments = _load_experiment_artifacts(run_id=run_id, root=root_path)
    human_review = _read_optional_model(
        reports / "human-review-artifact.json",
        HumanReviewArtifact,
    )
    domain_context = _domain_context(reports, run_id)
    selected_substrate, scientific_substrates = latest_selected_scientific_substrate(
        root_path,
        run_id,
    )
    from factori.mutation_tournament import (  # noqa: PLC0415
        latest_mutation_tournament_result,
    )
    from factori.substrate_tournament import (  # noqa: PLC0415
        latest_substrate_tournament_result,
    )

    substrate_tournament = latest_substrate_tournament_result(root_path, run_id)
    mutation_tournament = latest_mutation_tournament_result(root_path, run_id)
    if creative_search_report is None:
        from factori.creative_search import (  # noqa: PLC0415
            latest_creative_search_report,
        )

        creative_search_report = latest_creative_search_report(root_path, run_id)
    if substrate_tournament and substrate_tournament.winner_substrate_id_optional:
        tournament_selected = next(
            (
                substrate
                for substrate in scientific_substrates
                if substrate.substrate_id
                == substrate_tournament.winner_substrate_id_optional
            ),
            None,
        )
        if tournament_selected is not None:
            selected_substrate = tournament_selected
    if mutation_tournament and mutation_tournament.second_generation_winner_substrate_id_optional:
        mutation_selected = next(
            (
                substrate
                for substrate in scientific_substrates
                if substrate.substrate_id
                == mutation_tournament.second_generation_winner_substrate_id_optional
            ),
            None,
        )
        if mutation_selected is not None:
            selected_substrate = mutation_selected
    title = (
        selected_substrate.title
        if selected_substrate is not None
        else _source_title(reports, run_id, domain_context)
    )
    deferred = _deferred_gap_summary(loop, escalation)
    claim_summaries = _claim_summaries(source_map)
    sections = _build_sections(
        run_id=run_id,
        domain_context=domain_context,
        source_map=source_map,
        registry=registry,
        retrieval=retrieval,
        proofs=proofs,
        experiments=experiments,
        loop=loop,
        escalation=escalation,
        deferred=deferred,
        human_review=human_review,
        selected_substrate=selected_substrate,
        scientific_substrates=scientific_substrates,
        substrate_tournament=substrate_tournament,
        mutation_tournament=mutation_tournament,
        creative_search_report=creative_search_report,
    )
    markdown = _render_manuscript(title, sections)

    available_evidence = {
        "proof": any(_formal_proof(proof) for proof in proofs),
        "experiment": any(experiment.status == "completed" for experiment in experiments),
        "human_review": human_review is not None,
        "publication_ready": False,
    }
    claim_support = build_claim_support_audit(
        run_id=run_id,
        markdown=markdown,
        citation_registry=registry,
        available_evidence_artifacts=available_evidence,
    )
    citation_repair = repair_missing_required_citations_with_accepted_sources(
        markdown,
        claim_support,
        registry,
    )
    markdown = citation_repair.revised_markdown
    claim_support = build_claim_support_audit(
        run_id=run_id,
        markdown=markdown,
        citation_registry=registry,
        available_evidence_artifacts=available_evidence,
    )
    citation_safety = validate_citation_usage(markdown, registry)
    regenerated_map = build_claim_evidence_map(
        run_id=run_id,
        root=root_path,
        claim_support_audit=claim_support,
    )
    _require_safe_regeneration(claim_support, citation_safety, regenerated_map)
    quality = lint_paper_bundle_summary(
        run_id=run_id,
        root=root_path,
        _markdown_override=markdown,
        _claim_support_audit_override=claim_support,
        _citation_safety_override=citation_safety,
    )
    release = evaluate_full_paper_release(
        run_id=run_id,
        root=root_path,
        ledger=ledger,
        config=FullPaperReleaseGateConfig(
            run_id=run_id,
            allow_warnings=True,
            require_latex_export=False,
            require_citations=False,
            require_revision_status=False,
            write_report=False,
        ),
    )

    # Rebuild section bodies after any conservative citation repair.
    sections = _sections_from_markdown(markdown, sections)
    manuscript_path = f"runs/{run_id}/reports/{manuscript_id}.md"
    structured_path = f"runs/{run_id}/reports/{structured_id}.json"
    structured = FinalManuscriptStructuredDocument(
        run_id=run_id,
        regeneration_id=regeneration_id,
        title=title,
        sections=sections,
        claim_summaries=claim_summaries,
        accepted_citation_keys=sorted(record.citation_key for record in registry.citations),
        deferred_gap_statements=deferred["statements"],
        publication_ready=False,
    )
    source_counts = claim_evidence_summary_fields(source_map)
    report = FinalManuscriptRegenerationReport(
        run_id=run_id,
        regeneration_id=regeneration_id,
        regeneration_backend=backend,
        regeneration_status="completed",
        source_claim_evidence_map_path=_relative(source_map_path, root_path),
        source_citation_registry_path=_relative(registry_path, root_path),
        source_retrieval_report_path=_relative(retrieval_path, root_path),
        source_autonomous_loop_report_path=_relative(loop_path, root_path),
        source_capability_escalation_report_path_optional=(
            _relative(escalation_path, root_path) if escalation_path else None
        ),
        final_manuscript_path=manuscript_path,
        final_manuscript_structured_path=structured_path,
        sections_generated=len(sections),
        claim_count_total=len(source_map.links),
        supported_claim_count=int(source_counts["claim_evidence_supported_count"]),
        citation_supported_claim_count=int(source_counts["citation_supported_claim_count"]),
        proof_supported_claim_count=int(source_counts["proof_supported_claim_count"]),
        experiment_supported_claim_count=int(source_counts["experiment_supported_claim_count"]),
        human_review_linked_claim_count=int(source_counts["human_review_linked_claim_count"]),
        unsupported_claim_count=len(regenerated_map.unsupported_non_scaffold_claim_ids),
        deferred_gap_count=deferred["total"],
        deferred_proof_gap_count=deferred["proof"],
        deferred_retrieval_gap_count=deferred["retrieval"],
        deferred_experiment_gap_count=deferred["experiment"],
        claim_support_rechecked_after_regeneration=True,
        claim_evidence_map_rechecked_after_regeneration=True,
        citation_safety_rechecked_after_regeneration=True,
        quality_lint_rechecked_after_regeneration=True,
        release_rechecked_after_regeneration=True,
        publication_ready=False,
    )
    report_path = f"runs/{run_id}/reports/{regeneration_id}.json"
    index = FinalManuscriptRegenerationIndex(
        run_id=run_id,
        latest_regeneration_id=regeneration_id,
        regeneration_count=number,
        latest_regeneration_status=report.regeneration_status,
        current_preferred_manuscript=manuscript_path,
        current_preferred_structured_manuscript=structured_path,
        latest_report_path=report_path,
        publication_ready=False,
    )
    reviewer = build_reviewer_bundle_summary(
        run_id=run_id,
        root=root_path,
        claim_evidence_map=regenerated_map,
        final_manuscript_report=report,
    )
    reviewer_markdown = render_reviewer_bundle_summary_markdown(reviewer)
    metadata = {
        "stage": "final_manuscript_regeneration",
        "artifact_role": "final_manuscript_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }
    audit_metadata = {**metadata, "artifact_role": "post_regeneration_audit_context"}
    map_id = _next_claim_evidence_map_id(reports)
    reviewer_id = f"reviewer-bundle-summary-after-final-manuscript-{number:04d}"
    release_id = f"full-paper-release-report-after-final-manuscript-{number:04d}"
    claim_support_id = f"claim-support-audit-after-final-manuscript-{number:04d}"
    citation_safety_id = f"citation-safety-report-after-final-manuscript-{number:04d}"
    quality_id = f"quality-lint-after-final-manuscript-{number:04d}"
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(manuscript_id, ArtifactType.REPORT, markdown, "markdown", metadata),
            ArtifactWriteSpec(structured_id, ArtifactType.REPORT, structured, "json", metadata),
            ArtifactWriteSpec(regeneration_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{regeneration_id}-markdown",
                ArtifactType.REPORT,
                render_final_manuscript_regeneration_markdown(report),
                "markdown",
                metadata,
                filename_stem=regeneration_id,
            ),
            ArtifactWriteSpec(map_id, ArtifactType.REPORT, regenerated_map, "json", audit_metadata),
            ArtifactWriteSpec(
                claim_support_id, ArtifactType.REPORT, claim_support, "json", audit_metadata
            ),
            ArtifactWriteSpec(
                citation_safety_id, ArtifactType.REPORT, citation_safety, "json", audit_metadata
            ),
            ArtifactWriteSpec(quality_id, ArtifactType.REPORT, quality, "json", audit_metadata),
            ArtifactWriteSpec(release_id, ArtifactType.REPORT, release, "json", audit_metadata),
            ArtifactWriteSpec(
                f"{release_id}-markdown",
                ArtifactType.REPORT,
                render_full_paper_release_summary(release),
                "markdown",
                audit_metadata,
                filename_stem=release_id,
            ),
            ArtifactWriteSpec(reviewer_id, ArtifactType.REPORT, reviewer, "json", audit_metadata),
            ArtifactWriteSpec(
                f"{reviewer_id}-markdown",
                ArtifactType.REPORT,
                reviewer_markdown,
                "markdown",
                audit_metadata,
                filename_stem=reviewer_id,
            ),
            ArtifactWriteSpec(index_id, ArtifactType.REPORT, index, "json", metadata),
        ],
        action_type=ControllerActionType.FINAL_MANUSCRIPT_REGENERATED,
        commit_payload={
            "run_id": run_id,
            "regeneration_id": regeneration_id,
            "sections_generated": len(sections),
            "supported_claim_count": report.supported_claim_count,
            "unsupported_claim_count": report.unsupported_claim_count,
            "deferred_gap_count": report.deferred_gap_count,
            "claim_support_rechecked": True,
            "claim_evidence_map_rechecked": True,
            "citation_safety_rechecked": True,
            "quality_lint_rechecked": True,
            "release_rechecked": True,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return FinalManuscriptRegenerationResult(
        run_id=run_id,
        report=report,
        index=index,
        structured_manuscript=structured,
        manuscript_markdown=markdown,
        persistence=persistence,
        manuscript_artifact=by_id[manuscript_id],
        report_artifact=by_id[regeneration_id],
        structured_artifact=by_id[structured_id],
        index_artifact=by_id[index_id],
    )


def inspect_final_manuscript(*, run_id: str, root: str | Path = ".") -> dict[str, Any]:
    """Inspect the latest final manuscript regeneration without mutation."""
    root_path = Path(root)
    report, index = latest_final_manuscript_regeneration(root_path, run_id)
    if report is None or index is None:
        raise FinalManuscriptRegenerationError(
            f"No final manuscript regeneration found for run_id={run_id}."
        )
    return {
        **report.model_dump(mode="json"),
        **final_manuscript_summary_fields(report, index),
        "final_manuscript_index": index.model_dump(mode="json"),
    }


def latest_final_manuscript_regeneration(
    root: Path,
    run_id: str,
) -> tuple[FinalManuscriptRegenerationReport | None, FinalManuscriptRegenerationIndex | None]:
    """Load the latest immutable final manuscript report and index."""
    reports = root / "runs" / run_id / "reports"
    indexes = _numbered_paths(reports, "final-manuscript-regeneration-index-*.json")
    if not indexes:
        return None, None
    try:
        index = FinalManuscriptRegenerationIndex.model_validate_json(
            indexes[-1].read_text(encoding="utf-8")
        )
        report = FinalManuscriptRegenerationReport.model_validate_json(
            (root / index.latest_report_path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None, None
    return report, index


def final_manuscript_summary_fields(
    report: FinalManuscriptRegenerationReport | None,
    index: FinalManuscriptRegenerationIndex | None = None,
) -> dict[str, Any]:
    """Return stable reviewer/inspect/lint fields for final regeneration."""
    if report is None:
        return {
            "final_manuscript_present": False,
            "final_manuscript_regeneration_status": None,
            "final_manuscript_regeneration_count": 0,
            "final_manuscript_sections_generated": 0,
            "final_manuscript_supported_claim_count": 0,
            "final_manuscript_unsupported_claim_count": 0,
            "final_manuscript_deferred_gap_count": 0,
            "final_manuscript_path": None,
            "final_manuscript_structured_path": None,
        }
    return {
        "final_manuscript_present": True,
        "final_manuscript_regeneration_status": report.regeneration_status,
        "final_manuscript_regeneration_count": index.regeneration_count if index else 1,
        "final_manuscript_sections_generated": report.sections_generated,
        "final_manuscript_supported_claim_count": report.supported_claim_count,
        "final_manuscript_unsupported_claim_count": report.unsupported_claim_count,
        "final_manuscript_deferred_gap_count": report.deferred_gap_count,
        "final_manuscript_path": report.final_manuscript_path,
        "final_manuscript_structured_path": report.final_manuscript_structured_path,
    }


def render_final_manuscript_regeneration_markdown(
    report: FinalManuscriptRegenerationReport,
) -> str:
    """Render a concise non-evidence regeneration report."""
    return "\n".join(
        [
            "# Final Manuscript Regeneration Report",
            "",
            f"Run ID: `{report.run_id}`",
            f"Regeneration ID: `{report.regeneration_id}`",
            f"Status: `{report.regeneration_status}`",
            f"Sections generated: `{report.sections_generated}`",
            f"Supported claims represented: `{report.supported_claim_count}`",
            f"Unsupported claims after regeneration: `{report.unsupported_claim_count}`",
            f"Deferred gaps represented: `{report.deferred_gap_count}`",
            f"Final manuscript: `{report.final_manuscript_path}`",
            "",
            "This report is manuscript workflow context only. It does not create evidence, ",
            "scientific validation, or publication readiness.",
            "",
            "- publication_ready: false",
            "- creates_scientific_validation: false",
            "- implies_publication_readiness: false",
            "- is_verification_evidence: false",
            "",
        ]
    )


def _build_sections(
    *,
    run_id: str,
    domain_context: dict[str, str],
    source_map: ClaimEvidenceMap,
    registry: CitationRegistry,
    retrieval: RetrievalQualityReport,
    proofs: list[ProofArtifact],
    experiments: list[ExperimentArtifact],
    loop: AutonomousLoopRunReport,
    escalation: CapabilityEscalationReport | None,
    deferred: dict[str, Any],
    human_review: HumanReviewArtifact | None,
    selected_substrate: ScientificSubstrate | None = None,
    scientific_substrates: list[ScientificSubstrate] | None = None,
    substrate_tournament: SubstrateTournamentResult | None = None,
    mutation_tournament: MutationTournamentResult | None = None,
    creative_search_report: CreativeSearchControllerReport | None = None,
) -> list[FinalManuscriptSection]:
    counts = claim_evidence_summary_fields(source_map)
    formal = [proof for proof in proofs if _formal_proof(proof)]
    informal = [
        proof for proof in proofs if proof.proof_type in {"proof_plan", "informal_proof_note"}
    ]
    completed = [experiment for experiment in experiments if experiment.status == "completed"]
    substrate_experiments = [
        experiment
        for experiment in experiments
        if experiment.experiment_type
        in {
            "substrate_distance_decay_uv_local",
            "substrate_pca_low_rank_uv_local",
            "substrate_hierarchical_alpha_uv_local",
            "substrate_gravity_low_rank_hybrid_uv_local",
            "substrate_boundary_perturbation_uv_local",
        }
    ]
    completed_substrate_experiments = [
        experiment for experiment in substrate_experiments if experiment.status == "completed"
    ]
    winner_ids = _tournament_winner_experiment_ids(substrate_tournament)
    winner_types = _tournament_winner_experiment_types(substrate_tournament)
    if mutation_tournament is not None:
        winner_ids = _mutation_tournament_winner_experiment_ids(mutation_tournament)
        winner_types = _mutation_tournament_winner_experiment_types(mutation_tournament)
    winner_experiments = [
        experiment
        for experiment in completed_substrate_experiments
        if experiment.experiment_id in winner_ids
        or (not winner_ids and experiment.experiment_type in winner_types)
    ]
    displayed_experiments = winner_experiments or completed_substrate_experiments or completed
    accepted = [record for record in registry.citations if record.accepted_for_registry]
    citation_keys = sorted(record.citation_key for record in accepted)
    primary_citation = f" [@{citation_keys[0]}]" if citation_keys else ""
    domain = domain_context["domain"]
    domain_title = domain_context["domain_title"]
    method_phrase = domain_context["method_phrase"]
    research_question = domain_context["research_question"]
    hypothesis = domain_context["hypothesis"]
    substrate_context = _substrate_context(selected_substrate, scientific_substrates or [])
    if selected_substrate is not None:
        method_phrase = selected_substrate.concrete_model_object.model_type.replace("_", " ")
        research_question = (
            f"Can {selected_substrate.title} provide a bounded synthetic test for {domain}?"
        )
        hypothesis = selected_substrate.measurable_hypothesis
    citation_lines = [
        (
            f"Accepted source context for {domain} includes {record.title} "
            f"[@{record.citation_key}]."
        )
        for record in accepted
    ] or [f"No accepted registry source is asserted as complete coverage for {domain}."]
    bibliography = [entry.markdown for entry in registry.bibliography]
    appendix_proof_text = (
        "A passed formal artifact is linked to the mapped claim identifiers "
        + ", ".join(proof.proof_id for proof in formal)
        + ". Its authority is restricted to the declared checker scope; it does not establish "
        "novelty, broad correctness, or empirical validity."
        if formal
        else (
            "Proof-plan or informal proof context exists, but no passed formal artifact supports "
            "the deferred proof path. This is not a proof and the formal obligation remains open."
            if informal
            else "No passed formal proof artifact is linked; formal proof remains deferred."
        )
    )
    experiment_lines = []
    for experiment in displayed_experiments:
        if experiment.experiment_type in {
            "substrate_distance_decay_uv_local",
            "substrate_pca_low_rank_uv_local",
            "substrate_hierarchical_alpha_uv_local",
            "substrate_gravity_low_rank_hybrid_uv_local",
            "substrate_boundary_perturbation_uv_local",
        }:
            experiment_lines.extend(_substrate_experiment_metric_lines(experiment))
            continue
        metrics = ", ".join(f"`{key}={value}`" for key, value in sorted(experiment.metrics.items()))
        experiment_lines.append(
            "A completed uv-local synthetic experiment artifact "
            f"`{experiment.experiment_id}` with recorded metrics {metrics or '`none recorded`'}. "
            "The record is limited to its fixed configuration, hashes, logs, and mapped claim; "
            "it does not establish broad empirical validation."
        )
    if not experiment_lines:
        experiment_lines.append(
            "No completed experiment artifact is linked, so empirical demonstration remains a "
            "future bounded task rather than an asserted result."
        )
    for experiment in substrate_experiments:
        if experiment.status != "completed":
            experiment_lines.append(
                f"Substrate experiment `{experiment.experiment_id}` was retained as an "
                f"{experiment.status} bounded result. It does not support the positive mapped "
                "claim and does not imply real-world validation."
            )
    result_lines = [
        (
            "Within this run, the bounded result is that the configured synthetic demonstration "
            f"reported scoped metrics for {domain}; those metrics are evidence only for the mapped "
            "result claim and fixed sandbox configuration."
        )
        if completed
        else (
            "Within this run, no completed experiment artifact supports an empirical result claim; "
            "the empirical path remains future work."
        ),
        (
            "A passed formal proof artifact supports only its mapped formal claim and declared "
            "checker scope."
            if formal
            else (
                "No formal proof is claimed for the domain model; proof-related work remains "
                "deferred and is summarized in the limitations and appendix."
            )
        ),
        (
            "Accepted citations provide background and source context only. They do not verify "
            "the model, prove novelty, or establish complete literature coverage."
        ),
    ]
    if substrate_tournament is not None:
        result_lines.extend(_tournament_result_lines(substrate_tournament))
    if mutation_tournament is not None:
        result_lines.extend(_mutation_tournament_result_lines(mutation_tournament))
    if creative_search_report is not None:
        result_lines.extend(_creative_search_result_lines(creative_search_report))
    for experiment in displayed_experiments:
        if experiment.experiment_type in {
            "substrate_distance_decay_uv_local",
            "substrate_pca_low_rank_uv_local",
            "substrate_hierarchical_alpha_uv_local",
            "substrate_gravity_low_rank_hybrid_uv_local",
            "substrate_boundary_perturbation_uv_local",
        }:
            result_lines.extend(_substrate_experiment_result_lines(experiment))
    escalation_text = (
        "This workflow trace is not evidence; capability escalation ended with "
        f"`{escalation.escalation_status}` after "
        f"{escalation.proof_escalation_attempt_count} proof and "
        f"{escalation.retrieval_escalation_attempt_count} retrieval attempts."
        if escalation
        else "No capability-escalation report is present for this run."
    )
    limitation_lines = [
        (
            f"Retrieval was bounded to {retrieval.total_retrieved_sources} records; "
            f"{retrieval.accepted_source_count} were accepted and "
            f"{retrieval.rejected_source_count} were rejected by deterministic quality rules."
        ),
        "Accepted registry records provide background context only; rejected and hard-rejected "
        "records do not support manuscript claims.",
        *deferred["statements"],
        "Synthetic/local experiment records apply only to their mapped run and do not provide "
        "broad empirical validation.",
        "This manuscript does not claim novelty, complete literature coverage, broad correctness, "
        "or broad validation.",
    ]
    human_text = (
        f"A human-review artifact records status `{human_review.review_status}` as "
        "review occurrence "
        "only; it does not approve publication or validate scientific claims."
        if human_review
        else "No human-review artifact is required for this autonomous workflow path."
    )
    claim_ids = [
        link.claim_id
        for link in source_map.links
        if link.support_status in {"supported_within_scope", "not_required_scaffold"}
    ]
    bodies = {
        "Abstract": (
            f"This bounded manuscript studies {domain} through a concrete substrate: "
            f"{substrate_context['title']}. The main model object is "
            f"`{substrate_context['primary_equation']}`, with variables defined in the method "
            "section and a synthetic-only experiment design that compares the proposed method to "
            f"{substrate_context['baseline']}. The result schema is limited to "
            f"{substrate_context['metrics_text']} and supports only a mapped run-specific claim "
            f"if a completed artifact passes intake{primary_citation}. The research problem is "
            "how to express the domain tension as a measurable bounded model object, and the "
            "central contribution is the substrate and result schema rather than a validation "
            "claim. The paper does not claim novelty, broad empirical validation, broad "
            "correctness, or publication readiness."
        ),
        "Introduction": (
            f"{domain_title} creates a bounded modeling problem: claims about place, mobility, "
            "and regional structure can exceed the evidence carried by a small local run. This "
            f"paper treats {domain} as a domain-facing research setting, not as a completed "
            f"empirical validation. Accepted sources provide local background context"
            f"{primary_citation}, while the method section defines only a conservative synthetic "
            "demonstration path. The aim is to separate a research question from broader claims "
            "that would require additional proof, retrieval, or real-world empirical evidence."
        ),
        "Related Work and Source Boundaries": "\n\n".join(
            [
                *citation_lines,
                "The source registry is bounded. It supports background and source context only, "
                "not exhaustive literature coverage, proof, empirical validation, or novelty.",
                "Accepted bibliography records retained for export: " + " ".join(bibliography)
                if bibliography
                else "",
            ]
        ).strip(),
        "Research Question / Hypothesis": (
            f"Research question: {research_question}\n\n"
            f"Bounded hypothesis for this run: {hypothesis} The hypothesis is scoped to the "
            "configured local artifact path. It is not a novelty claim, not a general performance "
            "claim, and not evidence about unobserved datasets."
        ),
        "Method or Model": (
            f"The proposed model is a bounded {method_phrase} for {domain}. "
            f"Primary equation: `{substrate_context['primary_equation']}`. "
            f"Variables and notation: {substrate_context['variables_text']}. "
            f"Mechanism: {substrate_context['mechanism']} "
            f"Identifiability boundary: {substrate_context['identifiability']} "
            "The model is a scientific substrate and planning object unless an experiment "
            "artifact is completed and accepted for the mapped claim. Formal plans, retrieval "
            "expansion, and execution records are separated from scientific evidence unless a "
            "corresponding artifact passes the relevant intake rule."
        ),
        "Bounded Empirical Demonstration": "\n\n".join(
            [
                f"Substrate DGP or dataset: {substrate_context['dgp']}",
                (
                    f"Baseline: {substrate_context['baseline']}. "
                    f"Method: {substrate_context['method']}."
                ),
                (
                    f"Metrics: {substrate_context['metrics_text']}. "
                    f"Seed plan: {substrate_context['seed_plan']}"
                ),
                f"Ablation or stress test: {substrate_context['ablation']}",
                *experiment_lines,
            ]
        ),
        "Results Within Scope": "\n\n".join(
            [
                f"Result schema columns: {substrate_context['result_columns']}. "
                f"Claim supported if: {substrate_context['claim_supported_if']} "
                f"Claim not supported if: {substrate_context['claim_not_supported_if']}",
                *result_lines,
            ]
        ),
        "Limitations and Deferred Evidence": "\n\n".join(limitation_lines),
        "Conclusion": (
            f"The bounded result is a domain-facing manuscript about {domain} with a conservative "
            "research question, accepted source context, and scoped evidence language. Any "
            "completed uv-local synthetic experiment remains limited to its mapped configuration "
            "and metrics. No passed formal proof is implied for deferred proof paths, and deferred "
            "retrieval work remains visible. Stronger domain claims require new accepted evidence "
            "artifacts and the same safety checks."
        ),
        "Appendix A: Claim-Evidence Map": (
            f"The claim-evidence map contains {len(source_map.links)} claim records. These counts "
            "are audit context, not scientific validation: "
            f"{counts['citation_supported_claim_count']} are linked to accepted background "
            "citations, "
            f"{counts['proof_supported_claim_count']} to scoped proof artifacts, "
            f"{counts['experiment_supported_claim_count']} to completed experiment artifacts, and "
            f"{counts['human_review_linked_claim_count']} to review-occurrence context. "
            "Unsupported noncritical wording is omitted or represented as a boundary statement. "
            f"Evidence links do not transfer authority across claims. {appendix_proof_text} "
            f"{substrate_context['alternative_text']} "
            f"{_tournament_appendix_text(substrate_tournament)}"
            f" {_mutation_tournament_appendix_text(mutation_tournament)}"
            f" {_creative_search_appendix_text(creative_search_report)}"
        ),
        "Appendix B: Autonomous Execution and Provenance": (
            f"The autonomous loop completed {loop.iterations_completed} iteration(s) with terminal "
            f"state `{loop.terminal_state}` and stop reason `{loop.stop_reason}`. It recorded "
            f"{loop.resolved_gap_count} resolved, {loop.deferred_gap_count} deferred, and "
            f"{loop.exhausted_gap_count} exhausted workflow gaps. {escalation_text} {human_text} "
            "These records describe orchestration, packaging, and provenance only; they are not "
            "verification or scientific validation. publication_ready=false."
        ),
    }
    sections = []
    for heading in _SAFE_SECTION_PLAN:
        body = _develop_section_body(heading, bodies[heading])
        sections.append(
            FinalManuscriptSection(
                section_id=_slug(heading),
                heading=heading,
                body_markdown=body,
                word_count=_word_count(body),
                included_claim_ids=(
                    claim_ids if heading == "Appendix A: Claim-Evidence Map" else []
                ),
                citation_keys=(
                    sorted(record.citation_key for record in accepted)
                    if heading == "Related Work and Source Boundaries"
                    else []
                ),
                proof_artifact_ids=[proof.proof_id for proof in formal]
                if heading == "Appendix A: Claim-Evidence Map"
                else [],
                experiment_artifact_ids=[experiment.experiment_id for experiment in completed]
                if heading == "Bounded Empirical Demonstration"
                else [],
                deferred_gap_types=deferred["types"]
                if heading == "Limitations and Deferred Evidence"
                else [],
            )
        )
    return sections


_FINAL_SECTION_DEPTH_TARGETS = {
    "Abstract": 120,
    "Introduction": 220,
    "Related Work and Source Boundaries": 140,
    "Research Question / Hypothesis": 150,
    "Method or Model": 220,
    "Bounded Empirical Demonstration": 150,
    "Results Within Scope": 150,
    "Limitations and Deferred Evidence": 190,
    "Conclusion": 140,
    "Appendix A: Claim-Evidence Map": 160,
    "Appendix B: Autonomous Execution and Provenance": 160,
}

_FINAL_SECTION_DEVELOPMENT = {
    "Abstract": [
        """
        The manuscript is framed as a research draft rather than an audit report. It keeps the
        domain question, accepted source context, method boundary, and scoped demonstration in the
        foreground while reserving provenance details for the appendices.
        """,
        """
        Any empirical statement is confined to the recorded local configuration. Stronger claims
        about general performance, causal explanation, or comparative superiority would require
        new accepted evidence.
        """,
    ],
    "Introduction": [
        """
        Spatial and regional claims often depend on scale, boundary choice, observation density,
        and the distinction between local pattern and general explanation. A bounded manuscript can
        still be useful when it states exactly which part of that problem is being modeled and which
        parts remain outside the evidence base.
        """,
        """
        This draft therefore treats the selected domain as a setting for a conservative research
        question. The paper asks for a run-specific synthetic demonstration, not a complete account
        of the field. Source records, formal-evidence status, and experiment records constrain the
        wording rather than expanding it.
        """,
        """
        The resulting structure is meant to support later evidence extension. If future local
        retrieval packs, formal artifacts, or additional experiments are accepted, they can support
        stronger mapped statements without changing the conservative interpretation of this run.
        """,
    ],
    "Related Work and Source Boundaries": [
        """
        The related-work discussion is deliberately local to the accepted registry. It records
        context for the research question and helps bound terminology, but it does not assert that
        the literature search was exhaustive or that the paper establishes novelty.
        """,
        """
        Accepted source keys are used only where a statement needs background context. Rejected and
        hard-rejected records remain outside the manuscript bibliography, so the main body does not
        cite sources that failed the configured relevance or safety filters.
        """,
    ],
    "Research Question / Hypothesis": [
        """
        The research question is intentionally narrow. It asks whether the configured synthetic
        setup can report reproducible metrics for the mapped demonstration claim, not whether the
        method is generally correct or superior in unobserved places.
        """,
        """
        The hypothesis is likewise bounded. It can be supported only by the completed local
        experiment artifact for the target claim, and it remains silent about external datasets,
        future cases, and broader geographic explanation.
        """,
    ],
    "Method or Model": [
        """
        The model should be read as a demonstration scaffold. It fixes the local experiment bundle,
        seed policy, metrics, and target claim before any result statement is admitted. This keeps
        the method from drifting into unsupported interpretation after the demonstration runs.
        """,
        """
        Source context informs the framing but does not validate the model. Formal proof status,
        retrieval limitations, and sandbox outputs are handled as distinct support channels, so a
        statement supported by one channel cannot borrow authority from another.
        """,
    ],
    "Bounded Empirical Demonstration": [
        """
        The demonstration is a local synthetic exercise. It is useful for checking whether the
        configured machinery can produce reproducible run-specific metrics, but it does not
        establish real-world empirical validity or broad robustness.
        """,
        """
        A successful sandbox run supports only the mapped bounded result claim. Failed, timed-out,
        policy-rejected, or inconclusive runs remain non-evidence and cannot appear as completed
        empirical support.
        """,
    ],
    "Results Within Scope": [
        """
        Results are stated as observations about the recorded run. Metrics belong to the accepted
        experiment artifact, if present, and cannot be generalized to other datasets, baselines, or
        spatial contexts without additional scoped artifacts.
        """,
        """
        Formal and retrieval outcomes are reported as boundaries on interpretation. A proof plan is
        not a verified theorem, and accepted background sources do not validate empirical behavior.
        """,
    ],
    "Limitations and Deferred Evidence": [
        """
        The manuscript remains limited by the accepted source set, the local synthetic design, and
        any unresolved formal or retrieval paths. These limitations are part of the research claim,
        not bookkeeping to be discarded after the run completes.
        """,
        """
        Evidence boundary: accepted sources provide background context, completed synthetic
        experiments support only mapped run-specific claims, and formal obligations remain open
        unless a matching passed artifact is present.
        """,
        """
        Additional accepted sources could refine the related-work boundary, and future formal
        artifacts could support formal statements if their checker scope matches. Until then, those
        paths remain deferred and visible.
        """,
        """
        One completed synthetic experiment cannot support claims about broad empirical validation,
        policy transfer, or real-world performance. It reports only the fixed local configuration
        and mapped result claim.
        """,
    ],
    "Conclusion": [
        """
        The paper closes with a bounded domain-facing result: a research question, accepted source
        context, method boundary, and scoped demonstration can be stated without upgrading them into
        broad validation claims.
        """,
        """
        Remaining work is explicit. Deferred proof and retrieval paths are not resolved by the
        manuscript itself, and any stronger future statement would need an accepted citation, proof,
        or experiment link within its declared scope.
        """,
    ],
    "Appendix A: Claim-Evidence Map": [
        """
        This appendix records support scope rather than scientific validation. Citation links
        authorize bounded literature context only, proof links authorize only their declared formal
        statements, and completed experiment links authorize only their recorded runs.
        """,
        """
        Scaffold or boundary statements are included only to limit interpretation. Unsupported
        authority language is omitted or downgraded, and human-review records describe review
        occurrence rather than approval.
        """,
    ],
    "Appendix B: Autonomous Execution and Provenance": [
        """
        The execution record explains how the final manuscript was produced, why the loop stopped,
        which artifacts were included, and which gaps remained deferred. This provenance is useful
        for audit and replay, but it is not scientific evidence.
        """,
        """
        Bundle assembly, hash locking, and verification preserve reproducibility metadata. They do
        not change claim labels, create evidence, approve publication, or make deferred paths
        disappear.
        """,
        """
        The append-only ledger and numbered reports remain the source of provenance for this
        package. The manuscript body should be read as the bounded research draft; this appendix is
        the workflow record that keeps the draft auditable.
        """,
    ],
}


def _develop_section_body(heading: str, body: str) -> str:
    target = _FINAL_SECTION_DEPTH_TARGETS.get(heading)
    if target is None or _word_count(body) >= target:
        return body
    paragraphs = [body, *map(_normalize_paragraph, _FINAL_SECTION_DEVELOPMENT.get(heading, []))]
    developed = "\n\n".join(paragraphs)
    if _word_count(developed) < target:
        if heading.startswith("Appendix"):
            developed += (
                "\n\nThis appendix records provenance and support boundaries only. It changes no "
                "evidence label, source decision, verification status, or release authority."
            )
        else:
            developed += (
                "\n\nThis section remains limited to the selected domain, accepted source context, "
                "and scoped artifacts. It does not assert novelty, broad validation, or general "
                "correctness beyond the recorded run."
            )
    return developed


def _substrate_experiment_metric_lines(experiment: ExperimentArtifact) -> list[str]:
    metrics = experiment.metrics
    required = (
        "test_mae_baseline",
        "test_mae_method",
        "test_rmse_baseline",
        "test_rmse_method",
    )
    if any(name not in metrics for name in required):
        return [
            f"Substrate experiment `{experiment.experiment_id}` completed, but its comparison "
            "metrics are incomplete and no positive comparison is asserted."
        ]
    if experiment.experiment_type == "substrate_pca_low_rank_uv_local":
        lines = [
            (
                "The substrate-specific uv-local run compared the pooled gravity baseline "
                "without low-rank residual correction against the rank-k residual correction "
                "method. Recorded high latent-factor metrics were baseline MAE "
                f"`{metrics['test_mae_baseline']}`, method MAE "
                f"`{metrics['test_mae_method']}`, baseline RMSE "
                f"`{metrics['test_rmse_baseline']}`, method RMSE "
                f"`{metrics['test_rmse_method']}`, latent-factor recovery correlation "
                f"`{metrics.get('latent_factor_recovery_correlation')}`, and explained "
                f"residual variance `{metrics.get('explained_residual_variance')}`."
            )
        ]
    elif experiment.experiment_type == "substrate_hierarchical_alpha_uv_local":
        lines = [
            (
                "The second-generation hierarchical-alpha run compared pooled alpha, "
                "cluster-level alpha, and full origin-specific alpha. Recorded high-cluster "
                f"metrics were baseline MAE `{metrics['test_mae_baseline']}`, cluster-method "
                f"MAE `{metrics['test_mae_method']}`, baseline RMSE "
                f"`{metrics['test_rmse_baseline']}`, method RMSE "
                f"`{metrics['test_rmse_method']}`, and complexity-penalized score "
                f"`{metrics.get('complexity_penalized_score')}`."
            )
        ]
    elif experiment.experiment_type == "substrate_gravity_low_rank_hybrid_uv_local":
        lines = [
            (
                "The second-generation hybrid run compared the distance-decay winner against "
                "a distance-decay plus low-rank residual correction. Recorded high-residual "
                f"metrics were baseline MAE `{metrics['test_mae_baseline']}`, method MAE "
                f"`{metrics['test_mae_method']}`, baseline RMSE "
                f"`{metrics['test_rmse_baseline']}`, method RMSE "
                f"`{metrics['test_rmse_method']}`, latent-factor recovery correlation "
                f"`{metrics.get('latent_factor_recovery_correlation')}`, and explained "
                f"residual variance `{metrics.get('explained_residual_variance')}`."
            )
        ]
    elif experiment.experiment_type == "substrate_boundary_perturbation_uv_local":
        lines = [
            (
                "The second-generation boundary-perturbation run compared pooled and "
                "heterogeneous alpha under original and perturbed boundaries. Recorded "
                f"perturbed metrics were baseline MAE `{metrics['test_mae_baseline']}`, "
                f"method MAE `{metrics['test_mae_method']}`, baseline RMSE "
                f"`{metrics['test_rmse_baseline']}`, method RMSE "
                f"`{metrics['test_rmse_method']}`, robustness ratio "
                f"`{metrics.get('robustness_ratio')}`, and performance degradation "
                f"`{metrics.get('performance_degradation')}`."
            )
        ]
    else:
        lines = [
            (
                "The substrate-specific uv-local run compared the pooled-alpha baseline with the "
                "heterogeneous-alpha method. Recorded high-heterogeneity metrics were "
                f"baseline MAE `{metrics['test_mae_baseline']}`, method MAE "
                f"`{metrics['test_mae_method']}`, baseline RMSE "
                f"`{metrics['test_rmse_baseline']}`, and method RMSE "
                f"`{metrics['test_rmse_method']}`."
            )
        ]
    table = metrics.get("comparison_table")
    if isinstance(table, list) and table:
        lines.extend(
            [
                "| setting | baseline MAE | method MAE | baseline RMSE | method RMSE | "
                "MAE improvement | RMSE improvement |",
                "|---|---:|---:|---:|---:|---:|---:|",
                *[
                    "| {setting} | {baseline_mae} | {method_mae} | {baseline_rmse} | "
                    "{method_rmse} | {mae_improvement} | {rmse_improvement} |".format(
                        **row
                    )
                    for row in table
                    if isinstance(row, dict)
                    and {
                        "setting",
                        "baseline_mae",
                        "method_mae",
                        "baseline_rmse",
                        "method_rmse",
                        "mae_improvement",
                        "rmse_improvement",
                    }
                    <= row.keys()
                ],
            ]
        )
    if metrics.get("heterogeneity_ablation_present"):
        lines.append(
            _substrate_ablation_sentence(experiment.experiment_type)
        )
    return lines


def _substrate_experiment_result_lines(experiment: ExperimentArtifact) -> list[str]:
    metrics = experiment.metrics
    supported = bool(metrics.get("claim_support_satisfied"))
    if experiment.experiment_type == "substrate_pca_low_rank_uv_local":
        direction = (
            "The low-rank residual method beat the pooled gravity baseline under the declared "
            "MAE/RMSE and latent-recovery rule"
            if supported
            else "The low-rank residual method did not satisfy the declared support rule"
        )
        detail = (
            "The high latent-factor-strength MAE/RMSE improvements were "
            f"`{metrics.get('mae_improvement')}` and `{metrics.get('rmse_improvement')}`; "
            "the latent-factor recovery correlation and explained residual variance were "
            f"`{metrics.get('latent_factor_recovery_correlation')}` and "
            f"`{metrics.get('explained_residual_variance')}`."
        )
    elif experiment.experiment_type == "substrate_hierarchical_alpha_uv_local":
        direction = (
            "The cluster-level alpha method beat the pooled-alpha baseline under the declared "
            "MAE/RMSE rule"
            if supported
            else "The cluster-level alpha method did not satisfy the declared support rule"
        )
        detail = (
            "The high-cluster-heterogeneity MAE/RMSE improvements were "
            f"`{metrics.get('mae_improvement')}` and `{metrics.get('rmse_improvement')}`; "
            "the branch also records parameter counts and complexity-penalized score "
            f"`{metrics.get('complexity_penalized_score')}`."
        )
    elif experiment.experiment_type == "substrate_gravity_low_rank_hybrid_uv_local":
        direction = (
            "The gravity plus low-rank residual hybrid beat the distance-decay winner under "
            "the declared MAE/RMSE rule"
            if supported
            else "The gravity plus low-rank residual hybrid did not satisfy the declared rule"
        )
        detail = (
            "The high residual-factor-strength MAE/RMSE improvements were "
            f"`{metrics.get('mae_improvement')}` and `{metrics.get('rmse_improvement')}`; "
            "latent-factor recovery and explained residual variance were "
            f"`{metrics.get('latent_factor_recovery_correlation')}` and "
            f"`{metrics.get('explained_residual_variance')}`."
        )
    elif experiment.experiment_type == "substrate_boundary_perturbation_uv_local":
        direction = (
            "The heterogeneous-alpha advantage persisted under the declared boundary "
            "perturbation rule"
            if supported
            else "The boundary perturbation branch did not satisfy the declared support rule"
        )
        detail = (
            "The perturbed-boundary MAE/RMSE improvements were "
            f"`{metrics.get('mae_improvement')}` and `{metrics.get('rmse_improvement')}`; "
            "robustness ratio and performance degradation were "
            f"`{metrics.get('robustness_ratio')}` and "
            f"`{metrics.get('performance_degradation')}`."
        )
    else:
        direction = (
            "The method beat the pooled-alpha baseline under the declared MAE/RMSE rule"
            if supported
            else "The method did not satisfy the declared MAE/RMSE support rule"
        )
        detail = (
            "The high-heterogeneity MAE and RMSE improvements were "
            f"`{metrics.get('mae_improvement')}` and `{metrics.get('rmse_improvement')}`. "
            "The low/high heterogeneity comparison is retained as the declared ablation."
        )
    return [
        f"{direction} for the recorded synthetic settings. This is a bounded synthetic result "
        "for the mapped claim only, not evidence of performance on observed mobility data.",
        detail,
    ]


def _substrate_ablation_sentence(experiment_type: str) -> str:
    if experiment_type in {
        "substrate_pca_low_rank_uv_local",
        "substrate_gravity_low_rank_hybrid_uv_local",
    }:
        return (
            "The ablation includes low and high latent-factor-strength settings. The recorded "
            "result tests whether residual-structure advantage changes with factor strength; "
            "it remains a synthetic stress test rather than real-world evidence."
        )
    if experiment_type == "substrate_boundary_perturbation_uv_local":
        return (
            "The ablation includes original and perturbed boundary settings. The recorded "
            "result tests robustness to spatial-unit perturbation only; it remains a synthetic "
            "stress test rather than real-world evidence."
        )
    return (
        "The ablation includes low- and high-heterogeneity settings. The recorded result tests "
        "whether the alpha-heterogeneity advantage changes with alpha variation; it remains a "
        "synthetic stress test rather than real-world evidence."
    )


def _tournament_winner_experiment_types(
    tournament: SubstrateTournamentResult | None,
) -> set[str]:
    if tournament is None or not tournament.winner_substrate_id_optional:
        return set()
    winner = next(
        (
            entry
            for entry in tournament.entries
            if entry.substrate_id == tournament.winner_substrate_id_optional
        ),
        None,
    )
    if winner is None:
        return set()
    if winner.substrate_model_type == "low_rank_gravity_residual_representation":
        return {"substrate_pca_low_rank_uv_local"}
    if winner.substrate_model_type == "region_specific_distance_decay_gravity":
        return {"substrate_distance_decay_uv_local"}
    return set()


def _tournament_winner_experiment_ids(
    tournament: SubstrateTournamentResult | None,
) -> set[str]:
    if tournament is None or not tournament.winner_substrate_id_optional:
        return set()
    ids: set[str] = set()
    for entry in tournament.entries:
        if entry.substrate_id != tournament.winner_substrate_id_optional:
            continue
        artifact_path = entry.experiment_artifact_path_optional or ""
        stem = Path(artifact_path).stem
        if stem.startswith("experiment-artifact-"):
            ids.add(stem.removeprefix("experiment-artifact-"))
    return ids


def _mutation_tournament_winner_experiment_types(
    tournament: MutationTournamentResult,
) -> set[str]:
    if not tournament.second_generation_winner_substrate_id_optional:
        return set()
    winner = next(
        (
            entry
            for entry in tournament.entries
            if entry.substrate_id
            == tournament.second_generation_winner_substrate_id_optional
        ),
        None,
    )
    if winner is None:
        return set()
    mapping = {
        "region_specific_distance_decay_gravity": {
            "substrate_distance_decay_uv_local"
        },
        "hierarchical_region_cluster_distance_decay": {
            "substrate_hierarchical_alpha_uv_local"
        },
        "gravity_low_rank_residual_hybrid": {
            "substrate_gravity_low_rank_hybrid_uv_local"
        },
        "boundary_perturbation_distance_decay_robustness": {
            "substrate_boundary_perturbation_uv_local"
        },
    }
    return mapping.get(winner.substrate_model_type, set())


def _mutation_tournament_winner_experiment_ids(
    tournament: MutationTournamentResult,
) -> set[str]:
    if not tournament.second_generation_winner_substrate_id_optional:
        return set()
    ids: set[str] = set()
    for entry in tournament.entries:
        if entry.substrate_id != tournament.second_generation_winner_substrate_id_optional:
            continue
        artifact_path = entry.experiment_artifact_path_optional or ""
        stem = Path(artifact_path).stem
        if stem.startswith("experiment-artifact-"):
            ids.add(stem.removeprefix("experiment-artifact-"))
    return ids


def _tournament_result_lines(tournament: SubstrateTournamentResult) -> list[str]:
    lines = [
        (
            "A substrate tournament compared serious synthetic branches using declared "
            "within-scope metrics. The selected manuscript branch is "
            f"`{tournament.winner_substrate_title_optional or 'none'}`. "
            f"{tournament.winner_reason_optional or 'No winner was selected.'}"
        ),
        "| substrate | result | MAE ratio | RMSE ratio | ablation | score |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for entry in tournament.entries:
        lines.append(
            f"| {entry.substrate_title} | {entry.result_status} | "
            f"{entry.mae_improvement_ratio} | {entry.rmse_improvement_ratio} | "
            f"{entry.ablation_sensitivity} | {entry.tournament_score} |"
        )
    lines.append(
        "The tournament ranking is manuscript-focus context only; it does not create "
        "real-world validation, novelty, broad correctness, or publication readiness."
    )
    return lines


def _mutation_tournament_result_lines(
    tournament: MutationTournamentResult,
) -> list[str]:
    lines = [
        (
            "A second-generation mutation tournament compared the previous synthetic winner "
            "against tournament-driven mutation substrates. The selected current branch is "
            f"`{tournament.second_generation_winner_title_optional or 'none'}`. "
            f"{tournament.second_generation_winner_reason_optional or 'No winner was selected.'}"
        ),
        (
            "| branch | role | outcome | improvement | complexity penalty | robustness | score |"
        ),
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for entry in tournament.entries:
        lines.append(
            f"| {entry.title} | {entry.branch_role} | {entry.outcome_label} | "
            f"{entry.improvement_ratio} | {entry.complexity_penalty_optional or 0.0} | "
            f"{entry.robustness_metric_optional or 0.0} | {entry.score} |"
        )
    lines.append(
        "The second-generation ranking is synthetic manuscript-focus context only. It does "
        "not establish real-world validation, novelty, broad correctness, or publication "
        "readiness."
    )
    return lines


def _creative_search_result_lines(
    report: CreativeSearchControllerReport,
) -> list[str]:
    lines = [
        (
            "The recursive creative-search controller tracked bounded branch selection across "
            f"{report.cycle_count} cycle(s) and stopped because `{report.stop_reason.value}`. "
            "This lineage is search context, not scientific evidence."
        ),
        "| cycle | starting branch | starting score | ending branch | ending score | improvement |",
        "|---:|---|---:|---|---:|---:|",
    ]
    for cycle in report.cycles:
        lines.append(
            f"| {cycle.cycle_index} | {cycle.starting_winner} | {cycle.starting_score} | "
            f"{cycle.ending_winner} | {cycle.ending_score} | "
            f"{cycle.absolute_improvement} |"
        )
    lines.extend(
        [
            "Winning lineage:",
            "| stage | branch | bounded score |",
            "|---|---|---:|",
        ]
    )
    for entry in report.lineage:
        score = entry.score_optional if entry.score_optional is not None else "n/a"
        lines.append(
            f"| {entry.lineage_role} | {_safe_lineage_title(entry.title)} | {score} |"
        )
    lines.append(
        "Scores compare only the declared local synthetic fixtures and do not establish "
        "real-world validation, novelty, broad correctness, or publication readiness."
    )
    return lines


def _creative_search_appendix_text(
    report: CreativeSearchControllerReport | None,
) -> str:
    if report is None:
        return "No recursive creative-search controller report is present."
    lineage = "; ".join(
        f"{entry.lineage_role}: {_safe_lineage_title(entry.title)}"
        for entry in report.lineage
    )
    return (
        f"Recursive search lineage ({report.stop_reason.value}): {lineage}. "
        "This is provenance and branch-selection context only, not verification evidence."
    )


def _safe_lineage_title(title: str) -> str:
    """Keep provenance titles from being restated as unsupported authority claims."""
    safe = re.sub(r"\b(theorem|conjecture|proof)\b", "formal-status variant", title, flags=re.I)
    safe = re.sub(r"\bpublication[- ]ready\b", "release-bounded", safe, flags=re.I)
    return " ".join(safe.split())


def _tournament_appendix_text(tournament: SubstrateTournamentResult | None) -> str:
    if tournament is None:
        return "No substrate tournament report is present."
    non_winners = [
        entry
        for entry in tournament.entries
        if entry.substrate_id != tournament.winner_substrate_id_optional
    ]
    if not non_winners:
        return "No non-winning substrate branch was present in the tournament."
    return (
        "Non-winning tournament branches remain visible: "
        + "; ".join(
            _tournament_nonwinner_summary(entry)
            for entry in non_winners
        )
        + ". These alternatives are not pruned as impossible; they are lower-ranked or "
        "non-selected within this bounded synthetic run."
    )


def _tournament_nonwinner_summary(entry: Any) -> str:
    extras = ""
    if entry.latent_factor_recovery_correlation_optional is not None:
        extras = (
            ", latent-factor recovery correlation "
            f"{entry.latent_factor_recovery_correlation_optional}"
        )
    if entry.explained_residual_variance_optional is not None:
        extras += (
            ", explained residual variance "
            f"{entry.explained_residual_variance_optional}"
        )
    return (
        f"{entry.substrate_title} ended as {entry.result_status} with score "
        f"{entry.tournament_score}{extras}"
    )


def _mutation_tournament_appendix_text(
    tournament: MutationTournamentResult | None,
) -> str:
    if tournament is None:
        return "No mutation substrate tournament report is present."
    non_winners = [
        entry
        for entry in tournament.entries
        if entry.substrate_id
        != tournament.second_generation_winner_substrate_id_optional
    ]
    if not non_winners:
        return "No non-winning mutation branch was present in the tournament."
    return (
        "Mutation tournament alternatives remain visible: "
        + "; ".join(_mutation_tournament_branch_summary(entry) for entry in non_winners)
        + ". These branches are bounded synthetic alternatives, not failed scientific "
        "directions outside the declared fixture."
    )


def _mutation_tournament_branch_summary(entry: Any) -> str:
    return (
        f"{entry.title} ended as {entry.outcome_label} with score {entry.score}, "
        f"improvement ratio {entry.improvement_ratio}, and robustness metric "
        f"{entry.robustness_metric_optional or 0.0}"
    )


def _normalize_paragraph(text: str) -> str:
    return " ".join(text.split())


def _substrate_context(
    selected: ScientificSubstrate | None,
    substrates: list[ScientificSubstrate],
) -> dict[str, str]:
    if selected is None:
        return {
            "title": "a bounded synthetic demonstration substrate",
            "primary_equation": "Y = f(X; theta) + epsilon",
            "variables_text": (
                "Y is the target outcome, X are bounded inputs, and theta are model "
                "parameters"
            ),
            "mechanism": (
                "A deterministic method is compared with a simple baseline under fixed "
                "seeds."
            ),
            "identifiability": (
                "No broad identifiability claim is made without a matching artifact."
            ),
            "dgp": "A synthetic fixture is generated under fixed seeds.",
            "baseline": "simple deterministic baseline",
            "method": "bounded deterministic method",
            "metrics_text": "MAE and RMSE",
            "seed_plan": "fixed deterministic seeds",
            "ablation": "vary noise level and compare metric stability",
            "result_columns": "seed, baseline_MAE, baseline_RMSE, method_MAE, method_RMSE",
            "claim_supported_if": "the method improves MAE and RMSE for the configured run.",
            "claim_not_supported_if": (
                "metrics are missing or the method does not improve over baseline."
            ),
            "alternative_text": "No generated alternative scientific substrate is available.",
        }
    variables = ", ".join(
        f"`{variable.symbol}` = {variable.definition}"
        for variable in selected.variables_and_notation
    )
    design = selected.experiment_design
    schema = selected.result_schema
    alternatives = [
        substrate
        for substrate in substrates
        if substrate.substrate_id != selected.substrate_id
    ]
    if alternatives:
        alternative_text = (
            "Alternative generated substrates remain available but unpruned: "
            + "; ".join(
            (
                f"{substrate.title} via "
                f"{substrate.source_mutation_axis_optional or 'unknown axis'} "
                f"with equations {', '.join(substrate.concrete_model_object.equations)}"
            )
            for substrate in alternatives
        )
        )
    else:
        alternative_text = "No alternative generated substrate is present."
    return {
        "title": selected.title,
        "primary_equation": selected.concrete_model_object.equations[0],
        "variables_text": variables,
        "mechanism": selected.mechanism,
        "identifiability": selected.concrete_model_object.identifiability_notes,
        "dgp": selected.dgp_or_dataset,
        "baseline": selected.baseline,
        "method": design.method,
        "metrics_text": ", ".join(design.metrics),
        "seed_plan": design.seed_plan,
        "ablation": design.ablation_or_stress_test,
        "result_columns": ", ".join(schema.required_table_columns),
        "claim_supported_if": schema.claim_supported_if,
        "claim_not_supported_if": schema.claim_not_supported_if,
        "alternative_text": alternative_text,
    }


def _claim_summaries(source_map: ClaimEvidenceMap) -> list[FinalManuscriptClaimSummary]:
    summaries = []
    for link in source_map.links:
        if link.support_status == "supported_within_scope":
            disposition = "included_supported_within_scope"
        elif link.support_status == "not_required_scaffold":
            disposition = "included_scaffold_or_boundary"
        elif link.support_status == "partially_supported":
            disposition = "downgraded_to_boundary"
        else:
            disposition = "removed_forbidden_or_unsupported"
        summaries.append(
            FinalManuscriptClaimSummary(
                claim_id=link.claim_id,
                section_name=link.section_name,
                claim_class=link.claim_class,
                support_status=link.support_status,
                support_type=link.support_type,
                disposition=disposition,
                supporting_citation_keys=link.supporting_citation_keys,
                supporting_proof_artifact_ids=link.supporting_proof_artifact_ids,
                supporting_experiment_artifact_ids=link.supporting_experiment_artifact_ids,
                limitations=link.evidence_limitations,
            )
        )
    return summaries


def _deferred_gap_summary(
    loop: AutonomousLoopRunReport,
    escalation: CapabilityEscalationReport | None,
) -> dict[str, Any]:
    deferred = [
        item
        for item in loop.gap_terminal_classifications
        if item.terminal_class.startswith("deferred_")
        or item.terminal_class in {"duplicate_only", "noncritical_boundary_gap"}
    ]
    proof = sum("proof" in item.gap_type or "proof" in item.terminal_class for item in deferred)
    retrieval = sum(
        "retrieval" in item.gap_type or "retrieval" in item.terminal_class for item in deferred
    )
    experiment = sum(
        "experiment" in item.gap_type or "budget" in item.terminal_class for item in deferred
    )
    if not deferred:
        proof = loop.proof_paths_deferred
        retrieval = loop.retrieval_paths_deferred
    statements = []
    if proof:
        statements.append(
            f"No passed formal proof artifact resolves {proof} deferred proof path(s); "
            "proof plans are context only."
        )
    if retrieval:
        statements.append(
            f"{retrieval} retrieval path(s) remain deferred; local expansion is not validation."
        )
    if experiment:
        statements.append(f"{experiment} experiment path(s) remain deferred or budget-limited.")
    if escalation and escalation.deferred_after_escalation_count and not statements:
        statements.append(
            f"{escalation.deferred_after_escalation_count} path(s) remain deferred after local "
            "capability escalation."
        )
    types = sorted({item.gap_type for item in deferred})
    total = max(len(deferred), proof + retrieval + experiment, len(statements))
    return {
        "total": total,
        "proof": proof,
        "retrieval": retrieval,
        "experiment": experiment,
        "statements": statements,
        "types": types,
    }


def _require_safe_registry(registry: CitationRegistry) -> None:
    unsafe = [
        record.citation_key for record in registry.citations if not record.accepted_for_registry
    ]
    if unsafe:
        raise FinalManuscriptRegenerationError(
            "Citation registry contains non-accepted sources: " + ", ".join(sorted(unsafe))
        )


def _require_safe_regeneration(claim_support, citation_safety, claim_map: ClaimEvidenceMap) -> None:
    counts = claim_support.summary_counts
    blockers = {
        "missing_required_citation": int(counts.get("missing_required_citation", 0)),
        "scope_mismatch": int(counts.get("scope_mismatch", 0)),
        "forbidden_claim": int(counts.get("forbidden_claim", 0)),
        "unsupported_external_claim": int(counts.get("unsupported_external_claim", 0)),
        "citation_as_validation_misuse": int(counts.get("citation_as_validation_misuse", 0)),
        "unregistered_citation_keys": len(citation_safety.unregistered_citation_keys),
        "unsupported_claim_links": len(claim_map.unsupported_non_scaffold_claim_ids),
    }
    active = [f"{key}={value}" for key, value in blockers.items() if value]
    if active:
        raise FinalManuscriptRegenerationError(
            "Final manuscript regeneration failed closed: " + ", ".join(active)
        )


def _render_manuscript(title: str, sections: list[FinalManuscriptSection]) -> str:
    blocks = [f"# {title}"]
    for section in sections:
        blocks.extend(["", f"## {section.heading}", "", section.body_markdown.strip()])
    return "\n".join(blocks).rstrip() + "\n"


def _sections_from_markdown(
    markdown: str,
    originals: list[FinalManuscriptSection],
) -> list[FinalManuscriptSection]:
    by_heading = {section.heading: section for section in originals}
    matches = list(re.finditer(r"^##\s+(.+?)\s*$", markdown, re.MULTILINE))
    sections = []
    for index, match in enumerate(matches):
        heading = match.group(1).strip()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(markdown)
        body = markdown[match.end() : body_end].strip()
        original = by_heading.get(heading)
        if original is None:
            continue
        sections.append(
            original.model_copy(update={"body_markdown": body, "word_count": _word_count(body)})
        )
    return sections or originals


def _domain_context(reports: Path, run_id: str) -> dict[str, str]:
    config = _read_json_dict(reports / "llm-orchestration-config.json")
    plan = _read_json_dict(reports / "manuscript-plan.json")
    domain = str(config.get("domain") or plan.get("domain") or "").strip()
    if not domain:
        domain = _domain_from_run_id(run_id)
    domain = _clean_inline_text(domain) or "the selected research domain"
    domain_title = _title_case_domain(domain)
    plan_title = str(plan.get("title") or "").strip()
    method = str(config.get("method") or config.get("topic_or_question") or "").strip()
    method_phrase = _method_phrase(method=method, plan_title=plan_title)
    research_question = (
        f"How can a bounded {method_phrase} represent or stress-test {domain} while keeping "
        "source context, synthetic results, and deferred evidence separate?"
    )
    hypothesis = (
        "the configured uv-local synthetic demonstration can report reproducible, run-specific "
        f"metrics for a mapped {domain} claim under fixed seeds and captured artifacts."
    )
    return {
        "domain": domain,
        "domain_title": domain_title,
        "method_phrase": method_phrase,
        "research_question": research_question,
        "hypothesis": hypothesis,
    }


def _source_title(reports: Path, run_id: str, domain_context: dict[str, str]) -> str:
    candidates = [
        *reversed(_numbered_paths(reports, "final-manuscript-*.md")),
        reports / "evidence-aware-refreshed-manuscript-draft.md",
        reports / "revised-manuscript-draft.md",
        reports / "complete-manuscript-draft.md",
    ]
    for path in candidates:
        if not path.is_file() or "regeneration" in path.name:
            continue
        match = re.search(r"^#\s+(.+?)\s*$", path.read_text(encoding="utf-8"), re.MULTILINE)
        if not match:
            continue
        title = match.group(1).strip()
        if title.casefold() in {"draft", "untitled"}:
            continue
        if not _pipeline_facing_title(title) and _title_mentions_domain(
            title,
            domain_context["domain"],
        ):
            return title
    return f"Bounded Synthetic Demonstration for {domain_context['domain_title']}"


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _clean_inline_text(value: str) -> str:
    return " ".join(value.replace("\n", " ").split())


def _domain_from_run_id(run_id: str) -> str:
    normalized = run_id.replace("_", "-").casefold()
    if "spatial-heterogeneity" in normalized and "human-geography" in normalized:
        return "spatial heterogeneity in human geography"
    if "human-geography" in normalized:
        return "human geography"
    tokens = [
        token
        for token in re.split(r"[-\s]+", normalized)
        if token
        and token
        not in {
            "run",
            "local",
            "final",
            "manuscript",
            "bundle",
            "smoke",
            "paper",
            "autonomous",
            "openai",
            "hybrid",
        }
    ]
    return " ".join(tokens[:6]) if tokens else "the selected research domain"


def _title_case_domain(domain: str) -> str:
    small_words = {"and", "in", "of", "the", "for", "to", "with"}
    words = domain.split()
    titled = []
    for index, word in enumerate(words):
        if index and word.casefold() in small_words:
            titled.append(word.casefold())
        else:
            titled.append(word[:1].upper() + word[1:])
    return " ".join(titled)


def _method_phrase(*, method: str, plan_title: str) -> str:
    text = f"{method} {plan_title}".casefold()
    if "calibration" in text:
        return "synthetic calibration model"
    if "ablation" in text:
        return "synthetic ablation model"
    if "simulation" in text:
        return "synthetic simulation model"
    if "proof" in text or "theorem" in text:
        return "bounded formal-model sketch"
    if "heterogeneity" in text or "geograph" in text or "spatial" in text:
        return "synthetic spatial-heterogeneity demonstration model"
    return "bounded synthetic demonstration model"


def _pipeline_facing_title(title: str) -> bool:
    lowered = title.casefold()
    markers = {
        "factori",
        "claim-evidence",
        "citation registr",
        "evidence-aware",
        "evidence-bounded manuscript generation",
        "manuscript generation",
        "autonomous",
        "workflow",
        "bundle",
        "artifact",
        "pipeline",
        "release",
        "verification",
    }
    return any(marker in lowered for marker in markers)


def _title_mentions_domain(title: str, domain: str) -> bool:
    title_tokens = set(re.findall(r"[a-z0-9]+", title.casefold()))
    domain_tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", domain.casefold())
        if token not in {"the", "and", "of", "in", "for", "to"}
    }
    if not domain_tokens:
        return False
    required_overlap = min(3, len(domain_tokens))
    return len(title_tokens & domain_tokens) >= required_overlap


def _preferred_retrieval_report_path(reports: Path) -> Path:
    quality = reports / "retrieval-quality-report.json"
    fallback = reports / "retrieval-report.json"
    if quality.is_file():
        return quality
    if fallback.is_file():
        return fallback
    raise FinalManuscriptRegenerationError("No retrieval report found for final regeneration.")


def _latest_loop_report_path(
    root: Path,
    run_id: str,
    loop: AutonomousLoopRunReport | None,
) -> Path | None:
    return root / "runs" / run_id / "reports" / f"{loop.loop_id}.json" if loop else None


def _latest_escalation_report_path(
    root: Path,
    run_id: str,
    escalation: CapabilityEscalationReport | None,
) -> Path | None:
    return (
        root / "runs" / run_id / "reports" / f"{escalation.escalation_id}.json"
        if escalation
        else None
    )


def _next_regeneration_number(reports: Path) -> int:
    return (
        len(_numbered_paths(reports, "final-manuscript-regeneration-[0-9][0-9][0-9][0-9].json")) + 1
    )


def _next_claim_evidence_map_id(reports: Path) -> str:
    numbers = [
        int(match.group(1))
        for path in reports.glob("claim-evidence-map-*.json")
        if not path.name.endswith(".meta.json")
        if (match := re.match(r"claim-evidence-map-(\d+)\.json$", path.name))
    ]
    return f"claim-evidence-map-{max(numbers, default=0) + 1:04d}"


def _numbered_paths(directory: Path, pattern: str) -> list[Path]:
    return sorted(path for path in directory.glob(pattern) if not path.name.endswith(".meta.json"))


def _formal_proof(proof: ProofArtifact) -> bool:
    return proof.proof_type in _FORMAL_PROOF_TYPES and proof.checker_status == "passed"


def _read_model(path: Path, model_type):
    try:
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FinalManuscriptRegenerationError(f"Invalid required artifact: {path}") from exc


def _read_optional_model(path: Path, model_type):
    if not path.is_file():
        return None
    try:
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-") or "section"


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\w'-]+\b", text))
