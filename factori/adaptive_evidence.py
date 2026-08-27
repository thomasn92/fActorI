"""Bounded post-M103 scientific questioning, repair routing, and stopping."""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factori.adapters.adaptive_questioner import (
    AdaptiveQuestionerClient,
    AdaptiveQuestionerDecisionProposal,
)
from factori.adapters.errors import AdapterTransportError
from factori.adapters.hybrid_evidence import HybridEvidenceClient
from factori.adapters.llm_experiment_codegen import ExperimentCodeGenerationClient
from factori.artifacts import ArtifactStore
from factori.hashing import sha256_json
from factori.hybrid_evidence_packages import (
    HybridEvidencePackageError,
    execute_hybrid_evidence_packages,
    plan_hybrid_evidence_packages,
)
from factori.ledger import ResearchLedger
from factori.llm_budget import LLMBudgetExceeded
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.production_mode import stage_backend_record
from factori.schemas import (
    AdaptiveEvidenceDecision,
    AdaptiveEvidenceIteration,
    AdaptiveEvidenceLoopConfig,
    AdaptiveEvidenceLoopReport,
    AdaptiveQuestionerAnswer,
    AdaptiveQuestionerRawArtifact,
    ArtifactRef,
    ArtifactType,
    BackendKind,
    ControllerActionType,
    CrossPackageAdjudicationReport,
    EvidencePackageExecutionReport,
    HybridEvidencePackageConfig,
    HybridEvidencePackageReport,
    ScientificStageKind,
    TargetedResearchBrief,
)
from factori.targeted_llm_budget import TargetedLLMBudgetManager

_PACKAGE_RE = re.compile(r"^hybrid-evidence-package-report-(\d{4})\.json$")
_EXECUTION_RE = re.compile(r"^evidence-package-execution-report-(\d{4})\.json$")
_LOOP_RE = re.compile(r"^adaptive-evidence-loop-report-(\d{4})\.json$")
_RAW_RE = re.compile(r"^adaptive-questioner-raw-(\d{4})\.json$")
_DECISION_RE = re.compile(r"^adaptive-evidence-decision-(\d{4})\.json$")
_ITERATION_RE = re.compile(r"^adaptive-evidence-iteration-(\d{4})\.json$")
_ADJUDICATION_RE = re.compile(r"^cross-package-adjudication-report-(\d{4})\.json$")
_TERMINAL_STATUSES = {
    "satisfied_supported",
    "satisfied_negative",
    "stopped_weak_branch",
    "stopped_no_progress",
    "budget_exhausted",
    "blocked",
}

_QUESTION_LIMIT_REASON = "The maximum adaptive questioner iterations were exhausted."
_SCIENTIFIC_REPAIR_TRANSPORT_PREFIX = "Scientific code repair failed closed:"
_MAX_QUESTIONER_CONTEXT_BYTES = 64 * 1024
_DIAGNOSTIC_PRIORITIES = {
    "blocked_code": 10,
    "missing_code_artifact": 20,
    "missing_code_audit": 20,
    "missing_successful_execution": 30,
    "execution_failure": 30,
    "near_universal_fit_failure": 40,
    "non_finite_metric": 40,
    "invalid_required_metric": 50,
    "missing_required_metric": 50,
    "invalid_criteria_output": 55,
    "criteria_inconsistent": 55,
    "negative_control_implementation_failed": 55,
    "negative_control_interpretation_inconsistent": 55,
    "negative_control_provenance_failed": 55,
    "post_critic_execution_repair": 55,
    "negative_control_failed": 60,
    "missing_baseline": 65,
    "required_artifacts_deferred": 70,
    "inconclusive_result": 80,
    "invalid_metric_source": 90,
    "invalid_result_metric_source": 90,
    "local_contracts_passed": 999,
}


class AdaptiveEvidenceError(RuntimeError):
    """Raised when the adaptive loop cannot continue without weakening policy."""


@dataclass(frozen=True)
class AdaptiveEvidenceStageResult:
    run_id: str
    report: AdaptiveEvidenceLoopReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef


@dataclass(frozen=True)
class _Diagnostic:
    code: str
    message: str
    category: str
    blocks_acceptance: bool = True
    terminal_block: bool = False
    target_id: str = "global"
    repair_target_id: str | None = None

    @property
    def defect_id(self) -> str:
        return f"{self.code}:{self.target_id}"

    @property
    def priority(self) -> int:
        return _DIAGNOSTIC_PRIORITIES.get(self.code, 500)

    @property
    def repair_kind(self) -> str:
        if self.terminal_block:
            return "blocked"
        if self.category in {"implementation_fidelity", "numerical_validity"}:
            return "repair_code"
        if self.category in {"baseline_control_adequacy", "evidence_sufficiency"}:
            return "repair_evidence_plan"
        return "blocked"


def adaptive_loop_can_resume(
    prior: AdaptiveEvidenceLoopReport,
    config: AdaptiveEvidenceLoopConfig,
    *,
    latest_execution: EvidencePackageExecutionReport | None = None,
    authorized_timeout_seconds: int | None = None,
    root: Path | None = None,
) -> bool:
    """Return whether explicitly expanded bounds permit another adaptive pass."""
    if config.max_questioner_iterations <= len(prior.iterations):
        return False
    if (
        latest_execution is not None
        and root is not None
        and _post_critic_diagnostics(
            root / "runs" / prior.run_id / "reports",
            latest_execution,
        )
        and config.max_code_repair_calls > prior.code_repair_attempt_count
    ):
        return True
    if (
        latest_execution is not None
        and latest_execution.adjudication_ready
        and prior.iterations
        and latest_execution.report_id
        != prior.iterations[-1].decision.source_execution_report_id
    ):
        return True
    if latest_execution is not None and _can_retry_timed_out_execution(
        latest_execution,
        authorized_timeout_seconds=authorized_timeout_seconds,
    ):
        return True
    if (
        latest_execution is not None
        and _has_budget_deferred_required_artifact(latest_execution)
        and config.max_questioner_iterations > len(prior.iterations)
    ):
        return True
    if (
        latest_execution is not None
        and _code_repair_target(
            latest_execution,
            include_completed=bool(
                prior.iterations
                and prior.iterations[-1].decision.action == "repair_code"
            ),
        )
        is not None
        and config.max_code_repair_calls > prior.code_repair_attempt_count
        and config.max_questioner_iterations > len(prior.iterations)
    ):
        return True
    if prior.status == "budget_exhausted":
        return True
    if _scientific_repair_transport_failed(prior):
        return True
    if prior.status != "stopped_no_progress":
        return False
    if (
        latest_execution is not None
        and latest_execution.incomplete_required_artifact_plan_ids
        and (
            config.max_code_repair_calls > prior.code_repair_attempt_count
            or config.max_plan_repair_calls > prior.plan_repair_attempt_count
        )
    ):
        return True
    if prior.terminal_reason == _QUESTION_LIMIT_REASON:
        return config.max_questioner_iterations > prior.config.max_questioner_iterations
    if not prior.iterations:
        return False
    last_decision = prior.iterations[-1].decision
    if (
        last_decision.source_code_artifact_ids
        and config.max_code_repair_calls > prior.code_repair_attempt_count
    ):
        # A caller may explicitly reopen a generated-code branch after diagnostics or
        # policy evolve, even when the prior terminal action selected a plan repair.
        return True
    last_action = prior.iterations[-1].decision.action
    if last_action == "repair_code":
        return config.max_code_repair_calls > prior.code_repair_attempt_count
    if last_action == "repair_evidence_plan":
        return config.max_plan_repair_calls > prior.plan_repair_attempt_count
    if last_action == "stop_no_progress":
        blocking_answers = [
            item
            for item in last_decision.questions
            if item.blocking and item.status != "pass"
        ]
        repair_action = _repair_action_for_findings(
            blocking_answers,
            [],
            bool(last_decision.source_code_artifact_ids),
        )
        same_diagnostic_repairs = _matching_action_attempts(
            prior.iterations,
            fingerprint=last_decision.diagnostic_fingerprint,
            action=repair_action,
            root=root,
        )
        if same_diagnostic_repairs < config.no_progress_limit:
            if (
                repair_action == "repair_code"
                and config.max_code_repair_calls > prior.code_repair_attempt_count
            ):
                return True
            if (
                repair_action == "repair_evidence_plan"
                and config.max_plan_repair_calls > prior.plan_repair_attempt_count
            ):
                return True
        return config.no_progress_limit > prior.config.no_progress_limit
    return False


def _scientific_repair_transport_failed(prior: AdaptiveEvidenceLoopReport) -> bool:
    return (
        prior.status == "blocked"
        and prior.terminal_reason.startswith(_SCIENTIFIC_REPAIR_TRANSPORT_PREFIX)
        and "Adapter transport failed;" in prior.terminal_reason
    )


def _caused_by_transport_error(error: BaseException) -> bool:
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        if isinstance(current, AdapterTransportError):
            return True
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return False


def _iteration_counts_for_no_progress(
    iteration: AdaptiveEvidenceIteration,
    *,
    root: Path | None = None,
) -> bool:
    """Count only repairs that reached an accepted code artifact or sandbox run."""
    if iteration.decision.action == "repair_code":
        report_path = iteration.produced_execution_report_path_optional
        if report_path is None:
            return False
        if root is None:
            return True
        path = Path(report_path)
        if not path.is_absolute():
            path = root / path
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return True
        return bool(
            payload.get("code_artifact_count", 0)
            or payload.get("sandbox_executions", [])
        )
    if iteration.decision.action == "repair_evidence_plan":
        return any(
            (
                iteration.produced_package_report_path_optional,
                iteration.produced_execution_report_path_optional,
            )
        )
    return True


def _matching_action_attempts(
    iterations: list[AdaptiveEvidenceIteration],
    *,
    fingerprint: str,
    action: str,
    root: Path | None = None,
) -> int:
    return sum(
        item.before_fingerprint == fingerprint
        and item.decision.action == action
        and _iteration_counts_for_no_progress(item, root=root)
        for item in iterations
    )


def _premature_stop_repair_action(
    *,
    proposed_action: str,
    answers: list[AdaptiveQuestionerAnswer],
    diagnostics: list[_Diagnostic],
    has_code: bool,
    iterations: list[AdaptiveEvidenceIteration],
    fingerprint: str,
    config: AdaptiveEvidenceLoopConfig,
    code_attempts: int,
    plan_attempts: int,
    root: Path | None = None,
) -> str | None:
    """Route an unsupported no-progress stop to an available bounded repair."""
    if proposed_action not in {"stop_no_progress", "stop_weak_branch", "blocked"}:
        return None
    if proposed_action == "blocked" and any(
        item.terminal_block for item in diagnostics
    ):
        return None
    blocking_answers = [
        item for item in answers if item.blocking and item.status != "pass"
    ]
    blocking = [item for item in diagnostics if item.blocks_acceptance]
    if not blocking_answers and not blocking:
        return None
    repair_action = _repair_action_for_findings(blocking_answers, blocking, has_code)
    if (
        _matching_action_attempts(
            iterations,
            fingerprint=fingerprint,
            action=repair_action,
            root=root,
        )
        >= config.no_progress_limit
    ):
        return None
    if repair_action == "repair_code" and code_attempts < config.max_code_repair_calls:
        return repair_action
    if (
        repair_action == "repair_evidence_plan"
        and plan_attempts < config.max_plan_repair_calls
    ):
        return repair_action
    return None


