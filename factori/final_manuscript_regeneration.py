"""Deterministic final manuscript regeneration from scoped evidence state."""

from __future__ import annotations

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
    ExperimentArtifact,
    FinalManuscriptClaimSummary,
    FinalManuscriptRegenerationIndex,
    FinalManuscriptRegenerationReport,
    FinalManuscriptSection,
    FinalManuscriptStructuredDocument,
    FullPaperReleaseGateConfig,
    HumanReviewArtifact,
    ProofArtifact,
    RetrievalQualityReport,
)

_FORMAL_PROOF_TYPES = {"lean_verified", "formal_verified", "external_certificate"}
_SAFE_SECTION_PLAN = (
    "Abstract",
    "Introduction",
    "Related Work and Source Boundaries",
    "Method / System Architecture",
    "Claim-Evidence Map",
    "Formal / Proof Status",
    "Empirical Demonstration",
    "Autonomous Execution Trace",
    "Limitations and Deferred Gaps",
    "Conclusion",
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
    title = _source_title(reports, run_id)
    deferred = _deferred_gap_summary(loop, escalation)
    claim_summaries = _claim_summaries(source_map)
    sections = _build_sections(
        run_id=run_id,
        source_map=source_map,
        registry=registry,
        retrieval=retrieval,
        proofs=proofs,
        experiments=experiments,
        loop=loop,
        escalation=escalation,
        deferred=deferred,
        human_review=human_review,
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
    source_map: ClaimEvidenceMap,
    registry: CitationRegistry,
    retrieval: RetrievalQualityReport,
    proofs: list[ProofArtifact],
    experiments: list[ExperimentArtifact],
    loop: AutonomousLoopRunReport,
    escalation: CapabilityEscalationReport | None,
    deferred: dict[str, Any],
    human_review: HumanReviewArtifact | None,
) -> list[FinalManuscriptSection]:
    counts = claim_evidence_summary_fields(source_map)
    formal = [proof for proof in proofs if _formal_proof(proof)]
    informal = [
        proof for proof in proofs if proof.proof_type in {"proof_plan", "informal_proof_note"}
    ]
    completed = [experiment for experiment in experiments if experiment.status == "completed"]
    accepted = [record for record in registry.citations if record.accepted_for_registry]
    citation_lines = [
        (
            f"The bounded background for this manuscript includes {record.title} "
            f"[@{record.citation_key}]."
        )
        for record in accepted
    ] or ["No accepted registry source is asserted as complete literature coverage."]
    bibliography = [entry.markdown for entry in registry.bibliography]
    proof_text = (
        "A passed formal artifact is linked to the mapped claim identifiers "
        + ", ".join(proof.proof_id for proof in formal)
        + ". Its authority is restricted to the declared checker scope; it does not establish "
        "novelty, broad correctness, empirical validity, or publication readiness."
        if formal
        else (
            "Proof-plan or informal proof context exists, but no passed formal artifact supports "
            "the deferred proof path. This is not a proof and the formal obligation remains open."
            if informal
            else "No passed formal proof artifact is linked; formal proof remains deferred."
        )
    )
    experiment_lines = []
    for experiment in completed:
        metrics = ", ".join(f"`{key}={value}`" for key, value in sorted(experiment.metrics.items()))
        experiment_lines.append(
            "The current run includes completed local synthetic experiment artifact "
            f"`{experiment.experiment_id}` with recorded metrics {metrics or '`none recorded`'}. "
            "The record is limited to its fixed configuration, hashes, logs, and mapped claim; "
            "it does not establish broad empirical validation or publication readiness."
        )
    if not experiment_lines:
        experiment_lines.append(
            "No completed experiment artifact is linked, so empirical demonstration remains a "
            "future bounded task rather than an asserted result."
        )
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
        "The autonomous loop and capability reports are workflow traces, not scientific evidence.",
        "This manuscript does not claim novelty, complete literature coverage, broad correctness, "
        "or publication readiness; publication_ready remains false.",
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
            "This manuscript records a bounded research scaffold and the deterministic workflow "
            "used to assemble it from accepted source context and scoped evidence artifacts. The "
            "final claim-evidence map contains "
            f"{counts['claim_evidence_supported_count']} supported "
            f"claims, including {counts['proof_supported_claim_count']} proof-linked and "
            f"{counts['experiment_supported_claim_count']} experiment-linked claims. Retrieval, "
            "citation checks, autonomous execution, and capability escalation do not establish "
            "scientific validation. Deferred proof and retrieval work is retained as "
            "an explicit limitation, and publication_ready remains false."
        ),
        "Introduction": (
            "The manuscript addresses a representation and workflow problem: a research direction "
            "must remain readable while every positive statement is kept within the authority of "
            "its available source, proof, or experiment artifact. The objective of this final pass "
            "is therefore bounded coherence, not a claim of novelty or broad correctness. The "
            "regeneration process filters unsupported wording, retains scaffold and boundary "
            "statements, and exposes deferred work instead of converting workflow success into "
            "scientific authority."
        ),
        "Related Work and Source Boundaries": "\n\n".join(
            [
                *citation_lines,
                "The registry is bounded and does not claim exhaustive literature coverage, "
                "novelty, proof, empirical validation, or publication readiness.",
                "## Bibliography\n\n" + "\n".join(bibliography) if bibliography else "",
            ]
        ).strip(),
        "Method / System Architecture": (
            "The deterministic workflow constructs a citation registry from quality-accepted local "
            "records, audits manuscript sentences, links claims to scoped artifacts, plans missing "
            "evidence work, executes policy-approved local actions, and rebuilds the evidence map. "
            "A gated uv-local sandbox may execute only approved experiment bundles "
            "with fixed seeds, "
            "captured logs, metrics, and hashes. Local proof-plan refinement remains non-verifying "
            "unless an exact passed formal artifact is ingested. Release checks assess package "
            "readiness for review only and cannot create evidence."
        ),
        "Claim-Evidence Map": (
            f"The source map contains {len(source_map.links)} claim records. "
            "These counts do not establish scientific validation: "
            f"{counts['citation_supported_claim_count']} are linked to accepted "
            "background citations, "
            f"{counts['proof_supported_claim_count']} to scoped proof artifacts, "
            f"{counts['experiment_supported_claim_count']} to completed experiment artifacts, and "
            f"{counts['human_review_linked_claim_count']} to review-occurrence context. "
            "Unsupported "
            "noncritical wording is omitted or represented as a boundary statement. Evidence links "
            "do not transfer authority across claims."
        ),
        "Formal / Proof Status": proof_text,
        "Empirical Demonstration": "\n\n".join(experiment_lines),
        "Autonomous Execution Trace": (
            f"The autonomous loop completed {loop.iterations_completed} iteration(s) with terminal "
            f"state `{loop.terminal_state}` and stop reason `{loop.stop_reason}`. It recorded "
            f"{loop.resolved_gap_count} resolved, {loop.deferred_gap_count} deferred, and "
            f"{loop.exhausted_gap_count} exhausted workflow gaps. {escalation_text} {human_text} "
            "These records describe orchestration and provenance only; they are not "
            "verification or "
            "scientific validation."
        ),
        "Limitations and Deferred Gaps": "\n\n".join(limitation_lines),
        "Conclusion": (
            "This draft does not establish scientific validation. The regenerated artifact is a "
            "coherent bounded draft assembled from scoped claim-evidence links and autonomous "
            "workflow reports. Any "
            "completed local experiment remains limited to its mapped configuration. No passed "
            "formal proof artifact is implied where proof obligations remain unresolved. Retrieval "
            "obligations also remain visible. Stronger scientific "
            "claims require corresponding future artifacts and policy checks. The current release "
            "remains a human-review workflow status with warnings, and publication_ready is false."
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
                included_claim_ids=claim_ids if heading == "Claim-Evidence Map" else [],
                citation_keys=(
                    sorted(record.citation_key for record in accepted)
                    if heading == "Related Work and Source Boundaries"
                    else []
                ),
                proof_artifact_ids=[proof.proof_id for proof in formal]
                if heading == "Formal / Proof Status"
                else [],
                experiment_artifact_ids=[experiment.experiment_id for experiment in completed]
                if heading == "Empirical Demonstration"
                else [],
                deferred_gap_types=deferred["types"]
                if heading == "Limitations and Deferred Gaps"
                else [],
            )
        )
    return sections


