"""Explicit gated LLM-assisted full-paper orchestration."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.adapters.config import AdapterConfig
from factori.adapters.errors import (
    AdapterConfigurationError,
    AdapterResponseParseError,
    AdapterTransportError,
    error_payload,
)
from factori.adapters.llm_real import LLMTransport, OpenAIResponsesTransport
from factori.adapters.registry import AdapterRegistry, get_adapter_registry
from factori.artifacts import ArtifactStore
from factori.claim_adjudication import (
    ClaimAdjudicator,
    FakeClaimAdjudicator,
    OpenAIClaimAdjudicator,
)
from factori.config import OPENAI_API_KEY_ENV
from factori.full_paper_generation import (
    FullPaperGenerationError,
    FullPaperGenerationRunResult,
    generate_full_paper,
)
from factori.full_paper_release import (
    FullPaperReleaseError,
    FullPaperReleaseRunResult,
    run_full_paper_release_gate,
)
from factori.hashing import sha256_json
from factori.ledger import ResearchLedger
from factori.llm_budget import (
    LLMBudgetExceeded,
    RuntimeLLMBudgetGuard,
    budget_is_explicit,
    build_call_accounting_record,
    build_planned_llm_usage,
    evaluate_llm_budget,
    observed_usage_from_records,
)
from factori.manuscript_plan import planned_manuscript_section_count
from factori.persistence import (
    ArtifactWriteSpec,
    PersistenceResult,
    persist_artifacts_with_commit,
)
from factori.retrieval import (
    run_fixture_retrieval_with_provenance,
    run_local_retrieval_with_provenance,
    run_retrieval_with_provenance,
)
from factori.run_all import PipelineRunError, run_deterministic_pipeline
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    FullPaperGenerationConfig,
    FullPaperGenerationStatus,
    FullPaperReleaseGateConfig,
    FullPaperReleaseStatus,
    LLMBudgetDecision,
    LLMBudgetUsage,
    LLMCallAccountingRecord,
    LLMCallStatus,
    LLMOrchestrationConfig,
    LLMOrchestrationReport,
    LLMOrchestrationResult,
    LLMOrchestrationStatus,
    LLMOrchestrationStep,
    LLMOrchestrationStepStatus,
    LLMRunSafetyReport,
    PipelineRunConfig,
    PipelineRunStatus,
    RerunPolicy,
)
from factori.source_relevance import (
    FakeSourceRelevanceAdjudicator,
    OpenAISourceRelevanceAdjudicator,
    SourceRelevanceAdjudicator,
)
from factori.stage0 import OPPORTUNITY_THRESHOLD, discover_opportunities
from factori.stage_a import (
    MAX_STAGE_A_SURVIVORS,
    StageAResult,
    constraint_from_inputs,
    run_stage_a,
)
from factori.stage_b import run_stage_b
from factori.stage_b_phases import planned_stage_b_review_calls
from factori.storage_protocols import Clock, SystemClock

_FAKE_BACKEND = "fake"
_LLM_SCOPE_CANDIDATE_ONLY = "candidate-only"
_LLM_SCOPE_REVIEWER_ONLY = "reviewer-only"
_LLM_SCOPE_FULL_PAPER = "full-paper"
_SUPPORTED_LLM_SCOPES = {
    _LLM_SCOPE_CANDIDATE_ONLY,
    _LLM_SCOPE_FULL_PAPER,
    _LLM_SCOPE_REVIEWER_ONLY,
    "prose-only",
    "pipeline-only",
}
_READY_RELEASE_STATUSES = {
    FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW,
    FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW_WITH_WARNINGS,
}


class LLMOrchestrationError(RuntimeError):
    """Raised when gated LLM paper orchestration fails closed."""


class LLMRunInspectionError(RuntimeError):
    """Raised when an existing LLM run cannot be inspected read-only."""


@dataclass(frozen=True)
class LLMOrchestrationRunResult:
    """Runtime result for LLM orchestration plus optional persisted reports."""

    run_id: str
    report: LLMOrchestrationReport
    pipeline_report: Any | None = None
    generation_result: FullPaperGenerationRunResult | None = None
    release_result: FullPaperReleaseRunResult | None = None
    persistence: PersistenceResult | None = None
    config_artifact: ArtifactRef | None = None
    budget_artifact: ArtifactRef | None = None
    accounting_artifact: ArtifactRef | None = None
    report_artifact: ArtifactRef | None = None
    safety_artifact: ArtifactRef | None = None


def run_llm_paper_orchestration(
    *,
    config: LLMOrchestrationConfig,
    root: str | Path = ".",
    store: ArtifactStore | None = None,
    ledger: ResearchLedger | None = None,
    llm_transport: LLMTransport | None = None,
    reviewer_transport: LLMTransport | None = None,
    prose_transport: LLMTransport | None = None,
    claim_adjudicator_transport: LLMTransport | None = None,
    source_relevance_adjudicator_transport: LLMTransport | None = None,
    environ: dict[str, str] | None = None,
    clock: Clock | None = None,
    preflight_only: bool = False,
    llm_scope: str = _LLM_SCOPE_FULL_PAPER,
    enable_safe_repair: bool = False,
) -> LLMOrchestrationRunResult:
    """Run the explicitly gated LLM-assisted paper-generation workflow."""
    root_path = Path(root)
    clock = clock or SystemClock()
    normalized_scope = _normalize_llm_scope(llm_scope)
    enable_safe_repair = enable_safe_repair and normalized_scope == _LLM_SCOPE_FULL_PAPER
    config = _effective_config_for_scope(config, normalized_scope)
    if config.enable_retrieval:
        config = config.model_copy(
            update={"citation_policy": "registry-only", "include_citations": True}
        )
    elif config.citation_policy != "none":
        raise LLMOrchestrationError(
            "citation_policy=registry-only requires --enable-retrieval for run-llm-paper"
        )
    real_mode = _real_llm_mode(config)
    if real_mode and not config.allow_external_calls:
        raise LLMOrchestrationError(
            "LLM orchestration requested but allow_external_calls=false. "
            "Set allow_external_calls=true to use real LLM orchestration."
        )
    if real_mode and not budget_is_explicit(config.budget):
        raise LLMOrchestrationError(
            "Explicit LLM budget is required for real LLM orchestration."
        )
    planned_usage = _planned_usage(config, llm_scope=normalized_scope)
    budget_decision = evaluate_llm_budget(
        config.budget,
        planned_usage,
        require_explicit_budget=real_mode,
    )
    if not budget_decision.allowed:
        raise LLMOrchestrationError(_budget_error_message(budget_decision))

    runtime_budget_guard = RuntimeLLMBudgetGuard(config.budget, clock.now)
    try:
        registry = _registry_for_config(
            config,
            llm_transport=llm_transport,
            reviewer_transport=reviewer_transport,
            prose_transport=prose_transport,
            environ=environ,
            runtime_budget_guard=runtime_budget_guard if real_mode else None,
        )
    except (AdapterConfigurationError, ValueError) as exc:
        raise LLMOrchestrationError(str(exc)) from exc
    try:
        claim_adjudicator = _claim_adjudicator_for_config(
            config,
            transport=claim_adjudicator_transport,
            environ=environ,
            runtime_budget_guard=runtime_budget_guard if real_mode else None,
        )
    except (AdapterConfigurationError, ValueError) as exc:
        raise LLMOrchestrationError(str(exc)) from exc
    try:
        source_relevance_adjudicator = _source_relevance_adjudicator_for_config(
            config,
            transport=source_relevance_adjudicator_transport,
            environ=environ,
            runtime_budget_guard=runtime_budget_guard if real_mode else None,
        )
    except (AdapterConfigurationError, ValueError) as exc:
        raise LLMOrchestrationError(str(exc)) from exc

    preflight_started = clock.now()
    preflight_step = _step(
        "preflight",
        (
            LLMOrchestrationStepStatus.SUCCEEDED_WITH_WARNINGS
            if budget_decision.warnings
            else LLMOrchestrationStepStatus.SUCCEEDED
        ),
        "Validated LLM orchestration gates, credentials, adapters, models, and budget.",
        preflight_started,
        clock.now(),
        warnings=budget_decision.warnings,
    )
    if preflight_only:
        report = _build_report(
            config=config,
            status=(
                LLMOrchestrationStatus.ORCHESTRATION_SUCCEEDED_WITH_WARNINGS
                if budget_decision.warnings
                else LLMOrchestrationStatus.ORCHESTRATION_SUCCEEDED
            ),
            steps=[preflight_step],
            budget_decision=budget_decision,
            call_accounting=_accounting_records(registry, clock, runtime_budget_guard),
            warnings=list(budget_decision.warnings),
            blocking=[],
            generation_result=None,
            release_result=None,
            llm_scope=normalized_scope,
        )
        return LLMOrchestrationRunResult(run_id=config.run_id, report=report)

    store = store or ArtifactStore(root_path)
    ledger = ledger or ResearchLedger(root_path / "runs" / config.run_id / "ledger.sqlite")
    steps: list[LLMOrchestrationStep] = [preflight_step]
    warnings: list[str] = list(budget_decision.warnings)
    blocking: list[str] = []
    pipeline_report = None
    generation_result = None
    release_result = None
    extra_accounting_records: list[LLMCallAccountingRecord] = []

    if normalized_scope == _LLM_SCOPE_CANDIDATE_ONLY:
        return _run_candidate_only_scope(
            config=config,
            root_path=root_path,
            store=store,
            ledger=ledger,
            registry=registry,
            clock=clock,
            preflight_step=preflight_step,
            budget_decision=budget_decision,
            runtime_budget_guard=runtime_budget_guard,
        )
    if normalized_scope == _LLM_SCOPE_REVIEWER_ONLY:
        return _run_reviewer_only_scope(
            config=config,
            store=store,
            ledger=ledger,
            registry=registry,
            clock=clock,
            preflight_step=preflight_step,
            budget_decision=budget_decision,
            runtime_budget_guard=runtime_budget_guard,
        )

    pipeline_started = clock.now()
    try:
        pipeline_report = run_deterministic_pipeline(
            _pipeline_config(config, root_path),
            clock=clock,
            adapter_registry=registry,
        )
    except (PipelineRunError, AdapterTransportError, LLMBudgetExceeded) as exc:
        transport_error = _find_transport_error(exc)
        error_text = _failure_message(exc, transport_error)
        step_status = (
            LLMOrchestrationStepStatus.BLOCKED
            if isinstance(exc, LLMBudgetExceeded)
            else LLMOrchestrationStepStatus.FAILED
        )
        steps.append(
            _step(
                "run-all",
                step_status,
                (
                    "Pipeline execution was blocked by the runtime LLM budget."
                    if isinstance(exc, LLMBudgetExceeded)
                    else "Pipeline execution failed."
                ),
                pipeline_started,
                clock.now(),
                error=error_text,
            )
        )
        blocking.append(error_text)
        records = _accounting_records(registry, clock, runtime_budget_guard)
        if transport_error is not None:
            records.append(
                _failed_transport_record(
                    step_name="run-all",
                    error=transport_error,
                    config=config,
                    clock=clock,
                )
            )
        report = _build_report(
            config=config,
            status=(
                LLMOrchestrationStatus.ORCHESTRATION_BLOCKED
                if isinstance(exc, LLMBudgetExceeded)
                else LLMOrchestrationStatus.ORCHESTRATION_FAILED
            ),
            steps=steps,
            budget_decision=budget_decision,
            call_accounting=records,
            warnings=warnings,
            blocking=blocking,
            generation_result=None,
            release_result=None,
            llm_scope=normalized_scope,
            runtime_budget_blocked=isinstance(exc, LLMBudgetExceeded),
        )
        return _maybe_persist(config, store, ledger, report, pipeline_report, None, None)
    if runtime_budget_guard.blocked_records:
        warnings.extend(pipeline_report.warnings)
        budget_failure = next(
            (
                warning
                for warning in pipeline_report.warnings
                if "exceeded" in warning.lower()
            ),
            "Pipeline stopped after an upstream runtime LLM budget failure.",
        )
        blocking.append(budget_failure)
        steps.append(
            _step(
                "run-all",
                LLMOrchestrationStepStatus.BLOCKED,
                "Pipeline execution was blocked by the runtime LLM budget.",
                pipeline_started,
                clock.now(),
                warnings=pipeline_report.warnings,
                error=budget_failure,
                artifact_ids=["pipeline-run-report"],
            )
        )
        if config.generate_paper:
            steps.append(
                _step(
                    "generate-paper",
                    LLMOrchestrationStepStatus.SKIPPED,
                    "Paper generation was skipped because an upstream LLM budget failed.",
                    clock.now(),
                    clock.now(),
                )
            )
        if config.evaluate_release:
            steps.append(
                _step(
                    "evaluate-paper-release",
                    LLMOrchestrationStepStatus.SKIPPED,
                    "Release evaluation was skipped because an upstream LLM budget failed.",
                    clock.now(),
                    clock.now(),
                )
            )
        records = _accounting_records(registry, clock, runtime_budget_guard)
        report = _build_report(
            config=config,
            status=LLMOrchestrationStatus.ORCHESTRATION_BLOCKED,
            steps=steps,
            budget_decision=budget_decision,
            call_accounting=records,
            warnings=warnings,
            blocking=blocking,
            generation_result=None,
            release_result=None,
            observed_usage=observed_usage_from_records(records),
            llm_scope=normalized_scope,
            runtime_budget_blocked=True,
        )
        return _maybe_persist(config, store, ledger, report, pipeline_report, None, None)
    pipeline_status = (
        LLMOrchestrationStepStatus.SUCCEEDED_WITH_WARNINGS
        if pipeline_report.warnings
        or pipeline_report.pipeline_status == PipelineRunStatus.PIPELINE_SUCCEEDED_WITH_WARNINGS
        else LLMOrchestrationStepStatus.SUCCEEDED
    )
    steps.append(
        _step(
            "run-all",
            pipeline_status,
            "Deterministic pipeline completed with configured LLM seams.",
            pipeline_started,
            clock.now(),
            warnings=pipeline_report.warnings,
            artifact_ids=["pipeline-run-report"],
        )
    )
    warnings.extend(pipeline_report.warnings)

    if config.enable_retrieval:
        retrieval_started = clock.now()
        try:
            retrieval_result = _run_bounded_citation_retrieval(
                config=config,
                registry=registry,
                store=store,
                ledger=ledger,
                source_relevance_adjudicator=source_relevance_adjudicator,
            )
        except (AdapterTransportError, ValueError, RuntimeError) as exc:
            blocked_by_budget = isinstance(exc, LLMBudgetExceeded)
            blocking.append(str(exc))
            steps.append(
                _step(
                    "retrieval-citation-registry",
                    (
                        LLMOrchestrationStepStatus.BLOCKED
                        if blocked_by_budget
                        else LLMOrchestrationStepStatus.FAILED
                    ),
                    (
                        "Source relevance adjudication was blocked by the runtime LLM budget."
                        if blocked_by_budget
                        else "Bounded retrieval for citation provenance failed."
                    ),
                    retrieval_started,
                    clock.now(),
                    error=str(exc),
                )
            )
            if isinstance(exc, AdapterTransportError):
                extra_accounting_records.append(
                    _failed_transport_record(
                        step_name="llm-source-relevance-adjudication",
                        error=exc,
                        config=config,
                        clock=clock,
                    )
                )
        else:
            retrieval_warning = (
                "Retrieval quality is bounded background context only and does not "
                "establish novelty, validation, correctness, or publication readiness."
            )
            warnings.append(retrieval_warning)
            steps.append(
                _step(
                    "retrieval-citation-registry",
                    LLMOrchestrationStepStatus.SUCCEEDED_WITH_WARNINGS,
                    "Bounded retrieval metadata was recorded for registry-only citations.",
                    retrieval_started,
                    clock.now(),
                    warnings=[retrieval_warning],
                    artifact_ids=sorted(
                        artifact.id for artifact in retrieval_result.artifacts.values()
                    ),
                )
            )
    else:
        steps.append(
            _step(
                "retrieval-citation-registry",
                LLMOrchestrationStepStatus.SKIPPED,
                "Citation retrieval was disabled; citation policy remains none.",
                clock.now(),
                clock.now(),
            )
        )

    if config.generate_paper and not blocking:
        generation_started = clock.now()
        try:
            generation_result = generate_full_paper(
                run_id=config.run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                prose_generator=registry.prose_generator,
                config=_full_paper_config(config),
                enable_safe_repair=enable_safe_repair,
                claim_adjudicator=claim_adjudicator,
            )
        except LLMBudgetExceeded as exc:
            steps.append(
                _step(
                    "generate-paper",
                    LLMOrchestrationStepStatus.BLOCKED,
                    "Full-paper generation was blocked by the runtime LLM budget.",
                    generation_started,
                    clock.now(),
                    error=str(exc),
                )
            )
            blocking.append(str(exc))
        except (AdapterTransportError, AdapterResponseParseError) as exc:
            steps.append(
                _step(
                    "generate-paper",
                    LLMOrchestrationStepStatus.FAILED,
                    "Full-paper generation failed during claim adjudication or prose transport.",
                    generation_started,
                    clock.now(),
                    error=str(exc),
                )
            )
            blocking.append(str(exc))
            if isinstance(exc, AdapterTransportError):
                extra_accounting_records.append(
                    _failed_transport_record(
                        step_name="llm-claim-adjudication",
                        error=exc,
                        config=config,
                        clock=clock,
                    )
                )
        except FullPaperGenerationError as exc:
            steps.append(
                _step(
                    "generate-paper",
                    LLMOrchestrationStepStatus.FAILED,
                    "Full-paper generation failed.",
                    generation_started,
                    clock.now(),
                    error=str(exc),
                )
            )
            blocking.append(str(exc))
        else:
            status = (
                LLMOrchestrationStepStatus.SUCCEEDED_WITH_WARNINGS
                if generation_result.report.warnings
                or generation_result.report.generation_status
                == FullPaperGenerationStatus.PAPER_GENERATION_SUCCEEDED_WITH_WARNINGS
                else LLMOrchestrationStepStatus.SUCCEEDED
            )
            if generation_result.report.blocking_issues:
                status = LLMOrchestrationStepStatus.BLOCKED
                blocking.extend(generation_result.report.blocking_issues)
            warnings.extend(generation_result.report.warnings)
            steps.append(
                _step(
                    "generate-paper",
                    status,
                    "Generated non-evidence paper package.",
                    generation_started,
                    clock.now(),
                    warnings=generation_result.report.warnings,
                    artifact_ids=generation_result.artifact_bundle.artifact_ids,
                )
            )
    else:
        steps.append(
            _step(
                "generate-paper",
                LLMOrchestrationStepStatus.SKIPPED,
                (
                    "Paper generation was skipped because bounded retrieval failed."
                    if blocking and config.generate_paper
                    else "Paper generation was skipped by configuration."
                ),
                clock.now(),
                clock.now(),
            )
        )

    if config.evaluate_release and generation_result is not None and not blocking:
        release_started = clock.now()
        try:
            release_result = run_full_paper_release_gate(
                run_id=config.run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                config=FullPaperReleaseGateConfig(run_id=config.run_id, write_report=True),
            )
        except FullPaperReleaseError as exc:
            steps.append(
                _step(
                    "evaluate-paper-release",
                    LLMOrchestrationStepStatus.FAILED,
                    "Generated-paper release gate failed.",
                    release_started,
                    clock.now(),
                    error=str(exc),
                )
            )
            blocking.append(str(exc))
        else:
            release_status = release_result.report.decision.status
            if release_status in _READY_RELEASE_STATUSES:
                release_step_status = (
                    LLMOrchestrationStepStatus.SUCCEEDED_WITH_WARNINGS
                    if release_status
                    == FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW_WITH_WARNINGS
                    else LLMOrchestrationStepStatus.SUCCEEDED
                )
            else:
                release_step_status = LLMOrchestrationStepStatus.BLOCKED
                blocking.extend(release_result.report.decision.blocking_reasons)
            warnings.extend(release_result.report.decision.warnings)
            steps.append(
                _step(
                    "evaluate-paper-release",
                    release_step_status,
                    "Evaluated generated bundle for human-review readiness only.",
                    release_started,
                    clock.now(),
                    warnings=release_result.report.decision.warnings,
                    artifact_ids=["full-paper-release-report"],
                )
            )
    elif config.evaluate_release:
        steps.append(
            _step(
                "evaluate-paper-release",
                LLMOrchestrationStepStatus.SKIPPED,
                "Release evaluation was skipped because paper generation did not complete.",
                clock.now(),
                clock.now(),
            )
        )

    records = _accounting_records(
        registry,
        clock,
        runtime_budget_guard,
        claim_adjudicator=claim_adjudicator,
        source_relevance_adjudicator=source_relevance_adjudicator,
    )
    records.extend(extra_accounting_records)
    records.sort(
        key=lambda record: (
            record.step_name,
            record.backend,
            record.model or "",
            record.request_hash,
        )
    )
    observed_usage = observed_usage_from_records(records)
    status = _orchestration_status(steps, blocking, warnings)
    report = _build_report(
        config=config,
        status=status,
        steps=steps,
        budget_decision=budget_decision,
        call_accounting=records,
        warnings=warnings,
        blocking=blocking,
        generation_result=generation_result,
        release_result=release_result,
        observed_usage=observed_usage,
        llm_scope=normalized_scope,
        runtime_budget_blocked=any(
            record.status == LLMCallStatus.BLOCKED for record in records
        ),
    )
    return _maybe_persist(
        config,
        store,
        ledger,
        report,
        pipeline_report,
        generation_result,
        release_result,
    )


def llm_orchestration_result_model(
    result: LLMOrchestrationRunResult,
) -> LLMOrchestrationResult:
    """Convert a runtime result to the exported protocol result model."""
    return LLMOrchestrationResult(
        run_id=result.run_id,
        orchestration_status=result.report.orchestration_status,
        report=result.report,
        full_paper_generation_status=result.report.generate_paper_status,
        paper_release_status=result.report.release_status,
    )


def inspect_llm_run_summary(
    *,
    run_id: str,
    root: str | Path = ".",
) -> dict[str, Any]:
    """Load persisted LLM orchestration reports and return a compact read-only summary."""
    root_path = Path(root)
    reports_dir = root_path / "runs" / run_id / "reports"
    report_path = reports_dir / "llm-orchestration-report.json"
    if not report_path.is_file():
        raise LLMRunInspectionError(
            f"No LLM orchestration report found for run_id={run_id}. "
            "Run run-llm-paper with --write-report first."
        )
    report = LLMOrchestrationReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
    budget_payload = _read_json_optional(reports_dir / "llm-budget-report.json")
    accounting_payload = _read_json_optional(reports_dir / "llm-call-accounting.json")
    safety_payload = _read_json_optional(reports_dir / "llm-run-safety-report.json")
    budget_usage = (
        LLMBudgetUsage.model_validate(budget_payload["budget_usage"])
        if isinstance(budget_payload, dict) and "budget_usage" in budget_payload
        else report.budget_usage
    )
    accounting_records = (
        [LLMCallAccountingRecord.model_validate(record) for record in accounting_payload]
        if isinstance(accounting_payload, list)
        else list(report.call_accounting)
    )
    safety_report = (
        LLMRunSafetyReport.model_validate(safety_payload)
        if isinstance(safety_payload, dict)
        else report.safety_report
    )
    call_counts = _llm_call_counts(accounting_records)
    safe_repair_path = reports_dir / "safe-repair-report.json"
    artifact_paths = _existing_llm_inspection_paths(root_path, run_id)
    summary = {
        "run_id": run_id,
        "orchestration_status": report.orchestration_status.value,
        "paper_release_status": report.release_status,
        "publication_ready": report.publication_ready,
        "safety_report_safe": safety_report.safe,
        "blocking_issues": report.blocking_issues,
        "top_level_warnings": report.warnings,
        "candidate_generation_calls": budget_usage.candidate_generation_calls,
        "review_calls": budget_usage.review_calls,
        "prose_calls": budget_usage.prose_calls,
        "claim_adjudication_calls": budget_usage.claim_adjudication_calls,
        "source_relevance_adjudication_calls": (
            budget_usage.source_relevance_adjudication_calls
        ),
        "quality_repair_calls": budget_usage.quality_repair_calls,
        "total_calls": budget_usage.total_calls,
        "estimated_cost_usd": budget_usage.estimated_cost_usd,
        "runtime_budget_blocked": (
            report.selected_backends.get("runtime_budget_blocked") == "true"
            or call_counts["blocked_call_count"] > 0
        ),
        **call_counts,
        "safe_repair_report_present": safe_repair_path.is_file(),
        "artifact_paths": artifact_paths,
        "reports_dir": reports_dir.relative_to(root_path).as_posix(),
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }
    return summary


def build_llm_orchestration_preflight_summary(
    config: LLMOrchestrationConfig,
    *,
    llm_scope: str = _LLM_SCOPE_FULL_PAPER,
    enable_safe_repair: bool = False,
) -> dict[str, Any]:
    """Return deterministic, secret-free preflight metadata for CLI/reporting."""
    normalized_scope = _normalize_llm_scope(llm_scope)
    effective_config = _effective_config_for_scope(config, normalized_scope)
    effective_safe_repair = (
        enable_safe_repair and normalized_scope == _LLM_SCOPE_FULL_PAPER
    )
    planned = _planned_usage(effective_config, llm_scope=normalized_scope)
    return {
        "llm_scope": normalized_scope,
        "candidate_backend": effective_config.candidate_backend,
        "candidate_model": effective_config.llm_model,
        "reviewer_backend": effective_config.reviewer_backend,
        "reviewer_model": effective_config.reviewer_model,
        "prose_backend": effective_config.prose_backend,
        "prose_model": effective_config.prose_model,
        "claim_adjudicator_backend": effective_config.claim_adjudicator_backend,
        "claim_adjudicator_model": effective_config.claim_adjudicator_model,
        "source_relevance_adjudicator_backend": (
            effective_config.source_relevance_adjudicator_backend
        ),
        "source_relevance_adjudicator_model": (
            effective_config.source_relevance_adjudicator_model
        ),
        "quality_repair_backend": effective_config.quality_repair_backend,
        "quality_repair_model": effective_config.quality_repair_model,
        "allow_external_calls": effective_config.allow_external_calls,
        "budget_limits": effective_config.budget.model_dump(mode="json"),
        "estimated_max_calls": planned.total_calls,
        "candidate_generation_calls": planned.candidate_generation_calls,
        "review_calls": planned.review_calls,
        "prose_calls": planned.prose_calls,
        "claim_adjudication_calls": planned.claim_adjudication_calls,
        "source_relevance_adjudication_calls": (
            planned.source_relevance_adjudication_calls
        ),
        "quality_repair_calls": planned.quality_repair_calls,
        "write_report": effective_config.write_report,
        "generate_paper": effective_config.generate_paper,
        "evaluate_release": effective_config.evaluate_release,
        "generate_paper_effective": effective_config.generate_paper,
        "evaluate_release_effective": effective_config.evaluate_release,
        "export_latex_effective": effective_config.export_latex,
        "safe_repair_effective": effective_safe_repair,
        "enable_retrieval": effective_config.enable_retrieval,
        "retrieval_backend": effective_config.retrieval_backend,
        "retrieval_local_path": effective_config.retrieval_local_path,
        "max_retrieval_sources": effective_config.max_retrieval_sources,
        "citation_policy": effective_config.citation_policy,
    }


def _read_json_optional(path: Path) -> Any | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _llm_call_counts(records: list[LLMCallAccountingRecord]) -> dict[str, int]:
    return {
        "external_call_count": sum(1 for record in records if record.external_call_performed),
        "failed_call_count": sum(
            1 for record in records if record.status == LLMCallStatus.FAILED
        ),
        "blocked_call_count": sum(
            1 for record in records if record.status == LLMCallStatus.BLOCKED
        ),
        "skipped_call_count": sum(
            1 for record in records if record.status == LLMCallStatus.SKIPPED
        ),
    }


def _existing_llm_inspection_paths(root: Path, run_id: str) -> dict[str, str]:
    candidates = {
        "llm_orchestration_report": root
        / "runs"
        / run_id
        / "reports"
        / "llm-orchestration-report.json",
        "llm_budget_report": root / "runs" / run_id / "reports" / "llm-budget-report.json",
        "llm_call_accounting": root
        / "runs"
        / run_id
        / "reports"
        / "llm-call-accounting.json",
        "llm_run_safety_report": root
        / "runs"
        / run_id
        / "reports"
        / "llm-run-safety-report.json",
        "safe_repair_report": root / "runs" / run_id / "reports" / "safe-repair-report.json",
        "retrieval_report": root / "runs" / run_id / "reports" / "retrieval-report.json",
        "retrieval_quality_report": root
        / "runs"
        / run_id
        / "reports"
        / "retrieval-quality-report.json",
        "citation_registry": root
        / "runs"
        / run_id
        / "reports"
        / "citation-registry.json",
        "full_paper_generation_report": root
        / "runs"
        / run_id
        / "reports"
        / "full-paper-generation-report.json",
        "full_paper_release_report": root
        / "runs"
        / run_id
        / "reports"
        / "full-paper-release-report.json",
        "complete_manuscript_draft": root
        / "runs"
        / run_id
        / "reports"
        / "complete-manuscript-draft.md",
        "revised_manuscript_draft": root
        / "runs"
        / run_id
        / "reports"
        / "revised-manuscript-draft.md",
        "paper": root / "runs" / run_id / "latex" / "paper.tex",
        "revised_paper": root / "runs" / run_id / "latex" / "revised-paper.tex",
        "latex_source_map": root / "runs" / run_id / "latex" / "latex-source-map.json",
        "revised_latex_source_map": root
        / "runs"
        / run_id
        / "latex"
        / "revised-latex-source-map.json",
    }
    return {
        key: path.relative_to(root).as_posix()
        for key, path in sorted(candidates.items())
        if path.is_file()
    }


def _normalize_llm_scope(value: str) -> str:
    normalized = value.strip().lower().replace("_", "-")
    if normalized not in _SUPPORTED_LLM_SCOPES:
        allowed = ", ".join(sorted(_SUPPORTED_LLM_SCOPES))
        raise LLMOrchestrationError(f"llm_scope must be one of: {allowed}")
    if normalized not in {
        _LLM_SCOPE_CANDIDATE_ONLY,
        _LLM_SCOPE_REVIEWER_ONLY,
        _LLM_SCOPE_FULL_PAPER,
    }:
        raise LLMOrchestrationError(
            f"llm_scope={normalized} is reserved for a future isolated smoke path."
        )
    return normalized


def _effective_config_for_scope(
    config: LLMOrchestrationConfig,
    llm_scope: str,
) -> LLMOrchestrationConfig:
    if llm_scope not in {_LLM_SCOPE_CANDIDATE_ONLY, _LLM_SCOPE_REVIEWER_ONLY}:
        return config
    update: dict[str, Any] = {
        "prose_backend": _FAKE_BACKEND,
        "generate_paper": False,
        "evaluate_release": False,
        "include_citations": False,
        "export_latex": False,
        "critique": False,
        "revise": False,
        "apply_safe_fake_revision": False,
        "reexport_latex_after_revision": False,
        "render_check": False,
        "enable_retrieval": False,
        "retrieval_backend": "fake",
        "citation_policy": "none",
        "claim_adjudicator_backend": "off",
        "source_relevance_adjudicator_backend": "off",
        "quality_repair_backend": "off",
    }
    if llm_scope == _LLM_SCOPE_CANDIDATE_ONLY:
        update["reviewer_backend"] = _FAKE_BACKEND
    return config.model_copy(update=update)


def _real_llm_mode(config: LLMOrchestrationConfig) -> bool:
    return any(
        backend.strip().lower() not in {_FAKE_BACKEND, "off", "deterministic"}
        for backend in (
            config.candidate_backend,
            config.reviewer_backend,
            config.prose_backend,
            config.claim_adjudicator_backend,
            config.source_relevance_adjudicator_backend,
            config.quality_repair_backend,
        )
    )


def _planned_usage(
    config: LLMOrchestrationConfig,
    *,
    llm_scope: str = _LLM_SCOPE_FULL_PAPER,
) -> LLMBudgetUsage:
    candidate_calls = _planned_candidate_generation_calls(config)
    review_calls = (
        0
        if llm_scope == _LLM_SCOPE_CANDIDATE_ONLY
        else planned_stage_b_review_calls(MAX_STAGE_A_SURVIVORS)
    )
    prose_calls = planned_manuscript_section_count() if config.generate_paper else 0
    adjudication_calls = (
        config.budget.max_claim_adjudication_calls or 0
        if config.generate_paper
        else 0
    )
    source_relevance_calls = (
        config.budget.max_source_relevance_adjudication_calls or 0
        if config.enable_retrieval
        else 0
    )
    quality_repair_calls = (
        config.budget.max_quality_repair_calls or 0
        if config.generate_paper
        else 0
    )
    real_calls = (
        (
            candidate_calls
            if config.candidate_backend.strip().lower() != _FAKE_BACKEND
            else 0
        )
        + (
            review_calls
            if config.reviewer_backend.strip().lower() != _FAKE_BACKEND
            else 0
        )
        + (
            prose_calls
            if config.prose_backend.strip().lower() != _FAKE_BACKEND
            else 0
        )
        + (
            adjudication_calls
            if config.claim_adjudicator_backend == "openai"
            else 0
        )
        + (
            source_relevance_calls
            if config.source_relevance_adjudicator_backend == "openai"
            else 0
        )
        + (
            quality_repair_calls
            if config.quality_repair_backend == "openai"
            else 0
        )
    )
    return build_planned_llm_usage(
        candidate_backend=config.candidate_backend,
        reviewer_backend=config.reviewer_backend,
        prose_backend=config.prose_backend,
        claim_adjudicator_backend=config.claim_adjudicator_backend,
        source_relevance_adjudicator_backend=(
            config.source_relevance_adjudicator_backend
        ),
        quality_repair_backend=config.quality_repair_backend,
        candidate_generation_calls=candidate_calls,
        review_calls=review_calls,
        prose_calls=prose_calls,
        claim_adjudication_calls=adjudication_calls,
        source_relevance_adjudication_calls=source_relevance_calls,
        quality_repair_calls=quality_repair_calls,
        input_tokens=1000 * real_calls if real_calls else None,
        output_tokens=500 * real_calls if real_calls else None,
        estimated_cost_usd=round(0.01 * real_calls, 6) if real_calls else None,
        rate_limit_per_minute=config.budget.rate_limit_per_minute,
    )


def _planned_candidate_generation_calls(config: LLMOrchestrationConfig) -> int:
    constraints = constraint_from_inputs(config.domain, config.method)
    if not constraints.domain or constraints.method:
        return 1
    return len(
        [
            opportunity
            for opportunity in discover_opportunities(constraints)
            if float(opportunity["opportunity_score"]) >= OPPORTUNITY_THRESHOLD
        ]
    )


def _registry_for_config(
    config: LLMOrchestrationConfig,
    *,
    llm_transport: LLMTransport | None,
    reviewer_transport: LLMTransport | None,
    prose_transport: LLMTransport | None,
    environ: dict[str, str] | None,
    runtime_budget_guard: RuntimeLLMBudgetGuard | None = None,
) -> AdapterRegistry:
    return get_adapter_registry(
        AdapterConfig(
            adapter_backend=config.candidate_backend,
            allow_external_calls=config.allow_external_calls,
            llm_model=config.llm_model,
            reviewer_backend=config.reviewer_backend,
            use_llm_reviewers=config.reviewer_backend != _FAKE_BACKEND,
            reviewer_model=config.reviewer_model,
            reviewer_max_objections=config.reviewer_max_objections,
            retrieval_backend=(
                config.retrieval_backend if config.enable_retrieval else "fake"
            ),
            retrieval_limit=config.max_retrieval_sources,
            prose_backend=config.prose_backend,
            prose_model=config.prose_model,
        ),
        llm_transport=_budgeted_transport(
            llm_transport,
            runtime_budget_guard,
            step_name="llm-candidate-generation",
            backend=config.candidate_backend,
        ),
        reviewer_transport=_budgeted_transport(
            reviewer_transport,
            runtime_budget_guard,
            step_name="llm-stage-b-review",
            backend=config.reviewer_backend,
        ),
        prose_transport=_budgeted_transport(
            prose_transport,
            runtime_budget_guard,
            step_name="llm-prose-generation",
            backend=config.prose_backend,
        ),
        environ=environ,
    )


@dataclass
class _BudgetedLLMTransport:
    delegate: LLMTransport
    guard: RuntimeLLMBudgetGuard
    step_name: str
    backend: str
    provider: str = "openai"

    @property
    def endpoint(self) -> str:
        return str(getattr(self.delegate, "endpoint", "https://api.openai.com/v1/responses"))

    def create_response(
        self,
        *,
        api_key: str,
        model: str,
        prompt: str,
        response_schema: dict[str, Any],
    ) -> Any:
        request_payload = {
            "operation": "responses.create",
            "backend": self.backend,
            "provider": self.provider,
            "model": model,
            "prompt_hash": sha256_json({"prompt": prompt}),
            "response_schema_hash": sha256_json(response_schema),
        }
        self.guard.authorize_call(
            step_name=self.step_name,
            backend=self.backend,
            provider=self.provider,
            model=model,
            request_payload=request_payload,
            input_token_estimate=_estimate_input_tokens(prompt),
            output_token_estimate=500,
            estimated_cost_usd=0.01,
        )
        return self.delegate.create_response(
            api_key=api_key,
            model=model,
            prompt=prompt,
            response_schema=response_schema,
        )


def _budgeted_transport(
    transport: LLMTransport | None,
    guard: RuntimeLLMBudgetGuard | None,
    *,
    step_name: str,
    backend: str,
) -> LLMTransport | None:
    if guard is None or backend.strip().lower() == _FAKE_BACKEND:
        return transport
    return _BudgetedLLMTransport(
        delegate=transport or OpenAIResponsesTransport(),
        guard=guard,
        step_name=step_name,
        backend=backend,
    )


def _claim_adjudicator_for_config(
    config: LLMOrchestrationConfig,
    *,
    transport: LLMTransport | None,
    environ: dict[str, str] | None,
    runtime_budget_guard: RuntimeLLMBudgetGuard | None,
) -> ClaimAdjudicator | None:
    backend = config.claim_adjudicator_backend
    if backend == "off":
        return None
    if backend == "fake":
        return FakeClaimAdjudicator()
    if not config.allow_external_calls:
        raise AdapterConfigurationError(
            "OpenAI claim adjudicator requires allow_external_calls=true."
        )
    max_calls = config.budget.max_claim_adjudication_calls
    if max_calls is None or max_calls < 1:
        raise AdapterConfigurationError(
            "OpenAI claim adjudicator requires --max-claim-adjudication-calls >= 1."
        )
    environment = environ if environ is not None else os.environ
    api_key = environment.get(OPENAI_API_KEY_ENV, "")
    if not api_key:
        raise AdapterConfigurationError(
            "OpenAI claim adjudicator requested but no API key is configured."
        )
    adjudication_transport = _budgeted_transport(
        transport,
        runtime_budget_guard,
        step_name="llm-claim-adjudication",
        backend="openai",
    )
    return OpenAIClaimAdjudicator(
        api_key=api_key,
        model=config.claim_adjudicator_model,
        transport=adjudication_transport or OpenAIResponsesTransport(),
        allow_external_calls=True,
        max_calls=max_calls,
    )


def _source_relevance_adjudicator_for_config(
    config: LLMOrchestrationConfig,
    *,
    transport: LLMTransport | None,
    environ: dict[str, str] | None,
    runtime_budget_guard: RuntimeLLMBudgetGuard | None,
) -> SourceRelevanceAdjudicator | None:
    backend = config.source_relevance_adjudicator_backend
    if backend == "off":
        return None
    if backend == "fake":
        return FakeSourceRelevanceAdjudicator(
            model=config.source_relevance_adjudicator_model
        )
    if not config.enable_retrieval:
        raise AdapterConfigurationError(
            "OpenAI source relevance adjudication requires --enable-retrieval."
        )
    if not config.allow_external_calls:
        raise AdapterConfigurationError(
            "OpenAI source relevance adjudicator requires allow_external_calls=true."
        )
    max_calls = config.budget.max_source_relevance_adjudication_calls
    if max_calls is None or max_calls < 1:
        raise AdapterConfigurationError(
            "OpenAI source relevance adjudicator requires "
            "--max-source-relevance-adjudication-calls >= 1."
        )
    environment = environ if environ is not None else os.environ
    api_key = environment.get(OPENAI_API_KEY_ENV, "")
    if not api_key:
        raise AdapterConfigurationError(
            "OpenAI source relevance adjudicator requested but no API key is configured."
        )
    adjudication_transport = _budgeted_transport(
        transport,
        runtime_budget_guard,
        step_name="llm-source-relevance-adjudication",
        backend="openai",
    )
    return OpenAISourceRelevanceAdjudicator(
        api_key=api_key,
        model=config.source_relevance_adjudicator_model,
        transport=adjudication_transport or OpenAIResponsesTransport(),
        allow_external_calls=True,
        max_calls=max_calls,
    )


def _estimate_input_tokens(prompt: str) -> int:
    return max(1, (len(prompt) + 3) // 4)


def _pipeline_config(config: LLMOrchestrationConfig, root: Path) -> PipelineRunConfig:
    return PipelineRunConfig(
        run_id=config.run_id,
        domain=config.domain,
        method=config.method,
        root=root,
        adapter_backend=config.candidate_backend,
        allow_external_calls=config.allow_external_calls,
        llm_model=config.llm_model,
        reviewer_backend=config.reviewer_backend,
        use_llm_reviewers=config.reviewer_backend != _FAKE_BACKEND,
        reviewer_model=config.reviewer_model,
        reviewer_max_objections=config.reviewer_max_objections,
        rerun_policy=_parse_rerun_policy(config.rerun_policy),
        force=config.force,
    )


def _full_paper_config(config: LLMOrchestrationConfig) -> FullPaperGenerationConfig:
    return FullPaperGenerationConfig(
        run_id=config.run_id,
        include_citations=config.include_citations,
        citation_policy=config.citation_policy,
        max_retrieval_sources=config.max_retrieval_sources,
        export_latex=config.export_latex,
        critique=config.critique,
        revise=config.revise,
        apply_safe_fake_revision=config.apply_safe_fake_revision,
        reexport_latex_after_revision=config.reexport_latex_after_revision,
        render_check=config.render_check,
        allow_external_tools=config.allow_external_tools,
        latex_executable=config.latex_executable,
        prose_backend=config.prose_backend,
        allow_external_calls=config.allow_external_calls,
        prose_model=config.prose_model,
        claim_adjudicator_backend=config.claim_adjudicator_backend,
        claim_adjudicator_model=config.claim_adjudicator_model,
        quality_repair_backend=config.quality_repair_backend,
        quality_repair_model=config.quality_repair_model,
        write_report=True,
        rerun_policy=_parse_rerun_policy(config.rerun_policy),
        force=config.force,
    )


def _run_bounded_citation_retrieval(
    *,
    config: LLMOrchestrationConfig,
    registry: AdapterRegistry,
    store: ArtifactStore,
    ledger: ResearchLedger,
    source_relevance_adjudicator: SourceRelevanceAdjudicator | None,
):
    query = f"{config.domain} bounded literature context"
    if config.retrieval_backend.strip().lower() == "local":
        if not config.retrieval_local_path:
            raise ValueError(
                "--retrieval-local-path is required when retrieval_backend=local"
            )
        return run_local_retrieval_with_provenance(
            run_id=config.run_id,
            query=query,
            limit=config.max_retrieval_sources,
            local_path=config.retrieval_local_path,
            store=store,
            ledger=ledger,
            source_relevance_adjudicator=source_relevance_adjudicator,
            source_relevance_adjudicator_model=(
                config.source_relevance_adjudicator_model
            ),
            domain=config.domain,
            candidate_title_or_problem=config.method or config.domain,
        )
    if registry.retrieval.is_fake:
        return run_fixture_retrieval_with_provenance(
            run_id=config.run_id,
            query=query,
            limit=config.max_retrieval_sources,
            retrieval_client=registry.retrieval,
            store=store,
            ledger=ledger,
        )
    return run_retrieval_with_provenance(
        run_id=config.run_id,
        query=query,
        limit=config.max_retrieval_sources,
        retrieval_client=registry.retrieval,
        store=store,
        ledger=ledger,
    )


def _run_candidate_only_scope(
    *,
    config: LLMOrchestrationConfig,
    root_path: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    registry: AdapterRegistry,
    clock: Clock,
    preflight_step: LLMOrchestrationStep,
    budget_decision: LLMBudgetDecision,
    runtime_budget_guard: RuntimeLLMBudgetGuard,
) -> LLMOrchestrationRunResult:
    steps = [preflight_step]
    warnings = list(budget_decision.warnings)
    blocking: list[str] = []
    stage_a_result: StageAResult | None = None
    stage_a_started = clock.now()
    try:
        stage_a_result = run_stage_a(
            run_id=config.run_id,
            constraints=constraint_from_inputs(config.domain, config.method),
            store=store,
            ledger=ledger,
            llm_client=registry.llm,
        )
    except (AdapterTransportError, LLMBudgetExceeded) as exc:
        transport_error = _find_transport_error(exc)
        error_text = _failure_message(exc, transport_error)
        blocked = isinstance(exc, LLMBudgetExceeded)
        steps.append(
            _step(
                "candidate-only-stage-a",
                (
                    LLMOrchestrationStepStatus.BLOCKED
                    if blocked
                    else LLMOrchestrationStepStatus.FAILED
                ),
                (
                    "Candidate-only Stage A was blocked by the runtime LLM budget."
                    if blocked
                    else "Candidate-only Stage A failed."
                ),
                stage_a_started,
                clock.now(),
                error=error_text,
            )
        )
        blocking.append(error_text)
        records = _accounting_records(registry, clock, runtime_budget_guard)
        if transport_error is not None:
            records.append(
                _failed_transport_record(
                    step_name="candidate-only-stage-a",
                    error=transport_error,
                    config=config,
                    clock=clock,
                )
            )
        report = _build_report(
            config=config,
            status=(
                LLMOrchestrationStatus.ORCHESTRATION_BLOCKED
                if blocked
                else LLMOrchestrationStatus.ORCHESTRATION_FAILED
            ),
            steps=steps,
            budget_decision=budget_decision,
            call_accounting=records,
            warnings=warnings,
            blocking=blocking,
            generation_result=None,
            release_result=None,
            observed_usage=observed_usage_from_records(records),
            llm_scope=_LLM_SCOPE_CANDIDATE_ONLY,
            runtime_budget_blocked=blocked,
        )
        return _maybe_persist(config, store, ledger, report, None, None, None)

    artifact_ids = sorted(
        {
            stage_a_result.report_artifact.id,
            *(artifact.id for artifact in stage_a_result.candidate_artifacts.values()),
            *(artifact.id for artifact in stage_a_result.score_artifacts.values()),
            *(artifact.id for artifact in stage_a_result.llm_artifacts.values()),
        }
    )
    steps.append(
        _step(
            "candidate-only-stage-a",
            LLMOrchestrationStepStatus.SUCCEEDED,
            "Isolated Stage A candidate generation completed.",
            stage_a_started,
            clock.now(),
            artifact_ids=artifact_ids,
        )
    )
    records = _accounting_records(registry, clock, runtime_budget_guard)
    report = _build_report(
        config=config,
        status=_orchestration_status(steps, blocking, warnings),
        steps=steps,
        budget_decision=budget_decision,
        call_accounting=records,
        warnings=warnings,
        blocking=blocking,
        generation_result=None,
        release_result=None,
        observed_usage=observed_usage_from_records(records),
        llm_scope=_LLM_SCOPE_CANDIDATE_ONLY,
        runtime_budget_blocked=False,
    )
    return _maybe_persist(config, store, ledger, report, None, None, None)


def _run_reviewer_only_scope(
    *,
    config: LLMOrchestrationConfig,
    store: ArtifactStore,
    ledger: ResearchLedger,
    registry: AdapterRegistry,
    clock: Clock,
    preflight_step: LLMOrchestrationStep,
    budget_decision: LLMBudgetDecision,
    runtime_budget_guard: RuntimeLLMBudgetGuard,
) -> LLMOrchestrationRunResult:
    """Run isolated Stage A candidate generation and Stage B review processing."""
    steps = [preflight_step]
    warnings = list(budget_decision.warnings)
    blocking: list[str] = []

    stage_a_started = clock.now()
    try:
        stage_a_result = run_stage_a(
            run_id=config.run_id,
            constraints=constraint_from_inputs(config.domain, config.method),
            store=store,
            ledger=ledger,
            llm_client=registry.llm,
        )
    except (AdapterTransportError, LLMBudgetExceeded) as exc:
        return _isolated_scope_failure(
            config=config,
            store=store,
            ledger=ledger,
            registry=registry,
            clock=clock,
            steps=steps,
            warnings=warnings,
            blocking=blocking,
            budget_decision=budget_decision,
            runtime_budget_guard=runtime_budget_guard,
            scope=_LLM_SCOPE_REVIEWER_ONLY,
            step_name="reviewer-only-stage-a",
            summary="Reviewer-only Stage A failed.",
            started_at=stage_a_started,
            exc=exc,
        )
    steps.append(
        _step(
            "reviewer-only-stage-a",
            LLMOrchestrationStepStatus.SUCCEEDED,
            "Reviewer-only prerequisite Stage A completed.",
            stage_a_started,
            clock.now(),
            artifact_ids=_stage_a_artifact_ids(stage_a_result),
        )
    )

    stage_b_started = clock.now()
    try:
        stage_b_result = run_stage_b(
            run_id=config.run_id,
            store=store,
            ledger=ledger,
            reviewer_client=registry.reviewer,
        )
    except (AdapterTransportError, LLMBudgetExceeded) as exc:
        return _isolated_scope_failure(
            config=config,
            store=store,
            ledger=ledger,
            registry=registry,
            clock=clock,
            steps=steps,
            warnings=warnings,
            blocking=blocking,
            budget_decision=budget_decision,
            runtime_budget_guard=runtime_budget_guard,
            scope=_LLM_SCOPE_REVIEWER_ONLY,
            step_name="reviewer-only-stage-b",
            summary="Reviewer-only Stage B failed.",
            started_at=stage_b_started,
            exc=exc,
        )

    artifact_ids = {
        stage_b_result.report_artifact.id,
        *(artifact.id for values in stage_b_result.artifacts.values() for artifact in values),
        *(artifact.id for artifact in stage_b_result.llm_reviewer_artifacts),
    }
    steps.append(
        _step(
            "reviewer-only-stage-b",
            LLMOrchestrationStepStatus.SUCCEEDED,
            "Isolated Stage B reviewer path completed.",
            stage_b_started,
            clock.now(),
            artifact_ids=sorted(artifact_ids),
        )
    )
    records = _accounting_records(registry, clock, runtime_budget_guard)
    report = _build_report(
        config=config,
        status=_orchestration_status(steps, blocking, warnings),
        steps=steps,
        budget_decision=budget_decision,
        call_accounting=records,
        warnings=warnings,
        blocking=blocking,
        generation_result=None,
        release_result=None,
        observed_usage=observed_usage_from_records(records),
        llm_scope=_LLM_SCOPE_REVIEWER_ONLY,
        runtime_budget_blocked=False,
    )
    return _maybe_persist(config, store, ledger, report, None, None, None)


def _isolated_scope_failure(
    *,
    config: LLMOrchestrationConfig,
    store: ArtifactStore,
    ledger: ResearchLedger,
    registry: AdapterRegistry,
    clock: Clock,
    steps: list[LLMOrchestrationStep],
    warnings: list[str],
    blocking: list[str],
    budget_decision: LLMBudgetDecision,
    runtime_budget_guard: RuntimeLLMBudgetGuard,
    scope: str,
    step_name: str,
    summary: str,
    started_at: str,
    exc: AdapterTransportError | LLMBudgetExceeded,
) -> LLMOrchestrationRunResult:
    transport_error = _find_transport_error(exc)
    error_text = _failure_message(exc, transport_error)
    blocked = isinstance(exc, LLMBudgetExceeded)
    steps.append(
        _step(
            step_name,
            (
                LLMOrchestrationStepStatus.BLOCKED
                if blocked
                else LLMOrchestrationStepStatus.FAILED
            ),
            (
                f"{summary.removesuffix('.')} because the runtime LLM budget was exceeded."
                if blocked
                else summary
            ),
            started_at,
            clock.now(),
            error=error_text,
        )
    )
    blocking.append(error_text)
    records = _accounting_records(registry, clock, runtime_budget_guard)
    if transport_error is not None:
        records.append(
            _failed_transport_record(
                step_name=step_name,
                error=transport_error,
                config=config,
                clock=clock,
            )
        )
    report = _build_report(
        config=config,
        status=(
            LLMOrchestrationStatus.ORCHESTRATION_BLOCKED
            if blocked
            else LLMOrchestrationStatus.ORCHESTRATION_FAILED
        ),
        steps=steps,
        budget_decision=budget_decision,
        call_accounting=records,
        warnings=warnings,
        blocking=blocking,
        generation_result=None,
        release_result=None,
        observed_usage=observed_usage_from_records(records),
        llm_scope=scope,
        runtime_budget_blocked=blocked,
    )
    return _maybe_persist(config, store, ledger, report, None, None, None)


def _stage_a_artifact_ids(result: StageAResult) -> list[str]:
    return sorted(
        {
            result.report_artifact.id,
            *(artifact.id for artifact in result.candidate_artifacts.values()),
            *(artifact.id for artifact in result.score_artifacts.values()),
            *(artifact.id for artifact in result.llm_artifacts.values()),
        }
    )


def _parse_rerun_policy(value: str) -> RerunPolicy:
    normalized = value.lower().replace("-", "").replace("_", "")
    policies = {
        "failifexists": RerunPolicy.FAIL_IF_EXISTS,
        "skipifcomplete": RerunPolicy.SKIP_IF_COMPLETE,
        "allowifforced": RerunPolicy.ALLOW_IF_FORCED,
        "readonlyonly": RerunPolicy.READ_ONLY_ONLY,
    }
    try:
        return policies[normalized]
    except KeyError as exc:
        raise LLMOrchestrationError(
            "rerun_policy must be one of fail-if-exists, skip-if-complete, "
            "allow-if-forced, read-only-only"
        ) from exc


def _step(
    name: str,
    status: LLMOrchestrationStepStatus,
    summary: str,
    started_at: str,
    completed_at: str,
    *,
    artifact_ids: list[str] | None = None,
    warnings: list[str] | None = None,
    error: str | None = None,
) -> LLMOrchestrationStep:
    return LLMOrchestrationStep(
        step_name=name,
        status=status,
        summary=summary,
        artifact_ids=sorted(set(artifact_ids or [])),
        warnings=sorted(set(warnings or [])),
        error_message=error,
        started_at=started_at,
        completed_at=completed_at,
    )


def _accounting_records(
    registry: AdapterRegistry,
    clock: Clock,
    runtime_budget_guard: RuntimeLLMBudgetGuard | None = None,
    claim_adjudicator: ClaimAdjudicator | None = None,
    source_relevance_adjudicator: SourceRelevanceAdjudicator | None = None,
) -> list[LLMCallAccountingRecord]:
    records: list[LLMCallAccountingRecord] = []
    records.extend(
        _records_from_traces(
            step_name="llm-candidate-generation",
            traces=getattr(registry.llm, "generation_traces", []),
            backend=registry.config.adapter_backend,
            provider=getattr(registry.llm, "provider_name", registry.config.adapter_backend),
            model=registry.config.llm_model,
            clock=clock,
        )
    )
    records.extend(
        _records_from_traces(
            step_name="llm-stage-b-review",
            traces=getattr(registry.reviewer, "review_traces", []),
            backend=registry.config.reviewer_backend,
            provider=getattr(registry.reviewer, "provider_name", registry.config.reviewer_backend),
            model=registry.config.reviewer_model,
            clock=clock,
        )
    )
    prose_requests = list(getattr(registry.prose_generator, "generation_requests", []))
    prose_responses = list(getattr(registry.prose_generator, "raw_responses", []))
    prose_diagnostics = list(
        getattr(registry.prose_generator, "request_diagnostics", [])
    )
    for index, request in enumerate(prose_requests):
        response = prose_responses[index] if index < len(prose_responses) else None
        request_payload: Any = request.model_dump(mode="json")
        if index < len(prose_diagnostics):
            request_payload = {
                "request": request_payload,
                "request_diagnostics": prose_diagnostics[index],
            }
        records.append(
            build_call_accounting_record(
                step_name="llm-prose-generation",
                backend=registry.config.prose_backend,
                provider=getattr(
                    registry.prose_generator,
                    "provider_name",
                    registry.config.prose_backend,
                ),
                model=registry.config.prose_model,
                request_payload=request_payload,
                response_payload=response,
                started_at=clock.now(),
                completed_at=clock.now(),
                status=LLMCallStatus.SUCCEEDED,
                input_token_estimate=_estimate_request_tokens(request_payload),
                output_token_estimate=(
                    500 if registry.config.prose_backend != _FAKE_BACKEND else None
                ),
                estimated_cost_usd=(
                    0.01 if registry.config.prose_backend != _FAKE_BACKEND else None
                ),
                external_call_performed=registry.config.prose_backend != _FAKE_BACKEND,
            )
        )
    if claim_adjudicator is not None and claim_adjudicator.backend_name == "openai":
        requests = list(getattr(claim_adjudicator, "adjudication_requests", []))
        responses = list(getattr(claim_adjudicator, "raw_responses", []))
        for index, request_payload in enumerate(requests):
            records.append(
                build_call_accounting_record(
                    step_name="llm-claim-adjudication",
                    backend="openai",
                    provider="openai",
                    model=claim_adjudicator.model,
                    request_payload=request_payload,
                    response_payload=(
                        responses[index] if index < len(responses) else None
                    ),
                    started_at=clock.now(),
                    completed_at=clock.now(),
                    status=LLMCallStatus.SUCCEEDED,
                    input_token_estimate=_estimate_request_tokens(request_payload),
                    output_token_estimate=500,
                    estimated_cost_usd=0.01,
                    external_call_performed=True,
                )
            )
    if source_relevance_adjudicator is not None:
        requests = list(getattr(source_relevance_adjudicator, "adjudication_requests", []))
        responses = list(getattr(source_relevance_adjudicator, "raw_responses", []))
        backend = source_relevance_adjudicator.backend_name
        for index, request_payload in enumerate(requests):
            records.append(
                build_call_accounting_record(
                    step_name="llm-source-relevance-adjudication",
                    backend=backend,
                    provider=(
                        "openai"
                        if backend == "openai"
                        else getattr(
                            source_relevance_adjudicator,
                            "provider_name",
                            backend,
                        )
                    ),
                    model=source_relevance_adjudicator.model,
                    request_payload=request_payload,
                    response_payload=(
                        responses[index] if index < len(responses) else None
                    ),
                    started_at=clock.now(),
                    completed_at=clock.now(),
                    status=LLMCallStatus.SUCCEEDED,
                    input_token_estimate=_estimate_request_tokens(request_payload),
                    output_token_estimate=500 if backend == "openai" else None,
                    estimated_cost_usd=0.01 if backend == "openai" else None,
                    external_call_performed=backend == "openai",
                )
            )
    if runtime_budget_guard is not None:
        records.extend(runtime_budget_guard.blocked_records)
    if not records:
        records.extend(_fake_skipped_records(registry, clock))
    return sorted(
        records,
        key=lambda record: (
            record.step_name,
            record.backend,
            record.model or "",
            record.request_hash,
        ),
    )


def _records_from_traces(
    *,
    step_name: str,
    traces: list[Any],
    backend: str,
    provider: str,
    model: str,
    clock: Clock,
) -> list[LLMCallAccountingRecord]:
    return [
        build_call_accounting_record(
            step_name=step_name,
            backend=backend,
            provider=provider,
            model=model,
            request_payload=trace.request,
            response_payload=getattr(trace, "raw_response", None),
            started_at=clock.now(),
            completed_at=clock.now(),
            status=LLMCallStatus.SUCCEEDED,
            input_token_estimate=_estimate_request_tokens(trace.request),
            output_token_estimate=500 if backend != _FAKE_BACKEND else None,
            estimated_cost_usd=0.01 if backend != _FAKE_BACKEND else None,
            external_call_performed=backend != _FAKE_BACKEND,
        )
        for trace in traces
    ]


def _estimate_request_tokens(request_payload: Any) -> int | None:
    if not isinstance(request_payload, dict):
        return None
    prompt_contract = request_payload.get("prompt_contract")
    if isinstance(prompt_contract, dict):
        prompt_text = prompt_contract.get("prompt_text")
        if isinstance(prompt_text, str):
            return _estimate_input_tokens(prompt_text)
    request = request_payload.get("request")
    if isinstance(request, dict):
        return _estimate_request_tokens(request)
    if "sentences" in request_payload:
        return _estimate_input_tokens(json.dumps(request_payload, sort_keys=True))
    if "sources" in request_payload:
        return _estimate_input_tokens(json.dumps(request_payload, sort_keys=True))
    return None


def _fake_skipped_records(
    registry: AdapterRegistry,
    clock: Clock,
) -> list[LLMCallAccountingRecord]:
    records = []
    for step_name, backend, model in (
        ("llm-candidate-generation", registry.config.adapter_backend, registry.config.llm_model),
        ("llm-stage-b-review", registry.config.reviewer_backend, registry.config.reviewer_model),
        ("llm-prose-generation", registry.config.prose_backend, registry.config.prose_model),
    ):
        records.append(
            build_call_accounting_record(
                step_name=step_name,
                backend=backend,
                provider=backend,
                model=model,
                request_payload={"step_name": step_name, "backend": backend, "fake": True},
                response_payload=None,
                started_at=clock.now(),
                completed_at=clock.now(),
                status=LLMCallStatus.SKIPPED,
                external_call_performed=False,
            )
        )
    return records


def _find_transport_error(error: BaseException) -> AdapterTransportError | None:
    seen: set[int] = set()
    stack: list[BaseException | None] = [error]
    while stack:
        current = stack.pop()
        if current is None or id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, AdapterTransportError):
            return current
        stack.append(current.__cause__)
        stack.append(current.__context__)
    return None


def _failure_message(
    error: BaseException,
    transport_error: AdapterTransportError | None,
) -> str:
    if transport_error is None:
        return str(error)
    return f"{error}; transport_error={transport_error}"


def _failed_transport_record(
    *,
    step_name: str,
    error: AdapterTransportError,
    config: LLMOrchestrationConfig,
    clock: Clock,
) -> LLMCallAccountingRecord:
    payload = error_payload(error)
    return build_call_accounting_record(
        step_name=step_name,
        backend=error.backend,
        provider=error.provider,
        model=_model_for_transport_error(error, config),
        request_payload={
            "failure_stage": step_name,
            "backend": error.backend,
            "provider": error.provider,
            "operation": error.operation,
            "status_code": error.status_code,
            "url": payload.get("url"),
            "message": payload.get("message"),
        },
        response_payload=payload,
        started_at=clock.now(),
        completed_at=clock.now(),
        status=LLMCallStatus.FAILED,
        error_type=type(error).__name__,
        external_call_performed=True,
    )


def _model_for_transport_error(
    error: AdapterTransportError,
    config: LLMOrchestrationConfig,
) -> str | None:
    if error.backend != "openai":
        return None
    if config.candidate_backend == "openai":
        return config.llm_model
    if config.reviewer_backend == "openai":
        return config.reviewer_model
    if config.prose_backend == "openai":
        return config.prose_model
    if config.claim_adjudicator_backend == "openai":
        return config.claim_adjudicator_model
    if config.source_relevance_adjudicator_backend == "openai":
        return config.source_relevance_adjudicator_model
    return None


def _orchestration_status(
    steps: list[LLMOrchestrationStep],
    blocking: list[str],
    warnings: list[str],
) -> LLMOrchestrationStatus:
    if any(step.status == LLMOrchestrationStepStatus.FAILED for step in steps):
        return LLMOrchestrationStatus.ORCHESTRATION_FAILED
    if blocking or any(step.status == LLMOrchestrationStepStatus.BLOCKED for step in steps):
        return LLMOrchestrationStatus.ORCHESTRATION_BLOCKED
    if warnings or any(
        step.status == LLMOrchestrationStepStatus.SUCCEEDED_WITH_WARNINGS
        for step in steps
    ):
        return LLMOrchestrationStatus.ORCHESTRATION_SUCCEEDED_WITH_WARNINGS
    return LLMOrchestrationStatus.ORCHESTRATION_SUCCEEDED


def _build_report(
    *,
    config: LLMOrchestrationConfig,
    status: LLMOrchestrationStatus,
    steps: list[LLMOrchestrationStep],
    budget_decision: LLMBudgetDecision,
    call_accounting: list[LLMCallAccountingRecord],
    warnings: list[str],
    blocking: list[str],
    generation_result: FullPaperGenerationRunResult | None,
    release_result: FullPaperReleaseRunResult | None,
    observed_usage: LLMBudgetUsage | None = None,
    llm_scope: str = _LLM_SCOPE_FULL_PAPER,
    runtime_budget_blocked: bool = False,
) -> LLMOrchestrationReport:
    safety = LLMRunSafetyReport(
        run_id=config.run_id,
        safe=not blocking,
        warnings=[
            "LLM outputs are not verification evidence.",
            "LLM reviews are not proof evidence.",
            "LLM prose is not proof, experiment, retrieval, human approval, "
            "scientific validation, or publication readiness.",
            "Source relevance adjudication is bounded background-context filtering only.",
            "Release status is human-review readiness only.",
        ],
    )
    return LLMOrchestrationReport(
        report_id=f"llm-orchestration-report-{config.run_id}",
        run_id=config.run_id,
        config=config,
        orchestration_status=status,
        steps=steps,
        budget_decision=budget_decision,
        budget_usage=observed_usage or budget_decision.planned_usage,
        call_accounting=call_accounting,
        safety_report=safety,
        selected_backends={
            "llm_scope": llm_scope,
            "candidate_backend": config.candidate_backend,
            "candidate_model": config.llm_model,
            "reviewer_backend": config.reviewer_backend,
            "reviewer_model": config.reviewer_model,
            "prose_backend": config.prose_backend,
            "prose_model": config.prose_model,
            "claim_adjudicator_backend": config.claim_adjudicator_backend,
            "claim_adjudicator_model": config.claim_adjudicator_model,
            "source_relevance_adjudicator_backend": (
                config.source_relevance_adjudicator_backend
            ),
            "source_relevance_adjudicator_model": (
                config.source_relevance_adjudicator_model
            ),
            "quality_repair_backend": config.quality_repair_backend,
            "quality_repair_model": config.quality_repair_model,
            "retrieval_enabled": str(config.enable_retrieval).lower(),
            "retrieval_backend": config.retrieval_backend,
            "retrieval_local_path": config.retrieval_local_path or "",
            "citation_policy": config.citation_policy,
            "generate_paper_effective": str(config.generate_paper).lower(),
            "evaluate_release_effective": str(config.evaluate_release).lower(),
            "export_latex_effective": str(config.export_latex).lower(),
            "runtime_budget_blocked": str(runtime_budget_blocked).lower(),
            "preflight_status": (
                next(
                    (
                        step.status.value
                        for step in steps
                        if step.step_name == "preflight"
                    ),
                    "NotRecorded",
                )
            ),
        },
        generate_paper_status=(
            generation_result.report.generation_status.value
            if generation_result is not None
            else None
        ),
        release_status=(
            release_result.report.decision.status.value if release_result is not None else None
        ),
        warnings=sorted(set(warnings)),
        blocking_issues=sorted(set(blocking)),
    )


def _maybe_persist(
    config: LLMOrchestrationConfig,
    store: ArtifactStore,
    ledger: ResearchLedger,
    report: LLMOrchestrationReport,
    pipeline_report: Any | None,
    generation_result: FullPaperGenerationRunResult | None,
    release_result: FullPaperReleaseRunResult | None,
) -> LLMOrchestrationRunResult:
    if not config.write_report:
        return LLMOrchestrationRunResult(
            run_id=config.run_id,
            report=report,
            pipeline_report=pipeline_report,
            generation_result=generation_result,
            release_result=release_result,
        )
    if any(
        commit.action_type == ControllerActionType.LLM_ORCHESTRATION_WRITTEN
        for commit in ledger.list_commits(config.run_id)
    ):
        raise LLMOrchestrationError("LLM orchestration report already exists for this run")
    metadata = {
        "stage": "llm_orchestration",
        "artifact_role": "llm_orchestration_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }
    persistence = persist_artifacts_with_commit(
        run_id=config.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                "llm-orchestration-config",
                ArtifactType.REPORT,
                config,
                "json",
                metadata,
            ),
            ArtifactWriteSpec(
                "llm-budget-report",
                ArtifactType.REPORT,
                {
                    "budget_decision": report.budget_decision.model_dump(mode="json"),
                    "budget_usage": report.budget_usage.model_dump(mode="json"),
                },
                "json",
                metadata,
            ),
            ArtifactWriteSpec(
                "llm-call-accounting",
                ArtifactType.REPORT,
                [record.model_dump(mode="json") for record in report.call_accounting],
                "json",
                metadata,
            ),
            ArtifactWriteSpec(
                "llm-orchestration-report",
                ArtifactType.REPORT,
                report,
                "json",
                metadata,
            ),
            ArtifactWriteSpec(
                "llm-run-safety-report",
                ArtifactType.REPORT,
                report.safety_report,
                "json",
                metadata,
            ),
        ],
        action_type=ControllerActionType.LLM_ORCHESTRATION_WRITTEN,
        commit_payload={
            "run_id": config.run_id,
            "orchestration_status": report.orchestration_status.value,
            "total_llm_calls": report.budget_usage.total_calls,
            "candidate_backend": config.candidate_backend,
            "reviewer_backend": config.reviewer_backend,
            "prose_backend": config.prose_backend,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
        },
    )
    refs = {artifact.id: artifact for artifact in persistence.artifacts}
    return LLMOrchestrationRunResult(
        run_id=config.run_id,
        report=report,
        pipeline_report=pipeline_report,
        generation_result=generation_result,
        release_result=release_result,
        persistence=persistence,
        config_artifact=refs.get("llm-orchestration-config"),
        budget_artifact=refs.get("llm-budget-report"),
        accounting_artifact=refs.get("llm-call-accounting"),
        report_artifact=refs.get("llm-orchestration-report"),
        safety_artifact=refs.get("llm-run-safety-report"),
    )


def _budget_error_message(decision: LLMBudgetDecision) -> str:
    reasons = "; ".join(decision.reasons) or decision.decision_status.value
    return f"LLM budget blocked orchestration: {reasons}"


__all__ = [
    "LLMOrchestrationError",
    "LLMOrchestrationRunResult",
    "LLMRunInspectionError",
    "build_llm_orchestration_preflight_summary",
    "inspect_llm_run_summary",
    "llm_orchestration_result_model",
    "run_llm_paper_orchestration",
]