def _repair_instructions_for_override(
    answers: list[AdaptiveQuestionerAnswer],
    diagnostics: list[_Diagnostic],
) -> list[str]:
    instructions = []
    for item in answers:
        if not (item.blocking or item.status in {"fail", "unknown"}):
            continue
        recommended = item.recommended_fix_optional or ""
        if recommended.lower().startswith(("do not ", "no further ", "stop ")):
            recommended = ""
        instructions.append(
            recommended
            or "Resolve this blocking finding without changing the scientific target: "
            + item.explanation
        )
    if instructions:
        return instructions
    diagnostic_instructions = [
        item.message for item in diagnostics if item.blocks_acceptance
    ]
    return diagnostic_instructions or [
        "Resolve the blocking implementation finding without changing the scientific target."
    ]


def _can_retry_timed_out_execution(
    report: EvidencePackageExecutionReport,
    *,
    authorized_timeout_seconds: int | None,
) -> bool:
    if authorized_timeout_seconds is None:
        return False
    latest_code = _latest_code_by_spec(report)
    observations = {item.code_artifact_id: item for item in report.sandbox_executions}
    return any(
        (observation := observations.get(code.code_artifact_id)) is not None
        and observation.status == "timed_out"
        and code.timeout_seconds < authorized_timeout_seconds
        for code in latest_code.values()
    )


@dataclass
class _RepairContextPlanner:
    delegate: HybridEvidenceClient
    prior_package: dict[str, Any]
    decision: AdaptiveEvidenceDecision

    @property
    def backend_name(self) -> str:
        return self.delegate.backend_name

    @property
    def backend_kind(self) -> BackendKind:
        return self.delegate.backend_kind

    @property
    def model(self) -> str:
        return self.delegate.model

    @property
    def fallback_used(self) -> bool:
        return self.delegate.fallback_used

    @property
    def fallback_disclosed(self) -> bool:
        return self.delegate.fallback_disclosed

    def plan_package(
        self,
        *,
        prompt_id: str,
        substrate_payload: dict[str, Any],
        route_payload: dict[str, Any] | None,
        retrieval_context_payload: dict[str, Any] | None,
    ) -> Any:
        repair_context = {
            "repair_kind": "adaptive_evidence_plan_repair",
            "repair_of_package": self.prior_package,
            "decision": self.decision.model_dump(mode="json"),
            "repair_constraints": [
                "Preserve the source substrate, central scientific question, and bounded claim "
                "scope.",
                "Repair only invalid baselines, controls, DGP design, metrics, criteria, or "
                "execution contracts identified by the questioner.",
                "Do not select favorable thresholds or redesign around observed metric values.",
                "Treat the repaired package as a post-hoc new attempt requiring fresh code, seeds, "
                "execution, and metrics.",
                "Keep every generated-code artifact self-contained. Merge controls that consume "
                "benchmark-generated records into the benchmark executable, or require the "
                "control artifact to regenerate those synthetic records internally; never depend "
                "on cross-artifact input files.",
            ],
        }
        merged_route = dict(route_payload or {})
        merged_route["adaptive_evidence_plan_repair"] = repair_context
        return self.delegate.plan_package(
            prompt_id=prompt_id,
            substrate_payload=substrate_payload,
            route_payload=merged_route,
            retrieval_context_payload=retrieval_context_payload,
        )

    def draft_artifact(self, **kwargs: Any) -> Any:
        return self.delegate.draft_artifact(**kwargs)


