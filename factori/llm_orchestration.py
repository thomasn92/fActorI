"""Explicit gated LLM-assisted full-paper orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.adapters.config import AdapterConfig
from factori.adapters.errors import (
    AdapterConfigurationError,
    AdapterTransportError,
    error_payload,
)
from factori.adapters.llm_real import LLMTransport
from factori.adapters.registry import AdapterRegistry, get_adapter_registry
from factori.artifacts import ArtifactStore
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
from factori.ledger import ResearchLedger
from factori.llm_budget import (
    budget_is_explicit,
    build_call_accounting_record,
    build_planned_llm_usage,
    evaluate_llm_budget,
    observed_usage_from_records,
)
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
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
from factori.storage_protocols import Clock, SystemClock

_FAKE_BACKEND = "fake"
_READY_RELEASE_STATUSES = {
    FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW,
    FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW_WITH_WARNINGS,
}


class LLMOrchestrationError(RuntimeError):
    """Raised when gated LLM paper orchestration fails closed."""


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
    environ: dict[str, str] | None = None,
    clock: Clock | None = None,
    preflight_only: bool = False,
) -> LLMOrchestrationRunResult:
    """Run the explicitly gated LLM-assisted paper-generation workflow."""
    root_path = Path(root)
    clock = clock or SystemClock()
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
    planned_usage = _planned_usage(config)
    budget_decision = evaluate_llm_budget(
        config.budget,
        planned_usage,
        require_explicit_budget=real_mode,
    )
    if not budget_decision.allowed:
        raise LLMOrchestrationError(_budget_error_message(budget_decision))

    try:
        registry = _registry_for_config(
            config,
            llm_transport=llm_transport,
            reviewer_transport=reviewer_transport,
            prose_transport=prose_transport,
            environ=environ,
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
            call_accounting=_accounting_records(registry, clock),
            warnings=list(budget_decision.warnings),
            blocking=[],
            generation_result=None,
            release_result=None,
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

    pipeline_started = clock.now()
    try:
        pipeline_report = run_deterministic_pipeline(
            _pipeline_config(config, root_path),
            clock=clock,
            adapter_registry=registry,
        )
    except (PipelineRunError, AdapterTransportError) as exc:
        transport_error = _find_transport_error(exc)
        error_text = _failure_message(exc, transport_error)
        steps.append(
            _step(
                "run-all",
                LLMOrchestrationStepStatus.FAILED,
                "Pipeline execution failed.",
                pipeline_started,
                clock.now(),
                error=error_text,
            )
        )
        blocking.append(error_text)
        records = _accounting_records(registry, clock)
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
            status=LLMOrchestrationStatus.ORCHESTRATION_FAILED,
            steps=steps,
            budget_decision=budget_decision,
            call_accounting=records,
            warnings=warnings,
            blocking=blocking,
            generation_result=None,
            release_result=None,
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

    if config.generate_paper:
        generation_started = clock.now()
        try:
            generation_result = generate_full_paper(
                run_id=config.run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                prose_generator=registry.prose_generator,
                config=_full_paper_config(config),
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
                "Paper generation was skipped by configuration.",
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

    records = _accounting_records(registry, clock)
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


def build_llm_orchestration_preflight_summary(
    config: LLMOrchestrationConfig,
) -> dict[str, Any]:
    """Return deterministic, secret-free preflight metadata for CLI/reporting."""
    planned = _planned_usage(config)
    return {
        "candidate_backend": config.candidate_backend,
        "candidate_model": config.llm_model,
        "reviewer_backend": config.reviewer_backend,
        "reviewer_model": config.reviewer_model,
        "prose_backend": config.prose_backend,
        "prose_model": config.prose_model,
        "allow_external_calls": config.allow_external_calls,
        "budget_limits": config.budget.model_dump(mode="json"),
        "estimated_max_calls": planned.total_calls,
        "write_report": config.write_report,
        "generate_paper": config.generate_paper,
        "evaluate_release": config.evaluate_release,
    }


def _real_llm_mode(config: LLMOrchestrationConfig) -> bool:
    return any(
        backend.strip().lower() != _FAKE_BACKEND
        for backend in (
            config.candidate_backend,
            config.reviewer_backend,
            config.prose_backend,
        )
    )


def _planned_usage(config: LLMOrchestrationConfig) -> LLMBudgetUsage:
    real_calls = sum(
        1
        for backend in (
            config.candidate_backend,
            config.reviewer_backend,
            config.prose_backend,
        )
        if backend.strip().lower() != _FAKE_BACKEND
    )
    return build_planned_llm_usage(
        candidate_backend=config.candidate_backend,
        reviewer_backend=config.reviewer_backend,
        prose_backend=config.prose_backend,
        candidate_generation_calls=1,
        review_calls=1,
        prose_calls=1,
        input_tokens=1000 * real_calls if real_calls else None,
        output_tokens=500 * real_calls if real_calls else None,
        estimated_cost_usd=round(0.01 * real_calls, 6) if real_calls else None,
        rate_limit_per_minute=config.budget.rate_limit_per_minute,
    )


def _registry_for_config(
    config: LLMOrchestrationConfig,
    *,
    llm_transport: LLMTransport | None,
    reviewer_transport: LLMTransport | None,
    prose_transport: LLMTransport | None,
    environ: dict[str, str] | None,
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
            prose_backend=config.prose_backend,
            prose_model=config.prose_model,
        ),
        llm_transport=llm_transport,
        reviewer_transport=reviewer_transport,
        prose_transport=prose_transport,
        environ=environ,
    )


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
        write_report=True,
        rerun_policy=_parse_rerun_policy(config.rerun_policy),
        force=config.force,
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
                external_call_performed=registry.config.prose_backend != _FAKE_BACKEND,
            )
        )
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
            external_call_performed=backend != _FAKE_BACKEND,
        )
        for trace in traces
    ]


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
) -> LLMOrchestrationReport:
    safety = LLMRunSafetyReport(
        run_id=config.run_id,
        safe=not blocking,
        warnings=[
            "LLM outputs are not verification evidence.",
            "LLM reviews are not proof evidence.",
            "LLM prose is not proof, experiment, retrieval, human approval, "
            "scientific validation, or publication readiness.",
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
            "candidate_backend": config.candidate_backend,
            "candidate_model": config.llm_model,
            "reviewer_backend": config.reviewer_backend,
            "reviewer_model": config.reviewer_model,
            "prose_backend": config.prose_backend,
            "prose_model": config.prose_model,
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
    "build_llm_orchestration_preflight_summary",
    "llm_orchestration_result_model",
    "run_llm_paper_orchestration",
]
