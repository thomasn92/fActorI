"""Runtime context compression skeleton."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from factori.schemas import (
    BranchStatus,
    ControllerActionType,
    LedgerCommit,
    RuntimeSummary,
    VerificationLabel,
)


def compress_runtime_history(
    commits_or_events: Iterable[LedgerCommit | dict[str, Any]],
) -> RuntimeSummary:
    """Compress ledger commits or compact event dicts without mutating provenance."""
    events = [_event_from_item(item) for item in commits_or_events]
    if not events:
        return RuntimeSummary(
            action_count=0,
            failed_repair_count=0,
            short_summary="No runtime events. Ledger remains the source of truth.",
        )

    candidate_id = _last_non_empty("candidate_id", events)
    last_action = str(events[-1]["action"])
    scores = [event["score"] for event in events if event["score"] is not None]
    last_score = scores[-1] if scores else None
    best_score = max(scores) if scores else None
    verification_label = _last_verification_label(events)
    status = _last_status(events)
    failed_repair_count = sum(1 for event in events if bool(event["failed_repair"]))
    return RuntimeSummary(
        candidate_id=candidate_id,
        action_count=len(events),
        last_action=last_action,
        failed_repair_count=failed_repair_count,
        last_score=last_score,
        best_score=best_score,
        verification_label=verification_label,
        status=status,
        short_summary=(
            f"{len(events)} compressed events for {candidate_id or 'run'}; "
            "not provenance; ledger remains the source of truth."
        ),
    )


def _event_from_item(item: LedgerCommit | dict[str, Any]) -> dict[str, Any]:
    if isinstance(item, LedgerCommit):
        payload = item.payload
        return {
            "candidate_id": item.candidate_id,
            "action": item.action_type.value,
            "score": _score_from_payload(payload),
            "verification_label": _verification_from_payload(payload),
            "status": _status_from_payload(payload),
            "failed_repair": _is_failed_repair(item.action_type, payload),
        }
    return {
        "candidate_id": item.get("candidate_id"),
        "action": str(item.get("action", "Unknown")),
        "score": item.get("score"),
        "verification_label": item.get("verification_label", VerificationLabel.UNSUPPORTED),
        "status": item.get("status", BranchStatus.ACTIVE),
        "failed_repair": bool(item.get("failed_repair", False)),
    }


def _score_from_payload(payload: dict[str, Any]) -> float | None:
    if "base_score" in payload:
        return float(payload["base_score"])
    if "score" in payload and isinstance(payload["score"], int | float):
        return float(payload["score"])
    return None


def _verification_from_payload(payload: dict[str, Any]) -> VerificationLabel:
    label = payload.get("verification_label")
    if label is None:
        return VerificationLabel.UNSUPPORTED
    return VerificationLabel(str(label))


def _status_from_payload(payload: dict[str, Any]) -> BranchStatus:
    status = payload.get("to_status") or payload.get("status")
    if status is None:
        return BranchStatus.ACTIVE
    return BranchStatus(str(status))


def _is_failed_repair(action_type: ControllerActionType, payload: dict[str, Any]) -> bool:
    action_name = action_type.value.lower()
    return bool(payload.get("failed_repair", False)) or (
        "repair" in action_name and payload.get("result") == "failed"
    )


def _last_non_empty(key: str, events: list[dict[str, Any]]) -> str | None:
    for event in reversed(events):
        value = event.get(key)
        if value:
            return str(value)
    return None


def _last_verification_label(events: list[dict[str, Any]]) -> VerificationLabel:
    for event in reversed(events):
        label = event.get("verification_label")
        if isinstance(label, VerificationLabel):
            return label
        if label:
            return VerificationLabel(str(label))
    return VerificationLabel.UNSUPPORTED


def _last_status(events: list[dict[str, Any]]) -> BranchStatus:
    for event in reversed(events):
        status = event.get("status")
        if isinstance(status, BranchStatus):
            return status
        if status:
            return BranchStatus(str(status))
    return BranchStatus.ACTIVE
