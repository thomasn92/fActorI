"""Gated deterministic execution of planned proof, experiment, and retrieval specs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.autonomous_evidence_plan import persist_autonomous_evidence_gap_plan
from factori.citations import build_claim_support_audit, validate_citation_usage
from factori.claim_evidence import latest_claim_evidence_map_path, persist_claim_evidence_map
from factori.gap_attempts import (
    execution_attempt_fingerprint,
    gap_fingerprint_for_plan_item,
    persist_gap_attempt_artifacts,
    planned_spec_fingerprint,
)
from factori.hashing import sha256_json, sha256_text
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.reports import render_full_paper_release_summary
from factori.rerun_policy import validate_ledger_tip
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AutonomousEvidenceGapPlanItem,
    CitationRegistry,
    ControllerActionType,
    ExperimentArtifact,
    FullPaperReleaseGateConfig,
    PlannedExperimentSpec,
    PlannedSpecExecutionIndex,
    PlannedSpecExecutionItem,
    PlannedSpecExecutionReport,
    ProofArtifact,
    ProofObligationSpec,
    RetrievalExpansionRequest,
)

_EXECUTION_MODES = {"dry_run", "apply"}
_EXECUTOR_BACKENDS = {"deterministic_local", "fake", "external_tool"}


class PlannedSpecExecutionError(RuntimeError):
    """Raised when planned-spec execution cannot proceed safely."""


@dataclass(frozen=True)
class PlannedSpecExecutionResult:
    """Persisted planned-spec execution report and index."""

    run_id: str
    report: PlannedSpecExecutionReport
    index: PlannedSpecExecutionIndex
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    report_markdown_artifact: ArtifactRef
    index_artifact: ArtifactRef


@dataclass(frozen=True)
class _SpecRecord:
    spec_id: str
    spec_type: str
    path: Path
    spec: PlannedExperimentSpec | ProofObligationSpec | RetrievalExpansionRequest | None
    planned_spec_fingerprint: str | None = None
    load_error: str | None = None


def execute_planned_specs(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    execution_mode: str = "dry_run",
    spec_executor_backend: str = "deterministic_local",
    max_attempts_per_gap: int = 2,
) -> PlannedSpecExecutionResult:
    """Execute or dry-run deterministic local planned specs."""
    execution_mode = execution_mode.replace("-", "_")
    if execution_mode not in _EXECUTION_MODES:
        raise PlannedSpecExecutionError("execution mode must be dry_run or apply")
    if spec_executor_backend not in _EXECUTOR_BACKENDS:
        raise PlannedSpecExecutionError(
            "spec executor backend must be deterministic_local, fake, or external_tool"
        )
    if spec_executor_backend == "external_tool":
        raise PlannedSpecExecutionError(
            "external planned-spec execution is gated but not implemented in M68."
        )
    if max_attempts_per_gap < 1:
        raise PlannedSpecExecutionError("max attempts per gap must be at least 1")

    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    reports = run_path / "reports"
    if not run_path.is_dir():
        raise PlannedSpecExecutionError(f"No run directory found for run_id={run_id}.")
    ledger_check = validate_ledger_tip(run_id, root=root_path)
    if ledger_check.blocking_findings:
        raise PlannedSpecExecutionError(
            "Ledger validation has blocking findings; planned-spec execution cannot append safely."
        )

    execution_number = _next_execution_number(reports)
    execution_id = f"planned-spec-execution-{execution_number:04d}"
    records = _load_specs(reports, run_id)
    previously_attempted = _previously_attempted_spec_fingerprints(reports, run_id)
    if execution_mode == "dry_run":
        seen: dict[str, str] = {
            record.planned_spec_fingerprint: record.path.relative_to(root_path).as_posix()
            for record in records
            if record.planned_spec_fingerprint in previously_attempted
        }
        items = []
        for index, record in enumerate(records, start=1):
            if record.planned_spec_fingerprint and record.planned_spec_fingerprint in seen:
                items.append(
                    _skipped_duplicate_item(
                        index, record, seen[record.planned_spec_fingerprint]
                    )
                )
                continue
            if record.planned_spec_fingerprint:
                seen[record.planned_spec_fingerprint] = record.path.relative_to(
                    root_path
                ).as_posix()
            items.append(_dry_run_item(index, record, execution_id))
        report = _build_report(
            run_id=run_id,
            execution_id=execution_id,
            execution_mode=execution_mode,
            backend=spec_executor_backend,
            records=records,
            items=items,
            status="dry_run_completed",
            created_paths=[],
            ingested_paths=[],
            claim_map_rebuilt=False,
            plan_rebuilt=False,
            manuscript_refreshed=False,
            release_rechecked=False,
            requires_human=False,
            human_reason=None,
        )
        return _persist_execution_report(
            report=report,
            root=root_path,
            store=store,
            ledger=ledger,
            execution_number=execution_number,
            release_report=None,
        )

    items: list[PlannedSpecExecutionItem] = []
    created_paths: list[str] = []
    ingested_paths: list[str] = []
    seen_specs: dict[str, str] = {
        record.planned_spec_fingerprint: record.path.relative_to(root_path).as_posix()
        for record in records
        if record.planned_spec_fingerprint in previously_attempted
    }
    for index, record in enumerate(records, start=1):
        if record.planned_spec_fingerprint and record.planned_spec_fingerprint in seen_specs:
            items.append(
                _skipped_duplicate_item(
                    index, record, seen_specs[record.planned_spec_fingerprint]
                )
            )
            continue
        if record.planned_spec_fingerprint:
            seen_specs[record.planned_spec_fingerprint] = record.path.relative_to(
                root_path
            ).as_posix()
        if record.load_error is not None or record.spec is None:
            items.append(
                _failed_item(
                    index,
                    record,
                    f"Spec could not be loaded safely: {record.load_error or 'unknown error'}",
                )
            )
            continue
        if record.spec_type == "experiment_spec":
            item, created, ingested = _execute_experiment_spec(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                execution_id=execution_id,
                index=index,
                spec=record.spec,
            )
        elif record.spec_type == "proof_obligation_spec":
            item, created, ingested = _execute_proof_spec(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                execution_id=execution_id,
                index=index,
                spec=record.spec,
            )
        elif record.spec_type == "retrieval_expansion_request":
            item, created, ingested = _execute_retrieval_spec(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                execution_id=execution_id,
                index=index,
                spec=record.spec,
            )
        else:
            item, created, ingested = (
                _failed_item(index, record, "Unknown planned spec type."),
                [],
                [],
            )
        items.append(item)
        created_paths.extend(created)
        ingested_paths.extend(ingested)

    claim_support_path = _write_post_apply_claim_support(
        run_id=run_id,
        root=root_path,
        store=store,
        ledger=ledger,
        execution_id=execution_id,
    )
    created_paths.append(claim_support_path)
    map_result = persist_claim_evidence_map(
        run_id=run_id,
        root=root_path,
        store=store,
        ledger=ledger,
    )
    created_paths.append(map_result.map_artifact.path)
    plan_result = persist_autonomous_evidence_gap_plan(
        run_id=run_id,
        root=root_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
        max_attempts_per_gap=max_attempts_per_gap,
    )
    created_paths.append(plan_result.plan_artifact.path)

    manuscript_refreshed = _maybe_refresh_manuscript_after_new_evidence(
        run_id=run_id,
        root=root_path,
        store=store,
        ledger=ledger,
        created_paths=created_paths,
    )

    from factori.full_paper_release import evaluate_full_paper_release  # noqa: PLC0415

    release_report = evaluate_full_paper_release(
        run_id=run_id,
        root=root_path,
        ledger=ledger,
        config=FullPaperReleaseGateConfig(run_id=run_id, write_report=False),
    )
    status = (
        "completed_with_deferred_specs"
        if any(
            item.execution_status in {"deferred", "rejected", "failed"}
            for item in items
        )
        else "completed"
    )
    requires_human = any(item.execution_status == "failed" for item in items)
    report = _build_report(
        run_id=run_id,
        execution_id=execution_id,
        execution_mode=execution_mode,
        backend=spec_executor_backend,
        records=records,
        items=items,
        status=status,
        created_paths=sorted(set(created_paths)),
        ingested_paths=sorted(set(ingested_paths)),
        claim_map_rebuilt=True,
        plan_rebuilt=True,
        manuscript_refreshed=manuscript_refreshed,
        release_rechecked=True,
        requires_human=requires_human,
        human_reason=(
            "One or more planned specs failed deterministic local execution."
            if requires_human
            else None
        ),
    )
    result = _persist_execution_report(
        report=report,
        root=root_path,
        store=store,
        ledger=ledger,
        execution_number=execution_number,
        release_report=release_report,
    )
    persist_gap_attempt_artifacts(
        run_id=run_id,
        root=root_path,
        store=store,
        ledger=ledger,
        max_attempts_per_gap=max_attempts_per_gap,
    )
    return result


def inspect_planned_spec_execution(*, run_id: str, root: str | Path = ".") -> dict[str, Any]:
    """Inspect the latest planned-spec execution report without mutation."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    index_path = latest_planned_spec_execution_index_path(root_path, run_id)
    if index_path is None:
        raise PlannedSpecExecutionError(
            f"No planned spec execution found for run_id={run_id}."
        )
    index = PlannedSpecExecutionIndex.model_validate_json(index_path.read_text(encoding="utf-8"))
    report_path = _execution_report_path(reports, index.latest_execution_id)
    report = PlannedSpecExecutionReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
    return {
        **report.model_dump(mode="json"),
        **planned_spec_execution_summary_fields(report, index),
        "planned_spec_execution_report_path": report_path.relative_to(root_path).as_posix(),
        "planned_spec_execution_index_path": index_path.relative_to(root_path).as_posix(),
        "planned_spec_execution_index": index.model_dump(mode="json"),
    }


