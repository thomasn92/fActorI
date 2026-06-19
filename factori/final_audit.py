"""Deterministic final audit and release gate orchestration."""

from __future__ import annotations

from dataclasses import dataclass

from factori.artifacts import ArtifactStore
from factori.ledger import LedgerError, ResearchLedger
from factori.release_gate import decide_release_gate
from factori.reports import (
    render_final_audit_report_markdown,
    render_release_gate_decision_markdown,
)
from factori.schemas import (
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactRef,
    ArtifactType,
    AuditCategory,
    AuditCheck,
    AuditCheckStatus,
    AuditFinding,
    AuditSeverity,
    BlockedClaim,
    BranchOutcomeSummary,
    ClaimTable,
    ControllerActionType,
    DraftSkeleton,
    FinalAuditReport,
    LedgerCommit,
    LedgerSummary,
    ManuscriptPlan,
    PaperSkeleton,
    ReleaseGateDecision,
    ReproducibilityManifest,
    ResearchObject,
    VerificationLabel,
)


class FinalAuditError(RuntimeError):
    """Raised when final audit prerequisites are missing."""


@dataclass(frozen=True)
class FinalAuditInputs:
    """Ledger-loaded inputs to the final audit."""

    paper_skeleton: PaperSkeleton
    research_object: ResearchObject
    claim_table: ClaimTable
    artifact_manifest: ArtifactManifest
    ledger_summary: LedgerSummary
    branch_outcomes: list[BranchOutcomeSummary]
    reproducibility_manifest: ReproducibilityManifest
    manuscript_plan: ManuscriptPlan
    draft_skeleton: DraftSkeleton
    blocked_claims: list[BlockedClaim]
    commits: list[LedgerCommit]


@dataclass(frozen=True)
class FinalAuditResult:
    """Result of deterministic final audit and release gate."""

    run_id: str
    audit_report: FinalAuditReport
    release_gate_decision: ReleaseGateDecision
    audit_json_artifact: ArtifactRef
    audit_markdown_artifact: ArtifactRef
    release_json_artifact: ArtifactRef
    release_markdown_artifact: ArtifactRef