def run_adaptive_evidence_loop(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    brief: TargetedResearchBrief,
    questioner: AdaptiveQuestionerClient,
    planner: HybridEvidenceClient,
    plan_repairer: HybridEvidenceClient,
    code_generator: ExperimentCodeGenerationClient,
    code_repairer: ExperimentCodeGenerationClient,
    budget: TargetedLLMBudgetManager,
    config: AdaptiveEvidenceLoopConfig,
    retrieval_mode: str,
    require_non_fake_backends: bool = True,
    authorized_timeout_seconds: int | None = None,
    authorized_memory_limit_mb: int | None = None,
) -> AdaptiveEvidenceStageResult:
    """Question and repair one package until evidence is trustworthy or bounded stopping fires."""
    allowed_questioner_backends = (
        {BackendKind.LLM_OPENAI}
        if require_non_fake_backends
        else {BackendKind.LLM_OPENAI, BackendKind.LLM_OTHER}
    )
    if questioner.backend_kind not in allowed_questioner_backends:
        raise AdaptiveEvidenceError("Adaptive questioning requires a non-fake LLM backend.")
    if questioner.fallback_used:
        raise AdaptiveEvidenceError("Adaptive questioning forbids deterministic fallback.")

    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    prior = _load_latest_loop_report(reports)
    iterations = list(prior.iterations) if prior is not None else []
    code_attempts = prior.code_repair_attempt_count if prior is not None else 0
    if prior is not None and _scientific_repair_transport_failed(prior):
        code_attempts = max(0, code_attempts - 1)
    code_successes = prior.code_repair_success_count if prior is not None else 0
    plan_attempts = prior.plan_repair_attempt_count if prior is not None else 0
    plan_successes = prior.plan_repair_success_count if prior is not None else 0
    warnings = list(prior.warnings) if prior is not None else []
    latest_execution = (
        _load_latest_execution_report(reports)[1] if prior is not None else None
    )
    if latest_execution is not None:
        latest_execution = recover_historical_code_artifacts(
            latest_execution, reports
        )
    resumable_terminal_stop = (
        prior is not None
        and adaptive_loop_can_resume(
            prior,
            config,
            latest_execution=latest_execution,
            authorized_timeout_seconds=authorized_timeout_seconds,
            root=root_path,
        )
        and budget.can_spend_optional(1)
    )
    if (
        prior is not None
        and prior.status in _TERMINAL_STATUSES
        and not resumable_terminal_stop
    ):
        replay_report = prior.model_copy(
            update={
                "report_id": (
                    f"adaptive-evidence-loop-report-{_next_number(reports, _LOOP_RE):04d}"
                )
            }
        )
        return _persist_loop_report(
            report=replay_report,
            store=store,
            ledger=ledger,
            raw=None,
            decision=None,
            iteration=None,
        )

    for iteration_number in range(len(iterations) + 1, config.max_questioner_iterations + 1):
        package_path, package_report = _load_latest_package_report(reports)
        execution_path, execution_report = _load_latest_execution_report(reports)
        execution_report = recover_historical_code_artifacts(execution_report, reports)
        if _can_retry_timed_out_execution(
            execution_report,
            authorized_timeout_seconds=authorized_timeout_seconds,
        ):
            try:
                retried = execute_hybrid_evidence_packages(
                    run_id=run_id,
                    root=root_path,
                    store=store,
                    ledger=ledger,
                    planner=planner,
                    code_generator=code_generator,
                    retrieval_mode=retrieval_mode,
                    require_non_fake_backends=require_non_fake_backends,
                    timeout_seconds=authorized_timeout_seconds or 30,
                    memory_limit_mb=(
                        authorized_memory_limit_mb
                        or _memory_limit_mb(execution_report)
                    ),
                    max_artifact_plans=1,
                    max_codegen_calls=0,
                    max_safety_repair_calls=0,
                    max_runtime_repair_calls=0,
                    max_scientific_repair_calls=0,
                    reuse_compatible_m102_results=False,
                    execution_profile="full",
                    max_replications=execution_report.max_replications,
                    max_resamples=execution_report.max_resamples,
                    max_grid_cells=execution_report.max_grid_cells,
                    resume_previous_execution=True,
                )
            except HybridEvidencePackageError as exc:
                warnings.append(
                    "Deterministic authorized-timeout retry failed closed: " + str(exc)
                )
            else:
                execution_path = root_path / retried.report_artifact.path
                execution_report = retried.report
                warnings.append(
                    "Re-executed byte-identical audited code at the configured authorized "
                    f"timeout of {authorized_timeout_seconds} seconds."
                )
        if (
            _has_budget_deferred_required_artifact(execution_report)
            and budget.can_spend_optional(1)
        ):
            try:
                continued = execute_hybrid_evidence_packages(
                    run_id=run_id,
                    root=root_path,
                    store=store,
                    ledger=ledger,
                    planner=planner,
                    code_generator=code_generator,
                    retrieval_mode=retrieval_mode,
                    require_non_fake_backends=require_non_fake_backends,
                    timeout_seconds=_timeout_seconds(execution_report),
                    memory_limit_mb=_memory_limit_mb(execution_report),
                    max_artifact_plans=1,
                    max_codegen_calls=1,
                    max_safety_repair_calls=0,
                    max_runtime_repair_calls=0,
                    max_scientific_repair_calls=0,
                    reuse_compatible_m102_results=False,
                    execution_profile="full",
                    max_replications=execution_report.max_replications,
                    max_resamples=execution_report.max_resamples,
                    max_grid_cells=execution_report.max_grid_cells,
                    resume_previous_execution=True,
                )
            except (HybridEvidencePackageError, LLMBudgetExceeded) as exc:
                warnings.append(
                    "Bounded deferred-artifact continuation failed closed: " + str(exc)
                )
            else:
                execution_path = root_path / continued.report_artifact.path
                execution_report = continued.report
                warnings.append(
                    "Continued one budget-deferred required artifact before adaptive review."
                )
        diagnostics = [
            *_diagnose(package_report, execution_report),
            *_post_critic_diagnostics(reports, execution_report),
        ]
        active_defect = _active_defect(diagnostics)
        questions = _selected_questions(
            iteration=iteration_number,
            diagnostics=diagnostics,
            active_defect=active_defect,
        )
        context = _questioner_context(
            brief=brief,
            package_report=package_report,
            execution_report=execution_report,
            diagnostics=diagnostics,
            prior_iterations=iterations,
            active_defect=active_defect,
        )
        fingerprint = _diagnostic_fingerprint(
            package_report,
            execution_report,
            diagnostics=diagnostics,
        )
        before_defect_ids = _blocking_defect_ids(diagnostics)

        if not budget.can_spend_optional(1):
            return _persist_terminal_without_call(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                config=config,
                status="budget_exhausted",
                iterations=iterations,
                code_attempts=code_attempts,
                code_successes=code_successes,
                plan_attempts=plan_attempts,
                plan_successes=plan_successes,
                package_path=package_path,
                execution_path=execution_path,
                budget=budget,
                questioner=questioner,
                terminal_reason="No unreserved LLM budget remained for another questioner pass.",
                warnings=warnings,
            )
        calls_before = budget.usage.total_calls
        cost_before = budget.usage.estimated_cost_usd or 0.0
        try:
            response = questioner.review_evidence(
                prompt_id=f"adaptive-evidence-{iteration_number:04d}",
                questions_payload=questions,
                context_payload=context,
            )
        except LLMBudgetExceeded:
            return _persist_terminal_without_call(
                run_id=run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                config=config,
                status="budget_exhausted",
                iterations=iterations,
                code_attempts=code_attempts,
                code_successes=code_successes,
                plan_attempts=plan_attempts,
                plan_successes=plan_successes,
                package_path=package_path,
                execution_path=execution_path,
                budget=budget,
                questioner=questioner,
                terminal_reason="Runtime budget blocked the adaptive questioner call.",
                warnings=warnings,
            )
        if response.accepted is None or response.rejection_reasons:
            raise AdaptiveEvidenceError(
                "Adaptive questioner returned no valid decision: "
                + "; ".join(response.rejection_reasons)
            )

        decision = _normalize_decision(
            decision_id=(
                f"adaptive-evidence-decision-{_next_number(reports, _DECISION_RE):04d}"
            ),
            run_id=run_id,
            iteration=iteration_number,
            package_report=package_report,
            execution_report=execution_report,
            questions=questions,
            proposal=response.accepted,
            diagnostics=diagnostics,
            active_defect=active_defect,
            fingerprint=fingerprint,
            questioner=questioner,
        )
        process_repair = _premature_stop_repair_action(
            proposed_action=decision.action,
            answers=decision.questions,
            diagnostics=[active_defect] if active_defect is not None else diagnostics,
            has_code=bool(_latest_code_by_spec(execution_report)),
            iterations=iterations,
            fingerprint=fingerprint,
            config=config,
            code_attempts=code_attempts,
            plan_attempts=plan_attempts,
            root=root_path,
        )
        if process_repair is not None:
            repair_instructions = _repair_instructions_for_override(
                decision.questions,
                [active_defect] if active_defect is not None else diagnostics,
            )
            decision = decision.model_copy(
                update={
                    "action": process_repair,
                    "claim_disposition": "inconclusive",
                    "repair_instructions": repair_instructions,
                    "rationale": (
                        "Process validation found no executed same-diagnostic repair. The "
                        "artifact-grounded blocking finding remains eligible for the configured "
                        "bounded repair allowance."
                    ),
                }
            )
        repeated = _matching_action_attempts(
            iterations,
            fingerprint=fingerprint,
            action=decision.action,
            root=root_path,
        )
        if repeated >= config.no_progress_limit:
            decision = decision.model_copy(
                update={
                    "action": "stop_no_progress",
                    "claim_disposition": "deferred",
                    "rationale": (
                        "The same substantive diagnostics and action recurred without progress."
                    ),
                }
            )
        raw = AdaptiveQuestionerRawArtifact(
            raw_artifact_id=f"adaptive-questioner-raw-{_next_number(reports, _RAW_RE):04d}",
            run_id=run_id,
            iteration=iteration_number,
            backend_name=questioner.backend_name,
            model=questioner.model,
            prompt_text=response.prompt_text,
            requested_output_schema=response.requested_output_schema,
            raw_response=response.raw_response,
            accepted_decision_id_optional=decision.decision_id,
            rejection_reasons=response.rejection_reasons,
            fallback_used=questioner.fallback_used,
        )
        _persist_questioner_decision(
            run_id=run_id,
            raw=raw,
            decision=decision,
            store=store,
            ledger=ledger,
        )

        produced_package_path: str | None = None
        produced_execution_path: str | None = None
        after_fingerprint: str | None = None
        after_defect_ids: list[str] = []
        progress = False
        action_warnings: list[str] = []
        terminal_status = _terminal_status(decision.action)
        terminal_reason = decision.rationale

        if decision.action == "repair_code":
            if code_attempts >= config.max_code_repair_calls:
                terminal_status = "stopped_no_progress"
                terminal_reason = "The bounded scientific code-repair allowance was exhausted."
            elif not budget.can_spend_optional(2, quality_repair_calls=2):
                terminal_status = "budget_exhausted"
                terminal_reason = (
                    "Insufficient unreserved budget remained for scientific code repair "
                    "and one bounded runtime repair."
                )
            else:
                code_attempts += 1
                target_plan_id = _code_repair_target(
                    execution_report,
                    include_completed=True,
                    preferred_target_id=(
                        active_defect.repair_target_id or active_defect.target_id
                        if active_defect is not None
                        else None
                    ),
                )
                generate_missing_artifact = bool(
                    target_plan_id is None
                    and execution_report.incomplete_required_artifact_plan_ids
                )
                if target_plan_id is None and not generate_missing_artifact:
                    terminal_status = "blocked"
                    terminal_reason = "No prior executable artifact was available for code repair."
                else:
                    try:
                        repaired = execute_hybrid_evidence_packages(
                            run_id=run_id,
                            root=root_path,
                            store=store,
                            ledger=ledger,
                            planner=planner,
                            code_generator=(
                                code_generator if generate_missing_artifact else code_repairer
                            ),
                            retrieval_mode=retrieval_mode,
                            require_non_fake_backends=require_non_fake_backends,
                            timeout_seconds=_timeout_seconds(execution_report),
                            memory_limit_mb=_memory_limit_mb(execution_report),
                            max_artifact_plans=1,
                            max_codegen_calls=1 if generate_missing_artifact else 0,
                            max_safety_repair_calls=0,
                            max_runtime_repair_calls=1,
                            max_scientific_repair_calls=(
                                0 if generate_missing_artifact else 1
                            ),
                            scientific_repair_requests=(
                                None
                                if generate_missing_artifact
                                else {
                                    target_plan_id: {
                                        "decision_id": decision.decision_id,
                                        "deterministic_findings": (
                                            decision.deterministic_findings
                                        ),
                                        "questioner_findings": [
                                            item.model_dump(mode="json")
                                            for item in decision.questions
                                            if item.status in {"fail", "unknown"}
                                        ],
                                        "repair_instructions": decision.repair_instructions,
                                        "runtime_diagnostics": _latest_runtime_diagnostics(
                                            execution_report,
                                            root_path,
                                        ),
                                    }
                                }
                            ),
                            reuse_compatible_m102_results=False,
                            execution_profile="full",
                            max_replications=execution_report.max_replications,
                            max_resamples=execution_report.max_resamples,
                            max_grid_cells=execution_report.max_grid_cells,
                            resume_previous_execution=True,
                        )
                    except LLMBudgetExceeded:
                        code_attempts = max(0, code_attempts - 1)
                        terminal_status = "budget_exhausted"
                        terminal_reason = "Runtime budget blocked scientific code repair."
                    except HybridEvidencePackageError as exc:
                        if _caused_by_transport_error(exc):
                            code_attempts = max(0, code_attempts - 1)
                        terminal_status = "blocked"
                        terminal_reason = f"Scientific code repair failed closed: {exc}"
                    else:
                        produced_execution_path = repaired.report_artifact.path
                        repaired_diagnostics = [
                            *_diagnose(package_report, repaired.report),
                            *_post_critic_diagnostics(reports, repaired.report),
                        ]
                        after_defect_ids = _blocking_defect_ids(repaired_diagnostics)
                        after_fingerprint = _diagnostic_fingerprint(
                            package_report,
                            repaired.report,
                            diagnostics=repaired_diagnostics,
                        )
                        progress = _repair_made_progress(
                            active_defect,
                            repaired_diagnostics,
                        )
                        if generate_missing_artifact:
                            selected_ids = set(repaired.report.selected_artifact_plan_ids)
                            code_successes += int(
                                any(
                                    item.artifact_plan_id in selected_ids
                                    and item.execution_completed
                                    for item in repaired.report.results
                                )
                            )
                        else:
                            code_successes += int(
                                repaired.report.scientific_repair_success_count > 0
                            )
        elif decision.action == "repair_evidence_plan":
            if plan_attempts >= config.max_plan_repair_calls:
                terminal_status = "stopped_no_progress"
                terminal_reason = "The bounded evidence-plan repair allowance was exhausted."
            elif not budget.can_spend_optional(3, quality_repair_calls=2):
                terminal_status = "budget_exhausted"
                terminal_reason = (
                    "Insufficient unreserved budget remained for plan repair, rerun, and "
                    "one bounded runtime repair."
                )
            else:
                old_package = package_report.packages[0]
                repair_planner = _RepairContextPlanner(
                    delegate=plan_repairer,
                    prior_package=old_package.model_dump(mode="json"),
                    decision=decision,
                )
                try:
                    replanned = plan_hybrid_evidence_packages(
                        run_id=run_id,
                        root=root_path,
                        store=store,
                        ledger=ledger,
                        planner=repair_planner,
                        config=HybridEvidencePackageConfig(
                            run_id=run_id,
                            backend="llm-openai",
                            max_source_substrates=1,
                            max_planning_calls=1,
                            require_non_fake_backends=require_non_fake_backends,
                            repair_of_package_id_optional=old_package.package_id,
                            adaptive_repair_decision_id_optional=decision.decision_id,
                        ),
                    )
                    plan_attempts += 1
                    produced_package_path = replanned.report_artifact.path
                    execution = execute_hybrid_evidence_packages(
                        run_id=run_id,
                        root=root_path,
                        store=store,
                        ledger=ledger,
                        planner=planner,
                        code_generator=code_generator,
                        retrieval_mode=retrieval_mode,
                        require_non_fake_backends=require_non_fake_backends,
                        timeout_seconds=_timeout_seconds(execution_report),
                        memory_limit_mb=_memory_limit_mb(execution_report),
                        max_artifact_plans=12,
                        max_codegen_calls=min(12, budget.remaining_optional_calls),
                        max_safety_repair_calls=0,
                        max_runtime_repair_calls=1,
                        reuse_compatible_m102_results=False,
                        execution_profile="full",
                        max_replications=execution_report.max_replications,
                        max_resamples=execution_report.max_resamples,
                        max_grid_cells=execution_report.max_grid_cells,
                        resume_previous_execution=True,
                        allow_repaired_package_revision=True,
                    )
                except LLMBudgetExceeded:
                    terminal_status = "budget_exhausted"
                    terminal_reason = "Runtime budget blocked repaired-plan execution."
                except HybridEvidencePackageError as exc:
                    terminal_status = "blocked"
                    terminal_reason = f"Evidence-plan repair failed closed: {exc}"
                else:
                    produced_execution_path = execution.report_artifact.path
                    repaired_diagnostics = [
                        *_diagnose(replanned.report, execution.report),
                        *_post_critic_diagnostics(reports, execution.report),
                    ]
                    after_defect_ids = _blocking_defect_ids(repaired_diagnostics)
                    after_fingerprint = _diagnostic_fingerprint(
                        replanned.report,
                        execution.report,
                        diagnostics=repaired_diagnostics,
                    )
                    progress = _repair_made_progress(
                        active_defect,
                        repaired_diagnostics,
                    )
                    plan_successes += 1

        iteration = AdaptiveEvidenceIteration(
            iteration_id=(
                f"adaptive-evidence-iteration-{_next_number(reports, _ITERATION_RE):04d}"
            ),
            run_id=run_id,
            decision=decision,
            source_package_report_path=_relative(root_path, package_path),
            source_execution_report_path=_relative(root_path, execution_path),
            produced_package_report_path_optional=produced_package_path,
            produced_execution_report_path_optional=produced_execution_path,
            before_fingerprint=fingerprint,
            after_fingerprint_optional=after_fingerprint,
            active_defect_id_optional=(
                active_defect.defect_id if active_defect is not None else None
            ),
            blocking_defect_ids_before=before_defect_ids,
            blocking_defect_ids_after=after_defect_ids,
            resolved_defect_ids=sorted(set(before_defect_ids) - set(after_defect_ids))
            if after_fingerprint is not None
            else [],
            progress_made=progress,
            external_calls_used=budget.usage.total_calls - calls_before,
            estimated_cost_usd=round(
                (budget.usage.estimated_cost_usd or 0.0) - cost_before,
                6,
            ),
            warnings=action_warnings,
        )
        iterations.append(iteration)
        current_package_path, current_package = _load_latest_package_report(reports)
        current_execution_path, current_execution = _load_latest_execution_report(reports)

        if terminal_status is None:
            status = "in_progress"
            terminal_reason = "A bounded repair was executed; re-questioning is required."
        else:
            status = terminal_status
        report = _build_loop_report(
            run_id=run_id,
            reports=reports,
            config=config,
            status=status,
            iterations=iterations,
            code_attempts=code_attempts,
            code_successes=code_successes,
            plan_attempts=plan_attempts,
            plan_successes=plan_successes,
            package_path=current_package_path,
            execution_path=current_execution_path,
            execution=current_execution,
            decision=decision,
            budget=budget,
            questioner=questioner,
            terminal_reason=terminal_reason,
            warnings=[*warnings, *action_warnings],
        )
        persisted = _persist_loop_report(
            report=report,
            store=store,
            ledger=ledger,
            raw=None,
            decision=None,
            iteration=iteration,
        )
        if status != "in_progress":
            return persisted

    package_path, _ = _load_latest_package_report(reports)
    execution_path, execution = _load_latest_execution_report(reports)
    last_decision = iterations[-1].decision
    report = _build_loop_report(
        run_id=run_id,
        reports=reports,
        config=config,
        status="stopped_no_progress",
        iterations=iterations,
        code_attempts=code_attempts,
        code_successes=code_successes,
        plan_attempts=plan_attempts,
        plan_successes=plan_successes,
        package_path=package_path,
        execution_path=execution_path,
        execution=execution,
        decision=last_decision,
        budget=budget,
        questioner=questioner,
        terminal_reason=_QUESTION_LIMIT_REASON,
        warnings=warnings,
    )
    return _persist_loop_report(
        report=report,
        store=store,
        ledger=ledger,
        raw=None,
        decision=None,
        iteration=None,
    )