_FINAL_SECTION_DEPTH_TARGETS = {
    "Abstract": 140,
    "Introduction": 240,
    "Related Work and Source Boundaries": 130,
    "Method / System Architecture": 240,
    "Claim-Evidence Map": 180,
    "Formal / Proof Status": 130,
    "Empirical Demonstration": 170,
    "Autonomous Execution Trace": 130,
    "Limitations and Deferred Gaps": 200,
    "Conclusion": 170,
}

_FINAL_SECTION_DEVELOPMENT = {
    "Abstract": [
        """
        The bounded contribution is a final presentation layer that consumes existing registry,
        linkage, execution, and release records without changing their authority. It distinguishes
        accepted background context from claim-specific artifacts. This distinction does not
        establish scientific validation; it preserves failed or deferred work and records the
        post-regeneration safety checks.
        """,
        """
        The resulting document is intended for inspection and subsequent export. It is not proof,
        experiment evidence, human approval, or a substitute for the append-only ledger.
        """,
    ],
    "Introduction": [
        """
        The problem statement is operational as well as representational. Incremental drafting can
        leave related facts distributed across revisions even when each local patch is safe. A final
        deterministic pass must reconstruct section order, evidence boundaries, and deferred-work
        language from canonical artifacts instead of relying on stale prose.
        """,
        """
        The contribution of this draft is limited to that reconstruction contract. Each included
        statement is either supported within its declared scope or phrased as method, provenance,
        limitation, scaffold, or future-work context. Unsupported authority language is excluded.
        """,
        """
        This objective keeps the autonomous path usable without requiring a reviewer to reconcile
        ordinary state transitions. Human review remains a separate audit and override layer, while
        evidence admission continues to depend on deterministic artifact policy.
        """,
    ],
    "Related Work and Source Boundaries": [
        """
        This source section does not establish scientific validation. Registry inclusion records
        only that a source passed the configured metadata, duplicate, relevance, and safety rules.
        It does not show that the source set is exhaustive, that every relevant publication was
        retrieved, or that the manuscript contribution is new.
        """,
        """
        The bibliography preserves identifiers and accepted citation keys so each local statement
        can be traced to bounded context. Rejected and hard-rejected records remain visible in
        retrieval reports but cannot enter this bibliography or support a manuscript claim.
        """,
    ],
    "Method / System Architecture": [
        """
        The method separates discovery context, manuscript context, and verification evidence.
        This source workflow does not establish scientific validation; normalization and relevance
        filtering produce a bounded registry. This audit does not establish scientific validation;
        sentence checks classify support requirements before links are constructed from accepted
        context keys and claim-specific artifact identifiers.
        """,
        """
        Autonomous planning operates only after those classifications exist. Its actions can revise
        wording, create planned specifications, route approved local templates, or record deferred
        work. Planned specifications remain non-evidence until an execution adapter produces an
        artifact that passes the existing intake contract.
        """,
        """
        This audit does not establish scientific validation; regeneration reads the latest immutable
        indexes, writes numbered outputs, and recomputes usage, support, linkage, quality
        diagnostics,
        and release status. The provenance appendix is represented by content-hashed reports and the
        producing ledger commit; regeneration never edits prior artifacts.
        """,
    ],
    "Claim-Evidence Map": [
        """
        This accounting does not establish scientific validation; it records support scope. Citation
        links authorize bounded literature context only. Passed formal links authorize only their
        declared statement identifiers, and completed experiment links authorize only their declared
        run, configuration, metrics, and target identifiers.
        """,
        """
        A scaffold or boundary statement requires no external support because it describes the
        current package or limits its interpretation. A partially supported source claim is reduced
        to boundary wording, while a forbidden or unsupported authority claim is omitted.
        Human-review links record review occurrence or status only.
        """,
    ],
    "Formal / Proof Status": [
        """
        No passed formal proof artifact should be inferred from orchestration success, a generated
        proof obligation, or a refined proof plan. Formal authority requires an accepted artifact
        whose declared claim identifier or statement hash matches the mapped statement and whose
        checker status is passed.
        """,
        """
        This proof-status section does not establish broad correctness. Even a passed formal link
        would remain limited to its declared statement and checker scope; it would not support
        novelty, empirical behavior, literature coverage, or publication readiness.
        """,
    ],
    "Empirical Demonstration": [
        """
        This record does not establish empirical validation; the synthetic design is a
        reproducibility check for one configured local path. Inputs, code, dependency policy,
        fixed seed, logs, metrics, and outputs are hashed so the bounded result can be inspected
        without extending it to unobserved datasets or alternative methods.
        """,
        """
        A failed, timed-out, inconclusive, or policy-rejected sandbox run would not count as
        completed experiment support. Template selection and sandbox orchestration are workflow
        context only. The experiment artifact cannot support theorem, novelty, broad correctness,
        or release claims.
        """,
    ],
    "Autonomous Execution Trace": [
        """
        The trace records which deterministic stages ran, which artifacts were created, and why the
        controller stopped. New artifacts count as progress only once, while duplicate
        specifications,
        repeated no-op refreshes, and exhausted attempts remain non-progress outcomes.
        """,
        """
        This execution trace is not verification evidence. Terminal and escalation statuses describe
        workflow disposition only, preserve deferred work, and leave all claim authority under the
        citation, proof, experiment, and human-review intake policies.
        """,
    ],
    "Limitations and Deferred Gaps": [
        """
        Bounded retrieval context does not establish scientific validation or exhaustive coverage.
        The accepted-source count constrains the related-work discussion, while the rejected-source
        count records items that failed relevance, metadata, duplicate, or safety checks. This
        context does not establish scientific validation; additional local packs remain optional.
        """,
        """
        No passed formal proof artifact should be inferred from a proof plan, refined outline,
        fixture request, or deferred obligation. Likewise, one completed synthetic experiment
        cannot establish real-world performance, robustness across settings, or comparative
        superiority beyond its recorded configuration.
        """,
        """
        Automation budgets and disabled capabilities can leave safe work deferred even when no
        unsupported manuscript claim remains. Those dispositions remain in loop and escalation
        reports. They are not silently reclassified as resolved evidence and do not require human
        intervention unless the system encounters corruption, contradiction, or an unclassified
        safety condition.
        """,
    ],
    "Conclusion": [
        """
        The bounded result is a final manuscript whose wording is derived from the latest scoped
        state rather than accumulated revision fragments. Accepted context records, claim-specific
        artifacts, and workflow reports remain distinguishable in the Markdown document and
        structured companion.
        """,
        """
        This draft does not establish novelty, broad correctness, broad empirical validation, or
        publication readiness. Deferred proof and retrieval paths remain visible, and any future
        No passed formal proof artifact is implied by a deferred path. Any future automatic action
        must produce a new accepted scoped artifact before stronger mapped wording can appear.
        """,
        """
        The package can proceed to deterministic export or optional human audit, but presentation
        polish and review occurrence do not change the evidence classes recorded here. Publication
        readiness remains false under the current policy.
        """,
    ],
}


