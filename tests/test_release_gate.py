from __future__ import annotations

from factori.release_gate import decide_release_gate
from factori.schemas import (
    AuditCategory,
    AuditCheck,
    AuditCheckStatus,
    AuditSeverity,
    FinalAuditReport,
    ReleaseGateStatus,
)


def test_release_gate_blocks_on_any_blocking_failure() -> None:
    report = _audit_report(
        [
            _check(
                "blocking",
                AuditCheckStatus.FAIL,
                AuditSeverity.BLOCKING,
                "blocking failure",
            )
        ]
    )

    decision = decide_release_gate(report)

    assert decision.status == ReleaseGateStatus.RELEASE_BLOCKED
    assert not decision.ready_for_polished_prose
    assert "blocking failure" in decision.blocking_reasons


def test_release_gate_allows_warnings_without_blocking_failure() -> None:
    report = _audit_report(
        [
            _check("warning", AuditCheckStatus.WARNING, AuditSeverity.WARNING, "warning"),
            _check("paper_required_sections", AuditCheckStatus.PASS, AuditSeverity.INFO, "ok"),
        ]
    )

    decision = decide_release_gate(report)

    assert decision.status == ReleaseGateStatus.RELEASE_READY_WITH_WARNINGS
    assert decision.ready_for_polished_prose
    assert decision.ready_for_latex_export
    assert decision.warnings == ["warning"]


def test_release_gate_is_deterministic() -> None:
    report = _audit_report(
        [
            _check("pass", AuditCheckStatus.PASS, AuditSeverity.INFO, "ok"),
            _check("paper_required_sections", AuditCheckStatus.PASS, AuditSeverity.INFO, "ok"),
        ]
    )

    first = decide_release_gate(report)
    second = decide_release_gate(report)

    assert first == second
    assert first.status == ReleaseGateStatus.RELEASE_READY
    assert not first.ready_for_external_review


def _audit_report(checks: list[AuditCheck]) -> FinalAuditReport:
    return FinalAuditReport(
        run_id="run-1",
        checks=checks,
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


def _check(
    check_id: str,
    status: AuditCheckStatus,
    severity: AuditSeverity,
    message: str,
) -> AuditCheck:
    return AuditCheck(
        check_id=check_id,
        category=AuditCategory.PAPER_SKELETON_CONSISTENCY,
        status=status,
        severity=severity,
        message=message,
    )