def _diagnose(
    package: HybridEvidencePackageReport,
    execution: EvidencePackageExecutionReport,
) -> list[_Diagnostic]:
    findings: list[_Diagnostic] = []
    plan_targets = _diagnostic_plan_targets(package)
    latest_code = _latest_code_by_spec(execution)
    code_by_id = {item.code_artifact_id: item for item in latest_code.values()}
    audits = {item.code_artifact_id: item for item in execution.safety_audits}
    sandbox_by_code = {
        item.code_artifact_id: item for item in execution.sandbox_executions
    }
    sandbox_by_execution = {
        item.execution_id: item for item in execution.sandbox_executions
    }

    def plan_target(plan_id: str) -> tuple[str, str]:
        return plan_targets.get(plan_id, (plan_id, plan_id))

    def execution_target(execution_id: str) -> tuple[str, str]:
        sandbox = sandbox_by_execution.get(execution_id)
        code = code_by_id.get(sandbox.code_artifact_id) if sandbox is not None else None
        if code is not None:
            return plan_target(code.source_spec_id)
        return execution_id, execution_id

    code_required_plan_ids = {
        plan.artifact_plan_id
        for candidate in package.packages
        for plan in candidate.artifact_plans
        if plan.requires_code_generation or plan.requires_local_execution
    }
    for plan_id in sorted(code_required_plan_ids - set(latest_code)):
        stable_target, repair_target = plan_target(plan_id)
        findings.append(
            _Diagnostic(
                "missing_code_artifact",
                f"Executable artifact plan {plan_id} has no generated code artifact.",
                "implementation_fidelity",
                target_id=stable_target,
                repair_target_id=repair_target,
            )
        )
    for code in latest_code.values():
        stable_target, repair_target = plan_target(code.source_spec_id)
        audit = audits.get(code.code_artifact_id)
        if audit is None:
            findings.append(
                _Diagnostic(
                    "missing_code_audit",
                    f"Latest code artifact {code.code_artifact_id} has no safety audit.",
                    "implementation_fidelity",
                    terminal_block=True,
                    target_id=stable_target,
                    repair_target_id=repair_target,
                )
            )
        elif audit.blocked:
            findings.append(
                _Diagnostic(
                    "blocked_code",
                    f"Latest code artifact {code.code_artifact_id} is blocked: "
                    + "; ".join(audit.reasons),
                    "implementation_fidelity",
                    target_id=stable_target,
                    repair_target_id=repair_target,
                )
            )
        observation = sandbox_by_code.get(code.code_artifact_id)
        if observation is None or observation.status != "completed":
            findings.append(
                _Diagnostic(
                    "missing_successful_execution",
                    f"Latest code artifact {code.code_artifact_id} has no successful sandbox "
                    "execution.",
                    "implementation_fidelity",
                    target_id=stable_target,
                    repair_target_id=repair_target,
                )
            )
    active_code_ids = set(code_by_id)
    for extraction in execution.metric_extractions:
        extraction_sandbox = sandbox_by_execution.get(extraction.execution_id)
        if (
            extraction_sandbox is not None
            and extraction_sandbox.code_artifact_id not in active_code_ids
        ):
            # Runtime and scientific repairs retain failed attempts for provenance. Their
            # diagnostics must not remain active after a newer artifact for the same plan runs.
            continue
        stable_target, repair_target = execution_target(extraction.execution_id)
        for metric in extraction.missing_metrics:
            findings.append(
                _Diagnostic(
                    "missing_required_metric",
                    f"Metric extraction for {extraction.execution_id} is missing required "
                    f"metric {metric}.",
                    "implementation_fidelity",
                    target_id=f"{stable_target}/{metric}",
                    repair_target_id=repair_target,
                )
            )
        for metric in extraction.invalid_metrics:
            findings.append(
                _Diagnostic(
                    "invalid_required_metric",
                    f"Metric extraction for {extraction.execution_id} rejected required "
                    f"metric {metric}; required metrics and nested metric leaves must be "
                    "finite numeric values, and booleans are invalid.",
                    "implementation_fidelity",
                    target_id=f"{stable_target}/{metric}",
                    repair_target_id=repair_target,
                )
            )
        for metric, value in extraction.metrics.items():
            if not math.isfinite(float(value)):
                findings.append(
                    _Diagnostic(
                        "non_finite_metric",
                        f"Metric {metric} is not finite.",
                        "numerical_validity",
                        terminal_block=True,
                        target_id=f"{stable_target}/{metric}",
                        repair_target_id=repair_target,
                    )
                )
            source = extraction.metric_sources.get(metric, "")
            if "output.json#metrics." not in source:
                findings.append(
                    _Diagnostic(
                        "invalid_metric_source",
                        f"Metric {metric} is not sourced from sandbox output.json.",
                        "evidence_sufficiency",
                        terminal_block=True,
                        target_id=f"{stable_target}/{metric}",
                        repair_target_id=repair_target,
                    )
                )
            normalized = metric.casefold()
            if (
                "convergence_failure" in normalized
                or "nonconvergence" in normalized
                or "fit_failure_rate" in normalized
            ) and float(value) >= 0.95:
                findings.append(
                    _Diagnostic(
                        "near_universal_fit_failure",
                        f"Metric {metric}={value} indicates near-universal fitting failure.",
                        "numerical_validity",
                        target_id=f"{stable_target}/{metric}",
                        repair_target_id=repair_target,
                    )
                )
    for result in execution.results:
        stable_target, repair_target = plan_target(result.artifact_plan_id)
        negative_summary = _summary_mapping(result.negative_control_summary)
        provenance_checks = negative_summary.get("provenance_checks")
        failed_provenance = (
            sorted(key for key, value in provenance_checks.items() if value is False)
            if isinstance(provenance_checks, dict)
            else []
        )
        if failed_provenance:
            integrity_claim = negative_summary.get("integrity_passed")
            contradiction = (
                " while integrity_passed is true" if integrity_claim is True else ""
            )
            findings.append(
                _Diagnostic(
                    "negative_control_provenance_failed",
                    f"Negative-control provenance checks failed for "
                    f"{result.artifact_plan_id}: {', '.join(failed_provenance)}"
                    f"{contradiction}.",
                    "implementation_fidelity",
                    target_id=stable_target,
                    repair_target_id=repair_target,
                )
            )
        interpretation_status = negative_summary.get("interpretation_status")
        if (
            result.status == "negative_result"
            and interpretation_status == "InconclusiveResult"
        ):
            findings.append(
                _Diagnostic(
                    "negative_control_interpretation_inconsistent",
                    f"{result.artifact_plan_id} is classified as a negative result while its "
                    "negative-control interpretation remains InconclusiveResult. Verify whether "
                    "a method that does not consume the perturbed input is being counted as an "
                    "indistinguishability failure; expected invariance must be audited separately "
                    "from methods the negative control is designed to perturb.",
                    "implementation_fidelity",
                    target_id=stable_target,
                    repair_target_id=repair_target,
                )
            )
        for metric, source in result.metric_sources.items():
            if "output.json#metrics." not in source:
                findings.append(
                    _Diagnostic(
                        "invalid_result_metric_source",
                        f"Result metric {metric} is not sourced from sandbox output.json.",
                        "evidence_sufficiency",
                        terminal_block=True,
                        target_id=f"{stable_target}/{metric}",
                        repair_target_id=repair_target,
                    )
                )
        if result.status in {"failed", "blocked_safety_audit"}:
            findings.append(
                _Diagnostic(
                    "execution_failure",
                    f"{result.artifact_plan_id} ended as {result.status}: "
                    f"{result.failure_reason_optional or '; '.join(result.warnings)}",
                    "implementation_fidelity",
                    target_id=stable_target,
                    repair_target_id=repair_target,
                )
            )
        if result.status == "inconclusive":
            criteria_output_invalid = result.execution_completed and any(
                "Output did not resolve success and failure criteria consistently."
                in warning
                for warning in result.warnings
            )
            if criteria_output_invalid:
                findings.append(
                    _Diagnostic(
                        "invalid_criteria_output",
                        f"{result.artifact_plan_id} completed execution but did not emit "
                        "scalar boolean success_criteria_satisfied and "
                        "failure_criteria_satisfied fields.",
                        "implementation_fidelity",
                        target_id=stable_target,
                        repair_target_id=repair_target,
                    )
                )
            else:
                findings.append(
                    _Diagnostic(
                        "inconclusive_result",
                        f"{result.artifact_plan_id} is inconclusive: "
                        f"{'; '.join(result.warnings) or 'criteria unresolved'}",
                        "evidence_sufficiency",
                        target_id=stable_target,
                        repair_target_id=repair_target,
                    )
                )
        if any("Negative controls did not pass" in item for item in result.warnings):
            control_summary = _summary_mapping(result.negative_control_summary)
            attempted = control_summary.get("attempted")
            valid = control_summary.get("valid")
            complete_cells = control_summary.get("complete_cells")
            integrity = control_summary.get("permuted_label_integrity")
            implementation_failure = (
                isinstance(attempted, int)
                and not isinstance(attempted, bool)
                and attempted > 0
                and isinstance(valid, int)
                and not isinstance(valid, bool)
                and valid > 0
                and (
                    complete_cells == 0
                    or integrity == "failed"
                )
            )
            completed_control = (
                isinstance(complete_cells, int)
                and not isinstance(complete_cells, bool)
                and complete_cells > 0
                and integrity == "passed"
            )
            if implementation_failure:
                findings.append(
                    _Diagnostic(
                        "negative_control_implementation_failed",
                        f"Negative-control records for {result.artifact_plan_id} were "
                        "generated but failed completeness or permutation-integrity "
                        "bookkeeping.",
                        "implementation_fidelity",
                        target_id=stable_target,
                        repair_target_id=repair_target,
                    )
                )
            else:
                findings.append(
                    _Diagnostic(
                        "negative_control_failed",
                        f"Negative controls failed for {result.artifact_plan_id}.",
                        "baseline_control_adequacy",
                        terminal_block=completed_control,
                        target_id=stable_target,
                        repair_target_id=repair_target,
                    )
                )
        if result.success_criteria_satisfied is True and result.failure_criteria_satisfied is True:
            findings.append(
                _Diagnostic(
                    "criteria_inconsistent",
                    f"{result.artifact_plan_id} marks success and failure simultaneously.",
                    "evidence_sufficiency",
                    target_id=stable_target,
                    repair_target_id=repair_target,
                )
            )
    for candidate in package.packages:
        for plan in candidate.artifact_plans:
            if plan.requires_code_generation and not plan.baseline_or_comparator_plan:
                stable_target, repair_target = plan_target(plan.artifact_plan_id)
                findings.append(
                    _Diagnostic(
                        "missing_baseline",
                        f"{plan.artifact_plan_id} lacks a baseline or comparator.",
                        "baseline_control_adequacy",
                        target_id=stable_target,
                        repair_target_id=repair_target,
                    )
                )
    if execution.budget_deferred_artifact_count:
        findings.append(
            _Diagnostic(
                "required_artifacts_deferred",
                f"{execution.budget_deferred_artifact_count} package artifacts remain "
                "budget-deferred.",
                "evidence_sufficiency",
            )
        )
    if not findings and execution.adjudication_ready:
        findings.append(
            _Diagnostic(
                "local_contracts_passed",
                "All deterministic execution, provenance, and package-completeness checks passed.",
                "evidence_sufficiency",
                blocks_acceptance=False,
            )
        )
    return list(
        {
            item.defect_id: item
            for item in sorted(findings, key=lambda value: (value.priority, value.defect_id))
        }.values()
    )