def planned_spec_execution_summary_fields(
    report: PlannedSpecExecutionReport | None,
    index: PlannedSpecExecutionIndex | None = None,
) -> dict[str, Any]:
    """Return stable inspect/lint fields for planned-spec execution."""
    if report is None:
        return {
            "planned_spec_execution_present": False,
            "planned_spec_execution_count": 0,
            "latest_planned_spec_execution_mode": None,
            "latest_planned_spec_execution_status": None,
            "experiment_specs_executed": 0,
            "proof_specs_executed": 0,
            "retrieval_specs_executed": 0,
            "experiment_artifacts_created": 0,
            "proof_artifacts_created": 0,
            "retrieval_artifacts_created": 0,
            "planned_spec_duplicate_specs_skipped": 0,
            "planned_spec_unique_specs_executed": 0,
            "planned_spec_execution_requires_human_intervention": False,
        }
    return {
        "planned_spec_execution_present": True,
        "planned_spec_execution_count": index.execution_count if index else 1,
        "latest_planned_spec_execution_mode": report.execution_mode,
        "latest_planned_spec_execution_status": report.execution_status,
        "experiment_specs_executed": report.experiment_specs_executed,
        "proof_specs_executed": report.proof_specs_executed,
        "retrieval_specs_executed": report.retrieval_specs_executed,
        "experiment_artifacts_created": report.experiment_artifacts_created,
        "proof_artifacts_created": report.proof_artifacts_created,
        "retrieval_artifacts_created": report.retrieval_artifacts_created,
        "planned_spec_duplicate_specs_skipped": report.duplicate_specs_skipped,
        "planned_spec_unique_specs_executed": report.unique_specs_executed,
        "planned_spec_execution_requires_human_intervention": (
            report.requires_human_intervention
        ),
    }


def latest_planned_spec_execution_index_path(root: Path, run_id: str) -> Path | None:
    """Return the latest immutable planned-spec execution index path."""
    reports = root / "runs" / run_id / "reports"
    paths = [
        path
        for path in reports.glob("planned-spec-execution-index-*.json")
        if not path.name.endswith(".meta.json")
    ]
    return sorted(paths)[-1] if paths else None


