"""Read-only deterministic comparison of two completed fActorI runs."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factori.config import DEFAULT_ROOT, LEDGER_FILENAME
from factori.hashing import canonical_json, sha256_file
from factori.ledger import ResearchLedger
from factori.regression_diagnostics import detect_regressions, regression_status
from factori.reports import render_cross_run_comparison_markdown
from factori.schemas import (
    ArtifactManifest,
    ArtifactManifestEntry,
    AuditCategory,
    AuditCheckStatus,
    BranchOutcomeSummary,
    Claim,
    ClaimTable,
    CrossRunComparisonReport,
    DiagnosticReport,
    ExportReadinessReport,
    FinalAuditReport,
    LedgerSummary,
    PaperSkeleton,
    RegressionCategory,
    RegressionSeverity,
    RegressionStatus,
    ReleaseGateDecision,
    ReplayVerificationReport,
    ResearchObject,
    RunDifference,
    VerificationLabel,
)


class CrossRunError(RuntimeError):
    """Raised when either run directory does not exist."""


@dataclass(frozen=True)
class _RunSnapshot:
    run_id: str
    run_path: Path
    ledger_summary: LedgerSummary | None
    artifact_manifest: ArtifactManifest | None
    branch_outcomes: list[BranchOutcomeSummary] | None
    research_object: ResearchObject | None
    final_audit: FinalAuditReport | None
    release_gate: ReleaseGateDecision | None
    export_readiness: ExportReadinessReport | None
    replay_report: ReplayVerificationReport | None
    diagnostic_report: DiagnosticReport | None
    paper_skeleton: PaperSkeleton | None
    claim_table: ClaimTable | None
    sources_loaded: list[str]
    load_errors: list[str]
    ledger_count_before: int | None
    artifact_manifest_hash_before: str | None


_REQUIRED_OUTPUTS = {
    "ledger_summary",
    "artifact_manifest",
    "branch_outcomes",
    "research_object",
    "final_audit",
    "release_gate",
    "export_readiness",
    "paper_skeleton",
    "claim_table",
}

_OPTIONAL_OUTPUTS = {"replay_report", "diagnostic_report"}

_LABEL_STRENGTH = {
    VerificationLabel.UNSUPPORTED: 0,
    VerificationLabel.LIMITATION: 1,
    VerificationLabel.NEGATIVE_RESULT: 1,
    VerificationLabel.CONJECTURE: 1,
    VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED: 2,
    VerificationLabel.EXPERIMENT_VERIFIED: 2,
    VerificationLabel.LEAN_VERIFIED: 2,
    VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED: 3,
}


def compare_runs(
    baseline_run_id: str,
    candidate_run_id: str,
    root: str | Path = DEFAULT_ROOT,
) -> CrossRunComparisonReport:
    """Compare two completed runs from disk without mutating either run."""
    root_path = Path(root)
    baseline_path = root_path / "runs" / baseline_run_id
    candidate_path = root_path / "runs" / candidate_run_id
    if not baseline_path.is_dir():
        raise CrossRunError(f"Baseline run does not exist: {baseline_run_id}")
    if not candidate_path.is_dir():
        raise CrossRunError(f"Candidate run does not exist: {candidate_run_id}")

    baseline = _load_snapshot(baseline_run_id, baseline_path)
    candidate = _load_snapshot(candidate_run_id, candidate_path)
    differences = _compare_snapshots(baseline, candidate)

    baseline_ledger_after = _ledger_count(baseline.run_path, baseline.run_id)
    candidate_ledger_after = _ledger_count(candidate.run_path, candidate.run_id)
    baseline_manifest_after = _manifest_hash(baseline.run_path)
    candidate_manifest_after = _manifest_hash(candidate.run_path)
    baseline_ledger_mutated = baseline.ledger_count_before != baseline_ledger_after
    candidate_ledger_mutated = candidate.ledger_count_before != candidate_ledger_after
    baseline_manifest_mutated = (
        baseline.artifact_manifest_hash_before != baseline_manifest_after
    )
    candidate_manifest_mutated = (
        candidate.artifact_manifest_hash_before != candidate_manifest_after
    )
    if baseline_ledger_mutated or candidate_ledger_mutated:
        differences.append(
            _difference(
                "comparison-ledger-mutation",
                RegressionCategory.LEDGER_DRIFT,
                RegressionSeverity.BLOCKING,
                "comparison.ledger_mutated",
                False,
                True,
                "Cross-run comparison changed a ledger commit count",
                is_regression=True,
            )
        )
    if baseline_manifest_mutated or candidate_manifest_mutated:
        differences.append(
            _difference(
                "comparison-manifest-mutation",
                RegressionCategory.ARTIFACT_DRIFT,
                RegressionSeverity.BLOCKING,
                "comparison.artifact_manifest_mutated",
                False,
                True,
                "Cross-run comparison changed an artifact manifest",
                is_regression=True,
            )
        )

    errors = sorted(
        f"{baseline_run_id}: {error}" for error in baseline.load_errors
    ) + sorted(f"{candidate_run_id}: {error}" for error in candidate.load_errors)
    provisional = CrossRunComparisonReport(
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        differences=sorted(differences, key=lambda item: item.difference_id),
        regression_status=RegressionStatus.NO_REGRESSION,
        sources_loaded={
            baseline_run_id: sorted(baseline.sources_loaded),
            candidate_run_id: sorted(candidate.sources_loaded),
        },
        comparison_errors=errors,
        baseline_release_status=(
            baseline.release_gate.status if baseline.release_gate is not None else None
        ),
        candidate_release_status=(
            candidate.release_gate.status if candidate.release_gate is not None else None
        ),
        baseline_replay_status=(
            baseline.replay_report.replay_status
            if baseline.replay_report is not None
            else None
        ),
        candidate_replay_status=(
            candidate.replay_report.replay_status
            if candidate.replay_report is not None
            else None
        ),
        baseline_diagnostic_status=(
            baseline.diagnostic_report.diagnostic_status
            if baseline.diagnostic_report is not None
            else None
        ),
        candidate_diagnostic_status=(
            candidate.diagnostic_report.diagnostic_status
            if candidate.diagnostic_report is not None
            else None
        ),
        baseline_ledger_mutated=baseline_ledger_mutated,
        candidate_ledger_mutated=candidate_ledger_mutated,
        baseline_artifact_manifest_mutated=baseline_manifest_mutated,
        candidate_artifact_manifest_mutated=candidate_manifest_mutated,
        ledger_mutated=baseline_ledger_mutated or candidate_ledger_mutated,
        artifact_manifest_mutated=(
            baseline_manifest_mutated or candidate_manifest_mutated
        ),
    )
    findings = detect_regressions(provisional)
    return provisional.model_copy(
        update={
            "regression_findings": findings,
            "regression_status": regression_status(findings, errors),
        }
    )


def write_cross_run_report(
    *,
    report: CrossRunComparisonReport,
    root: str | Path = DEFAULT_ROOT,
) -> tuple[Path, Path]:
    """Write optional comparison reports outside provenance and the artifact store."""
    comparison_path = (
        Path(root) / "runs" / report.candidate_run_id / "comparisons"
    )
    comparison_path.mkdir(parents=True, exist_ok=True)
    stem = (
        f"comparison-{_safe_id(report.baseline_run_id)}-vs-"
        f"{_safe_id(report.candidate_run_id)}"
    )
    json_path = comparison_path / f"{stem}.json"
    markdown_path = comparison_path / f"{stem}.md"
    payload = {
        "not_provenance": True,
        "not_evidence": True,
        "not_ledgered": True,
        "report": report.model_dump(mode="json"),
    }
    json_path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    markdown = "\n".join(
        [
            "---",
            "not_provenance: true",
            "not_evidence: true",
            "not_ledgered: true",
            "---",
            "",
            render_cross_run_comparison_markdown(comparison_report=report),
        ]
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def _load_snapshot(run_id: str, run_path: Path) -> _RunSnapshot:
    loaded: list[str] = []
    errors: list[str] = []
    return _RunSnapshot(
        run_id=run_id,
        run_path=run_path,
        ledger_summary=_load_model(
            run_path / "research_object" / "ledger-summary.json",
            LedgerSummary,
            "ledger_summary",
            loaded,
            errors,
        ),
        artifact_manifest=_load_model(
            run_path / "research_object" / "artifact-manifest.json",
            ArtifactManifest,
            "artifact_manifest",
            loaded,
            errors,
        ),
        branch_outcomes=_load_branch_outcomes(
            run_path / "research_object" / "branch-outcomes.json",
            loaded,
            errors,
        ),
        research_object=_load_model(
            run_path / "research_object" / "research-object.json",
            ResearchObject,
            "research_object",
            loaded,
            errors,
        ),
        final_audit=_load_model(
            run_path / "reports" / "final-audit-report.json",
            FinalAuditReport,
            "final_audit",
            loaded,
            errors,
        ),
        release_gate=_load_model(
            run_path / "reports" / "release-gate-decision.json",
            ReleaseGateDecision,
            "release_gate",
            loaded,
            errors,
        ),
        export_readiness=_load_model(
            run_path / "reports" / "export-readiness-report.json",
            ExportReadinessReport,
            "export_readiness",
            loaded,
            errors,
        ),
        replay_report=_load_wrapped_model(
            run_path / "replay" / "replay-verification-report.json",
            ReplayVerificationReport,
            "replay_report",
            loaded,
            errors,
        ),
        diagnostic_report=_load_wrapped_model(
            run_path / "diagnostics" / "diagnostic-report.json",
            DiagnosticReport,
            "diagnostic_report",
            loaded,
            errors,
        ),
        paper_skeleton=_load_model(
            run_path / "research_object" / "paper-skeleton.json",
            PaperSkeleton,
            "paper_skeleton",
            loaded,
            errors,
        ),
        claim_table=_load_model(
            run_path / "reports" / "claim-table.json",
            ClaimTable,
            "claim_table",
            loaded,
            errors,
        ),
        sources_loaded=loaded,
        load_errors=errors,
        ledger_count_before=_ledger_count(run_path, run_id),
        artifact_manifest_hash_before=_manifest_hash(run_path),
    )


def _compare_snapshots(
    baseline: _RunSnapshot,
    candidate: _RunSnapshot,
) -> list[RunDifference]:
    differences: list[RunDifference] = []
    if baseline.run_id != candidate.run_id:
        differences.append(
            _difference(
                "run-id",
                RegressionCategory.UNKNOWN,
                RegressionSeverity.INFO,
                "run_id",
                baseline.run_id,
                candidate.run_id,
                "Expected run ID difference",
            )
        )
    differences.extend(_presence_differences(baseline, candidate))
    differences.extend(_ledger_differences(baseline, candidate))
    differences.extend(_artifact_differences(baseline, candidate))
    differences.extend(_research_object_differences(baseline, candidate))
    differences.extend(_branch_differences(baseline, candidate))
    differences.extend(_claim_differences(baseline, candidate))
    differences.extend(_audit_differences(baseline, candidate))
    differences.extend(_release_and_export_differences(baseline, candidate))
    differences.extend(_optional_report_differences(baseline, candidate))
    return differences


def _presence_differences(
    baseline: _RunSnapshot,
    candidate: _RunSnapshot,
) -> list[RunDifference]:
    differences: list[RunDifference] = []
    for output in sorted(_REQUIRED_OUTPUTS | _OPTIONAL_OUTPUTS):
        baseline_present = getattr(baseline, output) is not None
        candidate_present = getattr(candidate, output) is not None
        if baseline_present == candidate_present:
            continue
        optional = output in _OPTIONAL_OUTPUTS
        missing_candidate = baseline_present and not candidate_present
        differences.append(
            _difference(
                f"output-presence-{output}",
                RegressionCategory.MISSING_OUTPUT,
                (
                    RegressionSeverity.INFO
                    if optional or not missing_candidate
                    else RegressionSeverity.BLOCKING
                ),
                f"outputs.{output}.present",
                baseline_present,
                candidate_present,
                (
                    f"Optional output presence changed: {output}"
                    if optional
                    else f"Required candidate output is missing: {output}"
                    if missing_candidate
                    else f"Candidate added required output: {output}"
                ),
                is_regression=missing_candidate and not optional,
                optional_output=optional,
            )
        )
    return differences


def _ledger_differences(
    baseline: _RunSnapshot,
    candidate: _RunSnapshot,
) -> list[RunDifference]:
    if baseline.ledger_summary is None or candidate.ledger_summary is None:
        return []
    differences: list[RunDifference] = []
    _append_value_difference(
        differences,
        "ledger-commit-count",
        RegressionCategory.LEDGER_DRIFT,
        "ledger.commit_count",
        baseline.ledger_summary.commit_count,
        candidate.ledger_summary.commit_count,
        RegressionSeverity.WARNING,
        "Ledger commit count changed",
    )
    _append_value_difference(
        differences,
        "ledger-action-type-counts",
        RegressionCategory.STAGE_COUNT_CHANGE,
        "ledger.action_type_counts",
        baseline.ledger_summary.action_type_counts,
        candidate.ledger_summary.action_type_counts,
        RegressionSeverity.WARNING,
        "Ledger action type counts changed",
    )
    _append_value_difference(
        differences,
        "candidate-count",
        RegressionCategory.CANDIDATE_COUNT_CHANGE,
        "ledger.candidate_count",
        baseline.ledger_summary.candidate_count,
        candidate.ledger_summary.candidate_count,
        RegressionSeverity.WARNING,
        "Candidate count changed",
    )
    return differences


def _artifact_differences(
    baseline: _RunSnapshot,
    candidate: _RunSnapshot,
) -> list[RunDifference]:
    if baseline.artifact_manifest is None or candidate.artifact_manifest is None:
        return []
    differences: list[RunDifference] = []
    base_manifest = baseline.artifact_manifest
    cand_manifest = candidate.artifact_manifest
    _append_value_difference(
        differences,
        "artifact-count",
        RegressionCategory.ARTIFACT_DRIFT,
        "artifacts.count",
        len(base_manifest.artifacts),
        len(cand_manifest.artifacts),
        RegressionSeverity.WARNING,
        "Artifact count changed",
    )
    _append_value_difference(
        differences,
        "evidence-artifact-count",
        RegressionCategory.ARTIFACT_DRIFT,
        "artifacts.evidence_count",
        base_manifest.evidence_artifact_count,
        cand_manifest.evidence_artifact_count,
        RegressionSeverity.WARNING,
        "Evidence artifact count changed",
    )
    _append_value_difference(
        differences,
        "presentation-artifact-count",
        RegressionCategory.ARTIFACT_DRIFT,
        "artifacts.presentation_count",
        base_manifest.presentation_artifact_count,
        cand_manifest.presentation_artifact_count,
        RegressionSeverity.WARNING,
        "Presentation artifact count changed",
    )
    base_entries = {entry.artifact_id: entry for entry in base_manifest.artifacts}
    cand_entries = {entry.artifact_id: entry for entry in cand_manifest.artifacts}
    for artifact_id in sorted(set(base_entries) & set(cand_entries)):
        base_entry = base_entries[artifact_id]
        cand_entry = cand_entries[artifact_id]
        if base_entry.content_hash != cand_entry.content_hash:
            presentation_only = (
                base_entry.is_presentation and cand_entry.is_presentation
            )
            differences.append(
                _difference(
                    f"artifact-hash-{_safe_id(artifact_id)}",
                    (
                        RegressionCategory.ARTIFACT_DRIFT
                        if presentation_only
                        else RegressionCategory.HASH_DRIFT
                    ),
                    (
                        RegressionSeverity.INFO
                        if presentation_only
                        else RegressionSeverity.BLOCKING
                    ),
                    f"artifacts.{artifact_id}.content_hash",
                    base_entry.content_hash,
                    cand_entry.content_hash,
                    (
                        f"Presentation artifact hash changed: {artifact_id}"
                        if presentation_only
                        else f"Artifact hash changed: {artifact_id}"
                    ),
                    baseline_refs=[base_entry.path],
                    candidate_refs=[cand_entry.path],
                    is_regression=not presentation_only,
                )
            )
        differences.extend(
            _evidence_classification_differences(artifact_id, base_entry, cand_entry)
        )
    return differences


def _evidence_classification_differences(
    artifact_id: str,
    baseline: ArtifactManifestEntry,
    candidate: ArtifactManifestEntry,
) -> list[RunDifference]:
    differences: list[RunDifference] = []
    newly_presentation_evidence = (
        candidate.is_evidence
        and (candidate.is_presentation or _is_markdown_or_latex(candidate.path))
        and not (
            baseline.is_evidence
            and (baseline.is_presentation or _is_markdown_or_latex(baseline.path))
        )
    )
    missing_producing_commit = (
        candidate.is_evidence
        and baseline.producing_commit_hash is not None
        and candidate.producing_commit_hash is None
    )
    if newly_presentation_evidence or missing_producing_commit:
        reason = (
            "Markdown/LaTeX or presentation artifact newly classified as evidence"
            if newly_presentation_evidence
            else "Evidence artifact newly lacks a producing commit"
        )
        differences.append(
            _difference(
                f"evidence-boundary-{_safe_id(artifact_id)}",
                RegressionCategory.EVIDENCE_BOUNDARY_REGRESSION,
                RegressionSeverity.BLOCKING,
                f"artifacts.{artifact_id}.evidence_boundary",
                {
                    "is_evidence": baseline.is_evidence,
                    "is_presentation": baseline.is_presentation,
                    "producing_commit_hash": baseline.producing_commit_hash,
                },
                {
                    "is_evidence": candidate.is_evidence,
                    "is_presentation": candidate.is_presentation,
                    "producing_commit_hash": candidate.producing_commit_hash,
                },
                f"{reason}: {artifact_id}",
                baseline_refs=[baseline.path],
                candidate_refs=[candidate.path],
                is_regression=True,
            )
        )
    return differences


def _research_object_differences(
    baseline: _RunSnapshot,
    candidate: _RunSnapshot,
) -> list[RunDifference]:
    if baseline.research_object is None or candidate.research_object is None:
        return []
    differences: list[RunDifference] = []
    base_nucleus = baseline.research_object.final_nucleus
    cand_nucleus = candidate.research_object.final_nucleus
    _append_value_difference(
        differences,
        "final-nucleus-type",
        RegressionCategory.UNKNOWN,
        "final_nucleus.type",
        base_nucleus.nucleus_type.value,
        cand_nucleus.nucleus_type.value,
        RegressionSeverity.WARNING,
        "Final nucleus type changed",
    )
    _append_value_difference(
        differences,
        "final-nucleus-id",
        RegressionCategory.UNKNOWN,
        "final_nucleus.id",
        base_nucleus.id,
        cand_nucleus.id,
        RegressionSeverity.WARNING,
        "Different final nucleus selected",
    )
    return differences


def _branch_differences(
    baseline: _RunSnapshot,
    candidate: _RunSnapshot,
) -> list[RunDifference]:
    if baseline.branch_outcomes is None or candidate.branch_outcomes is None:
        return []
    differences: list[RunDifference] = []
    base_counts = Counter(outcome.outcome for outcome in baseline.branch_outcomes)
    cand_counts = Counter(outcome.outcome for outcome in candidate.branch_outcomes)
    _append_value_difference(
        differences,
        "branch-outcome-counts",
        RegressionCategory.BRANCH_OUTCOME_CHANGE,
        "branches.outcome_counts",
        dict(sorted(base_counts.items())),
        dict(sorted(cand_counts.items())),
        RegressionSeverity.WARNING,
        "Branch outcome counts changed",
    )
    _append_value_difference(
        differences,
        "stage-c-ready-count",
        RegressionCategory.STAGE_COUNT_CHANGE,
        "branches.stage_c_ready_count",
        base_counts.get("StageCReady", 0),
        cand_counts.get("StageCReady", 0),
        RegressionSeverity.WARNING,
        "Stage C-ready count changed",
    )
    base_labels = Counter(
        outcome.verification_label.value
        for outcome in baseline.branch_outcomes
        if outcome.verification_label is not None
    )
    cand_labels = Counter(
        outcome.verification_label.value
        for outcome in candidate.branch_outcomes
        if outcome.verification_label is not None
    )
    _append_value_difference(
        differences,
        "verification-label-counts",
        RegressionCategory.BRANCH_OUTCOME_CHANGE,
        "branches.verification_label_counts",
        dict(sorted(base_labels.items())),
        dict(sorted(cand_labels.items())),
        RegressionSeverity.WARNING,
        "Verification label counts changed",
    )
    return differences


def _claim_differences(
    baseline: _RunSnapshot,
    candidate: _RunSnapshot,
) -> list[RunDifference]:
    if baseline.claim_table is None or candidate.claim_table is None:
        return []
    differences: list[RunDifference] = []
    base_claims = {claim.claim_id: claim for claim in baseline.claim_table.claims}
    cand_claims = {claim.claim_id: claim for claim in candidate.claim_table.claims}
    _append_value_difference(
        differences,
        "claim-count",
        RegressionCategory.STAGE_COUNT_CHANGE,
        "claims.count",
        len(base_claims),
        len(cand_claims),
        RegressionSeverity.WARNING,
        "Claim count changed",
    )
    base_blocked = sum(not claim.allowed_in_main_text for claim in base_claims.values())
    cand_blocked = sum(not claim.allowed_in_main_text for claim in cand_claims.values())
    if base_blocked != cand_blocked:
        differences.append(
            _difference(
                "blocked-claim-count",
                RegressionCategory.BLOCKED_CLAIM_CHANGE,
                (
                    RegressionSeverity.WARNING
                    if cand_blocked > base_blocked
                    else RegressionSeverity.INFO
                ),
                "claims.blocked_count",
                base_blocked,
                cand_blocked,
                "Blocked claim count increased"
                if cand_blocked > base_blocked
                else "Blocked claim count decreased",
                is_regression=cand_blocked > base_blocked,
            )
        )
    for claim_id in sorted(set(base_claims) & set(cand_claims)):
        base_claim = base_claims[claim_id]
        cand_claim = cand_claims[claim_id]
        if base_claim.claim_label != cand_claim.claim_label:
            inflated = _label_inflated(base_claim.claim_label, cand_claim.claim_label)
            differences.append(
                _difference(
                    f"claim-label-{_safe_id(claim_id)}",
                    RegressionCategory.CLAIM_LABEL_CHANGE,
                    (
                        RegressionSeverity.BLOCKING
                        if inflated
                        else RegressionSeverity.WARNING
                    ),
                    f"claims.{claim_id}.label",
                    base_claim.claim_label.value,
                    cand_claim.claim_label.value,
                    f"Claim label inflated: {claim_id}"
                    if inflated
                    else f"Claim label changed: {claim_id}",
                    is_regression=True,
                )
            )
        if _new_synthetic_real_world_claim(base_claim, cand_claim):
            differences.append(
                _difference(
                    f"synthetic-boundary-{_safe_id(claim_id)}",
                    RegressionCategory.EVIDENCE_BOUNDARY_REGRESSION,
                    RegressionSeverity.BLOCKING,
                    f"claims.{claim_id}.synthetic_boundary",
                    base_claim.claim_text,
                    cand_claim.claim_text,
                    f"Synthetic evidence newly framed as real-world validation: {claim_id}",
                    is_regression=True,
                )
            )
    return differences


def _audit_differences(
    baseline: _RunSnapshot,
    candidate: _RunSnapshot,
) -> list[RunDifference]:
    if baseline.final_audit is None or candidate.final_audit is None:
        return []
    differences: list[RunDifference] = []
    if candidate.final_audit.warnings_count > baseline.final_audit.warnings_count:
        differences.append(
            _difference(
                "final-audit-warning-count",
                RegressionCategory.UNKNOWN,
                RegressionSeverity.WARNING,
                "final_audit.warnings_count",
                baseline.final_audit.warnings_count,
                candidate.final_audit.warnings_count,
                "Final audit has new warnings",
                is_regression=True,
            )
        )
    base_violations = _evidence_violation_ids(baseline.final_audit)
    cand_violations = _evidence_violation_ids(candidate.final_audit)
    new_violations = sorted(cand_violations - base_violations)
    if new_violations:
        differences.append(
            _difference(
                "audit-evidence-boundary",
                RegressionCategory.EVIDENCE_BOUNDARY_REGRESSION,
                RegressionSeverity.BLOCKING,
                "final_audit.evidence_boundary_violations",
                sorted(base_violations),
                sorted(cand_violations),
                f"New evidence-boundary audit violations: {new_violations}",
                is_regression=True,
            )
        )
    return differences


def _release_and_export_differences(
    baseline: _RunSnapshot,
    candidate: _RunSnapshot,
) -> list[RunDifference]:
    differences: list[RunDifference] = []
    if baseline.release_gate is not None and candidate.release_gate is not None:
        _append_value_difference(
            differences,
            "release-status",
            RegressionCategory.RELEASE_STATUS_REGRESSION,
            "release.status",
            baseline.release_gate.status.value,
            candidate.release_gate.status.value,
            RegressionSeverity.WARNING,
            "Release status changed",
        )
    if baseline.export_readiness is not None and candidate.export_readiness is not None:
        for field in [
            "ready_for_polished_prose",
            "ready_for_latex_export",
            "ready_for_external_review",
        ]:
            base_value = getattr(baseline.export_readiness, field)
            cand_value = getattr(candidate.export_readiness, field)
            if base_value == cand_value:
                continue
            blocking = (base_value and not cand_value) or (
                field == "ready_for_external_review" and cand_value
            )
            differences.append(
                _difference(
                    f"export-{field.replace('_', '-')}",
                    RegressionCategory.EXPORT_READINESS_REGRESSION,
                    (
                        RegressionSeverity.BLOCKING
                        if blocking
                        else RegressionSeverity.WARNING
                    ),
                    f"export.{field}",
                    base_value,
                    cand_value,
                    f"Export readiness changed: {field}",
                    is_regression=blocking,
                )
            )
    return differences


def _optional_report_differences(
    baseline: _RunSnapshot,
    candidate: _RunSnapshot,
) -> list[RunDifference]:
    differences: list[RunDifference] = []
    if baseline.replay_report is not None and candidate.replay_report is not None:
        _append_value_difference(
            differences,
            "replay-status",
            RegressionCategory.REPLAY_STATUS_REGRESSION,
            "replay.status",
            baseline.replay_report.replay_status.value,
            candidate.replay_report.replay_status.value,
            RegressionSeverity.WARNING,
            "Replay status changed",
        )
        if candidate.replay_report.warnings_count > baseline.replay_report.warnings_count:
            differences.append(
                _difference(
                    "replay-warning-count",
                    RegressionCategory.REPLAY_STATUS_REGRESSION,
                    RegressionSeverity.WARNING,
                    "replay.warnings_count",
                    baseline.replay_report.warnings_count,
                    candidate.replay_report.warnings_count,
                    "Replay has new warnings",
                    is_regression=True,
                )
            )
    if baseline.diagnostic_report is not None and candidate.diagnostic_report is not None:
        _append_value_difference(
            differences,
            "diagnostic-status",
            RegressionCategory.DIAGNOSTIC_STATUS_REGRESSION,
            "diagnostics.status",
            baseline.diagnostic_report.diagnostic_status.value,
            candidate.diagnostic_report.diagnostic_status.value,
            RegressionSeverity.WARNING,
            "Diagnostic status changed",
        )
    return differences


def _load_model(
    path: Path,
    model: type,
    source_name: str,
    loaded: list[str],
    errors: list[str],
) -> Any | None:
    if not path.is_file():
        return None
    try:
        value = model.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        errors.append(f"{source_name} failed to load: {exc}")
        return None
    loaded.append(source_name)
    return value


def _load_wrapped_model(
    path: Path,
    model: type,
    source_name: str,
    loaded: list[str],
    errors: list[str],
) -> Any | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        value = model.model_validate(payload.get("report", payload))
    except (json.JSONDecodeError, OSError, ValidationError) as exc:
        errors.append(f"{source_name} failed to load: {exc}")
        return None
    loaded.append(source_name)
    return value


def _load_branch_outcomes(
    path: Path,
    loaded: list[str],
    errors: list[str],
) -> list[BranchOutcomeSummary] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        outcomes = [
            BranchOutcomeSummary.model_validate(item)
            for item in payload.get("branch_outcomes", [])
        ]
    except (json.JSONDecodeError, OSError, ValidationError) as exc:
        errors.append(f"branch_outcomes failed to load: {exc}")
        return None
    loaded.append("branch_outcomes")
    return outcomes


def _ledger_count(run_path: Path, run_id: str) -> int | None:
    ledger_path = run_path / LEDGER_FILENAME
    if not ledger_path.is_file():
        return None
    return len(ResearchLedger(ledger_path).list_commits(run_id))


def _manifest_hash(run_path: Path) -> str | None:
    path = run_path / "research_object" / "artifact-manifest.json"
    return sha256_file(path) if path.is_file() else None


def _evidence_violation_ids(report: FinalAuditReport) -> set[str]:
    relevant = {
        AuditCategory.EVIDENCE_BOUNDARY,
        AuditCategory.CLAIM_LABEL_PRESERVATION,
        AuditCategory.SYNTHETIC_DATA_BOUNDARY,
    }
    return {
        check.check_id
        for check in report.checks
        if check.category in relevant and check.status == AuditCheckStatus.FAIL
    }


def _label_inflated(
    baseline: VerificationLabel,
    candidate: VerificationLabel,
) -> bool:
    return _LABEL_STRENGTH[candidate] > _LABEL_STRENGTH[baseline]


def _new_synthetic_real_world_claim(baseline: Claim, candidate: Claim) -> bool:
    if candidate.claim_label != VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
        return False
    markers = ("real-world", "real world", "real markets", "real mobility")
    baseline_has = any(marker in baseline.claim_text.lower() for marker in markers)
    candidate_has = any(marker in candidate.claim_text.lower() for marker in markers)
    return candidate_has and not baseline_has


def _is_markdown_or_latex(path: str) -> bool:
    suffix = path.rsplit(".", maxsplit=1)[-1].lower() if "." in path else ""
    return suffix in {"md", "markdown", "tex", "pdf"}


def _append_value_difference(
    differences: list[RunDifference],
    difference_id: str,
    category: RegressionCategory,
    field: str,
    baseline_value: Any,
    candidate_value: Any,
    severity: RegressionSeverity,
    message: str,
) -> None:
    if baseline_value == candidate_value:
        return
    differences.append(
        _difference(
            difference_id,
            category,
            severity,
            field,
            baseline_value,
            candidate_value,
            message,
            is_regression=severity != RegressionSeverity.INFO,
        )
    )


def _difference(
    difference_id: str,
    category: RegressionCategory,
    severity: RegressionSeverity,
    field: str,
    baseline_value: Any,
    candidate_value: Any,
    message: str,
    *,
    baseline_refs: list[str] | None = None,
    candidate_refs: list[str] | None = None,
    is_regression: bool = False,
    optional_output: bool = False,
) -> RunDifference:
    return RunDifference(
        difference_id=difference_id,
        category=category,
        severity=severity,
        field=field,
        baseline_value=baseline_value,
        candidate_value=candidate_value,
        message=message,
        baseline_refs=baseline_refs or [],
        candidate_refs=candidate_refs or [],
        is_regression=is_regression,
        optional_output=optional_output,
    )


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return safe or "run"
