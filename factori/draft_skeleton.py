"""Deterministic draft skeleton generation."""

from __future__ import annotations

from dataclasses import dataclass

from factori.artifacts import ArtifactStore
from factori.checklist import build_manuscript_checklist
from factori.ledger import ResearchLedger
from factori.reports import render_draft_skeleton_markdown, render_manuscript_checklist_markdown
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BlockedClaim,
    Claim,
    ClaimTable,
    ControllerActionType,
    DraftClaimPlaceholder,
    DraftSection,
    DraftSkeleton,
    ManuscriptChecklist,
    ManuscriptPlan,
    VerificationLabel,
)


class DraftSkeletonError(RuntimeError):
    """Raised when draft skeleton prerequisites are missing."""


@dataclass(frozen=True)
class DraftSkeletonResult:
    """Result of deterministic draft skeleton generation."""

    run_id: str
    manuscript_plan: ManuscriptPlan
    claim_table: ClaimTable
    blocked_claims: list[BlockedClaim]
    draft_skeleton: DraftSkeleton
    checklist: ManuscriptChecklist
    draft_json_artifact: ArtifactRef
    draft_markdown_artifact: ArtifactRef
    checklist_json_artifact: ArtifactRef
    checklist_markdown_artifact: ArtifactRef


def load_manuscript_planning_artifacts(
    run_id: str,
    ledger: ResearchLedger,
) -> tuple[ManuscriptPlan, ClaimTable, list[BlockedClaim]]:
    """Load manuscript planning artifacts from the ledger."""
    commits = ledger.list_commits(run_id)
    plan_commit = _latest_commit(commits, ControllerActionType.MANUSCRIPT_PLAN_BUILT)
    claim_table_commit = _latest_commit(commits, ControllerActionType.CLAIM_TABLE_BUILT)
    blocked_claims_commit = _latest_commit(
        commits,
        ControllerActionType.BLOCKED_CLAIMS_IDENTIFIED,
    )
    if plan_commit is None or claim_table_commit is None or blocked_claims_commit is None:
        raise DraftSkeletonError(
            "Manuscript planning artifacts not found; run factori plan-manuscript first"
        )
    manuscript_plan = ManuscriptPlan.model_validate(plan_commit.payload)
    claim_table = ClaimTable.model_validate(claim_table_commit.payload)
    blocked_claims = [
        BlockedClaim.model_validate(item)
        for item in blocked_claims_commit.payload.get("blocked_claims", [])
    ]
    return manuscript_plan, claim_table, blocked_claims


def build_draft_skeleton(
    manuscript_plan: ManuscriptPlan,
    claim_table: ClaimTable,
    blocked_claims: list[BlockedClaim],
) -> DraftSkeleton:
    """Build a deterministic draft scaffold from the manuscript plan."""
    claim_by_id = {claim.claim_id: claim for claim in claim_table.claims}
    allowed_claim_ids = set(manuscript_plan.allowed_claim_ids)
    evidence_links = [
        link for link in claim_table.evidence_links if link.claim_id in allowed_claim_ids
    ]
    section_stubs = [
        _section_stub(section, claim_by_id)
        for section in manuscript_plan.sections
    ]
    placeholders = [
        _claim_placeholder(claim_by_id[claim_id])
        for claim_id in sorted(allowed_claim_ids)
        if claim_id in claim_by_id
    ]
    blocked_warnings = [
        _blocked_warning(blocked)
        for blocked in sorted(blocked_claims, key=lambda item: item.claim_id)
    ]
    skeleton = DraftSkeleton(
        skeleton_id=f"draft-skeleton-{manuscript_plan.final_nucleus_id}",
        title=manuscript_plan.title,
        abstract_stub=(
            "Abstract stub: summarize only the allowed labeled claims and cite their "
            "evidence placeholders."
        ),
        section_stubs=section_stubs,
        claim_placeholders=placeholders,
        evidence_links=evidence_links,
        blocked_claim_warnings=blocked_warnings,
    )
    checklist = build_manuscript_checklist(skeleton, claim_table, blocked_claims)
    return skeleton.model_copy(update={"checklist": checklist})