def latest_planned_spec_execution_report(
    root: Path,
    run_id: str,
) -> tuple[PlannedSpecExecutionReport | None, PlannedSpecExecutionIndex | None]:
    """Load the latest planned-spec execution report and index."""
    index_path = latest_planned_spec_execution_index_path(root, run_id)
    if index_path is None:
        return None, None
    try:
        index = PlannedSpecExecutionIndex.model_validate_json(
            index_path.read_text(encoding="utf-8")
        )
        report_path = _execution_report_path(
            root / "runs" / run_id / "reports",
            index.latest_execution_id,
        )
        report = PlannedSpecExecutionReport.model_validate_json(
            report_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None, None
    return report, index


def render_planned_spec_execution_markdown(report: PlannedSpecExecutionReport) -> str:
    """Render a concise planned-spec execution report."""
    lines = [
        "# Planned Spec Execution",
        "",
        f"Run ID: `{report.run_id}`",
        f"Execution ID: `{report.execution_id}`",
        f"Mode: `{report.execution_mode}`",
        f"Backend: `{report.spec_executor_backend}`",
        f"Status: `{report.execution_status}`",
        f"Specs found: `{report.spec_count}`",
        f"Specs executed/deferred/rejected/failed: `{report.specs_executed}/"
        f"{report.specs_deferred}/{report.specs_rejected}/{report.specs_failed}`",
        f"Duplicate specs skipped: `{report.duplicate_specs_skipped}`",
        f"Experiment artifacts created: `{report.experiment_artifacts_created}`",
        f"Proof artifacts created: `{report.proof_artifacts_created}`",
        f"Retrieval artifacts created: `{report.retrieval_artifacts_created}`",
        "",
        "## Items",
    ]
    for item in report.items:
        lines.append(
            f"- `{item.item_id}` / `{item.spec_id}`: "
            f"`{item.executor_decision}` -> `{item.execution_status}`"
        )
    lines.extend(
        [
            "",
            "## Non-Evidence Boundary",
            "- planned specs are not evidence",
            "- synthetic local experiments support only mapped bounded result claims",
            "- proof plans are not formal verification",
            "- publication_ready: false",
            "- creates_scientific_validation: false",
            "- implies_publication_readiness: false",
            "- is_verification_evidence: false",
            "",
        ]
    )
    return "\n".join(lines)


def _execute_experiment_spec(
    *,
    run_id: str,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    execution_id: str,
    index: int,
    spec: PlannedExperimentSpec,
) -> tuple[PlannedSpecExecutionItem, list[str], list[str]]:
    from factori.evidence_artifact_intake import (  # noqa: PLC0415
        EvidenceArtifactIntakeError,
        ingest_experiment_artifact,
    )

    if spec.run_id != run_id:
        return _rejected_loaded_item(
            index,
            spec.spec_id,
            "experiment_spec",
            None,
            "run_id mismatch",
        )
    if _forces_failed_experiment(spec):
        return (
            PlannedSpecExecutionItem(
                item_id=f"item-{index:04d}",
                spec_id=spec.spec_id,
                spec_type="experiment_spec",
                target_claim_id_optional=spec.target_claim_id,
                gap_fingerprint=_gap_fingerprint_for_spec(spec),
                planned_spec_fingerprint=planned_spec_fingerprint(spec),
                execution_attempt_fingerprint=execution_attempt_fingerprint(
                    run_id=run_id,
                    execution_id=execution_id,
                    target_id=spec.spec_id,
                    gap_fingerprint=_gap_fingerprint_for_spec(spec),
                    planned_spec_fingerprint_value=planned_spec_fingerprint(spec),
                    decision="reject",
                    status="rejected",
                ),
                executor_decision="reject",
                execution_status="rejected",
                rejected_reason_optional=(
                    "Deterministic local template refused a forced-failure experiment spec."
                ),
                safety_notes=_standard_safety_notes(),
            ),
            [],
            [],
        )
    suffix = _safe_suffix(f"{spec.spec_id}-{execution_id}")
    config = {
        "run_id": run_id,
        "spec_id": spec.spec_id,
        "target_claim_id": spec.target_claim_id,
        "template": "synthetic_calibration_demo",
        "seed": 1729,
        "dataset": spec.suggested_dataset,
        "metrics": spec.suggested_metrics,
    }
    metrics = _synthetic_experiment_metrics(config)
    config_hash = sha256_json(config)
    dataset_hash = sha256_json(
        {
            "synthetic_template": "calibration_grid_v1",
            "seed": config["seed"],
            "target_claim_id": spec.target_claim_id,
        }
    )
    metrics_id = f"local-synthetic-experiment-metrics-{suffix}"
    log_id = f"local-synthetic-experiment-log-{suffix}"
    adapter_output_id = f"local-synthetic-experiment-artifact-candidate-{suffix}"
    log = (
        "deterministic local synthetic experiment template executed\n"
        "no arbitrary Python, shell command, network, Docker, or external API was invoked\n"
    )
    metrics_payload = {
        "run_id": run_id,
        "spec_id": spec.spec_id,
        "config_hash": config_hash,
        "dataset_hash": dataset_hash,
        "metrics": metrics,
        "status": "completed",
        "publication_ready": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
    }
    context_persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                metrics_id,
                ArtifactType.EXPERIMENT,
                metrics_payload,
                "json",
                _metadata("local_synthetic_experiment_metrics_context"),
            ),
            ArtifactWriteSpec(
                log_id,
                ArtifactType.LOG,
                log,
                "text",
                _metadata("local_synthetic_experiment_log_context"),
            ),
        ],
        action_type=ControllerActionType.PLANNED_SPEC_EXECUTION_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "execution_id": execution_id,
            "spec_id": spec.spec_id,
            "spec_type": "experiment_spec",
            "adapter_phase": "local_synthetic_template",
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    created = [artifact.path for artifact in context_persistence.artifacts]
    experiment = ExperimentArtifact(
        run_id=run_id,
        experiment_id=f"local-synthetic-experiment-{suffix}",
        experiment_type="synthetic_local_calibration_demo",
        claim_ids_or_section_ids=[
            spec.target_claim_id,
            _slug(spec.target_section),
        ],
        hypothesis_or_question=spec.hypothesis_or_question,
        status="completed",
        dataset_name_optional="deterministic synthetic calibration fixture",
        dataset_hash_optional=dataset_hash,
        config_hash=config_hash,
        code_commit_hash_optional=None,
        command_optional="built_in_deterministic_template:no_shell",
        metrics=metrics,
        result_summary=(
            "This is a synthetic/local experiment artifact. It supports only the "
            "bounded mapped result claim for this run and does not imply broad "
            "empirical validation, novelty, correctness validation, or publication readiness."
        ),
        artifact_paths=created,
        limitations=[
            "Synthetic/local experiment only.",
            "No network, external API, arbitrary Python, Docker, or shell command was executed.",
            "The artifact supports only declared claim IDs or sections within the run.",
            "It does not imply broad empirical validation or publication readiness.",
        ],
        created_at="1970-01-01T00:00:00Z",
        ingested_at="1970-01-01T00:00:00Z",
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )
    adapter_output = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                adapter_output_id,
                ArtifactType.REPORT,
                experiment,
                "json",
                _metadata("local_synthetic_experiment_artifact_candidate_context"),
            )
        ],
        action_type=ControllerActionType.PLANNED_SPEC_EXECUTION_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "execution_id": execution_id,
            "spec_id": spec.spec_id,
            "spec_type": "experiment_spec",
            "adapter_phase": "experiment_artifact_candidate",
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    created.extend(artifact.path for artifact in adapter_output.artifacts)
    try:
        ingest = ingest_experiment_artifact(
            run_id=run_id,
            root=root,
            store=store,
            ledger=ledger,
            experiment_file=root / adapter_output.artifacts[0].path,
        )
    except EvidenceArtifactIntakeError as exc:
        return (
            PlannedSpecExecutionItem(
                item_id=f"item-{index:04d}",
                spec_id=spec.spec_id,
                spec_type="experiment_spec",
                target_claim_id_optional=spec.target_claim_id,
                gap_fingerprint=_gap_fingerprint_for_spec(spec),
                planned_spec_fingerprint=planned_spec_fingerprint(spec),
                execution_attempt_fingerprint=execution_attempt_fingerprint(
                    run_id=run_id,
                    execution_id=execution_id,
                    target_id=spec.spec_id,
                    gap_fingerprint=_gap_fingerprint_for_spec(spec),
                    planned_spec_fingerprint_value=planned_spec_fingerprint(spec),
                    decision="execute",
                    status="failed",
                ),
                executor_decision="execute",
                execution_status="failed",
                failed_reason_optional=f"Experiment artifact intake rejected output: {exc}",
                created_artifact_path_optional=adapter_output.artifacts[0].path,
                safety_notes=_standard_safety_notes(),
            ),
            created,
            [],
        )
    ingested = [ingest.experiment_artifact.path, ingest.experiment_index_artifact.path]
    return (
        PlannedSpecExecutionItem(
            item_id=f"item-{index:04d}",
            spec_id=spec.spec_id,
            spec_type="experiment_spec",
            target_claim_id_optional=spec.target_claim_id,
            gap_fingerprint=_gap_fingerprint_for_spec(spec),
            planned_spec_fingerprint=planned_spec_fingerprint(spec),
            execution_attempt_fingerprint=execution_attempt_fingerprint(
                run_id=run_id,
                execution_id=execution_id,
                target_id=spec.spec_id,
                gap_fingerprint=_gap_fingerprint_for_spec(spec),
                planned_spec_fingerprint_value=planned_spec_fingerprint(spec),
                decision="execute",
                status="executed",
            ),
            executor_decision="execute",
            execution_status="executed",
            created_artifact_path_optional=adapter_output.artifacts[0].path,
            ingested_artifact_path_optional=ingest.experiment_artifact.path,
            safety_notes=[
                *_standard_safety_notes(),
                "The built-in synthetic experiment template produced a completed bounded artifact.",
            ],
        ),
        created,
        ingested,
    )


