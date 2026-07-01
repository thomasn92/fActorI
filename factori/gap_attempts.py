"""Deterministic gap fingerprints, attempt history, and planned-spec de-duplication."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.hashing import sha256_json
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AutonomousEvidenceGapPlan,
    AutonomousEvidenceGapPlanItem,
    AutonomousPlanExecutionAction,
    AutonomousPlanExecutionReport,
    ControllerActionType,
    GapAttemptHistory,
    GapAttemptRecord,
    PlannedExperimentSpec,
    PlannedSpecDedupIndex,
    PlannedSpecDuplicateRecord,
    PlannedSpecExecutionItem,
    PlannedSpecExecutionReport,
    ProofArtifact,
    ProofObligationSpec,
    RetrievalExpansionRequest,
)

_SPEC_PATTERNS = (
    ("experiment_spec", "experiment-spec-*.json", PlannedExperimentSpec),
    ("proof_obligation_spec", "proof-obligation-spec-*.json", ProofObligationSpec),
    (
        "retrieval_expansion_request",
        "retrieval-expansion-request-*.json",
        RetrievalExpansionRequest,
    ),
)


class GapAttemptHistoryError(RuntimeError):
    """Raised when gap-attempt history cannot be inspected."""


@dataclass(frozen=True)
class GapAttemptPersistenceResult:
    """Persisted gap-attempt history and de-dup index."""

    history: GapAttemptHistory
    dedup_index: PlannedSpecDedupIndex
    persistence: PersistenceResult
    history_artifact: ArtifactRef
    dedup_index_artifact: ArtifactRef


@dataclass
class _MutableGapAttemptRecord:
    """Mutable aggregation state converted to the frozen public schema at persistence."""

    gap_fingerprint: str
    target_claim_id_optional: str | None = None
    target_section_optional: str | None = None
    gap_type: str = "planned_spec_execution"
    recommended_action: str = "Execute planned spec."
    expected_artifact_type: str = "workflow_artifact"
    first_seen_iteration_optional: int | None = None
    latest_seen_iteration_optional: int | None = None
    attempt_count: int = 0
    successful_attempt_count: int = 0
    deferred_attempt_count: int = 0
    failed_attempt_count: int = 0
    no_op_attempt_count: int = 0
    latest_attempt_status: str | None = None
    current_gap_status: str = "open"
    created_spec_fingerprints: list[str] = field(default_factory=list)
    created_artifact_paths: list[str] = field(default_factory=list)
    linked_evidence_artifact_ids: list[str] = field(default_factory=list)
    resolution_reason_optional: str | None = None
    exhaustion_reason_optional: str | None = None


def gap_fingerprint_for_plan_item(
    *,
    run_id: str,
    item: AutonomousEvidenceGapPlanItem,
) -> str:
    """Return a stable fingerprint for one gap/action target."""
    return sha256_json(
        {
            "kind": "gap",
            "run_id": run_id,
            "target_claim_id_optional": item.target_claim_id_optional,
            "target_claim_text_hash_optional": _claim_hash_from_inputs(item.required_inputs),
            "target_section_optional": item.target_section_optional,
            "gap_type": item.gap_type,
            "recommended_action": item.recommended_action,
            "expected_artifact_type": item.expected_artifact_type,
            "required_inputs": sorted(item.required_inputs),
            "support_status": item.current_support_status,
        }
    )


def plan_item_fingerprint(
    *,
    run_id: str,
    item: AutonomousEvidenceGapPlanItem,
) -> str:
    """Return a stable fingerprint for a plan item without generated IDs."""
    return sha256_json(
        {
            "kind": "plan_item",
            "gap_fingerprint": gap_fingerprint_for_plan_item(run_id=run_id, item=item),
            "target_type": item.target_type,
            "priority": item.priority,
            "blocking": item.blocking,
            "automation_ready": item.automation_ready,
        }
    )


def planned_spec_fingerprint(
    spec: PlannedExperimentSpec | ProofObligationSpec | RetrievalExpansionRequest,
) -> str:
    """Return a stable fingerprint for a planned spec without generated spec IDs."""
    if isinstance(spec, PlannedExperimentSpec):
        payload: dict[str, Any] = {
            "kind": "planned_spec",
            "spec_type": "experiment_spec",
            "run_id": spec.run_id,
            "target_claim_id": spec.target_claim_id,
            "target_section": spec.target_section,
            "hypothesis_or_question": spec.hypothesis_or_question,
            "suggested_dataset": spec.suggested_dataset,
            "suggested_metrics": sorted(spec.suggested_metrics),
            "suggested_baselines": sorted(spec.suggested_baselines),
            "suggested_seed_policy": spec.suggested_seed_policy,
            "expected_output_artifacts": sorted(spec.expected_output_artifacts),
            "status": spec.status,
        }
    elif isinstance(spec, ProofObligationSpec):
        payload = {
            "kind": "planned_spec",
            "spec_type": "proof_obligation_spec",
            "run_id": spec.run_id,
            "target_claim_id": spec.target_claim_id,
            "statement": spec.statement,
            "suggested_checker": spec.suggested_checker,
            "required_artifact_type": spec.required_artifact_type,
            "status": spec.status,
        }
    else:
        payload = {
            "kind": "planned_spec",
            "spec_type": "retrieval_expansion_request",
            "run_id": spec.run_id,
            "target_claim_id_optional": spec.target_claim_id_optional,
            "target_section_optional": spec.target_section_optional,
            "query_terms": sorted(spec.query_terms),
            "reason": spec.reason,
            "minimum_source_quality": spec.minimum_source_quality,
            "status": spec.status,
        }
    return sha256_json(payload)


def execution_attempt_fingerprint(
    *,
    run_id: str,
    execution_id: str,
    target_id: str,
    gap_fingerprint: str | None,
    planned_spec_fingerprint_value: str | None,
    decision: str,
    status: str,
) -> str:
    """Return a stable execution-attempt fingerprint."""
    return sha256_json(
        {
            "kind": "execution_attempt",
            "run_id": run_id,
            "execution_id": execution_id,
            "target_id": target_id,
            "gap_fingerprint": gap_fingerprint,
            "planned_spec_fingerprint": planned_spec_fingerprint_value,
            "decision": decision,
            "status": status,
        }
    )


def annotate_plan_items_with_history(
    *,
    run_id: str,
    items: list[AutonomousEvidenceGapPlanItem],
    history: GapAttemptHistory | None,
) -> list[AutonomousEvidenceGapPlanItem]:
    """Attach fingerprints and suppress automation-ready state for exhausted gaps."""
    records = {record.gap_fingerprint: record for record in history.records} if history else {}
    annotated: list[AutonomousEvidenceGapPlanItem] = []
    for item in items:
        gap_fp = gap_fingerprint_for_plan_item(run_id=run_id, item=item)
        item_fp = plan_item_fingerprint(run_id=run_id, item=item)
        record = records.get(gap_fp)
        exhausted = bool(record and record.current_gap_status == "exhausted_no_progress")
        automation_ready = bool(item.automation_ready and not exhausted)
        annotated.append(
            item.model_copy(
                update={
                    "gap_fingerprint": gap_fp,
                    "plan_item_fingerprint": item_fp,
                    "gap_attempt_history_present": history is not None,
                    "gap_attempt_count": record.attempt_count if record else 0,
                    "gap_already_attempted": bool(record and record.attempt_count > 0),
                    "gap_exhausted": exhausted,
                    "automation_ready": automation_ready,
                    "automation_ready_after_history": automation_ready,
                }
            )
        )
    return annotated


def latest_gap_attempt_history_path(root: Path, run_id: str) -> Path | None:
    """Return the latest persisted gap-attempt history path."""
    reports = _reports(root, run_id)
    paths = [
        path
        for path in reports.glob("gap-attempt-history-*.json")
        if not path.name.endswith(".meta.json")
    ]
    return sorted(paths)[-1] if paths else None


def latest_planned_spec_dedup_index_path(root: Path, run_id: str) -> Path | None:
    """Return the latest persisted planned-spec de-dup index path."""
    reports = _reports(root, run_id)
    paths = [
        path
        for path in reports.glob("planned-spec-dedup-index-*.json")
        if not path.name.endswith(".meta.json")
    ]
    return sorted(paths)[-1] if paths else None


def load_latest_gap_attempt_history(root: Path, run_id: str) -> GapAttemptHistory | None:
    """Load the latest gap-attempt history if present and valid."""
    path = latest_gap_attempt_history_path(root, run_id)
    if path is None:
        return None
    try:
        return GapAttemptHistory.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def build_gap_attempt_history(
    *,
    run_id: str,
    root: str | Path = ".",
    max_attempts_per_gap: int = 2,
    now: str = "1970-01-01T00:00:00Z",
) -> GapAttemptHistory:
    """Build a derived attempt history from append-only plan/execution artifacts."""
    root_path = Path(root)
    max_attempts = max(1, max_attempts_per_gap)
    previous = load_latest_gap_attempt_history(root_path, run_id)
    # Execution reports are append-only, so reconstruct counters from them on every build.
    # Previous history snapshots are derived views and must not be replayed as attempts.
    records: dict[str, _MutableGapAttemptRecord] = {}
    current_open = _current_open_gap_fingerprints(run_id=run_id, root=root_path)
    for gap_fp, item in current_open.items():
        _ensure_record(records, gap_fp, item)

    for report in _load_autonomous_execution_reports(root_path, run_id):
        if report.execution_mode != "apply":
            continue
        for action in report.actions:
            if action.gap_type == "sufficiently_supported_for_bounded_draft":
                continue
            gap_fp = action.gap_fingerprint or _gap_fingerprint_from_action(run_id, action)
            record = _ensure_record(records, gap_fp, action)
            _record_autonomous_action(record, action)

    for report in _load_planned_spec_execution_reports(root_path, run_id):
        if report.execution_mode != "apply":
            continue
        for item in report.items:
            gap_fp = item.gap_fingerprint or _gap_fingerprint_from_spec_item(
                root_path, run_id, item
            )
            if gap_fp is None:
                continue
            record = _ensure_record(records, gap_fp, item)
            _record_planned_spec_item(root_path, record, item)

    for gap_fp, record in records.items():
        in_current_plan = gap_fp in current_open
        record.current_gap_status = _current_status(
            record=record,
            in_current_plan=in_current_plan,
            max_attempts=max_attempts,
        )
        if record.current_gap_status == "resolved" and not record.resolution_reason_optional:
            record.resolution_reason_optional = "Gap no longer appears as an open automation item."
        if (
            record.current_gap_status == "exhausted_no_progress"
            and not record.exhaustion_reason_optional
        ):
            record.exhaustion_reason_optional = (
                f"Attempted {record.attempt_count} time(s) without scoped evidence, "
                "non-duplicate spec creation, or manuscript/support progress."
            )

    ordered = sorted(
        (_record_from_mutable(record) for record in records.values()),
        key=lambda item: item.gap_fingerprint,
    )
    return GapAttemptHistory(
        run_id=run_id,
        history_version=_next_history_version(root_path, run_id),
        gap_count=len(ordered),
        attempt_count=sum(record.attempt_count for record in ordered),
        open_gap_count=sum(record.current_gap_status == "open" for record in ordered),
        exhausted_gap_count=sum(
            record.current_gap_status == "exhausted_no_progress" for record in ordered
        ),
        deferred_gap_count=sum(record.current_gap_status == "deferred" for record in ordered),
        resolved_gap_count=sum(record.current_gap_status == "resolved" for record in ordered),
        records=ordered,
        created_at=previous.created_at if previous else now,
        updated_at=now,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
        publication_ready=False,
    )


def build_planned_spec_dedup_index(
    *,
    run_id: str,
    root: str | Path = ".",
    now: str = "1970-01-01T00:00:00Z",
) -> PlannedSpecDedupIndex:
    """Build a derived de-dup index over planned specs and skipped duplicate attempts."""
    root_path = Path(root)
    first_paths: dict[str, str] = {}
    duplicate_records: list[PlannedSpecDuplicateRecord] = []
    spec_count = 0
    for spec, path in _iter_specs(root_path, run_id):
        spec_count += 1
        fingerprint = planned_spec_fingerprint(spec)
        rel_path = path.relative_to(root_path).as_posix()
        if fingerprint not in first_paths:
            first_paths[fingerprint] = rel_path
            continue
        duplicate_records.append(
            PlannedSpecDuplicateRecord(
                duplicate_spec_fingerprint=fingerprint,
                existing_spec_path=first_paths[fingerprint],
                skipped_new_spec_reason="Equivalent planned spec already exists.",
                gap_fingerprint=_gap_fingerprint_for_spec(spec),
                duplicate_spec_path_optional=rel_path,
            )
        )
    for report in _load_autonomous_execution_reports(root_path, run_id):
        for action in report.actions:
            if action.execution_status != "skipped" or not action.planned_spec_fingerprint_optional:
                continue
            duplicate_records.append(
                PlannedSpecDuplicateRecord(
                    duplicate_spec_fingerprint=action.planned_spec_fingerprint_optional,
                    existing_spec_path=action.created_artifact_path_optional or "unknown",
                    skipped_new_spec_reason=(
                        action.rejected_reason_optional
                        or "Equivalent planned spec already exists."
                    ),
                    gap_fingerprint=action.gap_fingerprint,
                )
            )
    for report in _load_planned_spec_execution_reports(root_path, run_id):
        for item in report.items:
            if item.execution_status != "skipped" or not item.planned_spec_fingerprint:
                continue
            duplicate_records.append(
                PlannedSpecDuplicateRecord(
                    duplicate_spec_fingerprint=item.planned_spec_fingerprint,
                    existing_spec_path=item.created_artifact_path_optional or "unknown",
                    skipped_new_spec_reason=(
                        item.rejected_reason_optional
                        or "Equivalent planned spec was skipped during execution."
                    ),
                    gap_fingerprint=item.gap_fingerprint,
                )
            )
    unique_fingerprints = sorted(first_paths)
    return PlannedSpecDedupIndex(
        run_id=run_id,
        spec_count=spec_count,
        unique_spec_count=len(unique_fingerprints),
        duplicate_spec_count=len(duplicate_records),
        spec_fingerprints=unique_fingerprints,
        duplicate_records=duplicate_records,
        latest_updated_at=now,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
        publication_ready=False,
    )


def persist_gap_attempt_artifacts(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    max_attempts_per_gap: int = 2,
) -> GapAttemptPersistenceResult:
    """Persist append-only gap-attempt history and planned-spec de-dup index."""
    root_path = Path(root)
    now = ledger.clock.now()
    history = build_gap_attempt_history(
        run_id=run_id,
        root=root_path,
        max_attempts_per_gap=max_attempts_per_gap,
        now=now,
    )
    dedup = build_planned_spec_dedup_index(run_id=run_id, root=root_path, now=now)
    version = history.history_version
    history_id = f"gap-attempt-history-{version:04d}"
    dedup_id = f"planned-spec-dedup-index-{version:04d}"
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                history_id,
                ArtifactType.REPORT,
                history,
                "json",
                _metadata("gap_attempt_history_context"),
            ),
            ArtifactWriteSpec(
                dedup_id,
                ArtifactType.REPORT,
                dedup,
                "json",
                _metadata("planned_spec_dedup_index_context"),
            ),
        ],
        action_type=ControllerActionType.GAP_ATTEMPT_HISTORY_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "gap_count": history.gap_count,
            "attempt_count": history.attempt_count,
            "exhausted_gap_count": history.exhausted_gap_count,
            "duplicate_spec_count": dedup.duplicate_spec_count,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return GapAttemptPersistenceResult(
        history=history,
        dedup_index=dedup,
        persistence=persistence,
        history_artifact=by_id[history_id],
        dedup_index_artifact=by_id[dedup_id],
    )


def find_existing_planned_spec(
    *,
    run_id: str,
    root: str | Path,
    spec: PlannedExperimentSpec | ProofObligationSpec | RetrievalExpansionRequest,
) -> tuple[str, str] | None:
    """Return existing relative spec path and fingerprint for an equivalent spec."""
    root_path = Path(root)
    fingerprint = planned_spec_fingerprint(spec)
    for existing, path in _iter_specs(root_path, run_id):
        if planned_spec_fingerprint(existing) == fingerprint:
            return path.relative_to(root_path).as_posix(), fingerprint
    return None


def inspect_gap_attempt_history(*, run_id: str, root: str | Path = ".") -> dict[str, Any]:
    """Inspect the latest gap-attempt history without mutation."""
    root_path = Path(root)
    history_path = latest_gap_attempt_history_path(root_path, run_id)
    if history_path is None:
        raise GapAttemptHistoryError(f"No gap-attempt history found for run_id={run_id}.")
    history = GapAttemptHistory.model_validate_json(history_path.read_text(encoding="utf-8"))
    return {
        **history.model_dump(mode="json"),
        **gap_attempt_summary_fields(history),
        "gap_attempt_history_path": history_path.relative_to(root_path).as_posix(),
    }


def inspect_planned_spec_dedup(*, run_id: str, root: str | Path = ".") -> dict[str, Any]:
    """Inspect the latest planned-spec de-dup index without mutation."""
    root_path = Path(root)
    index_path = latest_planned_spec_dedup_index_path(root_path, run_id)
    if index_path is None:
        raise GapAttemptHistoryError(f"No planned-spec de-dup index found for run_id={run_id}.")
    index = PlannedSpecDedupIndex.model_validate_json(index_path.read_text(encoding="utf-8"))
    return {
        **index.model_dump(mode="json"),
        **planned_spec_dedup_summary_fields(index),
        "planned_spec_dedup_index_path": index_path.relative_to(root_path).as_posix(),
    }


def gap_attempt_summary_fields(history: GapAttemptHistory | None) -> dict[str, Any]:
    """Return stable inspect/lint fields for gap-attempt history."""
    if history is None:
        return {
            "gap_attempt_history_present": False,
            "gap_attempt_count": 0,
            "gap_exhausted_no_progress_count": 0,
            "remaining_deferred_gap_count": 0,
        }
    return {
        "gap_attempt_history_present": True,
        "gap_attempt_count": history.attempt_count,
        "gap_exhausted_no_progress_count": history.exhausted_gap_count,
        "remaining_deferred_gap_count": history.deferred_gap_count,
    }


def planned_spec_dedup_summary_fields(index: PlannedSpecDedupIndex | None) -> dict[str, Any]:
    """Return stable inspect/lint fields for planned-spec de-duplication."""
    if index is None:
        return {
            "planned_spec_dedup_index_present": False,
            "duplicate_planned_spec_count": 0,
        }
    return {
        "planned_spec_dedup_index_present": True,
        "duplicate_planned_spec_count": index.duplicate_spec_count,
    }


def _claim_hash_from_inputs(required_inputs: list[str]) -> str | None:
    for item in required_inputs:
        if item.startswith("claim_text_hash="):
            return item.split("=", 1)[1]
    return None


def _reports(root: Path, run_id: str) -> Path:
    return root / "runs" / run_id / "reports"


def _current_open_gap_fingerprints(
    *,
    run_id: str,
    root: Path,
) -> dict[str, AutonomousEvidenceGapPlanItem]:
    plan = _load_latest_plan(root, run_id)
    if plan is None:
        return {}
    result: dict[str, AutonomousEvidenceGapPlanItem] = {}
    for item in plan.plan_items:
        if item.gap_type == "sufficiently_supported_for_bounded_draft":
            continue
        gap_fp = item.gap_fingerprint or gap_fingerprint_for_plan_item(run_id=run_id, item=item)
        result[gap_fp] = item
    return result


def _load_latest_plan(root: Path, run_id: str) -> AutonomousEvidenceGapPlan | None:
    reports = _reports(root, run_id)
    paths = [
        path
        for path in reports.glob("autonomous-evidence-gap-plan*.json")
        if not path.name.endswith(".meta.json")
    ]
    for path in sorted(paths, key=_plan_sort_key, reverse=True):
        try:
            return AutonomousEvidenceGapPlan.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
    return None


def _plan_sort_key(path: Path) -> tuple[int, str]:
    match = re.search(r"-(\d+)\.json$", path.name)
    return (int(match.group(1)) if match else 0, path.name)


def _load_autonomous_execution_reports(
    root: Path,
    run_id: str,
) -> list[AutonomousPlanExecutionReport]:
    reports = _reports(root, run_id)
    result: list[AutonomousPlanExecutionReport] = []
    for path in sorted(reports.glob("autonomous-plan-execution-*.json")):
        if path.name.endswith(".meta.json") or path.name.startswith(
            "autonomous-plan-execution-index-"
        ):
            continue
        try:
            report = AutonomousPlanExecutionReport.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            continue
        if report.run_id == run_id:
            result.append(report)
    return result


def _load_planned_spec_execution_reports(
    root: Path,
    run_id: str,
) -> list[PlannedSpecExecutionReport]:
    reports = _reports(root, run_id)
    result: list[PlannedSpecExecutionReport] = []
    for path in sorted(reports.glob("planned-spec-execution-*.json")):
        if path.name.endswith(".meta.json") or path.name.startswith(
            "planned-spec-execution-index-"
        ):
            continue
        try:
            report = PlannedSpecExecutionReport.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError):
            continue
        if report.run_id == run_id:
            result.append(report)
    return result


def _iter_specs(
    root: Path,
    run_id: str,
) -> list[tuple[PlannedExperimentSpec | ProofObligationSpec | RetrievalExpansionRequest, Path]]:
    reports = _reports(root, run_id)
    specs: list[
        tuple[PlannedExperimentSpec | ProofObligationSpec | RetrievalExpansionRequest, Path]
    ] = []
    for _spec_type, pattern, model in _SPEC_PATTERNS:
        for path in sorted(reports.glob(pattern)):
            if path.name.endswith(".meta.json"):
                continue
            try:
                spec = model.model_validate_json(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if spec.run_id == run_id:
                specs.append((spec, path))
    return specs


def _ensure_record(
    records: dict[str, _MutableGapAttemptRecord],
    gap_fp: str,
    source: (
        AutonomousEvidenceGapPlanItem
        | AutonomousPlanExecutionAction
        | PlannedSpecExecutionItem
    ),
) -> _MutableGapAttemptRecord:
    if gap_fp in records:
        return records[gap_fp]
    records[gap_fp] = _MutableGapAttemptRecord(
        gap_fingerprint=gap_fp,
        target_claim_id_optional=getattr(source, "target_claim_id_optional", None),
        target_section_optional=getattr(source, "target_section_optional", None),
        gap_type=getattr(source, "gap_type", "planned_spec_execution"),
        recommended_action=getattr(source, "recommended_action", "Execute planned spec."),
        expected_artifact_type=_expected_artifact_type(source),
    )
    return records[gap_fp]


def _expected_artifact_type(
    source: (
        AutonomousEvidenceGapPlanItem
        | AutonomousPlanExecutionAction
        | PlannedSpecExecutionItem
    ),
) -> str:
    if hasattr(source, "expected_artifact_type"):
        return str(source.expected_artifact_type)
    spec_type = getattr(source, "spec_type", "")
    if spec_type == "experiment_spec":
        return "experiment_artifact"
    if spec_type == "proof_obligation_spec":
        return "proof_artifact"
    if spec_type == "retrieval_expansion_request":
        return "retrieval_quality_report"
    return "workflow_artifact"


def _record_autonomous_action(
    record: _MutableGapAttemptRecord,
    action: AutonomousPlanExecutionAction,
) -> None:
    record.attempt_count += 1
    record.latest_attempt_status = action.execution_status
    if action.execution_status == "completed" and action.applied:
        if action.gap_type in {"needs_claim_downgrade", "needs_claim_removal"}:
            record.successful_attempt_count += 1
            record.resolution_reason_optional = "Claim was removed or downgraded safely."
        elif action.planned_spec_fingerprint_optional and action.execution_status != "skipped":
            record.successful_attempt_count += 1
        else:
            record.no_op_attempt_count += 1
    elif action.execution_status in {"deferred", "rejected"}:
        record.deferred_attempt_count += 1
    elif action.execution_status == "failed":
        record.failed_attempt_count += 1
    else:
        record.no_op_attempt_count += 1
    if action.planned_spec_fingerprint_optional:
        record.created_spec_fingerprints = sorted(
            set([*record.created_spec_fingerprints, action.planned_spec_fingerprint_optional])
        )
    if action.created_artifact_path_optional:
        record.created_artifact_paths = sorted(
            set([*record.created_artifact_paths, action.created_artifact_path_optional])
        )


def _record_planned_spec_item(
    root: Path,
    record: _MutableGapAttemptRecord,
    item: PlannedSpecExecutionItem,
) -> None:
    record.attempt_count += 1
    record.latest_attempt_status = item.execution_status
    if item.execution_status == "executed":
        if item.spec_type == "experiment_spec" and item.ingested_artifact_path_optional:
            record.successful_attempt_count += 1
            record.linked_evidence_artifact_ids = sorted(
                set([*record.linked_evidence_artifact_ids, item.ingested_artifact_path_optional])
            )
            record.resolution_reason_optional = "Completed scoped experiment artifact was ingested."
        elif item.spec_type == "proof_obligation_spec" and _proof_item_is_formal(root, item):
            record.successful_attempt_count += 1
            record.linked_evidence_artifact_ids = sorted(
                set(
                    [
                        *record.linked_evidence_artifact_ids,
                        item.ingested_artifact_path_optional or "",
                    ]
                )
                - {""}
            )
            record.resolution_reason_optional = "Passed formal proof artifact was ingested."
        else:
            record.no_op_attempt_count += 1
    elif item.execution_status in {"deferred", "rejected"}:
        record.deferred_attempt_count += 1
    elif item.execution_status == "failed":
        record.failed_attempt_count += 1
    else:
        record.no_op_attempt_count += 1
    if item.planned_spec_fingerprint:
        record.created_spec_fingerprints = sorted(
            set([*record.created_spec_fingerprints, item.planned_spec_fingerprint])
        )
    for path in (item.created_artifact_path_optional, item.ingested_artifact_path_optional):
        if path:
            record.created_artifact_paths = sorted(set([*record.created_artifact_paths, path]))


def _proof_item_is_formal(root: Path, item: PlannedSpecExecutionItem) -> bool:
    if not item.ingested_artifact_path_optional:
        return False
    path = root / item.ingested_artifact_path_optional
    try:
        proof = ProofArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return bool(proof.is_verification_evidence and proof.checker_status == "passed")


def _current_status(
    *,
    record: _MutableGapAttemptRecord,
    in_current_plan: bool,
    max_attempts: int,
) -> str:
    if record.linked_evidence_artifact_ids:
        return "resolved"
    if record.resolution_reason_optional and not in_current_plan:
        return "resolved"
    if not in_current_plan and record.successful_attempt_count:
        return "resolved"
    non_progress_attempts = (
        record.deferred_attempt_count
        + record.failed_attempt_count
        + record.no_op_attempt_count
    )
    if in_current_plan and non_progress_attempts >= max_attempts:
        return "exhausted_no_progress"
    if (
        in_current_plan
        and record.attempt_count >= max_attempts
        and record.successful_attempt_count == 0
    ):
        return "exhausted_no_progress"
    if record.latest_attempt_status in {"deferred", "rejected"}:
        return "deferred"
    return "open" if in_current_plan else "resolved"


def _gap_fingerprint_from_action(run_id: str, action: AutonomousPlanExecutionAction) -> str:
    item = AutonomousEvidenceGapPlanItem(
        item_id=action.plan_item_id,
        target_type="claim" if action.target_claim_id_optional else "bundle",
        target_claim_id_optional=action.target_claim_id_optional,
        target_section_optional=action.target_section_optional,
        current_support_status="unknown",
        gap_type=action.gap_type,
        recommended_action=action.recommended_action,
        rationale="Stable fingerprint projection for an autonomous execution action.",
        expected_artifact_type=_expected_artifact_type(action),
        automation_ready=True,
    )
    return gap_fingerprint_for_plan_item(run_id=run_id, item=item)


def _gap_fingerprint_for_spec(
    spec: PlannedExperimentSpec | ProofObligationSpec | RetrievalExpansionRequest,
) -> str:
    if isinstance(spec, PlannedExperimentSpec):
        item = AutonomousEvidenceGapPlanItem(
            item_id=spec.spec_id,
            target_type="claim",
            target_claim_id_optional=spec.target_claim_id,
            target_section_optional=spec.target_section,
            current_support_status="unknown",
            gap_type="needs_python_experiment",
            recommended_action="Execute planned experiment spec.",
            rationale="Stable fingerprint projection for a planned experiment spec.",
            expected_artifact_type="experiment_artifact",
            automation_ready=True,
        )
    elif isinstance(spec, ProofObligationSpec):
        item = AutonomousEvidenceGapPlanItem(
            item_id=spec.spec_id,
            target_type="claim",
            target_claim_id_optional=spec.target_claim_id,
            current_support_status="unknown",
            gap_type="needs_formal_proof",
            recommended_action="Execute planned proof obligation spec.",
            rationale="Stable fingerprint projection for a planned proof obligation spec.",
            expected_artifact_type="proof_artifact",
            automation_ready=True,
        )
    else:
        item = AutonomousEvidenceGapPlanItem(
            item_id=spec.request_id,
            target_type="retrieval",
            target_claim_id_optional=spec.target_claim_id_optional,
            target_section_optional=spec.target_section_optional,
            current_support_status="unknown",
            gap_type="needs_retrieval_expansion",
            recommended_action="Execute planned retrieval expansion request.",
            rationale="Stable fingerprint projection for a retrieval expansion request.",
            expected_artifact_type="retrieval_quality_report",
            automation_ready=True,
        )
    return gap_fingerprint_for_plan_item(run_id=spec.run_id, item=item)


def _gap_fingerprint_from_spec_item(
    root: Path,
    run_id: str,
    item: PlannedSpecExecutionItem,
) -> str | None:
    for spec, _path in _iter_specs(root, run_id):
        spec_id = (
            spec.spec_id
            if isinstance(spec, PlannedExperimentSpec | ProofObligationSpec)
            else spec.request_id
        )
        if spec_id == item.spec_id:
            plan_gap = _matching_plan_gap_fingerprint(root, run_id, spec)
            if plan_gap is not None:
                return plan_gap
            return _gap_fingerprint_for_spec(spec)
    return None


def _matching_plan_gap_fingerprint(
    root: Path,
    run_id: str,
    spec: PlannedExperimentSpec | ProofObligationSpec | RetrievalExpansionRequest,
) -> str | None:
    plan = _load_latest_plan(root, run_id)
    if plan is None:
        return None
    if isinstance(spec, PlannedExperimentSpec):
        gap_type = "needs_python_experiment"
        target_claim_id = spec.target_claim_id
        target_section = spec.target_section
    elif isinstance(spec, ProofObligationSpec):
        gap_type = "needs_formal_proof"
        target_claim_id = spec.target_claim_id
        target_section = None
    else:
        gap_type = "needs_retrieval_expansion"
        target_claim_id = spec.target_claim_id_optional
        target_section = spec.target_section_optional
    for plan_item in plan.plan_items:
        if plan_item.gap_type != gap_type:
            continue
        if plan_item.target_claim_id_optional != target_claim_id:
            continue
        if target_section and plan_item.target_section_optional != target_section:
            continue
        return plan_item.gap_fingerprint or gap_fingerprint_for_plan_item(
            run_id=run_id,
            item=plan_item,
        )
    return None


def _next_history_version(root: Path, run_id: str) -> int:
    reports = _reports(root, run_id)
    versions = []
    for path in reports.glob("gap-attempt-history-*.json"):
        if path.name.endswith(".meta.json"):
            continue
        match = re.fullmatch(r"gap-attempt-history-(\d+)\.json", path.name)
        if match:
            versions.append(int(match.group(1)))
    return max(versions, default=0) + 1


def _record_from_mutable(record: _MutableGapAttemptRecord) -> GapAttemptRecord:
    return GapAttemptRecord(
        gap_fingerprint=record.gap_fingerprint,
        target_claim_id_optional=record.target_claim_id_optional,
        target_section_optional=record.target_section_optional,
        gap_type=record.gap_type,
        recommended_action=record.recommended_action,
        expected_artifact_type=record.expected_artifact_type,
        first_seen_iteration_optional=record.first_seen_iteration_optional,
        latest_seen_iteration_optional=record.latest_seen_iteration_optional,
        attempt_count=record.attempt_count,
        successful_attempt_count=record.successful_attempt_count,
        deferred_attempt_count=record.deferred_attempt_count,
        failed_attempt_count=record.failed_attempt_count,
        no_op_attempt_count=record.no_op_attempt_count,
        latest_attempt_status=record.latest_attempt_status,
        current_gap_status=record.current_gap_status,
        created_spec_fingerprints=record.created_spec_fingerprints,
        created_artifact_paths=record.created_artifact_paths,
        linked_evidence_artifact_ids=record.linked_evidence_artifact_ids,
        resolution_reason_optional=record.resolution_reason_optional,
        exhaustion_reason_optional=record.exhaustion_reason_optional,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _metadata(role: str) -> dict[str, Any]:
    return {
        "stage": "gap_attempt_history",
        "artifact_role": role,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
        "publication_ready": False,
    }


__all__ = [
    "GapAttemptHistoryError",
    "GapAttemptPersistenceResult",
    "annotate_plan_items_with_history",
    "build_gap_attempt_history",
    "build_planned_spec_dedup_index",
    "execution_attempt_fingerprint",
    "find_existing_planned_spec",
    "gap_attempt_summary_fields",
    "gap_fingerprint_for_plan_item",
    "inspect_gap_attempt_history",
    "inspect_planned_spec_dedup",
    "latest_gap_attempt_history_path",
    "latest_planned_spec_dedup_index_path",
    "load_latest_gap_attempt_history",
    "persist_gap_attempt_artifacts",
    "plan_item_fingerprint",
    "planned_spec_dedup_summary_fields",
    "planned_spec_fingerprint",
]
