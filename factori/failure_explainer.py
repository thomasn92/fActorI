"""Deterministic root-cause explanations and safe rerun recommendations."""

from __future__ import annotations

import re

from factori.release_gate import decide_release_gate
from factori.schemas import (
    AuditCheck,
    AuditCheckStatus,
    AuditSeverity,
    DiagnosticSeverity,
    ExportReadinessReport,
    FinalAuditReport,
    RecommendedRerunStep,
    ReleaseGateDecision,
    ReplayCheck,
    ReplayVerificationReport,
    RootCauseCategory,
    RootCauseHypothesis,
)

_PIPELINE_COMMANDS = [
    (
        "run-stage-a",
        'uv run factori run-stage-a --run-id <run_id> --domain "<domain>"',
    ),
    ("run-stage-b", "uv run factori run-stage-b --run-id <run_id>"),
    ("select-stage-c", "uv run factori select-stage-c --run-id <run_id>"),
    ("run-stage-c", "uv run factori run-stage-c --run-id <run_id>"),
    ("synthesize-abstract", "uv run factori synthesize-abstract --run-id <run_id>"),
    ("plan-manuscript", "uv run factori plan-manuscript --run-id <run_id>"),
    (
        "build-draft-skeleton",
        "uv run factori build-draft-skeleton --run-id <run_id>",
    ),
    (
        "package-research-object",
        "uv run factori package-research-object --run-id <run_id>",
    ),
    (
        "assemble-paper-skeleton",
        "uv run factori assemble-paper-skeleton --run-id <run_id>",
    ),
    ("final-audit", "uv run factori final-audit --run-id <run_id>"),
    ("prepare-export", "uv run factori prepare-export --run-id <run_id>"),
    ("replay-verify", "uv run factori replay-verify --run-id <run_id>"),
]

_MISSING_OUTPUT_STAGES = {
    "final_nucleus": "synthesize-abstract",
    "claim_table": "plan-manuscript",
    "manuscript_plan": "plan-manuscript",
    "draft_skeleton": "build-draft-skeleton",
    "artifact_manifest": "package-research-object",
    "ledger_summary": "package-research-object",
    "branch_outcomes": "package-research-object",
    "reproducibility_manifest": "package-research-object",
    "research_object": "package-research-object",
    "paper_skeleton": "assemble-paper-skeleton",
    "final_audit": "final-audit",
    "release_gate": "final-audit",
    "export_readiness": "prepare-export",
}

_AUDIT_MISSING_OUTPUT_CHECKS = {
    "required_stage_reports_exist": "package-research-object",
    "research_object_artifacts_exist": "package-research-object",
    "final_nucleus_exists": "synthesize-abstract",
    "claim_table_exists": "plan-manuscript",
    "manuscript_plan_exists": "plan-manuscript",
    "draft_skeleton_exists": "build-draft-skeleton",
    "research_object_exists": "package-research-object",
    "paper_skeleton_exists": "assemble-paper-skeleton",
    "branch_outcome_summary_exists": "package-research-object",
    "failed_deferred_pruned_branches_represented": "package-research-object",
    "reproducibility_manifest_exists": "package-research-object",
    "paper_required_sections": "assemble-paper-skeleton",
}

_EVIDENCE_CHECKS = {
    "evidence_artifacts_have_producing_commits",
    "presentation_artifacts_not_evidence",
    "presentation_artifacts_not_verification_evidence",
    "markdown_latex_not_verification_evidence",
    "claim_evidence_not_presentation",
    "main_claims_have_evidence_links",
}

_LABEL_CHECKS = {
    "claim_labels_preserved",
    "conjectures_not_upgraded",
    "unsupported_claims_not_main",
    "negative_results_remain_negative",
    "limitations_remain_limitations",
}

_SYNTHETIC_CHECKS = {
    "synthetic_not_real_world",
    "no_real_data_verified_mvp",
    "no_real_data_experiment_verified_in_mvp",
}

_BLOCKED_CLAIM_CHECKS = {
    "blocked_claims_not_in_main_results",
    "blocked_claims_in_appendix",
    "blocked_claims_represented",
}


