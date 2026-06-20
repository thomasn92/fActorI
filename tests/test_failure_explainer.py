from __future__ import annotations

from factori.failure_explainer import (
    explain_audit_failures,
    explain_export_failures,
    explain_replay_failures,
    recommend_rerun_steps,
)
from factori.release_gate import decide_release_gate
from factori.schemas import (
    AuditCategory,
    AuditCheck,
    AuditCheckStatus,
    AuditSeverity,
    DiagnosticSeverity,
    ExportReadinessReport,
    FinalAuditReport,
    ReleaseGateDecision,
    ReleaseGateStatus,
    ReplayCheck,
    ReplayStatus,
    ReplayVerificationReport,
    RootCauseCategory,
)


def test_missing_final_nucleus_maps_to_missing_stage_output() -> None:
    causes = explain_replay_failures(
        _replay_report([_replay_check("final_nucleus_loaded", "final nucleus missing")])
    )

    assert causes[0].category == RootCauseCategory.MISSING_STAGE_OUTPUT
    assert causes[0].rerun_from_stage == "synthesize-abstract"


def test_missing_claim_table_maps_to_missing_stage_output() -> None:
    causes = explain_replay_failures(
        _replay_report([_replay_check("claim_table_loaded", "claim table missing")])
    )

    assert causes[0].category == RootCauseCategory.MISSING_STAGE_OUTPUT
    assert causes[0].rerun_from_stage == "plan-manuscript"


def test_missing_artifact_maps_to_missing_artifact() -> None:
    check = _replay_check(
        "artifact_hash:claim-table",
        "ledger artifact missing: reports/claim-table.json",
        observed="missing",
    )

    causes = explain_replay_failures(_replay_report([check]))

    assert causes[0].category == RootCauseCategory.MISSING_ARTIFACT


def test_hash_mismatch_maps_to_hash_mismatch() -> None:
    check = _replay_check(
        "manifest_hash:stage-a-report",
        "manifest artifact hash mismatch: stage-a-report",
        observed="bad-hash",
    )

    causes = explain_replay_failures(_replay_report([check]))

    assert causes[0].category == RootCauseCategory.HASH_MISMATCH
    assert causes[0].rerun_from_stage == "run-stage-a"


def test_missing_producing_commit_maps_to_evidence_boundary() -> None:
    causes = explain_replay_failures(
        _replay_report(
            [
                _replay_check(
                    "evidence_artifacts_have_producing_commits",
                    "evidence artifact lacks producing commit",
                )
            ]
        )
    )

    assert causes[0].category == RootCauseCategory.EVIDENCE_BOUNDARY_VIOLATION
    assert causes[0].rerun_from_stage == "package-research-object"


def test_markdown_or_latex_evidence_maps_to_evidence_boundary() -> None:
    causes = explain_replay_failures(
        _replay_report(
            [
                _replay_check(
                    "presentation_artifacts_not_verification_evidence",
                    "Markdown or LaTeX artifact marked as evidence",
                )
            ]
        )
    )

    assert causes[0].category == RootCauseCategory.EVIDENCE_BOUNDARY_VIOLATION


def test_conjecture_upgrade_maps_to_claim_label_inflation() -> None:
    check = _audit_check(
        "conjectures_not_upgraded",
        "conjecture was upgraded to theorem",
    )
    report = _audit_report([check])

    causes = explain_audit_failures(report, decide_release_gate(report))

    assert causes[0].category == RootCauseCategory.CLAIM_LABEL_INFLATION


def test_synthetic_real_world_claim_maps_to_synthetic_boundary() -> None:
    check = _audit_check(
        "synthetic_not_real_world",
        "synthetic evidence used as real-world validation",
    )
    report = _audit_report([check])

    causes = explain_audit_failures(report, decide_release_gate(report))

    assert causes[0].category == RootCauseCategory.SYNTHETIC_BOUNDARY_VIOLATION


def test_blocked_claim_leak_maps_to_blocked_claim_category() -> None:
    check = _audit_check(
        "blocked_claims_not_in_main_results",
        "blocked claim appears in main results",
    )
    report = _audit_report([check])

    causes = explain_audit_failures(report, decide_release_gate(report))

    assert causes[0].category == RootCauseCategory.BLOCKED_CLAIM_LEAK


def test_release_gate_inconsistency_is_detected() -> None:
    report = _audit_report([])
    inconsistent = ReleaseGateDecision(
        run_id="run-1",
        status=ReleaseGateStatus.RELEASE_BLOCKED,
        ready_for_polished_prose=False,
        ready_for_latex_export=False,
        ready_for_external_review=False,
        blocking_reasons=["tampered"],
        audit_checks=0,
    )

    causes = explain_audit_failures(report, inconsistent)

    assert causes[0].category == RootCauseCategory.RELEASE_GATE_INCONSISTENCY


