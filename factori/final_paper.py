"""Deterministic final-paper assembly skeleton."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.hashing import canonical_json, sha256_file
from factori.ledger import ResearchLedger
from factori.reports import render_paper_skeleton_markdown
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BlockedClaim,
    Claim,
    ClaimTable,
    ControllerActionType,
    DraftClaimPlaceholder,
    DraftSkeleton,
    LedgerCommit,
    ManuscriptPlan,
    PaperAppendix,
    PaperAssemblyReport,
    PaperSection,
    PaperSkeleton,
    ResearchObject,
    VerificationLabel,
)


class PaperAssemblyError(RuntimeError):
    """Raised when paper assembly prerequisites are missing."""


@dataclass(frozen=True)
class PaperAssemblyInputs:
    """Ledger-loaded inputs for deterministic paper assembly."""

    manuscript_plan: ManuscriptPlan
    draft_skeleton: DraftSkeleton
    claim_table: ClaimTable
    blocked_claims: list[BlockedClaim]
    research_object: ResearchObject


@dataclass(frozen=True)
class PaperAssemblyResult:
    """Result of deterministic paper skeleton assembly."""

    run_id: str
    manuscript_plan: ManuscriptPlan
    draft_skeleton: DraftSkeleton
    claim_table: ClaimTable
    blocked_claims: list[BlockedClaim]
    research_object: ResearchObject
    paper_skeleton: PaperSkeleton
    assembly_report: PaperAssemblyReport
    paper_json_artifact: ArtifactRef
    paper_markdown_artifact: ArtifactRef
    assembly_report_artifact: ArtifactRef


def load_paper_assembly_inputs(run_id: str, ledger: ResearchLedger) -> PaperAssemblyInputs:
    """Load deterministic paper assembly inputs from the ledger."""
    commits = ledger.list_commits(run_id)
    research_commit = _latest_research_object_commit(commits)
    if research_commit is None:
        raise PaperAssemblyError(
            "Research object artifacts not found; run factori package-research-object first"
        )
    manuscript_commit = _require_commit(
        commits,
        ControllerActionType.MANUSCRIPT_PLAN_BUILT,
        "Manuscript plan not found; run factori plan-manuscript first",
    )
    draft_commit = _require_commit(
        commits,
        ControllerActionType.DRAFT_SKELETON_BUILT,
        "Draft skeleton not found; run factori build-draft-skeleton first",
    )
    claim_table_commit = _require_commit(
        commits,
        ControllerActionType.CLAIM_TABLE_BUILT,
        "Claim table not found; run factori plan-manuscript first",
    )
    blocked_claims_commit = _require_commit(
        commits,
        ControllerActionType.BLOCKED_CLAIMS_IDENTIFIED,
        "Blocked claims not found; run factori plan-manuscript first",
    )
    return PaperAssemblyInputs(
        manuscript_plan=ManuscriptPlan.model_validate(manuscript_commit.payload),
        draft_skeleton=DraftSkeleton.model_validate(draft_commit.payload),
        claim_table=ClaimTable.model_validate(claim_table_commit.payload),
        blocked_claims=[
            BlockedClaim.model_validate(item)
            for item in blocked_claims_commit.payload.get("blocked_claims", [])
        ],
        research_object=ResearchObject.model_validate(research_commit.payload),
    )


def assemble_paper_skeleton(
    run_id: str,
    manuscript_plan: ManuscriptPlan,
    draft_skeleton: DraftSkeleton,
    claim_table: ClaimTable,
    research_object: ResearchObject,
    blocked_claims: list[BlockedClaim] | None = None,
) -> PaperSkeleton:
    """Assemble a deterministic paper-shaped scaffold without adding claims."""
    blocked_claims = blocked_claims or []
    blocked_ids = {claim.claim_id for claim in blocked_claims} | set(
        manuscript_plan.blocked_claim_ids
    )
    claim_by_id = {claim.claim_id: claim for claim in claim_table.claims}
    placeholders_by_id = {
        placeholder.claim_id: placeholder for placeholder in draft_skeleton.claim_placeholders
    }
    sections = [
        _paper_section(section, claim_by_id, placeholders_by_id, blocked_ids)
        for section in manuscript_plan.sections
        if section.title not in {"Title", "Appendix"}
    ]
    included_placeholders = [
        placeholder
        for section in sections
        for placeholder in section.claim_placeholders
    ]
    appendices = _appendices(
        claim_table=claim_table,
        blocked_claims=blocked_claims,
        research_object=research_object,
    )
    return PaperSkeleton(
        paper_id=f"paper-skeleton-{research_object.final_nucleus.id}",
        run_id=run_id,
        title=manuscript_plan.title,
        abstract_scaffold=draft_skeleton.abstract_stub,
        sections=sections,
        appendices=appendices,
        claim_placeholders=sorted(
            included_placeholders,
            key=lambda placeholder: placeholder.claim_id,
        ),
        provenance_refs=_provenance_refs(research_object),
    )


def build_paper_assembly_report(
    *,
    paper_skeleton: PaperSkeleton,
    manuscript_plan: ManuscriptPlan,
    draft_skeleton: DraftSkeleton,
    claim_table: ClaimTable,
    blocked_claims: list[BlockedClaim],
    research_object: ResearchObject,
) -> PaperAssemblyReport:
    """Compute deterministic paper assembly readiness."""
    warnings: list[str] = []
    included_claim_ids = {placeholder.claim_id for placeholder in paper_skeleton.claim_placeholders}
    included_claims = [
        claim for claim in claim_table.claims if claim.claim_id in included_claim_ids
    ]
    blocked_ids = {claim.claim_id for claim in blocked_claims} | set(
        manuscript_plan.blocked_claim_ids
    )
    planned_main_claims = [
        claim
        for claim in claim_table.claims
        if claim.claim_id in manuscript_plan.allowed_claim_ids
        and claim.claim_id not in blocked_ids
        and claim.allowed_in_main_text
    ]
    for claim in included_claims:
        if claim.allowed_in_main_text and not claim.evidence_artifact_ids:
            warnings.append(f"{claim.claim_id}: missing evidence links for main claim")
        if _has_synthetic_real_world_inflation(claim):
            warnings.append(f"{claim.claim_id}: synthetic evidence framed as real-world validation")
        if _uses_presentation_evidence(claim):
            warnings.append(f"{claim.claim_id}: presentation artifact listed as evidence")
    for claim in planned_main_claims:
        if claim.claim_label == VerificationLabel.UNSUPPORTED:
            warnings.append(f"{claim.claim_id}: unsupported claim appears in main text")

    if not claim_table.claims:
        warnings.append("claim table is empty")
    if not draft_skeleton.section_stubs:
        warnings.append("draft skeleton has no sections")
    if not research_object.run_id:
        warnings.append("research object is missing")

    return PaperAssemblyReport(
        sections_count=len(paper_skeleton.sections),
        claims_included=len(included_claims),
        claims_blocked=len(blocked_claims),
        evidence_links_count=sum(len(claim.evidence_artifact_ids) for claim in included_claims),
        warnings=sorted(set(warnings)),
        ready_for_polished_prose=not warnings,
    )


def run_paper_assembly(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PaperAssemblyResult:
    """Run deterministic paper skeleton assembly and write ledgered artifacts."""
    store.init_run(run_id)
    inputs = load_paper_assembly_inputs(run_id, ledger)
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.PAPER_ASSEMBLY_STARTED,
        payload={
            "run_id": run_id,
            "research_object": inputs.research_object.run_id,
            "manuscript_plan": inputs.manuscript_plan.plan_id,
        },
    )
    paper_skeleton = assemble_paper_skeleton(
        run_id,
        inputs.manuscript_plan,
        inputs.draft_skeleton,
        inputs.claim_table,
        inputs.research_object,
        inputs.blocked_claims,
    )
    assembly_report = build_paper_assembly_report(
        paper_skeleton=paper_skeleton,
        manuscript_plan=inputs.manuscript_plan,
        draft_skeleton=inputs.draft_skeleton,
        claim_table=inputs.claim_table,
        blocked_claims=inputs.blocked_claims,
        research_object=inputs.research_object,
    )
    paper_json_artifact = _write_research_object_json(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id="paper-skeleton",
        payload=paper_skeleton,
        action_type=ControllerActionType.PAPER_SKELETON_WRITTEN,
        metadata={"package_part": "paper_skeleton"},
    )
    paper_markdown_artifact = _write_research_object_markdown(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id="paper-skeleton",
        markdown=render_paper_skeleton_markdown(paper_skeleton=paper_skeleton),
        action_type=ControllerActionType.PAPER_SKELETON_WRITTEN,
        metadata={"package_part": "paper_skeleton_markdown"},
    )
    assembly_report_artifact = _write_research_object_json(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id="paper-assembly-report",
        payload=assembly_report,
        action_type=ControllerActionType.PAPER_ASSEMBLY_REPORT_WRITTEN,
        metadata={"package_part": "paper_assembly_report"},
    )
    return PaperAssemblyResult(
        run_id=run_id,
        manuscript_plan=inputs.manuscript_plan,
        draft_skeleton=inputs.draft_skeleton,
        claim_table=inputs.claim_table,
        blocked_claims=inputs.blocked_claims,
        research_object=inputs.research_object,
        paper_skeleton=paper_skeleton,
        assembly_report=assembly_report,
        paper_json_artifact=paper_json_artifact,
        paper_markdown_artifact=paper_markdown_artifact,
        assembly_report_artifact=assembly_report_artifact,
    )


def _paper_section(
    section,
    claim_by_id: dict[str, Claim],
    placeholders_by_id: dict[str, DraftClaimPlaceholder],
    blocked_ids: set[str],
) -> PaperSection:
    placeholders: list[DraftClaimPlaceholder] = []
    section_warnings: list[str] = []
    for claim_id in section.allowed_claim_ids:
        claim = claim_by_id.get(claim_id)
        if claim is None or claim.claim_id in blocked_ids:
            continue
        if not _claim_allowed_in_section(claim, section.title):
            if claim.claim_label == VerificationLabel.UNSUPPORTED:
                section_warnings.append(
                    f"{claim.claim_id}: unsupported claim excluded from main results"
                )
            continue
        placeholders.append(
            placeholders_by_id.get(claim_id) or _placeholder_from_claim(claim)
        )
    evidence_ids = sorted(
        {
            evidence_id
            for placeholder in placeholders
            for evidence_id in placeholder.evidence_artifact_ids
        }
    )
    return PaperSection(
        section_id=section.section_id,
        title=section.title,
        purpose=" ".join(section.bullets),
        claim_placeholders=sorted(placeholders, key=lambda item: item.claim_id),
        evidence_artifact_ids=evidence_ids,
        warnings=section_warnings,
    )


def _claim_allowed_in_section(claim: Claim, section_title: str) -> bool:
    if claim.claim_label == VerificationLabel.UNSUPPORTED:
        return False
    if claim.claim_label == VerificationLabel.CONJECTURE:
        return section_title in {"Theory or Synthetic Experiments", "Appendix"}
    if claim.claim_label == VerificationLabel.NEGATIVE_RESULT:
        return section_title in {"Negative Results or Boundary Cases", "Results", "Limitations"}
    if claim.claim_label == VerificationLabel.LIMITATION:
        return section_title == "Limitations"
    if claim.claim_label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
        return section_title in {"Theory or Synthetic Experiments", "Results"}
    return True


def _appendices(
    *,
    claim_table: ClaimTable,
    blocked_claims: list[BlockedClaim],
    research_object: ResearchObject,
) -> list[PaperAppendix]:
    claim_lines = [
        (
            f"{claim.claim_id}: label={claim.claim_label.value}; "
            f"candidate_id={claim.candidate_id}; "
            f"evidence={','.join(claim.evidence_artifact_ids) or 'none'}; "
            f"allowed_section={claim.allowed_section}"
        )
        for claim in sorted(claim_table.claims, key=lambda item: item.claim_id)
    ] or ["no claims available"]
    blocked_lines = [
        (
            f"{claim.claim_id}: label={claim.claim_label.value}; "
            f"candidate_id={claim.candidate_id}; reason={claim.blocked_reason}"
        )
        for claim in sorted(blocked_claims, key=lambda item: item.claim_id)
    ] or ["none"]
    provenance_lines = [
        f"ledger_summary_ref={_artifact_path(research_object.ledger_summary_ref)}",
        f"artifact_manifest_ref={_artifact_path(research_object.artifact_manifest_ref)}",
        f"reproducibility_manifest_ref={_artifact_path(research_object.reproducibility_manifest_ref)}",
        "The ledger remains the source of truth; this paper skeleton is not provenance.",
    ]
    failed_lines = [
        f"branch_outcomes_ref={_artifact_path(research_object.branch_outcomes_ref)}",
        "Deferred, failed, rejected, and pruned branches remain in branch-outcomes.json.",
    ]
    return [
        PaperAppendix(
            appendix_id="appendix-a-claim-evidence",
            title="Appendix A: Claim/Evidence Table",
            content_lines=claim_lines,
            claim_ids=[claim.claim_id for claim in claim_table.claims],
        ),
        PaperAppendix(
            appendix_id="appendix-b-blocked-claims",
            title="Appendix B: Blocked or Downgraded Claims",
            content_lines=blocked_lines,
            claim_ids=[claim.claim_id for claim in blocked_claims],
        ),
        PaperAppendix(
            appendix_id="appendix-c-provenance",
            title="Appendix C: Provenance and Reproducibility",
            content_lines=provenance_lines,
            artifact_ref_ids=[
                ref.id
                for ref in [
                    research_object.ledger_summary_ref,
                    research_object.artifact_manifest_ref,
                    research_object.reproducibility_manifest_ref,
                ]
                if ref is not None
            ],
        ),
        PaperAppendix(
            appendix_id="appendix-d-branch-outcomes",
            title="Appendix D: Failed, Deferred, and Pruned Branches",
            content_lines=failed_lines,
            artifact_ref_ids=[
                research_object.branch_outcomes_ref.id
                if research_object.branch_outcomes_ref is not None
                else "missing"
            ],
        ),
    ]


def _placeholder_from_claim(claim: Claim) -> DraftClaimPlaceholder:
    evidence = ", ".join(claim.evidence_artifact_ids) or "no evidence"
    return DraftClaimPlaceholder(
        claim_id=claim.claim_id,
        candidate_id=claim.candidate_id,
        claim_label=claim.claim_label,
        placeholder_text=(
            f"[{claim.claim_label.value} claim placeholder: claim_id={claim.claim_id}; "
            f"evidence={evidence}]"
        ),
        evidence_artifact_ids=claim.evidence_artifact_ids,
        allowed_section=claim.allowed_section,
        warnings=[],
    )


def _provenance_refs(research_object: ResearchObject) -> dict[str, ArtifactRef]:
    refs = {
        "manuscript_plan": research_object.manuscript_plan_ref,
        "draft_skeleton": research_object.draft_skeleton_ref,
        "claim_table": research_object.claim_table_ref,
        "blocked_claims": research_object.blocked_claims_ref,
        "checklist": research_object.checklist_ref,
    }
    optional_refs = {
        "artifact_manifest": research_object.artifact_manifest_ref,
        "ledger_summary": research_object.ledger_summary_ref,
        "branch_outcomes": research_object.branch_outcomes_ref,
        "reproducibility_manifest": research_object.reproducibility_manifest_ref,
    }
    refs.update({key: value for key, value in optional_refs.items() if value is not None})
    return refs


def _has_synthetic_real_world_inflation(claim: Claim) -> bool:
    if claim.claim_label != VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
        return False
    text = claim.claim_text.lower()
    return any(
        marker in text
        for marker in [
            "real-world",
            "real world",
            "real markets",
            "real mobility",
            "public data",
            "user data",
        ]
    )


def _uses_presentation_evidence(claim: Claim) -> bool:
    evidence_types = {evidence_type.lower() for evidence_type in claim.evidence_types}
    return bool(evidence_types & {"latex", "markdown", "presentation"})


def _artifact_path(artifact: ArtifactRef | None) -> str:
    return artifact.path if artifact is not None else "missing"


def _write_research_object_json(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    artifact_id: str,
    payload: Any,
    action_type: ControllerActionType,
    metadata: dict[str, Any],
) -> ArtifactRef:
    path = _research_object_path(store, run_id, artifact_id, "json")
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    artifact = _artifact_ref(store, artifact_id, path, {"format": "json", **metadata})
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=action_type,
        payload=_payload_for_commit(payload),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_research_object_markdown(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    artifact_id: str,
    markdown: str,
    action_type: ControllerActionType,
    metadata: dict[str, Any],
) -> ArtifactRef:
    path = _research_object_path(store, run_id, artifact_id, "md")
    path.write_text(markdown, encoding="utf-8")
    artifact = _artifact_ref(store, artifact_id, path, {"format": "markdown", **metadata})
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=action_type,
        payload={"artifact_id": artifact_id, "format": "markdown", **metadata},
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _research_object_path(
    store: ArtifactStore,
    run_id: str,
    artifact_id: str,
    extension: str,
) -> Path:
    directory = store.run_path(run_id) / "research_object"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{artifact_id}.{extension}"


def _artifact_ref(
    store: ArtifactStore,
    artifact_id: str,
    path: Path,
    metadata: dict[str, Any],
) -> ArtifactRef:
    return ArtifactRef(
        id=artifact_id,
        type=ArtifactType.REPORT,
        path=path.relative_to(store.root).as_posix(),
        content_hash=sha256_file(path),
        metadata={"stage": "paper_assembly", "fake": True, **metadata},
    )


def _payload_for_commit(payload: Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")
    if isinstance(payload, dict):
        return payload
    return {"payload": payload}


def _latest_research_object_commit(commits: list[LedgerCommit]) -> LedgerCommit | None:
    for commit in reversed(commits):
        if (
            commit.action_type == ControllerActionType.RESEARCH_OBJECT_WRITTEN
            and "final_nucleus" in commit.payload
        ):
            return commit
    return None


def _require_commit(
    commits: list[LedgerCommit],
    action_type: ControllerActionType,
    message: str,
) -> LedgerCommit:
    for commit in reversed(commits):
        if commit.action_type == action_type:
            return commit
    raise PaperAssemblyError(message)