def load_final_audit_inputs(run_id: str, ledger: ResearchLedger) -> FinalAuditInputs:
    """Load final audit inputs from ledger commits."""
    commits = ledger.list_commits(run_id)
    paper_commit = _latest_commit_with_key(
        commits,
        ControllerActionType.PAPER_SKELETON_WRITTEN,
        "paper_id",
    )
    if paper_commit is None:
        raise FinalAuditError(
            "Paper skeleton artifacts not found; run factori assemble-paper-skeleton first"
        )
    research_commit = _require_commit_with_key(
        commits,
        ControllerActionType.RESEARCH_OBJECT_WRITTEN,
        "final_nucleus",
        "Research object artifacts not found; run factori package-research-object first",
    )
    claim_table_commit = _require_commit(
        commits,
        ControllerActionType.CLAIM_TABLE_BUILT,
        "Claim table not found; run factori plan-manuscript first",
    )
    artifact_manifest_commit = _require_commit(
        commits,
        ControllerActionType.ARTIFACT_MANIFEST_WRITTEN,
        "Artifact manifest not found; run factori package-research-object first",
    )
    ledger_summary_commit = _require_commit(
        commits,
        ControllerActionType.LEDGER_SUMMARY_WRITTEN,
        "Ledger summary not found; run factori package-research-object first",
    )
    branch_outcomes_commit = _require_commit(
        commits,
        ControllerActionType.BRANCH_OUTCOMES_WRITTEN,
        "Branch outcomes not found; run factori package-research-object first",
    )
    reproducibility_commit = _require_commit(
        commits,
        ControllerActionType.REPRODUCIBILITY_MANIFEST_WRITTEN,
        "Reproducibility manifest not found; run factori package-research-object first",
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
    blocked_claims_commit = _require_commit(
        commits,
        ControllerActionType.BLOCKED_CLAIMS_IDENTIFIED,
        "Blocked claims not found; run factori plan-manuscript first",
    )
    return FinalAuditInputs(
        paper_skeleton=PaperSkeleton.model_validate(paper_commit.payload),
        research_object=ResearchObject.model_validate(research_commit.payload),
        claim_table=ClaimTable.model_validate(claim_table_commit.payload),
        artifact_manifest=ArtifactManifest.model_validate(artifact_manifest_commit.payload),
        ledger_summary=LedgerSummary.model_validate(ledger_summary_commit.payload),
        branch_outcomes=[
            BranchOutcomeSummary.model_validate(item)
            for item in branch_outcomes_commit.payload.get("branch_outcomes", [])
        ],
        reproducibility_manifest=ReproducibilityManifest.model_validate(
            reproducibility_commit.payload
        ),
        manuscript_plan=ManuscriptPlan.model_validate(manuscript_plan_commit.payload),
        draft_skeleton=DraftSkeleton.model_validate(draft_skeleton_commit.payload),
        blocked_claims=[
            BlockedClaim.model_validate(item)
            for item in blocked_claims_commit.payload.get("blocked_claims", [])
        ],
        commits=commits,
    )


def build_final_audit_report(
    *,
    run_id: str,
    inputs: FinalAuditInputs,
    ledger_valid: bool = True,
    ledger_error: str | None = None,
) -> FinalAuditReport:
    """Build a deterministic final audit report from loaded inputs."""
    checks = [
        _ledger_exists_check(inputs),
        _ledger_hash_chain_check(inputs, ledger_valid, ledger_error),
        _required_stage_reports_check(inputs.research_object),
        _research_object_artifacts_check(inputs.research_object),
        _artifact_hashes_check(inputs.artifact_manifest),
        _evidence_producing_commits_check(inputs.artifact_manifest),
        _presentation_not_evidence_check(inputs.artifact_manifest),
        _markdown_latex_not_evidence_check(inputs.artifact_manifest),
        _main_claim_evidence_links_check(inputs.paper_skeleton),
        _claim_label_preservation_check(inputs.paper_skeleton, inputs.claim_table),
        _conjecture_not_theorem_check(inputs.paper_skeleton, inputs.claim_table),
        _synthetic_boundary_check(inputs.paper_skeleton, inputs.claim_table),
        _no_real_data_verified_check(inputs.paper_skeleton, inputs.claim_table),
        _unsupported_main_claim_check(inputs.manuscript_plan, inputs.claim_table),
        _blocked_claim_not_main_check(inputs.paper_skeleton, inputs.blocked_claims),
        _blocked_claim_appendix_check(inputs.paper_skeleton, inputs.blocked_claims),
        _negative_result_check(inputs.paper_skeleton, inputs.claim_table),
        _limitation_check(inputs.paper_skeleton, inputs.claim_table),
        _final_nucleus_check(inputs.research_object),
        _claim_table_exists_check(inputs.research_object, inputs.claim_table),
        _manuscript_plan_exists_check(inputs.research_object, inputs.manuscript_plan),
        _draft_skeleton_exists_check(inputs.research_object, inputs.draft_skeleton),
        _research_object_exists_check(inputs.research_object),
        _paper_skeleton_exists_check(inputs.paper_skeleton),
        _branch_outcome_summary_check(inputs.research_object, inputs.branch_outcomes),
        _failed_deferred_pruned_branch_check(inputs.paper_skeleton, inputs.branch_outcomes),
        _reproducibility_manifest_check(inputs.research_object, inputs.reproducibility_manifest),
        _runtime_summary_not_provenance_check(inputs.paper_skeleton),
        _human_escalation_policy_check(inputs.commits),
        _paper_required_sections_check(inputs.paper_skeleton),
    ]
    findings = [
        AuditFinding(
            check_id=check.check_id,
            category=check.category,
            status=check.status,
            severity=check.severity,
            message=check.message,
            artifact_refs=check.artifact_refs,
            commit_refs=check.commit_refs,
        )
        for check in checks
        if check.status in {AuditCheckStatus.WARNING, AuditCheckStatus.FAIL}
    ]
    return FinalAuditReport(
        run_id=run_id,
        checks=checks,
        findings=findings,
        passes_count=sum(1 for check in checks if check.status == AuditCheckStatus.PASS),
        warnings_count=sum(1 for check in checks if check.status == AuditCheckStatus.WARNING),
        failures_count=sum(1 for check in checks if check.status == AuditCheckStatus.FAIL),
        blocking_failures_count=sum(
            1
            for check in checks
            if check.status == AuditCheckStatus.FAIL
            and check.severity == AuditSeverity.BLOCKING
        ),
    )


def run_final_audit(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> FinalAuditResult:
    """Run final audit, decide release gate, and write ledgered artifacts."""
    store.init_run(run_id)
    inputs = load_final_audit_inputs(run_id, ledger)
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.FINAL_AUDIT_STARTED,
        payload={"run_id": run_id, "paper_id": inputs.paper_skeleton.paper_id},
    )
    try:
        ledger.validate()
        ledger_valid = True
        ledger_error = None
    except LedgerError as exc:
        ledger_valid = False
        ledger_error = str(exc)

    audit_report = build_final_audit_report(
        run_id=run_id,
        inputs=inputs,
        ledger_valid=ledger_valid,
        ledger_error=ledger_error,
    )
    decision = decide_release_gate(audit_report)
    audit_json_artifact = _write_json_artifact(
        run_id=run_id,
        artifact_id="final-audit-report",
        payload=audit_report,
        action_type=ControllerActionType.FINAL_AUDIT_REPORT_WRITTEN,
        store=store,
        ledger=ledger,
    )
    audit_markdown_artifact = _write_markdown_artifact(
        run_id=run_id,
        artifact_id="final-audit-report",
        markdown=render_final_audit_report_markdown(audit_report=audit_report),
        action_type=ControllerActionType.FINAL_AUDIT_REPORT_WRITTEN,
        store=store,
        ledger=ledger,
    )
    release_json_artifact = _write_json_artifact(
        run_id=run_id,
        artifact_id="release-gate-decision",
        payload=decision,
        action_type=ControllerActionType.RELEASE_GATE_DECIDED,
        store=store,
        ledger=ledger,
    )
    release_markdown_artifact = _write_markdown_artifact(
        run_id=run_id,
        artifact_id="release-gate-decision",
        markdown=render_release_gate_decision_markdown(decision=decision),
        action_type=ControllerActionType.RELEASE_GATE_DECIDED,
        store=store,
        ledger=ledger,
    )
    return FinalAuditResult(
        run_id=run_id,
        audit_report=audit_report,
        release_gate_decision=decision,
        audit_json_artifact=audit_json_artifact,
        audit_markdown_artifact=audit_markdown_artifact,
        release_json_artifact=release_json_artifact,
        release_markdown_artifact=release_markdown_artifact,
    )


def _ledger_exists_check(inputs: FinalAuditInputs) -> AuditCheck:
    passed = inputs.ledger_summary.commit_count > 0 or bool(inputs.commits)
    return _check(
        "ledger_exists",
        AuditCategory.LEDGER_INTEGRITY,
        passed,
        "ledger exists" if passed else "ledger is missing or empty",
    )


def _ledger_hash_chain_check(
    inputs: FinalAuditInputs,
    ledger_valid: bool,
    ledger_error: str | None,
) -> AuditCheck:
    refs = [commit.commit_hash for commit in inputs.commits[-3:]]
    return _check(
        "ledger_hash_chain_valid",
        AuditCategory.LEDGER_INTEGRITY,
        ledger_valid,
        "ledger hash chain is valid" if ledger_valid else ledger_error or "ledger invalid",
        commit_refs=refs,
    )


def _required_stage_reports_check(research_object: ResearchObject) -> AuditCheck:
    required = {
        "stage_a",
        "stage_b",
        "stage_c_selection",
        "stage_c_verification",
        "abstract_synthesis",
    }
    missing = sorted(required - set(research_object.stage_reports))
    artifacts = [
        research_object.stage_reports[key]
        for key in sorted(set(research_object.stage_reports))
    ]
    return _check(
        "required_stage_reports_exist",
        AuditCategory.RESEARCH_OBJECT_COMPLETENESS,
        not missing and all(_artifact_has_hash(artifact) for artifact in artifacts),
        "required stage reports exist" if not missing else f"missing stage reports: {missing}",
        artifacts=artifacts,
    )


def _research_object_artifacts_check(research_object: ResearchObject) -> AuditCheck:
    artifacts = [
        research_object.artifact_manifest_ref,
        research_object.ledger_summary_ref,
        research_object.branch_outcomes_ref,
        research_object.reproducibility_manifest_ref,
    ]
    missing = [name for name, artifact in zip(
        [
            "artifact_manifest",
            "ledger_summary",
            "branch_outcomes",
            "reproducibility_manifest",
        ],
        artifacts,
        strict=True,
    ) if artifact is None or not _artifact_has_hash(artifact)]
    return _check(
        "research_object_artifacts_exist",
        AuditCategory.RESEARCH_OBJECT_COMPLETENESS,
        not missing,
        "research object artifacts exist" if not missing else f"missing artifacts: {missing}",
        artifacts=[artifact for artifact in artifacts if artifact is not None],
    )


def _artifact_hashes_check(artifact_manifest: ArtifactManifest) -> AuditCheck:
    missing = [entry.path for entry in artifact_manifest.artifacts if not entry.content_hash]
    return _check(
        "listed_artifacts_have_hashes",
        AuditCategory.ARTIFACT_INTEGRITY,
        not missing,
        "all listed artifacts have hashes" if not missing else f"missing hashes: {missing}",
    )


def _evidence_producing_commits_check(artifact_manifest: ArtifactManifest) -> AuditCheck:
    missing = [
        entry.path
        for entry in artifact_manifest.artifacts
        if entry.is_evidence and not entry.producing_commit_hash
    ]
    return _check(
        "evidence_artifacts_have_producing_commits",
        AuditCategory.EVIDENCE_BOUNDARY,
        not missing,
        "evidence artifacts have producing commits"
        if not missing
        else f"evidence artifacts missing producing commits: {missing}",
    )


def _presentation_not_evidence_check(artifact_manifest: ArtifactManifest) -> AuditCheck:
    offenders = [
        entry.path
        for entry in artifact_manifest.artifacts
        if entry.is_evidence and entry.is_presentation
    ]
    return _check(
        "presentation_artifacts_not_evidence",
        AuditCategory.EVIDENCE_BOUNDARY,
        not offenders,
        "presentation artifacts are not verification evidence"
        if not offenders
        else f"presentation artifacts marked as evidence: {offenders}",
    )


def _markdown_latex_not_evidence_check(artifact_manifest: ArtifactManifest) -> AuditCheck:
    offenders = [
        entry.path
        for entry in artifact_manifest.artifacts
        if entry.is_evidence and _is_markdown_or_latex(entry)
    ]
    return _check(
        "markdown_latex_not_verification_evidence",
        AuditCategory.EVIDENCE_BOUNDARY,
        not offenders,
        "Markdown and LaTeX are not verification evidence"
        if not offenders
        else f"Markdown/LaTeX marked as evidence: {offenders}",
    )


def _main_claim_evidence_links_check(paper_skeleton: PaperSkeleton) -> AuditCheck:
    missing = [
        placeholder.claim_id
        for placeholder in paper_skeleton.claim_placeholders
        if not placeholder.evidence_artifact_ids
    ]
    return _check(
        "main_claims_have_evidence_links",
        AuditCategory.EVIDENCE_BOUNDARY,
        not missing and bool(paper_skeleton.claim_placeholders),
        "main claims have evidence links"
        if not missing and paper_skeleton.claim_placeholders
        else f"main claims missing evidence links: {missing}",
    )


def _claim_label_preservation_check(
    paper_skeleton: PaperSkeleton,
    claim_table: ClaimTable,
) -> AuditCheck:
    claim_by_id = {claim.claim_id: claim for claim in claim_table.claims}
    mismatches = [
        placeholder.claim_id
        for placeholder in paper_skeleton.claim_placeholders
        if placeholder.claim_id not in claim_by_id
        or placeholder.claim_label != claim_by_id[placeholder.claim_id].claim_label
    ]
    return _check(
        "claim_labels_preserved",
        AuditCategory.CLAIM_LABEL_PRESERVATION,
        not mismatches,
        "claim labels are preserved"
        if not mismatches
        else f"claim label mismatches: {mismatches}",
    )


def _conjecture_not_theorem_check(
    paper_skeleton: PaperSkeleton,
    claim_table: ClaimTable,
) -> AuditCheck:
    claim_by_id = {claim.claim_id: claim for claim in claim_table.claims}
    offenders = []
    for placeholder in paper_skeleton.claim_placeholders:
        claim = claim_by_id.get(placeholder.claim_id)
        if claim is None or claim.claim_label != VerificationLabel.CONJECTURE:
            continue
        text = f"{claim.claim_text} {placeholder.placeholder_text}".lower()
        if placeholder.claim_label != VerificationLabel.CONJECTURE or "theorem" in text:
            offenders.append(claim.claim_id)
    return _check(
        "conjectures_not_upgraded",
        AuditCategory.CLAIM_LABEL_PRESERVATION,
        not offenders,
        "conjectures remain conjectures"
        if not offenders
        else f"conjectures upgraded or theorem-framed: {offenders}",
    )


def _synthetic_boundary_check(
    paper_skeleton: PaperSkeleton,
    claim_table: ClaimTable,
) -> AuditCheck:
    del paper_skeleton
    offenders = [
        claim.claim_id
        for claim in claim_table.claims
        if claim.claim_label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
        and _contains_real_world_claim(claim.claim_text)
    ]
    return _check(
        "synthetic_not_real_world",
        AuditCategory.SYNTHETIC_DATA_BOUNDARY,
        not offenders,
        "synthetic evidence remains synthetic-only"
        if not offenders
        else f"synthetic claims framed as real-world: {offenders}",
    )


def _no_real_data_verified_check(
    paper_skeleton: PaperSkeleton,
    claim_table: ClaimTable,
) -> AuditCheck:
    labels = [claim.claim_label for claim in claim_table.claims] + [
        placeholder.claim_label for placeholder in paper_skeleton.claim_placeholders
    ]
    passed = VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED not in labels
    return _check(
        "no_real_data_verified_mvp",
        AuditCategory.SYNTHETIC_DATA_BOUNDARY,
        passed,
        "RealDataExperimentVerified is absent in MVP"
        if passed
        else "RealDataExperimentVerified appears in MVP output",
    )


def _unsupported_main_claim_check(
    manuscript_plan: ManuscriptPlan,
    claim_table: ClaimTable,
) -> AuditCheck:
    offenders = [
        claim.claim_id
        for claim in claim_table.claims
        if claim.claim_id in manuscript_plan.allowed_claim_ids
        and claim.allowed_in_main_text
        and claim.claim_label == VerificationLabel.UNSUPPORTED
    ]
    return _check(
        "unsupported_claims_not_main",
        AuditCategory.CLAIM_LABEL_PRESERVATION,
        not offenders,
        "unsupported claims are excluded from main text"
        if not offenders
        else f"unsupported claims planned for main text: {offenders}",
    )


def _blocked_claim_not_main_check(
    paper_skeleton: PaperSkeleton,
    blocked_claims: list[BlockedClaim],
) -> AuditCheck:
    blocked_ids = {claim.claim_id for claim in blocked_claims}
    offenders = [
        placeholder.claim_id
        for placeholder in paper_skeleton.claim_placeholders
        if placeholder.claim_id in blocked_ids
    ]
    return _check(
        "blocked_claims_not_in_main_results",
        AuditCategory.BLOCKED_CLAIM_HANDLING,
        not offenders,
        "blocked claims are excluded from main results"
        if not offenders
        else f"blocked claims appear in main text: {offenders}",
    )


def _blocked_claim_appendix_check(
    paper_skeleton: PaperSkeleton,
    blocked_claims: list[BlockedClaim],
) -> AuditCheck:
    if not blocked_claims:
        return _check(
            "blocked_claims_in_appendix",
            AuditCategory.BLOCKED_CLAIM_HANDLING,
            True,
            "no blocked claims require appendix entries",
        )
    appendix_text = "\n".join(
        line
        for appendix in paper_skeleton.appendices
        if "Blocked" in appendix.title
        for line in appendix.content_lines
    )
    missing = [claim.claim_id for claim in blocked_claims if claim.claim_id not in appendix_text]
    return _check(
        "blocked_claims_in_appendix",
        AuditCategory.BLOCKED_CLAIM_HANDLING,
        not missing,
        "blocked claims appear in blocked-claims appendix"
        if not missing
        else f"blocked claims missing from appendix: {missing}",
    )


def _negative_result_check(
    paper_skeleton: PaperSkeleton,
    claim_table: ClaimTable,
) -> AuditCheck:
    labels_by_id = {
        placeholder.claim_id: placeholder.claim_label
        for placeholder in paper_skeleton.claim_placeholders
    }
    offenders = [
        claim.claim_id
        for claim in claim_table.claims
        if claim.claim_label == VerificationLabel.NEGATIVE_RESULT
        and (
            labels_by_id.get(claim.claim_id, VerificationLabel.NEGATIVE_RESULT)
            != VerificationLabel.NEGATIVE_RESULT
            or "positive evidence" in claim.claim_text.lower()
        )
    ]
    return _check(
        "negative_results_remain_negative",
        AuditCategory.CLAIM_LABEL_PRESERVATION,
        not offenders,
        "negative results remain negative or boundary-labeled"
        if not offenders
        else f"negative results improperly framed: {offenders}",
    )


def _limitation_check(paper_skeleton: PaperSkeleton, claim_table: ClaimTable) -> AuditCheck:
    labels_by_id = {
        placeholder.claim_id: placeholder.claim_label
        for placeholder in paper_skeleton.claim_placeholders
    }
    offenders = [
        claim.claim_id
        for claim in claim_table.claims
        if claim.claim_label == VerificationLabel.LIMITATION
        and labels_by_id.get(claim.claim_id, VerificationLabel.LIMITATION)
        != VerificationLabel.LIMITATION
    ]
    return _check(
        "limitations_remain_limitations",
        AuditCategory.CLAIM_LABEL_PRESERVATION,
        not offenders,
        "limitations remain limitations"
        if not offenders
        else f"limitations upgraded: {offenders}",
    )


def _final_nucleus_check(research_object: ResearchObject) -> AuditCheck:
    return _check(
        "final_nucleus_exists",
        AuditCategory.RESEARCH_OBJECT_COMPLETENESS,
        bool(research_object.final_nucleus.id),
        "final nucleus exists",
    )


def _claim_table_exists_check(
    research_object: ResearchObject,
    claim_table: ClaimTable,
) -> AuditCheck:
    passed = _artifact_has_hash(research_object.claim_table_ref) and bool(claim_table.claims)
    return _check(
        "claim_table_exists",
        AuditCategory.RESEARCH_OBJECT_COMPLETENESS,
        passed,
        "claim table exists" if passed else "claim table missing or empty",
        artifacts=[research_object.claim_table_ref],
    )


def _manuscript_plan_exists_check(
    research_object: ResearchObject,
    manuscript_plan: ManuscriptPlan,
) -> AuditCheck:
    passed = _artifact_has_hash(research_object.manuscript_plan_ref) and bool(
        manuscript_plan.sections
    )
    return _check(
        "manuscript_plan_exists",
        AuditCategory.RESEARCH_OBJECT_COMPLETENESS,
        passed,
        "manuscript plan exists" if passed else "manuscript plan missing or empty",
        artifacts=[research_object.manuscript_plan_ref],
    )


def _draft_skeleton_exists_check(
    research_object: ResearchObject,
    draft_skeleton: DraftSkeleton,
) -> AuditCheck:
    passed = _artifact_has_hash(research_object.draft_skeleton_ref) and bool(
        draft_skeleton.section_stubs
    )
    return _check(
        "draft_skeleton_exists",
        AuditCategory.RESEARCH_OBJECT_COMPLETENESS,
        passed,
        "draft skeleton exists" if passed else "draft skeleton missing or empty",
        artifacts=[research_object.draft_skeleton_ref],
    )


def _research_object_exists_check(research_object: ResearchObject) -> AuditCheck:
    passed = bool(research_object.run_id) and _artifact_has_hash(research_object.ledger_summary_ref)
    return _check(
        "research_object_exists",
        AuditCategory.RESEARCH_OBJECT_COMPLETENESS,
        passed,
        "research object exists" if passed else "research object incomplete",
    )


def _paper_skeleton_exists_check(paper_skeleton: PaperSkeleton) -> AuditCheck:
    return _check(
        "paper_skeleton_exists",
        AuditCategory.PAPER_SKELETON_CONSISTENCY,
        bool(paper_skeleton.paper_id) and bool(paper_skeleton.sections),
        "paper skeleton exists",
    )


def _branch_outcome_summary_check(
    research_object: ResearchObject,
    branch_outcomes: list[BranchOutcomeSummary],
) -> AuditCheck:
    passed = _artifact_has_hash(research_object.branch_outcomes_ref) and bool(branch_outcomes)
    return _check(
        "branch_outcome_summary_exists",
        AuditCategory.PROVENANCE_COMPLETENESS,
        passed,
        "branch outcome summary exists"
        if passed
        else "branch outcome summary missing or empty",
        artifacts=[
            research_object.branch_outcomes_ref
            for _ in [0]
            if research_object.branch_outcomes_ref is not None
        ],
    )


def _failed_deferred_pruned_branch_check(
    paper_skeleton: PaperSkeleton,
    branch_outcomes: list[BranchOutcomeSummary],
) -> AuditCheck:
    tracked = {
        "PrunedDuplicate",
        "RejectedRedTeam",
        "PrunedUncertain",
        "InsufficientRetrievalAdequacy",
        "DeferredRealDataCandidate",
        "RequiresRealData",
        "StagnationStop",
        "BudgetDeferred",
    }
    relevant = [outcome for outcome in branch_outcomes if outcome.outcome in tracked]
    appendix_present = any(
        "Failed, Deferred, and Pruned" in appendix.title
        for appendix in paper_skeleton.appendices
    )
    if not relevant:
        return _warning(
            "failed_deferred_pruned_branches_represented",
            AuditCategory.PROVENANCE_COMPLETENESS,
            "no failed/deferred/pruned branch outcomes were listed",
        )
    return _check(
        "failed_deferred_pruned_branches_represented",
        AuditCategory.PROVENANCE_COMPLETENESS,
        appendix_present,
        "failed/deferred/pruned branches are represented"
        if appendix_present
        else "failed/deferred/pruned branch appendix missing",
    )


def _reproducibility_manifest_check(
    research_object: ResearchObject,
    reproducibility_manifest: ReproducibilityManifest,
) -> AuditCheck:
    passed = _artifact_has_hash(research_object.reproducibility_manifest_ref) and (
        reproducibility_manifest.reproducible or not reproducibility_manifest.blocking_issues
    )
    return _check(
        "reproducibility_manifest_exists",
        AuditCategory.REPRODUCIBILITY_READINESS,
        passed,
        "reproducibility manifest exists"
        if passed
        else f"reproducibility blocking issues: {reproducibility_manifest.blocking_issues}",
        artifacts=[
            research_object.reproducibility_manifest_ref
            for _ in [0]
            if research_object.reproducibility_manifest_ref is not None
        ],
    )


def _runtime_summary_not_provenance_check(paper_skeleton: PaperSkeleton) -> AuditCheck:
    offenders = [
        key for key in paper_skeleton.provenance_refs if "runtime" in key.lower()
    ]
    return _check(
        "runtime_summary_not_provenance",
        AuditCategory.PROVENANCE_COMPLETENESS,
        not offenders,
        "runtime summaries are not used as provenance"
        if not offenders
        else f"runtime summaries used as provenance: {offenders}",
    )


def _human_escalation_policy_check(commits: list[LedgerCommit]) -> AuditCheck:
    offenders = [
        commit.commit_hash
        for commit in commits
        if _payload_contains_value(commit.payload, "AskHuman")
        and not _payload_has_tail_condition(commit.payload)
    ]
    return _check(
        "human_required_only_for_tail_cases",
        AuditCategory.HUMAN_ESCALATION_POLICY,
        not offenders,
        "HumanRequired was not used for ordinary deterministic repairs"
        if not offenders
        else f"AskHuman without tail condition: {offenders}",
        commit_refs=offenders,
    )


def _paper_required_sections_check(paper_skeleton: PaperSkeleton) -> AuditCheck:
    titles = {section.title for section in paper_skeleton.sections}
    required = {"Abstract", "Introduction", "Limitations", "Conclusion"}
    model_ok = bool(titles & {"General Model", "Problem Setup"})
    result_ok = bool(titles & {"Results", "Negative Results or Boundary Cases"})
    missing = sorted(required - titles)
    if not model_ok:
        missing.append("General Model or Problem Setup")
    if not result_ok:
        missing.append("Results or Negative Results")
    return _check(
        "paper_required_sections",
        AuditCategory.PAPER_SKELETON_CONSISTENCY,
        not missing,
        "paper skeleton includes required sections"
        if not missing
        else f"missing paper sections: {missing}",
    )


def _write_json_artifact(
    *,
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
        metadata={"stage": "final_audit", "fake": True},
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
    *,
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
        metadata={"stage": "final_audit", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=action_type,
        payload={"artifact_id": artifact_id, "format": "markdown"},
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _check(
    check_id: str,
    category: AuditCategory,
    passed: bool,
    message: str,
    *,
    artifacts: list[ArtifactRef] | None = None,
    commit_refs: list[str] | None = None,
) -> AuditCheck:
    return AuditCheck(
        check_id=check_id,
        category=category,
        status=AuditCheckStatus.PASS if passed else AuditCheckStatus.FAIL,
        severity=AuditSeverity.INFO if passed else AuditSeverity.BLOCKING,
        message=message,
        artifact_refs=artifacts or [],
        commit_refs=commit_refs or [],
    )


def _warning(check_id: str, category: AuditCategory, message: str) -> AuditCheck:
    return AuditCheck(
        check_id=check_id,
        category=category,
        status=AuditCheckStatus.WARNING,
        severity=AuditSeverity.WARNING,
        message=message,
    )


def _artifact_has_hash(artifact: ArtifactRef | None) -> bool:
    return artifact is not None and bool(artifact.content_hash)


def _is_markdown_or_latex(entry: ArtifactManifestEntry) -> bool:
    suffix = entry.path.rsplit(".", maxsplit=1)[-1].lower() if "." in entry.path else ""
    return entry.artifact_type == ArtifactType.LATEX or suffix in {"md", "markdown", "tex", "pdf"}


def _contains_real_world_claim(text: str) -> bool:
    lowered = text.lower()
    return any(
        marker in lowered
        for marker in [
            "real-world",
            "real world",
            "real markets",
            "real mobility",
            "public data",
            "user data",
        ]
    )


def _payload_contains_value(payload, value: str) -> bool:
    if isinstance(payload, dict):
        return any(_payload_contains_value(item, value) for item in payload.values())
    if isinstance(payload, list):
        return any(_payload_contains_value(item, value) for item in payload)
    return str(payload) == value


def _payload_has_tail_condition(payload) -> bool:
    tail_keys = {
        "extra_budget_required",
        "irreversible_decision",
        "external_access_required",
        "user_preference_needed",
    }
    if isinstance(payload, dict):
        if any(payload.get(key) is True for key in tail_keys):
            return True
        uncertainty = payload.get("decision_uncertainty")
        risk = payload.get("action_risk")
        if isinstance(uncertainty, int | float) and uncertainty > 0.8:
            return True
        if isinstance(risk, int | float) and risk > 0.8:
            return True
        return any(_payload_has_tail_condition(item) for item in payload.values())
    if isinstance(payload, list):
        return any(_payload_has_tail_condition(item) for item in payload)
    return False


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
        raise FinalAuditError(message)
    return commit


def _require_commit(
    commits: list[LedgerCommit],
    action_type: ControllerActionType,
    message: str,
) -> LedgerCommit:
    for commit in reversed(commits):
        if commit.action_type == action_type:
            return commit
    raise FinalAuditError(message)
