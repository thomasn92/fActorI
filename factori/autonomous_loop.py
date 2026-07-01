"""Deterministic autonomous loop controller for bounded evidence-gap reduction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.autonomous_evidence_plan import (
    autonomous_evidence_plan_summary_fields,
    latest_autonomous_evidence_gap_plan_path,
    persist_autonomous_evidence_gap_plan,
)
from factori.autonomous_plan_execution import (
    execute_autonomous_evidence_plan,
    inspect_autonomous_plan_execution,
)
from factori.claim_evidence import (
    claim_evidence_summary_fields,
    latest_claim_evidence_map_path,
    persist_claim_evidence_map,
)
from factori.hashing import sha256_file
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.planned_spec_execution import (
    execute_planned_specs,
    inspect_planned_spec_execution,
)
from factori.reports import render_full_paper_release_summary
from factori.rerun_policy import validate_ledger_tip
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AutonomousEvidenceGapPlan,
    AutonomousLoopDecision,
    AutonomousLoopIndex,
    AutonomousLoopIterationReport,
    AutonomousLoopRunReport,
    ClaimEvidenceMap,
    ControllerActionType,
    FullPaperReleaseGateConfig,
)

_LOOP_BACKENDS = {"deterministic", "fake", "openai"}


class AutonomousLoopError(RuntimeError):
    """Raised when the autonomous loop cannot proceed safely."""


@dataclass(frozen=True)
class AutonomousLoopResult:
    """Persisted autonomous loop report and index."""

    run_id: str
    report: AutonomousLoopRunReport
    index: AutonomousLoopIndex
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    report_markdown_artifact: ArtifactRef
    index_artifact: ArtifactRef


def run_autonomous_loop(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    loop_backend: str = "deterministic",
    max_iterations: int = 3,
) -> AutonomousLoopResult:
    """Run the deterministic autonomous plan/spec/recheck loop."""
    if loop_backend not in _LOOP_BACKENDS:
        raise AutonomousLoopError("loop backend must be deterministic, fake, or openai")
    if loop_backend == "openai":
        raise AutonomousLoopError(
            "OpenAI autonomous loop control is schema-gated but not implemented in M69."
        )
    if max_iterations < 1:
        raise AutonomousLoopError("max iterations must be at least 1")

    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    reports = run_path / "reports"
    if not run_path.is_dir():
        raise AutonomousLoopError(f"No run directory found for run_id={run_id}.")

    ledger_check = validate_ledger_tip(run_id, root=root_path)
    if ledger_check.blocking_findings:
        raise AutonomousLoopError(
            "Ledger validation has blocking findings; autonomous loop cannot append safely."
        )

    corrupt_map_reason = _claim_map_corruption_reason(root_path, run_id)
    loop_number = _next_loop_number(reports)
    loop_id = f"autonomous-loop-{loop_number:04d}"
    started_at = _now(ledger)
    initial_release_status = _current_release_status(root_path, run_id)
    initial_claim_counts = _current_claim_counts(root_path, run_id)
    initial_gap_counts = _current_gap_counts(root_path, run_id)
    if corrupt_map_reason is not None:
        decision = AutonomousLoopDecision(
            continue_loop=False,
            stop_reason="safety_gate_blocked",
            loop_status="blocked_requires_human_intervention",
            rationale=corrupt_map_reason,
        )
        report = AutonomousLoopRunReport(
            run_id=run_id,
            loop_id=loop_id,
            loop_backend=loop_backend,
            loop_status="blocked_requires_human_intervention",
            max_iterations=max_iterations,
            iterations_completed=0,
            stop_reason="safety_gate_blocked",
            started_at=started_at,
            completed_at=_now(ledger),
            initial_release_status=initial_release_status,
            final_release_status=initial_release_status,
            initial_publication_ready=False,
            final_publication_ready=False,
            initial_claim_evidence_counts=initial_claim_counts,
            final_claim_evidence_counts=initial_claim_counts,
            initial_gap_counts=initial_gap_counts,
            final_gap_counts=initial_gap_counts,
            iterations=[],
            requires_human_intervention=True,
            human_intervention_reason_optional=decision.rationale,
        )
        return _persist_loop_report(
            report=report,
            root=root_path,
            store=store,
            ledger=ledger,
            loop_number=loop_number,
            release_report=None,
        )

    iterations: list[AutonomousLoopIterationReport] = []
    artifacts_created: list[str] = []
    previous_snapshot: _ProgressSnapshot | None = None
    no_progress_streak = 0
    final_decision: AutonomousLoopDecision | None = None
    release_report = None

    for iteration_number in range(1, max_iterations + 1):
        manuscript_before = _hash_if_exists(_preferred_manuscript_path(reports))
        claim_map_before = _hash_if_exists(latest_claim_evidence_map_path(root_path, run_id))

        map_result = persist_claim_evidence_map(
            run_id=run_id,
            root=root_path,
            store=store,
            ledger=ledger,
        )
        artifacts_created.append(map_result.map_artifact.path)
        plan_result = persist_autonomous_evidence_gap_plan(
            run_id=run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            backend="deterministic",
        )
        artifacts_created.append(plan_result.plan_artifact.path)
        if plan_result.plan.requires_human_intervention:
            decision = AutonomousLoopDecision(
                continue_loop=False,
                stop_reason="requires_human_intervention",
                loop_status="blocked_requires_human_intervention",
                rationale=(
                    plan_result.plan.human_intervention_reason_optional
                    or "Autonomous plan requires human intervention."
                ),
            )
            iteration = _iteration_report(
                iteration_number=iteration_number,
                root=root_path,
                run_id=run_id,
                plan=plan_result.plan,
                plan_path=plan_result.plan_artifact.path,
                autonomous_execution_summary=None,
                planned_spec_execution_summary=None,
                planned_spec_report_path=None,
                release_report_path=None,
                reviewer_summary_path=None,
                manuscript_before=manuscript_before,
                decision=decision,
                created_paths=[map_result.map_artifact.path, plan_result.plan_artifact.path],
                claim_map_before=claim_map_before,
            )
            iterations.append(iteration)
            final_decision = decision
            break

        autonomous_execution = execute_autonomous_evidence_plan(
            run_id=run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            execution_mode="apply",
            executor_backend="deterministic",
        )
        artifacts_created.append(autonomous_execution.report_artifact.path)
        artifacts_created.extend(autonomous_execution.report.created_artifact_paths)
        autonomous_summary = inspect_autonomous_plan_execution(run_id=run_id, root=root_path)

        planned_execution = execute_planned_specs(
            run_id=run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            execution_mode="apply",
            spec_executor_backend="deterministic_local",
        )
        artifacts_created.append(planned_execution.report_artifact.path)
        artifacts_created.extend(planned_execution.report.created_artifact_paths)
        planned_summary = inspect_planned_spec_execution(run_id=run_id, root=root_path)

        rebuilt_map = persist_claim_evidence_map(
            run_id=run_id,
            root=root_path,
            store=store,
            ledger=ledger,
        )
        artifacts_created.append(rebuilt_map.map_artifact.path)
        final_plan_result = persist_autonomous_evidence_gap_plan(
            run_id=run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            backend="deterministic",
        )
        artifacts_created.append(final_plan_result.plan_artifact.path)

        release_report = _evaluate_release(run_id, root_path, ledger)
        release_path = _planned_spec_release_path(root_path, run_id, planned_summary)
        reviewer_summary_path = _planned_spec_reviewer_summary_path(
            root_path,
            run_id,
            planned_summary,
        )
        iteration_paths = [
            map_result.map_artifact.path,
            plan_result.plan_artifact.path,
            autonomous_execution.report_artifact.path,
            planned_execution.report_artifact.path,
            rebuilt_map.map_artifact.path,
            final_plan_result.plan_artifact.path,
        ]
        if release_path is not None:
            iteration_paths.append(release_path)
        if reviewer_summary_path is not None:
            iteration_paths.append(reviewer_summary_path)
        artifacts_created.extend(iteration_paths)

        snapshot = _snapshot(
            claim_map=rebuilt_map.claim_evidence_map,
            plan=final_plan_result.plan,
            autonomous_summary=autonomous_summary,
            planned_summary=planned_summary,
            manuscript_before=manuscript_before,
            manuscript_after=_hash_if_exists(_preferred_manuscript_path(reports)),
        )
        meaningful_progress = _meaningful_progress(previous_snapshot, snapshot)
        no_progress_streak = 0 if meaningful_progress else no_progress_streak + 1
        if release_report.decision.status.value == "ReleaseGateFailed":
            decision = AutonomousLoopDecision(
                continue_loop=False,
                stop_reason="safety_gate_blocked",
                loop_status="blocked_requires_human_intervention",
                rationale="Post-iteration release gate failed; autonomous loop stopped safely.",
            )
        else:
            decision = _decide_iteration(
                snapshot=snapshot,
                iteration_number=iteration_number,
                max_iterations=max_iterations,
                no_progress_streak=no_progress_streak,
            )
        iteration = _iteration_report(
            iteration_number=iteration_number,
            root=root_path,
            run_id=run_id,
            plan=final_plan_result.plan,
            plan_path=final_plan_result.plan_artifact.path,
            autonomous_execution_summary=autonomous_summary,
            planned_spec_execution_summary=planned_summary,
            planned_spec_report_path=planned_summary["planned_spec_execution_report_path"],
            release_report_path=release_path,
            reviewer_summary_path=reviewer_summary_path,
            manuscript_before=manuscript_before,
            decision=decision,
            created_paths=iteration_paths,
            claim_map_before=claim_map_before,
        )
        iterations.append(iteration)
        previous_snapshot = snapshot
        final_decision = decision
        if not decision.continue_loop:
            break

    if final_decision is None:
        final_decision = AutonomousLoopDecision(
            continue_loop=False,
            stop_reason="max_iterations_reached",
            loop_status="stopped_max_iterations",
            rationale="Loop reached max_iterations before completing an iteration.",
        )
    final_claim_counts = _current_claim_counts(root_path, run_id)
    final_gap_counts = _current_gap_counts(root_path, run_id)
    final_release_status = (
        release_report.decision.status.value
        if release_report is not None
        else _current_release_status(root_path, run_id)
    )
    report = AutonomousLoopRunReport(
        run_id=run_id,
        loop_id=loop_id,
        loop_backend=loop_backend,
        loop_status=final_decision.loop_status or "failed",
        max_iterations=max_iterations,
        iterations_completed=len(iterations),
        stop_reason=final_decision.stop_reason or "safety_gate_blocked",
        started_at=started_at,
        completed_at=_now(ledger),
        initial_release_status=initial_release_status,
        final_release_status=final_release_status,
        initial_publication_ready=False,
        final_publication_ready=False,
        initial_claim_evidence_counts=initial_claim_counts,
        final_claim_evidence_counts=final_claim_counts,
        initial_gap_counts=initial_gap_counts,
        final_gap_counts=final_gap_counts,
        iterations=iterations,
        artifacts_created=sorted(set(artifacts_created)),
        requires_human_intervention=(
            final_decision.stop_reason in {"requires_human_intervention", "safety_gate_blocked"}
        ),
        human_intervention_reason_optional=(
            final_decision.rationale
            if final_decision.stop_reason in {"requires_human_intervention", "safety_gate_blocked"}
            else None
        ),
    )
    return _persist_loop_report(
        report=report,
        root=root_path,
        store=store,
        ledger=ledger,
        loop_number=loop_number,
        release_report=release_report,
    )


def inspect_autonomous_loop(*, run_id: str, root: str | Path = ".") -> dict[str, Any]:
    """Inspect the latest autonomous loop run without mutation."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    index_path = latest_autonomous_loop_index_path(root_path, run_id)
    if index_path is None:
        raise AutonomousLoopError(f"No autonomous loop found for run_id={run_id}.")
    index = AutonomousLoopIndex.model_validate_json(index_path.read_text(encoding="utf-8"))
    report_path = reports / f"{index.latest_loop_id}.json"
    report = AutonomousLoopRunReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    return {
        **report.model_dump(mode="json"),
        **autonomous_loop_summary_fields(report, index),
        "autonomous_loop_report_path": report_path.relative_to(root_path).as_posix(),
        "autonomous_loop_index_path": index_path.relative_to(root_path).as_posix(),
        "autonomous_loop_index": index.model_dump(mode="json"),
    }