def explain_replay_failures(
    replay_report: ReplayVerificationReport,
) -> list[RootCauseHypothesis]:
    """Explain failed and warning replay checks deterministically."""
    causes = [
        _cause_from_replay_check(check)
        for check in replay_report.checks
        if check.status in {AuditCheckStatus.WARNING, AuditCheckStatus.FAIL}
    ]
    return _deduplicate_causes(causes)


def explain_audit_failures(
    final_audit_report: FinalAuditReport,
    release_gate_decision: ReleaseGateDecision,
) -> list[RootCauseHypothesis]:
    """Explain audit findings and release-gate inconsistencies."""
    causes = [
        _cause_from_audit_check(check)
        for check in final_audit_report.checks
        if check.status in {AuditCheckStatus.WARNING, AuditCheckStatus.FAIL}
    ]
    expected = decide_release_gate(final_audit_report)
    if (
        expected.status != release_gate_decision.status
        or expected.ready_for_polished_prose
        != release_gate_decision.ready_for_polished_prose
        or expected.ready_for_latex_export != release_gate_decision.ready_for_latex_export
        or expected.ready_for_external_review
        != release_gate_decision.ready_for_external_review
    ):
        causes.append(
            _root_cause(
                category=RootCauseCategory.RELEASE_GATE_INCONSISTENCY,
                severity=DiagnosticSeverity.BLOCKING,
                source="release_gate",
                check_id="release_gate_consistency",
                summary="Release gate does not match the final audit",
                explanation=(
                    f"Expected {expected.status.value}, observed "
                    f"{release_gate_decision.status.value}."
                ),
                rerun_from_stage="final-audit",
            )
        )
    return _deduplicate_causes(causes)


def explain_export_failures(
    export_readiness_report: ExportReadinessReport,
    release_gate_decision: ReleaseGateDecision | None,
) -> list[RootCauseHypothesis]:
    """Explain export blockers and release/export inconsistencies."""
    causes: list[RootCauseHypothesis] = []
    if release_gate_decision is not None and (
        export_readiness_report.ready_for_polished_prose
        != release_gate_decision.ready_for_polished_prose
        or export_readiness_report.ready_for_latex_export
        != release_gate_decision.ready_for_latex_export
        or bool(export_readiness_report.blocking_reasons)
        != bool(release_gate_decision.blocking_reasons)
    ):
        causes.append(
            _root_cause(
                category=RootCauseCategory.EXPORT_READINESS_INCONSISTENCY,
                severity=DiagnosticSeverity.BLOCKING,
                source="export_readiness",
                check_id="export_readiness_consistency",
                summary="Export readiness does not match the release gate",
                explanation=(
                    "Stored export readiness flags or blockers disagree with release state."
                ),
                rerun_from_stage="prepare-export",
            )
        )
    for index, reason in enumerate(export_readiness_report.blocking_reasons):
        category, stage = _classify_message(reason)
        causes.append(
            _root_cause(
                category=category,
                severity=DiagnosticSeverity.BLOCKING,
                source="export_readiness",
                check_id=f"export_blocker_{index}",
                summary=reason,
                explanation="The export-readiness report records this deterministic blocker.",
                rerun_from_stage=stage or "prepare-export",
            )
        )
    for index, warning in enumerate(export_readiness_report.warnings):
        category, stage = _classify_message(warning)
        causes.append(
            _root_cause(
                category=category,
                severity=DiagnosticSeverity.WARNING,
                source="export_readiness",
                check_id=f"export_warning_{index}",
                summary=warning,
                explanation="The export-readiness report records this warning.",
                rerun_from_stage=stage,
            )
        )
    return _deduplicate_causes(causes)


