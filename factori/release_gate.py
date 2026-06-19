"""Deterministic release gate decisions for final audit reports."""

from __future__ import annotations

from factori.schemas import (
    AuditCheckStatus,
    AuditSeverity,
    FinalAuditReport,
    ReleaseGateDecision,
    ReleaseGateStatus,
)


def decide_release_gate(audit_report: FinalAuditReport) -> ReleaseGateDecision:
    """Decide deterministic release readiness from an audit report."""
    blocking = [
        check.message
        for check in audit_report.checks
        if check.status == AuditCheckStatus.FAIL and check.severity == AuditSeverity.BLOCKING
    ]
    warnings = [
        check.message
        for check in audit_report.checks
        if check.status == AuditCheckStatus.WARNING or check.severity == AuditSeverity.WARNING
    ]
    if blocking:
        status = ReleaseGateStatus.RELEASE_BLOCKED
    elif warnings:
        status = ReleaseGateStatus.RELEASE_READY_WITH_WARNINGS
    else:
        status = ReleaseGateStatus.RELEASE_READY

    ready_for_polished_prose = status != ReleaseGateStatus.RELEASE_BLOCKED
    required_sections_ok = any(
        check.check_id == "paper_required_sections"
        and check.status in {AuditCheckStatus.PASS, AuditCheckStatus.NOT_APPLICABLE}
        for check in audit_report.checks
    )
    return ReleaseGateDecision(
        run_id=audit_report.run_id,
        status=status,
        ready_for_polished_prose=ready_for_polished_prose,
        ready_for_latex_export=ready_for_polished_prose and required_sections_ok,
        ready_for_external_review=False,
        blocking_reasons=blocking,
        warnings=warnings,
        audit_checks=len(audit_report.checks),
    )