def autonomous_loop_summary_fields(
    report: AutonomousLoopRunReport | None,
    index: AutonomousLoopIndex | None = None,
) -> dict[str, Any]:
    """Return stable inspect/lint fields for the autonomous loop controller."""
    if report is None:
        return {
            "autonomous_loop_present": False,
            "autonomous_loop_count": 0,
            "latest_autonomous_loop_status": None,
            "latest_autonomous_loop_iterations_completed": 0,
            "latest_autonomous_loop_stop_reason": None,
            "autonomous_loop_final_unsupported_claim_count": 0,
            "autonomous_loop_final_automation_ready_item_count": 0,
            "autonomous_loop_requires_human_intervention": False,
        }
    return {
        "autonomous_loop_present": True,
        "autonomous_loop_count": index.loop_count if index else 1,
        "latest_autonomous_loop_status": report.loop_status,
        "latest_autonomous_loop_iterations_completed": report.iterations_completed,
        "latest_autonomous_loop_stop_reason": report.stop_reason,
        "autonomous_loop_final_unsupported_claim_count": int(
            report.final_claim_evidence_counts.get("unsupported", 0)
        ),
        "autonomous_loop_final_automation_ready_item_count": int(
            report.final_gap_counts.get("automation_ready", 0)
        ),
        "autonomous_loop_requires_human_intervention": report.requires_human_intervention,
    }


