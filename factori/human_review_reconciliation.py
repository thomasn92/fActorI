"""Deterministic reconciliation of human-review requests with a safe manuscript."""

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
from factori.human_review import HumanReviewIntakeError, load_human_review_artifact
from factori.latex_export import (
    build_latex_export_contract,
    export_markdown_draft_to_latex,
    load_latex_export_inputs,
)
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.reports import render_full_paper_release_summary
from factori.reviewer_change_requests import (
    accepted_citation_key,
    load_reviewer_change_request_sets,
)
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    CitationRegistry,
    ClaimEvidenceMap,
    ControllerActionType,
    FullPaperReleaseGateConfig,
    HumanReviewArtifact,
    HumanReviewReconciliationCycle,
    HumanReviewReconciliationIndex,
    HumanReviewReconciliationItem,
    HumanReviewReconciliationReport,
    ReviewerChangeRequest,
    ReviewerChangeRequestSet,
)

_PROBLEM_FRAMING_TEXT = (
    "The problem framing is limited to organizing the current research question as "
    "a bounded manuscript scaffold; it does not assert that the proposed "
    "representation is novel, correct, or validated."
)
_BOUNDARY_TEXT = (
    "This reconciliation preserves the distinction between manuscript context and "
    "evidence: only linked artifacts support their mapped claims, while prose, human "
    "review, retrieval adequacy, and release status remain non-evidential."
)
_LIMITATIONS_TEXT = (
    "Human-review reconciliation does not resolve missing proof, experiment, "
    "retrieval, or human-decision requirements; those remain explicit follow-up work "
    "unless corresponding scoped artifacts are present."
)
_PROOF_TEXT = (
    "The current run includes a formal proof artifact linked to a specific mapped "
    "claim; this artifact is treated as verification evidence only within its "
    "declared checker scope and does not establish novelty, empirical validity, "
    "broad correctness, or publication readiness."
)
_EXPERIMENT_TEXT = (
    "The current run includes a completed experiment artifact linked to a bounded "
    "result claim; the artifact records the reported configuration, hashes, and "
    "metrics for that run only and does not imply broad empirical validation, "
    "novelty, broad correctness, or publication readiness."
)
_CITATION_TEXT = (
    "Accepted registry citations in this draft provide bounded background context "
    "only and do not establish proof, validation, novelty, or publication readiness."
)

_FORBIDDEN_AUTHORITY_PATTERNS = (
    "publication ready",
    "publication-ready",
    "publication_ready",
    "ready for publication",
    "ready to submit",
    "approved for publication",
    "novel",
    "novelty",
    "validated",
    "validation",
    "validates",
    "scientifically validated",
    "validated method",
    "correctness",
    "is correct",
    "correctness validated",
    "establish correctness",
    "broad validation",
    "validates the method broadly",
    "remove publication_ready=false",
    "remove publication ready false",
)


class HumanReviewReconciliationError(RuntimeError):
    """Raised when human-review reconciliation cannot complete safely."""


@dataclass(frozen=True)
class HumanReviewReconciliationResult:
    """Persisted reconciliation and post-reconciliation audit artifacts."""

    run_id: str
    report: HumanReviewReconciliationReport
    reconciled_markdown: str
    claim_evidence_map: ClaimEvidenceMap
    release_status: str
    persistence: PersistenceResult
    release_persistence: PersistenceResult
    index_persistence: PersistenceResult
    report_artifact: ArtifactRef
    report_markdown_artifact: ArtifactRef
    manuscript_artifact: ArtifactRef
    claim_evidence_map_artifact: ArtifactRef
    reviewer_summary_artifact: ArtifactRef
    reconciliation_index_artifact: ArtifactRef


