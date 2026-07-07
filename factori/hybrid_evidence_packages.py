"""Hybrid evidence-package planning, bounded execution, and inspection."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from factori.adapters.errors import AdapterError
from factori.adapters.hybrid_evidence import (
    PACKAGE_ALLOWED_LABELS,
    HybridEvidenceClient,
)
from factori.adapters.llm_experiment_codegen import ExperimentCodeGenerationClient
from factori.artifacts import ArtifactStore
from factori.generated_experiment_safety import audit_generated_experiment_code
from factori.generated_experiments import (
    _blocked_observation,
    _execute_in_sandbox,
    extract_metrics_from_output,
)
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.production_mode import evaluate_production_mode, stage_backend_record
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BackendKind,
    ControllerActionType,
    DeepOpportunityDiscoveryReport,
    EvidenceArtifactPlan,
    EvidenceArtifactType,
    EvidencePackageExecutionInspectionReport,
    EvidencePackageExecutionReport,
    EvidencePackageExecutionResult,
    ExperimentCodeSafetyAudit,
    HybridEvidencePackageCandidate,
    HybridEvidencePackageConfig,
    HybridEvidencePackageInspectionReport,
    HybridEvidencePackageRawArtifact,
    HybridEvidencePackageReport,
    HybridEvidencePackageScore,
    LLMExperimentCodeArtifact,
    LLMExperimentCodeRawArtifact,
    LLMRoutePlanningReport,
    LLMSubstrateConstructionReport,
    MetricExtractionResult,
    ProductionModePolicy,
    RetrievalContext,
    SandboxExecutionConfig,
    SandboxExecutionResult,
    ScientificStageKind,
    StageBackendRecord,
)

_SUBSTRATE_RE = re.compile(r"^llm-substrate-construction-report-(\d{4})\.json$")
_ROUTE_RE = re.compile(r"^llm-route-planning-report-(\d{4})\.json$")
_PACKAGE_RE = re.compile(r"^hybrid-evidence-package-report-(\d{4})\.json$")
_PACKAGE_RAW_RE = re.compile(r"^hybrid-evidence-package-raw-(\d{4})\.json$")
_EXECUTION_RE = re.compile(r"^evidence-package-execution-report-(\d{4})\.json$")
_CODE_RE = re.compile(r"^evidence-package-code-artifact-(\d{4})\.py$")
_CODE_RAW_RE = re.compile(r"^hybrid-evidence-code-raw-(\d{4})\.json$")
_DRAFT_RAW_RE = re.compile(r"^hybrid-evidence-draft-raw-(\d{4})\.json$")
_AUDIT_RE = re.compile(r"^evidence-package-code-safety-audit-(\d{4})\.json$")
_SANDBOX_RE = re.compile(r"^evidence-package-sandbox-execution-(\d{4})\.json$")
_METRIC_RE = re.compile(r"^evidence-package-metric-extraction-(\d{4})\.json$")
_RESULT_RE = re.compile(r"^evidence-package-result-(\d{4})\.json$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_BRANCH_ROUTE_MAP = {
    EvidenceArtifactType.BENCHMARK_TOURNAMENT: "benchmark_tournament",
    EvidenceArtifactType.COUNTEREXAMPLE_SEARCH: "counterexample_search",
}
_CODE_ARTIFACT_TYPES = {
    EvidenceArtifactType.NUMERICAL_ILLUSTRATION,
    EvidenceArtifactType.SYNTHETIC_EXPERIMENT,
    EvidenceArtifactType.BENCHMARK_TOURNAMENT,
    EvidenceArtifactType.COUNTEREXAMPLE_SEARCH,
    EvidenceArtifactType.NEGATIVE_CONTROL,
    EvidenceArtifactType.ROBUSTNESS_SWEEP,
}
_SYMBOLIC_TYPES = {
    EvidenceArtifactType.SYMBOLIC_REDUCTION,
    EvidenceArtifactType.SYMBOLIC_DERIVATION,
    EvidenceArtifactType.PROOF_PLAN,
}


class HybridEvidencePackageError(RuntimeError):
    """Raised when hybrid evidence-package planning or execution cannot proceed safely."""


@dataclass(frozen=True)
class HybridEvidencePackageStageResult:
    run_id: str
    report: HybridEvidencePackageReport | EvidencePackageExecutionReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


def plan_hybrid_evidence_packages(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    planner: HybridEvidenceClient,
    config: HybridEvidencePackageConfig,
) -> HybridEvidencePackageStageResult:
    """Plan one hybrid evidence package per selected LLM ScientificSubstrate."""
    if config.run_id != run_id:
        raise HybridEvidencePackageError("Hybrid evidence config run_id does not match run_id.")
    if planner.backend_kind not in {BackendKind.LLM_OPENAI, BackendKind.LLM_OTHER}:
        raise HybridEvidencePackageError("Hybrid evidence planning requires a non-fake LLM.")
    if planner.fallback_used:
        raise HybridEvidencePackageError("Hybrid evidence planning forbids deterministic fallback.")

    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    substrate_path, substrate_report = _load_latest_substrate_report(run_id, reports)
    if config.require_non_fake_backends and not substrate_report.production_ready:
        raise HybridEvidencePackageError(
            "Strict hybrid evidence planning requires a production-eligible M100 substrate report."
        )
    route_path, route_report = _load_optional_route_report(run_id, reports)
    route_by_substrate = {
        item.source_substrate_id: item for item in (route_report.decisions if route_report else [])
    }
    deep = _load_deep_from_substrate(root_path, substrate_report)
    retrieval_by_pair = _load_retrieval_contexts(root_path, deep)
    candidate_by_id = {item.substrate_id: item for item in substrate_report.candidates}
    selected = [
        candidate_by_id[item]
        for item in substrate_report.selected_substrate_ids
        if item in candidate_by_id
    ][: min(config.max_source_substrates, config.max_planning_calls)]
    if not selected:
        raise HybridEvidencePackageError("No selected M100 substrates are available.")

    report_number = _next_number(reports, _PACKAGE_RE)
    raw_number = _next_number(reports, _PACKAGE_RAW_RE)
    report_id = f"hybrid-evidence-package-report-{report_number:04d}"
    packages: list[HybridEvidencePackageCandidate] = []
    scores: list[HybridEvidencePackageScore] = []
    raws: list[HybridEvidencePackageRawArtifact] = []
    warnings: list[str] = []
    repaired_count = 0

    for index, substrate in enumerate(selected, start=1):
        package_id = f"hybrid-evidence-package-{report_number:04d}-{_slug(substrate.substrate_id)}"
        retrieval = retrieval_by_pair.get(substrate.source_pair_id)
        route = route_by_substrate.get(substrate.substrate_id)
        try:
            response = planner.plan_package(
                prompt_id=f"{report_id}-prompt-{index:03d}",
                substrate_payload=substrate.model_dump(mode="json"),
                route_payload=route.model_dump(mode="json") if route else None,
                retrieval_context_payload=(
                    retrieval.model_dump(mode="json") if retrieval is not None else None
                ),
            )
        except (AdapterError, ValueError) as exc:
            raise HybridEvidencePackageError(
                f"LLM hybrid evidence planning failed for {substrate.substrate_id}: {exc}"
            ) from exc
        rejection_reasons = list(response.rejection_reasons)
        package: HybridEvidencePackageCandidate | None = None
        score: HybridEvidencePackageScore | None = None
        if response.accepted is not None and not rejection_reasons:
            proposal = response.accepted.package
            try:
                artifact_plans = [
                    EvidenceArtifactPlan(
                        artifact_plan_id=f"{package_id}-artifact-{plan_index:03d}",
                        **plan.model_dump(mode="python"),
                    )
                    for plan_index, plan in enumerate(proposal.artifact_plans, start=1)
                ]
                package = HybridEvidencePackageCandidate(
                    package_id=package_id,
                    run_id=run_id,
                    source_substrate_id=substrate.substrate_id,
                    source_idea_node_id=substrate.source_idea_node_id,
                    source_variant_id=substrate.source_variant_id,
                    source_opportunity_id=substrate.source_opportunity_id,
                    domain_id=substrate.domain_id,
                    method_id=substrate.method_id,
                    title=proposal.title,
                    primary_claim_draft=proposal.primary_claim_draft,
                    allowed_claim_scope=proposal.allowed_claim_scope,
                    package_rationale=proposal.package_rationale,
                    artifact_plans=artifact_plans,
                    minimum_required_artifacts=proposal.minimum_required_artifacts,
                    optional_supporting_artifacts=proposal.optional_supporting_artifacts,
                    artifact_dependency_graph=proposal.artifact_dependency_graph,
                    claim_support_map=proposal.claim_support_map,
                    known_gaps=proposal.known_gaps,
                    unresolved_obligations=proposal.unresolved_obligations,
                    recommended_next_action=proposal.recommended_next_action,
                )
                score = HybridEvidencePackageScore(
                    package_id=package_id,
                    **response.accepted.score.model_dump(mode="python"),
                )
            except ValidationError as exc:
                rejection_reasons.append(str(exc))
        if rejection_reasons:
            warnings.append(
                f"Rejected hybrid package for {substrate.substrate_id}: "
                + "; ".join(rejection_reasons)
            )
        if package is not None and score is not None:
            packages.append(package)
            scores.append(score)
            repaired_count += int(bool(response.repair_actions))
            if response.repair_actions:
                warnings.append(
                    f"Applied package claim-boundary repairs for {substrate.substrate_id}: "
                    + "; ".join(response.repair_actions)
                )
        raw_id = f"hybrid-evidence-package-raw-{raw_number + index - 1:04d}"
        raws.append(
            HybridEvidencePackageRawArtifact(
                raw_artifact_id=raw_id,
                run_id=run_id,
                source_substrate_id=substrate.substrate_id,
                backend_name=planner.backend_name,
                model=planner.model,
                prompt_text=response.prompt_text,
                requested_output_schema=response.requested_output_schema,
                raw_response=response.raw_response,
                accepted_package_id_optional=package.package_id if package else None,
                rejection_reasons=rejection_reasons,
                fallback_used=planner.fallback_used,
            )
        )

    rejected = len(selected) - len(packages)
    if rejected and config.require_non_fake_backends:
        raise HybridEvidencePackageError(
            "Strict hybrid evidence planning requires one valid package per selected substrate: "
            + " | ".join(warnings)
        )
    if not packages:
        raise HybridEvidencePackageError("No valid hybrid evidence packages remained.")

    artifact_counter = Counter(
        plan.artifact_type.value for package in packages for plan in package.artifact_plans
    )
    backend_records = [
        _planning_backend_record(report_id, planner, [item.raw_artifact_id for item in raws]),
        _package_validation_backend_record(
            report_id, [plan.artifact_plan_id for pkg in packages for plan in pkg.artifact_plans]
        ),
    ]
    production = evaluate_production_mode(
        run_id=run_id,
        records=[*substrate_report.backend_records, *backend_records],
        policy=ProductionModePolicy(
            require_non_fake_backends=config.require_non_fake_backends,
            fail_on_silent_fallback=config.fail_on_silent_fallback,
        ),
        expected_stage_kinds=[
            ScientificStageKind.SUBSTRATE_CONSTRUCTION,
            ScientificStageKind.HYBRID_EVIDENCE_PLANNING,
            ScientificStageKind.SPEC_VALIDATION,
        ],
        report_id=f"{report_id}-production-evaluation",
    )
    if config.require_non_fake_backends and production.blocking_violation_count:
        raise HybridEvidencePackageError(
            "Strict hybrid evidence planning blocked: "
            + "; ".join(item.message for item in production.violations)
        )
    report = HybridEvidencePackageReport(
        run_id=run_id,
        report_id=report_id,
        planning_status="completed_with_warnings" if warnings else "completed",
        config=config,
        source_substrate_report_path=_relative(root_path, substrate_path),
        source_route_planning_report_path_optional=(
            _relative(root_path, route_path) if route_path is not None else None
        ),
        selected_substrate_count=len(selected),
        package_count=len(packages),
        rejected_package_count=rejected,
        repaired_package_count=repaired_count,
        artifact_plan_count=sum(len(item.artifact_plans) for item in packages),
        artifact_type_coverage=len(artifact_counter),
        artifact_type_counts=dict(sorted(artifact_counter.items())),
        raw_artifact_paths=[
            f"runs/{run_id}/reports/{item.raw_artifact_id}.json" for item in raws
        ],
        packages=packages,
        scores=scores,
        backend_records=backend_records,
        warnings=warnings,
        production_ready=(
            config.require_non_fake_backends and not production.blocking_violation_count
        ),
    )
    persistence = _persist_package_report(
        report=report,
        raw_artifacts=raws,
        store=store,
        ledger=ledger,
    )
    return _stage_result(report, persistence)


def execute_hybrid_evidence_packages(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    planner: HybridEvidenceClient,
    code_generator: ExperimentCodeGenerationClient,
    retrieval_mode: Literal["mocked_retrieval", "real_retrieval"],
    require_non_fake_backends: bool = False,
    timeout_seconds: int = 30,
    memory_limit_mb: int = 512,
    allowed_dependencies: list[str] | None = None,
) -> HybridEvidencePackageStageResult:
    """Execute executable package components and draft/check bounded non-code artifacts."""
    if planner.backend_kind not in {BackendKind.LLM_OPENAI, BackendKind.LLM_OTHER}:
        raise HybridEvidencePackageError("Hybrid evidence execution requires a non-fake LLM.")
    if code_generator.backend_kind not in {BackendKind.LLM_OPENAI, BackendKind.LLM_OTHER}:
        raise HybridEvidencePackageError("Hybrid evidence code generation requires a non-fake LLM.")
    if planner.fallback_used or code_generator.fallback_used:
        raise HybridEvidencePackageError(
            "Hybrid evidence execution forbids deterministic fallback."
        )
    dependencies = allowed_dependencies or ["numpy"]
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    package_path, package_report = _load_latest_package_report(run_id, reports)
    if require_non_fake_backends and not package_report.production_ready:
        raise HybridEvidencePackageError(
            "Strict hybrid evidence execution requires a production-eligible package report."
        )
    substrate_report = _load_substrate_report(
        root_path / package_report.source_substrate_report_path
    )
    deep = _load_deep_from_substrate(root_path, substrate_report)
    retrieval_by_pair = _load_retrieval_contexts(root_path, deep)
    substrate_by_id = {item.substrate_id: item for item in substrate_report.candidates}

    report_number = _next_number(reports, _EXECUTION_RE)
    report_id = f"evidence-package-execution-report-{report_number:04d}"
    raw_number = _next_number(reports, _CODE_RAW_RE)
    draft_raw_number = _next_number(reports, _DRAFT_RAW_RE)
    code_number = _next_number(root_path / "runs" / run_id / "experiments", _CODE_RE)
    audit_number = _next_number(reports, _AUDIT_RE)
    sandbox_number = _next_number(reports, _SANDBOX_RE)
    metric_number = _next_number(reports, _METRIC_RE)
    result_number = _next_number(reports, _RESULT_RE)

    code_artifacts: list[LLMExperimentCodeArtifact] = []
    audits: list[ExperimentCodeSafetyAudit] = []
    sandbox_configs: list[SandboxExecutionConfig] = []
    observations: list[Any] = []
    sandbox_executions: list[SandboxExecutionResult] = []
    metric_results: list[MetricExtractionResult] = []
    results: list[EvidencePackageExecutionResult] = []
    code_raws: list[LLMExperimentCodeRawArtifact] = []
    draft_raws: list[dict[str, Any]] = []
    warnings: list[str] = []
    code_index = 0
    draft_index = 0

    for package in package_report.packages:
        substrate = substrate_by_id.get(package.source_substrate_id)
        retrieval = retrieval_by_pair.get(substrate.source_pair_id) if substrate else None
        if substrate is None:
            raise HybridEvidencePackageError(
                f"Source substrate metadata missing for package {package.package_id}."
            )
        for plan in package.artifact_plans:
            result_id = f"evidence-package-result-{result_number + len(results):04d}"
            if plan.artifact_type in _CODE_ARTIFACT_TYPES:
                code_id = f"evidence-package-code-artifact-{code_number + code_index:04d}"
                code_index += 1
                code_raw_id = f"hybrid-evidence-code-raw-{raw_number + len(code_raws):04d}"
                metrics = _required_metrics(plan)
                spec_payload = _code_spec_payload(package=package, plan=plan, metrics=metrics)
                try:
                    response = code_generator.generate_code(
                        spec_payload=spec_payload,
                        substrate_payload=substrate.model_dump(mode="json"),
                        allowed_dependencies=dependencies,
                    )
                except (AdapterError, ValueError) as exc:
                    raise HybridEvidencePackageError(
                        f"Hybrid evidence code generation failed for {plan.artifact_plan_id}: {exc}"
                    ) from exc
                rejection_reasons = list(response.rejection_reasons)
                accepted_code_id: str | None = None
                if response.accepted is None or rejection_reasons:
                    if require_non_fake_backends:
                        raise HybridEvidencePackageError(
                            f"Strict hybrid evidence execution requires valid generated code for "
                            f"{plan.artifact_plan_id}: " + "; ".join(rejection_reasons)
                        )
                    result = _failed_result(
                        package=package,
                        plan=plan,
                        result_id=result_id,
                        reason="Generated code proposal was rejected: "
                        + "; ".join(rejection_reasons),
                    )
                    results.append(result)
                else:
                    proposal = response.accepted
                    artifact = LLMExperimentCodeArtifact(
                        code_artifact_id=code_id,
                        run_id=run_id,
                        source_spec_id=plan.artifact_plan_id,
                        source_route_id=plan.artifact_plan_id,
                        source_substrate_id=package.source_substrate_id,
                        route_type=_branch_route_type(plan.artifact_type),
                        backend_kind=code_generator.backend_kind,
                        language=proposal.language,
                        entrypoint=proposal.entrypoint,
                        code=proposal.code,
                        expected_output_files=proposal.expected_output_files,
                        required_inputs=proposal.required_inputs,
                        declared_dependencies=proposal.declared_dependencies,
                        random_seed=proposal.random_seed,
                        timeout_seconds=min(proposal.timeout_seconds, timeout_seconds),
                        network_required=False,
                        filesystem_scope=proposal.filesystem_scope,
                    )
                    accepted_code_id = code_id
                    audit = audit_generated_experiment_code(
                        artifact=artifact,
                        required_metrics=metrics,
                        negative_controls_required=bool(plan.negative_control_plan_optional)
                        or plan.artifact_type == EvidenceArtifactType.NEGATIVE_CONTROL,
                        allowed_dependencies=dependencies,
                    )
                    code_artifacts.append(artifact)
                    audits.append(audit)
                    config = SandboxExecutionConfig(
                        entrypoint=artifact.entrypoint,
                        output_json_filename="output.json",
                        timeout_seconds=artifact.timeout_seconds,
                        memory_limit_mb=memory_limit_mb,
                        network_disabled=True,
                        seed=artifact.random_seed,
                        allowed_dependencies=artifact.declared_dependencies,
                    )
                    sandbox_configs.append(config)
                    execution_id = (
                        f"evidence-package-sandbox-execution-"
                        f"{sandbox_number + len(sandbox_executions):04d}"
                    )
                    observation = (
                        _blocked_observation(
                            run_id=run_id,
                            execution_id=execution_id,
                            artifact=artifact,
                            config=config,
                            reasons=audit.reasons,
                        )
                        if audit.blocked
                        else _execute_in_sandbox(
                            run_id=run_id,
                            artifact=artifact,
                            execution_id=execution_id,
                            config=config,
                        )
                    )
                    observations.append(observation)
                    sandbox_executions.append(observation.result)
                    extraction = extract_metrics_from_output(
                        execution=observation.result,
                        output_payload=observation.output_payload,
                        required_metrics=metrics,
                        output_json_path=observation.result.output_json_path,
                    ).model_copy(update={"execution_id": execution_id})
                    if not audit.blocked:
                        metric_results.append(extraction)
                    result = _code_result(
                        package=package,
                        plan=plan,
                        result_id=result_id,
                        execution=observation.result,
                        extraction=extraction,
                        output_payload=observation.output_payload,
                    )
                    results.append(result)
                    if result.status in {"failed", "inconclusive", "blocked_safety_audit"}:
                        warnings.append(
                            f"{plan.artifact_plan_id} produced {result.status}: "
                            f"{result.failure_reason_optional or '; '.join(result.warnings)}"
                        )
                code_raws.append(
                    LLMExperimentCodeRawArtifact(
                        raw_artifact_id=code_raw_id,
                        run_id=run_id,
                        source_spec_id=plan.artifact_plan_id,
                        backend_name=code_generator.backend_name,
                        model=code_generator.model,
                        prompt_text=response.prompt_text,
                        requested_output_schema=response.requested_output_schema,
                        raw_response=response.raw_response,
                        accepted_code_artifact_id_optional=accepted_code_id,
                        rejection_reasons=rejection_reasons,
                        fallback_used=code_generator.fallback_used,
                    )
                )
            elif plan.artifact_type in _SYMBOLIC_TYPES or (
                plan.artifact_type == EvidenceArtifactType.LITERATURE_NOVELTY_CHECK
            ):
                if plan.artifact_type == EvidenceArtifactType.LITERATURE_NOVELTY_CHECK:
                    _validate_retrieval_for_execution(
                        plan=plan,
                        retrieval=retrieval,
                        retrieval_mode=retrieval_mode,
                        require_non_fake_backends=require_non_fake_backends,
                    )
                draft_raw_id = f"hybrid-evidence-draft-raw-{draft_raw_number + draft_index:04d}"
                draft_index += 1
                response = planner.draft_artifact(
                    prompt_id=f"{report_id}-draft-{draft_index:03d}",
                    package_payload=package.model_dump(mode="json"),
                    artifact_plan_payload=plan.model_dump(mode="json"),
                    retrieval_context_payload=(
                        retrieval.model_dump(mode="json") if retrieval is not None else None
                    ),
                )
                draft_raws.append(
                    {
                        "raw_artifact_id": draft_raw_id,
                        "run_id": run_id,
                        "package_id": package.package_id,
                        "artifact_plan_id": plan.artifact_plan_id,
                        "backend_name": planner.backend_name,
                        "model": planner.model,
                        "prompt_text": response.prompt_text,
                        "requested_output_schema": response.requested_output_schema,
                        "raw_response": response.raw_response,
                        "rejection_reasons": response.rejection_reasons,
                        "fallback_used": planner.fallback_used,
                        "publication_ready": False,
                    }
                )
                if response.accepted is None or response.rejection_reasons:
                    result = _failed_result(
                        package=package,
                        plan=plan,
                        result_id=result_id,
                        reason="Draft artifact was rejected: "
                        + "; ".join(response.rejection_reasons),
                    )
                    warnings.append(result.failure_reason_optional or "Draft artifact rejected.")
                else:
                    result = _draft_result(
                        package=package,
                        plan=plan,
                        result_id=result_id,
                        draft=response.accepted.model_dump(mode="json"),
                        retrieval=retrieval,
                    )
                results.append(result)
            else:
                results.append(_deferred_or_rejected_result(package, plan, result_id))

    artifact_counter = Counter(
        plan.artifact_type.value
        for pkg in package_report.packages
        for plan in pkg.artifact_plans
    )
    label_counter = Counter(item.evidence_label for item in results)
    backend_records = _execution_backend_records(
        report_id=report_id,
        planner=planner,
        code_generator=code_generator,
        package_report=package_report,
        code_ids=[item.code_artifact_id for item in code_artifacts],
        execution_ids=[item.execution_id for item in sandbox_executions],
        metric_ids=[item.execution_id for item in metric_results],
        draft_ids=[item["raw_artifact_id"] for item in draft_raws],
        retrieval_mode=retrieval_mode,
        has_retrieval=any(
            plan.artifact_type == EvidenceArtifactType.LITERATURE_NOVELTY_CHECK
            for pkg in package_report.packages
            for plan in pkg.artifact_plans
        ),
    )
    expected = [
        ScientificStageKind.HYBRID_EVIDENCE_PLANNING,
        ScientificStageKind.SPEC_VALIDATION,
    ]
    if code_artifacts:
        expected.extend(
            [
                ScientificStageKind.EXPERIMENT_CODE_GENERATION,
                ScientificStageKind.CODE_SAFETY_AUDIT,
                ScientificStageKind.EXPERIMENT_EXECUTION,
                ScientificStageKind.METRIC_COMPUTATION,
            ]
        )
    if draft_raws:
        expected.append(ScientificStageKind.SYMBOLIC_DERIVATION)
    if any(
        plan.artifact_type == EvidenceArtifactType.LITERATURE_NOVELTY_CHECK
        for pkg in package_report.packages
        for plan in pkg.artifact_plans
    ):
        expected.extend(
            [ScientificStageKind.LITERATURE_RETRIEVAL, ScientificStageKind.NOVELTY_ASSESSMENT]
        )
    production = evaluate_production_mode(
        run_id=run_id,
        records=[*package_report.backend_records, *backend_records],
        policy=ProductionModePolicy(require_non_fake_backends=require_non_fake_backends),
        expected_stage_kinds=expected,
        report_id=f"{report_id}-production-evaluation",
    )
    if require_non_fake_backends and production.blocking_violation_count:
        raise HybridEvidencePackageError(
            "Strict hybrid evidence execution blocked: "
            + "; ".join(item.message for item in production.violations)
        )
    execution_status = "completed_with_warnings" if warnings else "completed"
    report = EvidencePackageExecutionReport(
        run_id=run_id,
        report_id=report_id,
        execution_status=execution_status,
        source_package_report_path=_relative(root_path, package_path),
        package_count=package_report.package_count,
        artifact_plan_count=package_report.artifact_plan_count,
        executable_artifact_count=sum(
            count for artifact_type, count in artifact_counter.items()
            if EvidenceArtifactType(artifact_type) in _CODE_ARTIFACT_TYPES
        ),
        symbolic_artifact_count=sum(
            count for artifact_type, count in artifact_counter.items()
            if EvidenceArtifactType(artifact_type) in _SYMBOLIC_TYPES
        ),
        retrieval_artifact_count=artifact_counter[EvidenceArtifactType.LITERATURE_NOVELTY_CHECK.value],
        deferred_artifact_count=(
            artifact_counter[EvidenceArtifactType.DEFER_UNAVAILABLE_CHECKER.value]
            + artifact_counter[EvidenceArtifactType.DEFER_INSUFFICIENT_SUPPORT.value]
            + artifact_counter[EvidenceArtifactType.REJECT_FALSE_BRIDGE.value]
        ),
        code_artifact_count=len(code_artifacts),
        safety_audit_count=len(audits),
        blocked_code_count=sum(item.blocked for item in audits),
        executed_code_count=sum(not item.blocked for item in audits),
        failed_execution_count=sum(
            item.status in {"failed", "timed_out"} for item in sandbox_executions
        ),
        metric_extraction_count=len(metric_results),
        result_count=len(results),
        evidence_label_counts=dict(sorted(label_counter.items())),
        artifact_type_counts=dict(sorted(artifact_counter.items())),
        raw_artifact_paths=[
            *[f"runs/{run_id}/reports/{item.raw_artifact_id}.json" for item in code_raws],
            *[f"runs/{run_id}/reports/{item['raw_artifact_id']}.json" for item in draft_raws],
        ],
        code_artifact_paths=[
            f"runs/{run_id}/experiments/{item.code_artifact_id}.py" for item in code_artifacts
        ],
        safety_audit_paths=[
            f"runs/{run_id}/reports/evidence-package-code-safety-audit-{audit_number + i:04d}.json"
            for i in range(len(audits))
        ],
        sandbox_execution_paths=[
            f"runs/{run_id}/reports/{item.execution_id}.json" for item in sandbox_executions
        ],
        metric_extraction_paths=[
            f"runs/{run_id}/reports/evidence-package-metric-extraction-{metric_number + i:04d}.json"
            for i in range(len(metric_results))
        ],
        result_paths=[f"runs/{run_id}/reports/{item.result_id}.json" for item in results],
        code_artifacts=code_artifacts,
        safety_audits=audits,
        sandbox_configs=sandbox_configs,
        sandbox_executions=sandbox_executions,
        metric_extractions=metric_results,
        results=results,
        backend_records=backend_records,
        warnings=warnings,
        production_ready=(require_non_fake_backends and not production.blocking_violation_count),
    )
    persistence = _persist_execution_report(
        report=report,
        code_raws=code_raws,
        draft_raws=draft_raws,
        observations=observations,
        audits=audits,
        metric_results=metric_results,
        results=results,
        store=store,
        ledger=ledger,
    )
    return _stage_result(report, persistence)


def inspect_hybrid_evidence_packages(
    *, run_id: str, root: str | Path = "."
) -> HybridEvidencePackageInspectionReport:
    reports = Path(root) / "runs" / run_id / "reports"
    path = _latest_matching(reports, _PACKAGE_RE)
    if path is None:
        return HybridEvidencePackageInspectionReport(
            run_id=run_id,
            hybrid_evidence_package_present=False,
        )
    report = _load_package_report(path)
    return HybridEvidencePackageInspectionReport(
        run_id=run_id,
        hybrid_evidence_package_present=True,
        latest_report_id_optional=report.report_id,
        planning_status_optional=report.planning_status,
        selected_substrate_count=report.selected_substrate_count,
        package_count=report.package_count,
        rejected_package_count=report.rejected_package_count,
        repaired_package_count=report.repaired_package_count,
        artifact_plan_count=report.artifact_plan_count,
        artifact_type_coverage=report.artifact_type_coverage,
        artifact_type_counts=report.artifact_type_counts,
        packages=report.packages,
        scores=report.scores,
        backend_records=report.backend_records,
        warnings=report.warnings,
        production_ready=report.production_ready,
    )


def inspect_evidence_package_execution(
    *, run_id: str, root: str | Path = "."
) -> EvidencePackageExecutionInspectionReport:
    reports = Path(root) / "runs" / run_id / "reports"
    path = _latest_matching(reports, _EXECUTION_RE)
    if path is None:
        return EvidencePackageExecutionInspectionReport(
            run_id=run_id,
            evidence_package_execution_present=False,
        )
    report = _load_execution_report(path)
    return EvidencePackageExecutionInspectionReport(
        run_id=run_id,
        evidence_package_execution_present=True,
        latest_report_id_optional=report.report_id,
        execution_status_optional=report.execution_status,
        package_count=report.package_count,
        artifact_plan_count=report.artifact_plan_count,
        executable_artifact_count=report.executable_artifact_count,
        symbolic_artifact_count=report.symbolic_artifact_count,
        retrieval_artifact_count=report.retrieval_artifact_count,
        deferred_artifact_count=report.deferred_artifact_count,
        code_artifact_count=report.code_artifact_count,
        blocked_code_count=report.blocked_code_count,
        executed_code_count=report.executed_code_count,
        metric_extraction_count=report.metric_extraction_count,
        result_count=report.result_count,
        evidence_label_counts=report.evidence_label_counts,
        artifact_type_counts=report.artifact_type_counts,
        results=report.results,
        backend_records=report.backend_records,
        warnings=report.warnings,
        production_ready=report.production_ready,
    )


def render_hybrid_evidence_package_text(report: HybridEvidencePackageInspectionReport) -> str:
    return "\n".join(
        [
            "Hybrid evidence packages: "
            f"{'present' if report.hybrid_evidence_package_present else 'absent'}",
            f"Status: {report.planning_status_optional or 'not available'}",
            f"Packages/artifact plans: {report.package_count}/{report.artifact_plan_count}",
            f"Artifact type coverage: {report.artifact_type_coverage}",
            f"Production ready: {str(report.production_ready).lower()}",
            "publication_ready=false",
        ]
    )


def render_evidence_package_execution_text(
    report: EvidencePackageExecutionInspectionReport,
) -> str:
    return "\n".join(
        [
            "Evidence package execution: "
            f"{'present' if report.evidence_package_execution_present else 'absent'}",
            f"Status: {report.execution_status_optional or 'not available'}",
            f"Results: {report.result_count}",
            f"Executable/symbolic/retrieval/deferred: {report.executable_artifact_count}/"
            f"{report.symbolic_artifact_count}/{report.retrieval_artifact_count}/"
            f"{report.deferred_artifact_count}",
            f"Code blocked/executed: {report.blocked_code_count}/{report.executed_code_count}",
            f"Metric extractions: {report.metric_extraction_count}",
            f"Production ready: {str(report.production_ready).lower()}",
            "publication_ready=false",
        ]
    )


def _persist_package_report(
    *,
    report: HybridEvidencePackageReport,
    raw_artifacts: list[HybridEvidencePackageRawArtifact],
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PersistenceResult:
    metadata = _metadata("hybrid_evidence_package_planning")
    specs = [
        ArtifactWriteSpec(item.raw_artifact_id, ArtifactType.REPORT, item, "json", metadata)
        for item in raw_artifacts
    ]
    specs.extend(
        [
            ArtifactWriteSpec(report.report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report.report_id}-markdown",
                ArtifactType.REPORT,
                render_hybrid_evidence_package_markdown(report),
                "markdown",
                metadata,
                filename_stem=report.report_id,
            ),
        ]
    )
    return persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.HYBRID_EVIDENCE_PACKAGES_PLANNED,
        commit_payload={
            "run_id": report.run_id,
            "report_id": report.report_id,
            "package_count": report.package_count,
            "artifact_plan_count": report.artifact_plan_count,
            "publication_ready": False,
        },
    )


def _persist_execution_report(
    *,
    report: EvidencePackageExecutionReport,
    code_raws: list[LLMExperimentCodeRawArtifact],
    draft_raws: list[dict[str, Any]],
    observations: list[Any],
    audits: list[ExperimentCodeSafetyAudit],
    metric_results: list[MetricExtractionResult],
    results: list[EvidencePackageExecutionResult],
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PersistenceResult:
    metadata = _metadata("hybrid_evidence_package_execution")
    specs: list[ArtifactWriteSpec] = []
    specs.extend(
        ArtifactWriteSpec(item.raw_artifact_id, ArtifactType.REPORT, item, "json", metadata)
        for item in code_raws
    )
    specs.extend(
        ArtifactWriteSpec(item["raw_artifact_id"], ArtifactType.REPORT, item, "json", metadata)
        for item in draft_raws
    )
    specs.extend(
        ArtifactWriteSpec(
            item.code_artifact_id,
            ArtifactType.EXPERIMENT,
            item.code,
            "text",
            metadata,
            extension="py",
            format_label="python",
        )
        for item in report.code_artifacts
    )
    for observation in observations:
        execution = observation.result
        specs.extend(
            [
                ArtifactWriteSpec(
                    f"{execution.execution_id}-stdout",
                    ArtifactType.LOG,
                    observation.stdout,
                    "text",
                    metadata,
                ),
                ArtifactWriteSpec(
                    f"{execution.execution_id}-stderr",
                    ArtifactType.LOG,
                    observation.stderr,
                    "text",
                    metadata,
                ),
            ]
        )
        if observation.output_text is not None:
            specs.append(
                ArtifactWriteSpec(
                    f"{execution.execution_id}-output",
                    ArtifactType.EXPERIMENT,
                    observation.output_payload or {"invalid_output": observation.output_text},
                    "json",
                    metadata,
                )
            )
    specs.extend(
        ArtifactWriteSpec(
            Path(path).stem,
            ArtifactType.REPORT,
            audit,
            "json",
            metadata,
        )
        for path, audit in zip(report.safety_audit_paths, audits, strict=True)
    )
    specs.extend(
        ArtifactWriteSpec(item.execution_id, ArtifactType.REPORT, item, "json", metadata)
        for item in report.sandbox_executions
    )
    specs.extend(
        ArtifactWriteSpec(
            Path(path).stem,
            ArtifactType.REPORT,
            item,
            "json",
            metadata,
        )
        for path, item in zip(report.metric_extraction_paths, metric_results, strict=True)
    )
    specs.extend(
        ArtifactWriteSpec(item.result_id, ArtifactType.REPORT, item, "json", metadata)
        for item in results
    )
    specs.extend(
        [
            ArtifactWriteSpec(report.report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report.report_id}-markdown",
                ArtifactType.REPORT,
                render_evidence_package_execution_markdown(report),
                "markdown",
                metadata,
                filename_stem=report.report_id,
            ),
        ]
    )
    return persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.HYBRID_EVIDENCE_PACKAGES_EXECUTED,
        commit_payload={
            "run_id": report.run_id,
            "report_id": report.report_id,
            "result_count": report.result_count,
            "metric_extraction_count": report.metric_extraction_count,
            "publication_ready": False,
        },
    )


def render_hybrid_evidence_package_markdown(report: HybridEvidencePackageReport) -> str:
    lines = [
        "# Hybrid Evidence Packages",
        "",
        f"Status: `{report.planning_status}`",
        f"Packages: `{report.package_count}`",
        f"Artifact plans: `{report.artifact_plan_count}`",
        "",
        "| Package | Artifact types |",
        "|---|---|",
    ]
    for package in report.packages:
        types = ", ".join(plan.artifact_type.value for plan in package.artifact_plans)
        lines.append(f"| {package.title} | {types} |")
    lines.extend(
        [
            "",
            "Packages are planning context only. They do not prove novelty, theorem status, "
            "real-world validation, or publication readiness.",
            "",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def render_evidence_package_execution_markdown(report: EvidencePackageExecutionReport) -> str:
    lines = [
        "# Hybrid Evidence Package Execution",
        "",
        f"Status: `{report.execution_status}`",
        f"Results: `{report.result_count}`",
        f"Metric extractions: `{report.metric_extraction_count}`",
        "",
        "| Artifact type | Evidence label | Status |",
        "|---|---|---|",
    ]
    lines.extend(
        f"| {item.artifact_type.value} | {item.evidence_label} | {item.status} |"
        for item in report.results
    )
    lines.extend(
        [
            "",
            "Metrics, when present, were parsed only from sandbox output JSON. Symbolic and "
            "proof artifacts are draft-labeled unless an external checker validates them.",
            "",
            "publication_ready=false",
        ]
    )
    return "\n".join(lines)


def _stage_result(
    report: HybridEvidencePackageReport | EvidencePackageExecutionReport,
    persistence: PersistenceResult,
) -> HybridEvidencePackageStageResult:
    by_id = {item.id: item for item in persistence.artifacts}
    return HybridEvidencePackageStageResult(
        run_id=report.run_id,
        report=report,
        persistence=persistence,
        report_artifact=by_id[report.report_id],
        markdown_artifact=by_id[f"{report.report_id}-markdown"],
    )


def _code_result(
    *,
    package: HybridEvidencePackageCandidate,
    plan: EvidenceArtifactPlan,
    result_id: str,
    execution: SandboxExecutionResult,
    extraction: MetricExtractionResult,
    output_payload: dict[str, Any] | None,
) -> EvidencePackageExecutionResult:
    success = _output_bool(output_payload, "success_criteria_satisfied")
    failure = _output_bool(output_payload, "failure_criteria_satisfied")
    negative_passed = _output_bool(output_payload, "negative_controls_passed")
    warnings: list[str] = []
    if execution.status == "blocked":
        status = "blocked_safety_audit"
        label = "InconclusiveResult"
    elif execution.status != "completed" or not extraction.schema_valid:
        status = "failed" if execution.status in {"failed", "timed_out"} else "inconclusive"
        label = "InconclusiveResult"
    elif negative_passed is not True:
        status = "inconclusive"
        label = "InconclusiveResult"
        warnings.append("Negative controls did not pass; support was downgraded.")
    elif failure is True:
        status = "negative_result"
        label = "NegativeResult"
    elif success is True and failure is False:
        status = "completed"
        label = _primary_success_label(plan.artifact_type)
    else:
        status = "inconclusive"
        label = "InconclusiveResult"
        warnings.append("Output did not resolve success and failure criteria consistently.")
    return EvidencePackageExecutionResult(
        result_id=result_id,
        package_id=package.package_id,
        artifact_plan_id=plan.artifact_plan_id,
        source_substrate_id=package.source_substrate_id,
        artifact_type=plan.artifact_type,
        status=status,
        evidence_label=label,
        scope_label="bounded local package execution only; no real-world validation",
        metrics=extraction.metrics if extraction.schema_valid else {},
        metric_sources=extraction.metric_sources if extraction.schema_valid else {},
        baseline_summary=_output_string(output_payload, "baseline_summary", "Unavailable."),
        control_summary=_output_string(output_payload, "control_summary", "Unavailable."),
        negative_control_summary=_output_string(
            output_payload, "negative_control_summary", "Unavailable."
        ),
        success_criteria_satisfied=success,
        failure_criteria_satisfied=failure,
        warnings=[*warnings, *extraction.extraction_warnings],
        failure_reason_optional=execution.failure_reason_optional,
    )


def _draft_result(
    *,
    package: HybridEvidencePackageCandidate,
    plan: EvidenceArtifactPlan,
    result_id: str,
    draft: dict[str, Any],
    retrieval: RetrievalContext | None,
) -> EvidencePackageExecutionResult:
    if plan.artifact_type == EvidenceArtifactType.LITERATURE_NOVELTY_CHECK:
        label = "RetrievalNoveltyAssessment"
        scope = "bounded retrieval-context novelty-risk assessment; novelty_proven=false"
        warnings = [
            "Novelty and underuse are risk/hypothesis assessments, not established findings."
        ]
        if retrieval is None:
            warnings.append("No retrieval context was available for this assessment.")
    else:
        label = _primary_success_label(plan.artifact_type)
        scope = "draft symbolic/proof-plan artifact only; no checker validation"
        warnings = ["Checker status is not_checked; no formal proof label is created."]
    return EvidencePackageExecutionResult(
        result_id=result_id,
        package_id=package.package_id,
        artifact_plan_id=plan.artifact_plan_id,
        source_substrate_id=package.source_substrate_id,
        artifact_type=plan.artifact_type,
        status="draft_created",
        evidence_label=label,
        scope_label=scope,
        metrics={},
        metric_sources={},
        baseline_summary="Not a metric-bearing local execution artifact.",
        control_summary="Draft artifact; controls are obligations only.",
        negative_control_summary="Draft artifact; negative controls are obligations only.",
        draft_payload=draft,
        unresolved_obligations=[
            str(item) for item in draft.get("unresolved_obligations", [])
        ]
        or list(plan.symbolic_obligations_optional or plan.retrieval_requirements_optional or []),
        warnings=warnings,
    )


def _failed_result(
    *,
    package: HybridEvidencePackageCandidate,
    plan: EvidenceArtifactPlan,
    result_id: str,
    reason: str,
) -> EvidencePackageExecutionResult:
    return EvidencePackageExecutionResult(
        result_id=result_id,
        package_id=package.package_id,
        artifact_plan_id=plan.artifact_plan_id,
        source_substrate_id=package.source_substrate_id,
        artifact_type=plan.artifact_type,
        status="failed",
        evidence_label="InconclusiveResult",
        scope_label="failed package component; no support created",
        metrics={},
        metric_sources={},
        baseline_summary="Unavailable because the component failed.",
        control_summary="Unavailable because the component failed.",
        negative_control_summary="Unavailable because the component failed.",
        warnings=[reason],
        failure_reason_optional=reason,
    )


def _deferred_or_rejected_result(
    package: HybridEvidencePackageCandidate,
    plan: EvidenceArtifactPlan,
    result_id: str,
) -> EvidencePackageExecutionResult:
    if plan.artifact_type == EvidenceArtifactType.REJECT_FALSE_BRIDGE:
        status = "rejected_false_bridge"
        label = "RejectedFalseBridge"
        reason = "Artifact plan rejects a decorative or unverifiable method-domain bridge."
    elif plan.artifact_type == EvidenceArtifactType.DEFER_UNAVAILABLE_CHECKER:
        status = "deferred_unavailable_checker"
        label = "UnsupportedRouteDeferred"
        reason = "Artifact requires a checker that is unavailable in M103."
    else:
        status = "deferred_insufficient_support"
        label = "UnsupportedRouteDeferred"
        reason = "Artifact lacks sufficient executable, retrieval, or checker support in M103."
    return EvidencePackageExecutionResult(
        result_id=result_id,
        package_id=package.package_id,
        artifact_plan_id=plan.artifact_plan_id,
        source_substrate_id=package.source_substrate_id,
        artifact_type=plan.artifact_type,
        status=status,
        evidence_label=label,
        scope_label="deferred or rejected package component; no support created",
        metrics={},
        metric_sources={},
        baseline_summary="Not executed.",
        control_summary="Not executed.",
        negative_control_summary="Not executed.",
        unresolved_obligations=list(plan.checker_requirements_optional or []),
        warnings=[reason],
        failure_reason_optional=reason,
    )


def _required_metrics(plan: EvidenceArtifactPlan) -> list[str]:
    raw = plan.output_contract.get("required_metrics")
    if not isinstance(raw, list) or not raw:
        raw = plan.metric_plan_optional or ["primary_metric"]
    result = []
    for item in raw:
        slug = "_".join(_TOKEN_RE.findall(str(item).lower()))
        if slug and slug not in result:
            result.append(slug)
    return result or ["primary_metric"]


def _code_spec_payload(
    *,
    package: HybridEvidencePackageCandidate,
    plan: EvidenceArtifactPlan,
    metrics: list[str],
) -> dict[str, Any]:
    return {
        "spec_id": plan.artifact_plan_id,
        "route_id": plan.artifact_plan_id,
        "source_substrate_id": package.source_substrate_id,
        "route_type": _branch_route_type(plan.artifact_type).value,
        "title": f"{package.title}: {plan.artifact_type.value}",
        "objective": plan.purpose,
        "input_contract": plan.input_contract,
        "output_contract": {
            "required_metrics": metrics,
            "required_payload_fields": [
                "metrics",
                "baseline_summary",
                "control_summary",
                "negative_control_summary",
                "negative_controls_passed",
                "success_criteria_satisfied",
                "failure_criteria_satisfied",
            ],
            "scope_label": package.allowed_claim_scope,
            "success_criterion": "; ".join(plan.success_criteria),
            "failure_criterion": "; ".join(plan.failure_criteria),
        },
        "baseline_plan": plan.baseline_or_comparator_plan,
        "control_plan": plan.control_plan_optional or [],
        "negative_control_plan": plan.negative_control_plan_optional or [],
        "robustness_plan": [],
        "metric_plan": metrics,
        "success_criteria": plan.success_criteria,
        "failure_criteria": plan.failure_criteria,
        "expected_artifacts": ["output.json"],
        "allowed_evidence_labels": plan.allowed_evidence_labels,
        "forbidden_claims": plan.forbidden_claims,
        "execution_backend_required": plan.execution_backend_required,
    }


def _branch_route_type(artifact_type: EvidenceArtifactType):
    from factori.schemas import BranchRouteType

    return BranchRouteType(_BRANCH_ROUTE_MAP.get(artifact_type, "synthetic_experiment"))


def _primary_success_label(artifact_type: EvidenceArtifactType) -> str:
    for label in PACKAGE_ALLOWED_LABELS[artifact_type]:
        if label not in {"NegativeResult", "InconclusiveResult", "UnsupportedRouteDeferred"}:
            return label
    return PACKAGE_ALLOWED_LABELS[artifact_type][0]


def _validate_retrieval_for_execution(
    *,
    plan: EvidenceArtifactPlan,
    retrieval: RetrievalContext | None,
    retrieval_mode: str,
    require_non_fake_backends: bool,
) -> None:
    if not plan.requires_retrieval:
        raise HybridEvidencePackageError("Literature novelty artifact does not require retrieval.")
    if retrieval_mode == "real_retrieval":
        if retrieval is None or retrieval.retrieval_mode != "real_retrieval":
            raise HybridEvidencePackageError(
                "Real retrieval mode requires an existing real RetrievalContext for the package."
            )
    elif require_non_fake_backends:
        raise HybridEvidencePackageError("Strict production mode forbids mocked retrieval.")


def _execution_backend_records(
    *,
    report_id: str,
    planner: HybridEvidenceClient,
    code_generator: ExperimentCodeGenerationClient,
    package_report: HybridEvidencePackageReport,
    code_ids: list[str],
    execution_ids: list[str],
    metric_ids: list[str],
    draft_ids: list[str],
    retrieval_mode: str,
    has_retrieval: bool,
) -> list[StageBackendRecord]:
    records: list[StageBackendRecord] = []
    if code_ids:
        records.extend(
            [
                stage_backend_record(
                    stage_id=f"{report_id}-code-generation",
                    stage_kind=ScientificStageKind.EXPERIMENT_CODE_GENERATION,
                    backend_kind=code_generator.backend_kind,
                    backend_name=code_generator.backend_name,
                    is_scientific_generation=True,
                    is_scientific_judgment=False,
                    is_execution_or_verification=False,
                    reason=(
                        "Package code components come from the recorded non-fake LLM backend."
                    ),
                    artifact_ids=[report_id, *code_ids],
                    fallback_used=code_generator.fallback_used,
                    fallback_disclosed=code_generator.fallback_disclosed,
                ),
                stage_backend_record(
                    stage_id=f"{report_id}-code-safety-audit",
                    stage_kind=ScientificStageKind.CODE_SAFETY_AUDIT,
                    backend_kind=BackendKind.LOCAL_EXECUTION,
                    backend_name="hybrid_evidence_ast_policy",
                    is_scientific_generation=False,
                    is_scientific_judgment=False,
                    is_execution_or_verification=True,
                    allowed_in_production=True,
                    reason="Local AST and contract checks block unsafe package code.",
                    artifact_ids=[report_id, *code_ids],
                ),
            ]
        )
    if execution_ids:
        records.extend(
            [
                stage_backend_record(
                    stage_id=f"{report_id}-sandbox-execution",
                    stage_kind=ScientificStageKind.EXPERIMENT_EXECUTION,
                    backend_kind=BackendKind.LOCAL_EXECUTION,
                    backend_name="hybrid_evidence_generated_python_runner",
                    is_scientific_generation=False,
                    is_scientific_judgment=False,
                    is_execution_or_verification=True,
                    allowed_in_production=True,
                    reason="Audited package code runs locally with fixed seeds and limits.",
                    artifact_ids=[report_id, *execution_ids],
                ),
                stage_backend_record(
                    stage_id=f"{report_id}-metric-extraction",
                    stage_kind=ScientificStageKind.METRIC_COMPUTATION,
                    backend_kind=BackendKind.LOCAL_EXECUTION,
                    backend_name="hybrid_evidence_output_json_metric_extractor",
                    is_scientific_generation=False,
                    is_scientific_judgment=False,
                    is_execution_or_verification=True,
                    allowed_in_production=True,
                    reason="Package metrics are parsed only from sandbox output JSON.",
                    artifact_ids=[report_id, *metric_ids],
                ),
            ]
        )
    if draft_ids:
        records.append(
            stage_backend_record(
                stage_id=f"{report_id}-symbolic-drafting",
                stage_kind=ScientificStageKind.SYMBOLIC_DERIVATION,
                backend_kind=planner.backend_kind,
                backend_name=planner.backend_name,
                is_scientific_generation=True,
                is_scientific_judgment=False,
                is_execution_or_verification=False,
                reason=(
                    "Symbolic, proof-plan, and retrieval-risk draft text comes from the recorded "
                    "non-fake LLM and remains draft-labeled."
                ),
                artifact_ids=[report_id, *draft_ids],
                fallback_used=planner.fallback_used,
                fallback_disclosed=planner.fallback_disclosed,
            )
        )
    if has_retrieval:
        records.append(
            stage_backend_record(
                stage_id=f"{report_id}-retrieval-context",
                stage_kind=ScientificStageKind.LITERATURE_RETRIEVAL,
                backend_kind=(
                    BackendKind.RETRIEVAL_REAL
                    if retrieval_mode == "real_retrieval"
                    else BackendKind.FIXTURE
                ),
                backend_name=retrieval_mode,
                is_scientific_generation=False,
                is_scientific_judgment=False,
                is_execution_or_verification=True,
                allowed_in_production=retrieval_mode == "real_retrieval",
                reason=(
                    "Literature novelty checks use existing bounded retrieval contexts; mocked "
                    "retrieval is development-only."
                ),
                artifact_ids=[report_id],
            )
        )
        records.append(
            stage_backend_record(
                stage_id=f"{report_id}-novelty-assessment",
                stage_kind=ScientificStageKind.NOVELTY_ASSESSMENT,
                backend_kind=planner.backend_kind,
                backend_name=planner.backend_name,
                is_scientific_generation=False,
                is_scientific_judgment=True,
                is_execution_or_verification=False,
                reason="Novelty output is bounded to risk assessment with novelty_proven=false.",
                artifact_ids=[report_id, *draft_ids],
                fallback_used=planner.fallback_used,
                fallback_disclosed=planner.fallback_disclosed,
            )
        )
    return records


def _planning_backend_record(
    report_id: str, planner: HybridEvidenceClient, raw_ids: list[str]
) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-hybrid-evidence-planning",
        stage_kind=ScientificStageKind.HYBRID_EVIDENCE_PLANNING,
        backend_kind=planner.backend_kind,
        backend_name=planner.backend_name,
        is_scientific_generation=False,
        is_scientific_judgment=True,
        is_execution_or_verification=False,
        reason="Hybrid evidence-package composition and rationale come from the non-fake LLM.",
        artifact_ids=[report_id, *raw_ids],
        fallback_used=planner.fallback_used,
        fallback_disclosed=planner.fallback_disclosed,
    )


def _package_validation_backend_record(
    report_id: str, artifact_ids: list[str]
) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-package-validation",
        stage_kind=ScientificStageKind.SPEC_VALIDATION,
        backend_kind=BackendKind.LOCAL_EXECUTION,
        backend_name="hybrid_evidence_contract_validator",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=True,
        allowed_in_production=True,
        reason="Local validation enforces artifact types, label policy, and claim boundaries.",
        artifact_ids=[report_id, *artifact_ids],
    )


def _load_latest_substrate_report(
    run_id: str, reports: Path
) -> tuple[Path, LLMSubstrateConstructionReport]:
    path = _latest_matching(reports, _SUBSTRATE_RE)
    if path is None:
        raise HybridEvidencePackageError(f"No M100 substrate report found for run_id={run_id}.")
    report = _load_substrate_report(path)
    if report.run_id != run_id:
        raise HybridEvidencePackageError("M100 substrate report run_id is inconsistent.")
    return path, report


def _load_substrate_report(path: Path) -> LLMSubstrateConstructionReport:
    try:
        return LLMSubstrateConstructionReport.model_validate_json(
            path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise HybridEvidencePackageError(f"Could not load M100 substrate report: {exc}") from exc


def _load_optional_route_report(
    run_id: str, reports: Path
) -> tuple[Path | None, LLMRoutePlanningReport | None]:
    path = _latest_matching(reports, _ROUTE_RE)
    if path is None:
        return None, None
    try:
        report = LLMRoutePlanningReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise HybridEvidencePackageError(f"Could not load M101 route report: {exc}") from exc
    if report.run_id != run_id:
        raise HybridEvidencePackageError("M101 route report run_id is inconsistent.")
    return path, report


def _load_deep_from_substrate(
    root_path: Path, substrate_report: LLMSubstrateConstructionReport
) -> DeepOpportunityDiscoveryReport:
    path = root_path / substrate_report.source_deep_opportunity_report_path
    try:
        return DeepOpportunityDiscoveryReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise HybridEvidencePackageError(f"Could not load deep opportunity report: {exc}") from exc


def _load_retrieval_contexts(
    root_path: Path, report: DeepOpportunityDiscoveryReport
) -> dict[str, RetrievalContext]:
    result = {}
    for relative in report.retrieval_context_paths:
        path = root_path / relative
        try:
            context = RetrievalContext.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise HybridEvidencePackageError(
                f"Could not load retrieval context {relative}: {exc}"
            ) from exc
        result[context.source_pair_id] = context
    return result


def _load_latest_package_report(
    run_id: str, reports: Path
) -> tuple[Path, HybridEvidencePackageReport]:
    path = _latest_matching(reports, _PACKAGE_RE)
    if path is None:
        raise HybridEvidencePackageError(f"No hybrid evidence package report found for {run_id}.")
    report = _load_package_report(path)
    if report.run_id != run_id:
        raise HybridEvidencePackageError("Hybrid evidence package report run_id is inconsistent.")
    return path, report


def _load_package_report(path: Path) -> HybridEvidencePackageReport:
    try:
        return HybridEvidencePackageReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise HybridEvidencePackageError(f"Could not load hybrid package report: {exc}") from exc


def _load_execution_report(path: Path) -> EvidencePackageExecutionReport:
    try:
        return EvidencePackageExecutionReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise HybridEvidencePackageError(f"Could not load package execution report: {exc}") from exc


def _latest_matching(directory: Path, pattern: re.Pattern[str]) -> Path | None:
    if not directory.is_dir():
        return None
    matches = [path for path in directory.iterdir() if pattern.match(path.name)]
    return max(matches, key=lambda path: path.name) if matches else None


def _next_number(directory: Path, pattern: re.Pattern[str]) -> int:
    if not directory.is_dir():
        return 1
    numbers = [
        int(match.group(1))
        for path in directory.iterdir()
        if (match := pattern.match(path.name)) is not None
    ]
    return max(numbers, default=0) + 1


def _relative(root_path: Path, path: Path) -> str:
    try:
        return path.relative_to(root_path).as_posix()
    except ValueError:
        return path.as_posix()


def _slug(value: str) -> str:
    return "-".join(_TOKEN_RE.findall(value.lower()))[:80] or "package"


def _output_string(payload: dict[str, Any] | None, key: str, fallback: str) -> str:
    if payload is None or not isinstance(payload.get(key), str) or not payload[key].strip():
        return fallback
    return str(payload[key]).strip()


def _output_bool(payload: dict[str, Any] | None, key: str) -> bool | None:
    if payload is None or not isinstance(payload.get(key), bool):
        return None
    return bool(payload[key])


def _metadata(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "artifact_role": "hybrid_evidence_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "creates_real_world_validation": False,
        "creates_verified_theorem": False,
        "novelty_proven": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }


__all__ = [
    "HybridEvidencePackageError",
    "HybridEvidencePackageStageResult",
    "execute_hybrid_evidence_packages",
    "inspect_evidence_package_execution",
    "inspect_hybrid_evidence_packages",
    "plan_hybrid_evidence_packages",
    "render_evidence_package_execution_text",
    "render_hybrid_evidence_package_text",
]