def latest_autonomous_loop_index_path(root: Path, run_id: str) -> Path | None:
    """Return the latest immutable autonomous loop index path."""
    reports = root / "runs" / run_id / "reports"
    paths = [
        path
        for path in reports.glob("autonomous-loop-index-*.json")
        if not path.name.endswith(".meta.json")
    ]
    return sorted(paths)[-1] if paths else None


def latest_autonomous_loop_report(
    root: Path,
    run_id: str,
) -> tuple[AutonomousLoopRunReport | None, AutonomousLoopIndex | None]:
    """Load the latest autonomous loop report and index."""
    index_path = latest_autonomous_loop_index_path(root, run_id)
    if index_path is None:
        return None, None
    try:
        index = AutonomousLoopIndex.model_validate_json(index_path.read_text(encoding="utf-8"))
        report = AutonomousLoopRunReport.model_validate_json(
            (root / "runs" / run_id / "reports" / f"{index.latest_loop_id}.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, ValueError):
        return None, None
    return report, index


def render_autonomous_loop_markdown(report: AutonomousLoopRunReport) -> str:
    """Render a concise autonomous loop report."""
    lines = [
        "# Autonomous Loop Report",
        "",
        f"Run ID: `{report.run_id}`",
        f"Loop ID: `{report.loop_id}`",
        f"Backend: `{report.loop_backend}`",
        f"Status: `{report.loop_status}`",
        f"Iterations completed: `{report.iterations_completed}`",
        f"Stop reason: `{report.stop_reason}`",
        f"Final release: `{report.final_release_status}`",
        f"Publication ready: `{str(report.final_publication_ready).lower()}`",
        (
            "Human intervention required: "
            f"`{str(report.requires_human_intervention).lower()}`"
        ),
        "",
        "## Final Gaps",
    ]
    for key, value in sorted(report.final_gap_counts.items()):
        lines.append(f"- `{key}`: `{value}`")
    lines.extend(["", "## Iterations"])
    for iteration in report.iterations:
        lines.append(
            f"- `{iteration.iteration}`: release `{iteration.release_status}`, "
            f"unsupported `{iteration.unsupported_claim_count}`, automation-ready "
            f"`{iteration.automation_ready_item_count}`, decision "
            f"`{iteration.decision.stop_reason or 'continue'}`"
        )
    lines.extend(
        [
            "",
            "## Non-Evidence Boundary",
            "- The autonomous loop is orchestration only.",
            "- Planned specs are not evidence.",
            "- Failed or inconclusive artifacts are not evidence.",
            "- publication_ready: false",
            "- creates_scientific_validation: false",
            "- implies_publication_readiness: false",
            "- is_verification_evidence: false",
            "",
        ]
    )
    return "\n".join(lines)


@dataclass(frozen=True)
class _ProgressSnapshot:
    supported: int
    unsupported: int
    partial: int
    automation_ready: int
    actions_applied: int
    created_spec_count: int
    experiment_artifacts_created: int
    proof_artifacts_created: int
    retrieval_artifacts_created: int
    manuscript_before: str | None
    manuscript_after: str | None


def _snapshot(
    *,
    claim_map: ClaimEvidenceMap,
    plan: AutonomousEvidenceGapPlan,
    autonomous_summary: dict[str, Any],
    planned_summary: dict[str, Any],
    manuscript_before: str | None,
    manuscript_after: str | None,
) -> _ProgressSnapshot:
    claim_summary = claim_evidence_summary_fields(claim_map)
    plan_summary = autonomous_evidence_plan_summary_fields(plan)
    return _ProgressSnapshot(
        supported=int(claim_summary["claim_evidence_supported_count"]),
        unsupported=int(claim_summary["claim_evidence_unsupported_count"]),
        partial=int(claim_summary["claim_evidence_partial_count"]),
        automation_ready=int(plan_summary["automation_ready_item_count"]),
        actions_applied=int(autonomous_summary.get("autonomous_actions_applied") or 0),
        created_spec_count=int(autonomous_summary.get("autonomous_created_spec_count") or 0),
        experiment_artifacts_created=int(planned_summary.get("experiment_artifacts_created") or 0),
        proof_artifacts_created=int(planned_summary.get("proof_artifacts_created") or 0),
        retrieval_artifacts_created=int(planned_summary.get("retrieval_artifacts_created") or 0),
        manuscript_before=manuscript_before,
        manuscript_after=manuscript_after,
    )


def _meaningful_progress(
    previous: _ProgressSnapshot | None,
    current: _ProgressSnapshot,
) -> bool:
    if current.manuscript_before != current.manuscript_after:
        return True
    if current.actions_applied > current.created_spec_count:
        return True
    if current.experiment_artifacts_created > 0:
        return True
    if previous is None:
        return (
            current.unsupported == 0
            or current.automation_ready == 0
            or current.experiment_artifacts_created > 0
        )
    return (
        current.unsupported < previous.unsupported
        or current.partial < previous.partial
        or current.automation_ready < previous.automation_ready
    )


def _decide_iteration(
    *,
    snapshot: _ProgressSnapshot,
    iteration_number: int,
    max_iterations: int,
    no_progress_streak: int,
) -> AutonomousLoopDecision:
    if snapshot.unsupported == 0 and snapshot.automation_ready == 0:
        return AutonomousLoopDecision(
            continue_loop=False,
            stop_reason="no_unsupported_claims",
            loop_status="completed",
            rationale=(
                "All non-scaffold claims are supported within scope and no automation-ready "
                "plan items remain."
            ),
        )
    if snapshot.unsupported == 0 and snapshot.automation_ready > 0 and no_progress_streak >= 2:
        return AutonomousLoopDecision(
            continue_loop=False,
            stop_reason="no_progress",
            loop_status="stopped_no_progress",
            rationale=(
                "Only repeated executable gaps remain, and two consecutive iterations "
                "made no meaningful evidence, manuscript, or gap-count progress."
            ),
        )
    if snapshot.unsupported == 0 and snapshot.automation_ready == 0:
        return AutonomousLoopDecision(
            continue_loop=False,
            stop_reason="no_automation_ready_items",
            loop_status="completed_with_deferred_gaps",
            rationale="No automation-ready items remain.",
        )
    if no_progress_streak >= 2:
        return AutonomousLoopDecision(
            continue_loop=False,
            stop_reason="no_progress",
            loop_status="stopped_no_progress",
            rationale=(
                "Two consecutive iterations made no meaningful evidence, manuscript, "
                "or gap-count progress."
            ),
        )
    if iteration_number >= max_iterations:
        return AutonomousLoopDecision(
            continue_loop=False,
            stop_reason="max_iterations_reached",
            loop_status="stopped_max_iterations",
            rationale="The configured maximum iteration count was reached.",
        )
    return AutonomousLoopDecision(
        continue_loop=True,
        rationale="Automation-ready gaps remain and progress policy allows another iteration.",
    )


def _iteration_report(
    *,
    iteration_number: int,
    root: Path,
    run_id: str,
    plan: AutonomousEvidenceGapPlan,
    plan_path: str,
    autonomous_execution_summary: dict[str, Any] | None,
    planned_spec_execution_summary: dict[str, Any] | None,
    planned_spec_report_path: str | None,
    release_report_path: str | None,
    reviewer_summary_path: str | None,
    manuscript_before: str | None,
    decision: AutonomousLoopDecision,
    created_paths: list[str],
    claim_map_before: str | None,
) -> AutonomousLoopIterationReport:
    claim_map_path = latest_claim_evidence_map_path(root, run_id)
    claim_map = _read_claim_map(claim_map_path)
    claim_summary = claim_evidence_summary_fields(claim_map)
    plan_summary = autonomous_evidence_plan_summary_fields(plan)
    autonomous_execution_summary = autonomous_execution_summary or {}
    planned_spec_execution_summary = planned_spec_execution_summary or {}
    manuscript_after = _hash_if_exists(
        _preferred_manuscript_path(root / "runs" / run_id / "reports")
    )
    return AutonomousLoopIterationReport(
        iteration=iteration_number,
        claim_evidence_map_path=(
            claim_map_path.relative_to(root).as_posix() if claim_map_path else None
        ),
        autonomous_plan_path=plan_path,
        autonomous_execution_report_path=autonomous_execution_summary.get(
            "autonomous_execution_report_path"
        ),
        planned_spec_execution_report_path=planned_spec_report_path,
        evidence_aware_refresh_report_path=_latest_refresh_path(root, run_id),
        release_report_path=release_report_path,
        reviewer_summary_path_optional=reviewer_summary_path,
        supported_claim_count=int(claim_summary["claim_evidence_supported_count"]),
        unsupported_claim_count=int(claim_summary["claim_evidence_unsupported_count"]),
        partial_claim_count=int(claim_summary["claim_evidence_partial_count"]),
        automation_ready_item_count=int(plan_summary["automation_ready_item_count"]),
        actions_applied=int(autonomous_execution_summary.get("autonomous_actions_applied") or 0),
        actions_deferred=int(
            autonomous_execution_summary.get("autonomous_actions_deferred") or 0
        ),
        actions_rejected=int(
            autonomous_execution_summary.get("autonomous_actions_rejected") or 0
        ),
        planned_specs_created=int(
            autonomous_execution_summary.get("autonomous_created_spec_count") or 0
        ),
        planned_specs_executed=int(planned_spec_execution_summary.get("specs_executed") or 0),
        experiment_artifacts_created=int(
            planned_spec_execution_summary.get("experiment_artifacts_created") or 0
        ),
        proof_artifacts_created=int(
            planned_spec_execution_summary.get("proof_artifacts_created") or 0
        ),
        retrieval_artifacts_created=int(
            planned_spec_execution_summary.get("retrieval_artifacts_created") or 0
        ),
        manuscript_modified=manuscript_before != manuscript_after,
        release_status=_current_release_status(root, run_id),
        publication_ready=False,
        created_artifact_count=len(set(created_paths)),
        new_evidence_artifact_count=int(
            planned_spec_execution_summary.get("experiment_artifacts_created") or 0
        ),
        new_spec_count=int(autonomous_execution_summary.get("autonomous_created_spec_count") or 0),
        manuscript_hash_before=manuscript_before,
        manuscript_hash_after=manuscript_after,
        claim_evidence_map_hash_before=claim_map_before,
        claim_evidence_map_hash_after=_hash_if_exists(claim_map_path),
        decision=decision,
    )


def _persist_loop_report(
    *,
    report: AutonomousLoopRunReport,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    loop_number: int,
    release_report,
) -> AutonomousLoopResult:
    from factori.full_paper_generation import (  # noqa: PLC0415
        build_reviewer_bundle_summary,
        render_reviewer_bundle_summary_markdown,
    )

    report_id = report.loop_id
    index_id = f"autonomous-loop-index-{loop_number:04d}"
    reviewer_id = f"reviewer-bundle-summary-after-autonomous-loop-{loop_number:04d}"
    release_id = f"full-paper-release-report-after-autonomous-loop-{loop_number:04d}"
    artifact_paths = list(report.artifacts_created)
    artifact_paths.extend(
        [
            f"runs/{report.run_id}/reports/{report_id}.json",
            f"runs/{report.run_id}/reports/{report_id}.md",
            f"runs/{report.run_id}/reports/{index_id}.json",
            f"runs/{report.run_id}/reports/{reviewer_id}.json",
            f"runs/{report.run_id}/reports/{reviewer_id}.md",
        ]
    )
    if release_report is not None:
        artifact_paths.extend(
            [
                f"runs/{report.run_id}/reports/{release_id}.json",
                f"runs/{report.run_id}/reports/{release_id}.md",
            ]
        )
    iteration_specs = [
        ArtifactWriteSpec(
            f"autonomous-loop-iteration-{loop_number:04d}-{iteration.iteration:03d}",
            ArtifactType.REPORT,
            iteration,
            "json",
            _metadata("autonomous_loop_iteration_context"),
        )
        for iteration in report.iterations
    ]
    artifact_paths.extend(
        [
            f"runs/{report.run_id}/reports/autonomous-loop-iteration-"
            f"{loop_number:04d}-{iteration.iteration:03d}.json"
            for iteration in report.iterations
        ]
    )
    report = report.model_copy(update={"artifacts_created": sorted(set(artifact_paths))})
    index = AutonomousLoopIndex(
        run_id=report.run_id,
        latest_loop_id=report.loop_id,
        loop_count=loop_number,
        latest_loop_status=report.loop_status,
        latest_iterations_completed=report.iterations_completed,
        latest_stop_reason=report.stop_reason,
        latest_artifact_paths=report.artifacts_created,
        latest_requires_human_intervention=report.requires_human_intervention,
    )
    reviewer = build_reviewer_bundle_summary(
        run_id=report.run_id,
        root=root,
        release_report=release_report,
        autonomous_loop_report=report,
    )
    specs = [
        *iteration_specs,
        ArtifactWriteSpec(
            report_id,
            ArtifactType.REPORT,
            report,
            "json",
            _metadata("autonomous_loop_context"),
        ),
        ArtifactWriteSpec(
            f"{report_id}-markdown",
            ArtifactType.REPORT,
            render_autonomous_loop_markdown(report),
            "markdown",
            _metadata("autonomous_loop_context"),
            filename_stem=report_id,
        ),
        ArtifactWriteSpec(
            index_id,
            ArtifactType.REPORT,
            index,
            "json",
            _metadata("autonomous_loop_index_context"),
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
        action_type=ControllerActionType.AUTONOMOUS_LOOP_WRITTEN,
        commit_payload={
            "run_id": report.run_id,
            "loop_id": report.loop_id,
            "loop_status": report.loop_status,
            "iterations_completed": report.iterations_completed,
            "stop_reason": report.stop_reason,
            "requires_human_intervention": report.requires_human_intervention,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return AutonomousLoopResult(
        run_id=report.run_id,
        report=report,
        index=index,
        persistence=persistence,
        report_artifact=by_id[report_id],
        report_markdown_artifact=by_id[f"{report_id}-markdown"],
        index_artifact=by_id[index_id],
    )


def _evaluate_release(run_id: str, root: Path, ledger: ResearchLedger):
    from factori.full_paper_release import evaluate_full_paper_release  # noqa: PLC0415

    return evaluate_full_paper_release(
        run_id=run_id,
        root=root,
        ledger=ledger,
        config=FullPaperReleaseGateConfig(run_id=run_id, write_report=False),
    )


def _current_release_status(root: Path, run_id: str) -> str:
    from factori.full_paper_generation import inspect_paper_bundle_summary  # noqa: PLC0415

    try:
        bundle = inspect_paper_bundle_summary(run_id=run_id, root=root)
    except Exception:  # noqa: BLE001
        return "unknown"
    return str(bundle.get("release_status") or "unknown")


def _current_claim_counts(root: Path, run_id: str) -> dict[str, int]:
    claim_map = _read_claim_map(latest_claim_evidence_map_path(root, run_id))
    summary = claim_evidence_summary_fields(claim_map)
    return {
        "supported": int(summary["claim_evidence_supported_count"]),
        "partial": int(summary["claim_evidence_partial_count"]),
        "unsupported": int(summary["claim_evidence_unsupported_count"]),
        "proof_supported": int(summary["proof_supported_claim_count"]),
        "experiment_supported": int(summary["experiment_supported_claim_count"]),
        "citation_supported": int(summary["citation_supported_claim_count"]),
    }


def _current_gap_counts(root: Path, run_id: str) -> dict[str, int]:
    path = latest_autonomous_evidence_gap_plan_path(root, run_id)
    plan = _read_plan(path)
    summary = autonomous_evidence_plan_summary_fields(plan)
    return {
        "plan_items": int(summary["autonomous_plan_item_count"]),
        "automation_ready": int(summary["automation_ready_item_count"]),
        "python_experiment": int(summary["autonomous_python_experiment_item_count"]),
        "formal_proof": int(summary["autonomous_formal_proof_item_count"]),
        "retrieval_expansion": int(summary["autonomous_retrieval_expansion_item_count"]),
        "claim_downgrade": int(summary["autonomous_claim_downgrade_item_count"]),
        "claim_removal": int(summary["autonomous_claim_removal_item_count"]),
    }


def _claim_map_corruption_reason(root: Path, run_id: str) -> str | None:
    path = latest_claim_evidence_map_path(root, run_id)
    if path is None or not path.is_file():
        return None
    try:
        ClaimEvidenceMap.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return (
            "Claim-evidence map is corrupt or unreadable; autonomous loop requires "
            "a valid map or no prior map before rebuilding."
        )
    return None


def _read_claim_map(path: Path | None) -> ClaimEvidenceMap | None:
    if path is None or not path.is_file():
        return None
    try:
        return ClaimEvidenceMap.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_plan(path: Path | None) -> AutonomousEvidenceGapPlan | None:
    if path is None or not path.is_file():
        return None
    try:
        return AutonomousEvidenceGapPlan.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _preferred_manuscript_path(reports: Path) -> Path | None:
    candidates = [
        *sorted(reports.glob("reconciled-manuscript-cycle-*.md"), reverse=True),
        reports / "evidence-aware-refreshed-manuscript-draft.md",
        *sorted(reports.glob("autonomous-revised-manuscript-*.md"), reverse=True),
        reports / "quality-repaired-manuscript-draft.md",
        reports / "revised-manuscript-draft.md",
        reports / "complete-manuscript-draft.md",
    ]
    for path in candidates:
        if path.is_file() and not path.name.endswith(".meta.json"):
            return path
    return None


def _hash_if_exists(path: Path | None) -> str | None:
    return sha256_file(path) if path is not None and path.is_file() else None


def _latest_refresh_path(root: Path, run_id: str) -> str | None:
    path = root / "runs" / run_id / "reports" / "evidence-aware-refresh-report.json"
    return path.relative_to(root).as_posix() if path.is_file() else None


def _planned_spec_release_path(
    root: Path,
    run_id: str,
    planned_summary: dict[str, Any],
) -> str | None:
    count = int(planned_summary.get("planned_spec_execution_count") or 0)
    if count < 1:
        return None
    path = (
        root
        / "runs"
        / run_id
        / "reports"
        / f"full-paper-release-report-after-planned-spec-execution-{count:04d}.json"
    )
    return path.relative_to(root).as_posix() if path.is_file() else None


def _planned_spec_reviewer_summary_path(
    root: Path,
    run_id: str,
    planned_summary: dict[str, Any],
) -> str | None:
    count = int(planned_summary.get("planned_spec_execution_count") or 0)
    if count < 1:
        return None
    path = (
        root
        / "runs"
        / run_id
        / "reports"
        / f"reviewer-bundle-summary-after-planned-spec-execution-{count:04d}.json"
    )
    return path.relative_to(root).as_posix() if path.is_file() else None


def _next_loop_number(reports: Path) -> int:
    numbers: list[int] = []
    for path in reports.glob("autonomous-loop-*.json"):
        if path.name.endswith(".meta.json") or path.name.startswith("autonomous-loop-iteration-"):
            continue
        stem = path.stem
        suffix = stem.rsplit("-", maxsplit=1)[-1]
        if suffix.isdigit():
            numbers.append(int(suffix))
    return (max(numbers) + 1) if numbers else 1


def _now(ledger: ResearchLedger) -> str:
    return ledger.clock.now()


def _metadata(role: str) -> dict[str, Any]:
    return {
        "stage": "autonomous_loop",
        "artifact_role": role,
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }
