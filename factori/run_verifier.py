"""Read-only deterministic replay verification for completed runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factori.config import DEFAULT_ROOT, LEDGER_FILENAME
from factori.hashing import sha256_file
from factori.ledger import LedgerError, ResearchLedger
from factori.release_gate import decide_release_gate
from factori.schemas import (
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactRef,
    ArtifactType,
    AuditCategory,
    AuditCheckStatus,
    AuditSeverity,
    BranchOutcomeSummary,
    ClaimTable,
    ControllerActionType,
    ExportReadinessReport,
    FinalAuditReport,
    LedgerCommit,
    PaperSkeleton,
    ReleaseGateDecision,
    ReplayCheck,
    ReplayFinding,
    ReplayStatus,
    ReplayVerificationReport,
    ResearchObject,
    RunVerificationSummary,
)


class ReplayVerificationError(RuntimeError):
    """Raised when replay prerequisites are missing."""


@dataclass(frozen=True)
class _LoadedArtifact:
    key: str
    commit: LedgerCommit | None
    artifact: ArtifactRef | None
    data: Any | None
    error: str | None = None


@dataclass(frozen=True)
class _ReplayDiskState:
    run_id: str
    root: Path
    ledger: ResearchLedger
    commits: list[LedgerCommit]
    commit_count_before: int
    artifact_manifest_hash_before: str | None
    artifact_manifest: _LoadedArtifact
    required_outputs: dict[str, _LoadedArtifact]


_REQUIRED_OUTPUTS: dict[str, tuple[ControllerActionType, str | None, type | None]] = {
    "final_nucleus": (ControllerActionType.FINAL_NUCLEUS_SELECTED, "id", None),
    "claim_table": (ControllerActionType.CLAIM_TABLE_BUILT, "claims", ClaimTable),
    "manuscript_plan": (ControllerActionType.MANUSCRIPT_PLAN_BUILT, "sections", None),
    "draft_skeleton": (ControllerActionType.DRAFT_SKELETON_BUILT, "section_stubs", None),
    "research_object": (
        ControllerActionType.RESEARCH_OBJECT_WRITTEN,
        "final_nucleus",
        ResearchObject,
    ),
    "paper_skeleton": (ControllerActionType.PAPER_SKELETON_WRITTEN, "paper_id", PaperSkeleton),
    "final_audit": (ControllerActionType.FINAL_AUDIT_REPORT_WRITTEN, "checks", FinalAuditReport),
    "release_gate": (ControllerActionType.RELEASE_GATE_DECIDED, "status", ReleaseGateDecision),
    "export_readiness": (
        ControllerActionType.EXPORT_READINESS_REPORT_WRITTEN,
        "ready_for_polished_prose",
        ExportReadinessReport,
    ),
    "branch_outcomes": (ControllerActionType.BRANCH_OUTCOMES_WRITTEN, "branch_outcomes", None),
}


def replay_verify_run(
    run_id: str,
    root: str | Path = DEFAULT_ROOT,
) -> ReplayVerificationReport:
    """Replay a completed run from disk without creating ledger commits."""
    state = _load_replay_disk_state(run_id, Path(root))
    checks: list[ReplayCheck] = []
    checks.extend(_ledger_checks(state.ledger))
    checks.extend(_required_output_checks(state))
    checks.extend(_artifact_hash_checks(state))
    checks.extend(_evidence_boundary_checks(state))
    checks.extend(_claim_and_branch_checks(state))
    checks.extend(_decision_consistency_checks(state, checks))
    checks.extend(_mutation_checks(state))
    return _build_report(state, checks)


def summarize_replay_verification(
    report: ReplayVerificationReport,
) -> RunVerificationSummary:
    """Build a compact replay verification summary."""
    return RunVerificationSummary(
        run_id=report.run_id,
        ledger_commits_checked=report.ledger_commits_checked,
        artifacts_checked=report.artifacts_checked,
        hashes_verified=report.hashes_verified,
        evidence_artifacts_checked=report.evidence_artifacts_checked,
        presentation_artifacts_checked=report.presentation_artifacts_checked,
        stage_outputs_checked=report.stage_outputs_checked,
        warnings=report.warnings_count,
        blocking_failures=report.blocking_failures_count,
        replay_status=report.replay_status,
        ledger_mutated=report.ledger_mutated,
        artifact_manifest_mutated=report.artifact_manifest_mutated,
    )


def _load_replay_disk_state(run_id: str, root: Path) -> _ReplayDiskState:
    ledger_path = root / "runs" / run_id / LEDGER_FILENAME
    if not ledger_path.is_file():
        raise ReplayVerificationError(
            "Export preparation artifacts not found; run factori prepare-export first"
        )
    ledger = ResearchLedger(ledger_path)
    commits = ledger.list_commits(run_id)
    if _latest_commit_with_key(
        commits,
        ControllerActionType.EXPORT_READINESS_REPORT_WRITTEN,
        "ready_for_polished_prose",
    ) is None:
        raise ReplayVerificationError(
            "Export preparation artifacts not found; run factori prepare-export first"
        )

    artifact_manifest = _load_output(
        key="artifact_manifest",
        root=root,
        commits=commits,
        action_type=ControllerActionType.ARTIFACT_MANIFEST_WRITTEN,
        required_key="artifacts",
        model=ArtifactManifest,
    )
    manifest_hash = _artifact_file_hash(root, artifact_manifest.artifact)
    required_outputs = {
        key: _load_output(
            key=key,
            root=root,
            commits=commits,
            action_type=action_type,
            required_key=required_key,
            model=model,
        )
        for key, (action_type, required_key, model) in _REQUIRED_OUTPUTS.items()
    }
    return _ReplayDiskState(
        run_id=run_id,
        root=root,
        ledger=ledger,
        commits=commits,
        commit_count_before=len(commits),
        artifact_manifest_hash_before=manifest_hash,
        artifact_manifest=artifact_manifest,
        required_outputs=required_outputs,
    )


def _load_output(
    *,
    key: str,
    root: Path,
    commits: list[LedgerCommit],
    action_type: ControllerActionType,
    required_key: str | None,
    model: type | None,
) -> _LoadedArtifact:
    commit = (
        _latest_commit_with_key(commits, action_type, required_key)
        if required_key is not None
        else _latest_commit(commits, action_type)
    )
    if commit is None:
        return _LoadedArtifact(
            key=key,
            commit=None,
            artifact=None,
            data=None,
            error=f"{action_type.value} commit not found",
        )
    artifact = _json_artifact(commit)
    if artifact is None:
        return _LoadedArtifact(
            key=key,
            commit=commit,
            artifact=None,
            data=None,
            error=f"{action_type.value} JSON artifact reference not found",
        )
    path = root / artifact.path
    if not path.is_file():
        return _LoadedArtifact(
            key=key,
            commit=commit,
            artifact=artifact,
            data=None,
            error=f"artifact file not found: {artifact.path}",
        )
    try:
        raw_data = json.loads(path.read_text(encoding="utf-8"))
        data = model.model_validate(raw_data) if model is not None else raw_data
    except (json.JSONDecodeError, OSError, ValidationError) as exc:
        return _LoadedArtifact(
            key=key,
            commit=commit,
            artifact=artifact,
            data=None,
            error=f"artifact file failed to load: {exc}",
        )
    return _LoadedArtifact(
        key=key,
        commit=commit,
        artifact=artifact,
        data=data,
    )


def _ledger_checks(ledger: ResearchLedger) -> list[ReplayCheck]:
    try:
        ledger.validate()
    except LedgerError as exc:
        return [
            _check(
                "ledger_hash_chain_valid",
                AuditCategory.LEDGER_INTEGRITY,
                False,
                f"ledger continuity/hash chain failed: {exc}",
                expected="valid ledger hash chain",
                observed=str(exc),
            )
        ]
    return [
        _check(
            "ledger_hash_chain_valid",
            AuditCategory.LEDGER_INTEGRITY,
            True,
            "ledger continuity/hash chain is valid",
            expected="valid ledger hash chain",
            observed="valid",
        )
    ]


def _required_output_checks(state: _ReplayDiskState) -> list[ReplayCheck]:
    checks = [
        _loaded_output_check(
            state.artifact_manifest,
            "artifact_manifest_loaded",
            "artifact manifest is loadable from disk",
        )
    ]
    labels = {
        "final_nucleus": "final nucleus exists",
        "claim_table": "claim table exists",
        "manuscript_plan": "manuscript plan exists",
        "draft_skeleton": "draft skeleton exists",
        "research_object": "research object exists",
        "paper_skeleton": "paper skeleton exists",
        "final_audit": "final audit report exists",
        "release_gate": "release gate decision exists",
        "export_readiness": "export readiness report exists",
        "branch_outcomes": "branch outcome summary exists",
    }
    checks.extend(
        _loaded_output_check(
            loaded,
            f"{key}_loaded",
            labels[key],
        )
        for key, loaded in state.required_outputs.items()
    )
    return checks


def _artifact_hash_checks(state: _ReplayDiskState) -> list[ReplayCheck]:
    checks: list[ReplayCheck] = []
    manifest = state.artifact_manifest.data
    if isinstance(manifest, ArtifactManifest):
        for entry in manifest.artifacts:
            checks.append(_manifest_entry_hash_check(state.root, entry))
    for artifact in _unique_ledger_artifacts(state.commits):
        checks.append(_artifact_ref_hash_check(state.root, artifact))
    return checks


def _evidence_boundary_checks(state: _ReplayDiskState) -> list[ReplayCheck]:
    manifest = state.artifact_manifest.data
    checks: list[ReplayCheck] = []
    if isinstance(manifest, ArtifactManifest):
        evidence_entries = [entry for entry in manifest.artifacts if entry.is_evidence]
        presentation_entries = [entry for entry in manifest.artifacts if entry.is_presentation]
        checks.append(
            _check(
                "evidence_artifacts_have_producing_commits",
                AuditCategory.EVIDENCE_BOUNDARY,
                all(entry.producing_commit_hash for entry in evidence_entries),
                "evidence artifacts have producing commit hashes"
                if all(entry.producing_commit_hash for entry in evidence_entries)
                else "one or more evidence artifacts lack producing commit hashes",
                expected="all evidence artifacts have producing commit hashes",
                observed=str(
                    sorted(
                        entry.artifact_id
                        for entry in evidence_entries
                        if not entry.producing_commit_hash
                    )
                ),
            )
        )
        evidence_presentation = [
            entry.artifact_id
            for entry in evidence_entries
            if entry.is_presentation or _is_markdown_or_latex_entry(entry)
        ]
        checks.append(
            _check(
                "presentation_artifacts_not_verification_evidence",
                AuditCategory.EVIDENCE_BOUNDARY,
                not evidence_presentation,
                "presentation artifacts are not marked as verification evidence"
                if not evidence_presentation
                else f"presentation artifacts marked as evidence: {evidence_presentation}",
                expected="no Markdown, LaTeX, or presentation evidence",
                observed=str(evidence_presentation),
            )
        )
        checks.append(
            _check(
                "presentation_artifacts_checked",
                AuditCategory.ARTIFACT_INTEGRITY,
                True,
                f"presentation artifacts checked: {len(presentation_entries)}",
                observed=str(len(presentation_entries)),
            )
        )

    claim_table = state.required_outputs["claim_table"].data
    if isinstance(claim_table, ClaimTable) and isinstance(manifest, ArtifactManifest):
        by_id = {entry.artifact_id: entry for entry in manifest.artifacts}
        presentation_claim_evidence = [
            artifact_id
            for claim in claim_table.claims
            for artifact_id in claim.evidence_artifact_ids
            if artifact_id in by_id
            and (
                by_id[artifact_id].is_presentation
                or _is_markdown_or_latex_entry(by_id[artifact_id])
            )
        ]
        checks.append(
            _check(
                "claim_evidence_not_presentation",
                AuditCategory.EVIDENCE_BOUNDARY,
                not presentation_claim_evidence,
                "claims do not use presentation artifacts as verification evidence"
                if not presentation_claim_evidence
                else f"claims use presentation evidence: {presentation_claim_evidence}",
                expected="no presentation evidence in claim table",
                observed=str(presentation_claim_evidence),
            )
        )
    return checks


def _claim_and_branch_checks(state: _ReplayDiskState) -> list[ReplayCheck]:
    checks: list[ReplayCheck] = []
    claim_table = state.required_outputs["claim_table"].data
    paper_skeleton = state.required_outputs["paper_skeleton"].data
    branch_outcomes_loaded = state.required_outputs["branch_outcomes"].data
    research_object = state.required_outputs["research_object"].data
    branch_outcomes = _branch_outcomes(branch_outcomes_loaded)
    checks.append(_real_data_label_check(state, claim_table, branch_outcomes))
    checks.append(_blocked_claims_represented_check(claim_table, paper_skeleton))
    checks.append(
        _failed_or_deferred_represented_check(
            branch_outcomes,
            research_object,
            paper_skeleton,
        )
    )
    checks.append(_runtime_summary_not_provenance_check(paper_skeleton))
    return checks


def _decision_consistency_checks(
    state: _ReplayDiskState,
    prior_checks: list[ReplayCheck],
) -> list[ReplayCheck]:
    checks: list[ReplayCheck] = []
    final_audit = state.required_outputs["final_audit"].data
    release_gate = state.required_outputs["release_gate"].data
    export_readiness = state.required_outputs["export_readiness"].data
    replay_blocking_so_far = sum(
        1
        for check in prior_checks
        if check.status == AuditCheckStatus.FAIL and check.severity == AuditSeverity.BLOCKING
    )
    if isinstance(final_audit, FinalAuditReport):
        final_audit_matches = (
            (replay_blocking_so_far == 0 and final_audit.blocking_failures_count == 0)
            or (replay_blocking_so_far > 0 and final_audit.blocking_failures_count > 0)
        )
        checks.append(
            _check(
                "final_audit_consistent_with_replay",
                AuditCategory.REPRODUCIBILITY_READINESS,
                final_audit_matches,
                "final audit decision is consistent with replay findings"
                if final_audit_matches
                else "final audit decision does not match current replay findings",
                expected="final audit blocking count tracks current replay state",
                observed=(
                    f"replay_blocking={replay_blocking_so_far}; "
                    f"final_audit_blocking={final_audit.blocking_failures_count}"
                ),
            )
        )

    if isinstance(final_audit, FinalAuditReport) and isinstance(release_gate, ReleaseGateDecision):
        recomputed = decide_release_gate(final_audit)
        release_matches = (
            release_gate.status == recomputed.status
            and release_gate.ready_for_polished_prose == recomputed.ready_for_polished_prose
            and release_gate.ready_for_latex_export == recomputed.ready_for_latex_export
            and release_gate.ready_for_external_review == recomputed.ready_for_external_review
        )
        checks.append(
            _check(
                "release_gate_consistent_with_final_audit",
                AuditCategory.REPRODUCIBILITY_READINESS,
                release_matches,
                "release gate is consistent with final audit"
                if release_matches
                else "release gate is inconsistent with final audit",
                expected=recomputed.status.value,
                observed=release_gate.status.value,
            )
        )

    if isinstance(release_gate, ReleaseGateDecision) and isinstance(
        export_readiness,
        ExportReadinessReport,
    ):
        export_matches = (
            release_gate.ready_for_polished_prose
            == export_readiness.ready_for_polished_prose
            and release_gate.ready_for_latex_export
            == export_readiness.ready_for_latex_export
        )
        if release_gate.blocking_reasons and not export_readiness.export_blocked:
            export_matches = False
        if not release_gate.blocking_reasons and export_readiness.blocking_reasons:
            export_matches = False
        checks.append(
            _check(
                "export_readiness_consistent_with_release_gate",
                AuditCategory.REPRODUCIBILITY_READINESS,
                export_matches,
                "export readiness is consistent with release gate"
                if export_matches
                else "export readiness is inconsistent with release gate",
                expected=(
                    f"polished={release_gate.ready_for_polished_prose}; "
                    f"latex={release_gate.ready_for_latex_export}"
                ),
                observed=(
                    f"polished={export_readiness.ready_for_polished_prose}; "
                    f"latex={export_readiness.ready_for_latex_export}; "
                    f"blocked={export_readiness.export_blocked}"
                ),
            )
        )
    return checks


def _mutation_checks(state: _ReplayDiskState) -> list[ReplayCheck]:
    after_commits = state.ledger.list_commits(state.run_id)
    manifest_hash_after = _artifact_file_hash(state.root, state.artifact_manifest.artifact)
    ledger_mutated = len(after_commits) != state.commit_count_before
    artifact_manifest_mutated = manifest_hash_after != state.artifact_manifest_hash_before
    return [
        _check(
            "replay_did_not_mutate_ledger",
            AuditCategory.LEDGER_INTEGRITY,
            not ledger_mutated,
            "replay did not mutate ledger"
            if not ledger_mutated
            else "replay mutated ledger commit count",
            expected=str(state.commit_count_before),
            observed=str(len(after_commits)),
        ),
        _check(
            "replay_did_not_mutate_artifact_manifest",
            AuditCategory.ARTIFACT_INTEGRITY,
            not artifact_manifest_mutated,
            "replay did not mutate artifact manifest"
            if not artifact_manifest_mutated
            else "replay mutated artifact manifest",
            expected=str(state.artifact_manifest_hash_before),
            observed=str(manifest_hash_after),
        ),
    ]


def _build_report(
    state: _ReplayDiskState,
    checks: list[ReplayCheck],
) -> ReplayVerificationReport:
    warnings = [
        check
        for check in checks
        if check.status == AuditCheckStatus.WARNING or check.severity == AuditSeverity.WARNING
    ]
    blocking_failures = [
        check
        for check in checks
        if check.status == AuditCheckStatus.FAIL and check.severity == AuditSeverity.BLOCKING
    ]
    replay_status = ReplayStatus.REPLAY_VERIFIED
    if blocking_failures:
        replay_status = ReplayStatus.REPLAY_FAILED
    elif warnings:
        replay_status = ReplayStatus.REPLAY_VERIFIED_WITH_WARNINGS
    manifest = state.artifact_manifest.data
    manifest_entries = manifest.artifacts if isinstance(manifest, ArtifactManifest) else []
    ledger_artifacts = _unique_ledger_artifacts(state.commits)
    artifact_count = len(
        {entry.path for entry in manifest_entries} | {ref.path for ref in ledger_artifacts}
    )
    hash_passes = [
        check
        for check in checks
        if check.check_id.startswith(("artifact_hash:", "manifest_hash:"))
        and check.status == AuditCheckStatus.PASS
    ]
    evidence_count = sum(1 for entry in manifest_entries if entry.is_evidence)
    presentation_count = sum(1 for entry in manifest_entries if entry.is_presentation)
    stage_outputs_checked = sum(
        1
        for check in checks
        if check.check_id.endswith("_loaded") and check.status == AuditCheckStatus.PASS
    )
    ledger_mutated = any(
        check.check_id == "replay_did_not_mutate_ledger"
        and check.status == AuditCheckStatus.FAIL
        for check in checks
    )
    artifact_manifest_mutated = any(
        check.check_id == "replay_did_not_mutate_artifact_manifest"
        and check.status == AuditCheckStatus.FAIL
        for check in checks
    )
    findings = [
        ReplayFinding(
            check_id=check.check_id,
            category=check.category,
            status=check.status,
            severity=check.severity,
            message=check.message,
            expected=check.expected,
            observed=check.observed,
            artifact_refs=check.artifact_refs,
            commit_refs=check.commit_refs,
        )
        for check in checks
        if check.status in {AuditCheckStatus.WARNING, AuditCheckStatus.FAIL}
    ]
    return ReplayVerificationReport(
        run_id=state.run_id,
        checks=checks,
        findings=findings,
        replay_status=replay_status,
        ledger_commits_checked=state.commit_count_before,
        artifacts_checked=artifact_count,
        hashes_verified=len(hash_passes),
        evidence_artifacts_checked=evidence_count,
        presentation_artifacts_checked=presentation_count,
        stage_outputs_checked=stage_outputs_checked,
        warnings_count=len(warnings),
        blocking_failures_count=len(blocking_failures),
        ledger_mutated=ledger_mutated,
        artifact_manifest_mutated=artifact_manifest_mutated,
    )


def _loaded_output_check(
    loaded: _LoadedArtifact,
    check_id: str,
    success_message: str,
) -> ReplayCheck:
    passed = loaded.error is None
    commit_refs = [loaded.commit.commit_hash] if loaded.commit is not None else []
    artifacts = [loaded.artifact] if loaded.artifact is not None else []
    return _check(
        check_id,
        AuditCategory.PROVENANCE_COMPLETENESS,
        passed,
        success_message if passed else f"{success_message} failed: {loaded.error}",
        expected="loadable JSON artifact",
        observed="loaded" if passed else loaded.error,
        artifacts=artifacts,
        commit_refs=commit_refs,
    )


def _manifest_entry_hash_check(root: Path, entry: ArtifactManifestEntry) -> ReplayCheck:
    path = root / entry.path
    if not path.is_file():
        return _check(
            f"manifest_hash:{entry.artifact_id}",
            AuditCategory.ARTIFACT_INTEGRITY,
            False,
            f"manifest artifact missing: {entry.path}",
            expected=entry.content_hash,
            observed="missing",
        )
    observed = sha256_file(path)
    return _check(
        f"manifest_hash:{entry.artifact_id}",
        AuditCategory.ARTIFACT_INTEGRITY,
        observed == entry.content_hash,
        f"manifest artifact hash verified: {entry.artifact_id}"
        if observed == entry.content_hash
        else f"manifest artifact hash mismatch: {entry.artifact_id}",
        expected=entry.content_hash,
        observed=observed,
    )


def _artifact_ref_hash_check(root: Path, artifact: ArtifactRef) -> ReplayCheck:
    path = root / artifact.path
    if not path.is_file():
        return _check(
            f"artifact_hash:{artifact.id}",
            AuditCategory.ARTIFACT_INTEGRITY,
            False,
            f"ledger artifact missing: {artifact.path}",
            expected=artifact.content_hash,
            observed="missing",
            artifacts=[artifact],
            commit_refs=[artifact.producing_commit_hash] if artifact.producing_commit_hash else [],
        )
    observed = sha256_file(path)
    return _check(
        f"artifact_hash:{artifact.id}",
        AuditCategory.ARTIFACT_INTEGRITY,
        observed == artifact.content_hash,
        f"ledger artifact hash verified: {artifact.id}"
        if observed == artifact.content_hash
        else f"ledger artifact hash mismatch: {artifact.id}",
        expected=artifact.content_hash,
        observed=observed,
        artifacts=[artifact],
        commit_refs=[artifact.producing_commit_hash] if artifact.producing_commit_hash else [],
    )


def _real_data_label_check(
    state: _ReplayDiskState,
    claim_table: Any,
    branch_outcomes: list[BranchOutcomeSummary],
) -> ReplayCheck:
    offenders: list[str] = []
    if isinstance(claim_table, ClaimTable):
        offenders.extend(
            claim.claim_id
            for claim in claim_table.claims
            if claim.claim_label.value == "RealDataExperimentVerified"
        )
    offenders.extend(
        outcome.candidate_id
        for outcome in branch_outcomes
        if outcome.verification_label is not None
        and outcome.verification_label.value == "RealDataExperimentVerified"
    )
    for loaded in state.required_outputs.values():
        if _payload_contains_value(_raw_data(loaded), "RealDataExperimentVerified"):
            offenders.append(loaded.key)
    offenders = sorted(set(offenders))
    return _check(
        "no_real_data_experiment_verified_in_mvp",
        AuditCategory.SYNTHETIC_DATA_BOUNDARY,
        not offenders,
        "RealDataExperimentVerified does not appear in MVP outputs"
        if not offenders
        else f"RealDataExperimentVerified appears in: {offenders}",
        expected="no RealDataExperimentVerified labels",
        observed=str(offenders),
    )


def _blocked_claims_represented_check(claim_table: Any, paper_skeleton: Any) -> ReplayCheck:
    if not isinstance(claim_table, ClaimTable):
        return _warning(
            "blocked_claims_represented",
            AuditCategory.BLOCKED_CLAIM_HANDLING,
            "claim table unavailable; blocked claim representation could not be checked",
        )
    blocked = [
        claim.claim_id
        for claim in claim_table.claims
        if not claim.allowed_in_main_text or claim.claim_label.value == "Unsupported"
    ]
    if not blocked:
        return _not_applicable(
            "blocked_claims_represented",
            AuditCategory.BLOCKED_CLAIM_HANDLING,
            "no blocked claims present",
        )
    appendix_lines: list[str] = []
    if isinstance(paper_skeleton, PaperSkeleton):
        for appendix in paper_skeleton.appendices:
            if "Blocked" in appendix.title:
                appendix_lines.extend(appendix.content_lines)
    represented = [
        claim_id for claim_id in blocked if any(claim_id in line for line in appendix_lines)
    ]
    return _check(
        "blocked_claims_represented",
        AuditCategory.BLOCKED_CLAIM_HANDLING,
        set(represented) == set(blocked),
        "blocked claims are represented in the blocked-claims appendix"
        if set(represented) == set(blocked)
        else f"blocked claims missing from appendix: {sorted(set(blocked) - set(represented))}",
        expected=str(sorted(blocked)),
        observed=str(sorted(represented)),
    )


def _failed_or_deferred_represented_check(
    branch_outcomes: list[BranchOutcomeSummary],
    research_object: Any,
    paper_skeleton: Any,
) -> ReplayCheck:
    interesting = {
        "PrunedDuplicate",
        "RejectedRedTeam",
        "PrunedUncertain",
        "InsufficientRetrievalAdequacy",
        "DeferredRealDataCandidate",
        "RequiresRealData",
        "StagnationStop",
        "BudgetDeferred",
    }
    failed = [outcome for outcome in branch_outcomes if outcome.outcome in interesting]
    if not failed:
        return _not_applicable(
            "failed_deferred_pruned_branches_represented",
            AuditCategory.RESEARCH_OBJECT_COMPLETENESS,
            "no failed, deferred, or pruned branches present",
        )
    research_ref_present = (
        isinstance(research_object, ResearchObject)
        and research_object.branch_outcomes_ref is not None
    )
    appendix_present = False
    if isinstance(paper_skeleton, PaperSkeleton):
        appendix_present = any(
            "Failed" in appendix.title or "Deferred" in appendix.title or "Pruned" in appendix.title
            for appendix in paper_skeleton.appendices
        )
    return _check(
        "failed_deferred_pruned_branches_represented",
        AuditCategory.RESEARCH_OBJECT_COMPLETENESS,
        research_ref_present and appendix_present,
        "failed/deferred/pruned branches are represented in package and appendix"
        if research_ref_present and appendix_present
        else "failed/deferred/pruned branches are not fully represented",
        expected="branch outcome ref and failed/deferred/pruned appendix",
        observed=f"research_ref={research_ref_present}; appendix={appendix_present}",
    )


def _runtime_summary_not_provenance_check(paper_skeleton: Any) -> ReplayCheck:
    if not isinstance(paper_skeleton, PaperSkeleton):
        return _warning(
            "runtime_summary_not_provenance",
            AuditCategory.PROVENANCE_COMPLETENESS,
            "paper skeleton unavailable; runtime provenance check could not be completed",
        )
    offenders = [key for key in paper_skeleton.provenance_refs if "runtime" in key.lower()]
    return _check(
        "runtime_summary_not_provenance",
        AuditCategory.PROVENANCE_COMPLETENESS,
        not offenders,
        "runtime summaries are not treated as provenance"
        if not offenders
        else f"runtime summaries used as provenance: {offenders}",
        expected="no runtime summary provenance refs",
        observed=str(offenders),
    )


def _branch_outcomes(raw: Any) -> list[BranchOutcomeSummary]:
    if not isinstance(raw, dict):
        return []
    outcomes = raw.get("branch_outcomes", [])
    parsed: list[BranchOutcomeSummary] = []
    for item in outcomes:
        try:
            parsed.append(BranchOutcomeSummary.model_validate(item))
        except ValidationError:
            continue
    return parsed


def _unique_ledger_artifacts(commits: list[LedgerCommit]) -> list[ArtifactRef]:
    artifacts: dict[tuple[str, str], ArtifactRef] = {}
    for commit in commits:
        for artifact in commit.artifact_refs:
            artifacts[(artifact.path, artifact.id)] = artifact
    return [artifacts[key] for key in sorted(artifacts)]


def _json_artifact(commit: LedgerCommit) -> ArtifactRef | None:
    for artifact in commit.artifact_refs:
        if artifact.path.endswith(".json"):
            return artifact
    return commit.artifact_refs[0] if commit.artifact_refs else None


def _artifact_file_hash(root: Path, artifact: ArtifactRef | None) -> str | None:
    if artifact is None:
        return None
    path = root / artifact.path
    return sha256_file(path) if path.is_file() else None


def _latest_commit(
    commits: list[LedgerCommit],
    action_type: ControllerActionType,
) -> LedgerCommit | None:
    for commit in reversed(commits):
        if commit.action_type == action_type:
            return commit
    return None


def _latest_commit_with_key(
    commits: list[LedgerCommit],
    action_type: ControllerActionType,
    key: str | None,
) -> LedgerCommit | None:
    for commit in reversed(commits):
        if commit.action_type != action_type:
            continue
        if key is None or key in commit.payload:
            return commit
    return None


def _raw_data(loaded: _LoadedArtifact) -> Any:
    if hasattr(loaded.data, "model_dump"):
        return loaded.data.model_dump(mode="json")
    return loaded.data


def _payload_contains_value(payload: Any, value: str) -> bool:
    if isinstance(payload, dict):
        return any(_payload_contains_value(item, value) for item in payload.values())
    if isinstance(payload, list):
        return any(_payload_contains_value(item, value) for item in payload)
    return str(payload) == value


def _is_markdown_or_latex_entry(entry: ArtifactManifestEntry) -> bool:
    suffix = entry.path.rsplit(".", maxsplit=1)[-1].lower() if "." in entry.path else ""
    return entry.artifact_type == ArtifactType.LATEX or suffix in {"md", "markdown", "tex", "pdf"}


def _check(
    check_id: str,
    category: AuditCategory,
    passed: bool,
    message: str,
    *,
    expected: str | None = None,
    observed: str | None = None,
    artifacts: list[ArtifactRef] | None = None,
    commit_refs: list[str] | None = None,
) -> ReplayCheck:
    return ReplayCheck(
        check_id=check_id,
        category=category,
        status=AuditCheckStatus.PASS if passed else AuditCheckStatus.FAIL,
        severity=AuditSeverity.INFO if passed else AuditSeverity.BLOCKING,
        message=message,
        expected=expected,
        observed=observed,
        artifact_refs=artifacts or [],
        commit_refs=commit_refs or [],
    )


def _warning(check_id: str, category: AuditCategory, message: str) -> ReplayCheck:
    return ReplayCheck(
        check_id=check_id,
        category=category,
        status=AuditCheckStatus.WARNING,
        severity=AuditSeverity.WARNING,
        message=message,
    )


def _not_applicable(check_id: str, category: AuditCategory, message: str) -> ReplayCheck:
    return ReplayCheck(
        check_id=check_id,
        category=category,
        status=AuditCheckStatus.NOT_APPLICABLE,
        severity=AuditSeverity.INFO,
        message=message,
    )