def test_export_readiness_inconsistency_is_detected() -> None:
    readiness = ExportReadinessReport(
        run_id="run-1",
        ready_for_polished_prose=False,
        ready_for_latex_export=False,
        ready_for_external_review=False,
        export_blocked=True,
        export_allowed_claims=0,
        export_blocked_claims=0,
        blocking_reasons=["tampered export readiness"],
    )

    causes = explain_export_failures(readiness, _ready_release())

    assert any(
        cause.category == RootCauseCategory.EXPORT_READINESS_INCONSISTENCY
        for cause in causes
    )


def test_runtime_summary_misuse_is_detected() -> None:
    check = _audit_check(
        "runtime_summary_not_provenance",
        "runtime summary used as provenance",
    )
    report = _audit_report([check])

    causes = explain_audit_failures(report, decide_release_gate(report))

    assert causes[0].category == RootCauseCategory.RUNTIME_SUMMARY_MISUSE


def test_recommended_rerun_steps_are_deterministic_and_not_executed() -> None:
    causes = explain_replay_failures(
        _replay_report([_replay_check("claim_table_loaded", "claim table missing")])
    )

    first = recommend_rerun_steps(causes)
    second = recommend_rerun_steps(causes)

    assert first == second
    assert first[0].stage == "plan-manuscript"
    assert first[-1].stage == "replay-verify"
    assert all(not step.executes_automatically for step in first)


def test_unknown_blocking_failure_requires_manual_inspection() -> None:
    causes = explain_replay_failures(
        _replay_report([_replay_check("unknown_check", "unclassified failure")])
    )

    steps = recommend_rerun_steps(causes)

    assert causes[0].category == RootCauseCategory.UNKNOWN
    assert causes[0].severity == DiagnosticSeverity.BLOCKING
    assert steps[0].manual_inspection_required
    assert steps[0].command is None


def _replay_check(
    check_id: str,
    message: str,
    *,
    observed: str | None = None,
    status: AuditCheckStatus = AuditCheckStatus.FAIL,
    severity: AuditSeverity = AuditSeverity.BLOCKING,
) -> ReplayCheck:
    return ReplayCheck(
        check_id=check_id,
        category=AuditCategory.REPRODUCIBILITY_READINESS,
        status=status,
        severity=severity,
        message=message,
        observed=observed,
    )


def _replay_report(checks: list[ReplayCheck]) -> ReplayVerificationReport:
    return ReplayVerificationReport(
        run_id="run-1",
        checks=checks,
        replay_status=ReplayStatus.REPLAY_FAILED,
        ledger_commits_checked=1,
        artifacts_checked=0,
        hashes_verified=0,
        evidence_artifacts_checked=0,
        presentation_artifacts_checked=0,
        stage_outputs_checked=0,
        warnings_count=sum(check.status == AuditCheckStatus.WARNING for check in checks),
        blocking_failures_count=sum(check.severity == AuditSeverity.BLOCKING for check in checks),
        ledger_mutated=False,
        artifact_manifest_mutated=False,
    )


def _audit_check(
    check_id: str,
    message: str,
    *,
    status: AuditCheckStatus = AuditCheckStatus.FAIL,
    severity: AuditSeverity = AuditSeverity.BLOCKING,
) -> AuditCheck:
    return AuditCheck(
        check_id=check_id,
        category=AuditCategory.REPRODUCIBILITY_READINESS,
        status=status,
        severity=severity,
        message=message,
    )


def _audit_report(checks: list[AuditCheck]) -> FinalAuditReport:
    return FinalAuditReport(
        run_id="run-1",
        checks=checks,
        passes_count=sum(check.status == AuditCheckStatus.PASS for check in checks),
        warnings_count=sum(check.status == AuditCheckStatus.WARNING for check in checks),
        failures_count=sum(check.status == AuditCheckStatus.FAIL for check in checks),
        blocking_failures_count=sum(
            check.status == AuditCheckStatus.FAIL
            and check.severity == AuditSeverity.BLOCKING
            for check in checks
        ),
    )


def _ready_release() -> ReleaseGateDecision:
    return ReleaseGateDecision(
        run_id="run-1",
        status=ReleaseGateStatus.RELEASE_READY,
        ready_for_polished_prose=True,
        ready_for_latex_export=True,
        ready_for_external_review=False,
        audit_checks=0,
    )