def _execute_proof_spec(
    *,
    run_id: str,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    execution_id: str,
    index: int,
    spec: ProofObligationSpec,
) -> tuple[PlannedSpecExecutionItem, list[str], list[str]]:
    from factori.evidence_artifact_intake import (  # noqa: PLC0415
        EvidenceArtifactIntakeError,
        ingest_proof_artifact,
    )

    if spec.run_id != run_id:
        return _rejected_loaded_item(
            index,
            spec.spec_id,
            "proof_obligation_spec",
            None,
            "run_id mismatch",
        )
    suffix = _safe_suffix(f"{spec.spec_id}-{execution_id}")
    proof_type = "proof_plan"
    checker_status = "not_checked"
    checker_name = "deterministic-local-proof-plan"
    review_status = "planned_not_verified"
    limitations = [
        "Deterministic local proof adapter produced a proof plan only.",
        "No Lean or external proof checker was invoked.",
        "This artifact is not formal verification evidence.",
        "It does not imply novelty, broad correctness validation, or publication readiness.",
    ]
    if _fixture_formal_proof_allowed(spec):
        proof_type = "formal_verified"
        checker_status = "passed"
        checker_name = "deterministic-fixture-proof-checker"
        review_status = "fixture_checker_passed"
        limitations = [
            "Deterministic fixture-backed formal proof artifact for the declared statement only.",
            "The fixture supports only the mapped claim ID or statement hash.",
            "It does not establish novelty, broad correctness, or publication readiness.",
        ]
    note_id = f"local-proof-adapter-note-{suffix}"
    note_text = (
        f"Proof obligation for `{spec.target_claim_id}`.\n\n"
        f"Statement: {spec.statement}\n\n"
        "The deterministic local backend does not invoke Lean or external proof tools by default.\n"
    )
    note_persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                note_id,
                ArtifactType.LEAN,
                note_text,
                "text",
                _metadata("local_proof_adapter_note_context"),
            )
        ],
        action_type=ControllerActionType.PLANNED_SPEC_EXECUTION_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "execution_id": execution_id,
            "spec_id": spec.spec_id,
            "spec_type": "proof_obligation_spec",
            "adapter_phase": "local_proof_contract",
            "formal_fixture": proof_type == "formal_verified",
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    note_path = note_persistence.artifacts[0].path
    proof = ProofArtifact(
        run_id=run_id,
        proof_id=f"local-proof-{suffix}",
        proof_type=proof_type,
        claim_ids_or_statement_ids=[
            spec.target_claim_id,
            sha256_text(spec.statement),
        ],
        statement=spec.statement,
        artifact_path_optional=note_path,
        checker_name_optional=checker_name,
        checker_version_optional="deterministic-local-v1",
        checker_status=checker_status,
        checker_log_hash_optional=sha256_text(note_text),
        proof_hash=sha256_text(spec.statement),
        review_status=review_status,
        limitations=limitations,
        created_at="1970-01-01T00:00:00Z",
        ingested_at="1970-01-01T00:00:00Z",
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=proof_type == "formal_verified",
    )
    adapter_output_id = f"local-proof-artifact-candidate-{suffix}"
    adapter_output = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                adapter_output_id,
                ArtifactType.REPORT,
                proof,
                "json",
                _metadata("local_proof_artifact_candidate_context"),
            )
        ],
        action_type=ControllerActionType.PLANNED_SPEC_EXECUTION_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "execution_id": execution_id,
            "spec_id": spec.spec_id,
            "spec_type": "proof_obligation_spec",
            "adapter_phase": "proof_artifact_candidate",
            "proof_type": proof.proof_type,
            "checker_status": proof.checker_status,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": proof.is_verification_evidence,
        },
    )
    created = [note_path, adapter_output.artifacts[0].path]
    try:
        ingest = ingest_proof_artifact(
            run_id=run_id,
            root=root,
            store=store,
            ledger=ledger,
            proof_file=root / adapter_output.artifacts[0].path,
        )
    except EvidenceArtifactIntakeError as exc:
        return (
            PlannedSpecExecutionItem(
                item_id=f"item-{index:04d}",
                spec_id=spec.spec_id,
                spec_type="proof_obligation_spec",
                target_claim_id_optional=spec.target_claim_id,
                gap_fingerprint=_gap_fingerprint_for_spec(spec),
                planned_spec_fingerprint=planned_spec_fingerprint(spec),
                execution_attempt_fingerprint=execution_attempt_fingerprint(
                    run_id=run_id,
                    execution_id=execution_id,
                    target_id=spec.spec_id,
                    gap_fingerprint=_gap_fingerprint_for_spec(spec),
                    planned_spec_fingerprint_value=planned_spec_fingerprint(spec),
                    decision="execute",
                    status="failed",
                ),
                executor_decision="execute",
                execution_status="failed",
                failed_reason_optional=f"Proof artifact intake rejected output: {exc}",
                created_artifact_path_optional=adapter_output.artifacts[0].path,
                safety_notes=_standard_safety_notes(),
            ),
            created,
            [],
        )
    ingested = [ingest.proof_artifact.path, ingest.proof_index_artifact.path]
    return (
        PlannedSpecExecutionItem(
            item_id=f"item-{index:04d}",
            spec_id=spec.spec_id,
            spec_type="proof_obligation_spec",
            target_claim_id_optional=spec.target_claim_id,
            gap_fingerprint=_gap_fingerprint_for_spec(spec),
            planned_spec_fingerprint=planned_spec_fingerprint(spec),
            execution_attempt_fingerprint=execution_attempt_fingerprint(
                run_id=run_id,
                execution_id=execution_id,
                target_id=spec.spec_id,
                gap_fingerprint=_gap_fingerprint_for_spec(spec),
                planned_spec_fingerprint_value=planned_spec_fingerprint(spec),
                decision="execute",
                status="executed",
            ),
            executor_decision="execute",
            execution_status="executed",
            created_artifact_path_optional=adapter_output.artifacts[0].path,
            ingested_artifact_path_optional=ingest.proof_artifact.path,
            safety_notes=[
                *_standard_safety_notes(),
                (
                    "Fixture-backed formal proof was accepted for declared scope only."
                    if proof.is_verification_evidence
                    else "Proof plan was ingested as non-verification context only."
                ),
            ],
        ),
        created,
        ingested,
    )


