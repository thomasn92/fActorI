"""Read-only orchestration for deterministic hygiene remediation planning."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from factori.config import DEFAULT_ROOT
from factori.hashing import canonical_json
from factori.remediation import remediation_action_for_finding
from factori.reports import render_hygiene_remediation_plan_markdown
from factori.schemas import (
    HygieneRemediationPlan,
    OutputHygieneReport,
    OutputHygieneStatus,
    RemediationActionKind,
    RemediationPlanStatus,
    RemediationRisk,
)


def build_hygiene_remediation_plan(
    hygiene_report: OutputHygieneReport,
) -> HygieneRemediationPlan:
    """Build a deterministic recommendation plan without executing any action."""
    actions = [
        remediation_action_for_finding(hygiene_report.run_id, finding)
        for finding in sorted(
            hygiene_report.findings,
            key=lambda item: item.finding_id,
        )
    ]
    warnings: list[str] = []
    if hygiene_report.ledger_mutated:
        warnings.append("source hygiene inspection reported ledger mutation")
    if hygiene_report.artifact_manifest_mutated:
        warnings.append("source hygiene inspection reported artifact manifest mutation")
    return HygieneRemediationPlan(
        run_id=hygiene_report.run_id,
        source_hygiene_status=hygiene_report.hygiene_status,
        plan_status=_plan_status(hygiene_report, actions),
        actions=actions,
        source_finding_ids=sorted(
            finding.finding_id for finding in hygiene_report.findings
        ),
        warnings=warnings,
        ledger_mutated=hygiene_report.ledger_mutated,
        artifact_manifest_mutated=hygiene_report.artifact_manifest_mutated,
        execution_performed=False,
    )


def summarize_hygiene_remediation_plan(
    plan: HygieneRemediationPlan,
) -> dict[str, Any]:
    """Return a deterministic compact remediation-plan summary."""
    return {
        "run_id": plan.run_id,
        "actions_total": len(plan.actions),
        "low_risk_actions": _risk_count(plan, RemediationRisk.LOW),
        "medium_risk_actions": _risk_count(plan, RemediationRisk.MEDIUM),
        "high_risk_actions": _risk_count(plan, RemediationRisk.HIGH),
        "unsafe_actions": _risk_count(plan, RemediationRisk.UNSAFE),
        "manual_inspection_actions": sum(
            action.kind == RemediationActionKind.INSPECT_MANUALLY
            for action in plan.actions
        ),
        "rerun_stage_actions": sum(
            action.kind == RemediationActionKind.RERUN_PRODUCING_STAGE
            for action in plan.actions
        ),
        "plan_status": plan.plan_status.value,
    }


def write_hygiene_remediation_plan(
    *,
    plan: HygieneRemediationPlan,
    root: str | Path = DEFAULT_ROOT,
) -> tuple[Path, Path]:
    """Write an optional plan outside provenance, evidence, manifests, and the ledger."""
    hygiene_path = Path(root) / "runs" / plan.run_id / "hygiene"
    hygiene_path.mkdir(parents=True, exist_ok=True)
    payload = {
        "not_provenance": True,
        "not_evidence": True,
        "not_ledgered": True,
        "plan": plan.model_dump(mode="json"),
    }
    json_path = hygiene_path / "remediation-plan.json"
    markdown_path = hygiene_path / "remediation-plan.md"
    json_path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    markdown = "\n".join(
        [
            "---",
            "not_provenance: true",
            "not_evidence: true",
            "not_ledgered: true",
            "---",
            "",
            render_hygiene_remediation_plan_markdown(remediation_plan=plan),
        ]
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def _plan_status(
    hygiene_report: OutputHygieneReport,
    actions: list,
) -> RemediationPlanStatus:
    if hygiene_report.hygiene_status == OutputHygieneStatus.HYGIENE_INSPECTION_FAILED:
        return RemediationPlanStatus.RUN_INCONSISTENT
    if any(
        action.kind == RemediationActionKind.REJECT_RUN_AS_INCONSISTENT
        or action.risk == RemediationRisk.UNSAFE
        for action in actions
    ):
        return RemediationPlanStatus.RUN_INCONSISTENT
    if not actions:
        return RemediationPlanStatus.NO_REMEDIATION_NEEDED
    if any(
        action.kind == RemediationActionKind.INSPECT_MANUALLY
        or action.risk == RemediationRisk.HIGH
        for action in actions
    ):
        return RemediationPlanStatus.MANUAL_INSPECTION_REQUIRED
    return RemediationPlanStatus.REMEDIATION_RECOMMENDED


def _risk_count(plan: HygieneRemediationPlan, risk: RemediationRisk) -> int:
    return sum(action.risk == risk for action in plan.actions)


__all__ = [
    "build_hygiene_remediation_plan",
    "summarize_hygiene_remediation_plan",
    "write_hygiene_remediation_plan",
]
