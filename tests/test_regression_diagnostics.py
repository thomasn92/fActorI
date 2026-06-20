from __future__ import annotations

from factori.regression_diagnostics import (
    detect_regressions,
    regression_status,
    summarize_cross_run_comparison,
)
from factori.schemas import (
    CrossRunComparisonReport,
    RegressionCategory,
    RegressionSeverity,
    RegressionStatus,
    ReleaseGateStatus,
    ReplayStatus,
    RunDifference,
)


def test_missing_candidate_output_is_blocking() -> None:
    finding = _finding(
        _difference(
            "missing-paper",
            RegressionCategory.MISSING_OUTPUT,
            True,
            None,
            severity=RegressionSeverity.BLOCKING,
        )
    )

    assert finding.severity == RegressionSeverity.BLOCKING


def test_artifact_hash_drift_is_blocking() -> None:
    finding = _finding(
        _difference(
            "hash-drift",
            RegressionCategory.HASH_DRIFT,
            "a" * 64,
            "b" * 64,
        )
    )

    assert finding.severity == RegressionSeverity.BLOCKING


def test_release_ready_to_blocked_is_blocking() -> None:
    finding = _finding(
        _difference(
            "release-blocked",
            RegressionCategory.RELEASE_STATUS_REGRESSION,
            ReleaseGateStatus.RELEASE_READY.value,
            ReleaseGateStatus.RELEASE_BLOCKED.value,
        )
    )

    assert finding.severity == RegressionSeverity.BLOCKING


def test_release_ready_to_warnings_is_warning() -> None:
    finding = _finding(
        _difference(
            "release-warning",
            RegressionCategory.RELEASE_STATUS_REGRESSION,
            ReleaseGateStatus.RELEASE_READY.value,
            ReleaseGateStatus.RELEASE_READY_WITH_WARNINGS.value,
        )
    )

    assert finding.severity == RegressionSeverity.WARNING


def test_replay_verified_to_failed_is_blocking() -> None:
    finding = _finding(
        _difference(
            "replay-failed",
            RegressionCategory.REPLAY_STATUS_REGRESSION,
            ReplayStatus.REPLAY_VERIFIED.value,
            ReplayStatus.REPLAY_FAILED.value,
        )
    )

    assert finding.severity == RegressionSeverity.BLOCKING


def test_evidence_boundary_regression_is_blocking() -> None:
    finding = _finding(
        _difference(
            "evidence-boundary",
            RegressionCategory.EVIDENCE_BOUNDARY_REGRESSION,
            False,
            True,
        )
    )

    assert finding.severity == RegressionSeverity.BLOCKING


def test_claim_label_inflation_is_blocking() -> None:
    finding = _finding(
        _difference(
            "claim-inflation",
            RegressionCategory.CLAIM_LABEL_CHANGE,
            "Conjecture",
            "LeanVerified",
            severity=RegressionSeverity.BLOCKING,
            message="Claim label inflated",
        )
    )

    assert finding.severity == RegressionSeverity.BLOCKING


def test_new_blocked_claims_are_warning() -> None:
    finding = _finding(
        _difference(
            "blocked-claims",
            RegressionCategory.BLOCKED_CLAIM_CHANGE,
            0,
            1,
            severity=RegressionSeverity.WARNING,
        )
    )

    assert finding.severity == RegressionSeverity.WARNING


def test_final_nucleus_change_is_warning() -> None:
    finding = _finding(
        _difference(
            "nucleus-change",
            RegressionCategory.UNKNOWN,
            "nucleus-a",
            "nucleus-b",
            severity=RegressionSeverity.WARNING,
        )
    )

    assert finding.severity == RegressionSeverity.WARNING


def test_branch_outcome_change_is_warning() -> None:
    finding = _finding(
        _difference(
            "branch-change",
            RegressionCategory.BRANCH_OUTCOME_CHANGE,
            {"StopFailure": 1},
            {"StopFailure": 2},
            severity=RegressionSeverity.WARNING,
        )
    )

    assert finding.severity == RegressionSeverity.WARNING


def test_optional_report_presence_is_information_only() -> None:
    difference = _difference(
        "optional-replay",
        RegressionCategory.MISSING_OUTPUT,
        False,
        True,
        severity=RegressionSeverity.INFO,
        optional_output=True,
    )

    assert detect_regressions(_report([difference])) == []


def test_comparison_summary_is_deterministic() -> None:
    difference = _difference(
        "branch-change",
        RegressionCategory.BRANCH_OUTCOME_CHANGE,
        1,
        2,
        severity=RegressionSeverity.WARNING,
    )
    report = _with_findings(_report([difference]))

    first = summarize_cross_run_comparison(report)
    second = summarize_cross_run_comparison(report)

    assert first == second
    assert first.warning_regressions == 1


def test_status_rules_are_deterministic() -> None:
    blocking = _finding(
        _difference(
            "hash",
            RegressionCategory.HASH_DRIFT,
            "a",
            "b",
        )
    )
    warning = _finding(
        _difference(
            "count",
            RegressionCategory.ARTIFACT_DRIFT,
            1,
            2,
            severity=RegressionSeverity.WARNING,
        )
    )

    assert regression_status([blocking]) == RegressionStatus.REGRESSION_DETECTED
    assert regression_status([warning]) == RegressionStatus.REGRESSION_WARNINGS
    assert regression_status([]) == RegressionStatus.NO_REGRESSION
    assert regression_status([], ["load error"]) == RegressionStatus.COMPARISON_FAILED


def _finding(difference: RunDifference):
    findings = detect_regressions(_report([difference]))
    assert len(findings) == 1
    return findings[0]


def _difference(
    difference_id: str,
    category: RegressionCategory,
    baseline,
    candidate,
    *,
    severity: RegressionSeverity = RegressionSeverity.INFO,
    message: str = "deterministic difference",
    optional_output: bool = False,
) -> RunDifference:
    return RunDifference(
        difference_id=difference_id,
        category=category,
        severity=severity,
        field=difference_id,
        baseline_value=baseline,
        candidate_value=candidate,
        message=message,
        is_regression=severity != RegressionSeverity.INFO,
        optional_output=optional_output,
    )


def _report(differences: list[RunDifference]) -> CrossRunComparisonReport:
    return CrossRunComparisonReport(
        baseline_run_id="baseline",
        candidate_run_id="candidate",
        differences=differences,
        regression_status=RegressionStatus.NO_REGRESSION,
        sources_loaded={"baseline": [], "candidate": []},
    )


def _with_findings(report: CrossRunComparisonReport) -> CrossRunComparisonReport:
    findings = detect_regressions(report)
    return report.model_copy(
        update={
            "regression_findings": findings,
            "regression_status": regression_status(findings),
        }
    )
