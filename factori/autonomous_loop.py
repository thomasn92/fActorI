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
from factori.capability_escalation import (
    CapabilityEscalationError,
    escalate_capabilities,
)
from factori.claim_evidence import (
    claim_evidence_summary_fields,
    latest_claim_evidence_map_path,
    persist_claim_evidence_map,
)
from factori.experiment_template_routing import (
    build_sandbox_budget_report,
    render_sandbox_budget_markdown,
    route_experiment_gaps,
)
from factori.gap_attempts import (
    build_gap_attempt_history,
    latest_gap_attempt_history_path,
    latest_planned_spec_dedup_index_path,
    persist_gap_attempt_artifacts,
)
from factori.gap_strategy_diversification import (
    persist_gap_strategy_diversification,
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
    AutonomousEvidenceGapPlanItem,
    AutonomousLoopDecision,
    AutonomousLoopGapTerminalClassification,
    AutonomousLoopIndex,
    AutonomousLoopIterationReport,
    AutonomousLoopRunReport,
    ClaimEvidenceMap,
    ControllerActionType,
    FullPaperReleaseGateConfig,
    GapAttemptHistory,
    PlannedSpecDedupIndex,
    SandboxBudgetPolicy,
)
from factori.scientific_substrate import latest_selected_scientific_substrate
from factori.substrate_experiment_routing import route_substrate_experiment

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
    max_attempts_per_gap: int = 2,
    enable_strategy_diversification: bool = False,
    enable_experiment_routing: bool = False,
    enable_empirical_demonstration_gaps: bool = False,
    enable_capability_escalation: bool = False,
    python_sandbox_backend: str = "off",
    max_sandbox_runs_per_loop: int = 3,
    max_sandbox_runs_per_iteration: int = 1,
    max_sandbox_seconds_per_loop: int = 120,
    max_sandbox_failures_per_loop: int = 1,
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
    if max_attempts_per_gap < 1:
        raise AutonomousLoopError("max attempts per gap must be at least 1")
    if python_sandbox_backend not in {"off", "uv_local", "fake"}:
        raise AutonomousLoopError("python sandbox backend must be off, uv_local, or fake")
    if min(
        max_sandbox_runs_per_loop,
        max_sandbox_runs_per_iteration,
        max_sandbox_seconds_per_loop,
        max_sandbox_failures_per_loop,
    ) < 0:
        raise AutonomousLoopError("sandbox budgets must be non-negative")

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
            terminal_state="stopped_safety_blocked",
            terminal_state_reason=corrupt_map_reason,
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
            terminal_state="stopped_safety_blocked",
            terminal_state_reason=corrupt_map_reason,
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
    diversification_report_paths: list[str] = []
    strategy_option_count = 0
    selected_strategy_count = 0
    duplicate_strategy_count = 0
    experiment_routing_paths: list[str] = []
    routed_experiment_gap_count = 0
    routed_experiment_spec_count = 0
    empirical_gaps_created = 0
    empirical_gaps_routed = 0
    sandbox_runs_used = 0
    sandbox_failures_used = 0
    sandbox_experiment_artifacts = 0
    sandbox_budget_exhausted = False
    capability_escalation_report_paths: list[str] = []
    capability_escalation_status: str | None = None
    proof_escalation_attempt_count = 0
    retrieval_escalation_attempt_count = 0
    successful_escalation_count = 0
    deferred_after_escalation_count = 0
    budget_policy = SandboxBudgetPolicy(
        max_sandbox_runs_per_loop=max_sandbox_runs_per_loop,
        max_sandbox_runs_per_iteration=max_sandbox_runs_per_iteration,
        max_sandbox_seconds_per_loop=max_sandbox_seconds_per_loop,
        max_sandbox_failures_per_loop=max_sandbox_failures_per_loop,
        max_experiment_artifacts_per_loop=max_sandbox_runs_per_loop,
    )

    for iteration_number in range(1, max_iterations + 1):
        manuscript_before = _hash_if_exists(_preferred_manuscript_path(reports))
        claim_map_before = _hash_if_exists(latest_claim_evidence_map_path(root_path, run_id))

        map_result = persist_claim_evidence_map(
            run_id=run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            enable_empirical_demonstration_gaps=enable_empirical_demonstration_gaps,
        )
        artifacts_created.append(map_result.map_artifact.path)
        plan_result = persist_autonomous_evidence_gap_plan(
            run_id=run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            backend="deterministic",
            max_attempts_per_gap=max_attempts_per_gap,
        )
        artifacts_created.append(plan_result.plan_artifact.path)
        empirical_gaps_created = max(
            empirical_gaps_created,
            plan_result.plan.empirical_demonstration_gap_count,
        )
        if plan_result.plan.requires_human_intervention:
            decision = AutonomousLoopDecision(
                continue_loop=False,
                stop_reason="requires_human_intervention",
                loop_status="blocked_requires_human_intervention",
                rationale=(
                    plan_result.plan.human_intervention_reason_optional
                    or "Autonomous plan requires human intervention."
                ),
                terminal_state="stopped_requires_human_intervention",
                terminal_state_reason=(
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
                iterations_without_progress=0,
            )
            iterations.append(iteration)
            final_decision = decision
            break

        routing_result = None
        substrate_routing_result = None
        routing_report_path = None
        iteration_routed_experiment_count = 0
        iteration_created_experiment_spec_count = 0
        if enable_experiment_routing:
            remaining_loop_runs = max(max_sandbox_runs_per_loop - sandbox_runs_used, 0)
            selected_substrate, _ = latest_selected_scientific_substrate(root_path, run_id)
            if selected_substrate is not None:
                substrate_routing_result = route_substrate_experiment(
                    run_id=run_id,
                    root=root_path,
                    store=store,
                    ledger=ledger,
                )
            if (
                substrate_routing_result is not None
                and substrate_routing_result.report.substrate_experiment_routed
            ):
                substrate_paths = [
                    substrate_routing_result.report_artifact.path,
                    substrate_routing_result.markdown_artifact.path,
                ]
                if substrate_routing_result.spec_artifact is not None:
                    substrate_paths.append(substrate_routing_result.spec_artifact.path)
                experiment_routing_paths.extend(substrate_paths)
                artifacts_created.extend(substrate_paths)
                routing_report_path = substrate_routing_result.report_artifact.path
                iteration_routed_experiment_count = 1
                iteration_created_experiment_spec_count = int(
                    substrate_routing_result.spec_artifact is not None
                )
                routed_experiment_gap_count += 1
                routed_experiment_spec_count += iteration_created_experiment_spec_count
                empirical_gaps_routed += 1
            else:
                routing_result = route_experiment_gaps(
                    run_id=run_id,
                    root=root_path,
                    store=store,
                    ledger=ledger,
                    routing_backend="deterministic",
                    sandbox_budget_remaining=remaining_loop_runs,
                )
                generic_paths = [
                    routing_result.report_artifact.path,
                    routing_result.markdown_artifact.path,
                    routing_result.index_artifact.path,
                    routing_result.registry_artifact.path,
                ]
                experiment_routing_paths.extend(generic_paths)
                artifacts_created.extend(generic_paths)
                routing_report_path = routing_result.report_artifact.path
                iteration_routed_experiment_count = routing_result.report.routed_gap_count
                iteration_created_experiment_spec_count = (
                    routing_result.report.created_experiment_spec_count
                )
                routed_experiment_gap_count += iteration_routed_experiment_count
                routed_experiment_spec_count += iteration_created_experiment_spec_count
                empirical_gaps_routed += routing_result.report.bounded_empirical_gaps_routed

        autonomous_execution = execute_autonomous_evidence_plan(
            run_id=run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            execution_mode="apply",
            executor_backend="deterministic",
            max_attempts_per_gap=max_attempts_per_gap,
        )
        artifacts_created.append(autonomous_execution.report_artifact.path)
        artifacts_created.extend(autonomous_execution.report.created_artifact_paths)
        autonomous_summary = inspect_autonomous_plan_execution(run_id=run_id, root=root_path)
        if enable_empirical_demonstration_gaps:
            post_execution_map = persist_claim_evidence_map(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                enable_empirical_demonstration_gaps=True,
            )
            artifacts_created.append(post_execution_map.map_artifact.path)

        planned_execution = execute_planned_specs(
            run_id=run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            execution_mode="apply",
            spec_executor_backend="deterministic_local",
            python_sandbox_backend=python_sandbox_backend,
            max_sandbox_runs=min(
                max(max_sandbox_runs_per_loop - sandbox_runs_used, 0),
                max_sandbox_runs_per_iteration,
            ),
            max_attempts_per_gap=max_attempts_per_gap,
        )
        iteration_sandbox_attempts = _sandbox_attempt_count(planned_execution.report)
        iteration_sandbox_failures = _sandbox_failure_count(planned_execution.report)
        sandbox_runs_used += iteration_sandbox_attempts
        sandbox_failures_used += iteration_sandbox_failures
        sandbox_experiment_artifacts += planned_execution.report.experiment_artifacts_created
        sandbox_budget_exhausted = (
            sandbox_runs_used >= max_sandbox_runs_per_loop
            or sandbox_failures_used >= max_sandbox_failures_per_loop
            or sandbox_experiment_artifacts >= max_sandbox_runs_per_loop
        )
        history_result = persist_gap_attempt_artifacts(
            run_id=run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            max_attempts_per_gap=max_attempts_per_gap,
        )
        artifacts_created.extend(
            [
                history_result.history_artifact.path,
                history_result.dedup_index_artifact.path,
            ]
        )
        artifacts_created.append(planned_execution.report_artifact.path)
        artifacts_created.extend(planned_execution.report.created_artifact_paths)
        planned_summary = inspect_planned_spec_execution(run_id=run_id, root=root_path)

        rebuilt_map = persist_claim_evidence_map(
            run_id=run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            enable_empirical_demonstration_gaps=enable_empirical_demonstration_gaps,
        )
        artifacts_created.append(rebuilt_map.map_artifact.path)
        diversification_result = None
        if enable_strategy_diversification and history_result.history.exhausted_gap_count > 0:
            diversification_result = persist_gap_strategy_diversification(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                backend="deterministic",
            )
            diversification_paths = [
                diversification_result.report_artifact.path,
                diversification_result.report_markdown_artifact.path,
                diversification_result.index_artifact.path,
            ]
            artifacts_created.extend(diversification_paths)
            diversification_report_paths.append(
                diversification_result.report_artifact.path
            )
            strategy_option_count += diversification_result.report.strategy_option_count
            selected_strategy_count += diversification_result.report.selected_strategy_count
            duplicate_strategy_count += diversification_result.report.duplicate_strategy_count
            history_result = persist_gap_attempt_artifacts(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                max_attempts_per_gap=max_attempts_per_gap,
            )
            artifacts_created.extend(
                [
                    history_result.history_artifact.path,
                    history_result.dedup_index_artifact.path,
                ]
            )
        final_plan_result = persist_autonomous_evidence_gap_plan(
            run_id=run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            backend="deterministic",
            max_attempts_per_gap=max_attempts_per_gap,
        )
        artifacts_created.append(final_plan_result.plan_artifact.path)
        empirical_gaps_created = max(
            empirical_gaps_created,
            final_plan_result.plan.empirical_demonstration_gap_count,
        )
        immediate_strategy_paths: list[str] = []
        if (
            diversification_result is not None
            and diversification_result.report.selected_strategy_count > 0
        ):
            immediate_execution = execute_autonomous_evidence_plan(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                execution_mode="apply",
                executor_backend="deterministic",
                max_attempts_per_gap=max_attempts_per_gap,
            )
            autonomous_summary = inspect_autonomous_plan_execution(
                run_id=run_id,
                root=root_path,
            )
            immediate_planned_execution = execute_planned_specs(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                execution_mode="apply",
                spec_executor_backend="deterministic_local",
                python_sandbox_backend=python_sandbox_backend,
                max_sandbox_runs=min(
                    max(max_sandbox_runs_per_loop - sandbox_runs_used, 0),
                    max_sandbox_runs_per_iteration,
                ),
                max_attempts_per_gap=max_attempts_per_gap,
            )
            sandbox_runs_used += _sandbox_attempt_count(immediate_planned_execution.report)
            sandbox_failures_used += _sandbox_failure_count(
                immediate_planned_execution.report
            )
            sandbox_experiment_artifacts += (
                immediate_planned_execution.report.experiment_artifacts_created
            )
            sandbox_budget_exhausted = (
                sandbox_runs_used >= max_sandbox_runs_per_loop
                or sandbox_failures_used >= max_sandbox_failures_per_loop
                or sandbox_experiment_artifacts >= max_sandbox_runs_per_loop
            )
            planned_summary = inspect_planned_spec_execution(
                run_id=run_id,
                root=root_path,
            )
            history_result = persist_gap_attempt_artifacts(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                max_attempts_per_gap=max_attempts_per_gap,
            )
            rebuilt_map = persist_claim_evidence_map(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                enable_empirical_demonstration_gaps=(
                    enable_empirical_demonstration_gaps
                ),
            )
            final_plan_result = persist_autonomous_evidence_gap_plan(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                backend="deterministic",
                max_attempts_per_gap=max_attempts_per_gap,
            )
            immediate_strategy_paths = [
                immediate_execution.report_artifact.path,
                *immediate_execution.report.created_artifact_paths,
                immediate_planned_execution.report_artifact.path,
                *immediate_planned_execution.report.created_artifact_paths,
                history_result.history_artifact.path,
                history_result.dedup_index_artifact.path,
                rebuilt_map.map_artifact.path,
                final_plan_result.plan_artifact.path,
            ]
            artifacts_created.extend(immediate_strategy_paths)

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
            *immediate_strategy_paths,
        ]
        if diversification_result is not None:
            iteration_paths.extend(
                [
                    diversification_result.report_artifact.path,
                    diversification_result.report_markdown_artifact.path,
                    diversification_result.index_artifact.path,
                ]
            )
        if release_path is not None:
            iteration_paths.append(release_path)
        if reviewer_summary_path is not None:
            iteration_paths.append(reviewer_summary_path)
        if routing_result is not None:
            iteration_paths.extend(
                [
                    routing_result.report_artifact.path,
                    routing_result.markdown_artifact.path,
                    routing_result.index_artifact.path,
                    routing_result.registry_artifact.path,
                ]
            )
        if substrate_routing_result is not None:
            iteration_paths.extend(
                [
                    substrate_routing_result.report_artifact.path,
                    substrate_routing_result.markdown_artifact.path,
                ]
            )
            if substrate_routing_result.spec_artifact is not None:
                iteration_paths.append(substrate_routing_result.spec_artifact.path)
        artifacts_created.extend(iteration_paths)

        snapshot = _snapshot(
            claim_map=rebuilt_map.claim_evidence_map,
            plan=final_plan_result.plan,
            autonomous_summary=autonomous_summary,
            planned_summary=planned_summary,
            manuscript_before=manuscript_before,
            manuscript_after=_hash_if_exists(_preferred_manuscript_path(reports)),
            selected_strategy_count=(
                diversification_result.report.selected_strategy_count
                if diversification_result is not None
                else 0
            ),
            routed_experiment_spec_count=(
                iteration_created_experiment_spec_count
            ),
        )
        terminal_history = build_gap_attempt_history(
            run_id=run_id,
            root=root_path,
            max_attempts_per_gap=max_attempts_per_gap,
            now=_now(ledger),
        )
        terminal = _terminal_summary(
            plan=final_plan_result.plan,
            history=terminal_history,
            dedup_index=_read_planned_spec_dedup_index(
                dedup_path=latest_planned_spec_dedup_index_path(root_path, run_id)
            ),
            sandbox_budget_exhausted=sandbox_budget_exhausted,
            claim_map=rebuilt_map.claim_evidence_map,
        )
        meaningful_progress = _meaningful_progress(previous_snapshot, snapshot)
        no_progress_streak = 0 if meaningful_progress else no_progress_streak + 1
        if release_report.decision.status.value == "ReleaseGateFailed":
            decision = AutonomousLoopDecision(
                continue_loop=False,
                stop_reason="safety_gate_blocked",
                loop_status="blocked_requires_human_intervention",
                rationale="Post-iteration release gate failed; autonomous loop stopped safely.",
                resolved_gap_count=terminal.resolved_gap_count,
                deferred_gap_count=terminal.deferred_gap_count,
                exhausted_gap_count=terminal.exhausted_gap_count,
                duplicate_only_gap_count=terminal.duplicate_only_gap_count,
                blocking_gap_count=terminal.blocking_gap_count,
                automation_ready_after_history_count=(
                    terminal.automation_ready_after_history_count
                ),
                terminal_state="stopped_safety_blocked",
                terminal_state_reason=(
                    "Post-iteration release gate failed; autonomous loop stopped safely."
                ),
                remaining_deferred_reasons=terminal.remaining_deferred_reasons,
                non_automation_ready_reasons=terminal.non_automation_ready_reasons,
                gap_terminal_classifications=list(terminal.classifications),
            )
        else:
            decision = _decide_iteration(
                snapshot=snapshot,
                previous_snapshot=previous_snapshot,
                terminal=terminal,
                iteration_number=iteration_number,
                max_iterations=max_iterations,
                no_progress_streak=no_progress_streak,
            )
        iteration_plan = final_plan_result.plan
        iteration_plan_path = final_plan_result.plan_artifact.path
        capability_result = None
        capability_report_path = None
        if (
            enable_capability_escalation
            and not decision.continue_loop
            and decision.terminal_state == "completed_with_deferred_gaps"
            and (terminal.proof_paths_deferred or terminal.retrieval_paths_deferred)
        ):
            try:
                capability_result = escalate_capabilities(
                    run_id=run_id,
                    root=root_path,
                    store=store,
                    ledger=ledger,
                    allow_network=False,
                    allow_external_proof_tools=False,
                    allow_external_retrieval_tools=False,
                    max_escalation_attempts_per_gap=max_attempts_per_gap,
                    max_escalation_attempts_per_loop=4,
                    max_retrieval_sources_per_escalation=8,
                    max_tool_runtime_seconds=30,
                )
            except CapabilityEscalationError as exc:
                decision = AutonomousLoopDecision(
                    continue_loop=False,
                    stop_reason="safety_gate_blocked",
                    loop_status="blocked_requires_human_intervention",
                    rationale=(
                        "Capability escalation failed closed: "
                        f"{exc}"
                    ),
                    terminal_state="stopped_safety_blocked",
                    terminal_state_reason=(
                        "Capability escalation failed closed before finalizing loop state."
                    ),
                    resolved_gap_count=terminal.resolved_gap_count,
                    deferred_gap_count=terminal.deferred_gap_count,
                    exhausted_gap_count=terminal.exhausted_gap_count,
                    duplicate_only_gap_count=terminal.duplicate_only_gap_count,
                    blocking_gap_count=terminal.blocking_gap_count,
                    automation_ready_after_history_count=(
                        terminal.automation_ready_after_history_count
                    ),
                    remaining_deferred_reasons=terminal.remaining_deferred_reasons,
                    non_automation_ready_reasons=terminal.non_automation_ready_reasons,
                    gap_terminal_classifications=list(terminal.classifications),
                )
            else:
                capability_report_path = capability_result.report_artifact.path
                capability_escalation_report_paths.append(capability_report_path)
                capability_escalation_status = (
                    capability_result.report.escalation_status
                )
                proof_escalation_attempt_count += (
                    capability_result.report.proof_escalation_attempt_count
                )
                retrieval_escalation_attempt_count += (
                    capability_result.report.retrieval_escalation_attempt_count
                )
                successful_escalation_count += (
                    capability_result.report.successful_escalation_count
                )
                deferred_after_escalation_count += (
                    capability_result.report.deferred_after_escalation_count
                )
                capability_paths = [
                    capability_result.policy_artifact.path,
                    capability_result.report_artifact.path,
                    capability_result.report_markdown_artifact.path,
                    capability_result.index_artifact.path,
                    *capability_result.report.created_artifact_paths,
                    *capability_result.report.ingested_artifact_paths,
                ]
                artifacts_created.extend(capability_paths)
                latest_plan_path = latest_autonomous_evidence_gap_plan_path(
                    root_path,
                    run_id,
                )
                latest_claim_map = _read_claim_map(
                    latest_claim_evidence_map_path(root_path, run_id)
                )
                if latest_plan_path is not None and latest_claim_map is not None:
                    latest_plan = AutonomousEvidenceGapPlan.model_validate_json(
                        latest_plan_path.read_text(encoding="utf-8")
                    )
                    terminal_history = build_gap_attempt_history(
                        run_id=run_id,
                        root=root_path,
                        max_attempts_per_gap=max_attempts_per_gap,
                        now=_now(ledger),
                    )
                    terminal = _terminal_summary(
                        plan=latest_plan,
                        history=terminal_history,
                        dedup_index=_read_planned_spec_dedup_index(
                            dedup_path=latest_planned_spec_dedup_index_path(
                                root_path,
                                run_id,
                            )
                        ),
                        sandbox_budget_exhausted=sandbox_budget_exhausted,
                        claim_map=latest_claim_map,
                    )
                    snapshot = _snapshot(
                        claim_map=latest_claim_map,
                        plan=latest_plan,
                        autonomous_summary=autonomous_summary,
                        planned_summary=planned_summary,
                        manuscript_before=manuscript_before,
                        manuscript_after=_hash_if_exists(
                            _preferred_manuscript_path(reports)
                        ),
                        selected_strategy_count=(
                            diversification_result.report.selected_strategy_count
                            if diversification_result is not None
                            else 0
                        ),
                        routed_experiment_spec_count=(
                            iteration_created_experiment_spec_count
                        ),
                    )
                    decision = _decide_iteration(
                        snapshot=snapshot,
                        previous_snapshot=previous_snapshot,
                        terminal=terminal,
                        iteration_number=iteration_number,
                        max_iterations=max_iterations,
                        no_progress_streak=no_progress_streak,
                    )
                    iteration_plan = latest_plan
                    iteration_plan_path = latest_plan_path.relative_to(
                        root_path
                    ).as_posix()
        iteration = _iteration_report(
            iteration_number=iteration_number,
            root=root_path,
            run_id=run_id,
            plan=iteration_plan,
            plan_path=iteration_plan_path,
            autonomous_execution_summary=autonomous_summary,
            planned_spec_execution_summary=planned_summary,
            planned_spec_report_path=planned_summary["planned_spec_execution_report_path"],
            release_report_path=release_path,
            reviewer_summary_path=reviewer_summary_path,
            manuscript_before=manuscript_before,
            decision=decision,
            created_paths=iteration_paths,
            claim_map_before=claim_map_before,
            iterations_without_progress=no_progress_streak,
            strategy_diversification_report_path=(
                diversification_result.report_artifact.path
                if diversification_result is not None
                else None
            ),
            strategy_option_count=(
                diversification_result.report.strategy_option_count
                if diversification_result is not None
                else 0
            ),
            selected_strategy_count=(
                diversification_result.report.selected_strategy_count
                if diversification_result is not None
                else 0
            ),
            experiment_gap_routing_report_path=(
                routing_report_path
            ),
            routed_experiment_gap_count=(
                iteration_routed_experiment_count
            ),
            routed_experiment_spec_count=(
                iteration_created_experiment_spec_count
            ),
            sandbox_budget_runs_used=sandbox_runs_used,
            sandbox_budget_runs_remaining=max(max_sandbox_runs_per_loop - sandbox_runs_used, 0),
            sandbox_budget_exhausted=sandbox_budget_exhausted,
            capability_escalation_report_path=capability_report_path,
            proof_escalation_attempt_count=(
                capability_result.report.proof_escalation_attempt_count
                if capability_result is not None
                else 0
            ),
            retrieval_escalation_attempt_count=(
                capability_result.report.retrieval_escalation_attempt_count
                if capability_result is not None
                else 0
            ),
            successful_escalation_count=(
                capability_result.report.successful_escalation_count
                if capability_result is not None
                else 0
            ),
            deferred_after_escalation_count=(
                capability_result.report.deferred_after_escalation_count
                if capability_result is not None
                else 0
            ),
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
            terminal_state="stopped_max_iterations",
            terminal_state_reason="Loop reached max_iterations before completing an iteration.",
        )
    final_claim_counts = _current_claim_counts(root_path, run_id)
    final_gap_counts = _current_gap_counts(root_path, run_id)
    final_release_status = (
        release_report.decision.status.value
        if release_report is not None
        else _current_release_status(root_path, run_id)
    )
    history_path = latest_gap_attempt_history_path(root_path, run_id)
    dedup_path = latest_planned_spec_dedup_index_path(root_path, run_id)
    latest_history = build_gap_attempt_history(
        run_id=run_id,
        root=root_path,
        max_attempts_per_gap=max_attempts_per_gap,
        now=_now(ledger),
    )
    final_plan_path = latest_autonomous_evidence_gap_plan_path(root_path, run_id)
    final_plan = (
        AutonomousEvidenceGapPlan.model_validate_json(final_plan_path.read_text(encoding="utf-8"))
        if final_plan_path is not None
        else None
    )
    final_claim_map = _read_claim_map(
        latest_claim_evidence_map_path(root_path, run_id)
    )
    final_terminal = (
        _terminal_summary(
            plan=final_plan,
            history=latest_history,
            dedup_index=_read_planned_spec_dedup_index(dedup_path=dedup_path),
            sandbox_budget_exhausted=sandbox_budget_exhausted,
            claim_map=final_claim_map,
        )
        if final_plan is not None
        else _empty_terminal_summary()
    )
    empirical_paths_resolved = max(
        final_terminal.empirical_paths_resolved,
        sum(
            bool(link.supporting_experiment_artifact_ids)
            and link.support_status == "supported_within_scope"
            for link in final_claim_map.links
        ),
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
        gap_attempt_history_path=(
            history_path.relative_to(root_path).as_posix() if history_path else None
        ),
        dedup_index_path=(dedup_path.relative_to(root_path).as_posix() if dedup_path else None),
        duplicate_specs_skipped=sum(iteration.duplicate_specs_skipped for iteration in iterations),
        gaps_marked_exhausted=latest_history.exhausted_gap_count,
        iterations_without_progress=no_progress_streak,
        stopped_due_to_exhausted_gaps=(
            final_decision.stop_reason in {"exhausted_gaps", "deferred_gaps", "no_progress"}
            and latest_history.exhausted_gap_count > 0
        ),
        strategy_diversification_enabled=enable_strategy_diversification,
        strategy_diversification_report_paths=diversification_report_paths,
        strategy_option_count=strategy_option_count,
        selected_strategy_count=selected_strategy_count,
        duplicate_strategy_count=duplicate_strategy_count,
        experiment_routing_enabled=enable_experiment_routing,
        experiment_gap_routing_report_paths=experiment_routing_paths,
        sandbox_budget_report_path=f"runs/{run_id}/reports/sandbox-budget-report-{loop_number:04d}.json",
        sandbox_budget_policy=budget_policy,
        routed_experiment_gap_count=routed_experiment_gap_count,
        routed_experiment_spec_count=routed_experiment_spec_count,
        sandbox_budget_exhausted=sandbox_budget_exhausted,
        sandbox_budget_runs_used=sandbox_runs_used,
        sandbox_budget_runs_remaining=max(max_sandbox_runs_per_loop - sandbox_runs_used, 0),
        empirical_gaps_created=empirical_gaps_created,
        empirical_gaps_routed=empirical_gaps_routed,
        sandbox_experiments_completed=sandbox_experiment_artifacts,
        experiment_artifacts_ingested=sandbox_experiment_artifacts,
        terminal_state=(
            final_decision.terminal_state or "stopped_requires_human_intervention"
        ),
        terminal_state_reason=(
            final_decision.terminal_state_reason or final_decision.rationale
        ),
        resolved_gap_count=final_terminal.resolved_gap_count,
        deferred_gap_count=final_terminal.deferred_gap_count,
        exhausted_gap_count=final_terminal.exhausted_gap_count,
        duplicate_only_gap_count=final_terminal.duplicate_only_gap_count,
        blocking_gap_count=final_terminal.blocking_gap_count,
        automation_ready_after_history_count=(
            final_terminal.automation_ready_after_history_count
        ),
        empirical_paths_resolved=empirical_paths_resolved,
        proof_paths_deferred=final_terminal.proof_paths_deferred,
        retrieval_paths_deferred=final_terminal.retrieval_paths_deferred,
        stopped_before_max_iterations=(
            len(iterations) < max_iterations
            and final_decision.terminal_state != "stopped_max_iterations"
        ),
        capability_escalation_enabled=enable_capability_escalation,
        capability_escalation_report_paths=capability_escalation_report_paths,
        capability_escalation_status=capability_escalation_status,
        proof_escalation_attempt_count=proof_escalation_attempt_count,
        retrieval_escalation_attempt_count=retrieval_escalation_attempt_count,
        successful_escalation_count=successful_escalation_count,
        deferred_after_escalation_count=deferred_after_escalation_count,
        capability_escalation_network_allowed=False,
        capability_escalation_external_tools_allowed=False,
        gap_terminal_classifications=list(final_terminal.classifications),
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
            "autonomous_loop_iterations_without_progress": 0,
            "autonomous_loop_stopped_due_to_exhausted_gaps": False,
            "gap_exhausted_no_progress_count": 0,
            "duplicate_specs_skipped": 0,
            "strategy_diversification_enabled": False,
            "strategy_option_count": 0,
            "selected_strategy_count": 0,
            "duplicate_strategy_count": 0,
            "experiment_routing_enabled": False,
            "routed_experiment_gap_count": 0,
            "routed_experiment_spec_count": 0,
            "sandbox_budget_exhausted": False,
            "sandbox_budget_runs_used": 0,
            "sandbox_budget_runs_remaining": 0,
            "bounded_empirical_gap_count": 0,
            "empirical_gaps_created": 0,
            "empirical_gaps_routed": 0,
            "sandbox_experiments_completed": 0,
            "experiment_artifacts_ingested": 0,
            "autonomous_loop_terminal_state": None,
            "autonomous_loop_terminal_state_reason": None,
            "autonomous_loop_resolved_gap_count": 0,
            "autonomous_loop_deferred_gap_count": 0,
            "autonomous_loop_exhausted_gap_count": 0,
            "autonomous_loop_duplicate_only_gap_count": 0,
            "autonomous_loop_blocking_gap_count": 0,
            "autonomous_loop_automation_ready_after_history_count": 0,
            "autonomous_loop_stopped_before_max_iterations": False,
            "autonomous_loop_empirical_paths_resolved": 0,
            "autonomous_loop_proof_paths_deferred": 0,
            "autonomous_loop_retrieval_paths_deferred": 0,
            "capability_escalation_enabled": False,
            "capability_escalation_status": None,
            "proof_escalation_attempt_count": 0,
            "retrieval_escalation_attempt_count": 0,
            "successful_escalation_count": 0,
            "deferred_after_escalation_count": 0,
            "capability_escalation_network_allowed": False,
            "capability_escalation_external_tools_allowed": False,
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
        "autonomous_loop_iterations_without_progress": report.iterations_without_progress,
        "autonomous_loop_stopped_due_to_exhausted_gaps": report.stopped_due_to_exhausted_gaps,
        "gap_exhausted_no_progress_count": report.gaps_marked_exhausted,
        "duplicate_specs_skipped": report.duplicate_specs_skipped,
        "strategy_diversification_enabled": report.strategy_diversification_enabled,
        "strategy_option_count": report.strategy_option_count,
        "selected_strategy_count": report.selected_strategy_count,
        "duplicate_strategy_count": report.duplicate_strategy_count,
        "experiment_routing_enabled": report.experiment_routing_enabled,
        "routed_experiment_gap_count": report.routed_experiment_gap_count,
        "routed_experiment_spec_count": report.routed_experiment_spec_count,
        "sandbox_budget_exhausted": report.sandbox_budget_exhausted,
        "sandbox_budget_runs_used": report.sandbox_budget_runs_used,
        "sandbox_budget_runs_remaining": report.sandbox_budget_runs_remaining,
        "bounded_empirical_gap_count": report.empirical_gaps_created,
        "empirical_gaps_created": report.empirical_gaps_created,
        "empirical_gaps_routed": report.empirical_gaps_routed,
        "sandbox_experiments_completed": report.sandbox_experiments_completed,
        "experiment_artifacts_ingested": report.experiment_artifacts_ingested,
        "autonomous_loop_terminal_state": report.terminal_state,
        "autonomous_loop_terminal_state_reason": report.terminal_state_reason,
        "autonomous_loop_resolved_gap_count": report.resolved_gap_count,
        "autonomous_loop_deferred_gap_count": report.deferred_gap_count,
        "autonomous_loop_exhausted_gap_count": report.exhausted_gap_count,
        "autonomous_loop_duplicate_only_gap_count": report.duplicate_only_gap_count,
        "autonomous_loop_blocking_gap_count": report.blocking_gap_count,
        "autonomous_loop_automation_ready_after_history_count": (
            report.automation_ready_after_history_count
        ),
        "autonomous_loop_stopped_before_max_iterations": (
            report.stopped_before_max_iterations
        ),
        "autonomous_loop_empirical_paths_resolved": report.empirical_paths_resolved,
        "autonomous_loop_proof_paths_deferred": report.proof_paths_deferred,
        "autonomous_loop_retrieval_paths_deferred": report.retrieval_paths_deferred,
        "capability_escalation_enabled": report.capability_escalation_enabled,
        "capability_escalation_status": report.capability_escalation_status,
        "proof_escalation_attempt_count": report.proof_escalation_attempt_count,
        "retrieval_escalation_attempt_count": report.retrieval_escalation_attempt_count,
        "successful_escalation_count": report.successful_escalation_count,
        "deferred_after_escalation_count": report.deferred_after_escalation_count,
        "capability_escalation_network_allowed": (
            report.capability_escalation_network_allowed
        ),
        "capability_escalation_external_tools_allowed": (
            report.capability_escalation_external_tools_allowed
        ),
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
        f"Terminal state: `{report.terminal_state}`",
        f"Terminal reason: {report.terminal_state_reason}",
        f"Resolved gaps: `{report.resolved_gap_count}`",
        f"Deferred gaps: `{report.deferred_gap_count}`",
        f"Exhausted gaps: `{report.exhausted_gap_count}`",
        f"Duplicate-only gaps: `{report.duplicate_only_gap_count}`",
        f"Blocking gaps: `{report.blocking_gap_count}`",
        (
            "Automation-ready after history: "
            f"`{report.automation_ready_after_history_count}`"
        ),
        (
            "Stopped before max iterations: "
            f"`{str(report.stopped_before_max_iterations).lower()}`"
        ),
        f"Empirical paths resolved: `{report.empirical_paths_resolved}`",
        f"Proof paths deferred: `{report.proof_paths_deferred}`",
        f"Retrieval paths deferred: `{report.retrieval_paths_deferred}`",
        (
            "Capability escalation enabled/status: "
            f"`{str(report.capability_escalation_enabled).lower()}/"
            f"{report.capability_escalation_status or 'none'}`"
        ),
        (
            "Proof/retrieval escalations attempted: "
            f"`{report.proof_escalation_attempt_count}/"
            f"{report.retrieval_escalation_attempt_count}`"
        ),
        (
            "Successful/deferred after escalation: "
            f"`{report.successful_escalation_count}/"
            f"{report.deferred_after_escalation_count}`"
        ),
        (
            "Escalation network/external tools allowed: "
            f"`{str(report.capability_escalation_network_allowed).lower()}/"
            f"{str(report.capability_escalation_external_tools_allowed).lower()}`"
        ),
        f"Final release: `{report.final_release_status}`",
        f"Publication ready: `{str(report.final_publication_ready).lower()}`",
        f"Duplicate specs skipped: `{report.duplicate_specs_skipped}`",
        f"Gaps exhausted/no-progress: `{report.gaps_marked_exhausted}`",
        f"Iterations without progress: `{report.iterations_without_progress}`",
        f"Experiment routing enabled: `{str(report.experiment_routing_enabled).lower()}`",
        f"Routed experiment gaps: `{report.routed_experiment_gap_count}`",
        f"Routed experiment specs: `{report.routed_experiment_spec_count}`",
        f"Bounded empirical gaps created: `{report.empirical_gaps_created}`",
        f"Bounded empirical gaps routed: `{report.empirical_gaps_routed}`",
        f"Sandbox experiments completed: `{report.sandbox_experiments_completed}`",
        f"Experiment artifacts ingested: `{report.experiment_artifacts_ingested}`",
        f"Sandbox budget exhausted: `{str(report.sandbox_budget_exhausted).lower()}`",
        f"Sandbox runs used/remaining: `{report.sandbox_budget_runs_used}/"
        f"{report.sandbox_budget_runs_remaining}`",
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
            f"`{iteration.decision.stop_reason or 'continue'}`, duplicate skips "
            f"`{iteration.duplicate_specs_skipped}`"
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
    duplicate_specs_skipped: int
    exhausted_gap_count: int
    deferred_gap_count: int
    selected_strategy_count: int
    routed_experiment_spec_count: int
    manuscript_before: str | None
    manuscript_after: str | None


@dataclass(frozen=True)
class _TerminalSummary:
    classifications: tuple[AutonomousLoopGapTerminalClassification, ...]
    resolved_gap_count: int
    deferred_gap_count: int
    exhausted_gap_count: int
    duplicate_only_gap_count: int
    blocking_gap_count: int
    automation_ready_after_history_count: int
    empirical_paths_resolved: int
    proof_paths_deferred: int
    retrieval_paths_deferred: int

    @property
    def remaining_deferred_reasons(self) -> list[str]:
        return sorted(
            {
                item.reason
                for item in self.classifications
                if item.terminal_class.startswith("deferred_")
                or item.terminal_class in {"duplicate_only", "noncritical_boundary_gap"}
            }
        )

    @property
    def non_automation_ready_reasons(self) -> list[str]:
        return sorted(
            {
                item.reason
                for item in self.classifications
                if not item.automation_ready_after_history
                and not item.terminal_class.startswith("resolved_")
            }
        )

    @property
    def has_deferred_terminal_work(self) -> bool:
        return any(
            item.terminal_class.startswith("deferred_")
            or item.terminal_class in {"duplicate_only", "noncritical_boundary_gap"}
            for item in self.classifications
        )


def _snapshot(
    *,
    claim_map: ClaimEvidenceMap,
    plan: AutonomousEvidenceGapPlan,
    autonomous_summary: dict[str, Any],
    planned_summary: dict[str, Any],
    manuscript_before: str | None,
    manuscript_after: str | None,
    selected_strategy_count: int = 0,
    routed_experiment_spec_count: int = 0,
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
        duplicate_specs_skipped=(
            int(autonomous_summary.get("duplicate_specs_skipped") or 0)
            + int(planned_summary.get("planned_spec_duplicate_specs_skipped") or 0)
        ),
        exhausted_gap_count=int(plan_summary.get("gap_exhausted_no_progress_count") or 0),
        deferred_gap_count=int(plan_summary.get("remaining_deferred_gap_count") or 0),
        selected_strategy_count=selected_strategy_count,
        routed_experiment_spec_count=routed_experiment_spec_count,
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
    if current.routed_experiment_spec_count > 0:
        return True
    if current.experiment_artifacts_created > 0:
        return True
    if previous is None:
        return (
            current.experiment_artifacts_created > 0
            or current.created_spec_count > 0
            or current.unsupported < 1 and current.automation_ready == 0
        )
    return (
        current.unsupported < previous.unsupported
        or current.partial < previous.partial
        or current.automation_ready < previous.automation_ready
    )


def _sandbox_attempt_count(report) -> int:
    return sum(
        1
        for item in report.items
        if item.spec_type == "experiment_spec"
        and item.executor_decision == "execute"
        and any("uv_local" in note for note in item.safety_notes)
    )


def _sandbox_failure_count(report) -> int:
    return sum(
        1
        for item in report.items
        if item.spec_type == "experiment_spec"
        and item.execution_status == "failed"
        and any("uv_local" in note for note in item.safety_notes)
    )


_EXHAUSTED_GAP_STATUSES = {
    "exhausted_no_progress",
    "exhausted_initial_strategy",
    "exhausted_all_strategies",
    "deferred_after_diversification",
}
_SEMANTIC_GAP_TYPES = {
    "needs_python_experiment",
    "needs_formal_proof",
    "needs_retrieval_expansion",
    "needs_claim_downgrade",
    "needs_claim_removal",
    "needs_manuscript_refresh",
}


def _terminal_summary(
    *,
    plan: AutonomousEvidenceGapPlan,
    history: GapAttemptHistory,
    dedup_index: PlannedSpecDedupIndex | None,
    sandbox_budget_exhausted: bool,
    claim_map: ClaimEvidenceMap | None = None,
) -> _TerminalSummary:
    records = {record.gap_fingerprint: record for record in history.records}
    duplicate_gaps = {
        record.gap_fingerprint
        for record in (dedup_index.duplicate_records if dedup_index else [])
        if record.gap_fingerprint
    }
    classifications: list[AutonomousLoopGapTerminalClassification] = []
    classified_fingerprints: set[str] = set()
    evidence_supported_claim_ids = {
        link.claim_id
        for link in (claim_map.links if claim_map is not None else [])
        if link.support_status == "supported_within_scope"
        and (
            link.supporting_proof_artifact_ids
            or link.supporting_experiment_artifact_ids
            or link.supporting_citation_keys
        )
    }

    for record in history.records:
        if (
            record.current_gap_status != "resolved"
            or record.gap_type not in _SEMANTIC_GAP_TYPES
        ):
            continue
        if record.linked_evidence_artifact_ids or (
            record.target_claim_id_optional in evidence_supported_claim_ids
        ):
            terminal_class = "resolved_by_evidence"
            reason = record.resolution_reason_optional or "Scoped evidence resolved this gap."
        elif record.gap_type == "needs_claim_removal":
            terminal_class = "resolved_by_claim_removal"
            reason = record.resolution_reason_optional or "The unsupported claim was removed."
        else:
            terminal_class = "resolved_by_claim_downgrade"
            reason = record.resolution_reason_optional or (
                "The claim no longer requires the original evidence action after bounded revision."
            )
        classifications.append(
            AutonomousLoopGapTerminalClassification(
                gap_fingerprint=record.gap_fingerprint,
                target_claim_id_optional=record.target_claim_id_optional,
                target_section_optional=record.target_section_optional,
                gap_type=record.gap_type,
                terminal_class=terminal_class,
                blocking=False,
                automation_ready_after_history=False,
                reason=reason,
            )
        )
        classified_fingerprints.add(record.gap_fingerprint)

    ordered_items = sorted(
        plan.plan_items,
        key=lambda item: (
            not bool(item.strategy_fingerprint and item.automation_ready),
            item.item_id,
        ),
    )
    for item in ordered_items:
        if item.gap_type == "sufficiently_supported_for_bounded_draft":
            continue
        gap_fingerprint = item.gap_fingerprint or item.source_gap_fingerprint
        if gap_fingerprint in classified_fingerprints:
            continue
        record = records.get(gap_fingerprint or "")
        status = record.current_gap_status if record is not None else "open"
        exhausted = bool(item.gap_exhausted or status in _EXHAUSTED_GAP_STATUSES)
        duplicated = bool(gap_fingerprint and gap_fingerprint in duplicate_gaps)
        ready = bool(item.automation_ready_after_history or item.automation_ready)
        terminal_class, reason = _terminal_class_for_item(
            item=item,
            status=status,
            exhausted=exhausted,
            duplicated=duplicated,
            sandbox_budget_exhausted=sandbox_budget_exhausted,
            ready=ready,
        )
        if terminal_class in {
            "deferred_exhausted_proof",
            "deferred_exhausted_retrieval",
            "deferred_budget_exhausted",
            "deferred_requires_external_tool",
            "deferred_requires_network",
            "duplicate_only",
        }:
            ready = False
        blocking = bool(
            item.blocking
            and item.current_support_status in {
                "unsupported",
                "partially_supported",
                "blocked_forbidden_claim",
            }
        )
        classifications.append(
            AutonomousLoopGapTerminalClassification(
                gap_fingerprint=gap_fingerprint,
                target_claim_id_optional=item.target_claim_id_optional,
                target_section_optional=item.target_section_optional,
                gap_type=item.gap_type,
                terminal_class=terminal_class,
                blocking=blocking,
                automation_ready_after_history=ready,
                reason=reason,
            )
        )
        if gap_fingerprint:
            classified_fingerprints.add(gap_fingerprint)

    for record in history.records:
        if record.gap_fingerprint in classified_fingerprints:
            continue
        if (
            record.gap_type not in _SEMANTIC_GAP_TYPES
            or record.current_gap_status not in _EXHAUSTED_GAP_STATUSES | {"deferred"}
        ):
            continue
        terminal_class, reason = _terminal_class_for_history_record(record.gap_type)
        classifications.append(
            AutonomousLoopGapTerminalClassification(
                gap_fingerprint=record.gap_fingerprint,
                target_claim_id_optional=record.target_claim_id_optional,
                target_section_optional=record.target_section_optional,
                gap_type=record.gap_type,
                terminal_class=terminal_class,
                blocking=False,
                automation_ready_after_history=False,
                reason=record.exhaustion_reason_optional or reason,
            )
        )

    exhausted_fingerprints = {
        record.gap_fingerprint
        for record in history.records
        if record.current_gap_status in _EXHAUSTED_GAP_STATUSES
    }
    return _TerminalSummary(
        classifications=tuple(classifications),
        resolved_gap_count=sum(
            item.terminal_class.startswith("resolved_") for item in classifications
        ),
        deferred_gap_count=sum(
            item.terminal_class.startswith("deferred_") for item in classifications
        ),
        exhausted_gap_count=len(exhausted_fingerprints),
        duplicate_only_gap_count=sum(
            item.terminal_class == "duplicate_only" for item in classifications
        ),
        blocking_gap_count=sum(item.blocking for item in classifications),
        automation_ready_after_history_count=sum(
            item.automation_ready_after_history for item in classifications
        ),
        empirical_paths_resolved=sum(
            item.terminal_class == "resolved_by_evidence"
            and item.gap_type == "needs_python_experiment"
            for item in classifications
        ),
        proof_paths_deferred=sum(
            item.terminal_class == "deferred_exhausted_proof"
            for item in classifications
        ),
        retrieval_paths_deferred=sum(
            item.terminal_class == "deferred_exhausted_retrieval"
            for item in classifications
        ),
    )


def _empty_terminal_summary() -> _TerminalSummary:
    return _TerminalSummary(
        classifications=(),
        resolved_gap_count=0,
        deferred_gap_count=0,
        exhausted_gap_count=0,
        duplicate_only_gap_count=0,
        blocking_gap_count=0,
        automation_ready_after_history_count=0,
        empirical_paths_resolved=0,
        proof_paths_deferred=0,
        retrieval_paths_deferred=0,
    )


def _read_planned_spec_dedup_index(
    *,
    dedup_path: Path | None,
) -> PlannedSpecDedupIndex | None:
    if dedup_path is None:
        return None
    try:
        return PlannedSpecDedupIndex.model_validate_json(
            dedup_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None


def _terminal_class_for_item(
    *,
    item: AutonomousEvidenceGapPlanItem,
    status: str,
    exhausted: bool,
    duplicated: bool,
    sandbox_budget_exhausted: bool,
    ready: bool,
) -> tuple[str, str]:
    text = " ".join([item.recommended_action, *item.required_inputs]).casefold()
    if item.gap_type == "needs_python_experiment" and sandbox_budget_exhausted:
        return (
            "deferred_budget_exhausted",
            "The loop sandbox budget is exhausted; this experiment action is deferred.",
        )
    if exhausted and not ready and item.gap_type == "needs_formal_proof":
        return (
            "deferred_exhausted_proof",
            "All safe local proof strategies for this gap are exhausted without formal evidence.",
        )
    if exhausted and not ready and item.gap_type == "needs_retrieval_expansion":
        return (
            "deferred_exhausted_retrieval",
            "All safe local retrieval strategies for this gap are exhausted without new support.",
        )
    if duplicated and not ready:
        return (
            "duplicate_only",
            "Only an equivalent previously attempted planned specification remains.",
        )
    if not ready and any(marker in text for marker in ("requires network", "external api")):
        return (
            "deferred_requires_network",
            "The remaining action requires network access, which this loop does not permit.",
        )
    if not ready and any(
        marker in text
        for marker in ("external proof tool", "invoke lean", "requires external tool")
    ):
        return (
            "deferred_requires_external_tool",
            "The remaining action requires an external tool outside this loop policy.",
        )
    if item.blocking and item.current_support_status in {
        "unsupported",
        "partially_supported",
        "blocked_forbidden_claim",
    }:
        return (
            "blocking_unsupported_claim",
            "A support-required claim remains unsupported within the current evidence scope.",
        )
    if not item.blocking:
        return (
            "noncritical_boundary_gap",
            "This bounded workflow gap is noncritical and does not represent "
            "missing claim support.",
        )
    return (
        "unclassified_requires_human_intervention",
        f"The remaining `{status}` gap could not be classified by terminal policy.",
    )


def _terminal_class_for_history_record(gap_type: str) -> tuple[str, str]:
    if gap_type == "needs_formal_proof":
        return "deferred_exhausted_proof", "The proof gap exhausted safe local attempts."
    if gap_type == "needs_retrieval_expansion":
        return (
            "deferred_exhausted_retrieval",
            "The retrieval gap exhausted safe local attempts.",
        )
    return "duplicate_only", "The gap has no remaining non-duplicate local action."


def _decide_iteration(
    *,
    snapshot: _ProgressSnapshot,
    previous_snapshot: _ProgressSnapshot | None,
    terminal: _TerminalSummary,
    iteration_number: int,
    max_iterations: int,
    no_progress_streak: int,
) -> AutonomousLoopDecision:
    changed, unchanged = _progress_explanation(previous_snapshot, snapshot)
    common = {
        "resolved_gap_count": terminal.resolved_gap_count,
        "deferred_gap_count": terminal.deferred_gap_count,
        "exhausted_gap_count": terminal.exhausted_gap_count,
        "duplicate_only_gap_count": terminal.duplicate_only_gap_count,
        "blocking_gap_count": terminal.blocking_gap_count,
        "automation_ready_after_history_count": (
            terminal.automation_ready_after_history_count
        ),
        "changes_this_iteration": changed,
        "unchanged_this_iteration": unchanged,
        "remaining_deferred_reasons": terminal.remaining_deferred_reasons,
        "non_automation_ready_reasons": terminal.non_automation_ready_reasons,
        "gap_terminal_classifications": list(terminal.classifications),
    }
    if (
        snapshot.unsupported == 0
        and terminal.blocking_gap_count == 0
        and terminal.automation_ready_after_history_count == 0
    ):
        if terminal.has_deferred_terminal_work or terminal.exhausted_gap_count > 0:
            reason = (
                "All support-required claims are resolved within scope; remaining proof, "
                "retrieval, budget, duplicate-only, or boundary work is deferred and has no "
                "safe automation-ready action."
            )
            return AutonomousLoopDecision(
                continue_loop=False,
                stop_reason="deferred_gaps",
                loop_status="completed_with_deferred_gaps",
                rationale=reason,
                terminal_state="completed_with_deferred_gaps",
                terminal_state_reason=reason,
                **common,
            )
        reason = "All support-required claims are supported and no deferred gaps remain."
        return AutonomousLoopDecision(
            continue_loop=False,
            stop_reason="no_unsupported_claims",
            loop_status="completed",
            rationale=reason,
            terminal_state="completed_all_supported",
            terminal_state_reason=reason,
            **common,
        )
    if terminal.automation_ready_after_history_count == 0 and snapshot.unsupported > 0:
        reason = (
            "Unsupported claims remain, but history, policy, or budget leaves no safe "
            "automation-ready action. Deferred gaps remain visible."
        )
        return AutonomousLoopDecision(
            continue_loop=False,
            stop_reason="no_progress",
            loop_status="stopped_no_progress",
            rationale=reason,
            terminal_state="stopped_no_progress",
            terminal_state_reason=reason,
            **common,
        )
    if no_progress_streak >= 2:
        reason = (
            "Two consecutive iterations made no meaningful evidence, manuscript, "
            "or gap-count progress."
        )
        return AutonomousLoopDecision(
            continue_loop=False,
            stop_reason="no_progress",
            loop_status="stopped_no_progress",
            rationale=reason,
            terminal_state="stopped_no_progress",
            terminal_state_reason=reason,
            **common,
        )
    if iteration_number >= max_iterations:
        reason = "The configured maximum iteration count was reached."
        return AutonomousLoopDecision(
            continue_loop=False,
            stop_reason="max_iterations_reached",
            loop_status="stopped_max_iterations",
            rationale=reason,
            terminal_state="stopped_max_iterations",
            terminal_state_reason=reason,
            **common,
        )
    return AutonomousLoopDecision(
        continue_loop=True,
        rationale=(
            "Automation-ready gaps remain after attempt-history and budget checks; "
            "progress policy allows another iteration."
        ),
        **common,
    )


def _progress_explanation(
    previous: _ProgressSnapshot | None,
    current: _ProgressSnapshot,
) -> tuple[list[str], list[str]]:
    changed: list[str] = []
    unchanged: list[str] = []
    if current.manuscript_before != current.manuscript_after:
        changed.append("The preferred manuscript hash changed after a safety-clean revision.")
    else:
        unchanged.append("The preferred manuscript hash did not change.")
    if current.experiment_artifacts_created > 0:
        changed.append("A new scoped experiment artifact was created in this iteration.")
    else:
        unchanged.append("No new scoped experiment artifact was created.")
    if previous is not None and current.unsupported < previous.unsupported:
        changed.append("The unsupported claim count decreased.")
    elif previous is not None:
        unchanged.append("The unsupported claim count did not decrease.")
    if current.created_spec_count > 0 or current.routed_experiment_spec_count > 0:
        changed.append("At least one new non-duplicate planned specification was created.")
    else:
        unchanged.append("No new non-duplicate planned specification was created.")
    return changed, unchanged


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
    iterations_without_progress: int,
    strategy_diversification_report_path: str | None = None,
    strategy_option_count: int = 0,
    selected_strategy_count: int = 0,
    experiment_gap_routing_report_path: str | None = None,
    routed_experiment_gap_count: int = 0,
    routed_experiment_spec_count: int = 0,
    sandbox_budget_runs_used: int = 0,
    sandbox_budget_runs_remaining: int = 0,
    sandbox_budget_exhausted: bool = False,
    capability_escalation_report_path: str | None = None,
    proof_escalation_attempt_count: int = 0,
    retrieval_escalation_attempt_count: int = 0,
    successful_escalation_count: int = 0,
    deferred_after_escalation_count: int = 0,
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
        strategy_diversification_report_path=strategy_diversification_report_path,
        experiment_gap_routing_report_path=experiment_gap_routing_report_path,
        capability_escalation_report_path=capability_escalation_report_path,
        strategy_option_count=strategy_option_count,
        selected_strategy_count=selected_strategy_count,
        routed_experiment_gap_count=routed_experiment_gap_count,
        routed_experiment_spec_count=routed_experiment_spec_count,
        sandbox_budget_runs_used=sandbox_budget_runs_used,
        sandbox_budget_runs_remaining=sandbox_budget_runs_remaining,
        sandbox_budget_exhausted=sandbox_budget_exhausted,
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
        proof_escalation_attempt_count=proof_escalation_attempt_count,
        retrieval_escalation_attempt_count=retrieval_escalation_attempt_count,
        successful_escalation_count=successful_escalation_count,
        deferred_after_escalation_count=deferred_after_escalation_count,
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
        duplicate_specs_skipped=(
            int(autonomous_execution_summary.get("duplicate_specs_skipped") or 0)
            + int(planned_spec_execution_summary.get("planned_spec_duplicate_specs_skipped") or 0)
        ),
        gaps_marked_exhausted=int(plan_summary.get("gap_exhausted_no_progress_count") or 0),
        iterations_without_progress=iterations_without_progress,
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
    budget_id = f"sandbox-budget-report-{loop_number:04d}"
    artifact_paths = list(report.artifacts_created)
    artifact_paths.extend(
        [
            f"runs/{report.run_id}/reports/{report_id}.json",
            f"runs/{report.run_id}/reports/{report_id}.md",
            f"runs/{report.run_id}/reports/{index_id}.json",
            f"runs/{report.run_id}/reports/{budget_id}.json",
            f"runs/{report.run_id}/reports/{budget_id}.md",
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
    budget_report = build_sandbox_budget_report(
        run_id=report.run_id,
        budget_id=budget_id,
        policy=report.sandbox_budget_policy or SandboxBudgetPolicy(),
        runs_used=report.sandbox_budget_runs_used,
        failures_used=0,
        experiment_artifacts_created=sum(
            iteration.experiment_artifacts_created for iteration in report.iterations
        ),
        deferred_reason=(
            "Sandbox run budget exhausted; remaining experiment specs were deferred."
            if report.sandbox_budget_exhausted
            else None
        ),
    )
    report = report.model_copy(
        update={
            "sandbox_budget_report_path": f"runs/{report.run_id}/reports/{budget_id}.json",
        }
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
            budget_id,
            ArtifactType.REPORT,
            budget_report,
            "json",
            _metadata("sandbox_budget_context"),
        ),
        ArtifactWriteSpec(
            f"{budget_id}-markdown",
            ArtifactType.REPORT,
            render_sandbox_budget_markdown(budget_report),
            "markdown",
            _metadata("sandbox_budget_context"),
            filename_stem=budget_id,
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
        "exhausted_no_progress": int(summary.get("gap_exhausted_no_progress_count") or 0),
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