def recommend_rerun_steps(
    root_causes: list[RootCauseHypothesis],
) -> list[RecommendedRerunStep]:
    """Return deterministic safe rerun suggestions without executing them."""
    stage_positions = {stage: index for index, (stage, _) in enumerate(_PIPELINE_COMMANDS)}
    known_positions = [
        stage_positions[cause.rerun_from_stage]
        for cause in root_causes
        if cause.rerun_from_stage in stage_positions
    ]
    steps: list[RecommendedRerunStep] = []
    manual_causes = [cause for cause in root_causes if cause.manual_inspection_required]
    if manual_causes:
        steps.append(
            RecommendedRerunStep(
                step_id="manual-inspection",
                stage="manual-inspection",
                command=None,
                reason="Inspect immutable provenance before rerunning: "
                + ", ".join(cause.root_cause_id for cause in manual_causes),
                order=0,
                safe_to_run=False,
                manual_inspection_required=True,
            )
        )
    if not known_positions:
        return steps
    first_position = min(known_positions)
    cause_ids = ", ".join(cause.root_cause_id for cause in root_causes)
    for offset, (stage, command) in enumerate(
        _PIPELINE_COMMANDS[first_position:],
        start=len(steps),
    ):
        steps.append(
            RecommendedRerunStep(
                step_id=f"rerun-{stage}",
                stage=stage,
                command=command,
                reason=f"Rebuild deterministic outputs affected by: {cause_ids}",
                order=offset,
                downstream=offset > len(manual_causes),
            )
        )
    return steps


def _cause_from_replay_check(check: ReplayCheck) -> RootCauseHypothesis:
    category, stage, manual = _classify_replay_check(check)
    return _root_cause(
        category=category,
        severity=_diagnostic_severity(check.severity),
        source="replay",
        check_id=check.check_id,
        summary=check.message,
        explanation=_explanation(category, check.message),
        rerun_from_stage=stage,
        manual_inspection_required=manual,
        affected_output=_affected_output(check.check_id),
        artifact_refs=[artifact.path for artifact in check.artifact_refs],
    )


def _cause_from_audit_check(check: AuditCheck) -> RootCauseHypothesis:
    category, stage, manual = _classify_audit_check(check)
    return _root_cause(
        category=category,
        severity=_diagnostic_severity(check.severity),
        source="final_audit",
        check_id=check.check_id,
        summary=check.message,
        explanation=_explanation(category, check.message),
        rerun_from_stage=stage,
        manual_inspection_required=manual,
        affected_output=_affected_output(check.check_id),
        artifact_refs=[artifact.path for artifact in check.artifact_refs],
    )


def _classify_replay_check(
    check: ReplayCheck,
) -> tuple[RootCauseCategory, str | None, bool]:
    check_id = check.check_id
    if check_id.endswith("_loaded"):
        output = check_id.removesuffix("_loaded")
        return (
            RootCauseCategory.MISSING_STAGE_OUTPUT,
            _MISSING_OUTPUT_STAGES.get(output),
            False,
        )
    if check_id.startswith(("manifest_hash:", "artifact_hash:")):
        if check.observed == "missing" or "missing" in check.message.lower():
            return RootCauseCategory.MISSING_ARTIFACT, _infer_stage(check), False
        return RootCauseCategory.HASH_MISMATCH, _infer_stage(check), _infer_stage(check) is None
    if check_id == "ledger_hash_chain_valid":
        return RootCauseCategory.LEDGER_CONTINUITY_ISSUE, None, True
    if check_id in _EVIDENCE_CHECKS:
        return RootCauseCategory.EVIDENCE_BOUNDARY_VIOLATION, _evidence_stage(check), False
    if check_id in _LABEL_CHECKS:
        return RootCauseCategory.CLAIM_LABEL_INFLATION, "plan-manuscript", False
    if check_id in _SYNTHETIC_CHECKS:
        return RootCauseCategory.SYNTHETIC_BOUNDARY_VIOLATION, "plan-manuscript", False
    if check_id in _BLOCKED_CLAIM_CHECKS:
        return RootCauseCategory.BLOCKED_CLAIM_LEAK, "build-draft-skeleton", False
    if check_id in {
        "release_gate_consistent_with_final_audit",
        "final_audit_consistent_with_replay",
    }:
        return RootCauseCategory.RELEASE_GATE_INCONSISTENCY, "final-audit", False
    if check_id == "export_readiness_consistent_with_release_gate":
        return RootCauseCategory.EXPORT_READINESS_INCONSISTENCY, "prepare-export", False
    if check_id == "runtime_summary_not_provenance":
        return RootCauseCategory.RUNTIME_SUMMARY_MISUSE, "package-research-object", False
    if check_id.startswith("replay_did_not_mutate"):
        return RootCauseCategory.REPLAY_REPORT_ONLY, None, True
    category, stage = _classify_message(check.message)
    return category, stage, category == RootCauseCategory.UNKNOWN


