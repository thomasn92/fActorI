"""Deterministic dry-run and apply execution of autonomous evidence-gap plans."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.autonomous_evidence_plan import (
    latest_autonomous_evidence_gap_plan_path,
    persist_autonomous_evidence_gap_plan,
)
from factori.citations import (
    build_claim_support_audit,
    repair_confirmed_claim_support_violations,
    repair_missing_required_citations_with_accepted_sources,
    validate_citation_usage,
)
from factori.claim_evidence import (
    latest_claim_evidence_map_path,
    latest_claim_support_audit_path,
    persist_claim_evidence_map,
)
from factori.gap_attempts import (
    find_existing_planned_spec,
    gap_fingerprint_for_plan_item,
    persist_gap_attempt_artifacts,
    plan_item_fingerprint,
    planned_spec_fingerprint,
)
from factori.hashing import sha256_file, sha256_text
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.reports import render_full_paper_release_summary
from factori.rerun_policy import validate_ledger_tip
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AutonomousEvidenceGapPlan,
    AutonomousEvidenceGapPlanItem,
    AutonomousPlanExecutionAction,
    AutonomousPlanExecutionIndex,
    AutonomousPlanExecutionReport,
    CitationRegistry,
    ClaimEvidenceMap,
    ClaimSupportAuditReport,
    ControllerActionType,
    FullPaperReleaseGateConfig,
    PlannedExperimentSpec,
    ProofObligationSpec,
    RetrievalExpansionRequest,
)

_EXECUTOR_BACKENDS = {"deterministic", "fake", "openai"}
_EXECUTION_MODES = {"dry_run", "apply"}
_BOUNDARY_REPLACEMENT = (
    "This point remains a bounded manuscript scaffold pending scoped evidence; "
    "the current draft makes no novelty, validation, correctness, or publication-"
    "readiness claim for it."
)


class AutonomousPlanExecutionError(RuntimeError):
    """Raised when autonomous plan execution cannot proceed safely."""


@dataclass(frozen=True)
class AutonomousPlanExecutionResult:
    """Persisted autonomous execution and final derived artifacts."""

    run_id: str
    report: AutonomousPlanExecutionReport
    index: AutonomousPlanExecutionIndex
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    report_markdown_artifact: ArtifactRef
    index_artifact: ArtifactRef


def execute_autonomous_evidence_plan(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    execution_mode: str = "dry_run",
    executor_backend: str = "deterministic",
    max_attempts_per_gap: int = 2,
) -> AutonomousPlanExecutionResult:
    """Validate and execute the latest autonomous plan within bounded policy."""
    execution_mode = execution_mode.replace("-", "_")
    if execution_mode not in _EXECUTION_MODES:
        raise AutonomousPlanExecutionError("execution mode must be dry_run or apply")
    if executor_backend not in _EXECUTOR_BACKENDS:
        raise AutonomousPlanExecutionError(
            "executor backend must be deterministic, fake, or openai"
        )
    if executor_backend == "openai":
        raise AutonomousPlanExecutionError(
            "OpenAI autonomous plan execution is schema-gated but not implemented in M67."
        )
    if max_attempts_per_gap < 1:
        raise AutonomousPlanExecutionError("max attempts per gap must be at least 1")

    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    reports = run_path / "reports"
    if not run_path.is_dir():
        raise AutonomousPlanExecutionError(f"No run directory found for run_id={run_id}.")
    ledger_check = validate_ledger_tip(run_id, root=root_path)
    if ledger_check.blocking_findings:
        raise AutonomousPlanExecutionError(
            "Ledger validation has blocking findings; autonomous execution cannot append safely."
        )

    execution_number = _next_execution_number(reports)
    execution_id = f"execution-{execution_number:04d}"
    plan_path = latest_autonomous_evidence_gap_plan_path(root_path, run_id)
    relative_plan_path = (
        plan_path.relative_to(root_path).as_posix()
        if plan_path is not None
        else f"runs/{run_id}/reports/autonomous-evidence-gap-plan.json"
    )
    plan, plan_error = _load_plan(plan_path, run_id)
    if plan_error is not None or plan is None:
        return _persist_blocked_execution(
            run_id=run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            execution_id=execution_id,
            execution_number=execution_number,
            execution_mode=execution_mode,
            executor_backend=executor_backend,
            plan_path=relative_plan_path,
            reason=plan_error or "Autonomous evidence plan is unavailable.",
        )
    if plan.requires_human_intervention or plan.planner_status.startswith("blocked_"):
        return _persist_blocked_execution(
            run_id=run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            execution_id=execution_id,
            execution_number=execution_number,
            execution_mode=execution_mode,
            executor_backend=executor_backend,
            plan_path=relative_plan_path,
            reason=(
                plan.human_intervention_reason_optional
                or f"Planner status `{plan.planner_status}` blocks autonomous execution."
            ),
            plan_item_count=len(plan.plan_items),
        )

    manuscript_path = _preferred_manuscript_path(reports)
    manuscript_before_hash = sha256_file(manuscript_path)
    claim_map_path = latest_claim_evidence_map_path(root_path, run_id)
    claim_map_before_hash = sha256_file(claim_map_path) if claim_map_path else None
    if execution_mode == "dry_run":
        actions = [
            _dry_run_action(run_id, index, item, manuscript_before_hash)
            for index, item in enumerate(plan.plan_items, start=1)
        ]
        report = _build_report(
            run_id=run_id,
            plan_path=relative_plan_path,
            execution_id=execution_id,
            execution_mode=execution_mode,
            executor_backend=executor_backend,
            plan_item_count=len(plan.plan_items),
            actions=actions,
            status="dry_run_completed",
            manuscript_modified=False,
            claim_map_rebuilt=False,
            claim_support_rechecked=False,
            citation_safety_rechecked=False,
            release_rechecked=False,
            created_paths=[],
            requires_human=False,
            human_reason=None,
        )
        result = _persist_final_execution(
            report=report,
            root=root_path,
            store=store,
            ledger=ledger,
            execution_number=execution_number,
            release_report=None,
        )
        if sha256_file(manuscript_path) != manuscript_before_hash:
            raise AutonomousPlanExecutionError("Dry-run modified the manuscript unexpectedly.")
        if claim_map_path and sha256_file(claim_map_path) != claim_map_before_hash:
            raise AutonomousPlanExecutionError(
                "Dry-run modified the claim-evidence map unexpectedly."
            )
        return result

    refresh_artifact_path: str | None = None
    refresh_performed = False
    if any(item.gap_type == "needs_manuscript_refresh" for item in plan.plan_items):
        existing_refresh = reports / "evidence-aware-refresh-report.json"
        existing_manuscript = reports / "evidence-aware-refreshed-manuscript-draft.md"
        if existing_refresh.is_file() and existing_manuscript.is_file():
            refresh_artifact_path = existing_manuscript.relative_to(root_path).as_posix()
            manuscript_path = existing_manuscript
        else:
            from factori.evidence_aware_refresh import (  # noqa: PLC0415
                EvidenceAwareRefreshError,
                refresh_evidence_aware_manuscript,
            )

            try:
                refresh_result = refresh_evidence_aware_manuscript(
                    run_id=run_id,
                    root=root_path,
                    store=store,
                    ledger=ledger,
                    backend="deterministic",
                )
            except EvidenceAwareRefreshError as exc:
                raise AutonomousPlanExecutionError(
                    f"Deterministic evidence-aware refresh failed: {exc}"
                ) from exc
            refresh_artifact_path = refresh_result.manuscript_artifact.path
            refresh_performed = True
            manuscript_path = root_path / refresh_artifact_path
            claim_map_path = latest_claim_evidence_map_path(root_path, run_id)

    registry = _read_registry(reports / "citation-registry.json", run_id)
    claim_map = _read_claim_map(claim_map_path, run_id)
    support_audit = _read_claim_support_audit(root_path, run_id)
    markdown = manuscript_path.read_text(encoding="utf-8")
    actions, revised_markdown, specs = _apply_actions(
        run_id=run_id,
        root=root_path,
        execution_id=execution_id,
        plan=plan,
        markdown=markdown,
        claim_map=claim_map,
        claim_support=support_audit,
        manuscript_before_hash=manuscript_before_hash,
        refresh_artifact_path=refresh_artifact_path,
    )
    manuscript_modified = refresh_performed or revised_markdown != markdown
    available_evidence = _available_evidence(claim_map)
    post_support = build_claim_support_audit(
        run_id=run_id,
        markdown=revised_markdown,
        citation_registry=registry,
        available_evidence_artifacts=available_evidence,
    )
    revised_markdown, _ = repair_confirmed_claim_support_violations(
        revised_markdown,
        post_support,
    )
    post_support = build_claim_support_audit(
        run_id=run_id,
        markdown=revised_markdown,
        citation_registry=registry,
        available_evidence_artifacts=available_evidence,
    )
    citation_repair = repair_missing_required_citations_with_accepted_sources(
        revised_markdown,
        post_support,
        registry,
    )
    revised_markdown = citation_repair.revised_markdown
    manuscript_modified = manuscript_modified or revised_markdown != markdown
    post_support = build_claim_support_audit(
        run_id=run_id,
        markdown=revised_markdown,
        citation_registry=registry,
        available_evidence_artifacts=available_evidence,
    )
    citation_safety = validate_citation_usage(revised_markdown, registry)
    if not citation_safety.safe:
        raise AutonomousPlanExecutionError(
            "Autonomous apply produced a citation-safety violation; no execution "
            "report was finalized."
        )

    apply_id = f"autonomous-plan-apply-{execution_number:04d}"
    apply_specs = list(specs)
    created_paths = [
        action.created_artifact_path_optional
        for action in actions
        if action.created_artifact_path_optional is not None
    ]
    if manuscript_modified:
        manuscript_id = f"autonomous-revised-manuscript-{execution_id}"
        apply_specs.append(
            ArtifactWriteSpec(
                manuscript_id,
                ArtifactType.REPORT,
                revised_markdown,
                "markdown",
                _metadata("autonomous_execution_manuscript_context"),
            )
        )
        created_paths.append(f"runs/{run_id}/reports/{manuscript_id}.md")
    audit_id = f"claim-support-audit-after-autonomous-{execution_id}"
    safety_id = f"citation-safety-after-autonomous-{execution_id}"
    apply_specs.extend(
        [
            ArtifactWriteSpec(
                audit_id,
                ArtifactType.REPORT,
                post_support,
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
        ]
    )
    created_paths.extend(
        [
            f"runs/{run_id}/reports/{audit_id}.json",
            f"runs/{run_id}/reports/{safety_id}.json",
        ]
    )
    persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=apply_specs,
        action_type=ControllerActionType.AUTONOMOUS_PLAN_EXECUTION_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "execution_id": execution_id,
            "execution_mode": "apply",
            "artifact_phase": apply_id,
            "manuscript_modified": manuscript_modified,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )

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
    from factori.full_paper_release import evaluate_full_paper_release  # noqa: PLC0415

    release_report = evaluate_full_paper_release(
        run_id=run_id,
        root=root_path,
        ledger=ledger,
        config=FullPaperReleaseGateConfig(run_id=run_id, write_report=False),
    )
    status = (
        "completed_with_deferred_actions"
        if any(action.execution_status in {"deferred", "rejected", "failed"} for action in actions)
        else "completed"
    )
    requires_human = any(action.execution_status == "failed" for action in actions)
    report = _build_report(
        run_id=run_id,
        plan_path=relative_plan_path,
        execution_id=execution_id,
        execution_mode=execution_mode,
        executor_backend=executor_backend,
        plan_item_count=len(plan.plan_items),
        actions=actions,
        status=status,
        manuscript_modified=manuscript_modified,
        claim_map_rebuilt=True,
        claim_support_rechecked=True,
        citation_safety_rechecked=True,
        release_rechecked=True,
        created_paths=sorted(set(created_paths)),
        requires_human=requires_human,
        human_reason=(
            "One or more deterministic execution actions failed classification or application."
            if requires_human
            else None
        ),
    )
    result = _persist_final_execution(
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


def inspect_autonomous_plan_execution(*, run_id: str, root: str | Path = ".") -> dict[str, Any]:
    """Inspect the latest immutable autonomous execution report."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    index_path = latest_autonomous_plan_execution_index_path(root_path, run_id)
    if index_path is None:
        raise AutonomousPlanExecutionError(
            f"No autonomous plan execution found for run_id={run_id}."
        )
    index = AutonomousPlanExecutionIndex.model_validate_json(index_path.read_text(encoding="utf-8"))
    report_path = _execution_report_path(reports, index.latest_execution_id)
    report = AutonomousPlanExecutionReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
    return {
        **report.model_dump(mode="json"),
        **autonomous_execution_summary_fields(report, index),
        "autonomous_execution_report_path": report_path.relative_to(root_path).as_posix(),
        "autonomous_execution_index_path": index_path.relative_to(root_path).as_posix(),
        "autonomous_plan_execution_index": index.model_dump(mode="json"),
    }


