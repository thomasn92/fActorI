from __future__ import annotations

import pytest

from factori.remediation import (
    command_for_stage,
    recommend_stage_for_hygiene_finding,
    remediation_action_for_finding,
)
from factori.schemas import (
    OutputHygieneCategory,
    OutputHygieneFinding,
    OutputHygieneSeverity,
    RemediationActionKind,
    RemediationRisk,
)


@pytest.mark.parametrize(
    ("path", "stage"),
    [
        ("reports/stage-a-report.md", "run-stage-a"),
        ("reports/stage-b-report.md", "run-stage-b"),
        ("reports/stage-c-selection-report.md", "select-stage-c"),
        ("reports/stage-c-verification-report.md", "run-stage-c"),
        ("reports/abstract-synthesis-report.md", "synthesize-abstract"),
        ("reports/manuscript-plan.json", "plan-manuscript"),
        ("reports/claim-table.json", "plan-manuscript"),
        ("reports/draft-skeleton.json", "build-draft-skeleton"),
        ("reports/manuscript-checklist.json", "build-draft-skeleton"),
        ("research_object/research-object.json", "package-research-object"),
        ("research_object/artifact-manifest.json", "package-research-object"),
        ("research_object/paper-skeleton.json", "assemble-paper-skeleton"),
        ("reports/final-audit-report.json", "final-audit"),
        ("reports/export-readiness-report.json", "prepare-export"),
        ("replay/replay-verification-report.json", "replay-verify"),
        ("diagnostics/diagnostic-report.json", "diagnose-run"),
    ],
)
def test_recommended_stage_inference_is_deterministic(path, stage) -> None:
    finding = _finding(OutputHygieneCategory.STALE_OUTPUT, paths=[path])

    first = recommend_stage_for_hygiene_finding(finding)
    second = recommend_stage_for_hygiene_finding(finding)

    assert first == stage
    assert second == stage


def test_orphaned_artifact_maps_to_quarantine_recommendation() -> None:
    action = remediation_action_for_finding(
        "run-1",
        _finding(OutputHygieneCategory.ORPHANED_ARTIFACT),
    )

    assert action.kind == RemediationActionKind.QUARANTINE_UNMANIFESTED_FILE
    assert not action.execution_performed


def test_missing_manifest_entry_maps_to_regeneration() -> None:
    action = remediation_action_for_finding(
        "run-1",
        _finding(OutputHygieneCategory.MISSING_MANIFEST_ENTRY),
    )

    assert action.kind == RemediationActionKind.REGENERATE_MANIFEST
    assert action.risk == RemediationRisk.MEDIUM
    assert action.recommended_stage == "package-research-object"


def test_missing_file_maps_to_known_producing_stage() -> None:
    action = remediation_action_for_finding(
        "run-1",
        _finding(
            OutputHygieneCategory.MISSING_FILE_FOR_MANIFEST_ENTRY,
            paths=["reports/stage-b-report.md"],
        ),
    )

    assert action.kind == RemediationActionKind.RERUN_PRODUCING_STAGE
    assert action.recommended_stage == "run-stage-b"
    assert "run-stage-b" in (action.recommended_command or "")


def test_missing_file_without_known_stage_maps_to_restore() -> None:
    action = remediation_action_for_finding(
        "run-1",
        _finding(
            OutputHygieneCategory.MISSING_FILE_FOR_MANIFEST_ENTRY,
            paths=["reports/custom-output.json"],
        ),
    )

    assert action.kind == RemediationActionKind.RESTORE_MISSING_ARTIFACT


def test_hash_mismatch_is_high_risk_and_never_executed() -> None:
    action = remediation_action_for_finding(
        "run-1",
        _finding(
            OutputHygieneCategory.HASH_MISMATCH,
            paths=["reports/manuscript-plan.json"],
        ),
    )

    assert action.kind == RemediationActionKind.RERUN_PRODUCING_STAGE
    assert action.risk == RemediationRisk.HIGH
    assert action.recommended_stage == "plan-manuscript"
    assert not action.execution_performed


def test_unknown_hash_mismatch_requires_manual_inspection() -> None:
    action = remediation_action_for_finding(
        "run-1",
        _finding(OutputHygieneCategory.HASH_MISMATCH),
    )

    assert action.kind == RemediationActionKind.INSPECT_MANUALLY
    assert action.risk == RemediationRisk.HIGH


def test_evidence_boundary_risk_rejects_unknown_run_state() -> None:
    action = remediation_action_for_finding(
        "run-1",
        _finding(OutputHygieneCategory.EVIDENCE_BOUNDARY_RISK),
    )

    assert action.kind == RemediationActionKind.REJECT_RUN_AS_INCONSISTENT
    assert action.risk == RemediationRisk.UNSAFE


def test_evidence_boundary_risk_can_recommend_planning_rerun() -> None:
    action = remediation_action_for_finding(
        "run-1",
        _finding(
            OutputHygieneCategory.EVIDENCE_BOUNDARY_RISK,
            paths=["reports/claim-table.json"],
        ),
    )

    assert action.kind == RemediationActionKind.RERUN_PRODUCING_STAGE
    assert action.recommended_stage == "plan-manuscript"


def test_replay_diagnostics_leak_maps_to_non_provenance_cleanup() -> None:
    action = remediation_action_for_finding(
        "run-1",
        _finding(
            OutputHygieneCategory.REPLAY_DIAGNOSTICS_LEAK,
            paths=["replay/replay-verification-report.json"],
        ),
    )

    assert action.kind == RemediationActionKind.REMOVE_NON_PROVENANCE_REPORT
    assert action.recommended_stage == "replay-verify"


def test_unknown_finding_maps_to_manual_inspection() -> None:
    action = remediation_action_for_finding(
        "run-1",
        _finding(OutputHygieneCategory.UNKNOWN),
    )

    assert action.kind == RemediationActionKind.INSPECT_MANUALLY


def test_stale_temp_file_maps_to_low_risk_removal_recommendation() -> None:
    action = remediation_action_for_finding(
        "run-1",
        _finding(OutputHygieneCategory.STALE_OUTPUT, paths=["reports/stale.tmp"]),
    )

    assert action.kind == RemediationActionKind.REMOVE_STALE_TEMP_FILE
    assert action.risk == RemediationRisk.LOW


def test_stage_commands_are_suggestions_only() -> None:
    command = command_for_stage("prepare-export", "run-1")

    assert command == "uv run factori prepare-export --run-id run-1"


def _finding(
    category: OutputHygieneCategory,
    *,
    paths: list[str] | None = None,
) -> OutputHygieneFinding:
    return OutputHygieneFinding(
        finding_id=f"finding-{category.value}",
        category=category,
        severity=OutputHygieneSeverity.BLOCKING,
        message=f"deterministic {category.value} finding",
        paths=paths or [],
    )