def _diagnostic_plan_targets(
    package: HybridEvidencePackageReport,
) -> dict[str, tuple[str, str]]:
    """Map regenerated plan IDs onto stable substrate/type identities."""
    targets: dict[str, tuple[str, str]] = {}
    for candidate in package.packages:
        type_counts: Counter[str] = Counter()
        for plan in candidate.artifact_plans:
            artifact_type = plan.artifact_type.value
            ordinal = type_counts[artifact_type]
            type_counts[artifact_type] += 1
            stable = f"{candidate.source_substrate_id}/{artifact_type}/{ordinal}"
            targets[plan.artifact_plan_id] = (stable, plan.artifact_plan_id)
    return targets


def _blocking_defect_ids(diagnostics: list[_Diagnostic]) -> list[str]:
    return sorted(item.defect_id for item in diagnostics if item.blocks_acceptance)


def _summary_mapping(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _repair_made_progress(
    active_defect: _Diagnostic | None,
    repaired_diagnostics: list[_Diagnostic],
) -> bool:
    """Require a repair to remove the defect without introducing a worse blocker."""
    if active_defect is None:
        return False
    blocking = [item for item in repaired_diagnostics if item.blocks_acceptance]
    if any(item.defect_id == active_defect.defect_id for item in blocking):
        return False
    next_defect = _active_defect(repaired_diagnostics)
    return next_defect is None or next_defect.priority > active_defect.priority


def _active_defect(diagnostics: list[_Diagnostic]) -> _Diagnostic | None:
    blocking = [item for item in diagnostics if item.blocks_acceptance]
    return min(blocking, key=lambda item: (item.priority, item.defect_id), default=None)


def _selected_questions(
    *,
    iteration: int,
    diagnostics: list[_Diagnostic],
    active_defect: _Diagnostic | None,
) -> list[dict[str, Any]]:
    prompts = {
        "implementation_fidelity": (
            "Does the current code and execution resolve the identified implementation defect "
            "while preserving the declared study contract?"
        ),
        "numerical_validity": (
            "Does the current execution resolve the identified numerical defect sufficiently for "
            "the bounded result to be interpreted?"
        ),
        "baseline_control_adequacy": (
            "Does the current design resolve the identified baseline or control defect without "
            "tuning toward a favorable result?"
        ),
        "evidence_sufficiency": (
            "Does the current package resolve the identified evidence defect and answer the "
            "bounded question, including an honest negative answer when appropriate?"
        ),
    }
    focus_category = (
        active_defect.category if active_defect is not None else "evidence_sufficiency"
    )
    focus_id = active_defect.defect_id if active_defect is not None else "acceptance-readiness"
    questions = [
        (
            f"defect-{focus_id}",
            focus_category,
            prompts.get(focus_category, prompts["evidence_sufficiency"]),
        )
    ]
    if iteration > 1 and active_defect is not None:
        questions.append(
            (
                "repair-sufficiency",
                "repair_sufficiency",
                "Did the latest repair actually remove or downgrade this same defect without "
                "changing the scientific target?",
            )
        )
    questions.append(
        (
            "stopping",
            "stopping",
            "Given this focused defect and the remaining bounded budget, should the branch be "
            "accepted, repaired, honestly downgraded, or stopped?",
        )
    )
    diagnostic_text = [
        f"{item.defect_id}: {item.message}"
        for item in diagnostics
        if active_defect is None or item.defect_id == active_defect.defect_id
    ]
    return [
        {
            "question_id": question_id,
            "category": category,
            "question": question,
            "deterministic_context": diagnostic_text,
        }
        for question_id, category, question in questions
    ]


def _questioner_context(
    *,
    brief: TargetedResearchBrief,
    package_report: HybridEvidencePackageReport,
    execution_report: EvidencePackageExecutionReport,
    diagnostics: list[_Diagnostic],
    prior_iterations: list[AdaptiveEvidenceIteration],
    active_defect: _Diagnostic | None = None,
) -> dict[str, Any]:
    latest_code = _latest_code_by_spec(execution_report)
    active_target = (
        active_defect.repair_target_id or active_defect.target_id
        if active_defect is not None
        else None
    )
    relevant_results = [
        item
        for item in execution_report.results
        if active_target in {None, "global", item.artifact_plan_id}
        or (active_target and active_target.startswith(f"{item.artifact_plan_id}/"))
    ] or execution_report.results[:1]
    relevant_code = [
        item
        for spec_id, item in latest_code.items()
        if active_target in {None, "global", spec_id}
        or (active_target and active_target.startswith(f"{spec_id}/"))
    ] or list(latest_code.values())[:1]
    relevant_code_ids = {item.code_artifact_id for item in relevant_code}
    context = {
        "research_brief": {
            "brief_id": brief.brief_id,
            "title": brief.title,
            "central_question": brief.central_question,
            "method": brief.method,
            "baselines": brief.baseline_candidates,
            "metrics": brief.expected_metrics,
            "controls": brief.controls,
            "negative_controls": brief.negative_controls,
            "allowed_claim_scope": brief.allowed_claim_scope,
            "forbidden_claims": brief.forbidden_claims,
        },
        "package_report": _questioner_package_summary(package_report),
        "active_defect": (
            {
                "defect_id": active_defect.defect_id,
                "category": active_defect.category,
                "message": active_defect.message,
                "repair_kind": active_defect.repair_kind,
                "repair_target_id": active_defect.repair_target_id,
                "priority": active_defect.priority,
            }
            if active_defect is not None
            else None
        ),
        "execution_summary": {
            "report_id": execution_report.report_id,
            "execution_profile": execution_report.execution_profile,
            "adjudication_ready": execution_report.adjudication_ready,
            "required_artifact_plan_count": execution_report.required_artifact_plan_count,
            "completed_required_artifact_count": (
                execution_report.completed_required_artifact_count
            ),
            "incomplete_required_artifact_plan_ids": (
                execution_report.incomplete_required_artifact_plan_ids
            ),
            "results": [
                _questioner_result_summary(item) for item in relevant_results[:2]
            ],
            "metric_extractions": [
                {
                    "execution_id": item.execution_id,
                    "metrics_extracted": item.metrics_extracted,
                    "schema_valid": item.schema_valid,
                    "metric_count": len(item.metrics),
                    "metric_source_count": len(item.metric_sources),
                    "missing_metrics": item.missing_metrics,
                    "invalid_metrics": item.invalid_metrics,
                    "extraction_warnings": item.extraction_warnings,
                }
                for item in execution_report.metric_extractions
                if not relevant_code_ids
                or item.execution_id
                in {
                    sandbox.execution_id
                    for sandbox in execution_report.sandbox_executions
                    if sandbox.code_artifact_id in relevant_code_ids
                }
            ],
            "latest_code_artifacts": [
                _questioner_code_summary(item) for item in relevant_code[:1]
            ],
            "latest_safety_audits": [
                {
                    "code_artifact_id": item.code_artifact_id,
                    "passed": item.passed,
                    "blocked": item.blocked,
                    "reasons": item.reasons,
                    "contract_violations": item.contract_violations,
                    "semantic_contract_violations": item.semantic_contract_violations,
                    "workload_limit_violations": item.workload_limit_violations,
                }
                for item in execution_report.safety_audits
                if item.code_artifact_id in relevant_code_ids
            ],
            "latest_sandbox_executions": [
                {
                    "execution_id": item.execution_id,
                    "code_artifact_id": item.code_artifact_id,
                    "status": item.status,
                    "exit_code": item.exit_code,
                    "runtime_seconds": item.runtime_seconds,
                    "timeout": item.timeout,
                    "output_json_path": item.output_json_path,
                }
                for item in execution_report.sandbox_executions
                if item.code_artifact_id in relevant_code_ids
            ],
        },
        "deterministic_diagnostics": [
            {
                "defect_id": item.defect_id,
                "category": item.category,
                "message": item.message,
                "priority": item.priority,
                "repair_kind": item.repair_kind,
                "blocks_acceptance": item.blocks_acceptance,
            }
            for item in diagnostics[:8]
        ],
        "blocking_defect_ids": _blocking_defect_ids(diagnostics),
        "prior_adaptive_decision_count": len(prior_iterations),
        "prior_action_counts": dict(
            sorted(Counter(item.decision.action for item in prior_iterations).items())
        ),
        "prior_adaptive_decisions": [
            {
                "iteration": item.decision.iteration,
                "action": item.decision.action,
                "rationale": item.decision.rationale,
                "repair_instructions": item.decision.repair_instructions,
                "progress_made": item.progress_made,
            }
            for item in prior_iterations[-2:]
        ],
    }
    return _fit_questioner_context(context)


def _questioner_package_summary(
    report: HybridEvidencePackageReport,
) -> dict[str, Any]:
    return {
        "report_id": report.report_id,
        "planning_status": report.planning_status,
        "artifact_plan_count": report.artifact_plan_count,
        "artifact_type_counts": report.artifact_type_counts,
        "warnings": report.warnings,
        "packages": [
            {
                "package_id": package.package_id,
                "title": package.title,
                "primary_claim_draft": package.primary_claim_draft,
                "allowed_claim_scope": package.allowed_claim_scope,
                "package_rationale": _bounded_text(
                    package.package_rationale, max_chars=2_000
                ),
                "artifact_plans": [
                    {
                        "artifact_plan_id": plan.artifact_plan_id,
                        "artifact_type": plan.artifact_type.value,
                        "purpose": _bounded_text(plan.purpose, max_chars=1_200),
                        "claim_component_supported": _bounded_text(
                            plan.claim_component_supported, max_chars=1_200
                        ),
                        "required_inputs": _bounded_sequence(
                            plan.input_contract.get("required_inputs", []), limit=12
                        ),
                        "parameters": _bounded_sequence(
                            plan.input_contract.get("parameters", []), limit=16
                        ),
                        "required_metrics": _bounded_sequence(
                            plan.output_contract.get("required_metrics", []), limit=16
                        ),
                        "baselines": plan.baseline_or_comparator_plan[:8],
                        "controls": (plan.control_plan_optional or [])[:8],
                        "negative_controls": (
                            plan.negative_control_plan_optional or []
                        )[:8],
                        "success_criteria": plan.success_criteria[:8],
                        "failure_criteria": plan.failure_criteria[:8],
                    }
                    for plan in package.artifact_plans[:4]
                ],
                "minimum_required_artifacts": package.minimum_required_artifacts[:12],
                "known_gaps": package.known_gaps[:8],
                "unresolved_obligations": package.unresolved_obligations[:8],
            }
            for package in report.packages[:2]
        ],
    }


def _questioner_result_summary(result: Any) -> dict[str, Any]:
    return {
        "result_id": result.result_id,
        "package_id": result.package_id,
        "artifact_plan_id": result.artifact_plan_id,
        "artifact_type": getattr(result.artifact_type, "value", result.artifact_type),
        "source_prior_result_id_optional": result.source_prior_result_id_optional,
        "execution_profile": result.execution_profile,
        "execution_completed": result.execution_completed,
        "supports_adjudication": result.supports_adjudication,
        "status": result.status,
        "evidence_label": getattr(result.evidence_label, "value", result.evidence_label),
        "scope_label": result.scope_label,
        "metrics": _bounded_mapping(result.metrics, max_chars=12_000),
        "metric_source_count": len(result.metric_sources),
        "metric_source_examples": dict(sorted(result.metric_sources.items())[:8]),
        "baseline_summary": _bounded_text(result.baseline_summary, max_chars=2_000),
        "control_summary": _bounded_text(result.control_summary, max_chars=2_000),
        "negative_control_summary": _bounded_text(
            result.negative_control_summary, max_chars=2_000
        ),
        "success_criteria_satisfied": result.success_criteria_satisfied,
        "failure_criteria_satisfied": result.failure_criteria_satisfied,
        "unresolved_obligations": result.unresolved_obligations,
        "warnings": result.warnings,
        "failure_reason_optional": result.failure_reason_optional,
    }


def _questioner_code_summary(code_artifact: Any) -> dict[str, Any]:
    payload = code_artifact.model_dump(mode="json")
    code = str(payload.get("code", ""))
    return {
        "code_artifact_id": payload.get("code_artifact_id"),
        "source_spec_id": payload.get("source_spec_id"),
        "repair_of_code_artifact_id_optional": payload.get(
            "repair_of_code_artifact_id_optional"
        ),
        "generation_attempt": payload.get("generation_attempt"),
        "entrypoint": payload.get("entrypoint"),
        "expected_output_files": payload.get("expected_output_files", []),
        "random_seed": payload.get("random_seed"),
        "timeout_seconds": payload.get("timeout_seconds"),
        "code_sha256": sha256_json({"code": code}),
        "code_line_count": len(code.splitlines()),
        "code_excerpt": _bounded_text(code, max_chars=12_000),
    }


def _bounded_sequence(value: Any, *, limit: int) -> list[Any]:
    return list(value[:limit]) if isinstance(value, list) else []


def _fit_questioner_context(context: dict[str, Any]) -> dict[str, Any]:
    """Keep adaptive context below one explicit transport-safe byte ceiling."""

    def size() -> int:
        return len(
            json.dumps(context, sort_keys=True, separators=(",", ":"), default=str).encode(
                "utf-8"
            )
        )

    if size() <= _MAX_QUESTIONER_CONTEXT_BYTES:
        return context
    for code in context.get("execution_summary", {}).get("latest_code_artifacts", []):
        code["code_excerpt"] = _bounded_text(
            str(code.get("code_excerpt", "")), max_chars=4_000
        )
    if size() <= _MAX_QUESTIONER_CONTEXT_BYTES:
        return context
    for result in context.get("execution_summary", {}).get("results", []):
        metrics = result.get("metrics", {})
        if isinstance(metrics, dict) and isinstance(metrics.get("values"), dict):
            selected = dict(list(sorted(metrics["values"].items()))[:12])
            metrics["values"] = selected
            metrics["included_count"] = len(selected)
            metrics["omitted_count"] = metrics.get("total_count", 0) - len(selected)
        for field_name in (
            "baseline_summary",
            "control_summary",
            "negative_control_summary",
        ):
            result[field_name] = _bounded_text(
                str(result.get(field_name, "")), max_chars=800
            )
    if size() <= _MAX_QUESTIONER_CONTEXT_BYTES:
        return context
    context["prior_adaptive_decisions"] = context.get("prior_adaptive_decisions", [])[-1:]
    package_report = context.get("package_report", {})
    for package in package_report.get("packages", []):
        package["artifact_plans"] = package.get("artifact_plans", [])[:2]
        package["unresolved_obligations"] = package.get("unresolved_obligations", [])[:4]
    if size() <= _MAX_QUESTIONER_CONTEXT_BYTES:
        return context
    context["execution_summary"]["latest_code_artifacts"] = [
        {
            key: value
            for key, value in item.items()
            if key != "code_excerpt"
        }
        for item in context["execution_summary"].get("latest_code_artifacts", [])
    ]
    if size() > _MAX_QUESTIONER_CONTEXT_BYTES:
        context["package_report"]["packages"] = [
            {
                "package_id": item.get("package_id"),
                "title": item.get("title"),
                "primary_claim_draft": item.get("primary_claim_draft"),
                "allowed_claim_scope": item.get("allowed_claim_scope"),
            }
            for item in context["package_report"].get("packages", [])[:1]
        ]
    if size() > _MAX_QUESTIONER_CONTEXT_BYTES:
        raise AdaptiveEvidenceError(
            "Compact adaptive evidence snapshot exceeded the 64 KiB context ceiling."
        )
    return context


def _bounded_mapping(values: dict[str, Any], *, max_chars: int) -> dict[str, Any]:
    selected: dict[str, Any] = {}
    used = 2
    for key, value in sorted(values.items()):
        item_chars = len(json.dumps({key: value}, sort_keys=True, default=str))
        if used + item_chars > max_chars:
            continue
        selected[key] = value
        used += item_chars
    return {
        "total_count": len(values),
        "included_count": len(selected),
        "omitted_count": len(values) - len(selected),
        "values": selected,
    }


def _bounded_text(value: str, *, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    marker = f"\n...[{len(value) - max_chars} characters omitted]...\n"
    remaining = max_chars - len(marker)
    head = remaining // 2
    return value[:head] + marker + value[-(remaining - head) :]


def _normalize_decision(
    *,
    decision_id: str,
    run_id: str,
    iteration: int,
    package_report: HybridEvidencePackageReport,
    execution_report: EvidencePackageExecutionReport,
    questions: list[dict[str, Any]],
    proposal: AdaptiveQuestionerDecisionProposal,
    diagnostics: list[_Diagnostic],
    active_defect: _Diagnostic | None,
    fingerprint: str,
    questioner: AdaptiveQuestionerClient,
) -> AdaptiveEvidenceDecision:
    question_by_id = {item["question_id"]: item for item in questions}
    answers = [
        AdaptiveQuestionerAnswer(
            question_id=item.question_id,
            category=item.category,
            question=question_by_id[item.question_id]["question"],
            status=item.status,
            explanation=item.explanation,
            evidence_artifact_ids=item.evidence_artifact_ids,
            blocking=item.blocking,
            recommended_fix_optional=item.recommended_fix_optional,
        )
        for item in proposal.answers
    ]
    action = proposal.recommended_action
    disposition = proposal.claim_disposition
    blocking_answers = [
        item for item in answers if item.blocking or item.status in {"fail", "unknown"}
    ]
    deterministic_blocks = (
        [active_defect]
        if active_defect is not None and active_defect.blocks_acceptance
        else []
    )
    terminal_hard = [item for item in diagnostics if item.terminal_block]
    primary_plan_ids = {
        candidate.artifact_plans[0].artifact_plan_id
        for candidate in package_report.packages
        if candidate.artifact_plans
    }
    completed = any(
        item.artifact_plan_id in primary_plan_ids
        and item.supports_adjudication
        and item.status == "completed"
        for item in execution_report.results
    )
    negative = any(
        item.artifact_plan_id in primary_plan_ids
        and item.supports_adjudication
        and item.status == "negative_result"
        for item in execution_report.results
    )
    latest_code = _latest_code_by_spec(execution_report)

    if terminal_hard:
        action = "blocked"
        disposition = "deferred"
    elif action == "accept_supported_result" and (
        blocking_answers
        or deterministic_blocks
        or not execution_report.adjudication_ready
        or not completed
    ):
        action = _repair_action_for_findings(
            blocking_answers,
            deterministic_blocks,
            bool(latest_code),
        )
        disposition = "inconclusive"
    elif action in {"accept_negative_result", "downgrade_claim"}:
        if (
            not blocking_answers
            and not deterministic_blocks
            and execution_report.adjudication_ready
            and negative
        ):
            action = "accept_negative_result"
            disposition = "negative_result"
        else:
            action = _repair_action_for_findings(
                blocking_answers,
                deterministic_blocks,
                bool(latest_code),
            )
            disposition = "inconclusive"
    elif action == "repair_code" and not latest_code:
        action = "repair_evidence_plan"
    elif action == "stop_budget_exhausted":
        action = "stop_no_progress"
        disposition = "deferred"

    if active_defect is not None and action in {"repair_code", "repair_evidence_plan"}:
        action = active_defect.repair_kind
        if action == "blocked":
            disposition = "deferred"

    repair_instructions = list(proposal.repair_instructions)
    if action in {"repair_code", "repair_evidence_plan"} and not repair_instructions:
        repair_instructions = [
            item.recommended_fix_optional or item.explanation
            for item in blocking_answers
        ] or [item.message for item in deterministic_blocks] or [
            "Resolve the blocking diagnostic findings without changing the scientific target."
        ]

    return AdaptiveEvidenceDecision(
        decision_id=decision_id,
        run_id=run_id,
        iteration=iteration,
        source_package_report_id=package_report.report_id,
        source_execution_report_id=execution_report.report_id,
        source_package_ids=[item.package_id for item in package_report.packages],
        source_result_ids=[item.result_id for item in execution_report.results],
        source_code_artifact_ids=[item.code_artifact_id for item in latest_code.values()],
        questions=answers,
        deterministic_findings=(
            [active_defect.message]
            if active_defect is not None
            else [item.message for item in diagnostics if item.blocks_acceptance]
        ),
        action=action,
        rationale=proposal.rationale,
        repair_instructions=repair_instructions,
        unresolved_questions=proposal.unresolved_questions,
        claim_disposition=disposition,
        diagnostic_fingerprint=fingerprint,
        backend_kind=questioner.backend_kind,
    )


def _repair_action_for_findings(
    answers: list[AdaptiveQuestionerAnswer],
    diagnostics: list[_Diagnostic],
    has_code: bool,
) -> str:
    categories = {item.category for item in answers} | {
        item.category for item in diagnostics
    }
    if has_code and categories & {"implementation_fidelity", "numerical_validity"}:
        return "repair_code"
    if categories & {"baseline_control_adequacy", "evidence_sufficiency"}:
        return "repair_evidence_plan"
    return "blocked"


def _terminal_status(action: str) -> str | None:
    return {
        "accept_supported_result": "satisfied_supported",
        "accept_negative_result": "satisfied_negative",
        "stop_weak_branch": "stopped_weak_branch",
        "stop_no_progress": "stopped_no_progress",
        "stop_budget_exhausted": "budget_exhausted",
        "blocked": "blocked",
    }.get(action)


def _code_repair_target(
    execution: EvidencePackageExecutionReport,
    *,
    include_completed: bool = False,
    preferred_target_id: str | None = None,
) -> str | None:
    code_specs = {item.source_spec_id for item in execution.code_artifacts}
    if preferred_target_id:
        preferred_spec = preferred_target_id.split("/", 1)[0]
        if preferred_spec in code_specs:
            return preferred_spec
    for result in execution.results:
        if result.artifact_plan_id in code_specs and result.status not in {
            "completed",
            "negative_result",
        }:
            return result.artifact_plan_id
    if include_completed:
        for result in reversed(execution.results):
            if result.artifact_plan_id in code_specs:
                return result.artifact_plan_id
    return None


def _has_budget_deferred_required_artifact(
    execution: EvidencePackageExecutionReport,
) -> bool:
    return bool(
        execution.budget_deferred_artifact_count
        and execution.incomplete_required_artifact_plan_ids
    )


def _latest_code_by_spec(execution: EvidencePackageExecutionReport) -> dict[str, Any]:
    latest: dict[str, Any] = {}
    for item in execution.code_artifacts:
        latest[item.source_spec_id] = item
    return latest


def _diagnostic_fingerprint(
    package: HybridEvidencePackageReport,
    execution: EvidencePackageExecutionReport,
    *,
    diagnostics: list[_Diagnostic] | None = None,
) -> str:
    current = diagnostics if diagnostics is not None else _diagnose(package, execution)
    return sha256_json(
        {
            "blocking_defect_ids": _blocking_defect_ids(current),
            "adjudication_ready": execution.adjudication_ready,
        }
    )


def _build_loop_report(
    *,
    run_id: str,
    reports: Path,
    config: AdaptiveEvidenceLoopConfig,
    status: str,
    iterations: list[AdaptiveEvidenceIteration],
    code_attempts: int,
    code_successes: int,
    plan_attempts: int,
    plan_successes: int,
    package_path: Path,
    execution_path: Path,
    execution: EvidencePackageExecutionReport,
    decision: AdaptiveEvidenceDecision,
    budget: TargetedLLMBudgetManager,
    questioner: AdaptiveQuestionerClient,
    terminal_reason: str,
    warnings: list[str],
) -> AdaptiveEvidenceLoopReport:
    report_id = f"adaptive-evidence-loop-report-{_next_number(reports, _LOOP_RE):04d}"
    blocking_questions = [
        item.question_id
        for item in decision.questions
        if item.blocking or item.status in {"fail", "unknown"}
    ]
    accepted = (
        [
            item.result_id
            for item in execution.results
            if item.supports_adjudication
            and item.status
            == ("completed" if status == "satisfied_supported" else "negative_result")
        ]
        if status in {"satisfied_supported", "satisfied_negative"}
        else []
    )
    backend_records = []
    if iterations:
        backend_records.append(
            stage_backend_record(
                stage_id=f"{report_id}-adaptive-questioner",
                stage_kind=ScientificStageKind.CRITIC_REVIEW,
                backend_kind=questioner.backend_kind,
                backend_name=questioner.backend_name,
                is_scientific_generation=False,
                is_scientific_judgment=True,
                is_execution_or_verification=False,
                reason=(
                    "One non-fake LLM questioner evaluates implementation fidelity, controls, "
                    "evidence sufficiency, bounded claims, and stopping without creating evidence."
                ),
                artifact_ids=[item.decision.decision_id for item in iterations],
                allowed_in_production=True,
                fallback_used=questioner.fallback_used,
                fallback_disclosed=questioner.fallback_disclosed,
            )
        )
    backend_records.append(
        stage_backend_record(
            stage_id=f"{report_id}-decision-validation",
            stage_kind=ScientificStageKind.SPEC_VALIDATION,
            backend_kind=BackendKind.LOCAL_EXECUTION,
            backend_name="adaptive_evidence_decision_validator",
            is_scientific_generation=False,
            is_scientific_judgment=False,
            is_execution_or_verification=True,
            reason=(
                "Local policy validates metric provenance, safety, action compatibility, repair "
                "limits, and terminal evidence status."
            ),
            artifact_ids=[report_id],
            allowed_in_production=True,
        )
    )
    obligations = sorted(
        {
            *decision.unresolved_questions,
            *[
                obligation
                for result in execution.results
                for obligation in result.unresolved_obligations
            ],
        }
    )
    return AdaptiveEvidenceLoopReport(
        report_id=report_id,
        run_id=run_id,
        config=config,
        status=status,
        iterations=iterations,
        accepted_result_ids=accepted,
        code_repair_attempt_count=code_attempts,
        code_repair_success_count=code_successes,
        plan_repair_attempt_count=plan_attempts,
        plan_repair_success_count=plan_successes,
        unresolved_blocking_questions=blocking_questions,
        unresolved_obligations=obligations,
        terminal_reason=terminal_reason,
        latest_package_report_path=_relative(budget.root, package_path),
        latest_execution_report_path=_relative(budget.root, execution_path),
        call_accounting_paths=list(budget.call_accounting_paths),
        budget_usage=budget.usage,
        backend_records=backend_records,
        warnings=warnings,
        production_ready=(
            bool(iterations)
            and questioner.backend_kind == BackendKind.LLM_OPENAI
            and not questioner.fallback_used
        ),
    )


def _persist_terminal_without_call(
    *,
    run_id: str,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    config: AdaptiveEvidenceLoopConfig,
    status: str,
    iterations: list[AdaptiveEvidenceIteration],
    code_attempts: int,
    code_successes: int,
    plan_attempts: int,
    plan_successes: int,
    package_path: Path,
    execution_path: Path,
    budget: TargetedLLMBudgetManager,
    questioner: AdaptiveQuestionerClient,
    terminal_reason: str,
    warnings: list[str],
) -> AdaptiveEvidenceStageResult:
    execution = EvidencePackageExecutionReport.model_validate_json(
        execution_path.read_text(encoding="utf-8")
    )
    if iterations:
        decision = iterations[-1].decision
    else:
        decision = _local_budget_decision(
            run_id=run_id,
            package_path=package_path,
            execution=execution,
        )
    report = _build_loop_report(
        run_id=run_id,
        reports=root / "runs" / run_id / "reports",
        config=config,
        status=status,
        iterations=iterations,
        code_attempts=code_attempts,
        code_successes=code_successes,
        plan_attempts=plan_attempts,
        plan_successes=plan_successes,
        package_path=package_path,
        execution_path=execution_path,
        execution=execution,
        decision=decision,
        budget=budget,
        questioner=questioner,
        terminal_reason=terminal_reason,
        warnings=warnings,
    )
    return _persist_loop_report(
        report=report,
        store=store,
        ledger=ledger,
        raw=None,
        decision=None,
        iteration=None,
    )


def _local_budget_decision(
    *, run_id: str, package_path: Path, execution: EvidencePackageExecutionReport
) -> AdaptiveEvidenceDecision:
    package = HybridEvidencePackageReport.model_validate_json(
        package_path.read_text(encoding="utf-8")
    )
    answer = AdaptiveQuestionerAnswer(
        question_id="budget",
        category="stopping",
        question="Does unreserved budget remain for adaptive scientific questioning?",
        status="fail",
        explanation="The deterministic runtime budget has been exhausted.",
        blocking=True,
    )
    return AdaptiveEvidenceDecision(
        decision_id="adaptive-evidence-budget-exhausted",
        run_id=run_id,
        iteration=1,
        source_package_report_id=package.report_id,
        source_execution_report_id=execution.report_id,
        source_package_ids=[item.package_id for item in package.packages],
        source_result_ids=[item.result_id for item in execution.results],
        questions=[answer],
        deterministic_findings=["No unreserved LLM budget remained."],
        action="stop_budget_exhausted",
        rationale="The deterministic runtime budget was exhausted before questioning.",
        unresolved_questions=["All scientific questioner checks remain unresolved."],
        claim_disposition="deferred",
        diagnostic_fingerprint=_diagnostic_fingerprint(package, execution),
        backend_kind=BackendKind.LOCAL_EXECUTION,
    )


def _persist_questioner_decision(
    *,
    run_id: str,
    raw: AdaptiveQuestionerRawArtifact,
    decision: AdaptiveEvidenceDecision,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PersistenceResult:
    return persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                raw.raw_artifact_id,
                ArtifactType.REPORT,
                raw,
                "json",
                _metadata("adaptive_questioner_raw"),
            ),
            ArtifactWriteSpec(
                decision.decision_id,
                ArtifactType.REPORT,
                decision,
                "json",
                _metadata("adaptive_evidence_decision"),
            ),
        ],
        action_type=ControllerActionType.CONTROLLER_ACTION,
        commit_payload={
            "operation": "adaptive_evidence_questioner_decision",
            "decision_id": decision.decision_id,
            "action": decision.action,
        },
    )


