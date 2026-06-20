"""Explicit deterministic mappings from hygiene findings to safe recommendations."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from factori.schemas import (
    OutputHygieneCategory,
    OutputHygieneFinding,
    OutputHygieneSeverity,
    RemediationAction,
    RemediationActionKind,
    RemediationRisk,
    RemediationStatus,
)

_STAGE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("diagnose-run", ("diagnostics/", "diagnostic-report")),
    ("replay-verify", ("replay/", "replay-verification")),
    (
        "prepare-export",
        (
            "export-readiness",
            "export-bundle",
            "export-section",
            "export-claim",
            "prose-generation-contract",
            "latex-export-plan",
        ),
    ),
    ("final-audit", ("final-audit", "release-gate")),
    ("assemble-paper-skeleton", ("paper-skeleton", "paper-assembly")),
    (
        "package-research-object",
        (
            "research-object",
            "artifact-manifest",
            "ledger-summary",
            "branch-outcomes",
            "reproducibility-manifest",
            "research_object/",
        ),
    ),
    (
        "build-draft-skeleton",
        ("draft-skeleton", "manuscript-checklist", "checklist"),
    ),
    (
        "plan-manuscript",
        ("manuscript-plan", "claim-table", "blocked-claims"),
    ),
    (
        "synthesize-abstract",
        (
            "abstract-synthesis",
            "abstraction-report",
            "abstraction-attack",
            "final-nucleus",
        ),
    ),
    (
        "run-stage-c",
        (
            "stage-c-verification",
            "fake-proof",
            "fake-synthetic-experiment",
        ),
    ),
    (
        "select-stage-c",
        (
            "stage-c-selection",
            "budget-selection",
            "redteam-selection",
            "uncertainty-",
            "stage-c-score",
        ),
    ),
    ("run-stage-b", ("stage-b-report", "stage-b-score", "stage-b")),
    ("run-stage-a", ("stage-a-report", "stage-a-score", "opportunity-report")),
)

_STAGE_COMMANDS = {
    "run-stage-a": (
        'uv run factori run-stage-a --run-id {run_id} --domain "<domain>"'
    ),
    "run-stage-b": "uv run factori run-stage-b --run-id {run_id}",
    "select-stage-c": "uv run factori select-stage-c --run-id {run_id}",
    "run-stage-c": "uv run factori run-stage-c --run-id {run_id}",
    "synthesize-abstract": "uv run factori synthesize-abstract --run-id {run_id}",
    "plan-manuscript": "uv run factori plan-manuscript --run-id {run_id}",
    "build-draft-skeleton": (
        "uv run factori build-draft-skeleton --run-id {run_id}"
    ),
    "package-research-object": (
        "uv run factori package-research-object --run-id {run_id}"
    ),
    "assemble-paper-skeleton": (
        "uv run factori assemble-paper-skeleton --run-id {run_id}"
    ),
    "final-audit": "uv run factori final-audit --run-id {run_id}",
    "prepare-export": "uv run factori prepare-export --run-id {run_id}",
    "replay-verify": "uv run factori replay-verify --run-id {run_id}",
    "diagnose-run": "uv run factori diagnose-run --run-id {run_id}",
}


def recommend_stage_for_hygiene_finding(
    finding: OutputHygieneFinding,
) -> str | None:
    """Infer the safest producing stage from explicit output names and paths."""
    corpus = " ".join(
        [
            finding.message,
            finding.expected or "",
            finding.observed or "",
            *finding.paths,
        ]
    ).lower()
    for stage, patterns in _STAGE_PATTERNS:
        if any(pattern in corpus for pattern in patterns):
            return stage
    return None


def remediation_action_for_finding(
    run_id: str,
    finding: OutputHygieneFinding,
) -> RemediationAction:
    """Map one hygiene finding to one conservative non-executing action."""
    stage = recommend_stage_for_hygiene_finding(finding)
    category = finding.category

    if category == OutputHygieneCategory.ORPHANED_ARTIFACT:
        return _action(
            run_id,
            finding,
            RemediationActionKind.QUARANTINE_UNMANIFESTED_FILE,
            RemediationRisk.MEDIUM,
            "Review the unlinked file and quarantine it outside the run if it is not needed.",
        )
    if category == OutputHygieneCategory.MISSING_MANIFEST_ENTRY:
        return _action(
            run_id,
            finding,
            RemediationActionKind.REGENERATE_MANIFEST,
            RemediationRisk.MEDIUM,
            "Review provenance, then regenerate the research-object manifest from the ledger.",
            stage="package-research-object",
        )
    if category == OutputHygieneCategory.MISSING_FILE_FOR_MANIFEST_ENTRY:
        if stage is not None:
            return _rerun_action(run_id, finding, stage, RemediationRisk.HIGH)
        return _action(
            run_id,
            finding,
            RemediationActionKind.RESTORE_MISSING_ARTIFACT,
            RemediationRisk.HIGH,
            "Restore the exact hashed artifact from a trusted copy or inspect the run manually.",
        )
    if category == OutputHygieneCategory.HASH_MISMATCH:
        if stage is not None:
            return _rerun_action(run_id, finding, stage, RemediationRisk.HIGH)
        return _action(
            run_id,
            finding,
            RemediationActionKind.INSPECT_MANUALLY,
            RemediationRisk.HIGH,
            "Inspect the hash drift before deciding whether the producing stage can be rerun.",
        )
    if category == OutputHygieneCategory.DUPLICATE_OUTPUT:
        return _action(
            run_id,
            finding,
            RemediationActionKind.INSPECT_MANUALLY,
            RemediationRisk.MEDIUM,
            "Compare duplicate outputs and retain provenance until a human identifies the copy.",
        )
    if category == OutputHygieneCategory.STALE_OUTPUT:
        if _clearly_temp_or_cache(finding.paths):
            return _action(
                run_id,
                finding,
                RemediationActionKind.REMOVE_STALE_TEMP_FILE,
                RemediationRisk.LOW,
                "Remove the clearly temporary or cache file only after human confirmation.",
            )
        return _action(
            run_id,
            finding,
            RemediationActionKind.INSPECT_MANUALLY,
            RemediationRisk.MEDIUM,
            "Inspect the stale output and its ledger or sidecar references before any change.",
            stage=stage,
        )
    if category == OutputHygieneCategory.NON_PROVENANCE_LEAK:
        if _mentions_manifest(finding):
            return _action(
                run_id,
                finding,
                RemediationActionKind.REGENERATE_MANIFEST,
                RemediationRisk.HIGH,
                "Regenerate the manifest only after confirming excluded reports are not evidence.",
                stage="package-research-object",
            )
        return _action(
            run_id,
            finding,
            RemediationActionKind.REMOVE_NON_PROVENANCE_REPORT,
            RemediationRisk.MEDIUM,
            "Remove the leaked derived report from provenance only after human confirmation.",
            stage=stage,
        )
    if category == OutputHygieneCategory.MANIFEST_EXCLUSION_VIOLATION:
        return _action(
            run_id,
            finding,
            RemediationActionKind.REGENERATE_MANIFEST,
            RemediationRisk.HIGH,
            "Inspect the exclusion violation, then regenerate packaging outputs from the ledger.",
            stage="package-research-object",
        )
    if category == OutputHygieneCategory.EVIDENCE_BOUNDARY_RISK:
        if stage is not None:
            return _rerun_action(run_id, finding, stage, RemediationRisk.HIGH)
        return _action(
            run_id,
            finding,
            RemediationActionKind.REJECT_RUN_AS_INCONSISTENT,
            RemediationRisk.UNSAFE,
            "Reject the run until a human resolves the evidence-boundary violation.",
        )
    if category == OutputHygieneCategory.REPLAY_DIAGNOSTICS_LEAK:
        return _action(
            run_id,
            finding,
            RemediationActionKind.REMOVE_NON_PROVENANCE_REPORT,
            RemediationRisk.MEDIUM,
            "Keep replay or diagnostics outside provenance and remove the leaked linkage.",
            stage=stage,
        )
    if category in {
        OutputHygieneCategory.UNEXPECTED_DIRECTORY,
        OutputHygieneCategory.UNEXPECTED_FILE,
        OutputHygieneCategory.UNKNOWN,
    }:
        risk = (
            RemediationRisk.HIGH
            if finding.severity == OutputHygieneSeverity.BLOCKING
            else RemediationRisk.MEDIUM
        )
        return _action(
            run_id,
            finding,
            RemediationActionKind.INSPECT_MANUALLY,
            risk,
            "Inspect this unclassified output without changing the run.",
            stage=stage,
        )
    return _action(
        run_id,
        finding,
        RemediationActionKind.INSPECT_MANUALLY,
        RemediationRisk.HIGH,
        "No specific safe remediation mapping exists; inspect the run manually.",
        stage=stage,
    )


def command_for_stage(stage: str, run_id: str) -> str | None:
    """Return a deterministic suggested command without executing it."""
    template = _STAGE_COMMANDS.get(stage)
    return template.format(run_id=run_id) if template is not None else None


def _rerun_action(
    run_id: str,
    finding: OutputHygieneFinding,
    stage: str,
    risk: RemediationRisk,
) -> RemediationAction:
    return _action(
        run_id,
        finding,
        RemediationActionKind.RERUN_PRODUCING_STAGE,
        risk,
        f"After manual inspection, rerun {stage} and its dependent downstream stages.",
        stage=stage,
    )


def _action(
    run_id: str,
    finding: OutputHygieneFinding,
    kind: RemediationActionKind,
    risk: RemediationRisk,
    reason: str,
    *,
    stage: str | None = None,
) -> RemediationAction:
    status = (
        RemediationStatus.UNSAFE_TO_EXECUTE
        if risk == RemediationRisk.UNSAFE
        else RemediationStatus.RECOMMENDED
        if risk == RemediationRisk.LOW
        else RemediationStatus.MANUAL_APPROVAL_REQUIRED
    )
    return RemediationAction(
        action_id=f"remediate-{_safe_id(finding.finding_id)}",
        finding_id=finding.finding_id,
        finding_category=finding.category,
        kind=kind,
        risk=risk,
        status=status,
        reason=reason,
        paths=sorted(set(finding.paths)),
        recommended_stage=stage,
        recommended_command=(
            command_for_stage(stage, run_id) if stage is not None else None
        ),
        requires_human_review=True,
        execution_performed=False,
    )


def _clearly_temp_or_cache(paths: list[str]) -> bool:
    suffixes = {".bak", ".cache", ".old", ".orig", ".pyc", ".swp", ".temp", ".tmp"}
    return bool(paths) and all(
        PurePosixPath(path).suffix.lower() in suffixes
        or "__pycache__" in PurePosixPath(path).parts
        for path in paths
    )


def _mentions_manifest(finding: OutputHygieneFinding) -> bool:
    corpus = " ".join([finding.message, *finding.paths]).lower()
    return "manifest" in corpus


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-") or "finding"


__all__ = [
    "command_for_stage",
    "recommend_stage_for_hygiene_finding",
    "remediation_action_for_finding",
]