def _classify_audit_check(
    check: AuditCheck,
) -> tuple[RootCauseCategory, str | None, bool]:
    check_id = check.check_id
    if check_id in _AUDIT_MISSING_OUTPUT_CHECKS:
        return (
            RootCauseCategory.MISSING_STAGE_OUTPUT,
            _AUDIT_MISSING_OUTPUT_CHECKS[check_id],
            False,
        )
    if check_id == "listed_artifacts_have_hashes":
        return RootCauseCategory.MISSING_ARTIFACT, "package-research-object", False
    if check_id == "ledger_hash_chain_valid" or check_id == "ledger_exists":
        return RootCauseCategory.LEDGER_CONTINUITY_ISSUE, None, True
    if check_id in _EVIDENCE_CHECKS:
        return RootCauseCategory.EVIDENCE_BOUNDARY_VIOLATION, _evidence_stage(check), False
    if check_id in _LABEL_CHECKS:
        return RootCauseCategory.CLAIM_LABEL_INFLATION, "plan-manuscript", False
    if check_id in _SYNTHETIC_CHECKS:
        return RootCauseCategory.SYNTHETIC_BOUNDARY_VIOLATION, "plan-manuscript", False
    if check_id in _BLOCKED_CLAIM_CHECKS:
        return RootCauseCategory.BLOCKED_CLAIM_LEAK, "build-draft-skeleton", False
    if check_id == "runtime_summary_not_provenance":
        return RootCauseCategory.RUNTIME_SUMMARY_MISUSE, "package-research-object", False
    category, stage = _classify_message(check.message)
    return category, stage, category == RootCauseCategory.UNKNOWN


def _classify_message(message: str) -> tuple[RootCauseCategory, str | None]:
    lowered = message.lower()
    if "hash mismatch" in lowered:
        return RootCauseCategory.HASH_MISMATCH, None
    if "artifact" in lowered and ("missing" in lowered or "not found" in lowered):
        return RootCauseCategory.MISSING_ARTIFACT, None
    if "markdown" in lowered or "latex" in lowered or "producing commit" in lowered:
        return RootCauseCategory.EVIDENCE_BOUNDARY_VIOLATION, "plan-manuscript"
    if "conjecture" in lowered or "label" in lowered or "positive evidence" in lowered:
        return RootCauseCategory.CLAIM_LABEL_INFLATION, "plan-manuscript"
    if "synthetic" in lowered and ("real-world" in lowered or "real world" in lowered):
        return RootCauseCategory.SYNTHETIC_BOUNDARY_VIOLATION, "plan-manuscript"
    if "blocked claim" in lowered:
        return RootCauseCategory.BLOCKED_CLAIM_LEAK, "build-draft-skeleton"
    if "release gate" in lowered:
        return RootCauseCategory.RELEASE_GATE_INCONSISTENCY, "final-audit"
    if "export readiness" in lowered:
        return RootCauseCategory.EXPORT_READINESS_INCONSISTENCY, "prepare-export"
    if "runtime summar" in lowered:
        return RootCauseCategory.RUNTIME_SUMMARY_MISUSE, "package-research-object"
    return RootCauseCategory.UNKNOWN, None


def _evidence_stage(check: ReplayCheck | AuditCheck) -> str:
    lowered = check.message.lower()
    if "producing commit" in lowered:
        return "package-research-object"
    return "plan-manuscript"