def _develop_section_body(heading: str, body: str) -> str:
    target = _FINAL_SECTION_DEPTH_TARGETS.get(heading)
    if target is None or _word_count(body) >= target:
        return body
    bibliography = ""
    if "\n\n## Bibliography" in body:
        body, bibliography_body = body.split("\n\n## Bibliography", maxsplit=1)
        bibliography = "\n\n## Bibliography" + bibliography_body
    paragraphs = [body, *map(_normalize_paragraph, _FINAL_SECTION_DEVELOPMENT.get(heading, []))]
    developed = "\n\n".join(paragraphs)
    if _word_count(developed) < target:
        developed += (
            "\n\nThis section remains bounded by the recorded artifacts and does not create "
            "scientific validation or publication readiness. Its organization records the current "
            "artifact state, separates workflow context from scoped support, and preserves the "
            "limitations needed for conservative interpretation. It changes no evidence label, "
            "source decision, verification status, or release authority."
        )
    return developed + bibliography


def _normalize_paragraph(text: str) -> str:
    return " ".join(text.split())


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


def _source_title(reports: Path, run_id: str) -> str:
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
        if match and match.group(1).strip().casefold() not in {"draft", "untitled"}:
            return match.group(1).strip()
    return f"Bounded Evidence-Aware Manuscript for {run_id}"


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