def autonomous_execution_summary_fields(
    report: AutonomousPlanExecutionReport | None,
    index: AutonomousPlanExecutionIndex | None = None,
) -> dict[str, Any]:
    """Return stable inspect/lint summary fields for autonomous execution."""
    if report is None:
        return {
            "autonomous_execution_present": False,
            "autonomous_execution_count": 0,
            "latest_autonomous_execution_mode": None,
            "latest_autonomous_execution_status": None,
            "autonomous_actions_applied": 0,
            "autonomous_actions_deferred": 0,
            "autonomous_actions_rejected": 0,
            "autonomous_actions_failed": 0,
            "autonomous_created_spec_count": 0,
            "duplicate_specs_skipped": 0,
            "autonomous_execution_requires_human_intervention": False,
            "autonomous_next_required_artifacts": [],
        }
    return {
        "autonomous_execution_present": True,
        "autonomous_execution_count": index.execution_count if index else 1,
        "latest_autonomous_execution_mode": report.execution_mode,
        "latest_autonomous_execution_status": report.execution_status,
        "autonomous_actions_applied": report.actions_applied,
        "autonomous_actions_deferred": report.actions_deferred,
        "autonomous_actions_rejected": report.actions_rejected,
        "autonomous_actions_failed": report.actions_failed,
        "autonomous_created_spec_count": sum(
            1
            for action in report.actions
            if action.created_artifact_path_optional
            and action.applied
            and action.execution_status == "completed"
            and any(
                token in action.created_artifact_path_optional
                for token in (
                    "experiment-spec-",
                    "proof-obligation-spec-",
                    "retrieval-expansion-request-",
                )
            )
        ),
        "duplicate_specs_skipped": report.duplicate_specs_skipped,
        "autonomous_execution_requires_human_intervention": (report.requires_human_intervention),
        "autonomous_next_required_artifacts": list(report.next_required_artifacts),
    }


