"""Deterministic evidence-aware manuscript wording refresh."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from factori.artifacts import ArtifactStore
from factori.citations import (
    build_claim_support_audit,
    repair_confirmed_claim_support_violations,
    repair_missing_required_citations_with_accepted_sources,
    validate_citation_usage,
)
from factori.claim_evidence import (
    build_claim_evidence_map,
    latest_claim_evidence_map_path,
    persist_claim_evidence_map,
)
from factori.full_paper_generation import (
    build_reviewer_bundle_summary,
    render_reviewer_bundle_summary_markdown,
)
from factori.full_paper_release import evaluate_full_paper_release
from factori.latex_export import (
    build_latex_export_contract,
    export_markdown_draft_to_latex,
    load_latex_export_inputs,
)
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.reports import render_full_paper_release_summary
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    CitationRegistry,
    ClaimEvidenceMap,
    ControllerActionType,
    EvidenceAwareRefreshReport,
    FullPaperReleaseGateConfig,
    HumanReviewArtifact,
)

_PROOF_WORDING = (
    "The current run includes a formal proof artifact linked to a specific mapped "
    "claim; this artifact is treated as verification evidence only within its "
    "declared checker scope and does not establish novelty, empirical validity, "
    "broad correctness, or publication readiness."
)
_EXPERIMENT_WORDING = (
    "The current run includes a completed experiment artifact linked to a bounded "
    "result claim; the artifact records the reported configuration, hashes, and "
    "metrics for that run only and does not imply broad empirical validation, "
    "novelty, broad correctness, or publication readiness."
)
_CITATION_WORDING = (
    "Accepted registry citations in this draft provide bounded background context "
    "only and do not establish proof, validation, novelty, or publication readiness."
)


class EvidenceAwareRefreshError(RuntimeError):
    """Raised when an evidence-aware refresh cannot be completed safely."""


@dataclass(frozen=True)
class EvidenceAwareRefreshResult:
    """Persisted evidence-aware refresh and post-refresh safety artifacts."""

    run_id: str
    report: EvidenceAwareRefreshReport
    refreshed_markdown: str
    claim_evidence_map: ClaimEvidenceMap
    release_status: str
    persistence: PersistenceResult
    release_persistence: PersistenceResult
    report_artifact: ArtifactRef
    manuscript_artifact: ArtifactRef
    claim_evidence_map_artifact: ArtifactRef
    reviewer_summary_artifact: ArtifactRef


def refresh_evidence_aware_manuscript(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    backend: str = "off",
    model: str | None = None,
    max_calls: int = 0,
    allow_external_calls: bool = False,
) -> EvidenceAwareRefreshResult:
    """Refresh the final manuscript with bounded linked-artifact wording."""
    del model
    if backend not in {"off", "deterministic", "fake", "openai"}:
        raise EvidenceAwareRefreshError(
            "evidence-aware refresh backend must be off, deterministic, fake, or openai"
        )
    if max_calls < 0:
        raise EvidenceAwareRefreshError("max evidence-aware refresh calls must be non-negative")
    if backend == "off":
        raise EvidenceAwareRefreshError(
            "Evidence-aware refresh is disabled; select deterministic, fake, or openai."
        )
    if backend == "openai":
        if not allow_external_calls:
            raise EvidenceAwareRefreshError(
                "OpenAI evidence-aware refresh requires --allow-external-calls."
            )
        raise EvidenceAwareRefreshError(
            "OpenAI evidence-aware refresh is gated but not implemented in M63."
        )

    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    reports = run_path / "reports"
    if not run_path.is_dir():
        raise EvidenceAwareRefreshError(f"No run directory found for run_id={run_id}.")
    if (reports / "evidence-aware-refresh-report.json").exists():
        raise EvidenceAwareRefreshError(
            "Evidence-aware refresh report already exists for this run."
        )

    claim_map_path = latest_claim_evidence_map_path(root_path, run_id)
    if claim_map_path is None:
        raise EvidenceAwareRefreshError(
            "No claim-evidence map found; run build-claim-evidence-map first."
        )
    claim_map = ClaimEvidenceMap.model_validate_json(
        claim_map_path.read_text(encoding="utf-8")
    )
    if claim_map.unsupported_non_scaffold_claim_ids:
        raise EvidenceAwareRefreshError(
            "Claim-evidence map contains unsupported non-scaffold claims; refresh is blocked."
        )

    manuscript_path = _preferred_manuscript_path(reports)
    markdown = manuscript_path.read_text(encoding="utf-8")
    registry = _read_registry(reports / "citation-registry.json")
    formal_proof_count = _supported_link_count(
        claim_map,
        support_type="formal_proof_verification",
    )
    experiment_count = _supported_link_count(
        claim_map,
        support_type="experiment_result",
    )
    citation_count = _supported_link_count(
        claim_map,
        support_type="citation_background_context",
    )
    refreshed, sections = _refresh_markdown(
        markdown,
        include_proof=formal_proof_count > 0,
        include_experiment=experiment_count > 0,
        include_citations=citation_count > 0,
    )
    removed_or_downgraded = 0
    available_evidence = {
        "proof": formal_proof_count > 0,
        "experiment": experiment_count > 0,
        "human_review": _valid_human_review_present(
            reports / "human-review-artifact.json"
        ),
        "publication_ready": False,
    }
    claim_support = build_claim_support_audit(
        run_id=run_id,
        markdown=refreshed,
        citation_registry=registry,
        available_evidence_artifacts=available_evidence,
    )
    refreshed, removed_ids = repair_confirmed_claim_support_violations(
        refreshed,
        claim_support,
    )
    removed_or_downgraded += len(removed_ids)
    claim_support = build_claim_support_audit(
        run_id=run_id,
        markdown=refreshed,
        citation_registry=registry,
        available_evidence_artifacts=available_evidence,
    )
    citation_repair = repair_missing_required_citations_with_accepted_sources(
        refreshed,
        claim_support,
        registry,
    )
    refreshed = citation_repair.revised_markdown
    removed_or_downgraded += (
        citation_repair.claims_downgraded + citation_repair.claims_removed
    )
    claim_support = build_claim_support_audit(
        run_id=run_id,
        markdown=refreshed,
        citation_registry=registry,
        available_evidence_artifacts=available_evidence,
    )
    citation_safety = validate_citation_usage(refreshed, registry)
    refreshed_map = build_claim_evidence_map(
        run_id=run_id,
        root=root_path,
        claim_support_audit=claim_support,
    )
    _require_post_refresh_safety(
        claim_support=claim_support,
        citation_safety=citation_safety,
        claim_map=refreshed_map,
    )

    latex_inputs = load_latex_export_inputs(run_id, root=root_path, ledger=ledger)
    latex_contract = build_latex_export_contract(
        run_id=run_id,
        manuscript_draft_artifact_id="revised-manuscript-draft",
        drafting_plan=latex_inputs.drafting_plan,
        drafting_report=latex_inputs.drafting_report,
        citation_registry=registry,
        citation_registry_artifact_id=(
            latex_inputs.citation_registry_artifact.id
            if latex_inputs.citation_registry_artifact is not None
            else None
        ),
        render_check_enabled=False,
    )
    latex = export_markdown_draft_to_latex(
        run_id=run_id,
        draft_markdown=refreshed,
        contract=latex_contract,
        drafting_plan=latex_inputs.drafting_plan,
        drafting_report=latex_inputs.drafting_report,
        citation_registry=registry,
    )
    if not latex.safety_report.safe:
        raise EvidenceAwareRefreshError(
            "Evidence-aware refresh produced an unsafe LaTeX export."
        )

    report = EvidenceAwareRefreshReport(
        run_id=run_id,
        refresh_enabled=True,
        refresh_backend=backend,
        refresh_status="refreshed" if sections else "no_action_needed",
        claim_evidence_map_path=claim_map_path.relative_to(root_path).as_posix(),
        proof_supported_claim_count=formal_proof_count,
        experiment_supported_claim_count=experiment_count,
        citation_supported_claim_count=citation_count,
        sections_refreshed=sections,
        proof_language_inserted=formal_proof_count > 0,
        experiment_language_inserted=experiment_count > 0,
        unsupported_claims_removed_or_downgraded=removed_or_downgraded,
        claim_support_rechecked_after_refresh=True,
        claim_evidence_map_rechecked_after_refresh=True,
        citation_safety_rechecked_after_refresh=True,
    )
    persistence = _persist_refreshed_bundle(
        run_id=run_id,
        store=store,
        ledger=ledger,
        report=report,
        markdown=refreshed,
        claim_support=claim_support,
        citation_safety=citation_safety,
        latex=latex,
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}

    map_result = persist_claim_evidence_map(
        run_id=run_id,
        root=root_path,
        store=store,
        ledger=ledger,
    )
    release_report = evaluate_full_paper_release(
        run_id=run_id,
        root=root_path,
        ledger=ledger,
        config=FullPaperReleaseGateConfig(run_id=run_id, write_report=False),
    )
    reviewer_summary = build_reviewer_bundle_summary(
        run_id=run_id,
        root=root_path,
        release_report=release_report,
        claim_evidence_map=map_result.claim_evidence_map,
    )
    release_persistence = _persist_post_refresh_release(
        run_id=run_id,
        store=store,
        ledger=ledger,
        release_report=release_report,
        reviewer_summary=reviewer_summary,
    )
    release_by_id = {artifact.id: artifact for artifact in release_persistence.artifacts}
    return EvidenceAwareRefreshResult(
        run_id=run_id,
        report=report,
        refreshed_markdown=refreshed,
        claim_evidence_map=map_result.claim_evidence_map,
        release_status=release_report.decision.status.value,
        persistence=persistence,
        release_persistence=release_persistence,
        report_artifact=by_id["evidence-aware-refresh-report"],
        manuscript_artifact=by_id["revised-manuscript-draft"],
        claim_evidence_map_artifact=map_result.map_artifact,
        reviewer_summary_artifact=release_by_id[
            "reviewer-bundle-summary-after-evidence-aware-refresh"
        ],
    )


def _refresh_markdown(
    markdown: str,
    *,
    include_proof: bool,
    include_experiment: bool,
    include_citations: bool,
) -> tuple[str, list[str]]:
    refreshed = markdown
    sections: list[str] = []
    boundary_sentences = []
    if include_citations and _CITATION_WORDING not in refreshed:
        boundary_sentences.append(_CITATION_WORDING)
    if include_proof and _PROOF_WORDING not in refreshed:
        boundary_sentences.append(_PROOF_WORDING)
    if boundary_sentences:
        refreshed = _append_section_paragraph(
            refreshed,
            "Claim and Evidence Boundaries",
            " ".join(boundary_sentences),
        )
        sections.append("Claim and Evidence Boundaries")
    if include_experiment and _EXPERIMENT_WORDING not in refreshed:
        refreshed = _append_section_paragraph(
            refreshed,
            "Demonstration Status",
            _EXPERIMENT_WORDING,
        )
        sections.append("Demonstration Status")
    return refreshed, sorted(set(sections))


def _append_section_paragraph(markdown: str, heading: str, paragraph: str) -> str:
    pattern = re.compile(
        rf"^(?P<marks>#+)\s+{re.escape(heading)}\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(markdown)
    if match is None:
        raise EvidenceAwareRefreshError(
            f"Required manuscript section is missing: {heading}."
        )
    next_heading = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE).search(
        markdown,
        match.end(),
    )
    insert_at = next_heading.start() if next_heading is not None else len(markdown)
    before = markdown[:insert_at].rstrip()
    after = markdown[insert_at:].lstrip("\n")
    return f"{before}\n\n{paragraph}\n\n{after}".rstrip() + "\n"


def _supported_link_count(claim_map: ClaimEvidenceMap, *, support_type: str) -> int:
    return sum(
        1
        for link in claim_map.links
        if link.support_status == "supported_within_scope"
        and link.support_type == support_type
    )


def _require_post_refresh_safety(*, claim_support, citation_safety, claim_map) -> None:
    counts = claim_support.post_adjudication_summary_counts or claim_support.summary_counts
    unresolved = sum(
        int(counts.get(key, 0))
        for key in (
            "missing_required_citation",
            "scope_mismatch",
            "forbidden_claim",
            "unsupported_external_claim",
            "citation_as_validation_misuse",
        )
    )
    if unresolved:
        raise EvidenceAwareRefreshError(
            "Evidence-aware refresh left unresolved claim-support violations."
        )
    if not citation_safety.safe:
        raise EvidenceAwareRefreshError(
            "Evidence-aware refresh failed citation-safety validation."
        )
    if claim_map.unsupported_non_scaffold_claim_ids:
        raise EvidenceAwareRefreshError(
            "Evidence-aware refresh created unsupported non-scaffold claims."
        )


def _persist_refreshed_bundle(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    report: EvidenceAwareRefreshReport,
    markdown: str,
    claim_support,
    citation_safety,
    latex,
) -> PersistenceResult:
    metadata = {
        "stage": "evidence_aware_refresh",
        "artifact_role": "evidence_aware_manuscript_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }
    specs = [
        ArtifactWriteSpec(
            "evidence-aware-refresh-report",
            ArtifactType.REPORT,
            report,
            "json",
            metadata,
        ),
        ArtifactWriteSpec(
            "revised-manuscript-draft",
            ArtifactType.REPORT,
            markdown,
            "markdown",
            metadata,
            filename_stem="evidence-aware-refreshed-manuscript-draft",
        ),
        ArtifactWriteSpec(
            "claim-support-audit",
            ArtifactType.REPORT,
            claim_support,
            "json",
            metadata,
            filename_stem="claim-support-audit-after-evidence-aware-refresh",
        ),
        ArtifactWriteSpec(
            "citation-safety-report",
            ArtifactType.REPORT,
            citation_safety,
            "json",
            metadata,
            filename_stem="citation-safety-report-after-evidence-aware-refresh",
        ),
        ArtifactWriteSpec(
            "revised-paper",
            ArtifactType.LATEX,
            latex.paper_tex,
            "latex",
            metadata,
            filename_stem="evidence-aware-refreshed-paper",
        ),
        ArtifactWriteSpec(
            "revised-references",
            ArtifactType.LATEX,
            latex.references_bib,
            "bib",
            metadata,
            filename_stem="evidence-aware-refreshed-references",
        ),
        ArtifactWriteSpec(
            "revised-latex-source-map",
            ArtifactType.LATEX,
            latex.source_map,
            "json",
            metadata,
            filename_stem="evidence-aware-refreshed-latex-source-map",
        ),
        ArtifactWriteSpec(
            "revised-latex-export-report",
            ArtifactType.LATEX,
            latex,
            "json",
            metadata,
            filename_stem="evidence-aware-refreshed-latex-export-report",
        ),
        ArtifactWriteSpec(
            "revised-latex-safety-report",
            ArtifactType.LATEX,
            latex.safety_report,
            "json",
            metadata,
            filename_stem="evidence-aware-refreshed-latex-safety-report",
        ),
    ]
    return persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.EVIDENCE_AWARE_REFRESH_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "refresh_backend": report.refresh_backend,
            "refresh_status": report.refresh_status,
            "sections_refreshed": report.sections_refreshed,
            "claim_support_rechecked": True,
            "citation_safety_rechecked": True,
            "claim_evidence_map_rechecked": True,
            "publication_ready": False,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
        },
    )


def _persist_post_refresh_release(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    release_report,
    reviewer_summary,
) -> PersistenceResult:
    metadata = {
        "stage": "evidence_aware_refresh",
        "artifact_role": "post_refresh_human_review_readiness_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }
    reviewer_markdown = render_reviewer_bundle_summary_markdown(reviewer_summary)
    return persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                "full-paper-release-report",
                ArtifactType.REPORT,
                release_report,
                "json",
                metadata,
                filename_stem="full-paper-release-report-after-evidence-aware-refresh",
            ),
            ArtifactWriteSpec(
                "full-paper-bundle-completeness",
                ArtifactType.REPORT,
                release_report.completeness,
                "json",
                metadata,
                filename_stem=(
                    "full-paper-bundle-completeness-after-evidence-aware-refresh"
                ),
            ),
            ArtifactWriteSpec(
                "full-paper-evidence-boundary-report",
                ArtifactType.REPORT,
                release_report.evidence_boundary,
                "json",
                metadata,
                filename_stem=(
                    "full-paper-evidence-boundary-report-after-evidence-aware-refresh"
                ),
            ),
            ArtifactWriteSpec(
                "full-paper-release-summary",
                ArtifactType.REPORT,
                render_full_paper_release_summary(release_report),
                "markdown",
                metadata,
                filename_stem="full-paper-release-summary-after-evidence-aware-refresh",
            ),
            ArtifactWriteSpec(
                "reviewer-bundle-summary-after-evidence-aware-refresh",
                ArtifactType.REPORT,
                reviewer_summary,
                "json",
                metadata,
            ),
            ArtifactWriteSpec(
                "reviewer-bundle-summary-after-evidence-aware-refresh-markdown",
                ArtifactType.REPORT,
                reviewer_markdown,
                "markdown",
                metadata,
                filename_stem="reviewer-bundle-summary-after-evidence-aware-refresh",
            ),
        ],
        action_type=ControllerActionType.EVIDENCE_AWARE_REFRESH_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "release_status": release_report.decision.status.value,
            "ready_for_human_review": release_report.decision.ready_for_human_review,
            "publication_ready": False,
            "reviewer_summary_updated": True,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
        },
    )


def _preferred_manuscript_path(reports: Path) -> Path:
    for name in (
        "evidence-aware-refreshed-manuscript-draft.md",
        "revised-manuscript-draft.md",
        "complete-manuscript-draft.md",
    ):
        path = reports / name
        if path.is_file():
            return path
    raise EvidenceAwareRefreshError("No complete or revised manuscript draft was found.")


def _read_registry(path: Path) -> CitationRegistry:
    if not path.is_file():
        raise EvidenceAwareRefreshError("Citation registry is required for final refresh.")
    return CitationRegistry.model_validate_json(path.read_text(encoding="utf-8"))


def _valid_human_review_present(path: Path) -> bool:
    if not path.is_file():
        return False
    try:
        review = HumanReviewArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return False
    return (
        review.reviewer_is_human
        and not review.llm_generated
        and review.review_status != "not_reviewed"
    )


__all__ = [
    "EvidenceAwareRefreshError",
    "EvidenceAwareRefreshResult",
    "refresh_evidence_aware_manuscript",
]
