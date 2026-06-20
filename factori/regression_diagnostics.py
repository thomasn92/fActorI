"""Deterministic regression detection and cross-run comparison summaries."""

from __future__ import annotations

from factori.schemas import (
    CrossRunComparisonReport,
    DiagnosticStatus,
    RegressionCategory,
    RegressionFinding,
    RegressionSeverity,
    RegressionStatus,
    ReleaseGateStatus,
    ReplayStatus,
    RunComparisonSummary,
    RunDifference,
)


def detect_regressions(
    comparison_report: CrossRunComparisonReport,
) -> list[RegressionFinding]:
    """Derive deterministic warning and blocking regressions from run differences."""
    findings: list[RegressionFinding] = []
    for difference in comparison_report.differences:
        severity = _regression_severity(difference)
        if severity is None:
            continue
        findings.append(
            RegressionFinding(
                finding_id=f"regression-{difference.difference_id}",
                category=difference.category,
                severity=severity,
                summary=difference.message,
                difference_ids=[difference.difference_id],
                baseline_value=difference.baseline_value,
                candidate_value=difference.candidate_value,
            )
        )
    return sorted(findings, key=lambda finding: finding.finding_id)


def summarize_cross_run_comparison(
    report: CrossRunComparisonReport,
) -> RunComparisonSummary:
    """Build a compact deterministic cross-run summary."""
    return RunComparisonSummary(
        baseline_run_id=report.baseline_run_id,
        candidate_run_id=report.candidate_run_id,
        differences_count=len(report.differences),
        blocking_regressions=sum(
            finding.severity == RegressionSeverity.BLOCKING
            for finding in report.regression_findings
        ),
        warning_regressions=sum(
            finding.severity == RegressionSeverity.WARNING
            for finding in report.regression_findings
        ),
        info_differences=sum(
            difference.severity == RegressionSeverity.INFO
            for difference in report.differences
        ),
        regression_status=report.regression_status,
        baseline_release_status=report.baseline_release_status,
        candidate_release_status=report.candidate_release_status,
        baseline_replay_status=report.baseline_replay_status,
        candidate_replay_status=report.candidate_replay_status,
        ledger_mutated=report.ledger_mutated,
        artifact_manifest_mutated=report.artifact_manifest_mutated,
    )


def regression_status(
    findings: list[RegressionFinding],
    comparison_errors: list[str] | None = None,
) -> RegressionStatus:
    """Return the deterministic overall regression status."""
    if comparison_errors:
        return RegressionStatus.COMPARISON_FAILED
    if any(finding.severity == RegressionSeverity.BLOCKING for finding in findings):
        return RegressionStatus.REGRESSION_DETECTED
    if any(finding.severity == RegressionSeverity.WARNING for finding in findings):
        return RegressionStatus.REGRESSION_WARNINGS
    return RegressionStatus.NO_REGRESSION


def _regression_severity(difference: RunDifference) -> RegressionSeverity | None:
    category = difference.category
    baseline = _value(difference.baseline_value)
    candidate = _value(difference.candidate_value)

    if category in {
        RegressionCategory.HASH_DRIFT,
        RegressionCategory.EVIDENCE_BOUNDARY_REGRESSION,
    }:
        return RegressionSeverity.BLOCKING
    if category == RegressionCategory.MISSING_OUTPUT:
        if difference.optional_output:
            return None
        if difference.baseline_value is not None and difference.candidate_value is None:
            return RegressionSeverity.BLOCKING
        if difference.severity == RegressionSeverity.BLOCKING:
            return RegressionSeverity.BLOCKING
        return None
    if category == RegressionCategory.CLAIM_LABEL_CHANGE:
        if (
            difference.severity == RegressionSeverity.BLOCKING
            or "inflat" in difference.message.lower()
        ):
            return RegressionSeverity.BLOCKING
        return RegressionSeverity.WARNING
    if category == RegressionCategory.RELEASE_STATUS_REGRESSION:
        if (
            baseline == ReleaseGateStatus.RELEASE_READY.value
            and candidate == ReleaseGateStatus.RELEASE_BLOCKED.value
        ):
            return RegressionSeverity.BLOCKING
        if baseline != candidate:
            return RegressionSeverity.WARNING
    if category == RegressionCategory.EXPORT_READINESS_REGRESSION:
        if difference.baseline_value is True and difference.candidate_value is False:
            return RegressionSeverity.BLOCKING
        if baseline != candidate:
            return RegressionSeverity.WARNING
    if category == RegressionCategory.REPLAY_STATUS_REGRESSION:
        verified = {
            ReplayStatus.REPLAY_VERIFIED.value,
            ReplayStatus.REPLAY_VERIFIED_WITH_WARNINGS.value,
        }
        if baseline in verified and candidate == ReplayStatus.REPLAY_FAILED.value:
            return RegressionSeverity.BLOCKING
        if baseline != candidate and not difference.optional_output:
            return RegressionSeverity.WARNING
    if category == RegressionCategory.DIAGNOSTIC_STATUS_REGRESSION:
        if (
            baseline != DiagnosticStatus.BLOCKED.value
            and candidate == DiagnosticStatus.BLOCKED.value
        ):
            return RegressionSeverity.BLOCKING
        if baseline != candidate and not difference.optional_output:
            return RegressionSeverity.WARNING
    if difference.severity == RegressionSeverity.BLOCKING:
        return RegressionSeverity.BLOCKING
    if difference.severity == RegressionSeverity.WARNING or difference.is_regression:
        return RegressionSeverity.WARNING
    return None


def _value(value) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value)