def latest_autonomous_plan_execution_index_path(root: Path, run_id: str) -> Path | None:
    """Return the latest immutable autonomous execution index path."""
    reports = root / "runs" / run_id / "reports"
    paths = [
        path
        for path in reports.glob("autonomous-plan-execution-index-*.json")
        if not path.name.endswith(".meta.json")
    ]
    return sorted(paths)[-1] if paths else None


def latest_autonomous_plan_execution_report(
    root: Path, run_id: str
) -> tuple[AutonomousPlanExecutionReport | None, AutonomousPlanExecutionIndex | None]:
    """Load the latest execution report and index for bundle summaries."""
    index_path = latest_autonomous_plan_execution_index_path(root, run_id)
    if index_path is None:
        return None, None
    try:
        index = AutonomousPlanExecutionIndex.model_validate_json(
            index_path.read_text(encoding="utf-8")
        )
        report_path = _execution_report_path(
            root / "runs" / run_id / "reports",
            index.latest_execution_id,
        )
        report = AutonomousPlanExecutionReport.model_validate_json(
            report_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None, None
    return report, index


def render_autonomous_plan_execution_markdown(
    report: AutonomousPlanExecutionReport,
) -> str:
    """Render a concise autonomous plan execution report."""
    lines = [
        "# Autonomous Plan Execution",
        "",
        f"Run ID: `{report.run_id}`",
        f"Execution ID: `{report.execution_id}`",
        f"Mode: `{report.execution_mode}`",
        f"Backend: `{report.executor_backend}`",
        f"Status: `{report.execution_status}`",
        f"Actions applied/deferred/rejected/failed: `{report.actions_applied}/"
        f"{report.actions_deferred}/{report.actions_rejected}/{report.actions_failed}`",
        f"Duplicate specs skipped: `{report.duplicate_specs_skipped}`",
        f"Manuscript modified: `{str(report.manuscript_modified).lower()}`",
        "",
        "## Actions",
    ]
    for action in report.actions:
        lines.append(
            f"- `{action.action_id}` / `{action.plan_item_id}`: "
            f"`{action.execution_decision}` -> `{action.execution_status}`"
        )
    lines.extend(
        [
            "",
            "## Next Required Artifacts",
            *[f"- {item}" for item in report.next_required_artifacts or ["none"]],
            "",
            "## Non-Evidence Boundary",
            "- publication_ready: false",
            "- creates_scientific_validation: false",
            "- implies_publication_readiness: false",
            "- is_verification_evidence: false",
            "",
        ]
    )
    return "\n".join(lines)


def _apply_actions(
    *,
    run_id: str,
    root: Path,
    execution_id: str,
    plan: AutonomousEvidenceGapPlan,
    markdown: str,
    claim_map: ClaimEvidenceMap,
    claim_support: ClaimSupportAuditReport,
    manuscript_before_hash: str,
    refresh_artifact_path: str | None,
) -> tuple[list[AutonomousPlanExecutionAction], str, list[ArtifactWriteSpec]]:
    revised = markdown
    actions: list[AutonomousPlanExecutionAction] = []
    specs: list[ArtifactWriteSpec] = []
    support_by_id = {item.sentence_id: item for item in claim_support.claim_support_items}
    links_by_id = {link.claim_id: link for link in claim_map.links}
    for index, item in enumerate(plan.plan_items, start=1):
        action_id = f"action-{index:04d}"
        common = {
            "action_id": action_id,
            "plan_item_id": item.item_id,
            "target_claim_id_optional": item.target_claim_id_optional,
            "target_section_optional": item.target_section_optional,
            "gap_type": item.gap_type,
            "recommended_action": item.recommended_action,
            "dry_run": False,
            "gap_fingerprint": item.gap_fingerprint
            or gap_fingerprint_for_plan_item(run_id=run_id, item=item),
            "plan_item_fingerprint": item.plan_item_fingerprint
            or plan_item_fingerprint(run_id=run_id, item=item),
            "source_gap_fingerprint_optional": item.source_gap_fingerprint,
            "strategy_fingerprint_optional": item.strategy_fingerprint,
            "strategy_family_optional": item.strategy_family,
            "before_hash_optional": manuscript_before_hash,
            "safety_notes": [
                "Execution creates workflow/specification artifacts only.",
                "No action creates scientific validation or publication readiness.",
            ],
        }
        if item.gap_type == "sufficiently_supported_for_bounded_draft":
            actions.append(
                AutonomousPlanExecutionAction(
                    **common,
                    execution_decision="noop",
                    execution_status="completed",
                    applied=False,
                    after_hash_optional=sha256_text(revised),
                )
            )
            continue
        if not item.automation_ready:
            actions.append(
                AutonomousPlanExecutionAction(
                    **common,
                    execution_decision="defer",
                    execution_status="deferred",
                    applied=False,
                    deferred_reason_optional="Plan item is not marked automation-ready.",
                    after_hash_optional=sha256_text(revised),
                )
            )
            continue
        if item.gap_type == "needs_python_experiment":
            if not item.target_claim_id_optional or not item.target_section_optional:
                actions.append(_failed_action(common, revised, "Experiment target is incomplete."))
                continue
            stem = _spec_stem("experiment-spec", item, execution_id)
            spec = PlannedExperimentSpec(
                run_id=run_id,
                spec_id=stem,
                target_claim_id=item.target_claim_id_optional,
                target_section=item.target_section_optional,
                hypothesis_or_question=(
                    support_by_id.get(item.target_claim_id_optional).sentence_snippet
                    if item.target_claim_id_optional in support_by_id
                    else item.recommended_action
                ),
                suggested_dataset=_strategy_experiment_dataset(item),
                suggested_metrics=_strategy_experiment_metrics(item),
                suggested_baselines=_strategy_experiment_baselines(item),
                suggested_seed_policy="fixed seeds recorded in the future experiment artifact",
                expected_output_artifacts=[
                    "experiment configuration",
                    "metrics report",
                    "execution log and hashes",
                ],
            )
            path = f"runs/{run_id}/reports/{stem}.json"
            actions.append(
                _planned_spec_action(
                    common=common,
                    markdown=revised,
                    root=root,
                    run_id=run_id,
                    spec=spec,
                    path=path,
                    artifact_spec=ArtifactWriteSpec(
                        stem,
                        ArtifactType.REPORT,
                        spec,
                        "json",
                        _metadata("planned_experiment_spec_context"),
                    ),
                    specs=specs,
                )
            )
            continue
        if item.gap_type == "needs_formal_proof":
            if not item.target_claim_id_optional:
                actions.append(_failed_action(common, revised, "Proof target is incomplete."))
                continue
            stem = _spec_stem("proof-obligation-spec", item, execution_id)
            statement = (
                support_by_id.get(item.target_claim_id_optional).sentence_snippet
                if item.target_claim_id_optional in support_by_id
                else links_by_id.get(item.target_claim_id_optional).support_scope
                if item.target_claim_id_optional in links_by_id
                else item.recommended_action
            )
            spec = ProofObligationSpec(
                run_id=run_id,
                spec_id=stem,
                target_claim_id=item.target_claim_id_optional,
                statement=_strategy_proof_statement(item, statement),
                suggested_checker=_strategy_proof_checker(item),
                required_artifact_type=_strategy_proof_artifact_type(item),
            )
            path = f"runs/{run_id}/reports/{stem}.json"
            actions.append(
                _planned_spec_action(
                    common=common,
                    markdown=revised,
                    root=root,
                    run_id=run_id,
                    spec=spec,
                    path=path,
                    artifact_spec=ArtifactWriteSpec(
                        stem,
                        ArtifactType.REPORT,
                        spec,
                        "json",
                        _metadata("planned_proof_obligation_context"),
                    ),
                    specs=specs,
                )
            )
            continue
        if item.gap_type == "needs_retrieval_expansion":
            stem = _spec_stem("retrieval-expansion-request", item, execution_id)
            request = RetrievalExpansionRequest(
                run_id=run_id,
                request_id=stem,
                target_claim_id_optional=item.target_claim_id_optional,
                target_section_optional=item.target_section_optional,
                query_terms=_query_terms(item),
                reason=item.rationale,
                minimum_source_quality=(
                    "accepted registry source after deterministic metadata, duplicate, "
                    "relevance, and hard-rejection checks; "
                    f"strategy={item.strategy_family or 'initial'}"
                ),
            )
            path = f"runs/{run_id}/reports/{stem}.json"
            actions.append(
                _planned_spec_action(
                    common=common,
                    markdown=revised,
                    root=root,
                    run_id=run_id,
                    spec=request,
                    path=path,
                    artifact_spec=ArtifactWriteSpec(
                        stem,
                        ArtifactType.REPORT,
                        request,
                        "json",
                        _metadata("planned_retrieval_expansion_context"),
                    ),
                    specs=specs,
                )
            )
            continue
        if item.gap_type in {"needs_claim_downgrade", "needs_claim_removal"}:
            target = support_by_id.get(item.target_claim_id_optional or "")
            if target is None:
                actions.append(
                    _failed_action(
                        common,
                        revised,
                        "Target claim is not in the final claim-support audit.",
                    )
                )
                continue
            replacement = "" if item.gap_type == "needs_claim_removal" else _BOUNDARY_REPLACEMENT
            updated = _replace_sentence_by_hash(
                revised,
                target.sentence_text_hash,
                replacement,
            )
            if updated == revised:
                actions.append(
                    _failed_action(
                        common,
                        revised,
                        "Target claim text could not be matched safely.",
                    )
                )
                continue
            revised = updated
            actions.append(
                AutonomousPlanExecutionAction(
                    **common,
                    execution_decision="apply",
                    execution_status="completed",
                    applied=True,
                    after_hash_optional=sha256_text(revised),
                )
            )
            continue
        if item.gap_type == "needs_manuscript_refresh":
            if refresh_artifact_path is None:
                actions.append(
                    _failed_action(
                        common,
                        revised,
                        "Deterministic evidence-aware refresh did not produce an artifact.",
                    )
                )
            else:
                actions.append(
                    AutonomousPlanExecutionAction(
                        **common,
                        execution_decision="apply",
                        execution_status="completed",
                        applied=True,
                        created_artifact_path_optional=refresh_artifact_path,
                        after_hash_optional=sha256_text(revised),
                    )
                )
            continue
        actions.append(_failed_action(common, revised, "Plan gap type is not executable."))
    return actions, revised, specs


def _dry_run_action(
    run_id: str,
    index: int,
    item: AutonomousEvidenceGapPlanItem,
    manuscript_hash: str,
) -> AutonomousPlanExecutionAction:
    noop = item.gap_type == "sufficiently_supported_for_bounded_draft"
    gap_fp = item.gap_fingerprint or gap_fingerprint_for_plan_item(run_id=run_id, item=item)
    item_fp = item.plan_item_fingerprint or plan_item_fingerprint(run_id=run_id, item=item)
    return AutonomousPlanExecutionAction(
        action_id=f"action-{index:04d}",
        plan_item_id=item.item_id,
        target_claim_id_optional=item.target_claim_id_optional,
        target_section_optional=item.target_section_optional,
        gap_type=item.gap_type,
        recommended_action=item.recommended_action,
        execution_decision="noop" if noop else "would_apply" if item.automation_ready else "defer",
        execution_status=(
            "completed" if noop else "planned" if item.automation_ready else "deferred"
        ),
        dry_run=True,
        applied=False,
        gap_fingerprint=gap_fp,
        plan_item_fingerprint=item_fp,
        deferred_reason_optional=(
            None if item.automation_ready or noop else "Plan item is not automation-ready."
        ),
        before_hash_optional=manuscript_hash,
        after_hash_optional=manuscript_hash,
        safety_notes=[
            "Dry-run validated the action without modifying manuscript or evidence artifacts."
        ],
    )


def _build_report(
    *,
    run_id: str,
    plan_path: str,
    execution_id: str,
    execution_mode: str,
    executor_backend: str,
    plan_item_count: int,
    actions: list[AutonomousPlanExecutionAction],
    status: str,
    manuscript_modified: bool,
    claim_map_rebuilt: bool,
    claim_support_rechecked: bool,
    citation_safety_rechecked: bool,
    release_rechecked: bool,
    created_paths: list[str],
    requires_human: bool,
    human_reason: str | None,
) -> AutonomousPlanExecutionReport:
    return AutonomousPlanExecutionReport(
        run_id=run_id,
        plan_path=plan_path,
        execution_id=execution_id,
        execution_mode=execution_mode,
        executor_backend=executor_backend,
        execution_status=status,
        plan_item_count=plan_item_count,
        actions=actions,
        actions_attempted=len(actions),
        actions_applied=sum(action.applied for action in actions),
        actions_deferred=sum(action.execution_status == "deferred" for action in actions),
        actions_rejected=sum(action.execution_status == "rejected" for action in actions),
        actions_failed=sum(action.execution_status == "failed" for action in actions),
        duplicate_specs_skipped=sum(action.execution_status == "skipped" for action in actions),
        existing_specs_reused=sum(
            action.execution_status == "skipped" and bool(action.created_artifact_path_optional)
            for action in actions
        ),
        gap_attempt_history_updated=status not in {"blocked", "dry_run_completed"},
        actions_marked_exhausted=0,
        manuscript_modified=manuscript_modified,
        claim_evidence_map_rebuilt=claim_map_rebuilt,
        claim_support_rechecked=claim_support_rechecked,
        citation_safety_rechecked=citation_safety_rechecked,
        release_rechecked=release_rechecked,
        next_required_artifacts=_next_required_artifacts(actions),
        created_artifact_paths=created_paths,
        requires_human_intervention=requires_human,
        human_intervention_reason_optional=human_reason,
        publication_ready=False,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _persist_blocked_execution(
    *,
    run_id: str,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    execution_id: str,
    execution_number: int,
    execution_mode: str,
    executor_backend: str,
    plan_path: str,
    reason: str,
    plan_item_count: int = 0,
) -> AutonomousPlanExecutionResult:
    report = _build_report(
        run_id=run_id,
        plan_path=plan_path,
        execution_id=execution_id,
        execution_mode=execution_mode,
        executor_backend=executor_backend,
        plan_item_count=plan_item_count,
        actions=[],
        status="blocked",
        manuscript_modified=False,
        claim_map_rebuilt=False,
        claim_support_rechecked=False,
        citation_safety_rechecked=False,
        release_rechecked=False,
        created_paths=[],
        requires_human=True,
        human_reason=reason,
    )
    return _persist_final_execution(
        report=report,
        root=root,
        store=store,
        ledger=ledger,
        execution_number=execution_number,
        release_report=None,
    )


def _persist_final_execution(
    *,
    report: AutonomousPlanExecutionReport,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    execution_number: int,
    release_report,
) -> AutonomousPlanExecutionResult:
    from factori.full_paper_generation import (  # noqa: PLC0415
        build_reviewer_bundle_summary,
        render_reviewer_bundle_summary_markdown,
    )

    mode_stem = "dry-run-" if report.execution_mode == "dry_run" else ""
    report_id = f"autonomous-plan-execution-{mode_stem}{execution_number:04d}"
    index_id = f"autonomous-plan-execution-index-{execution_number:04d}"
    reviewer_id = f"reviewer-bundle-summary-after-autonomous-execution-{execution_number:04d}"
    release_id = f"full-paper-release-report-after-autonomous-execution-{execution_number:04d}"
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
    index = AutonomousPlanExecutionIndex(
        run_id=report.run_id,
        latest_execution_id=report.execution_id,
        execution_count=execution_number,
        latest_execution_mode=report.execution_mode,
        latest_execution_status=report.execution_status,
        latest_created_artifact_paths=report.created_artifact_paths,
        latest_requires_human_intervention=report.requires_human_intervention,
    )
    reviewer = build_reviewer_bundle_summary(
        run_id=report.run_id,
        root=root,
        release_report=release_report,
        autonomous_execution_report=report,
    )
    specs = [
        ArtifactWriteSpec(
            report_id,
            ArtifactType.REPORT,
            report,
            "json",
            _metadata("autonomous_plan_execution_context"),
        ),
        ArtifactWriteSpec(
            f"{report_id}-markdown",
            ArtifactType.REPORT,
            render_autonomous_plan_execution_markdown(report),
            "markdown",
            _metadata("autonomous_plan_execution_context"),
            filename_stem=report_id,
        ),
        ArtifactWriteSpec(
            index_id,
            ArtifactType.REPORT,
            index,
            "json",
            _metadata("autonomous_plan_execution_index_context"),
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
        action_type=ControllerActionType.AUTONOMOUS_PLAN_EXECUTION_WRITTEN,
        commit_payload={
            "run_id": report.run_id,
            "execution_id": report.execution_id,
            "execution_mode": report.execution_mode,
            "execution_status": report.execution_status,
            "actions_applied": report.actions_applied,
            "actions_deferred": report.actions_deferred,
            "actions_rejected": report.actions_rejected,
            "actions_failed": report.actions_failed,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return AutonomousPlanExecutionResult(
        run_id=report.run_id,
        report=report,
        index=index,
        persistence=persistence,
        report_artifact=by_id[report_id],
        report_markdown_artifact=by_id[f"{report_id}-markdown"],
        index_artifact=by_id[index_id],
    )


def _load_plan(
    path: Path | None, run_id: str
) -> tuple[AutonomousEvidenceGapPlan | None, str | None]:
    if path is None:
        return None, "Autonomous evidence plan is missing."
    try:
        plan = AutonomousEvidenceGapPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "Autonomous evidence plan is corrupt or unreadable."
    if plan.run_id != run_id:
        return None, "Autonomous evidence plan run_id does not match the requested run."
    return plan, None


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


def _read_claim_map(path: Path | None, run_id: str) -> ClaimEvidenceMap:
    if path is None:
        raise AutonomousPlanExecutionError(f"No claim-evidence map found for run_id={run_id}.")
    return ClaimEvidenceMap.model_validate_json(path.read_text(encoding="utf-8"))


def _read_claim_support_audit(root: Path, run_id: str) -> ClaimSupportAuditReport:
    path = latest_claim_support_audit_path(root, run_id)
    if not path.is_file():
        raise AutonomousPlanExecutionError("Claim-support audit is required for apply mode.")
    return ClaimSupportAuditReport.model_validate_json(path.read_text(encoding="utf-8"))


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
    raise AutonomousPlanExecutionError("No preferred manuscript draft was found.")


def _available_evidence(claim_map: ClaimEvidenceMap) -> dict[str, bool]:
    return {
        "proof": any(link.support_type == "formal_proof_verification" for link in claim_map.links),
        "experiment": any(link.support_type == "experiment_result" for link in claim_map.links),
        "human_review": any(
            link.support_type == "human_review_occurrence" for link in claim_map.links
        ),
        "publication_ready": False,
    }


def _replace_sentence_by_hash(markdown: str, expected_hash: str, replacement: str) -> str:
    for sentence in _sentences(markdown):
        if sha256_text(sentence) != expected_hash:
            continue
        pattern = re.compile(r"\s+".join(re.escape(part) for part in sentence.split()))
        updated, count = pattern.subn(replacement, markdown, count=1)
        if count:
            return re.sub(r"\n{3,}", "\n\n", updated)
    return markdown


def _sentences(markdown: str) -> list[str]:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in markdown.splitlines():
        if re.match(r"^#{1,6}\s+", line.strip()) or not line.strip():
            if current:
                paragraphs.append(" ".join(part.strip() for part in current))
                current = []
            continue
        current.append(line)
    if current:
        paragraphs.append(" ".join(part.strip() for part in current))
    result: list[str] = []
    for paragraph in paragraphs:
        result.extend(
            part.strip()
            for part in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9`*_])", paragraph)
            if part.strip()
        )
    return result


def _planned_spec_action(
    *,
    common: dict[str, Any],
    markdown: str,
    root: Path,
    run_id: str,
    spec: PlannedExperimentSpec | ProofObligationSpec | RetrievalExpansionRequest,
    path: str,
    artifact_spec: ArtifactWriteSpec,
    specs: list[ArtifactWriteSpec],
) -> AutonomousPlanExecutionAction:
    spec_fp = planned_spec_fingerprint(spec)
    existing = find_existing_planned_spec(run_id=run_id, root=root, spec=spec)
    if existing is not None:
        existing_path, existing_fp = existing
        return AutonomousPlanExecutionAction(
            **common,
            execution_decision="skip",
            execution_status="skipped",
            applied=False,
            planned_spec_fingerprint_optional=existing_fp,
            rejected_reason_optional=(
                "Equivalent planned spec already exists; reused existing spec."
            ),
            created_artifact_path_optional=existing_path,
            after_hash_optional=sha256_text(markdown),
        )
    specs.append(artifact_spec)
    return AutonomousPlanExecutionAction(
        **common,
        execution_decision="apply",
        execution_status="completed",
        applied=True,
        planned_spec_fingerprint_optional=spec_fp,
        created_artifact_path_optional=path,
        after_hash_optional=sha256_text(markdown),
    )


def _failed_action(
    common: dict[str, Any], markdown: str, reason: str
) -> AutonomousPlanExecutionAction:
    return AutonomousPlanExecutionAction(
        **common,
        execution_decision="defer",
        execution_status="failed",
        applied=False,
        deferred_reason_optional=reason,
        after_hash_optional=sha256_text(markdown),
    )


def _spec_stem(prefix: str, item: AutonomousEvidenceGapPlanItem, execution_id: str) -> str:
    target = item.target_claim_id_optional or item.item_id
    slug = "-".join(re.findall(r"[a-z0-9]+", target.casefold()))[:80] or item.item_id
    return f"{prefix}-{slug}-{execution_id}"


def _query_terms(item: AutonomousEvidenceGapPlanItem) -> list[str]:
    text = " ".join(
        filter(
            None,
            [
                item.target_section_optional,
                item.target_claim_id_optional,
                item.recommended_action,
                item.rationale,
                *item.required_inputs,
            ],
        )
    )
    terms = [
        token
        for token in re.findall(r"[a-z][a-z0-9-]{3,}", text.casefold())
        if token not in {"claim", "current", "requires", "support", "source"}
    ]
    return list(dict.fromkeys(terms))[:12] or ["bounded background context"]


def _strategy_experiment_dataset(item: AutonomousEvidenceGapPlanItem) -> str:
    if item.strategy_family == "experiment_dataset_variant":
        return "built-in fixed-seed synthetic calibration template"
    return "local deterministic fixture or explicitly configured dataset"


def _strategy_experiment_metrics(item: AutonomousEvidenceGapPlanItem) -> list[str]:
    if item.strategy_family == "experiment_metric_variant":
        return ["bounded robustness metric", "failure-rate diagnostic"]
    return ["primary bounded result metric", "failure/robustness metric"]


def _strategy_experiment_baselines(item: AutonomousEvidenceGapPlanItem) -> list[str]:
    if item.strategy_family == "experiment_baseline_variant":
        return ["deterministic null baseline", "declared deterministic baseline"]
    return ["declared deterministic baseline"]


def _strategy_proof_statement(
    item: AutonomousEvidenceGapPlanItem,
    statement: str,
) -> str:
    if item.strategy_family == "proof_decomposition_variant":
        return f"Decompose into scoped subclaims before checking: {statement}"
    if item.strategy_family == "proof_checker_variant":
        return f"Checker-neutral certificate obligation: {statement}"
    return statement


def _strategy_proof_checker(item: AutonomousEvidenceGapPlanItem) -> str:
    if item.strategy_family == "proof_decomposition_variant":
        return "deterministic local proof-plan decomposition adapter"
    if item.strategy_family == "proof_checker_variant":
        return "deterministic local checker-neutral certificate contract"
    return "explicitly configured local formal proof backend"


def _strategy_proof_artifact_type(item: AutonomousEvidenceGapPlanItem) -> str:
    if item.strategy_family == "proof_decomposition_variant":
        return "proof_plan"
    if item.strategy_family == "proof_checker_variant":
        return "external_certificate contract pending checker"
    return "passed scoped proof artifact"


def _next_required_artifacts(
    actions: list[AutonomousPlanExecutionAction],
) -> list[str]:
    required = []
    for action in actions:
        if action.gap_type == "needs_python_experiment":
            required.append(f"completed scoped experiment artifact for {action.plan_item_id}")
        elif action.gap_type == "needs_formal_proof":
            required.append(f"passed scoped proof artifact for {action.plan_item_id}")
        elif action.gap_type == "needs_retrieval_expansion":
            required.append(f"accepted bounded retrieval records for {action.plan_item_id}")
    return sorted(set(required))


def _metadata(role: str) -> dict[str, Any]:
    return {
        "stage": "autonomous_plan_execution",
        "artifact_role": role,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
    }


def _next_execution_number(reports: Path) -> int:
    numbers = []
    for path in reports.glob("autonomous-plan-execution-index-*.json"):
        if path.name.endswith(".meta.json"):
            continue
        match = re.fullmatch(r"autonomous-plan-execution-index-(\d+)\.json", path.name)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def _execution_report_path(reports: Path, execution_id: str) -> Path:
    number = int(execution_id.rsplit("-", 1)[-1])
    apply_path = reports / f"autonomous-plan-execution-{number:04d}.json"
    if apply_path.is_file():
        return apply_path
    dry_path = reports / f"autonomous-plan-execution-dry-run-{number:04d}.json"
    if dry_path.is_file():
        return dry_path
    raise AutonomousPlanExecutionError(f"Execution report for {execution_id} is missing.")


__all__ = [
    "AutonomousPlanExecutionError",
    "AutonomousPlanExecutionResult",
    "autonomous_execution_summary_fields",
    "execute_autonomous_evidence_plan",
    "inspect_autonomous_plan_execution",
    "latest_autonomous_plan_execution_index_path",
    "latest_autonomous_plan_execution_report",
    "render_autonomous_plan_execution_markdown",
]