def _execute_retrieval_spec(
    *,
    run_id: str,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    execution_id: str,
    index: int,
    spec: RetrievalExpansionRequest,
) -> tuple[PlannedSpecExecutionItem, list[str], list[str]]:
    if spec.run_id != run_id:
        return _rejected_loaded_item(
            index,
            spec.request_id,
            "retrieval_expansion_request",
            spec.target_claim_id_optional,
            "run_id mismatch",
        )
    suffix = _safe_suffix(f"{spec.request_id}-{execution_id}")
    source_pack = (
        root
        / "tests"
        / "fixtures"
        / "retrieval"
        / "openalex_style_human_geography_sources.json"
    )
    source_pack_available = source_pack.is_file()
    result = {
        "run_id": run_id,
        "request_id": spec.request_id,
        "target_claim_id_optional": spec.target_claim_id_optional,
        "target_section_optional": spec.target_section_optional,
        "query_terms": spec.query_terms,
        "status": (
            "completed_fixture_context_only"
            if source_pack_available
            else "completed_no_local_source_pack_match"
        ),
        "source_pack_path_optional": (
            str(source_pack.relative_to(root)) if source_pack_available else None
        ),
        "accepted_sources_added": 0,
        "network_called": False,
        "reason": (
            "M68 local retrieval expansion records a fixture-only request and does not "
            "call OpenAlex or mutate the citation registry."
        ),
        "publication_ready": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
    }
    result_id = f"retrieval-expansion-result-{suffix}"
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                result_id,
                ArtifactType.REPORT,
                result,
                "json",
                _metadata("local_retrieval_expansion_context"),
            )
        ],
        action_type=ControllerActionType.PLANNED_SPEC_EXECUTION_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "execution_id": execution_id,
            "spec_id": spec.request_id,
            "spec_type": "retrieval_expansion_request",
            "adapter_phase": "local_fixture_retrieval_expansion",
            "accepted_sources_added": 0,
            "network_called": False,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    artifact_path = persistence.artifacts[0].path
    return (
        PlannedSpecExecutionItem(
            item_id=f"item-{index:04d}",
            spec_id=spec.request_id,
            spec_type="retrieval_expansion_request",
            target_claim_id_optional=spec.target_claim_id_optional,
            gap_fingerprint=_gap_fingerprint_for_spec(spec),
            planned_spec_fingerprint=planned_spec_fingerprint(spec),
            execution_attempt_fingerprint=execution_attempt_fingerprint(
                run_id=run_id,
                execution_id=execution_id,
                target_id=spec.request_id,
                gap_fingerprint=_gap_fingerprint_for_spec(spec),
                planned_spec_fingerprint_value=planned_spec_fingerprint(spec),
                decision="execute",
                status="executed",
            ),
            executor_decision="execute",
            execution_status="executed",
            created_artifact_path_optional=artifact_path,
            safety_notes=[
                *_standard_safety_notes(),
                "Local retrieval expansion is fixture-only and performed no network call.",
            ],
        ),
        [artifact_path],
        [],
    )


def _write_post_apply_claim_support(
    *,
    run_id: str,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    execution_id: str,
) -> str:
    reports = root / "runs" / run_id / "reports"
    manuscript_path = _preferred_manuscript_path(reports)
    markdown = manuscript_path.read_text(encoding="utf-8")
    registry = _read_registry(reports / "citation-registry.json", run_id)
    evidence = _available_evidence(root, run_id)
    claim_support = build_claim_support_audit(
        run_id=run_id,
        markdown=markdown,
        citation_registry=registry,
        available_evidence_artifacts=evidence,
    )
    citation_safety = validate_citation_usage(markdown, registry)
    suffix = execution_id.rsplit("-", maxsplit=1)[-1]
    audit_id = f"claim-support-audit-after-planned-spec-execution-{suffix}"
    safety_id = f"citation-safety-after-planned-spec-execution-{suffix}"
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                audit_id,
                ArtifactType.REPORT,
                claim_support,
                "json",
                _metadata("claim_support_audit_context"),
            ),
            ArtifactWriteSpec(
                safety_id,
                ArtifactType.REPORT,
                citation_safety,
                "json",
                _metadata("citation_safety_context"),
            ),
        ],
        action_type=ControllerActionType.PLANNED_SPEC_EXECUTION_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "execution_id": execution_id,
            "artifact_phase": "post_planned_spec_claim_support",
            "claim_support_missing_required_citation_count": int(
                claim_support.summary_counts.get("missing_required_citation", 0)
            ),
            "citation_as_validation_misuse_count": int(
                claim_support.summary_counts.get("citation_as_validation_misuse", 0)
            ),
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    return persistence.artifacts[0].path