def run_draft_skeleton_generation(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> DraftSkeletonResult:
    """Run deterministic draft skeleton generation and write artifacts."""
    store.init_run(run_id)
    manuscript_plan, claim_table, blocked_claims = load_manuscript_planning_artifacts(
        run_id,
        ledger,
    )
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.DRAFT_SKELETON_STARTED,
        payload={
            "manuscript_plan": manuscript_plan.plan_id,
            "claims_total": len(claim_table.claims),
            "blocked_claims": len(blocked_claims),
        },
    )
    draft_skeleton = build_draft_skeleton(manuscript_plan, claim_table, blocked_claims)
    if draft_skeleton.checklist is None:
        checklist = build_manuscript_checklist(draft_skeleton, claim_table, blocked_claims)
        draft_skeleton = draft_skeleton.model_copy(update={"checklist": checklist})
    checklist = draft_skeleton.checklist

    draft_json_artifact = _write_draft_json(run_id, draft_skeleton, store, ledger)
    checklist_json_artifact = _write_checklist_json(run_id, checklist, store, ledger)
    draft_markdown_artifact = _write_draft_markdown(
        run_id,
        draft_skeleton,
        claim_table,
        blocked_claims,
        store,
        ledger,
    )
    checklist_markdown_artifact = _write_checklist_markdown(
        run_id,
        checklist,
        store,
        ledger,
    )
    return DraftSkeletonResult(
        run_id=run_id,
        manuscript_plan=manuscript_plan,
        claim_table=claim_table,
        blocked_claims=blocked_claims,
        draft_skeleton=draft_skeleton,
        checklist=checklist,
        draft_json_artifact=draft_json_artifact,
        draft_markdown_artifact=draft_markdown_artifact,
        checklist_json_artifact=checklist_json_artifact,
        checklist_markdown_artifact=checklist_markdown_artifact,
    )


def _section_stub(section, claim_by_id: dict[str, Claim]) -> DraftSection:
    required_evidence_ids = sorted(
        {
            evidence_id
            for claim_id in section.allowed_claim_ids
            for evidence_id in claim_by_id.get(claim_id, _empty_claim()).evidence_artifact_ids
        }
    )
    paragraph_placeholders = (
        [
            _paragraph_placeholder(claim_by_id[claim_id])
            for claim_id in section.allowed_claim_ids
            if claim_id in claim_by_id
        ]
        or [f"[Section scaffold: {section.title}; no claim placeholders assigned yet.]"]
    )
    warnings = [
        "No evidence IDs required for this section."
        if not required_evidence_ids and section.allowed_claim_ids
        else ""
    ]
    return DraftSection(
        section_id=section.section_id,
        section_title=section.title,
        section_purpose=" ".join(section.bullets),
        allowed_claim_ids=section.allowed_claim_ids,
        required_evidence_ids=required_evidence_ids,
        paragraph_placeholders=paragraph_placeholders,
        warnings=[warning for warning in warnings if warning],
    )


def _blocked_warning(blocked: BlockedClaim) -> str:
    if blocked.suggested_section:
        return (
            f"{blocked.claim_id}: {blocked.blocked_reason} "
            f"(suggested section: {blocked.suggested_section})"
        )
    return f"{blocked.claim_id}: {blocked.blocked_reason}"


def _claim_placeholder(claim: Claim) -> DraftClaimPlaceholder:
    return DraftClaimPlaceholder(
        claim_id=claim.claim_id,
        candidate_id=claim.candidate_id,
        claim_label=claim.claim_label,
        placeholder_text=_placeholder_text(claim),
        evidence_artifact_ids=claim.evidence_artifact_ids,
        allowed_section=claim.allowed_section,
        warnings=_placeholder_warnings(claim),
    )


def _paragraph_placeholder(claim: Claim) -> str:
    evidence = ", ".join(claim.evidence_artifact_ids) or "no evidence"
    return (
        f"[Paragraph placeholder: claim_id={claim.claim_id}; "
        f"label={claim.claim_label.value}; evidence={evidence}]"
    )