def _infer_stage(check: ReplayCheck) -> str | None:
    values = [check.check_id, check.message]
    values.extend(artifact.id for artifact in check.artifact_refs)
    values.extend(artifact.path for artifact in check.artifact_refs)
    text = " ".join(values).lower()
    stage_markers = [
        (("stage-a", "stage_a"), "run-stage-a"),
        (("stage-b", "stage_b"), "run-stage-b"),
        (("stage-c-selection", "stage_c_selection"), "select-stage-c"),
        (("stage-c", "fake-proof", "fake-synthetic"), "run-stage-c"),
        (("abstract", "final-nucleus"), "synthesize-abstract"),
        (("claim-table", "manuscript-plan"), "plan-manuscript"),
        (("draft-skeleton", "manuscript-checklist"), "build-draft-skeleton"),
        (("artifact-manifest", "ledger-summary", "branch-outcomes", "research-object"),
         "package-research-object"),
        (("paper-skeleton", "paper-assembly"), "assemble-paper-skeleton"),
        (("final-audit", "release-gate"), "final-audit"),
        (("export-", "prose-generation", "latex-export"), "prepare-export"),
    ]
    for markers, stage in stage_markers:
        if any(marker in text for marker in markers):
            return stage
    return None


def _affected_output(check_id: str) -> str | None:
    if check_id.endswith("_loaded"):
        return check_id.removesuffix("_loaded")
    if check_id in _AUDIT_MISSING_OUTPUT_CHECKS:
        return check_id.removesuffix("_exists")
    return None


def _root_cause(
    *,
    category: RootCauseCategory,
    severity: DiagnosticSeverity,
    source: str,
    check_id: str,
    summary: str,
    explanation: str,
    rerun_from_stage: str | None,
    manual_inspection_required: bool = False,
    affected_output: str | None = None,
    artifact_refs: list[str] | None = None,
) -> RootCauseHypothesis:
    return RootCauseHypothesis(
        root_cause_id=f"{source}-{_slug(check_id)}-{_slug(category.value)}",
        category=category,
        severity=severity,
        summary=summary,
        explanation=explanation,
        source=source,
        source_check_ids=[check_id],
        artifact_refs=sorted(set(artifact_refs or [])),
        affected_output=affected_output,
        rerun_from_stage=rerun_from_stage,
        manual_inspection_required=manual_inspection_required,
    )


def _explanation(category: RootCauseCategory, message: str) -> str:
    prefixes = {
        RootCauseCategory.MISSING_STAGE_OUTPUT: "A required deterministic stage output is absent.",
        RootCauseCategory.MISSING_ARTIFACT: (
            "A referenced artifact is absent or lacks required data."
        ),
        RootCauseCategory.HASH_MISMATCH: "Stored and recomputed artifact hashes differ.",
        RootCauseCategory.LEDGER_CONTINUITY_ISSUE: (
            "The immutable ledger cannot be verified safely."
        ),
        RootCauseCategory.EVIDENCE_BOUNDARY_VIOLATION: "Evidence linkage violates MVP boundaries.",
        RootCauseCategory.CLAIM_LABEL_INFLATION: "A claim no longer preserves its source label.",
        RootCauseCategory.SYNTHETIC_BOUNDARY_VIOLATION: (
            "Synthetic evidence crosses its allowed boundary."
        ),
        RootCauseCategory.BLOCKED_CLAIM_LEAK: (
            "A blocked claim appears outside its allowed location."
        ),
        RootCauseCategory.RELEASE_GATE_INCONSISTENCY: "Release state disagrees with audit state.",
        RootCauseCategory.EXPORT_READINESS_INCONSISTENCY: (
            "Export state disagrees with release state."
        ),
        RootCauseCategory.REPLAY_REPORT_ONLY: "Replay behavior violated its read-only boundary.",
        RootCauseCategory.RUNTIME_SUMMARY_MISUSE: (
            "A runtime summary is being treated as provenance."
        ),
        RootCauseCategory.UNKNOWN: "No specific deterministic mapping matched this finding.",
    }
    return f"{prefixes[category]} Source finding: {message}"


def _diagnostic_severity(severity: AuditSeverity) -> DiagnosticSeverity:
    return DiagnosticSeverity(severity.value)


def _deduplicate_causes(
    causes: list[RootCauseHypothesis],
) -> list[RootCauseHypothesis]:
    unique = {cause.root_cause_id: cause for cause in causes}
    return [unique[key] for key in sorted(unique)]


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "finding"
