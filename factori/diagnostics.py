"""Read-only deterministic provenance diagnostics for completed runs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factori.config import DEFAULT_ROOT, LEDGER_FILENAME
from factori.failure_explainer import (
    explain_audit_failures,
    explain_export_failures,
    explain_replay_failures,
    recommend_rerun_steps,
)
from factori.hashing import canonical_json, sha256_file
from factori.ledger import ResearchLedger
from factori.reports import render_diagnostic_report_markdown
from factori.schemas import (
    ArtifactManifest,
    DiagnosticFindingGroup,
    DiagnosticReport,
    DiagnosticSeverity,
    DiagnosticStatus,
    ExportReadinessReport,
    FinalAuditReport,
    LedgerSummary,
    ReleaseGateDecision,
    ReplayVerificationReport,
    ResearchObject,
    RootCauseCategory,
    RootCauseHypothesis,
)


class DiagnosticError(RuntimeError):
    """Raised when no diagnostic source report is available."""


@dataclass(frozen=True)
class _LoadedSources:
    replay_report: ReplayVerificationReport | None
    final_audit_report: FinalAuditReport | None
    release_gate_decision: ReleaseGateDecision | None
    export_readiness_report: ExportReadinessReport | None
    artifact_manifest: ArtifactManifest | None
    ledger_summary: LedgerSummary | None
    research_object: ResearchObject | None
    sources_loaded: list[str]
    load_errors: list[str]


def build_diagnostic_report(
    run_id: str,
    root: str | Path = DEFAULT_ROOT,
) -> DiagnosticReport:
    """Build a read-only deterministic explanation report from available disk outputs."""
    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    replay_path = run_path / "replay" / "replay-verification-report.json"
    audit_path = run_path / "reports" / "final-audit-report.json"
    if not replay_path.is_file() and not audit_path.is_file():
        raise DiagnosticError(
            "No final audit or replay outputs found; run factori final-audit or "
            "factori replay-verify --write-report first"
        )

    ledger_path = run_path / LEDGER_FILENAME
    ledger = ResearchLedger(ledger_path) if ledger_path.is_file() else None
    ledger_count_before = len(ledger.list_commits(run_id)) if ledger is not None else None
    artifact_manifest_path = run_path / "research_object" / "artifact-manifest.json"
    manifest_hash_before = _file_hash(artifact_manifest_path)

    sources = _load_sources(run_path)
    root_causes: list[RootCauseHypothesis] = []
    if sources.replay_report is not None:
        root_causes.extend(explain_replay_failures(sources.replay_report))
    if sources.final_audit_report is not None:
        if sources.release_gate_decision is not None:
            root_causes.extend(
                explain_audit_failures(
                    sources.final_audit_report,
                    sources.release_gate_decision,
                )
            )
        else:
            root_causes.append(
                _missing_source_cause(
                    "release_gate",
                    "Release gate decision is missing",
                    "final-audit",
                )
            )
    if sources.export_readiness_report is not None:
        root_causes.extend(
            explain_export_failures(
                sources.export_readiness_report,
                sources.release_gate_decision,
            )
        )

    root_causes.extend(_load_error_causes(sources.load_errors))
    root_causes = _deduplicate_causes(root_causes)
    recommended_steps = recommend_rerun_steps(root_causes)

    ledger_count_after = len(ledger.list_commits(run_id)) if ledger is not None else None
    manifest_hash_after = _file_hash(artifact_manifest_path)
    ledger_mutated = ledger_count_before != ledger_count_after
    artifact_manifest_mutated = manifest_hash_before != manifest_hash_after
    if ledger_mutated:
        root_causes.append(
            _boundary_cause(
                "diagnostics_ledger_mutation",
                "Diagnostics changed the ledger commit count",
            )
        )
    if artifact_manifest_mutated:
        root_causes.append(
            _boundary_cause(
                "diagnostics_manifest_mutation",
                "Diagnostics changed the artifact manifest",
            )
        )
    root_causes = _deduplicate_causes(root_causes)
    recommended_steps = recommend_rerun_steps(root_causes)
    finding_groups = _group_causes(root_causes)
    blocking_count = sum(
        cause.severity == DiagnosticSeverity.BLOCKING for cause in root_causes
    )
    warning_count = sum(
        cause.severity == DiagnosticSeverity.WARNING for cause in root_causes
    )
    status = _diagnostic_status(root_causes, recommended_steps)
    return DiagnosticReport(
        run_id=run_id,
        diagnostic_status=status,
        root_causes=root_causes,
        finding_groups=finding_groups,
        recommended_steps=recommended_steps,
        sources_loaded=sorted(sources.sources_loaded),
        warnings=sorted(sources.load_errors),
        blocking_causes_count=blocking_count,
        warning_causes_count=warning_count,
        ledger_mutated=ledger_mutated,
        artifact_manifest_mutated=artifact_manifest_mutated,
    )


def write_diagnostic_report(
    *,
    run_id: str,
    report: DiagnosticReport,
    root: str | Path = DEFAULT_ROOT,
) -> tuple[Path, Path]:
    """Write optional diagnostics outside the artifact store and immutable ledger."""
    diagnostic_path = Path(root) / "runs" / run_id / "diagnostics"
    diagnostic_path.mkdir(parents=True, exist_ok=True)
    payload = {
        "not_provenance": True,
        "not_evidence": True,
        "not_ledgered": True,
        "report": report.model_dump(mode="json"),
    }
    json_path = diagnostic_path / "diagnostic-report.json"
    markdown_path = diagnostic_path / "diagnostic-report.md"
    json_path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    markdown = "\n".join(
        [
            "---",
            "not_provenance: true",
            "not_evidence: true",
            "not_ledgered: true",
            "---",
            "",
            render_diagnostic_report_markdown(diagnostic_report=report),
        ]
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def _load_sources(run_path: Path) -> _LoadedSources:
    loaded: list[str] = []
    errors: list[str] = []
    replay = _load_wrapped_model(
        run_path / "replay" / "replay-verification-report.json",
        ReplayVerificationReport,
        "replay_report",
        loaded,
        errors,
    )
    audit = _load_model(
        run_path / "reports" / "final-audit-report.json",
        FinalAuditReport,
        "final_audit_report",
        loaded,
        errors,
    )
    release = _load_model(
        run_path / "reports" / "release-gate-decision.json",
        ReleaseGateDecision,
        "release_gate_decision",
        loaded,
        errors,
    )
    export = _load_model(
        run_path / "reports" / "export-readiness-report.json",
        ExportReadinessReport,
        "export_readiness_report",
        loaded,
        errors,
    )
    manifest = _load_model(
        run_path / "research_object" / "artifact-manifest.json",
        ArtifactManifest,
        "artifact_manifest",
        loaded,
        errors,
    )
    ledger_summary = _load_model(
        run_path / "research_object" / "ledger-summary.json",
        LedgerSummary,
        "ledger_summary",
        loaded,
        errors,
    )
    research_object = _load_model(
        run_path / "research_object" / "research-object.json",
        ResearchObject,
        "research_object",
        loaded,
        errors,
    )
    return _LoadedSources(
        replay_report=replay,
        final_audit_report=audit,
        release_gate_decision=release,
        export_readiness_report=export,
        artifact_manifest=manifest,
        ledger_summary=ledger_summary,
        research_object=research_object,
        sources_loaded=loaded,
        load_errors=errors,
    )


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


def _load_error_causes(errors: list[str]) -> list[RootCauseHypothesis]:
    return [
        RootCauseHypothesis(
            root_cause_id=f"diagnostics-load-error-{index}",
            category=RootCauseCategory.MISSING_ARTIFACT,
            severity=DiagnosticSeverity.BLOCKING,
            summary=error,
            explanation="A diagnostic source exists but cannot be parsed deterministically.",
            source="diagnostics",
            source_check_ids=[f"source_load_{index}"],
            manual_inspection_required=True,
        )
        for index, error in enumerate(sorted(errors))
    ]


def _missing_source_cause(
    output: str,
    summary: str,
    stage: str,
) -> RootCauseHypothesis:
    return RootCauseHypothesis(
        root_cause_id=f"diagnostics-missing-{output}",
        category=RootCauseCategory.MISSING_STAGE_OUTPUT,
        severity=DiagnosticSeverity.BLOCKING,
        summary=summary,
        explanation="A required deterministic report is absent from disk.",
        source="diagnostics",
        source_check_ids=[f"{output}_loaded"],
        affected_output=output,
        rerun_from_stage=stage,
    )


def _boundary_cause(check_id: str, summary: str) -> RootCauseHypothesis:
    return RootCauseHypothesis(
        root_cause_id=f"diagnostics-{check_id}",
        category=RootCauseCategory.REPLAY_REPORT_ONLY,
        severity=DiagnosticSeverity.BLOCKING,
        summary=summary,
        explanation="The read-only diagnostics boundary was violated.",
        source="diagnostics",
        source_check_ids=[check_id],
        manual_inspection_required=True,
    )


def _group_causes(
    root_causes: list[RootCauseHypothesis],
) -> list[DiagnosticFindingGroup]:
    by_category: dict[RootCauseCategory, list[RootCauseHypothesis]] = {}
    for cause in root_causes:
        by_category.setdefault(cause.category, []).append(cause)
    groups: list[DiagnosticFindingGroup] = []
    severity_order = {
        DiagnosticSeverity.INFO: 0,
        DiagnosticSeverity.WARNING: 1,
        DiagnosticSeverity.BLOCKING: 2,
    }
    for category in sorted(by_category, key=lambda item: item.value):
        causes = sorted(by_category[category], key=lambda item: item.root_cause_id)
        severity = max(causes, key=lambda item: severity_order[item.severity]).severity
        groups.append(
            DiagnosticFindingGroup(
                category=category,
                severity=severity,
                root_cause_ids=[cause.root_cause_id for cause in causes],
                summary=f"{len(causes)} finding(s) classified as {category.value}",
            )
        )
    return groups


def _diagnostic_status(
    root_causes: list[RootCauseHypothesis],
    recommended_steps,
) -> DiagnosticStatus:
    if any(cause.severity == DiagnosticSeverity.BLOCKING for cause in root_causes):
        return DiagnosticStatus.BLOCKED
    if not root_causes:
        return DiagnosticStatus.NO_ISSUES
    if recommended_steps:
        return DiagnosticStatus.ACTION_RECOMMENDED
    return DiagnosticStatus.WARNINGS_ONLY


def _deduplicate_causes(
    root_causes: list[RootCauseHypothesis],
) -> list[RootCauseHypothesis]:
    unique = {cause.root_cause_id: cause for cause in root_causes}
    return [unique[key] for key in sorted(unique)]


def _file_hash(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None