def _placeholder_text(claim: Claim) -> str:
    evidence = ", ".join(claim.evidence_artifact_ids) or "no evidence"
    if claim.claim_label == VerificationLabel.LEAN_VERIFIED:
        return (
            f"[LeanVerified claim placeholder: claim_id={claim.claim_id} "
            f"evidence={evidence}]"
        )
    if claim.claim_label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
        return (
            f"[SyntheticExperimentVerified claim placeholder: claim_id={claim.claim_id} "
            f"synthetic-only evidence={evidence}]"
        )
    if claim.claim_label == VerificationLabel.CONJECTURE:
        return f"[Conjecture placeholder: claim_id={claim.claim_id}; must not be stated as theorem]"
    if claim.claim_label == VerificationLabel.NEGATIVE_RESULT:
        return (
            f"[NegativeResult placeholder: claim_id={claim.claim_id}; "
            "present as boundary or failure mode]"
        )
    if claim.claim_label == VerificationLabel.LIMITATION:
        return f"[Limitation placeholder: claim_id={claim.claim_id}; present as limitation]"
    return f"[Unsupported placeholder: claim_id={claim.claim_id}; exclude from main results]"


def _placeholder_warnings(claim: Claim) -> list[str]:
    warnings: list[str] = []
    if claim.claim_label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
        warnings.append("Synthetic evidence must not be described as real-world validation.")
    if claim.claim_label == VerificationLabel.CONJECTURE:
        warnings.append("Conjecture must not be stated as theorem.")
    if claim.claim_label == VerificationLabel.UNSUPPORTED:
        warnings.append("Unsupported claims must not appear in main results.")
    if any(evidence_type == "latex" for evidence_type in claim.evidence_types):
        warnings.append("LaTeX artifacts are presentation artifacts, not evidence.")
    return warnings


def _write_draft_json(
    run_id: str,
    draft_skeleton: DraftSkeleton,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id="draft-skeleton",
        artifact_type=ArtifactType.REPORT,
        data=draft_skeleton,
        metadata={"stage": "draft_skeleton", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.DRAFT_SKELETON_BUILT,
        payload=draft_skeleton.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_checklist_json(
    run_id: str,
    checklist: ManuscriptChecklist,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id="manuscript-checklist",
        artifact_type=ArtifactType.REPORT,
        data=checklist,
        metadata={"stage": "draft_skeleton", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.MANUSCRIPT_CHECKLIST_BUILT,
        payload=checklist.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_draft_markdown(
    run_id: str,
    draft_skeleton: DraftSkeleton,
    claim_table: ClaimTable,
    blocked_claims: list[BlockedClaim],
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    markdown = render_draft_skeleton_markdown(
        run_id=run_id,
        draft_skeleton=draft_skeleton,
        claim_table=claim_table,
        blocked_claims=blocked_claims,
    )
    artifact = store.write_markdown(
        run_id=run_id,
        artifact_id="draft-skeleton",
        artifact_type=ArtifactType.REPORT,
        markdown=markdown,
        metadata={"stage": "draft_skeleton", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.DRAFT_SKELETON_REPORT_WRITTEN,
        payload={
            "sections": len(draft_skeleton.section_stubs),
            "claim_placeholders": len(draft_skeleton.claim_placeholders),
        },
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_checklist_markdown(
    run_id: str,
    checklist: ManuscriptChecklist,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    markdown = render_manuscript_checklist_markdown(run_id=run_id, checklist=checklist)
    artifact = store.write_markdown(
        run_id=run_id,
        artifact_id="manuscript-checklist",
        artifact_type=ArtifactType.REPORT,
        markdown=markdown,
        metadata={"stage": "draft_skeleton", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.MANUSCRIPT_CHECKLIST_REPORT_WRITTEN,
        payload={
            "checklist_items": len(checklist.items),
            "checklist_failures": checklist.failures_count,
        },
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _latest_commit(commits, action_type: ControllerActionType):
    return next(
        (
            commit
            for commit in reversed(commits)
            if commit.action_type == action_type
        ),
        None,
    )


def _empty_claim() -> Claim:
    return Claim(
        claim_id="empty",
        claim_text="empty placeholder",
        claim_label=VerificationLabel.UNSUPPORTED,
        candidate_id="empty",
        allowed_in_main_text=False,
        allowed_section="Future Work",
        reason="empty placeholder",
    )
