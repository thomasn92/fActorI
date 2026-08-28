"""Read-only rerun decisions and ledger tip/fork validation."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from factori.checkpoints import inspect_stage_checkpoint, ledger_path
from factori.hashing import canonical_json
from factori.ledger import LedgerError, ResearchLedger
from factori.pipeline import stage_is_read_only
from factori.schemas import (
    ControllerActionType,
    LedgerBranchFinding,
    LedgerCommit,
    LedgerTipStatus,
    LedgerTipValidationReport,
    PipelineStage,
    RerunPolicy,
    RunCompletenessStatus,
    RunStatusReport,
    StageRerunDecision,
    StageRerunStatus,
)

_STAGE_START_ACTIONS = {
    ControllerActionType.STAGE_A_STARTED: PipelineStage.RUN_STAGE_A,
    ControllerActionType.STAGE_B_STARTED: PipelineStage.RUN_STAGE_B,
    ControllerActionType.STAGE_C_SELECTION_STARTED: PipelineStage.SELECT_STAGE_C,
    ControllerActionType.STAGE_C_VERIFICATION_STARTED: PipelineStage.RUN_STAGE_C,
    ControllerActionType.ABSTRACT_SYNTHESIS_STARTED: PipelineStage.SYNTHESIZE_ABSTRACT,
    ControllerActionType.MANUSCRIPT_PLANNING_STARTED: PipelineStage.PLAN_MANUSCRIPT,
    ControllerActionType.DRAFT_SKELETON_STARTED: PipelineStage.BUILD_DRAFT_SKELETON,
    ControllerActionType.RESEARCH_OBJECT_PACKAGING_STARTED: (PipelineStage.PACKAGE_RESEARCH_OBJECT),
    ControllerActionType.PAPER_ASSEMBLY_STARTED: PipelineStage.ASSEMBLE_PAPER_SKELETON,
    ControllerActionType.FINAL_AUDIT_STARTED: PipelineStage.FINAL_AUDIT,
    ControllerActionType.EXPORT_PREPARATION_STARTED: PipelineStage.PREPARE_EXPORT,
}


def decide_stage_rerun(
    run_id: str,
    stage_name: str | PipelineStage,
    policy: RerunPolicy,
    status_report: RunStatusReport,
    *,
    force: bool = False,
    root: str | Path = ".",
) -> StageRerunDecision:
    """Decide whether one stage may run without mutating the inspected run."""
    stage = PipelineStage(stage_name)
    if stage_is_read_only(stage):
        return _decision(
            run_id,
            stage,
            policy,
            StageRerunStatus.READ_ONLY_ALLOWED,
            completed=stage in status_report.completed_stages,
            force=force,
            should_run=True,
            reason="Read-only stages may be invoked repeatedly.",
        )
    if status_report.completeness_status == RunCompletenessStatus.INCONSISTENT_RUN:
        return _decision(
            run_id,
            stage,
            policy,
            StageRerunStatus.BLOCKED_INCONSISTENT,
            completed=stage in status_report.completed_stages,
            force=force,
            should_run=False,
            reason="Mutating stages are blocked because the run is inconsistent.",
        )
    completed = inspect_stage_checkpoint(run_id, stage, root).completed
    if policy == RerunPolicy.READ_ONLY_ONLY:
        return _decision(
            run_id,
            stage,
            policy,
            StageRerunStatus.BLOCKED_INCONSISTENT,
            completed=completed,
            force=force,
            should_run=False,
            reason="ReadOnlyOnly policy forbids mutating stages.",
        )
    if not completed:
        return _decision(
            run_id,
            stage,
            policy,
            StageRerunStatus.ALLOWED,
            completed=False,
            force=force,
            should_run=True,
            reason="Stage completion artifacts do not exist.",
        )
    if policy == RerunPolicy.SKIP_IF_COMPLETE:
        return _decision(
            run_id,
            stage,
            policy,
            StageRerunStatus.SKIPPED_ALREADY_COMPLETE,
            completed=True,
            force=force,
            should_run=False,
            should_skip=True,
            reason="Stage is complete and policy requests a no-op skip.",
        )
    if policy == RerunPolicy.ALLOW_IF_FORCED and force:
        return _decision(
            run_id,
            stage,
            policy,
            StageRerunStatus.ALLOWED_FORCED,
            completed=True,
            force=True,
            should_run=True,
            reason="Stage is complete but explicit force allows a rerun.",
        )
    reason = (
        "Stage is complete; AllowIfForced requires --force."
        if policy == RerunPolicy.ALLOW_IF_FORCED
        else "Stage is already complete and FailIfExists blocks mutation."
    )
    return _decision(
        run_id,
        stage,
        policy,
        StageRerunStatus.BLOCKED_ALREADY_COMPLETE,
        completed=True,
        force=force,
        should_run=False,
        reason=reason,
    )


def validate_ledger_tip(
    run_id: str,
    *,
    root: str | Path = ".",
    policy: RerunPolicy = RerunPolicy.FAIL_IF_EXISTS,
) -> LedgerTipValidationReport:
    """Inspect ledger linearity, tips, parent links, and repeated stage markers."""
    path = ledger_path(root, run_id)
    if not path.is_file():
        return LedgerTipValidationReport(
            run_id=run_id,
            status=LedgerTipStatus.MISSING,
            commit_count=0,
            ledger_exists=False,
        )
    try:
        ledger = ResearchLedger.open_existing(path)
        commits = ledger.list_commits_read_only(run_id)
    except Exception as exc:  # pragma: no cover - defensive for corrupt SQLite files.
        finding = LedgerBranchFinding(
            finding_type="UnreadableLedger",
            message=f"Ledger could not be read: {exc}",
            blocking=True,
        )
        return LedgerTipValidationReport(
            run_id=run_id,
            status=LedgerTipStatus.INVALID,
            commit_count=0,
            branch_findings=[finding],
            blocking_findings=[finding],
            ledger_exists=True,
        )
    if not commits:
        finding = LedgerBranchFinding(
            finding_type="EmptyLedger",
            message="Ledger exists but has no commits.",
            blocking=False,
        )
        return LedgerTipValidationReport(
            run_id=run_id,
            status=LedgerTipStatus.WARNING,
            commit_count=0,
            branch_findings=[finding],
            ledger_exists=True,
        )

    branch_findings: list[LedgerBranchFinding] = []
    try:
        ResearchLedger.validate_snapshot(commits)
    except LedgerError as exc:
        branch_findings.append(
            LedgerBranchFinding(
                finding_type="HashChainInvalid",
                message=f"Ledger hash-chain validation failed: {exc}",
                blocking=True,
            )
        )
    branch_findings.extend(_branch_findings(commits))
    duplicate_findings = _duplicate_stage_findings(commits, policy)
    all_findings = [*branch_findings, *duplicate_findings]
    blocking = [finding for finding in all_findings if finding.blocking]
    parent_hashes = {commit.parent_hash for commit in commits if commit.parent_hash is not None}
    tip_hashes = sorted(
        commit.commit_hash for commit in commits if commit.commit_hash not in parent_hashes
    )
    status = (
        LedgerTipStatus.INVALID
        if blocking
        else LedgerTipStatus.WARNING
        if all_findings
        else LedgerTipStatus.VALID
    )
    return LedgerTipValidationReport(
        run_id=run_id,
        status=status,
        commit_count=len(commits),
        tip_hashes=tip_hashes,
        branch_findings=branch_findings,
        duplicate_stage_findings=duplicate_findings,
        blocking_findings=blocking,
        ledger_exists=True,
    )


def _branch_findings(commits: list[LedgerCommit]) -> list[LedgerBranchFinding]:
    findings: list[LedgerBranchFinding] = []
    hashes = {commit.commit_hash for commit in commits}
    children: dict[str | None, list[str]] = defaultdict(list)
    for index, commit in enumerate(commits):
        children[commit.parent_hash].append(commit.commit_hash)
        if commit.parent_hash is not None and commit.parent_hash not in hashes:
            findings.append(
                LedgerBranchFinding(
                    finding_type="BrokenParentLink",
                    message=f"Commit {commit.commit_hash} references a missing parent.",
                    commit_hashes=[commit.commit_hash],
                    parent_hash=commit.parent_hash,
                    blocking=True,
                )
            )
        if index > 0 and commit.parent_hash != commits[index - 1].commit_hash:
            findings.append(
                LedgerBranchFinding(
                    finding_type="NonTipAppend",
                    message="Commit does not extend the preceding insertion-order tip.",
                    commit_hashes=[commit.commit_hash, commits[index - 1].commit_hash],
                    parent_hash=commit.parent_hash,
                    blocking=True,
                )
            )
    for parent_hash, child_hashes in sorted(children.items(), key=lambda item: str(item[0])):
        if len(child_hashes) <= 1:
            continue
        finding_type = "MultipleRoots" if parent_hash is None else "ForkedParent"
        findings.append(
            LedgerBranchFinding(
                finding_type=finding_type,
                message="Multiple commits share the same parent hash.",
                commit_hashes=sorted(child_hashes),
                parent_hash=parent_hash,
                blocking=True,
            )
        )
    parent_hashes = {commit.parent_hash for commit in commits if commit.parent_hash is not None}
    tips = sorted(
        commit.commit_hash for commit in commits if commit.commit_hash not in parent_hashes
    )
    if len(tips) > 1:
        findings.append(
            LedgerBranchFinding(
                finding_type="MultipleTips",
                message="Ledger has multiple apparent tips.",
                commit_hashes=tips,
                blocking=True,
            )
        )
    return sorted(findings, key=lambda item: (item.finding_type, item.commit_hashes))


def _duplicate_stage_findings(
    commits: list[LedgerCommit],
    policy: RerunPolicy,
) -> list[LedgerBranchFinding]:
    by_stage: dict[PipelineStage, list[LedgerCommit]] = defaultdict(list)
    for commit in commits:
        stage = _STAGE_START_ACTIONS.get(commit.action_type)
        if stage is not None:
            by_stage[stage].append(commit)
    blocking = policy != RerunPolicy.ALLOW_IF_FORCED
    findings: list[LedgerBranchFinding] = []
    for stage, stage_commits in sorted(by_stage.items(), key=lambda item: item[0].value):
        if len(stage_commits) <= 1:
            continue
        payloads = {canonical_json(commit.payload) for commit in stage_commits}
        findings.append(
            LedgerBranchFinding(
                finding_type="DuplicateMutatingStage",
                message=(
                    f"Mutating stage {stage.value} started {len(stage_commits)} times; "
                    f"distinct start payloads={len(payloads)}."
                ),
                commit_hashes=[commit.commit_hash for commit in stage_commits],
                stage_name=stage,
                blocking=blocking,
            )
        )
    return findings


def _decision(
    run_id: str,
    stage: PipelineStage,
    policy: RerunPolicy,
    status: StageRerunStatus,
    *,
    completed: bool,
    force: bool,
    should_run: bool,
    reason: str,
    should_skip: bool = False,
) -> StageRerunDecision:
    return StageRerunDecision(
        run_id=run_id,
        stage_name=stage,
        policy=policy,
        status=status,
        stage_completed=completed,
        force_requested=force,
        should_run=should_run,
        should_skip=should_skip,
        reason=reason,
    )


__all__ = ["decide_stage_rerun", "validate_ledger_tip"]