def _maybe_refresh_manuscript_after_new_evidence(
    *,
    run_id: str,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    created_paths: list[str],
) -> bool:
    reports = root / "runs" / run_id / "reports"
    if (reports / "evidence-aware-refresh-report.json").is_file():
        return False
    claim_map_path = latest_claim_evidence_map_path(root, run_id)
    if claim_map_path is None or not claim_map_path.is_file():
        return False
    from factori.schemas import ClaimEvidenceMap  # noqa: PLC0415

    try:
        claim_map = ClaimEvidenceMap.model_validate_json(
            claim_map_path.read_text(encoding="utf-8")
        )
    except ValueError:
        return False
    if claim_map.unsupported_non_scaffold_claim_ids:
        return False
    if not any(
        link.support_type in {"formal_proof_verification", "experiment_result"}
        for link in claim_map.links
    ):
        return False
    from factori.evidence_aware_refresh import (  # noqa: PLC0415
        EvidenceAwareRefreshError,
        refresh_evidence_aware_manuscript,
    )

    try:
        refreshed = refresh_evidence_aware_manuscript(
            run_id=run_id,
            root=root,
            store=store,
            ledger=ledger,
            backend="deterministic",
        )
    except EvidenceAwareRefreshError:
        return False
    created_paths.extend(
        [
            refreshed.report_artifact.path,
            refreshed.manuscript_artifact.path,
            refreshed.claim_evidence_map_artifact.path,
        ]
    )
    return True


