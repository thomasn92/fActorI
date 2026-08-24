"""Generic one-branch orchestration over the production M98-M106 pipeline."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from factori.adaptive_evidence import (
    adaptive_loop_can_resume,
    recover_historical_code_artifacts,
    run_adaptive_evidence_loop,
)
from factori.artifacts import ArtifactStore
from factori.deep_opportunity_discovery import discover_deep_opportunities
from factori.evidence_package_adjudication import (
    adjudicate_evidence_packages,
    critique_evidence_packages,
)
from factori.final_paper import (
    assemble_final_paper,
    build_final_paper_bundle,
    render_final_paper,
    verify_final_paper,
)
from factori.hybrid_evidence_packages import (
    execute_hybrid_evidence_packages,
    plan_hybrid_evidence_packages,
)
from factori.ledger import ResearchLedger
from factori.llm_route_planning import plan_llm_routes
from factori.llm_substrate import construct_llm_substrates
from factori.llm_variance import (
    construct_idea_tree_from_llm_variance,
    generate_llm_variance,
)
from factori.nucleus_manuscript import (
    plan_nucleus_manuscript,
    revise_nucleus_manuscript,
    synthesize_nucleus_manuscript,
)
from factori.persistence import ArtifactWriteSpec, persist_artifacts_with_commit
from factori.production_mode import check_production_mode
from factori.schemas import (
    AdaptiveEvidenceLoopReport,
    ArtifactRef,
    ArtifactType,
    BackendKind,
    Candidate,
    ControllerActionType,
    DeepOpportunityDiscoveryConfig,
    EvidencePackageExecutionReport,
    FinalPaperAssemblyConfig,
    HybridEvidencePackageConfig,
    LatexRenderConfig,
    LLMBudgetUsage,
    LLMRoutePlanningConfig,
    LLMSubstrateConstructionConfig,
    LLMVarianceGenerationConfig,
    ManuscriptCriticRole,
    NucleusManuscriptConfig,
    TargetedResearchBrief,
    TargetedStudyCheckpoint,
    TargetedStudyConfig,
    TargetedStudyInspectionReport,
    TargetedStudyRunReport,
    TargetedStudyStageRecord,
)
from factori.targeted_llm_budget import TargetedLLMBudgetManager

_REPORT_RE = re.compile(r"^targeted-study-report-(\d{4})\.json$")
_CHECKPOINT_RE = re.compile(r"^targeted-study-checkpoint-(\d{4})\.json$")
_EXECUTION_RE = re.compile(r"^evidence-package-execution-report-(\d{4})\.json$")
_ADAPTIVE_RE = re.compile(r"^adaptive-evidence-loop-report-(\d{4})\.json$")
_NUCLEUS_MANUSCRIPT_RE = re.compile(
    r"^nucleus-manuscript-synthesis-report-(\d{4})\.json$"
)
_RETRIEVAL_CONTEXT_RE = re.compile(r"^retrieval-context-(\d{4})\.json$")
_FINAL_PAPER_ASSEMBLY_RE = re.compile(r"^final-paper-assembly-report-(\d{4})\.json$")
_FINAL_PAPER_VERIFICATION_RE = re.compile(
    r"^final-paper-verification-report-(\d{4})\.json$"
)
_FINAL_PAPER_RENDER_RE = re.compile(r"^final-paper-render-report-(\d{4})\.json$")
_MANUSCRIPT_REVISION_CALLS = 2 * len(tuple(ManuscriptCriticRole)) + 1


class TargetedStudyError(RuntimeError):
    """Raised when targeted orchestration cannot proceed without weakening policy."""


@dataclass(frozen=True)
class TargetedStudyClients:
    """Injected non-fake adapters used by smoke and full targeted runs."""

    opportunity_generator: Any
    retriever: Any
    variance_generator: Any
    substrate_generator: Any
    route_planner: Any
    hybrid_planner: Any
    code_generator: Any
    adaptive_questioner: Any | None = None
    scientific_critic: Any | None = None
    manuscript_client: Any | None = None


@dataclass(frozen=True)
class TargetedStudyResult:
    run_id: str
    report: TargetedStudyRunReport
    report_artifact: ArtifactRef | None = None


@dataclass
class _TargetedHybridPlanner:
    """Bind the selected brief and authorized workload to M103 LLM judgment."""

    delegate: Any
    contract: dict[str, Any]

    def __getattr__(self, name: str) -> Any:
        return getattr(self.delegate, name)

    def plan_package(
        self,
        *,
        prompt_id: str,
        substrate_payload: dict[str, Any],
        route_payload: dict[str, Any] | None,
        retrieval_context_payload: dict[str, Any] | None,
    ) -> Any:
        bounded_substrate = dict(substrate_payload)
        bounded_substrate["targeted_research_contract"] = self.contract
        response = self.delegate.plan_package(
            prompt_id=prompt_id,
            substrate_payload=bounded_substrate,
            route_payload=route_payload,
            retrieval_context_payload=retrieval_context_payload,
        )
        violations = _targeted_workload_violations(
            response.accepted,
            limits=self.contract["authorized_execution_limits"],
        )
        if not violations:
            return response
        return replace(
            response,
            accepted=None,
            rejection_reasons=[*response.rejection_reasons, *violations],
            repair_actions=[
                *response.repair_actions,
                "Regenerate the package within the immutable targeted execution ceilings.",
            ],
        )

    def draft_artifact(
        self,
        *,
        prompt_id: str,
        package_payload: dict[str, Any],
        artifact_plan_payload: dict[str, Any],
        retrieval_context_payload: dict[str, Any] | None,
    ) -> Any:
        bounded_package = dict(package_payload)
        bounded_package["targeted_research_contract"] = self.contract
        return self.delegate.draft_artifact(
            prompt_id=prompt_id,
            package_payload=bounded_package,
            artifact_plan_payload=artifact_plan_payload,
            retrieval_context_payload=retrieval_context_payload,
        )


def load_targeted_research_brief(
    *, config: TargetedStudyConfig, root: str | Path
) -> TargetedResearchBrief:
    """Load a direct brief or normalize one selected Stage A candidate without mutation."""
    root_path = Path(root)
    if config.brief_path_optional:
        path = _resolve_path(root_path, config.brief_path_optional)
        try:
            return TargetedResearchBrief.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            raise TargetedStudyError(f"Could not load targeted brief {path}: {exc}") from exc
    assert config.source_run_id_optional is not None
    assert config.candidate_id_optional is not None
    candidate_path = (
        root_path
        / "runs"
        / config.source_run_id_optional
        / "candidates"
        / f"{config.candidate_id_optional}.json"
    )
    try:
        raw = candidate_path.read_bytes()
        candidate = Candidate.model_validate_json(raw)
    except (OSError, ValidationError) as exc:
        raise TargetedStudyError(
            f"Could not load source candidate {config.candidate_id_optional}: {exc}"
        ) from exc
    return brief_from_candidate(
        candidate=candidate,
        source_run_id=config.source_run_id_optional,
        source_path=candidate_path,
        root=root_path,
        source_hash=hashlib.sha256(raw).hexdigest(),
    )


def _targeted_research_contract(
    brief: TargetedResearchBrief,
    config: TargetedStudyConfig,
) -> dict[str, Any]:
    return {
        "brief_id": brief.brief_id,
        "immutable": True,
        "central_question": brief.central_question,
        "allowed_method_scope": brief.method,
        "experiment_or_proof_direction": brief.experiment_or_proof_direction_optional,
        "required_baselines": brief.baseline_candidates,
        "required_metrics": brief.expected_metrics,
        "required_controls": brief.controls,
        "required_negative_controls": brief.negative_controls,
        "allowed_claim_scope": brief.allowed_claim_scope,
        "forbidden_claims": brief.forbidden_claims,
        "scope_rules": [
            "Do not introduce a proposed or primary method excluded by the selected brief.",
            "Do not replace the central question with a nearby upstream opportunity.",
            "Supporting methods must remain comparators and may not become the paper nucleus.",
            "Treat authorized execution limits as hard ceilings, not minimum targets.",
        ],
        "authorized_execution_limits": {
            "max_replications": config.max_replications,
            "max_resamples": config.max_resamples,
            "max_grid_cells": config.max_grid_cells,
            "timeout_seconds": config.experiment_timeout_seconds,
            "memory_limit_mb": config.experiment_memory_limit_mb,
        },
    }


def _targeted_workload_violations(
    accepted: Any | None,
    *,
    limits: dict[str, int],
) -> list[str]:
    if accepted is None or not hasattr(accepted, "model_dump"):
        return []
    payload = accepted.model_dump(mode="json")
    package = payload.get("package", {})
    plans = package.get("artifact_plans", []) if isinstance(package, dict) else []
    violations: list[str] = []
    for plan in plans if isinstance(plans, list) else []:
        if not isinstance(plan, dict):
            continue
        input_contract = plan.get("input_contract", {})
        parameters = (
            input_contract.get("parameters", [])
            if isinstance(input_contract, dict)
            else []
        )
        for parameter in parameters if isinstance(parameters, list) else []:
            if not isinstance(parameter, dict):
                continue
            name = str(parameter.get("name", "")).casefold()
            value = str(parameter.get("value", ""))
            numbers = [
                int(item.replace(",", "").replace("_", ""))
                for item in re.findall(r"\b\d[\d,_]*\b", value)
            ]
            if not numbers:
                continue
            limit_name = _targeted_workload_limit_name(name)
            if limit_name is not None and max(numbers) > limits[limit_name]:
                violations.append(
                    f"targeted workload parameter {parameter.get('name')}={value!r} "
                    f"exceeds {limit_name}={limits[limit_name]}"
                )
    return violations


def _targeted_workload_limit_name(name: str) -> str | None:
    if any(
        term in name
        for term in ("seed", "replication", "repetition", "replicate", "repeat")
    ):
        return "max_replications"
    if any(term in name for term in ("resample", "bootstrap")):
        return "max_resamples"
    tokens = set(re.findall(r"[a-z]+", name))
    if "grid" in tokens:
        return "max_grid_cells"
    if "per" not in tokens and tokens.intersection(
        {"cells", "scenarios", "conditions", "configurations"}
    ):
        return "max_grid_cells"
    if tokens.intersection({"cell", "scenario", "condition", "configuration"}) and (
        tokens.intersection({"count", "number", "num", "total", "maximum", "max"})
    ):
        return "max_grid_cells"
    return None


def brief_from_candidate(
    *,
    candidate: Candidate,
    source_run_id: str,
    source_path: Path,
    root: Path,
    source_hash: str,
) -> TargetedResearchBrief:
    """Copy a human-selected Stage A direction into the generic brief contract."""
    state = candidate.symbolic_state
    metrics = _string_list(state.get("expected_metrics"))
    controls = _string_list(state.get("controls"))
    negative_controls = _string_list(state.get("negative_controls"))
    risks = _string_list(state.get("risks"))
    baseline = [candidate.baseline] if candidate.baseline else []
    title = str(state.get("title") or candidate.question).strip()
    data_regime = str(state.get("data_regime") or candidate.data_requirement.value)
    adapter_backend = str(state.get("adapter_backend") or "").strip().lower()
    authoring_backend = (
        BackendKind.LLM_OPENAI if adapter_backend == "openai" else BackendKind.HUMAN
    )
    return TargetedResearchBrief(
        brief_id=f"targeted-brief-{_slug(candidate.id)}",
        title=title,
        domain=candidate.domain or candidate.constraints.domain or "targeted research domain",
        method=candidate.method or candidate.constraints.method or "targeted research method",
        central_question=candidate.question,
        hypothesis_optional=candidate.hypothesis,
        theory_or_model_object_optional=candidate.theory,
        experiment_or_proof_direction_optional=candidate.experiment,
        baseline_candidates=baseline,
        expected_metrics=metrics,
        controls=controls,
        negative_controls=negative_controls,
        data_regime=data_regime,
        known_risks=risks,
        allowed_claim_scope=(
            "Only bounded claims supported by generated and validated artifacts under their "
            "declared data, model, and execution regimes."
        ),
        forbidden_claims=[
            "real-world validation without real-world evidence",
            "verified theorem without exact checker evidence",
            "novelty proven",
            "underuse proven",
            "publication ready",
            "general domain truth from bounded or synthetic evidence",
        ],
        source_run_id_optional=source_run_id,
        source_candidate_ids=[candidate.id],
        source_artifact_paths=[_relative(root, source_path)],
        source_content_hashes={_relative(root, source_path): source_hash},
        authoring_backend_kind=authoring_backend,
        selection_backend_kind=BackendKind.HUMAN,
    )


def preflight_targeted_study(
    *, config: TargetedStudyConfig, root: str | Path
) -> TargetedStudyRunReport:
    """Validate source, policy, and conservative budget without writing run artifacts."""
    brief = load_targeted_research_brief(config=config, root=root)
    planned_calls = _planned_call_count(config)
    minimum_calls = _minimum_required_call_count(config)
    estimated_cost = _estimated_cost(config, planned_calls)
    minimum_cost = _estimated_cost(config, minimum_calls)
    blockers = _budget_blockers(config, minimum_calls, minimum_cost)
    stages = [
        TargetedStudyStageRecord(
            stage_name=name,
            status="planned",
            external_call_budget=calls,
        )
        for name, calls in _planned_stages(
            config.mode, config.adaptive_evidence, config.render_final_pdf
        )
    ]
    status = "preflight_ready" if not blockers else "deferred"
    return TargetedStudyRunReport(
        report_id="targeted-study-preflight",
        run_id=config.run_id,
        status=status,
        mode=config.mode,
        brief=brief,
        config=config,
        planned_external_call_count=planned_calls,
        minimum_required_external_call_count=minimum_calls,
        completed_external_call_count_upper_bound=0,
        estimated_cost_usd=estimated_cost,
        minimum_estimated_cost_usd=minimum_cost,
        stage_records=stages,
        blocking_reasons=blockers,
    )


def run_targeted_study(
    *,
    config: TargetedStudyConfig,
    root: str | Path,
    clients: TargetedStudyClients | None = None,
) -> TargetedStudyResult:
    """Run a generic one-branch smoke or full study through existing production stages."""
    preflight = preflight_targeted_study(config=config, root=root)
    if config.mode == "preflight":
        return TargetedStudyResult(run_id=config.run_id, report=preflight)
    if preflight.blocking_reasons:
        raise TargetedStudyError(
            "Targeted study preflight blocked: " + "; ".join(preflight.blocking_reasons)
        )
    if clients is None:
        raise TargetedStudyError("Smoke/full targeted studies require injected non-fake clients.")

    root_path = Path(root)
    store = ArtifactStore(root_path)
    ledger = ResearchLedger(root_path / "runs" / config.run_id / "ledger.jsonl")
    reports = root_path / "runs" / config.run_id / "reports"
    budget = TargetedLLMBudgetManager(
        config=config,
        root=root_path,
        store=store,
        ledger=ledger,
        reserve_calls=_paper_tail_call_reserve(config.mode),
        reserve_questioner_call=config.mode == "full",
    )
    brief = preflight.brief
    budgeted_clients = _budgeted_clients(clients, budget)
    budgeted_clients = replace(
        budgeted_clients,
        hybrid_planner=_TargetedHybridPlanner(
            delegate=budgeted_clients.hybrid_planner,
            contract=_targeted_research_contract(brief, config),
        ),
    )
    config_hash = _model_hash(config.model_copy(update={"resume": False}))
    brief_hash = _model_hash(brief)
    prior_checkpoint = _latest_checkpoint(reports)
    if prior_checkpoint is not None:
        if not config.resume:
            raise TargetedStudyError(
                "Targeted-study checkpoints already exist; use --resume or choose a new run id."
            )
        if (
            not _resume_config_matches(
                prior_checkpoint.config_hash,
                config=config,
                current_hash=config_hash,
                reports=reports,
            )
            or prior_checkpoint.brief_hash != brief_hash
        ):
            raise TargetedStudyError(
                "Resume configuration or source brief differs from checkpoint."
            )
        records = list(prior_checkpoint.stage_records)
        completed = set(prior_checkpoint.completed_stage_names)
        prior_adaptive = _latest_adaptive_report(reports)
        prior_execution = (
            _latest_execution_report(reports) if prior_adaptive is not None else None
        )
        if prior_execution is not None:
            prior_execution = recover_historical_code_artifacts(
                prior_execution, reports
            )
        if (
            "adaptive_evidence_loop" in completed
            and prior_adaptive is not None
            and adaptive_loop_can_resume(
                prior_adaptive,
                config.adaptive_evidence,
                latest_execution=prior_execution,
                authorized_timeout_seconds=config.experiment_timeout_seconds,
            )
        ):
            completed.remove("adaptive_evidence_loop")
        _reopen_deferred_paper_tail(completed, reports)
        if config.render_final_pdf:
            render = _latest_json(reports, _FINAL_PAPER_RENDER_RE)
            if render is None or render.get("render_status") != "rendered":
                completed.difference_update({"final_paper_render", "final_paper_bundle"})
                completed.discard("production_mode_check")
    else:
        records = []
        completed = set()

    brief_path = _persist_brief_if_needed(
        run_id=config.run_id,
        brief=brief,
        reports=reports,
        store=store,
        ledger=ledger,
    )
    completed_calls = budget.usage.total_calls
    checkpoint_paths = [
        _relative(root_path, path) for path in _matching(reports, _CHECKPOINT_RE)
    ]

    def stage(name: str, calls: int, operation: Any) -> Any:
        nonlocal completed_calls
        if name in completed:
            return None
        try:
            result = operation()
        except Exception as exc:
            completed_calls = budget.usage.total_calls
            failed = TargetedStudyStageRecord(
                stage_name=name,
                status="failed",
                external_call_budget=calls,
                error_optional=str(exc),
            )
            records.append(failed)
            _persist_targeted_report(
                run_id=config.run_id,
                config=config,
                brief=brief,
                records=records,
                checkpoints=checkpoint_paths,
                status="failed",
                blocking=[f"{name}: {exc}"],
                completed_calls=completed_calls,
                budget=budget,
                root=root_path,
                store=store,
                ledger=ledger,
            )
            raise TargetedStudyError(f"Targeted stage {name} failed: {exc}") from exc
        paths = _result_paths(result)
        result_report = getattr(result, "report", None)
        deferred_reason = _stage_result_deferred_reason(name, result_report)
        resumable_budget_stop = (
            name == "adaptive_evidence_loop"
            and getattr(result_report, "status", None) == "budget_exhausted"
        )
        deferred = resumable_budget_stop or deferred_reason is not None
        records.append(
            TargetedStudyStageRecord(
                stage_name=name,
                status="deferred" if deferred else "completed",
                artifact_paths=paths,
                external_call_budget=calls,
                warnings=(
                    ["Adaptive evidence loop can resume with a larger call/cost budget."]
                    if resumable_budget_stop
                    else ([deferred_reason] if deferred_reason else [])
                ),
            )
        )
        if not deferred:
            completed.add(name)
        completed_calls = budget.usage.total_calls
        checkpoint = _persist_checkpoint(
            run_id=config.run_id,
            config_hash=config_hash,
            brief_hash=brief_hash,
            records=records,
            call_accounting_paths=budget.call_accounting_paths,
            reports=reports,
            store=store,
            ledger=ledger,
        )
        checkpoint_paths.append(checkpoint.path)
        return result

    stage(
        "deep_opportunity_discovery",
        1,
        lambda: discover_deep_opportunities(
            run_id=config.run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            generator=budgeted_clients.opportunity_generator,
            retriever=budgeted_clients.retriever,
            config=DeepOpportunityDiscoveryConfig(
                run_id=config.run_id,
                backend=config.backend,
                retrieval_mode=config.retrieval_mode,
                source_mode="targeted_brief",
                targeted_brief_path_optional=brief_path,
                max_pairs=1,
                max_generation_calls=1,
                opportunities_per_pair=2,
                max_selected_opportunities=1,
                min_domain_family_coverage=1,
                min_method_family_coverage=1,
                max_opportunities_per_domain_family=1,
                max_opportunities_per_method_family=1,
                max_retrieval_sources_per_pair=3,
                require_non_fake_backends=True,
            ),
        ),
    )
    variance_generation_calls = 2 if config.mode == "full" else 1
    stage(
        "llm_variance_generation",
        variance_generation_calls,
        lambda: generate_llm_variance(
            run_id=config.run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            generator=budgeted_clients.variance_generator,
            config=LLMVarianceGenerationConfig(
                run_id=config.run_id,
                backend=config.backend,
                max_source_opportunities=1,
                variants_per_opportunity=3,
                max_variants_total=3,
                max_selected_variants=3,
                max_generation_calls=variance_generation_calls,
                min_variant_family_coverage=2,
                min_domain_family_coverage=1,
                min_method_family_coverage=1,
                require_non_fake_backends=True,
            ),
        ),
    )
    stage(
        "idea_tree_construction",
        0,
        lambda: construct_idea_tree_from_llm_variance(
            run_id=config.run_id, root=root_path, store=store, ledger=ledger
        ),
    )
    stage(
        "llm_substrate_construction",
        1,
        lambda: construct_llm_substrates(
            run_id=config.run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            generator=budgeted_clients.substrate_generator,
            config=LLMSubstrateConstructionConfig(
                run_id=config.run_id,
                backend=config.backend,
                max_source_variants=1,
                max_constructed_substrates=1,
                max_selected_substrates=1,
                max_generation_calls=1,
                min_domain_family_coverage=1,
                min_method_family_coverage=1,
                min_route_hint_coverage=1,
                require_non_fake_backends=True,
            ),
        ),
    )
    stage(
        "llm_route_planning",
        1,
        lambda: plan_llm_routes(
            run_id=config.run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            planner=budgeted_clients.route_planner,
            config=LLMRoutePlanningConfig(
                run_id=config.run_id,
                backend=config.backend,
                max_source_substrates=1,
                max_planning_calls=1,
                require_non_fake_backends=True,
            ),
        ),
    )
    stage(
        "hybrid_evidence_planning",
        1,
        lambda: plan_hybrid_evidence_packages(
            run_id=config.run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            planner=budgeted_clients.hybrid_planner,
            config=HybridEvidencePackageConfig(
                run_id=config.run_id,
                backend=config.backend,
                max_source_substrates=1,
                max_planning_calls=1,
                require_non_fake_backends=True,
            ),
        ),
    )
    codegen_calls = 1 if config.mode == "smoke" else 12
    safety_repair_calls = 0
    runtime_repair_calls = int(
        config.mode == "full"
        and budget.can_spend_optional(2, quality_repair_calls=1)
    )
    stage(
        "hybrid_evidence_execution",
        codegen_calls + safety_repair_calls + runtime_repair_calls,
        lambda: execute_hybrid_evidence_packages(
            run_id=config.run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            planner=budgeted_clients.hybrid_planner,
            code_generator=budgeted_clients.code_generator,
            retrieval_mode=config.retrieval_mode,
            require_non_fake_backends=True,
            timeout_seconds=(
                30 if config.mode == "smoke" else config.experiment_timeout_seconds
            ),
            memory_limit_mb=(
                512 if config.mode == "smoke" else config.experiment_memory_limit_mb
            ),
            max_artifact_plans=1 if config.mode == "smoke" else 12,
            max_codegen_calls=(
                codegen_calls
                if config.mode == "smoke"
                else min(codegen_calls, budget.remaining_optional_calls)
            ),
            max_safety_repair_calls=safety_repair_calls,
            max_runtime_repair_calls=runtime_repair_calls,
            execution_profile="smoke" if config.mode == "smoke" else "full",
            max_replications=(
                None if config.mode == "smoke" else config.max_replications
            ),
            max_resamples=None if config.mode == "smoke" else config.max_resamples,
            max_grid_cells=None if config.mode == "smoke" else config.max_grid_cells,
            resume_previous_execution=config.resume,
        ),
    )

    if config.mode == "full":
        if clients.adaptive_questioner is None:
            raise TargetedStudyError(
                "Full targeted studies require an adaptive questioner client."
            )
        budget.release_adaptive_questioner_reserve()
        adaptive_questioner = budget.wrap_client(
            clients.adaptive_questioner,
            {"review_evidence": "llm-stage-b-review"},
        )
        plan_repairer = budget.wrap_client(
            clients.hybrid_planner,
            {
                "plan_package": "llm-quality-repair",
                "draft_artifact": "targeted-hybrid-evidence-draft",
            },
        )
        code_repairer = budget.wrap_client(
            clients.code_generator,
            {
                "generate_code": "targeted-experiment-code-generation",
                "repair_code": "llm-quality-repair",
            },
        )
        adaptive_calls = _adaptive_call_upper_bound(config)
        adaptive_result = stage(
            "adaptive_evidence_loop",
            adaptive_calls,
            lambda: run_adaptive_evidence_loop(
                run_id=config.run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                brief=brief,
                questioner=adaptive_questioner,
                planner=budgeted_clients.hybrid_planner,
                plan_repairer=plan_repairer,
                code_generator=budgeted_clients.code_generator,
                code_repairer=code_repairer,
                budget=budget,
                config=config.adaptive_evidence,
                retrieval_mode=config.retrieval_mode,
                require_non_fake_backends=True,
                authorized_timeout_seconds=config.experiment_timeout_seconds,
                authorized_memory_limit_mb=config.experiment_memory_limit_mb,
            ),
        )
        adaptive = (
            adaptive_result.report
            if adaptive_result is not None
            else _latest_adaptive_report(reports)
        )
        if adaptive.status not in {"satisfied_supported", "satisfied_negative"}:
            records.append(
                TargetedStudyStageRecord(
                    stage_name="cross_package_adjudication",
                    status="deferred",
                    warnings=[
                        "Adaptive evidence questioning did not accept a trustworthy supported or "
                        "negative result; no manuscript was synthesized."
                    ],
                )
            )
            return _complete_targeted_run(
                config=config,
                brief=brief,
                records=records,
                checkpoints=checkpoint_paths,
                completed_calls=completed_calls,
                status="deferred",
                blockers=[adaptive.terminal_reason],
                root=root_path,
                store=store,
                ledger=ledger,
                budget=budget,
            )
        execution = _latest_execution_report(reports)
        if not execution.adjudication_ready:
            raise TargetedStudyError(
                "Adaptive evidence acceptance requires an adjudication-ready M103 report."
            )
        budget.release_paper_tail_reserve()
        if (
            budgeted_clients.scientific_critic is None
            or budgeted_clients.manuscript_client is None
        ):
            raise TargetedStudyError("Full targeted studies require critic and manuscript clients.")
        stage(
            "scientific_critic_ensemble",
            8,
            lambda: critique_evidence_packages(
                run_id=config.run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                critic=budgeted_clients.scientific_critic,
                require_non_fake_backends=True,
            ),
        )
        stage(
            "cross_package_adjudication",
            1,
            lambda: adjudicate_evidence_packages(
                run_id=config.run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                critic=budgeted_clients.scientific_critic,
                require_non_fake_backends=True,
            ),
        )
        manuscript_config = NucleusManuscriptConfig(
            run_id=config.run_id,
            backend=config.backend,
            max_revision_attempts=1,
            require_non_fake_backends=True,
        )
        stage(
            "manuscript_planning",
            1,
            lambda: plan_nucleus_manuscript(
                run_id=config.run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                planner=budgeted_clients.manuscript_client,
                config=manuscript_config,
            ),
        )
        stage(
            "manuscript_synthesis",
            1,
            lambda: synthesize_nucleus_manuscript(
                run_id=config.run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                planner=budgeted_clients.manuscript_client,
                config=manuscript_config,
            ),
        )
        stage(
            "manuscript_revision",
            _MANUSCRIPT_REVISION_CALLS,
            lambda: revise_nucleus_manuscript(
                run_id=config.run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                planner=budgeted_clients.manuscript_client,
                config=manuscript_config,
            ),
        )
        assembly_config = FinalPaperAssemblyConfig(
            run_id=config.run_id, require_non_fake_backends=True
        )
        stage(
            "final_paper_assembly",
            0,
            lambda: assemble_final_paper(
                run_id=config.run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                config=assembly_config,
            ),
        )
        stage(
            "final_paper_verification",
            0,
            lambda: verify_final_paper(
                run_id=config.run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                require_non_fake_backends=True,
            ),
        )
        if config.render_final_pdf:
            stage(
                "final_paper_render",
                0,
                lambda: render_final_paper(
                    run_id=config.run_id,
                    root=root_path,
                    store=store,
                    ledger=ledger,
                    config=LatexRenderConfig(
                        run_id=config.run_id,
                        render_check_enabled=True,
                        allow_external_tools=True,
                        latex_executable=config.latex_executable,
                        timeout_seconds=config.latex_timeout_seconds,
                    ),
                ),
            )
        stage(
            "final_paper_bundle",
            0,
            lambda: build_final_paper_bundle(
                run_id=config.run_id,
                root=root_path,
                store=store,
                ledger=ledger,
                require_rendered_pdf=config.render_final_pdf,
            ),
        )

    stage(
        "production_mode_check",
        0,
        lambda: check_production_mode(
            run_id=config.run_id,
            root=root_path,
            store=store,
            ledger=ledger,
            require_non_fake_backends=True,
        ),
    )
    latest_by_stage = {item.stage_name: item for item in records}
    deferred_tail = [
        item
        for name, item in latest_by_stage.items()
        if name in _PAPER_TAIL_STAGES and item.status == "deferred"
    ]
    if deferred_tail:
        return _complete_targeted_run(
            config=config,
            brief=brief,
            records=records,
            checkpoints=checkpoint_paths,
            completed_calls=completed_calls,
            status="deferred",
            blockers=[
                warning
                for item in deferred_tail
                for warning in item.warnings
            ]
            or ["The final paper pipeline did not produce a complete verified bundle."],
            root=root_path,
            store=store,
            ledger=ledger,
            budget=budget,
        )
    return _complete_targeted_run(
        config=config,
        brief=brief,
        records=records,
        checkpoints=checkpoint_paths,
        completed_calls=completed_calls,
        status="completed",
        blockers=[],
        root=root_path,
        store=store,
        ledger=ledger,
        budget=budget,
    )


_PAPER_TAIL_STAGES = {
    "manuscript_planning",
    "manuscript_synthesis",
    "manuscript_revision",
    "final_paper_assembly",
    "final_paper_verification",
    "final_paper_render",
    "final_paper_bundle",
}


def _stage_result_deferred_reason(name: str, report: Any) -> str | None:
    if report is None or name not in _PAPER_TAIL_STAGES:
        return None
    status_field = {
        "manuscript_planning": "manuscript_status",
        "manuscript_synthesis": "manuscript_status",
        "manuscript_revision": "manuscript_status",
        "final_paper_assembly": "assembly_status",
        "final_paper_verification": "verification_status",
        "final_paper_render": "render_status",
        "final_paper_bundle": "assembly_status",
    }[name]
    status = getattr(report, status_field, None)
    status = getattr(status, "value", status)
    if status not in {"deferred", "failed", "manuscript_deferred"}:
        return None
    reasons = list(
        getattr(report, "blocking_reasons", None)
        or getattr(report, "blocking_findings", None)
        or []
    )
    detail = "; ".join(str(item) for item in reasons) or f"reported {status}"
    return f"{name} deferred: {detail}"


def _reopen_deferred_paper_tail(completed: set[str], reports: Path) -> None:
    manuscript = _latest_json(reports, _NUCLEUS_MANUSCRIPT_RE)
    if manuscript:
        has_retrieval_binding = any(
            item.get("source_type") == "retrieval_source"
            for item in manuscript.get("evidence_citation_bindings", [])
            if isinstance(item, dict)
        )
        has_real_literature = any(
            payload.get("retrieval_mode") == "real_retrieval"
            and any(
                isinstance(source, dict) and not source.get("fake_or_mocked", True)
                for source in payload.get("sources", [])
            )
            for payload in _matching_json(reports, _RETRIEVAL_CONTEXT_RE)
        )
        if has_real_literature and not has_retrieval_binding:
            completed.difference_update(_PAPER_TAIL_STAGES)
            completed.discard("production_mode_check")
            return
        phase = manuscript.get("phase")
        deferred = manuscript.get("manuscript_status") in {
            "manuscript_deferred",
            "failed",
        }
        reopen_by_phase = {
            "planning": {
                "manuscript_synthesis",
                "manuscript_revision",
                "final_paper_assembly",
                "final_paper_verification",
                "final_paper_render",
                "final_paper_bundle",
            },
            "synthesis": {
                "manuscript_revision",
                "final_paper_assembly",
                "final_paper_verification",
                "final_paper_render",
                "final_paper_bundle",
            },
            "revision": {
                "final_paper_assembly",
                "final_paper_verification",
                "final_paper_render",
                "final_paper_bundle",
            },
        }
        if phase in reopen_by_phase:
            stages = set(reopen_by_phase[phase])
            if deferred:
                stages.add(f"manuscript_{phase}")
            completed.difference_update(stages)
            completed.discard("production_mode_check")
            return
        if deferred:
            completed.difference_update(_PAPER_TAIL_STAGES)
            completed.discard("production_mode_check")
            return

    assembly_reports = _matching_json(reports, _FINAL_PAPER_ASSEMBLY_RE)
    latest_assembly = next(
        (
            payload
            for payload in reversed(assembly_reports)
            if payload.get("operation") == "assembly"
        ),
        None,
    )
    if latest_assembly and latest_assembly.get("assembly_status") in {"deferred", "failed"}:
        completed.difference_update(
            {
                "final_paper_assembly",
                "final_paper_verification",
                "final_paper_render",
                "final_paper_bundle",
            }
        )
        completed.discard("production_mode_check")
        return

    verification = _latest_json(reports, _FINAL_PAPER_VERIFICATION_RE)
    if verification and verification.get("verification_status") in {"deferred", "failed"}:
        completed.difference_update(
            {"final_paper_verification", "final_paper_render", "final_paper_bundle"}
        )
        completed.discard("production_mode_check")
        return

    render = _latest_json(reports, _FINAL_PAPER_RENDER_RE)
    if render and render.get("render_status") in {"deferred", "failed"}:
        completed.difference_update({"final_paper_render", "final_paper_bundle"})
        completed.discard("production_mode_check")
        return

    latest_bundle = next(
        (
            payload
            for payload in reversed(assembly_reports)
            if payload.get("operation") == "bundle"
        ),
        None,
    )
    if latest_bundle and latest_bundle.get("assembly_status") in {"deferred", "failed"}:
        completed.discard("final_paper_bundle")
        completed.discard("production_mode_check")


def _matching_json(directory: Path, pattern: re.Pattern[str]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for path in _matching(directory, pattern):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def _latest_json(directory: Path, pattern: re.Pattern[str]) -> dict[str, Any] | None:
    payloads = _matching_json(directory, pattern)
    return payloads[-1] if payloads else None


def inspect_targeted_study(*, run_id: str, root: str | Path) -> TargetedStudyInspectionReport:
    """Inspect the latest targeted-study report and checkpoints without mutation."""
    reports = Path(root) / "runs" / run_id / "reports"
    report_path = _latest_matching(reports, _REPORT_RE)
    checkpoint_paths = _matching(reports, _CHECKPOINT_RE)
    if report_path is None:
        return TargetedStudyInspectionReport(
            run_id=run_id,
            targeted_study_present=False,
            checkpoint_count=len(checkpoint_paths),
        )
    try:
        report = TargetedStudyRunReport.model_validate_json(
            report_path.read_text(encoding="utf-8")
        )
    except (OSError, ValidationError) as exc:
        raise TargetedStudyError(f"Could not inspect targeted study: {exc}") from exc
    completed = [
        item.stage_name
        for item in report.stage_records
        if item.status in {"completed", "reused"}
    ]
    planned = [
        name
        for name, _ in _planned_stages(
            report.mode,
            report.config.adaptive_evidence,
            report.config.render_final_pdf,
        )
    ]
    next_stage = next((name for name in planned if name not in completed), None)
    adaptive = _latest_adaptive_report(reports)
    if (
        report.status == "deferred"
        and adaptive is not None
        and adaptive.status not in {"satisfied_supported", "satisfied_negative"}
    ):
        next_stage = None
    return TargetedStudyInspectionReport(
        run_id=run_id,
        targeted_study_present=True,
        latest_report_optional=report,
        checkpoint_count=len(checkpoint_paths),
        completed_stage_names=completed,
        next_stage_optional=next_stage,
        adaptive_evidence_status_optional=(adaptive.status if adaptive else None),
        adaptive_iteration_count=len(adaptive.iterations) if adaptive else 0,
        adaptive_last_action_optional=(
            adaptive.iterations[-1].decision.action
            if adaptive and adaptive.iterations
            else None
        ),
        adaptive_code_repair_count=(
            adaptive.code_repair_attempt_count if adaptive else 0
        ),
        adaptive_plan_repair_count=(
            adaptive.plan_repair_attempt_count if adaptive else 0
        ),
        adaptive_unresolved_blocking_questions=(
            adaptive.unresolved_blocking_questions if adaptive else []
        ),
        budget_usage=report.budget_usage,
        warnings=report.warnings,
        production_ready=report.production_ready,
    )


def _budgeted_clients(
    clients: TargetedStudyClients,
    budget: TargetedLLMBudgetManager,
) -> TargetedStudyClients:
    """Wrap every LLM transport method in one persistent runtime budget guard."""
    return TargetedStudyClients(
        opportunity_generator=budget.wrap_client(
            clients.opportunity_generator,
            {"generate_for_pair": "llm-candidate-generation"},
        ),
        retriever=clients.retriever,
        variance_generator=budget.wrap_client(
            clients.variance_generator,
            {"generate_variants": "targeted-variance-generation"},
        ),
        substrate_generator=budget.wrap_client(
            clients.substrate_generator,
            {"construct_substrate": "targeted-substrate-construction"},
        ),
        route_planner=budget.wrap_client(
            clients.route_planner,
            {"plan_route": "targeted-route-planning"},
        ),
        hybrid_planner=budget.wrap_client(
            clients.hybrid_planner,
            {
                "plan_package": "targeted-hybrid-evidence-planning",
                "draft_artifact": "targeted-symbolic-artifact-drafting",
            },
        ),
        code_generator=budget.wrap_client(
            clients.code_generator,
            {
                "generate_code": "targeted-experiment-code-generation",
                "repair_code": "llm-quality-repair",
            },
        ),
        adaptive_questioner=clients.adaptive_questioner,
        scientific_critic=(
            budget.wrap_client(
                clients.scientific_critic,
                {
                    "critique_package": "llm-stage-b-review",
                    "adjudicate_packages": "llm-claim-adjudication",
                },
            )
            if clients.scientific_critic is not None
            else None
        ),
        manuscript_client=(
            budget.wrap_client(
                clients.manuscript_client,
                {
                    "plan_manuscript": "llm-prose-generation",
                    "synthesize_manuscript": "llm-prose-generation",
                    "critique_manuscript": "llm-stage-b-review",
                    "revise_manuscript": "llm-prose-generation",
                },
            )
            if clients.manuscript_client is not None
            else None
        ),
    )


def _planned_stages(
    mode: str,
    adaptive_config: Any | None = None,
    render_final_pdf: bool = False,
) -> list[tuple[str, int]]:
    base = [
        ("deep_opportunity_discovery", 1),
        ("llm_variance_generation", 2 if mode == "full" else 1),
        ("idea_tree_construction", 0),
        ("llm_substrate_construction", 1),
        ("llm_route_planning", 1),
        ("hybrid_evidence_planning", 1),
        ("hybrid_evidence_execution", 1 if mode != "full" else 13),
    ]
    if mode == "full":
        adaptive_calls = (
            adaptive_config.max_questioner_iterations
            + adaptive_config.max_code_repair_calls
            + 3 * adaptive_config.max_plan_repair_calls
            if adaptive_config is not None
            else 7
        )
        base.extend(
            [
                ("adaptive_evidence_loop", adaptive_calls),
                ("scientific_critic_ensemble", 8),
                ("cross_package_adjudication", 1),
                ("manuscript_planning", 1),
                ("manuscript_synthesis", 1),
                ("manuscript_revision", _MANUSCRIPT_REVISION_CALLS),
                ("final_paper_assembly", 0),
                ("final_paper_verification", 0),
            ]
        )
        if render_final_pdf:
            base.append(("final_paper_render", 0))
        base.append(("final_paper_bundle", 0))
    if mode != "preflight":
        base.append(("production_mode_check", 0))
    return base


def _planned_call_count(config: TargetedStudyConfig) -> int:
    return sum(
        calls
        for _, calls in _planned_stages(
            config.mode, config.adaptive_evidence, config.render_final_pdf
        )
    )


def _minimum_required_call_count(config: TargetedStudyConfig) -> int:
    if config.mode != "full":
        return _planned_call_count(config)
    # Five scientific planning calls, one initial code-generation call, one questioner
    # pass, and the fixed M104-M105 LLM tail.
    return 5 + 1 + 1 + _paper_tail_call_reserve(config.mode)


def _paper_tail_call_reserve(mode: str) -> int:
    if mode != "full":
        return 0
    return 8 + 1 + 1 + 1 + _MANUSCRIPT_REVISION_CALLS


def _adaptive_call_upper_bound(config: TargetedStudyConfig) -> int:
    adaptive = config.adaptive_evidence
    return (
        adaptive.max_questioner_iterations
        + adaptive.max_code_repair_calls
        + 3 * adaptive.max_plan_repair_calls
    )


def _estimated_cost(config: TargetedStudyConfig, calls: int) -> float:
    per_call = (
        config.estimated_input_tokens_per_call * config.input_cost_per_million_usd
        + config.estimated_output_tokens_per_call * config.output_cost_per_million_usd
    ) / 1_000_000
    return round(calls * per_call, 6)


def _budget_blockers(
    config: TargetedStudyConfig, calls: int, estimated_cost: float
) -> list[str]:
    blockers: list[str] = []
    if config.mode != "preflight" and calls > config.max_total_calls:
        blockers.append(
            f"minimum required calls {calls} exceed max_total_calls={config.max_total_calls}"
        )
    if config.mode != "preflight" and estimated_cost > config.max_cost_usd:
        blockers.append(
            "minimum estimated cost "
            f"${estimated_cost:.4f} exceeds max_cost_usd=${config.max_cost_usd:.4f}"
        )
    return blockers


def _persist_brief_if_needed(
    *,
    run_id: str,
    brief: TargetedResearchBrief,
    reports: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> str:
    path = reports / f"{brief.brief_id}.json"
    if path.is_file():
        existing = TargetedResearchBrief.model_validate_json(path.read_text(encoding="utf-8"))
        if _model_hash(existing) != _model_hash(brief):
            raise TargetedStudyError("Persisted targeted brief differs from the requested source.")
        return _relative(store.root, path)
    result = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                brief.brief_id,
                ArtifactType.REPORT,
                brief,
                "json",
                _metadata("targeted_research_brief"),
            )
        ],
        action_type=ControllerActionType.CONTROLLER_ACTION,
        commit_payload={
            "operation": "targeted_research_brief_persisted",
            "brief_id": brief.brief_id,
        },
    )
    return result.artifacts[0].path


def _persist_checkpoint(
    *,
    run_id: str,
    config_hash: str,
    brief_hash: str,
    records: list[TargetedStudyStageRecord],
    call_accounting_paths: list[str],
    reports: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    number = _next_number(reports, _CHECKPOINT_RE)
    checkpoint_id = f"targeted-study-checkpoint-{number:04d}"
    completed = [item.stage_name for item in records if item.status in {"completed", "reused"}]
    checkpoint = TargetedStudyCheckpoint(
        checkpoint_id=checkpoint_id,
        run_id=run_id,
        config_hash=config_hash,
        brief_hash=brief_hash,
        completed_stage_names=completed,
        latest_stage_optional=completed[-1] if completed else None,
        stage_records=records,
        call_accounting_paths=call_accounting_paths,
    )
    result = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                checkpoint_id,
                ArtifactType.REPORT,
                checkpoint,
                "json",
                _metadata("targeted_study_checkpoint"),
            )
        ],
        action_type=ControllerActionType.CONTROLLER_ACTION,
        commit_payload={"operation": "targeted_study_checkpoint", "checkpoint_id": checkpoint_id},
    )
    return result.artifacts[0]


def _complete_targeted_run(
    *,
    config: TargetedStudyConfig,
    brief: TargetedResearchBrief,
    records: list[TargetedStudyStageRecord],
    checkpoints: list[str],
    status: str,
    blockers: list[str],
    completed_calls: int,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    budget: TargetedLLMBudgetManager | None = None,
) -> TargetedStudyResult:
    return _persist_targeted_report(
        config=config,
        brief=brief,
        records=records,
        checkpoints=checkpoints,
        status=status,
        blocking=blockers,
        completed_calls=completed_calls,
        root=root,
        store=store,
        ledger=ledger,
        budget=budget,
    )


def _persist_targeted_report(
    *,
    run_id: str | None = None,
    config: TargetedStudyConfig,
    brief: TargetedResearchBrief,
    records: list[TargetedStudyStageRecord],
    checkpoints: list[str],
    status: str,
    blocking: list[str],
    completed_calls: int,
    root: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    budget: TargetedLLMBudgetManager | None = None,
) -> TargetedStudyResult:
    actual_run_id = run_id or config.run_id
    reports = root / "runs" / actual_run_id / "reports"
    number = _next_number(reports, _REPORT_RE)
    report_id = f"targeted-study-report-{number:04d}"
    planned_calls = _planned_call_count(config)
    minimum_calls = _minimum_required_call_count(config)
    adaptive = _latest_adaptive_report(reports)
    budget_usage = (
        budget.usage
        if budget is not None
        else LLMBudgetUsage(
            total_calls=completed_calls,
            total_input_tokens=(
                completed_calls * config.estimated_input_tokens_per_call
            ),
            total_output_tokens=(
                completed_calls * config.estimated_output_tokens_per_call
            ),
            estimated_cost_usd=_estimated_cost(config, completed_calls),
        )
    )
    accounting_paths = list(budget.call_accounting_paths) if budget is not None else []
    report = TargetedStudyRunReport(
        report_id=report_id,
        run_id=actual_run_id,
        status=status,
        mode=config.mode,
        brief=brief,
        config=config,
        planned_external_call_count=planned_calls,
        minimum_required_external_call_count=minimum_calls,
        completed_external_call_count_upper_bound=budget_usage.total_calls,
        estimated_cost_usd=_estimated_cost(config, planned_calls),
        minimum_estimated_cost_usd=_estimated_cost(config, minimum_calls),
        budget_usage=budget_usage,
        call_accounting_paths=accounting_paths,
        adaptive_evidence_report_path_optional=(
            _relative(
                root,
                reports / f"{adaptive.report_id}.json",
            )
            if adaptive is not None
            else None
        ),
        adaptive_evidence_status_optional=adaptive.status if adaptive else None,
        stage_records=records,
        checkpoint_paths=checkpoints,
        terminal_artifact_paths=[path for item in records for path in item.artifact_paths],
        blocking_reasons=blocking,
        production_ready=(status == "completed" and config.require_non_fake_backends),
    )
    result = persist_artifacts_with_commit(
        run_id=actual_run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                report_id,
                ArtifactType.REPORT,
                report,
                "json",
                _metadata("targeted_study_report"),
            )
        ],
        action_type=ControllerActionType.CONTROLLER_ACTION,
        commit_payload={
            "operation": "targeted_study_report",
            "report_id": report_id,
            "status": status,
        },
    )
    return TargetedStudyResult(actual_run_id, report, result.artifacts[0])


def _latest_checkpoint(reports: Path) -> TargetedStudyCheckpoint | None:
    path = _latest_matching(reports, _CHECKPOINT_RE)
    if path is None:
        return None
    try:
        return TargetedStudyCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise TargetedStudyError(f"Could not load targeted-study checkpoint: {exc}") from exc


def _resume_config_matches(
    checkpoint_hash: str,
    *,
    config: TargetedStudyConfig,
    current_hash: str,
    reports: Path,
) -> bool:
    if checkpoint_hash == current_hash:
        return True
    prior_path = _latest_matching(reports, _REPORT_RE)
    if prior_path is None:
        return False
    try:
        prior = TargetedStudyRunReport.model_validate_json(
            prior_path.read_text(encoding="utf-8")
        ).config
    except (OSError, ValidationError):
        return False

    prior_base = prior.model_dump(mode="json")
    current_base = config.model_dump(mode="json")
    for payload in (prior_base, current_base):
        payload.pop("resume", None)
        payload.pop("max_total_calls", None)
        payload.pop("max_cost_usd", None)
        payload.pop("adaptive_evidence", None)
        payload.pop("experiment_timeout_seconds", None)
        payload.pop("experiment_memory_limit_mb", None)
        payload.pop("reasoning_effort", None)
        payload.pop("llm_timeout_seconds", None)
        payload.pop("render_final_pdf", None)
        payload.pop("latex_executable", None)
        payload.pop("latex_timeout_seconds", None)
    if prior_base != current_base:
        return False
    if config.max_total_calls < prior.max_total_calls:
        return False
    if config.max_cost_usd < prior.max_cost_usd:
        return False
    if config.experiment_timeout_seconds < prior.experiment_timeout_seconds:
        return False
    if config.experiment_memory_limit_mb < prior.experiment_memory_limit_mb:
        return False
    effort_rank = {"default": 0, "low": 1, "medium": 2, "high": 3}
    if effort_rank[config.reasoning_effort] < effort_rank[prior.reasoning_effort]:
        return False
    if config.llm_timeout_seconds < prior.llm_timeout_seconds:
        return False
    if prior.render_final_pdf and not config.render_final_pdf:
        return False
    if (
        prior.render_final_pdf
        and config.render_final_pdf
        and config.latex_executable != prior.latex_executable
    ):
        return False
    if config.latex_timeout_seconds < prior.latex_timeout_seconds:
        return False
    previous_adaptive = prior.adaptive_evidence
    current_adaptive = config.adaptive_evidence
    return all(
        current >= previous
        for current, previous in (
            (
                current_adaptive.max_questioner_iterations,
                previous_adaptive.max_questioner_iterations,
            ),
            (
                current_adaptive.max_code_repair_calls,
                previous_adaptive.max_code_repair_calls,
            ),
            (
                current_adaptive.max_plan_repair_calls,
                previous_adaptive.max_plan_repair_calls,
            ),
            (current_adaptive.no_progress_limit, previous_adaptive.no_progress_limit),
        )
    )


def _latest_execution_report(reports: Path) -> EvidencePackageExecutionReport:
    path = _latest_matching(reports, _EXECUTION_RE)
    if path is None:
        raise TargetedStudyError("Hybrid evidence execution report is missing.")
    return EvidencePackageExecutionReport.model_validate_json(path.read_text(encoding="utf-8"))


def _latest_adaptive_report(reports: Path) -> AdaptiveEvidenceLoopReport | None:
    path = _latest_matching(reports, _ADAPTIVE_RE)
    if path is None:
        return None
    try:
        return AdaptiveEvidenceLoopReport.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValidationError) as exc:
        raise TargetedStudyError(f"Could not load adaptive evidence report: {exc}") from exc


def _result_paths(result: Any) -> list[str]:
    paths: list[str] = []
    for name in ("report_artifact", "markdown_artifact"):
        artifact = getattr(result, name, None)
        if artifact is not None and getattr(artifact, "path", None):
            paths.append(artifact.path)
    return paths


def _model_hash(model: Any) -> str:
    data = model.model_dump_json(exclude_none=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _string_list(value: Any) -> list[str]:
    return [str(item).strip() for item in value] if isinstance(value, list) else []


def _resolve_path(root: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else root / path


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _slug(value: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", value.lower())) or "source"


def _matching(directory: Path, pattern: re.Pattern[str]) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(item for item in directory.iterdir() if pattern.match(item.name))


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


def _metadata(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "artifact_role": "targeted_study_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }
