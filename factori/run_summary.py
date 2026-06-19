"""Deterministic run and branch outcome summaries."""

from __future__ import annotations

from collections import Counter

from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.schemas import (
    BranchOutcomeSummary,
    BranchStatus,
    ControllerActionType,
    LedgerSummary,
    VerificationLabel,
)


def build_ledger_summary(run_id: str, ledger: ResearchLedger) -> LedgerSummary:
    """Build a deterministic ledger summary without mutating the ledger."""
    commits = ledger.list_commits(run_id)
    action_counts = Counter(commit.action_type.value for commit in commits)
    candidate_ids = {commit.candidate_id for commit in commits if commit.candidate_id}
    artifact_keys = {
        (artifact.path, artifact.content_hash)
        for commit in commits
        for artifact in commit.artifact_refs
    }
    human_tail_escalations = sum(
        1
        for commit in commits
        if _payload_contains_value(commit.payload, "AskHuman")
        or _payload_contains_value(commit.payload, BranchStatus.NEEDS_HUMAN_TAIL_ESCALATION.value)
    )
    return LedgerSummary(
        run_id=run_id,
        commit_count=len(commits),
        root_commit_hash=commits[0].commit_hash if commits else None,
        latest_commit_hash=commits[-1].commit_hash if commits else None,
        action_type_counts=dict(sorted(action_counts.items())),
        candidate_count=len(candidate_ids),
        artifact_count=len(artifact_keys),
        verification_decision_count=action_counts[
            ControllerActionType.STAGE_C_VERIFICATION_DECIDED.value
        ],
        human_tail_escalation_count=human_tail_escalations,
    )


def build_branch_outcomes(
    run_id: str,
    ledger: ResearchLedger,
    artifact_store: ArtifactStore | None = None,
) -> list[BranchOutcomeSummary]:
    """Build deterministic branch outcome summaries from ledger commits."""
    del artifact_store
    outcomes: list[BranchOutcomeSummary] = []
    for commit in ledger.list_commits(run_id):
        candidate_id = commit.candidate_id
        if commit.action_type == ControllerActionType.STAGE_C_VERIFICATION_DECIDED:
            label = VerificationLabel(commit.payload["label"])
            outcomes.append(
                BranchOutcomeSummary(
                    candidate_id=commit.payload["candidate_id"],
                    outcome=label.value,
                    status=BranchStatus(commit.payload["status"]),
                    verification_label=label,
                    action_type=commit.action_type,
                    reason=str(commit.payload.get("reason", "Stage C verification decision")),
                )
            )
        elif commit.action_type == ControllerActionType.STAGE_C_SELECTION_DECIDED and candidate_id:
            status_value = commit.payload.get("to_status")
            if status_value is not None:
                status = BranchStatus(status_value)
                outcomes.append(
                    BranchOutcomeSummary(
                        candidate_id=candidate_id,
                        outcome=status.value,
                        status=status,
                        action_type=commit.action_type,
                        reason=str(commit.payload.get("reason", "Stage C selection decision")),
                    )
                )
        elif commit.action_type in {
            ControllerActionType.STAGE_A_DATA_GATE_DEFERRED,
            ControllerActionType.STAGE_A_DUPLICATE_PRUNED,
            ControllerActionType.STAGE_A_GATE_PRUNED,
            ControllerActionType.STAGE_B_GATE_PRUNED,
        } and candidate_id:
            status = _status_from_payload(commit.payload)
            if status is not None:
                outcomes.append(
                    BranchOutcomeSummary(
                        candidate_id=candidate_id,
                        outcome=status.value,
                        status=status,
                        action_type=commit.action_type,
                        reason=str(commit.payload.get("reason", "branch pruning decision")),
                    )
                )
        elif commit.action_type == ControllerActionType.BLOCKED_CLAIMS_IDENTIFIED:
            for blocked_claim in commit.payload.get("blocked_claims", []):
                outcomes.append(
                    BranchOutcomeSummary(
                        candidate_id=str(blocked_claim["candidate_id"]),
                        outcome="BlockedClaim",
                        verification_label=VerificationLabel(blocked_claim["claim_label"]),
                        action_type=commit.action_type,
                        reason=str(blocked_claim["blocked_reason"]),
                    )
                )
    return sorted(
        outcomes,
        key=lambda outcome: (
            outcome.candidate_id,
            outcome.outcome,
            outcome.action_type.value,
            outcome.reason,
        ),
    )


def _status_from_payload(payload: dict[str, object]) -> BranchStatus | None:
    for key in ["to_status", "status", "final_status"]:
        value = payload.get(key)
        if value is not None:
            try:
                return BranchStatus(str(value))
            except ValueError:
                return None
    return None


def _payload_contains_value(payload, value: str) -> bool:
    if isinstance(payload, dict):
        return any(_payload_contains_value(item, value) for item in payload.values())
    if isinstance(payload, list):
        return any(_payload_contains_value(item, value) for item in payload)
    return str(payload) == value