def _persist_execution_report(
    *,
    report: PlannedSpecExecutionReport,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    execution_number: int,
    release_report,
) -> PlannedSpecExecutionResult:
    from factori.full_paper_generation import (  # noqa: PLC0415
        build_reviewer_bundle_summary,
        render_reviewer_bundle_summary_markdown,
    )

    mode_stem = "dry-run-" if report.execution_mode == "dry_run" else ""
    report_id = f"planned-spec-execution-{mode_stem}{execution_number:04d}"
    index_id = f"planned-spec-execution-index-{execution_number:04d}"
    reviewer_id = f"reviewer-bundle-summary-after-planned-spec-execution-{execution_number:04d}"
    release_id = f"full-paper-release-report-after-planned-spec-execution-{execution_number:04d}"
    final_paths = list(report.created_artifact_paths)
    final_paths.extend(
        [
            f"runs/{report.run_id}/reports/{report_id}.json",
            f"runs/{report.run_id}/reports/{report_id}.md",
            f"runs/{report.run_id}/reports/{index_id}.json",
            f"runs/{report.run_id}/reports/{reviewer_id}.json",
            f"runs/{report.run_id}/reports/{reviewer_id}.md",
        ]
    )
    if release_report is not None:
        final_paths.extend(
            [
                f"runs/{report.run_id}/reports/{release_id}.json",
                f"runs/{report.run_id}/reports/{release_id}.md",
            ]
        )
    report = report.model_copy(update={"created_artifact_paths": sorted(set(final_paths))})
    index = PlannedSpecExecutionIndex(
        run_id=report.run_id,
        latest_execution_id=report.execution_id,
        execution_count=execution_number,
        latest_execution_mode=report.execution_mode,
        latest_execution_status=report.execution_status,
        latest_created_artifact_paths=report.created_artifact_paths,
        latest_ingested_artifact_paths=report.ingested_artifact_paths,
        latest_requires_human_intervention=report.requires_human_intervention,
    )
    reviewer = build_reviewer_bundle_summary(
        run_id=report.run_id,
        root=root,
        release_report=release_report,
        planned_spec_execution_report=report,
    )
    specs = [
        ArtifactWriteSpec(
            report_id,
            ArtifactType.REPORT,
            report,
            "json",
            _metadata("planned_spec_execution_context"),
        ),
        ArtifactWriteSpec(
            f"{report_id}-markdown",
            ArtifactType.REPORT,
            render_planned_spec_execution_markdown(report),
            "markdown",
            _metadata("planned_spec_execution_context"),
            filename_stem=report_id,
        ),
        ArtifactWriteSpec(
            index_id,
            ArtifactType.REPORT,
            index,
            "json",
            _metadata("planned_spec_execution_index_context"),
        ),
        ArtifactWriteSpec(
            reviewer_id,
            ArtifactType.REPORT,
            reviewer,
            "json",
            _metadata("reviewer_bundle_summary_context"),
        ),
        ArtifactWriteSpec(
            f"{reviewer_id}-markdown",
            ArtifactType.REPORT,
            render_reviewer_bundle_summary_markdown(reviewer),
            "markdown",
            _metadata("reviewer_bundle_summary_context"),
            filename_stem=reviewer_id,
        ),
    ]
    if release_report is not None:
        specs.extend(
            [
                ArtifactWriteSpec(
                    release_id,
                    ArtifactType.REPORT,
                    release_report,
                    "json",
                    _metadata("full_paper_release_audit_context"),
                ),
                ArtifactWriteSpec(
                    f"{release_id}-markdown",
                    ArtifactType.REPORT,
                    render_full_paper_release_summary(release_report),
                    "markdown",
                    _metadata("full_paper_release_audit_context"),
                    filename_stem=release_id,
                ),
            ]
        )
    persistence = persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.PLANNED_SPEC_EXECUTION_WRITTEN,
        commit_payload={
            "run_id": report.run_id,
            "execution_id": report.execution_id,
            "execution_mode": report.execution_mode,
            "execution_status": report.execution_status,
            "specs_executed": report.specs_executed,
            "specs_deferred": report.specs_deferred,
            "specs_rejected": report.specs_rejected,
            "specs_failed": report.specs_failed,
            "experiment_artifacts_created": report.experiment_artifacts_created,
            "proof_artifacts_created": report.proof_artifacts_created,
            "retrieval_artifacts_created": report.retrieval_artifacts_created,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return PlannedSpecExecutionResult(
        run_id=report.run_id,
        report=report,
        index=index,
        persistence=persistence,
        report_artifact=by_id[report_id],
        report_markdown_artifact=by_id[f"{report_id}-markdown"],
        index_artifact=by_id[index_id],
    )


def _load_specs(reports: Path, run_id: str) -> list[_SpecRecord]:
    records: list[_SpecRecord] = []
    for path in sorted(reports.glob("experiment-spec-*.json")):
        if path.name.endswith(".meta.json"):
            continue
        records.append(_load_one_spec(path, "experiment_spec", run_id))
    for path in sorted(reports.glob("proof-obligation-spec-*.json")):
        if path.name.endswith(".meta.json"):
            continue
        records.append(_load_one_spec(path, "proof_obligation_spec", run_id))
    for path in sorted(reports.glob("retrieval-expansion-request-*.json")):
        if path.name.endswith(".meta.json"):
            continue
        records.append(_load_one_spec(path, "retrieval_expansion_request", run_id))
    return sorted(records, key=lambda item: (item.spec_type, item.spec_id))


def _previously_attempted_spec_fingerprints(reports: Path, run_id: str) -> set[str]:
    fingerprints: set[str] = set()
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
        if report.run_id != run_id or report.execution_mode != "apply":
            continue
        fingerprints.update(
            item.planned_spec_fingerprint
            for item in report.items
            if item.planned_spec_fingerprint and item.execution_status != "planned"
        )
    return fingerprints


def _load_one_spec(path: Path, spec_type: str, run_id: str) -> _SpecRecord:
    try:
        if spec_type == "experiment_spec":
            spec = PlannedExperimentSpec.model_validate_json(path.read_text(encoding="utf-8"))
            spec_id = spec.spec_id
        elif spec_type == "proof_obligation_spec":
            spec = ProofObligationSpec.model_validate_json(path.read_text(encoding="utf-8"))
            spec_id = spec.spec_id
        else:
            spec = RetrievalExpansionRequest.model_validate_json(path.read_text(encoding="utf-8"))
            spec_id = spec.request_id
    except (OSError, ValueError) as exc:
        return _SpecRecord(
            spec_id=path.stem,
            spec_type=spec_type,
            path=path,
            spec=None,
            load_error=str(exc),
        )
    if spec.run_id != run_id:
        return _SpecRecord(
            spec_id=spec_id,
            spec_type=spec_type,
            path=path,
            spec=spec,
            planned_spec_fingerprint=planned_spec_fingerprint(spec),
            load_error="spec run_id does not match requested run",
        )
    return _SpecRecord(
        spec_id=spec_id,
        spec_type=spec_type,
        path=path,
        spec=spec,
        planned_spec_fingerprint=planned_spec_fingerprint(spec),
    )


def _dry_run_item(index: int, record: _SpecRecord, execution_id: str) -> PlannedSpecExecutionItem:
    if record.load_error is not None:
        return _failed_item(index, record, record.load_error)
    target_claim = _target_claim(record.spec)
    gap_fp = _gap_fingerprint_from_record(record)
    return PlannedSpecExecutionItem(
        item_id=f"item-{index:04d}",
        spec_id=record.spec_id,
        spec_type=record.spec_type,
        target_claim_id_optional=target_claim,
        gap_fingerprint=gap_fp,
        planned_spec_fingerprint=record.planned_spec_fingerprint,
        execution_attempt_fingerprint=execution_attempt_fingerprint(
            run_id=record.spec.run_id if record.spec is not None else "",
            execution_id=execution_id,
            target_id=record.spec_id,
            gap_fingerprint=gap_fp,
            planned_spec_fingerprint_value=record.planned_spec_fingerprint,
            decision="would_execute",
            status="planned",
        ),
        executor_decision="would_execute",
        execution_status="planned",
        safety_notes=[
            "Dry-run classified the planned spec without creating evidence artifacts.",
            (
                "No arbitrary code, shell command, network, proof checker, or "
                "retrieval API was invoked."
            ),
        ],
    )


def _skipped_duplicate_item(
    index: int,
    record: _SpecRecord,
    existing_path: str,
) -> PlannedSpecExecutionItem:
    gap_fp = _gap_fingerprint_from_record(record)
    return PlannedSpecExecutionItem(
        item_id=f"item-{index:04d}",
        spec_id=record.spec_id,
        spec_type=record.spec_type,
        target_claim_id_optional=_target_claim(record.spec),
        gap_fingerprint=gap_fp,
        planned_spec_fingerprint=record.planned_spec_fingerprint,
        executor_decision="skip",
        execution_status="skipped",
        created_artifact_path_optional=existing_path,
        rejected_reason_optional="Equivalent planned spec already exists; skipped duplicate.",
        safety_notes=[
            *_standard_safety_notes(),
            "Duplicate planned specs do not count as progress.",
        ],
    )


def _failed_item(index: int, record: _SpecRecord, reason: str) -> PlannedSpecExecutionItem:
    return PlannedSpecExecutionItem(
        item_id=f"item-{index:04d}",
        spec_id=record.spec_id,
        spec_type=record.spec_type,
        target_claim_id_optional=_target_claim(record.spec),
        gap_fingerprint=_gap_fingerprint_from_record(record),
        planned_spec_fingerprint=record.planned_spec_fingerprint,
        executor_decision="reject",
        execution_status="failed",
        failed_reason_optional=reason,
        safety_notes=_standard_safety_notes(),
    )


def _rejected_loaded_item(
    index: int,
    spec_id: str,
    spec_type: str,
    target_claim: str | None,
    reason: str,
) -> tuple[PlannedSpecExecutionItem, list[str], list[str]]:
    return (
        PlannedSpecExecutionItem(
            item_id=f"item-{index:04d}",
            spec_id=spec_id,
            spec_type=spec_type,
            target_claim_id_optional=target_claim,
            executor_decision="reject",
            execution_status="rejected",
            rejected_reason_optional=reason,
            safety_notes=_standard_safety_notes(),
        ),
        [],
        [],
    )


def _build_report(
    *,
    run_id: str,
    execution_id: str,
    execution_mode: str,
    backend: str,
    records: list[_SpecRecord],
    items: list[PlannedSpecExecutionItem],
    status: str,
    created_paths: list[str],
    ingested_paths: list[str],
    claim_map_rebuilt: bool,
    plan_rebuilt: bool,
    manuscript_refreshed: bool,
    release_rechecked: bool,
    requires_human: bool,
    human_reason: str | None,
) -> PlannedSpecExecutionReport:
    experiment_executed = sum(
        item.spec_type == "experiment_spec" and item.execution_status == "executed"
        for item in items
    )
    proof_executed = sum(
        item.spec_type == "proof_obligation_spec" and item.execution_status == "executed"
        for item in items
    )
    retrieval_executed = sum(
        item.spec_type == "retrieval_expansion_request" and item.execution_status == "executed"
        for item in items
    )
    return PlannedSpecExecutionReport(
        run_id=run_id,
        execution_id=execution_id,
        execution_mode=execution_mode,
        spec_executor_backend=backend,
        execution_status=status,
        spec_count=len(records),
        experiment_specs_found=sum(record.spec_type == "experiment_spec" for record in records),
        proof_specs_found=sum(record.spec_type == "proof_obligation_spec" for record in records),
        retrieval_specs_found=sum(
            record.spec_type == "retrieval_expansion_request" for record in records
        ),
        specs_attempted=len(items),
        specs_executed=sum(item.execution_status == "executed" for item in items),
        specs_deferred=sum(item.execution_status == "deferred" for item in items),
        specs_rejected=sum(item.execution_status == "rejected" for item in items),
        specs_failed=sum(item.execution_status == "failed" for item in items),
        duplicate_specs_skipped=sum(item.execution_status == "skipped" for item in items),
        unique_specs_executed=sum(item.execution_status == "executed" for item in items),
        gap_attempt_history_updated=status not in {"blocked", "dry_run_completed"},
        executions_marked_no_progress=sum(
            item.execution_status == "executed"
            and not (
                item.spec_type == "experiment_spec"
                and bool(item.ingested_artifact_path_optional)
            )
            for item in items
        ),
        experiment_artifacts_created=sum(
            bool(
                item.execution_status == "executed"
                and item.ingested_artifact_path_optional
                and item.spec_type == "experiment_spec"
            )
            for item in items
        ),
        proof_artifacts_created=sum(
            bool(
                item.execution_status == "executed"
                and item.ingested_artifact_path_optional
                and item.spec_type == "proof_obligation_spec"
            )
            for item in items
        ),
        retrieval_artifacts_created=sum(
            bool(
                item.execution_status == "executed"
                and item.created_artifact_path_optional
                and item.spec_type == "retrieval_expansion_request"
            )
            for item in items
        ),
        experiment_specs_executed=experiment_executed,
        proof_specs_executed=proof_executed,
        retrieval_specs_executed=retrieval_executed,
        items=items,
        created_artifact_paths=created_paths,
        ingested_artifact_paths=ingested_paths,
        claim_evidence_map_rebuilt=claim_map_rebuilt,
        autonomous_plan_rebuilt=plan_rebuilt,
        manuscript_refreshed=manuscript_refreshed,
        release_rechecked=release_rechecked,
        requires_human_intervention=requires_human,
        human_intervention_reason_optional=human_reason,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _next_execution_number(reports: Path) -> int:
    numbers: list[int] = []
    for path in reports.glob("planned-spec-execution-*.json"):
        if path.name.startswith("planned-spec-execution-index-") or path.name.endswith(
            ".meta.json"
        ):
            continue
        digits = [part for part in path.stem.split("-") if part.isdigit()]
        if digits:
            numbers.append(int(digits[-1]))
    return max(numbers, default=0) + 1


def _execution_report_path(reports: Path, execution_id: str) -> Path:
    suffix = execution_id.rsplit("-", maxsplit=1)[-1]
    dry_run = reports / f"planned-spec-execution-dry-run-{suffix}.json"
    if dry_run.is_file():
        return dry_run
    return reports / f"planned-spec-execution-{suffix}.json"


def _preferred_manuscript_path(reports: Path) -> Path:
    autonomous = sorted(reports.glob("autonomous-revised-manuscript-execution-*.md"))
    if autonomous:
        return autonomous[-1]
    reconciled = sorted(reports.glob("reconciled-manuscript-cycle-*.md"))
    if reconciled:
        return reconciled[-1]
    for name in (
        "reconciled-manuscript-draft.md",
        "evidence-aware-refreshed-manuscript-draft.md",
        "revised-manuscript-draft.md",
        "complete-manuscript-draft.md",
    ):
        path = reports / name
        if path.is_file():
            return path
    raise PlannedSpecExecutionError("No preferred manuscript draft was found.")


def _read_registry(path: Path, run_id: str) -> CitationRegistry:
    if not path.is_file():
        return CitationRegistry(
            run_id=run_id,
            citations=[],
            bibliography=[],
            citation_key_policy="deterministic_no_registry",
            citation_policy="none",
            source_registry_hash=sha256_text("[]"),
        )
    return CitationRegistry.model_validate_json(path.read_text(encoding="utf-8"))


def _available_evidence(root: Path, run_id: str) -> dict[str, bool]:
    from factori.evidence_artifact_intake import (  # noqa: PLC0415
        inspect_experiment_artifacts,
        inspect_proof_artifacts,
    )

    proof = inspect_proof_artifacts(run_id=run_id, root=root)
    experiment = inspect_experiment_artifacts(run_id=run_id, root=root)
    human_review = root / "runs" / run_id / "reports" / "human-review-artifact.json"
    return {
        "proof": int(proof["formal_verification_passed_count"]) > 0,
        "experiment": int(experiment["completed_experiment_count"]) > 0,
        "human_review": human_review.is_file(),
        "publication_ready": False,
    }


def _synthetic_experiment_metrics(config: dict[str, Any]) -> dict[str, float | int | str]:
    seed = int(config["seed"])
    claim_factor = (sum(ord(ch) for ch in str(config["target_claim_id"])) % 17) / 1000
    baseline_error = round(0.240 + (seed % 11) / 1000 + claim_factor, 6)
    method_error = round(baseline_error - 0.037, 6)
    return {
        "seed": seed,
        "synthetic_sample_count": 64,
        "baseline_error": baseline_error,
        "method_error": method_error,
        "bounded_improvement": round(baseline_error - method_error, 6),
        "template": "synthetic_calibration_demo",
    }


def _forces_failed_experiment(spec: PlannedExperimentSpec) -> bool:
    text = " ".join(
        [
            spec.hypothesis_or_question,
            spec.suggested_dataset,
            *spec.suggested_metrics,
            *spec.suggested_baselines,
        ]
    ).casefold()
    return "force_failed_experiment" in text or "force failed experiment" in text


def _fixture_formal_proof_allowed(spec: ProofObligationSpec) -> bool:
    marker_text = f"{spec.suggested_checker} {spec.required_artifact_type}".casefold()
    return "deterministic fixture" in marker_text or "fixture-backed" in marker_text


def _target_claim(
    spec: PlannedExperimentSpec | ProofObligationSpec | RetrievalExpansionRequest | None,
) -> str | None:
    if isinstance(spec, PlannedExperimentSpec | ProofObligationSpec):
        return spec.target_claim_id
    if isinstance(spec, RetrievalExpansionRequest):
        return spec.target_claim_id_optional
    return None


def _gap_fingerprint_from_record(record: _SpecRecord) -> str | None:
    return _gap_fingerprint_for_spec(record.spec) if record.spec is not None else None


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


def _metadata(role: str) -> dict[str, Any]:
    return {
        "stage": "planned_spec_execution",
        "artifact_role": role,
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }


def _standard_safety_notes() -> list[str]:
    return [
        "Planned spec execution is workflow context unless a validated artifact is ingested.",
        (
            "No network, external API, arbitrary shell command, Docker, or "
            "untrusted Python was invoked."
        ),
        (
            "No artifact implies novelty, broad validation, correctness validation, "
            "or publication readiness."
        ),
    ]


def _slug(value: str) -> str:
    return "-".join(part for part in value.casefold().replace("&", "and").split() if part)


def _safe_suffix(value: str) -> str:
    cleaned = "".join(character if character.isalnum() else "-" for character in value)
    return "-".join(part for part in cleaned.split("-") if part)


__all__ = [
    "PlannedSpecExecutionError",
    "PlannedSpecExecutionResult",
    "execute_planned_specs",
    "inspect_planned_spec_execution",
    "latest_planned_spec_execution_index_path",
    "latest_planned_spec_execution_report",
    "planned_spec_execution_summary_fields",
    "render_planned_spec_execution_markdown",
]