def _persist_loop_report(
    *,
    report: AdaptiveEvidenceLoopReport,
    store: ArtifactStore,
    ledger: ResearchLedger,
    raw: AdaptiveQuestionerRawArtifact | None,
    decision: AdaptiveEvidenceDecision | None,
    iteration: AdaptiveEvidenceIteration | None,
) -> AdaptiveEvidenceStageResult:
    del raw, decision
    specs: list[ArtifactWriteSpec] = []
    if iteration is not None:
        specs.append(
            ArtifactWriteSpec(
                iteration.iteration_id,
                ArtifactType.REPORT,
                iteration,
                "json",
                _metadata("adaptive_evidence_iteration"),
            )
        )
    specs.append(
        ArtifactWriteSpec(
            report.report_id,
            ArtifactType.REPORT,
            report,
            "json",
            _metadata("adaptive_evidence_loop_report"),
        )
    )
    persistence = persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.CONTROLLER_ACTION,
        commit_payload={
            "operation": "adaptive_evidence_loop_report",
            "report_id": report.report_id,
            "status": report.status,
        },
    )
    report_artifact = next(
        item for item in persistence.artifacts if item.id == report.report_id
    )
    return AdaptiveEvidenceStageResult(
        run_id=report.run_id,
        report=report,
        persistence=persistence,
        report_artifact=report_artifact,
    )