def reconcile_human_review(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> HumanReviewReconciliationResult:
    """Apply only bounded deterministic revisions requested by a human reviewer."""
    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    reports = run_path / "reports"
    if not run_path.is_dir():
        raise HumanReviewReconciliationError(f"No run directory found for run_id={run_id}.")
    prior_index = _read_reconciliation_index(reports)
    cycle_number = prior_index.cycle_count + 1 if prior_index is not None else 1
    review_path = reports / "human-review-artifact.json"
    if not review_path.is_file():
        raise HumanReviewReconciliationError(
            "A valid human-review artifact is required before reconciliation."
        )
    try:
        review = load_human_review_artifact(review_path)
    except HumanReviewIntakeError as exc:
        raise HumanReviewReconciliationError(
            "Persisted human-review artifact is invalid."
        ) from exc
    _require_valid_review(run_id, review)
    registry = _read_registry(reports / "citation-registry.json")
    claim_map_path = latest_claim_evidence_map_path(root_path, run_id)
    claim_map = (
        ClaimEvidenceMap.model_validate_json(claim_map_path.read_text(encoding="utf-8"))
        if claim_map_path is not None
        else build_claim_evidence_map(run_id=run_id, root=root_path)
    )
    request_sets = load_reviewer_change_request_sets(run_id=run_id, root=root_path)
    reconciled_request_set_ids = {
        cycle.request_set_id
        for cycle in (prior_index.cycles if prior_index is not None else [])
        if cycle.request_set_id is not None
    }
    pending_request_sets = [
        item
        for item in request_sets
        if item.request_set_id not in reconciled_request_set_ids
    ]
    request_set = pending_request_sets[-1] if pending_request_sets else None
    if cycle_number > 1 and request_set is None:
        raise HumanReviewReconciliationError(
            "No unreconciled structured reviewer request set is available."
        )
    markdown_path = _preferred_manuscript_path(reports)
    markdown = markdown_path.read_text(encoding="utf-8")
    if request_set is not None:
        reconciled, outcomes, sections = _apply_structured_requests(
            markdown=markdown,
            request_set=request_set,
            registry=registry,
            claim_map=claim_map,
        )
    else:
        reconciled, outcomes, sections = _apply_requested_changes(
            markdown=markdown,
            review=review,
            registry=registry,
            claim_map=claim_map,
        )

    available_evidence = {
        "proof": _supported_artifact_ids(claim_map, "formal_proof_verification") != [],
        "experiment": _supported_artifact_ids(claim_map, "experiment_result") != [],
        "human_review": True,
        "publication_ready": False,
    }
    claim_support = build_claim_support_audit(
        run_id=run_id,
        markdown=reconciled,
        citation_registry=registry,
        available_evidence_artifacts=available_evidence,
    )
    reconciled, _ = repair_confirmed_claim_support_violations(reconciled, claim_support)
    claim_support = build_claim_support_audit(
        run_id=run_id,
        markdown=reconciled,
        citation_registry=registry,
        available_evidence_artifacts=available_evidence,
    )
    citation_repair = repair_missing_required_citations_with_accepted_sources(
        reconciled,
        claim_support,
        registry,
    )
    reconciled = citation_repair.revised_markdown
    claim_support = build_claim_support_audit(
        run_id=run_id,
        markdown=reconciled,
        citation_registry=registry,
        available_evidence_artifacts=available_evidence,
    )
    citation_safety = validate_citation_usage(reconciled, registry)
    reconciled_map = build_claim_evidence_map(
        run_id=run_id,
        root=root_path,
        claim_support_audit=claim_support,
    )
    _require_post_reconciliation_safety(
        claim_support,
        citation_safety,
        reconciled_map,
        baseline_unsupported_claim_ids=set(claim_map.unsupported_non_scaffold_claim_ids),
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
        draft_markdown=reconciled,
        contract=latex_contract,
        drafting_plan=latex_inputs.drafting_plan,
        drafting_report=latex_inputs.drafting_report,
        citation_registry=registry,
    )
    if not latex.safety_report.safe:
        raise HumanReviewReconciliationError(
            "Human-review reconciliation produced an unsafe LaTeX export."
        )

    applied = sum(item.outcome.startswith("applied_") for item in outcomes)
    rejected = sum(item.outcome.startswith("rejected_") for item in outcomes)
    deferred = sum(item.outcome.startswith("deferred_") for item in outcomes)
    requires_evidence = sum(item.requires_new_evidence for item in outcomes)
    status = (
        "no_action_needed"
        if not outcomes
        else "reconciled_with_rejections_or_deferrals"
        if rejected or deferred
        else "reconciled"
    )
    report = HumanReviewReconciliationReport(
        run_id=run_id,
        cycle_number=cycle_number,
        request_set_id=request_set.request_set_id if request_set else None,
        source_manuscript_path=markdown_path.relative_to(root_path).as_posix(),
        reconciled_manuscript_path=(
            f"runs/{run_id}/reports/reconciled-manuscript-cycle-"
            f"{cycle_number:03d}.md"
        ),
        human_review_artifact_path=review_path.relative_to(root_path).as_posix(),
        review_status=review.review_status,
        reconciliation_status=status,
        requested_change_count=(
            len(request_set.requests) if request_set else len(review.requested_changes)
        ),
        applied_change_count=applied,
        rejected_change_count=rejected,
        deferred_change_count=deferred,
        requires_new_evidence_count=requires_evidence,
        sections_modified=sorted(set(sections)),
        change_outcomes=outcomes,
        remaining_requested_changes=[
            item.requested_change
            for item in outcomes
            if item.outcome.startswith(("rejected_", "deferred_"))
        ],
        claim_support_rechecked_after_reconciliation=True,
        claim_evidence_map_rechecked_after_reconciliation=True,
        citation_safety_rechecked_after_reconciliation=True,
        release_rechecked_after_reconciliation=True,
    )
    persistence = _persist_reconciled_bundle(
        run_id=run_id,
        store=store,
        ledger=ledger,
        report=report,
        markdown=reconciled,
        claim_support=claim_support,
        citation_safety=citation_safety,
        latex=latex,
        cycle_number=cycle_number,
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
        human_review_reconciliation=report,
    )
    release_persistence = _persist_post_reconciliation_release(
        run_id=run_id,
        store=store,
        ledger=ledger,
        release_report=release_report,
        reviewer_summary=reviewer_summary,
        cycle_number=cycle_number,
    )
    release_by_id = {artifact.id: artifact for artifact in release_persistence.artifacts}
    index = _build_reconciliation_index(
        run_id=run_id,
        prior_index=prior_index,
        report=report,
        ledger_tip=release_persistence.commit.commit_hash,
    )
    index_persistence = _persist_reconciliation_index(
        run_id=run_id,
        store=store,
        ledger=ledger,
        index=index,
    )
    cycle_id = f"human-review-reconciliation-cycle-{cycle_number:03d}"
    reviewer_id = (
        f"reviewer-bundle-summary-after-reconciliation-cycle-{cycle_number:03d}"
    )
    return HumanReviewReconciliationResult(
        run_id=run_id,
        report=report,
        reconciled_markdown=reconciled,
        claim_evidence_map=map_result.claim_evidence_map,
        release_status=release_report.decision.status.value,
        persistence=persistence,
        release_persistence=release_persistence,
        index_persistence=index_persistence,
        report_artifact=by_id[cycle_id],
        report_markdown_artifact=by_id[f"{cycle_id}-markdown"],
        manuscript_artifact=by_id["revised-manuscript-draft"],
        claim_evidence_map_artifact=map_result.map_artifact,
        reviewer_summary_artifact=release_by_id[reviewer_id],
        reconciliation_index_artifact=index_persistence.artifacts[0],
    )


def inspect_human_review_reconciliation(
    *, run_id: str, root: str | Path = "."
) -> dict[str, object]:
    """Inspect the persisted reconciliation report without mutating the run."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    index = _read_reconciliation_index(reports)
    if index is None:
        raise HumanReviewReconciliationError(
            f"No human-review reconciliation found for run_id={run_id}."
        )
    report_path = root_path / index.cycles[-1].reconciliation_report_path
    report = HumanReviewReconciliationReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
    return {
        **report.model_dump(mode="json"),
        "human_review_reconciliation_present": True,
        "human_review_reconciliation_cycle_count": index.cycle_count,
        "latest_reconciliation_cycle": index.latest_cycle,
        "reconciliation_index": index.model_dump(mode="json"),
        "human_review_reconciliation_report_path": report_path.relative_to(root_path).as_posix(),
        "publication_ready": False,
    }


def render_human_review_reconciliation_markdown(
    report: HumanReviewReconciliationReport,
) -> str:
    """Render a concise reviewer-facing reconciliation report."""
    lines = [
        "# Human Review Reconciliation",
        "",
        f"Run ID: `{report.run_id}`",
        f"Cycle: `{report.cycle_number}`",
        f"Request set: `{report.request_set_id or 'legacy-human-review'}`",
        f"Review status: `{report.review_status}`",
        f"Reconciliation status: `{report.reconciliation_status}`",
        f"Requested changes: `{report.requested_change_count}`",
        f"Applied changes: `{report.applied_change_count}`",
        f"Rejected changes: `{report.rejected_change_count}`",
        f"Deferred changes: `{report.deferred_change_count}`",
        f"Requires new evidence: `{report.requires_new_evidence_count}`",
        "",
        "## Change Outcomes",
    ]
    if not report.change_outcomes:
        lines.append("- No requested changes were present.")
    for item in report.change_outcomes:
        section = f"; section={item.target_section}" if item.target_section else ""
        lines.append(f"- `{item.outcome}`{section}: {item.requested_change} ({item.rationale})")
    lines.extend(
        [
            "",
            "## Boundary",
            "- Reconciliation is a manuscript revision workflow only.",
            (
                "- It does not create proof, experiment evidence, novelty, correctness, "
                "approval, or publication readiness."
            ),
            "- publication_ready: false",
            "- creates_scientific_validation: false",
            "- implies_publication_readiness: false",
            "- is_verification_evidence: false",
            "",
        ]
    )
    return "\n".join(lines)


def _apply_requested_changes(
    *,
    markdown: str,
    review: HumanReviewArtifact,
    registry: CitationRegistry,
    claim_map: ClaimEvidenceMap,
) -> tuple[str, list[HumanReviewReconciliationItem], list[str]]:
    reconciled = markdown
    outcomes: list[HumanReviewReconciliationItem] = []
    sections: list[str] = []
    proof_ids = _supported_artifact_ids(claim_map, "formal_proof_verification")
    experiment_ids = _supported_artifact_ids(claim_map, "experiment_result")
    accepted_keys = sorted(
        record.citation_key
        for record in registry.citations
        if record.accepted_for_registry and record.source_status != "rejected"
    )
    for request in review.requested_changes:
        item = _classify_requested_change(
            request=request,
            proof_ids=proof_ids,
            experiment_ids=experiment_ids,
            accepted_keys=accepted_keys,
        )
        if item.applied_text and item.target_section:
            updated = _append_section_paragraph(
                reconciled,
                item.target_section,
                item.applied_text,
            )
            if updated != reconciled:
                sections.append(item.target_section)
                reconciled = updated
        outcomes.append(item)
    return reconciled, outcomes, sections


def _apply_structured_requests(
    *,
    markdown: str,
    request_set: ReviewerChangeRequestSet,
    registry: CitationRegistry,
    claim_map: ClaimEvidenceMap,
) -> tuple[str, list[HumanReviewReconciliationItem], list[str]]:
    reconciled = markdown
    outcomes: list[HumanReviewReconciliationItem] = []
    sections: list[str] = []
    for request in request_set.requests:
        item = _classify_structured_request(
            request=request,
            registry=registry,
            claim_map=claim_map,
        )
        if item.applied_text and item.target_section:
            updated = _append_section_paragraph(
                reconciled,
                item.target_section,
                item.applied_text,
            )
            if updated != reconciled:
                sections.append(item.target_section)
                reconciled = updated
        outcomes.append(item)
    return reconciled, outcomes, sections


def _classify_structured_request(
    *,
    request: ReviewerChangeRequest,
    registry: CitationRegistry,
    claim_map: ClaimEvidenceMap,
) -> HumanReviewReconciliationItem:
    action = request.requested_action
    section = request.target_section_optional
    description = request.requested_text_optional or action.replace("_", " ")
    if action in {
        "forbidden_publication_ready_request",
        "forbidden_validation_request",
    }:
        return _structured_item(
            request,
            description,
            "rejected_forbidden_authority_claim",
            "Structured requests cannot confer publication or validation authority.",
        )
    if action == "request_new_proof_artifact":
        return _structured_item(
            request,
            description,
            "deferred_requires_proof_artifact",
            "The request requires a new scoped proof artifact.",
            requires_new_evidence=True,
        )
    if action == "request_new_experiment_artifact":
        return _structured_item(
            request,
            description,
            "deferred_requires_experiment_artifact",
            "The request requires a new scoped experiment artifact.",
            requires_new_evidence=True,
        )
    if action == "request_retrieval_expansion":
        return _structured_item(
            request,
            description,
            "deferred_requires_retrieval_expansion",
            "Retrieval expansion requires new accepted local source records.",
            requires_new_evidence=True,
        )
    if action in {"clarify_wording", "expand_section"}:
        target = section or "Introduction and Problem Framing"
        text = (
            _PROBLEM_FRAMING_TEXT
            if target.casefold() == "introduction and problem framing"
            else _BOUNDARY_TEXT
        )
        return _structured_item(
            request,
            description,
            "applied_safe_text_revision",
            "A deterministic bounded wording template was applied.",
            section=target,
            text=text,
        )
    if action == "add_boundary_language":
        return _structured_item(
            request,
            description,
            "applied_boundary_clarification",
            "Evidence-authority boundaries were strengthened without adding a claim.",
            section=section or "Claim and Evidence Boundaries",
            text=_BOUNDARY_TEXT,
        )
    if action == "add_existing_citation":
        key = accepted_citation_key(request)
        record = next(
            (
                item
                for item in registry.citations
                if item.citation_key == key and item.accepted_for_registry
            ),
            None,
        )
        if record is None:
            return _structured_item(
                request,
                description,
                "rejected_unsupported_claim",
                "The requested citation is not an accepted registry source.",
            )
        text = (
            f"Accepted registry source [@{key}] is referenced only as bounded "
            "background context and not as proof or validation."
        )
        return _structured_item(
            request,
            description,
            "applied_existing_evidence_reference",
            "The accepted registry citation was added with bounded support language.",
            section=section or "Claim and Evidence Boundaries",
            text=text,
            citation_keys=[key] if key else [],
        )
    if action in {
        "add_existing_proof_reference",
        "add_existing_experiment_reference",
    }:
        proof = action == "add_existing_proof_reference"
        evidence_id = request.target_evidence_artifact_id_optional
        matching = _matching_structured_claim_links(request, claim_map)
        field = (
            "supporting_proof_artifact_ids"
            if proof
            else "supporting_experiment_artifact_ids"
        )
        if not evidence_id or not any(
            evidence_id in getattr(link, field) for link in matching
        ):
            return _structured_item(
                request,
                description,
                "rejected_unsupported_claim",
                "The evidence artifact is outside the targeted claim scope.",
            )
        return _structured_item(
            request,
            description,
            "applied_existing_evidence_reference",
            "The existing evidence artifact was referenced only within mapped scope.",
            section=(
                section
                or ("Claim and Evidence Boundaries" if proof else "Demonstration Status")
            ),
            text=_PROOF_TEXT if proof else _EXPERIMENT_TEXT,
            artifact_ids=[evidence_id],
        )
    if action in {"remove_unsupported_claim", "downgrade_claim"}:
        matching = _matching_structured_claim_links(request, claim_map)
        if matching and all(
            link.support_status in {"unsupported", "blocked_forbidden_claim"}
            for link in matching
        ):
            return _structured_item(
                request,
                description,
                "applied_boundary_clarification",
                "The unsupported target is retained only as an explicit boundary item.",
                section=section or "Claim and Evidence Boundaries",
                text=_BOUNDARY_TEXT,
            )
        return _structured_item(
            request,
            description,
            "deferred_requires_human_decision",
            "The target is not an unsupported mapped claim that can be safely downgraded.",
        )
    return _structured_item(
        request,
        description,
        "deferred_requires_human_decision",
        "The structured action has no deterministic safe implementation.",
    )


def _matching_structured_claim_links(
    request: ReviewerChangeRequest,
    claim_map: ClaimEvidenceMap,
):
    return [
        link
        for link in claim_map.links
        if (
            request.target_claim_id_optional is None
            or link.claim_id == request.target_claim_id_optional
        )
        and (
            request.target_claim_text_hash_optional is None
            or link.claim_text_hash == request.target_claim_text_hash_optional
        )
    ]


def _structured_item(
    request: ReviewerChangeRequest,
    description: str,
    outcome: str,
    rationale: str,
    *,
    section: str | None = None,
    text: str | None = None,
    artifact_ids: list[str] | None = None,
    citation_keys: list[str] | None = None,
    requires_new_evidence: bool = False,
) -> HumanReviewReconciliationItem:
    return HumanReviewReconciliationItem(
        request_id=request.request_id,
        requested_change=description,
        outcome=outcome,
        target_section=section,
        rationale=rationale,
        applied_text=text,
        supporting_artifact_ids=artifact_ids or [],
        supporting_citation_keys=citation_keys or [],
        requires_new_evidence=requires_new_evidence,
    )


def _classify_requested_change(
    *,
    request: str,
    proof_ids: list[str],
    experiment_ids: list[str],
    accepted_keys: list[str],
) -> HumanReviewReconciliationItem:
    lower = " ".join(request.casefold().split())
    boundary_request = any(
        token in lower
        for token in (
            "evidence boundary",
            "evidence-boundary",
            "does not imply",
            "do not claim",
            "bounded context",
            "bounded background",
        )
    )
    if "rejected source" in lower or "hard-rejected source" in lower:
        return _item(
            request,
            "rejected_unsupported_claim",
            "Rejected or hard-rejected sources cannot enter manuscript support.",
        )
    if "remove evidence gap" in lower or "remove evidence gaps" in lower:
        return _item(
            request,
            "rejected_unsupported_claim",
            "Evidence gaps can be removed only by compatible persisted artifacts.",
        )
    if not boundary_request and any(phrase in lower for phrase in _FORBIDDEN_AUTHORITY_PATTERNS):
        return _item(
            request,
            "rejected_forbidden_authority_claim",
            (
                "The request would assert forbidden novelty, validation, correctness, "
                "or publication authority."
            ),
        )
    if "proof" in lower or "theorem" in lower or "proven" in lower:
        if "without" not in lower and proof_ids and any(
            token in lower for token in ("existing", "artifact", "reference", "mention", "scope")
        ):
            return _item(
                request,
                "applied_existing_evidence_reference",
                "A passed formal artifact supports only its mapped claim and checker scope.",
                section="Claim and Evidence Boundaries",
                text=_PROOF_TEXT,
                artifact_ids=proof_ids,
            )
        return _item(
            request,
            "deferred_requires_proof_artifact",
            "No matching passed formal proof artifact was identified for the requested assertion.",
            requires_new_evidence=True,
        )
    if "experiment" in lower or "empirical" in lower or "metric" in lower:
        if "without" not in lower and experiment_ids and any(
            token in lower for token in ("existing", "artifact", "reference", "mention", "scope")
        ):
            return _item(
                request,
                "applied_existing_evidence_reference",
                "A completed experiment artifact supports only its mapped bounded result.",
                section="Demonstration Status",
                text=_EXPERIMENT_TEXT,
                artifact_ids=experiment_ids,
            )
        return _item(
            request,
            "deferred_requires_experiment_artifact",
            "No matching completed experiment artifact was identified for the requested assertion.",
            requires_new_evidence=True,
        )
    if (
        "retrieval" in lower
        or "literature search" in lower
        or "source coverage" in lower
    ) and any(
        token in lower
        for token in ("expand", "expanded", "broader", "replace", "new source")
    ):
        return _item(
            request,
            "deferred_requires_retrieval_expansion",
            (
                "Retrieval expansion requires new accepted source records and cannot "
                "be synthesized by revision."
            ),
            requires_new_evidence=True,
        )
    if "citation" in lower or "accepted source" in lower or "registry source" in lower:
        if not accepted_keys:
            return _item(
                request,
                "deferred_requires_retrieval_expansion",
                "No accepted registry citation is available for the requested reference.",
                requires_new_evidence=True,
            )
        return _item(
            request,
            "applied_existing_evidence_reference",
            (
                "Only existing accepted registry citations are described as bounded "
                "background context."
            ),
            section="Claim and Evidence Boundaries",
            text=_CITATION_TEXT,
            citation_keys=accepted_keys,
        )
    if boundary_request:
        return _item(
            request,
            "applied_boundary_clarification",
            (
                "The request strengthens existing evidence-authority boundaries "
                "without adding a claim."
            ),
            section="Claim and Evidence Boundaries",
            text=_BOUNDARY_TEXT,
        )
    if "limitation" in lower:
        return _item(
            request,
            "applied_boundary_clarification",
            "The request is implemented as explicit bounded follow-up work.",
            section="Limitations",
            text=_LIMITATIONS_TEXT,
        )
    if "problem framing" in lower or "research question" in lower:
        return _item(
            request,
            "applied_safe_text_revision",
            "The framing is clarified with deterministic non-authoritative wording.",
            section="Introduction and Problem Framing",
            text=_PROBLEM_FRAMING_TEXT,
        )
    if "wording" in lower or "clarify" in lower or "readability" in lower:
        return _item(
            request,
            "applied_safe_text_revision",
            "The wording request is implemented as a conservative boundary clarification.",
            section="Claim and Evidence Boundaries",
            text=_BOUNDARY_TEXT,
        )
    return _item(
        request,
        "deferred_requires_human_decision",
        "The free-text request is too ambiguous for deterministic manuscript mutation.",
    )


def _item(
    request: str,
    outcome: str,
    rationale: str,
    *,
    section: str | None = None,
    text: str | None = None,
    artifact_ids: list[str] | None = None,
    citation_keys: list[str] | None = None,
    requires_new_evidence: bool = False,
) -> HumanReviewReconciliationItem:
    return HumanReviewReconciliationItem(
        requested_change=request,
        outcome=outcome,
        target_section=section,
        rationale=rationale,
        applied_text=text,
        supporting_artifact_ids=artifact_ids or [],
        supporting_citation_keys=citation_keys or [],
        requires_new_evidence=requires_new_evidence,
    )


def _append_section_paragraph(markdown: str, heading: str, paragraph: str) -> str:
    if paragraph in markdown:
        return markdown
    pattern = re.compile(
        rf"^(?P<marks>#+)\s+{re.escape(heading)}\s*$",
        re.IGNORECASE | re.MULTILINE,
    )
    match = pattern.search(markdown)
    if match is None:
        raise HumanReviewReconciliationError(f"Required manuscript section is missing: {heading}.")
    next_heading = re.compile(r"^#{1,6}\s+.+$", re.MULTILINE).search(markdown, match.end())
    insert_at = next_heading.start() if next_heading is not None else len(markdown)
    before = markdown[:insert_at].rstrip()
    after = markdown[insert_at:].lstrip("\n")
    return f"{before}\n\n{paragraph}\n\n{after}".rstrip() + "\n"


def _supported_artifact_ids(claim_map: ClaimEvidenceMap, support_type: str) -> list[str]:
    values: set[str] = set()
    for link in claim_map.links:
        if link.support_status != "supported_within_scope" or link.support_type != support_type:
            continue
        if support_type == "formal_proof_verification":
            values.update(link.supporting_proof_artifact_ids)
        elif support_type == "experiment_result":
            values.update(link.supporting_experiment_artifact_ids)
    return sorted(values)


def _require_valid_review(run_id: str, review: HumanReviewArtifact) -> None:
    if review.run_id != run_id:
        raise HumanReviewReconciliationError("human review run_id does not match requested run_id")
    if (
        not review.reviewer_is_human
        or review.llm_generated
        or review.review_status == "not_reviewed"
    ):
        raise HumanReviewReconciliationError(
            "persisted human-review artifact is not a valid completed human review"
        )
    if not review.checklist_items or not review.reviewer_attestation.strip():
        raise HumanReviewReconciliationError("persisted human-review artifact is incomplete")


def _require_post_reconciliation_safety(
    claim_support,
    citation_safety,
    claim_map,
    *,
    baseline_unsupported_claim_ids: set[str],
) -> None:
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
        raise HumanReviewReconciliationError(
            "Human-review reconciliation left unresolved claim-support violations."
        )
    if not citation_safety.safe:
        raise HumanReviewReconciliationError(
            "Human-review reconciliation failed citation-safety validation."
        )
    introduced_unsupported = set(
        claim_map.unsupported_non_scaffold_claim_ids
    ) - baseline_unsupported_claim_ids
    if introduced_unsupported:
        raise HumanReviewReconciliationError(
            "Human-review reconciliation created unsupported non-scaffold claims."
        )


def _persist_reconciled_bundle(
    *,
    run_id,
    store,
    ledger,
    report,
    markdown,
    claim_support,
    citation_safety,
    latex,
    cycle_number,
) -> PersistenceResult:
    metadata = {
        "stage": "human_review_reconciliation",
        "artifact_role": "human_review_reconciled_manuscript_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }
    report_markdown = render_human_review_reconciliation_markdown(report)
    cycle_id = f"human-review-reconciliation-cycle-{cycle_number:03d}"
    cycle_suffix = f"cycle-{cycle_number:03d}"
    specs = [
        ArtifactWriteSpec(
            cycle_id, ArtifactType.REPORT, report, "json", metadata
        ),
        ArtifactWriteSpec(
            f"{cycle_id}-markdown",
            ArtifactType.REPORT,
            report_markdown,
            "markdown",
            metadata,
            filename_stem=cycle_id,
        ),
        ArtifactWriteSpec(
            "revised-manuscript-draft",
            ArtifactType.REPORT,
            markdown,
            "markdown",
            metadata,
            filename_stem=f"reconciled-manuscript-{cycle_suffix}",
        ),
        ArtifactWriteSpec(
            "claim-support-audit",
            ArtifactType.REPORT,
            claim_support,
            "json",
            metadata,
            filename_stem=f"claim-support-audit-after-reconciliation-{cycle_suffix}",
        ),
        ArtifactWriteSpec(
            "citation-safety-report",
            ArtifactType.REPORT,
            citation_safety,
            "json",
            metadata,
            filename_stem=f"citation-safety-report-after-reconciliation-{cycle_suffix}",
        ),
        ArtifactWriteSpec(
            "revised-paper",
            ArtifactType.LATEX,
            latex.paper_tex,
            "latex",
            metadata,
            filename_stem=f"reconciled-paper-{cycle_suffix}",
        ),
        ArtifactWriteSpec(
            "revised-references",
            ArtifactType.LATEX,
            latex.references_bib,
            "bib",
            metadata,
            filename_stem=f"reconciled-references-{cycle_suffix}",
        ),
        ArtifactWriteSpec(
            "revised-latex-source-map",
            ArtifactType.LATEX,
            latex.source_map,
            "json",
            metadata,
            filename_stem=f"reconciled-latex-source-map-{cycle_suffix}",
        ),
        ArtifactWriteSpec(
            "revised-latex-export-report",
            ArtifactType.LATEX,
            latex,
            "json",
            metadata,
            filename_stem=f"reconciled-latex-export-report-{cycle_suffix}",
        ),
        ArtifactWriteSpec(
            "revised-latex-safety-report",
            ArtifactType.LATEX,
            latex.safety_report,
            "json",
            metadata,
            filename_stem=f"reconciled-latex-safety-report-{cycle_suffix}",
        ),
    ]
    return persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.HUMAN_REVIEW_RECONCILIATION_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "cycle_number": cycle_number,
            "request_set_id": report.request_set_id,
            "reconciliation_status": report.reconciliation_status,
            "applied_change_count": report.applied_change_count,
            "rejected_change_count": report.rejected_change_count,
            "deferred_change_count": report.deferred_change_count,
            "requires_new_evidence_count": report.requires_new_evidence_count,
            "publication_ready": False,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
        },
    )


def _persist_post_reconciliation_release(
    *, run_id, store, ledger, release_report, reviewer_summary, cycle_number
) -> PersistenceResult:
    metadata = {
        "stage": "human_review_reconciliation",
        "artifact_role": "post_reconciliation_human_review_readiness_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }
    reviewer_markdown = render_reviewer_bundle_summary_markdown(reviewer_summary)
    cycle_suffix = f"cycle-{cycle_number:03d}"
    reviewer_id = f"reviewer-bundle-summary-after-reconciliation-{cycle_suffix}"
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
                filename_stem=f"full-paper-release-report-after-reconciliation-{cycle_suffix}",
            ),
            ArtifactWriteSpec(
                "full-paper-bundle-completeness",
                ArtifactType.REPORT,
                release_report.completeness,
                "json",
                metadata,
                filename_stem=f"full-paper-bundle-completeness-after-reconciliation-{cycle_suffix}",
            ),
            ArtifactWriteSpec(
                "full-paper-evidence-boundary-report",
                ArtifactType.REPORT,
                release_report.evidence_boundary,
                "json",
                metadata,
                filename_stem=f"full-paper-evidence-boundary-report-after-reconciliation-{cycle_suffix}",
            ),
            ArtifactWriteSpec(
                "full-paper-release-summary",
                ArtifactType.REPORT,
                render_full_paper_release_summary(release_report),
                "markdown",
                metadata,
                filename_stem=f"full-paper-release-summary-after-reconciliation-{cycle_suffix}",
            ),
            ArtifactWriteSpec(
                reviewer_id,
                ArtifactType.REPORT,
                reviewer_summary,
                "json",
                metadata,
            ),
            ArtifactWriteSpec(
                f"{reviewer_id}-markdown",
                ArtifactType.REPORT,
                reviewer_markdown,
                "markdown",
                metadata,
                filename_stem=reviewer_id,
            ),
        ],
        action_type=ControllerActionType.HUMAN_REVIEW_RECONCILIATION_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "cycle_number": cycle_number,
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
    cycle_paths = sorted(reports.glob("reconciled-manuscript-cycle-*.md"))
    if cycle_paths:
        return cycle_paths[-1]
    for name in (
        "reconciled-manuscript-draft.md",
        "evidence-aware-refreshed-manuscript-draft.md",
        "revised-manuscript-draft.md",
        "complete-manuscript-draft.md",
    ):
        path = reports / name
        if path.is_file():
            return path
    raise HumanReviewReconciliationError("No complete or revised manuscript draft was found.")


def _build_reconciliation_index(
    *,
    run_id: str,
    prior_index: HumanReviewReconciliationIndex | None,
    report: HumanReviewReconciliationReport,
    ledger_tip: str,
) -> HumanReviewReconciliationIndex:
    cycle_number = report.cycle_number
    cycle_suffix = f"cycle-{cycle_number:03d}"
    report_path = f"runs/{run_id}/reports/human-review-reconciliation-{cycle_suffix}.json"
    manuscript_path = f"runs/{run_id}/reports/reconciled-manuscript-{cycle_suffix}.md"
    reviewer_path = (
        f"runs/{run_id}/reports/reviewer-bundle-summary-after-reconciliation-"
        f"{cycle_suffix}.json"
    )
    cycle = HumanReviewReconciliationCycle(
        cycle_number=cycle_number,
        request_set_id=report.request_set_id,
        reconciliation_status=report.reconciliation_status,
        reconciliation_report_path=report_path,
        reconciled_manuscript_path=manuscript_path,
        reviewer_summary_path=reviewer_path,
        applied_change_count=report.applied_change_count,
        rejected_change_count=report.rejected_change_count,
        deferred_change_count=report.deferred_change_count,
        requires_new_evidence_count=report.requires_new_evidence_count,
        unresolved_request_count=len(report.remaining_requested_changes),
        ledger_tip_after_cycle=ledger_tip,
    )
    prior_cycles = list(prior_index.cycles) if prior_index is not None else []
    return HumanReviewReconciliationIndex(
        run_id=run_id,
        latest_cycle=cycle_number,
        cycle_count=len(prior_cycles) + 1,
        cycles=[*prior_cycles, cycle],
        current_preferred_reconciled_manuscript=manuscript_path,
        current_preferred_reviewer_summary=reviewer_path,
        ledger_tip_after_latest_cycle=ledger_tip,
    )


def _persist_reconciliation_index(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    index: HumanReviewReconciliationIndex,
) -> PersistenceResult:
    artifact_id = f"human-review-reconciliation-index-{index.latest_cycle:03d}"
    metadata = {
        "stage": "human_review_reconciliation",
        "artifact_role": "reconciliation_index_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }
    return persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                artifact_id,
                ArtifactType.REPORT,
                index,
                "json",
                metadata,
            )
        ],
        action_type=ControllerActionType.HUMAN_REVIEW_RECONCILIATION_INDEX_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "latest_cycle": index.latest_cycle,
            "cycle_count": index.cycle_count,
            "publication_ready": False,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
        },
    )


def _read_reconciliation_index(
    reports: Path,
) -> HumanReviewReconciliationIndex | None:
    paths = sorted(
        path
        for path in reports.glob("human-review-reconciliation-index-*.json")
        if not path.name.endswith(".meta.json")
    )
    if not paths:
        return None
    try:
        return HumanReviewReconciliationIndex.model_validate_json(
            paths[-1].read_text(encoding="utf-8")
        )
    except ValueError as exc:
        raise HumanReviewReconciliationError(
            "Latest human-review reconciliation index is invalid."
        ) from exc


def _read_registry(path: Path) -> CitationRegistry:
    if not path.is_file():
        raise HumanReviewReconciliationError("Citation registry is required for reconciliation.")
    return CitationRegistry.model_validate_json(path.read_text(encoding="utf-8"))


__all__ = [
    "HumanReviewReconciliationError",
    "HumanReviewReconciliationResult",
    "inspect_human_review_reconciliation",
    "reconcile_human_review",
    "render_human_review_reconciliation_markdown",
]
