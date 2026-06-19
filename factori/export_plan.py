"""Deterministic export-preparation orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from factori.artifacts import ArtifactStore
from factori.latex_plan import build_latex_export_plan
from factori.ledger import ResearchLedger
from factori.prose_contract import build_prose_generation_contract
from factori.reports import render_export_readiness_report_markdown
from factori.schemas import (
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactRef,
    ArtifactType,
    Claim,
    ClaimTable,
    ControllerActionType,
    DraftSkeleton,
    ExportBundleManifest,
    ExportClaimMap,
    ExportEvidencePlaceholder,
    ExportReadinessReport,
    ExportSectionMap,
    FinalAuditReport,
    LatexExportPlan,
    LedgerCommit,
    ManuscriptPlan,
    PaperSkeleton,
    ProseGenerationContract,
    ReleaseGateDecision,
    ReleaseGateStatus,
    VerificationLabel,
)


class ExportPreparationError(RuntimeError):
    """Raised when export-preparation prerequisites are missing."""


@dataclass(frozen=True)
class ExportPreparationInputs:
    """Ledger-loaded inputs for deterministic export preparation."""

    paper_skeleton: PaperSkeleton
    claim_table: ClaimTable
    manuscript_plan: ManuscriptPlan
    draft_skeleton: DraftSkeleton
    artifact_manifest: ArtifactManifest
    final_audit_report: FinalAuditReport
    release_gate_decision: ReleaseGateDecision


@dataclass(frozen=True)
class ExportPreparationResult:
    """Result of deterministic export preparation."""

    run_id: str
    prose_contract: ProseGenerationContract
    latex_plan: LatexExportPlan
    section_map: list[ExportSectionMap]
    claim_map: list[ExportClaimMap]
    readiness_report: ExportReadinessReport
    bundle_manifest: ExportBundleManifest
    prose_contract_artifact: ArtifactRef
    latex_plan_artifact: ArtifactRef
    section_map_artifact: ArtifactRef
    claim_map_artifact: ArtifactRef
    readiness_json_artifact: ArtifactRef
    readiness_markdown_artifact: ArtifactRef
    bundle_manifest_artifact: ArtifactRef


def load_export_preparation_inputs(
    run_id: str,
    ledger: ResearchLedger,
) -> ExportPreparationInputs:
    """Load export-preparation inputs from the ledger."""
    commits = ledger.list_commits(run_id)
    audit_commit = _latest_commit_with_key(
        commits,
        ControllerActionType.FINAL_AUDIT_REPORT_WRITTEN,
        "checks",
    )
    if audit_commit is None:
        raise ExportPreparationError(
            "Final audit artifacts not found; run factori final-audit first"
        )
    release_commit = _require_commit_with_key(
        commits,
        ControllerActionType.RELEASE_GATE_DECIDED,
        "status",
        "Release gate decision not found; run factori final-audit first",
    )
    paper_commit = _require_commit_with_key(
        commits,
        ControllerActionType.PAPER_SKELETON_WRITTEN,
        "paper_id",
        "Paper skeleton not found; run factori assemble-paper-skeleton first",
    )
    claim_table_commit = _require_commit(
        commits,
        ControllerActionType.CLAIM_TABLE_BUILT,
        "Claim table not found; run factori plan-manuscript first",
    )
    manuscript_plan_commit = _require_commit(
        commits,
        ControllerActionType.MANUSCRIPT_PLAN_BUILT,
        "Manuscript plan not found; run factori plan-manuscript first",
    )
    draft_skeleton_commit = _require_commit(
        commits,
        ControllerActionType.DRAFT_SKELETON_BUILT,
        "Draft skeleton not found; run factori build-draft-skeleton first",
    )
    artifact_manifest_commit = _require_commit(
        commits,
        ControllerActionType.ARTIFACT_MANIFEST_WRITTEN,
        "Artifact manifest not found; run factori package-research-object first",
    )
    return ExportPreparationInputs(
        paper_skeleton=PaperSkeleton.model_validate(paper_commit.payload),
        claim_table=ClaimTable.model_validate(claim_table_commit.payload),
        manuscript_plan=ManuscriptPlan.model_validate(manuscript_plan_commit.payload),
        draft_skeleton=DraftSkeleton.model_validate(draft_skeleton_commit.payload),
        artifact_manifest=ArtifactManifest.model_validate(artifact_manifest_commit.payload),
        final_audit_report=FinalAuditReport.model_validate(audit_commit.payload),
        release_gate_decision=ReleaseGateDecision.model_validate(release_commit.payload),
    )


def build_export_section_map(
    paper_skeleton: PaperSkeleton,
    manuscript_plan: ManuscriptPlan,
    draft_skeleton: DraftSkeleton,
) -> list[ExportSectionMap]:
    """Build deterministic section-to-source mapping."""
    plan_by_title = {section.title: section for section in manuscript_plan.sections}
    draft_by_title = {
        section.section_title: section for section in draft_skeleton.section_stubs
    }
    maps: list[ExportSectionMap] = []
    for section in paper_skeleton.sections:
        plan_section = plan_by_title.get(section.title)
        draft_section = draft_by_title.get(section.title)
        maps.append(
            ExportSectionMap(
                section_id=section.section_id,
                section_title=section.title,
                source_plan_section_id=plan_section.section_id if plan_section else None,
                source_draft_section_id=draft_section.section_id if draft_section else None,
                claim_ids=[placeholder.claim_id for placeholder in section.claim_placeholders],
                evidence_artifact_ids=section.evidence_artifact_ids,
                warnings=section.warnings,
            )
        )
    return maps


def build_export_claim_map(
    claim_table: ClaimTable,
    artifact_manifest: ArtifactManifest,
) -> list[ExportClaimMap]:
    """Build deterministic claim-to-evidence maps."""
    artifact_by_id = {entry.artifact_id: entry for entry in artifact_manifest.artifacts}
    return [
        _claim_map_entry(claim, artifact_by_id)
        for claim in sorted(claim_table.claims, key=lambda item: item.claim_id)
    ]


def evidence_placeholders_from_claim_map(
    claim_map: list[ExportClaimMap],
) -> list[ExportEvidencePlaceholder]:
    """Build deterministic evidence/citation placeholder plan from claim maps."""
    placeholders: dict[str, ExportEvidencePlaceholder] = {}
    for claim in claim_map:
        for artifact_id in claim.evidence_artifact_ids:
            placeholders.setdefault(
                artifact_id,
                ExportEvidencePlaceholder(
                    placeholder_id=f"evidence-{artifact_id}",
                    artifact_id=artifact_id,
                    artifact_type=_artifact_type_from_claim_map(claim, artifact_id),
                    content_hash=claim.evidence_hashes.get(artifact_id),
                    producing_commit_hash=claim.producing_commit_hashes.get(artifact_id),
                    placeholder_text=f"[evidence:{artifact_id}]",
                    is_verification_evidence=artifact_id in claim.evidence_hashes,
                ),
            )
    return [placeholders[key] for key in sorted(placeholders)]


def evaluate_export_readiness(
    prose_contract: ProseGenerationContract,
    latex_plan: LatexExportPlan,
    final_audit_report: FinalAuditReport,
    release_gate_decision: ReleaseGateDecision,
    claim_map: list[ExportClaimMap] | None = None,
) -> ExportReadinessReport:
    """Evaluate deterministic export readiness."""
    claim_map = claim_map or []
    blocking_reasons: list[str] = []
    warnings: list[str] = []
    if final_audit_report.blocking_failures_count > 0:
        blocking_reasons.append("final audit has Blocking failures")
    if not release_gate_decision.ready_for_polished_prose:
        blocking_reasons.append("release gate is not ready for polished prose")
    if any(
        not evidence_ids for claim_id, evidence_ids in prose_contract.claim_evidence_links.items()
        if claim_id in prose_contract.allowed_claims
    ):
        blocking_reasons.append("one or more exported main claims lack evidence links")
    if set(prose_contract.allowed_claims) & set(prose_contract.blocked_claims):
        blocking_reasons.append("blocked claim appears outside blocked appendix")
    blocked_claim_maps = [claim for claim in claim_map if not claim.export_allowed]
    blocking_reasons.extend(
        f"{claim.claim_id}: {claim.blocking_reason}"
        for claim in blocked_claim_maps
        if claim.blocking_reason
    )
    if release_gate_decision.status == ReleaseGateStatus.RELEASE_READY_WITH_WARNINGS:
        warnings.extend(release_gate_decision.warnings or ["release gate has warnings"])
    export_blocked = bool(blocking_reasons)
    return ExportReadinessReport(
        run_id=prose_contract.run_id,
        ready_for_polished_prose=(
            prose_contract.ready_for_polished_prose
            and release_gate_decision.ready_for_polished_prose
            and not export_blocked
        ),
        ready_for_latex_export=(
            latex_plan.ready_for_latex_export
            and release_gate_decision.ready_for_latex_export
            and not export_blocked
        ),
        ready_for_external_review=False,
        export_blocked=export_blocked,
        export_allowed_claims=sum(1 for claim in claim_map if claim.export_allowed),
        export_blocked_claims=sum(1 for claim in claim_map if not claim.export_allowed),
        blocking_reasons=sorted(set(blocking_reasons)),
        warnings=sorted(set(warnings)),
    )


def prepare_export(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ExportPreparationResult:
    """Prepare deterministic export contracts and maps."""
    store.init_run(run_id)
    inputs = load_export_preparation_inputs(run_id, ledger)
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.EXPORT_PREPARATION_STARTED,
        payload={"run_id": run_id, "paper_id": inputs.paper_skeleton.paper_id},
    )
    prose_contract = build_prose_generation_contract(
        run_id,
        inputs.paper_skeleton,
        inputs.claim_table,
        inputs.final_audit_report,
        inputs.release_gate_decision,
    )
    latex_plan = build_latex_export_plan(run_id, inputs.paper_skeleton, prose_contract)
    section_map = build_export_section_map(
        inputs.paper_skeleton,
        inputs.manuscript_plan,
        inputs.draft_skeleton,
    )
    claim_map = build_export_claim_map(inputs.claim_table, inputs.artifact_manifest)
    readiness = evaluate_export_readiness(
        prose_contract,
        latex_plan,
        inputs.final_audit_report,
        inputs.release_gate_decision,
        claim_map,
    )
    prose_ref = _write_json_artifact(
        run_id,
        "prose-generation-contract",
        prose_contract,
        ControllerActionType.PROSE_GENERATION_CONTRACT_WRITTEN,
        store,
        ledger,
    )
    latex_ref = _write_json_artifact(
        run_id,
        "latex-export-plan",
        latex_plan,
        ControllerActionType.LATEX_EXPORT_PLAN_WRITTEN,
        store,
        ledger,
    )
    section_ref = _write_json_artifact(
        run_id,
        "export-section-map",
        {"sections": section_map},
        ControllerActionType.EXPORT_SECTION_MAP_WRITTEN,
        store,
        ledger,
    )
    claim_ref = _write_json_artifact(
        run_id,
        "export-claim-map",
        {
            "claims": claim_map,
            "evidence_placeholders": evidence_placeholders_from_claim_map(claim_map),
        },
        ControllerActionType.EXPORT_CLAIM_MAP_WRITTEN,
        store,
        ledger,
    )
    readiness_json_ref = _write_json_artifact(
        run_id,
        "export-readiness-report",
        readiness,
        ControllerActionType.EXPORT_READINESS_REPORT_WRITTEN,
        store,
        ledger,
    )
    readiness_markdown_ref = _write_markdown_artifact(
        run_id,
        "export-readiness-report",
        render_export_readiness_report_markdown(readiness_report=readiness),
        ControllerActionType.EXPORT_READINESS_REPORT_WRITTEN,
        store,
        ledger,
    )
    bundle_manifest = ExportBundleManifest(
        run_id=run_id,
        prose_contract_ref=prose_ref,
        latex_plan_ref=latex_ref,
        section_map_ref=section_ref,
        claim_map_ref=claim_ref,
        readiness_report_ref=readiness_json_ref,
        export_artifact_refs=[
            prose_ref,
            latex_ref,
            section_ref,
            claim_ref,
            readiness_json_ref,
            readiness_markdown_ref,
        ],
    )
    bundle_ref = _write_json_artifact(
        run_id,
        "export-bundle-manifest",
        bundle_manifest,
        ControllerActionType.EXPORT_BUNDLE_MANIFEST_WRITTEN,
        store,
        ledger,
    )
    bundle_manifest = bundle_manifest.model_copy(
        update={"export_artifact_refs": [*bundle_manifest.export_artifact_refs, bundle_ref]}
    )
    return ExportPreparationResult(
        run_id=run_id,
        prose_contract=prose_contract,
        latex_plan=latex_plan,
        section_map=section_map,
        claim_map=claim_map,
        readiness_report=readiness,
        bundle_manifest=bundle_manifest,
        prose_contract_artifact=prose_ref,
        latex_plan_artifact=latex_ref,
        section_map_artifact=section_ref,
        claim_map_artifact=claim_ref,
        readiness_json_artifact=readiness_json_ref,
        readiness_markdown_artifact=readiness_markdown_ref,
        bundle_manifest_artifact=bundle_ref,
    )


def _claim_map_entry(
    claim: Claim,
    artifact_by_id: dict[str, ArtifactManifestEntry],
) -> ExportClaimMap:
    evidence_entries = [
        artifact_by_id[artifact_id]
        for artifact_id in claim.evidence_artifact_ids
        if artifact_id in artifact_by_id
    ]
    blocking_reason = _claim_blocking_reason(claim, evidence_entries)
    if blocking_reason is None and len(evidence_entries) < len(claim.evidence_artifact_ids):
        blocking_reason = "evidence artifact missing from artifact manifest"
    return ExportClaimMap(
        claim_id=claim.claim_id,
        candidate_id=claim.candidate_id,
        claim_label=claim.claim_label,
        evidence_artifact_ids=claim.evidence_artifact_ids,
        evidence_types=claim.evidence_types,
        evidence_hashes={
            entry.artifact_id: entry.content_hash
            for entry in evidence_entries
            if entry.content_hash and entry.is_evidence and not entry.is_presentation
        },
        producing_commit_hashes={
            entry.artifact_id: entry.producing_commit_hash
            for entry in evidence_entries
            if entry.producing_commit_hash and entry.is_evidence and not entry.is_presentation
        },
        allowed_export_sections=[claim.allowed_section] if blocking_reason is None else [],
        export_allowed=blocking_reason is None,
        blocking_reason=blocking_reason,
    )


def _claim_blocking_reason(
    claim: Claim,
    evidence_entries: list[ArtifactManifestEntry],
) -> str | None:
    if claim.allowed_in_main_text and not claim.evidence_artifact_ids:
        return "main claim lacks evidence links"
    if claim.claim_label == VerificationLabel.UNSUPPORTED and claim.allowed_in_main_text:
        return "unsupported claim cannot be exported in main text"
    if claim.claim_label == VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED:
        return "RealDataExperimentVerified is unavailable in the MVP"
    if claim.claim_label == VerificationLabel.CONJECTURE and "theorem" in claim.claim_text.lower():
        return "Conjecture cannot be exported as theorem"
    if (
        claim.claim_label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
        and _contains_real_world_claim(claim.claim_text)
    ):
        return "SyntheticExperimentVerified cannot be exported as real-world validation"
    if (
        claim.claim_label == VerificationLabel.NEGATIVE_RESULT
        and "positive evidence" in claim.claim_text.lower()
    ):
        return "NegativeResult cannot be exported as positive evidence"
    if any(entry.is_presentation or _is_markdown_or_latex(entry) for entry in evidence_entries):
        return "Markdown or LaTeX artifact cannot serve as verification evidence"
    if any(not entry.producing_commit_hash for entry in evidence_entries if entry.is_evidence):
        return "evidence artifact lacks producing commit hash"
    return None


def _artifact_type_from_claim_map(claim: ExportClaimMap, artifact_id: str) -> ArtifactType:
    for evidence_type in claim.evidence_types:
        try:
            return ArtifactType(evidence_type)
        except ValueError:
            continue
    return ArtifactType.REPORT if artifact_id not in claim.evidence_hashes else ArtifactType.LEAN


def _write_json_artifact(
    run_id: str,
    artifact_id: str,
    payload,
    action_type: ControllerActionType,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=artifact_id,
        artifact_type=ArtifactType.REPORT,
        data=payload,
        metadata={"stage": "export_preparation", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=action_type,
        payload=payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload,
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_markdown_artifact(
    run_id: str,
    artifact_id: str,
    markdown: str,
    action_type: ControllerActionType,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_markdown(
        run_id=run_id,
        artifact_id=artifact_id,
        artifact_type=ArtifactType.REPORT,
        markdown=markdown,
        metadata={"stage": "export_preparation", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=action_type,
        payload={"artifact_id": artifact_id, "format": "markdown"},
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _contains_real_world_claim(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in ["real-world", "real world", "real markets", "real mobility"]
    )


def _is_markdown_or_latex(entry: ArtifactManifestEntry) -> bool:
    suffix = entry.path.rsplit(".", maxsplit=1)[-1].lower() if "." in entry.path else ""
    return entry.artifact_type == ArtifactType.LATEX or suffix in {"md", "markdown", "tex", "pdf"}


def _latest_commit_with_key(
    commits: list[LedgerCommit],
    action_type: ControllerActionType,
    key: str,
) -> LedgerCommit | None:
    for commit in reversed(commits):
        if commit.action_type == action_type and key in commit.payload:
            return commit
    return None


def _require_commit_with_key(
    commits: list[LedgerCommit],
    action_type: ControllerActionType,
    key: str,
    message: str,
) -> LedgerCommit:
    commit = _latest_commit_with_key(commits, action_type, key)
    if commit is None:
        raise ExportPreparationError(message)
    return commit


def _require_commit(
    commits: list[LedgerCommit],
    action_type: ControllerActionType,
    message: str,
) -> LedgerCommit:
    for commit in reversed(commits):
        if commit.action_type == action_type:
            return commit
    raise ExportPreparationError(message)