def _load_latest_loop_report(reports: Path) -> AdaptiveEvidenceLoopReport | None:
    path = _latest_matching(reports, _LOOP_RE)
    if path is None:
        return None
    try:
        return AdaptiveEvidenceLoopReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise AdaptiveEvidenceError(f"Could not load adaptive evidence report: {exc}") from exc


def _post_critic_diagnostics(
    reports: Path,
    execution: EvidencePackageExecutionReport,
) -> list[_Diagnostic]:
    """Route adjudication repairs back only to the execution that critics reviewed."""
    path = _latest_matching(reports, _ADJUDICATION_RE)
    if path is None:
        return []
    try:
        report = CrossPackageAdjudicationReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise AdaptiveEvidenceError(
            f"Could not load cross-package adjudication report: {exc}"
        ) from exc
    if report.paper_nucleus_selection_optional is not None:
        return []
    if Path(report.source_execution_report_path).name != f"{execution.report_id}.json":
        return []

    result_by_package = {item.package_id: item for item in execution.results}
    diagnostics: list[_Diagnostic] = []
    for decision in report.decisions:
        decision_value = getattr(decision.decision, "value", decision.decision)
        if decision_value != "needs_repair" or not decision.required_repairs:
            continue
        result = result_by_package.get(decision.package_id)
        if result is None:
            continue
        repairs = " ".join(
            f"{index}. {instruction}"
            for index, instruction in enumerate(decision.required_repairs, start=1)
        )
        diagnostics.append(
            _Diagnostic(
                "post_critic_execution_repair",
                _bounded_text(
                    "Independent post-execution criticism rejected this package as a paper "
                    "nucleus. Repair the existing executable artifact without changing the "
                    f"scientific target. Required repairs: {repairs}",
                    max_chars=8_000,
                ),
                "implementation_fidelity",
                target_id=decision.package_id,
                repair_target_id=result.artifact_plan_id,
            )
        )
    return diagnostics


def _load_latest_package_report(
    reports: Path,
) -> tuple[Path, HybridEvidencePackageReport]:
    path = _latest_matching(reports, _PACKAGE_RE)
    if path is None:
        raise AdaptiveEvidenceError("Hybrid evidence package report is missing.")
    return path, HybridEvidencePackageReport.model_validate_json(
        path.read_text(encoding="utf-8")
    )


def _load_latest_execution_report(
    reports: Path,
) -> tuple[Path, EvidencePackageExecutionReport]:
    return load_latest_actionable_execution_report(reports)


def load_latest_actionable_execution_report(
    reports: Path,
) -> tuple[Path, EvidencePackageExecutionReport]:
    """Keep a rejected patch diagnostic from replacing prior execution evidence."""
    matches = _matching(reports, _EXECUTION_RE)
    if not matches:
        raise AdaptiveEvidenceError("Hybrid evidence execution report is missing.")
    latest_path = matches[-1]
    latest = EvidencePackageExecutionReport.model_validate_json(
        latest_path.read_text(encoding="utf-8")
    )
    if not _is_rejected_repair_only_report(latest):
        return latest_path, latest

    for path in reversed(matches[:-1]):
        historical = EvidencePackageExecutionReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        if (
            historical.source_package_report_path
            != latest.source_package_report_path
            or historical.execution_profile != latest.execution_profile
        ):
            continue
        if not (
            any(item.execution_completed for item in historical.results)
            or historical.metric_extractions
            or historical.sandbox_executions
        ):
            continue
        warning = (
            f"Ignored repair-only diagnostic {latest.report_id} as the active scientific "
            f"state; retained prior execution evidence from {historical.report_id}."
        )
        return path, historical.model_copy(
            update={"warnings": [*historical.warnings, warning]}
        )
    return latest_path, latest


def _is_rejected_repair_only_report(
    report: EvidencePackageExecutionReport,
) -> bool:
    if (
        report.code_artifacts
        or report.safety_audits
        or report.sandbox_executions
        or report.metric_extractions
        or not report.results
    ):
        return False
    return all(
        item.status == "failed"
        and (item.failure_reason_optional or "").startswith(
            "Scientific code repair was rejected:"
        )
        for item in report.results
    )


def recover_historical_code_artifacts(
    report: EvidencePackageExecutionReport,
    reports: Path,
) -> EvidencePackageExecutionReport:
    """Restore linked code execution context omitted from a resume report."""
    source_specs = {item.artifact_plan_id for item in report.results}
    existing_specs = {item.source_spec_id for item in report.code_artifacts}
    missing_specs = source_specs - existing_specs
    recovered_code: dict[str, Any] = {}
    existing_audits = {item.code_artifact_id for item in report.safety_audits}
    existing_executions = {
        item.code_artifact_id for item in report.sandbox_executions
    }
    recovered_audits: dict[str, Any] = {}
    recovered_executions: dict[str, Any] = {}
    for path in reversed(_matching(reports, _EXECUTION_RE)):
        historical = EvidencePackageExecutionReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
        if (
            historical.source_package_report_path != report.source_package_report_path
            or historical.execution_profile != report.execution_profile
        ):
            continue
        for artifact in reversed(historical.code_artifacts):
            if (
                artifact.source_spec_id in missing_specs
                and artifact.source_spec_id not in recovered_code
            ):
                recovered_code[artifact.source_spec_id] = artifact
        code_ids = {
            item.code_artifact_id for item in report.code_artifacts
        } | {item.code_artifact_id for item in recovered_code.values()}
        for audit in historical.safety_audits:
            if (
                audit.code_artifact_id in code_ids
                and audit.code_artifact_id not in existing_audits
                and audit.code_artifact_id not in recovered_audits
            ):
                recovered_audits[audit.code_artifact_id] = audit
        for execution in historical.sandbox_executions:
            if (
                execution.code_artifact_id in code_ids
                and execution.code_artifact_id not in existing_executions
                and execution.code_artifact_id not in recovered_executions
            ):
                recovered_executions[execution.code_artifact_id] = execution
    if not (recovered_code or recovered_audits or recovered_executions):
        return report
    return report.model_copy(
        update={
            "code_artifacts": [*report.code_artifacts, *recovered_code.values()],
            "safety_audits": [*report.safety_audits, *recovered_audits.values()],
            "sandbox_executions": [
                *report.sandbox_executions,
                *recovered_executions.values(),
            ],
        }
    )
def _timeout_seconds(report: EvidencePackageExecutionReport) -> int:
    values = [item.timeout_seconds for item in report.code_artifacts]
    return max(values, default=30)


def _memory_limit_mb(report: EvidencePackageExecutionReport) -> int:
    values = [item.memory_limit_mb for item in report.sandbox_configs]
    return max(values, default=512)


def _matching(directory: Path, pattern: re.Pattern[str]) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.iterdir() if pattern.match(path.name))


def _latest_matching(directory: Path, pattern: re.Pattern[str]) -> Path | None:
    matches = _matching(directory, pattern)
    return matches[-1] if matches else None


def _next_number(directory: Path, pattern: re.Pattern[str]) -> int:
    values = [
        int(match.group(1))
        for path in _matching(directory, pattern)
        if (match := pattern.match(path.name))
    ]
    return max(values, default=0) + 1


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _latest_runtime_diagnostics(
    execution: EvidencePackageExecutionReport,
    root: Path,
) -> dict[str, Any] | None:
    for observation in reversed(execution.sandbox_executions):
        if observation.status not in {"failed", "timed_out"}:
            continue
        return {
            "execution_id": observation.execution_id,
            "code_artifact_id": observation.code_artifact_id,
            "status": observation.status,
            "exit_code": observation.exit_code,
            "failure_reason": observation.failure_reason_optional,
            "timeout": observation.timeout,
            "stderr_tail": _bounded_artifact_tail(root, observation.stderr_path),
            "stdout_tail": _bounded_artifact_tail(root, observation.stdout_path),
        }
    return None


def _bounded_artifact_tail(root: Path, relative_path: str) -> str:
    path = (root / relative_path).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")[-12_000:]
    except OSError:
        return ""


def _metadata(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "artifact_role": "adaptive_evidence_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }


__all__ = [
    "AdaptiveEvidenceError",
    "AdaptiveEvidenceStageResult",
    "load_latest_actionable_execution_report",
    "recover_historical_code_artifacts",
    "run_adaptive_evidence_loop",
]
