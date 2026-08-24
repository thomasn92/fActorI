from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import factori.adaptive_evidence as adaptive_evidence_module
import factori.targeted_study as targeted_study_module
from factori.adapters.adaptive_questioner import (
    AdaptiveQuestionAnswerProposal,
    AdaptiveQuestionerDecisionProposal,
    AdaptiveQuestionerResponse,
    build_adaptive_questioner_prompt,
    parse_adaptive_questioner_response,
)
from factori.adapters.deep_opportunity import (
    OpportunityGenerationResponse,
    OpportunityProposal,
    OpportunityProposalEnvelope,
    OpportunityProposalItem,
    OpportunityScoreProposal,
)
from factori.adapters.errors import AdapterTransportError
from factori.adapters.llm_variance import (
    VarianceCandidateProposal,
    VarianceGenerationResponse,
    VarianceProposalItem,
    VarianceScoreProposal,
    build_llm_variance_prompt,
)
from factori.adaptive_evidence import (
    _can_retry_timed_out_execution,
    _Diagnostic,
    _iteration_counts_for_no_progress,
    _premature_stop_repair_action,
    _repair_instructions_for_override,
    adaptive_loop_can_resume,
    run_adaptive_evidence_loop,
)
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.deep_opportunity_discovery import (
    DeepOpportunityDiscoveryError,
    MockedOpportunityRetriever,
    OpenAlexOpportunityRetriever,
    _retrieval_queries,
    _retrieval_query,
    _targeted_source_metadata,
    discover_deep_opportunities,
    inspect_deep_opportunities,
)
from factori.ledger import ResearchLedger
from factori.llm_variance import generate_llm_variance
from factori.production_mode import collect_backend_records
from factori.schemas import (
    AdaptiveEvidenceLoopConfig,
    ArtifactType,
    BackendKind,
    BranchRouteType,
    Candidate,
    DataRequirement,
    DeepOpportunityDiscoveryConfig,
    EvidenceArtifactPlan,
    EvidenceArtifactType,
    EvidencePackageExecutionReport,
    EvidencePackageExecutionResult,
    ExperimentCodeSafetyAudit,
    HybridEvidencePackageCandidate,
    HybridEvidencePackageConfig,
    HybridEvidencePackageReport,
    LLMExperimentCodeArtifact,
    LLMVarianceGenerationConfig,
    MetricExtractionResult,
    SandboxExecutionConfig,
    SandboxExecutionResult,
    TargetedResearchBrief,
    TargetedStudyConfig,
)
from factori.targeted_llm_budget import TargetedLLMBudgetManager
from factori.targeted_study import (
    _PAPER_TAIL_STAGES,
    TargetedStudyClients,
    _complete_targeted_run,
    _model_hash,
    _reopen_deferred_paper_tail,
    _resume_config_matches,
    _stage_result_deferred_reason,
    _targeted_research_contract,
    _targeted_workload_violations,
    _TargetedHybridPlanner,
    inspect_targeted_study,
    preflight_targeted_study,
    run_targeted_study,
)


class _OpportunityGenerator:
    backend_name = "llm-openai-test-transport"
    backend_kind = BackendKind.LLM_OPENAI
    model = "test-model"
    fallback_used = False
    fallback_disclosed = True

    def generate_for_pair(
        self, *, pair_payload, retrieval_payload, opportunities_per_pair
    ) -> OpportunityGenerationResponse:
        accepted = []
        for index in range(opportunities_per_pair):
            accepted.append(
                OpportunityProposalItem(
                    candidate=OpportunityProposal(
                        research_question=f"Bounded targeted question {index}?",
                        hypothesis=f"Targeted hypothesis {index} is testable.",
                        theory_or_model_object=f"Concrete model object {index}",
                        mathematical_or_computational_form=f"f_{index}(x)",
                        experiment_or_proof_plan="Run a fixed-seed controlled comparison.",
                        benchmark_plan="Compare against the declared baseline.",
                        baseline_candidates=["declared baseline"],
                        expected_metrics=["bounded metric"],
                        failure_modes=["no improvement"],
                        negative_controls=["remove the proposed mechanism"],
                        data_regime="synthetic_only",
                        verification_path="controlled local execution",
                        paper_shape="bounded methods study",
                        novelty_risk="Hypothesis: close prior work may exist.",
                        underuse_hypothesis="Hypothesis: use may be limited; unverified.",
                        retrieval_support_summary="Mock context is non-evidence context only.",
                        false_bridge_risks=["scope inflation"],
                        tautology_risks=["rigged data generation"],
                        recommended_next_stage="variance_generation",
                    ),
                    score=OpportunityScoreProposal(
                        scientific_fit=0.8,
                        tractability=0.8,
                        question_specificity=0.8,
                        baseline_strength=0.8,
                        verification_feasibility=0.8,
                        expected_signal=0.7,
                        failure_mode_value=0.8,
                        paper_coherence=0.8,
                        novelty_risk_penalty=0.2,
                        false_bridge_penalty=0.1,
                        tautology_penalty=0.1,
                        retrieval_confidence=retrieval_payload["retrieval_confidence"],
                        final_score=0.8 - 0.01 * index,
                        score_explanation="Injected non-network LLM transport fixture.",
                    ),
                )
            )
        return OpportunityGenerationResponse(
            prompt_text="targeted opportunity test prompt",
            requested_output_schema=OpportunityProposalEnvelope.model_json_schema(),
            raw_response={"opportunities": [item.model_dump(mode="json") for item in accepted]},
            accepted=accepted,
            rejected=[],
        )


class _VarianceGenerator:
    backend_name = "llm-openai-test-transport"
    backend_kind = BackendKind.LLM_OPENAI
    model = "test-model"
    fallback_used = False
    fallback_disclosed = True

    def generate_variants(
        self,
        *,
        prompt_id,
        source_payload,
        retrieval_context_payload,
        variants_per_opportunity,
    ) -> VarianceGenerationResponse:
        families = ["benchmark", "robustness", "mechanism"]
        accepted = [
            VarianceProposalItem(
                candidate=VarianceCandidateProposal(
                    variant_family=family,
                    title=f"{family} targeted variant",
                    research_question=f"What happens in the {family} branch?",
                    hypothesis=f"The {family} branch differs from its comparator.",
                    theory_or_model_object=f"Concrete {family} object",
                    mathematical_or_computational_form=f"g_{index}(x)",
                    experiment_or_proof_plan="Run a bounded controlled check.",
                    benchmark_plan="Compare with a fixed baseline.",
                    baseline_candidates=["fixed baseline"],
                    negative_controls=["remove mechanism"],
                    failure_modes=["no measurable difference"],
                    verification_path="local synthetic execution",
                    expected_metrics=["bounded metric"],
                    data_regime="synthetic_only",
                    paper_role=family,
                    scientific_rationale="Tests a distinct bounded branch.",
                    novelty_risk="Hypothesis: prior work may overlap.",
                    false_bridge_risk="Scope may exceed evidence.",
                    tautology_risk="The generator may encode the result.",
                ),
                score=VarianceScoreProposal(
                    specificity=0.8,
                    branch_diversity=0.8,
                    baseline_quality=0.8,
                    verification_feasibility=0.8,
                    failure_mode_value=0.8,
                    paper_coherence=0.8,
                    novelty_risk_penalty=0.2,
                    false_bridge_penalty=0.1,
                    tautology_penalty=0.1,
                    final_score=0.8 - index * 0.01,
                    score_explanation="Injected non-network LLM transport fixture.",
                ),
            )
            for index, family in enumerate(families)
        ]
        prompt = build_llm_variance_prompt(
            prompt_id=prompt_id,
            backend_name=self.backend_name,
            model=self.model,
            source_payload=source_payload,
            retrieval_context_payload=retrieval_context_payload,
            variants_per_opportunity=variants_per_opportunity,
        )
        return VarianceGenerationResponse(
            prompt=prompt,
            raw_response={"variants": [item.model_dump(mode="json") for item in accepted]},
            accepted=accepted,
            rejected=[],
        )


class _CoverageRepairVarianceGenerator(_VarianceGenerator):
    def __init__(self) -> None:
        self.call_count = 0
        self.repair_payload = None

    def generate_variants(self, **kwargs) -> VarianceGenerationResponse:
        self.call_count += 1
        response = super().generate_variants(**kwargs)
        if self.call_count > 1:
            self.repair_payload = kwargs["source_payload"].get(
                "variance_generation_repair"
            )
            return response
        families = ["benchmark", "baseline_strengthening", "mechanism"]
        accepted = [
            item.model_copy(
                update={
                    "candidate": item.candidate.model_copy(
                        update={"variant_family": family}
                    )
                }
            )
            for item, family in zip(response.accepted, families, strict=True)
        ]
        return VarianceGenerationResponse(
            prompt=response.prompt,
            raw_response={
                "variants": [item.model_dump(mode="json") for item in accepted]
            },
            accepted=accepted,
            rejected=[],
        )


class _RejectedOpportunityGenerator(_OpportunityGenerator):
    def generate_for_pair(
        self, *, pair_payload, retrieval_payload, opportunities_per_pair
    ) -> OpportunityGenerationResponse:
        del pair_payload, retrieval_payload, opportunities_per_pair
        return OpportunityGenerationResponse(
            prompt_text="targeted rejected opportunity prompt",
            requested_output_schema=OpportunityProposalEnvelope.model_json_schema(),
            raw_response={"opportunities": [{"candidate": {}, "score": {}}]},
            accepted=[],
            rejected=[
                {
                    "index": 0,
                    "reasons": ["baseline_candidates field is required"],
                }
            ],
        )


class _AcceptNegativeQuestioner:
    backend_name = "llm-openai-test-questioner"
    backend_kind = BackendKind.LLM_OPENAI
    model = "test-model"
    fallback_used = False
    fallback_disclosed = True

    def __init__(self) -> None:
        self.call_count = 0

    def review_evidence(
        self, *, prompt_id, questions_payload, context_payload
    ) -> AdaptiveQuestionerResponse:
        del context_payload
        self.call_count += 1
        answers = [
            AdaptiveQuestionAnswerProposal(
                question_id=item["question_id"],
                category=item["category"],
                status="pass",
                explanation="The recorded artifacts answer this bounded question.",
                evidence_artifact_ids=["evidence-result-1"],
                blocking=False,
            )
            for item in questions_payload
        ]
        accepted = AdaptiveQuestionerDecisionProposal(
            answers=answers,
            recommended_action="accept_negative_result",
            rationale="The implementation is trustworthy and the bounded hypothesis failed.",
            claim_disposition="negative_result",
        )
        return AdaptiveQuestionerResponse(
            prompt_text=f"adaptive test prompt {prompt_id}",
            requested_output_schema={"type": "object"},
            raw_response={"decisions": [accepted.model_dump(mode="json")]},
            accepted=accepted,
            rejection_reasons=[],
        )


class _StopNoProgressQuestioner(_AcceptNegativeQuestioner):
    def review_evidence(
        self, *, prompt_id, questions_payload, context_payload
    ) -> AdaptiveQuestionerResponse:
        response = super().review_evidence(
            prompt_id=prompt_id,
            questions_payload=questions_payload,
            context_payload=context_payload,
        )
        assert response.accepted is not None
        accepted = response.accepted.model_copy(
            update={
                "recommended_action": "stop_no_progress",
                "rationale": "The bounded branch made no further progress.",
                "claim_disposition": "deferred",
            }
        )
        return AdaptiveQuestionerResponse(
            prompt_text=response.prompt_text,
            requested_output_schema=response.requested_output_schema,
            raw_response={"decisions": [accepted.model_dump(mode="json")]},
            accepted=accepted,
            rejection_reasons=response.rejection_reasons,
        )


class _RepairPlanQuestioner(_AcceptNegativeQuestioner):
    def review_evidence(
        self, *, prompt_id, questions_payload, context_payload
    ) -> AdaptiveQuestionerResponse:
        del context_payload
        self.call_count += 1
        answers = [
            AdaptiveQuestionAnswerProposal(
                question_id=item["question_id"],
                category=item["category"],
                status="fail",
                explanation="The declared evidence plan cannot identify the bounded contrast.",
                evidence_artifact_ids=["evidence-result-1"],
                blocking=True,
                recommended_fix_optional="Repair the evidence plan before rerunning.",
            )
            for item in questions_payload
        ]
        accepted = AdaptiveQuestionerDecisionProposal(
            answers=answers,
            recommended_action="repair_evidence_plan",
            rationale="The bounded evidence plan requires repair.",
            repair_instructions=["Repair the confounded factorial design."],
            unresolved_questions=["design identification"],
            claim_disposition="inconclusive",
        )
        return AdaptiveQuestionerResponse(
            prompt_text=f"adaptive plan-repair prompt {prompt_id}",
            requested_output_schema={"type": "object"},
            raw_response={"decisions": [accepted.model_dump(mode="json")]},
            accepted=accepted,
            rejection_reasons=[],
        )


def _brief() -> TargetedResearchBrief:
    return TargetedResearchBrief(
        brief_id="targeted-brief-test",
        title="Generic controlled robustness study",
        domain="probabilistic classification",
        method="calibration under corruption",
        central_question="When does corruption damage calibrated probabilities?",
        baseline_candidates=["uncalibrated predictor"],
        expected_metrics=["Brier score"],
        negative_controls=["zero corruption"],
        data_regime="Synthetic data with known ground truth.",
        known_risks=["The data generator may encode the expected answer."],
        allowed_claim_scope="Declared synthetic regimes only.",
        forbidden_claims=["real-world validation", "novelty proven"],
    )


def _persist_adaptive_negative_fixture(tmp_path: Path, run_id: str) -> None:
    plan_id = "adaptive-plan-1"
    package_id = "adaptive-package-1"
    code_id = "adaptive-code-1"
    execution_id = "adaptive-sandbox-1"
    output_path = f"runs/{run_id}/experiments/{execution_id}-output.json"
    plan = EvidenceArtifactPlan(
        artifact_plan_id=plan_id,
        artifact_type=EvidenceArtifactType.SYNTHETIC_EXPERIMENT,
        purpose="Test the bounded hypothesis under a declared synthetic DGP.",
        claim_component_supported="Finite-sample synthetic comparison.",
        input_contract={"seed": 17},
        output_contract={"required_metrics": ["effect_delta"]},
        baseline_or_comparator_plan=["fixed baseline"],
        control_plan_optional=["zero-signal control"],
        negative_control_plan_optional=["removed mechanism"],
        metric_plan_optional=["effect_delta"],
        execution_backend_required="local_execution",
        requires_code_generation=True,
        requires_local_execution=True,
        requires_retrieval=False,
        requires_symbolic_checker=False,
        requires_llm_drafting=False,
        allowed_evidence_labels=["SyntheticExperimentEvidence", "NegativeResult"],
        forbidden_claims=["real-world validation", "publication ready"],
        success_criteria=["effect_delta > 0"],
        failure_criteria=["effect_delta <= 0"],
    )
    package = HybridEvidencePackageCandidate(
        package_id=package_id,
        run_id=run_id,
        source_substrate_id="substrate-1",
        source_idea_node_id="idea-1",
        source_variant_id="variant-1",
        source_opportunity_id="opportunity-1",
        domain_id="domain-1",
        method_id="method-1",
        title="Bounded negative-result package",
        primary_claim_draft="The proposed method may not improve the declared metric.",
        allowed_claim_scope="Synthetic DGP only.",
        package_rationale="A controlled comparison can answer the bounded question.",
        artifact_plans=[plan],
        minimum_required_artifacts=[plan_id],
        artifact_dependency_graph={plan_id: []},
        claim_support_map={"bounded claim": [plan_id]},
        known_gaps=["No real-world evidence."],
        unresolved_obligations=["External validity remains unresolved."],
        recommended_next_action="Adjudicate the honest negative result.",
    )
    package_report = HybridEvidencePackageReport(
        run_id=run_id,
        report_id="hybrid-evidence-package-report-0001",
        planning_status="completed",
        config=HybridEvidencePackageConfig(
            run_id=run_id,
            backend="llm-openai",
            max_source_substrates=1,
            max_planning_calls=1,
            require_non_fake_backends=True,
        ),
        source_substrate_report_path="runs/source/substrate-report.json",
        selected_substrate_count=1,
        package_count=1,
        rejected_package_count=0,
        repaired_package_count=0,
        artifact_plan_count=1,
        artifact_type_coverage=1,
        artifact_type_counts={"synthetic_experiment": 1},
        packages=[package],
        production_ready=True,
    )
    code = LLMExperimentCodeArtifact(
        code_artifact_id=code_id,
        run_id=run_id,
        source_spec_id=plan_id,
        source_route_id="route-1",
        source_substrate_id="substrate-1",
        route_type=BranchRouteType.SYNTHETIC_EXPERIMENT,
        backend_kind=BackendKind.LLM_OPENAI,
        entrypoint="experiment.py",
        code=(
            "import json\n"
            "with open('output.json', 'w', encoding='utf-8') as handle:\n"
            "    json.dump({'metrics': {'effect_delta': -0.2}}, handle)\n"
        ),
        expected_output_files=["output.json"],
        random_seed=17,
        timeout_seconds=30,
        filesystem_scope="sandbox_workdir_only",
    )
    audit = ExperimentCodeSafetyAudit(
        code_artifact_id=code_id,
        passed=True,
        blocked=False,
        allowed_imports=["json"],
    )
    sandbox_config = SandboxExecutionConfig(
        entrypoint="experiment.py",
        output_json_filename="output.json",
        timeout_seconds=30,
        memory_limit_mb=256,
        seed=17,
    )
    sandbox = SandboxExecutionResult(
        execution_id=execution_id,
        code_artifact_id=code_id,
        status="completed",
        exit_code=0,
        stdout_path=f"runs/{run_id}/logs/stdout.txt",
        stderr_path=f"runs/{run_id}/logs/stderr.txt",
        output_json_path=output_path,
        artifact_paths=[output_path],
        runtime_seconds=0.1,
        timeout=False,
        memory_limit_mb=256,
        seed=17,
    )
    extraction = MetricExtractionResult(
        execution_id=execution_id,
        metrics_extracted=True,
        metrics={"effect_delta": -0.2},
        metric_sources={"effect_delta": f"{output_path}#metrics.effect_delta"},
        schema_valid=True,
    )
    result = EvidencePackageExecutionResult(
        result_id="evidence-result-1",
        package_id=package_id,
        artifact_plan_id=plan_id,
        source_substrate_id="substrate-1",
        artifact_type=EvidenceArtifactType.SYNTHETIC_EXPERIMENT,
        execution_completed=True,
        supports_adjudication=True,
        status="negative_result",
        evidence_label="NegativeResult",
        scope_label="Synthetic DGP only.",
        metrics={"effect_delta": -0.2},
        metric_sources={"effect_delta": f"{output_path}#metrics.effect_delta"},
        baseline_summary="The fixed baseline was executed.",
        control_summary="The zero-signal control passed.",
        negative_control_summary="The removed-mechanism control passed.",
        success_criteria_satisfied=False,
        failure_criteria_satisfied=True,
        unresolved_obligations=["External validity remains unresolved."],
    )
    execution_report = EvidencePackageExecutionReport(
        run_id=run_id,
        report_id="evidence-package-execution-report-0001",
        execution_status="completed",
        source_package_report_path=(
            f"runs/{run_id}/reports/hybrid-evidence-package-report-0001.json"
        ),
        execution_profile="full",
        package_count=1,
        artifact_plan_count=1,
        selected_artifact_plan_count=1,
        selected_artifact_plan_ids=[plan_id],
        executable_artifact_count=1,
        symbolic_artifact_count=0,
        retrieval_artifact_count=0,
        deferred_artifact_count=0,
        code_artifact_count=1,
        safety_audit_count=1,
        blocked_code_count=0,
        executed_code_count=1,
        failed_execution_count=0,
        metric_extraction_count=1,
        result_count=1,
        required_artifact_plan_count=1,
        completed_required_artifact_count=1,
        adjudication_ready_package_ids=[package_id],
        adjudication_ready=True,
        evidence_label_counts={"NegativeResult": 1},
        artifact_type_counts={"synthetic_experiment": 1},
        code_artifacts=[code],
        safety_audits=[audit],
        sandbox_configs=[sandbox_config],
        sandbox_executions=[sandbox],
        metric_extractions=[extraction],
        results=[result],
        production_ready=True,
    )
    store = ArtifactStore(tmp_path)
    store.write_json(
        run_id=run_id,
        artifact_id=package_report.report_id,
        artifact_type=ArtifactType.REPORT,
        data=package_report,
    )
    store.write_json(
        run_id=run_id,
        artifact_id=execution_report.report_id,
        artifact_type=ArtifactType.REPORT,
        data=execution_report,
    )


def test_targeted_brief_and_config_schemas_validate() -> None:
    brief = _brief()
    config = TargetedStudyConfig(
        run_id="targeted-test",
        mode="preflight",
        brief_path_optional="brief.json",
        model="test-model",
        max_total_calls=0,
        max_cost_usd=0,
    )

    assert brief.publication_ready is False
    assert config.mode == "preflight"
    authorized_retry = config.model_copy(
        update={"experiment_timeout_seconds": 900}
    )
    assert TargetedStudyConfig.model_validate(
        authorized_retry.model_dump(mode="json")
    ).experiment_timeout_seconds == 900
    assert (
        SandboxExecutionConfig(
            entrypoint="experiment.py",
            output_json_filename="output.json",
            timeout_seconds=900,
            memory_limit_mb=1024,
            seed=17,
        ).timeout_seconds
        == 900
    )


def test_targeted_final_pdf_rendering_requires_explicit_executable() -> None:
    with pytest.raises(ValueError, match="explicit latex_executable"):
        TargetedStudyConfig(
            run_id="targeted-pdf",
            mode="full",
            brief_path_optional="brief.json",
            model="test-model",
            allow_external_calls=True,
            require_non_fake_backends=True,
            max_total_calls=30,
            max_cost_usd=1.0,
            render_final_pdf=True,
        )

    config = TargetedStudyConfig(
        run_id="targeted-pdf",
        mode="full",
        brief_path_optional="brief.json",
        model="test-model",
        allow_external_calls=True,
        require_non_fake_backends=True,
        max_total_calls=30,
        max_cost_usd=1.0,
        render_final_pdf=True,
        latex_executable="pdflatex",
    )
    assert config.render_final_pdf is True


def test_adaptive_questioner_iteration_ceiling_supports_post_repair_review() -> None:
    config = AdaptiveEvidenceLoopConfig(
        max_questioner_iterations=64,
        max_code_repair_calls=32,
        max_plan_repair_calls=8,
        no_progress_limit=8,
    )

    assert config.max_questioner_iterations == 64
    assert config.max_code_repair_calls == 32
    assert config.max_plan_repair_calls == 8
    assert config.no_progress_limit == 8
    with pytest.raises(ValueError):
        AdaptiveEvidenceLoopConfig(max_questioner_iterations=65)


def test_adaptive_questioner_prompt_explains_logical_artifact_contract() -> None:
    prompt, _ = build_adaptive_questioner_prompt(
        prompt_id="logical-artifact-contract",
        questions_payload=[{"question_id": "evidence-sufficiency"}],
        context_payload={},
    )

    assert "output.json.logical_artifacts" in prompt
    assert "do not require separate physical files" in prompt


def test_adaptive_questioner_context_bounds_large_result_payloads() -> None:
    result = EvidencePackageExecutionResult(
        result_id="result-1",
        package_id="package-1",
        artifact_plan_id="plan-1",
        source_substrate_id="substrate-1",
        artifact_type=EvidenceArtifactType.NEGATIVE_CONTROL,
        execution_completed=True,
        status="inconclusive",
        evidence_label="InconclusiveResult",
        scope_label="synthetic only",
        metrics={f"metric-{index:04d}": float(index) for index in range(2_000)},
        metric_sources={f"metric-{index:04d}": "x" * 500 for index in range(2_000)},
        baseline_summary="b" * 100_000,
        control_summary="c" * 100_000,
        negative_control_summary="n" * 100_000,
    )

    summary = adaptive_evidence_module._questioner_result_summary(result)

    assert summary["metrics"]["omitted_count"] > 0
    assert len(summary["metric_source_examples"]) == 8
    assert len(summary["baseline_summary"]) == 8_000
    assert len(summary["control_summary"]) == 8_000
    assert len(summary["negative_control_summary"]) == 8_000


def test_targeted_hybrid_planning_receives_immutable_scope_and_workload() -> None:
    class Delegate:
        backend_name = "llm-openai-test"
        backend_kind = BackendKind.LLM_OPENAI
        model = "test-model"
        fallback_used = False
        fallback_disclosed = True

        def __init__(self) -> None:
            self.substrate_payload = {}

        def plan_package(self, **kwargs):
            self.substrate_payload = kwargs["substrate_payload"]

            class Response:
                accepted = None
                rejection_reasons = []
                repair_actions = []

            return Response()

    config = TargetedStudyConfig(
        run_id="targeted-contract",
        mode="full",
        brief_path_optional="brief.json",
        model="test-model",
        allow_external_calls=True,
        require_non_fake_backends=True,
        max_total_calls=27,
        max_cost_usd=1.0,
        max_replications=20,
        max_resamples=50,
        max_grid_cells=6,
    )
    delegate = Delegate()
    planner = _TargetedHybridPlanner(
        delegate=delegate,
        contract=_targeted_research_contract(_brief(), config),
    )

    planner.plan_package(
        prompt_id="targeted-contract-prompt",
        substrate_payload={"substrate_id": "substrate-1"},
        route_payload=None,
        retrieval_context_payload=None,
    )

    contract = delegate.substrate_payload["targeted_research_contract"]
    assert contract["immutable"] is True
    assert contract["central_question"] == _brief().central_question
    assert contract["authorized_execution_limits"]["max_replications"] == 20


def test_targeted_workload_rejects_plan_above_authorized_caps() -> None:
    class Accepted:
        def model_dump(self, *, mode):
            assert mode == "json"
            return {
                "package": {
                    "artifact_plans": [
                        {
                            "input_contract": {
                                "parameters": [
                                    {
                                        "name": "minimum_independent_seeds_per_cell",
                                        "value": "at least 100",
                                    }
                                ]
                            }
                        }
                    ]
                }
            }

    violations = _targeted_workload_violations(
        Accepted(),
        limits={
            "max_replications": 20,
            "max_resamples": 50,
            "max_grid_cells": 6,
        },
    )

    assert violations == [
        "targeted workload parameter minimum_independent_seeds_per_cell='at least 100' "
        "exceeds max_replications=20"
    ]


@pytest.mark.parametrize(
    "parameter_name,parameter_value",
    [
        ("max_replications_per_cell", "20"),
        ("primary_repetitions_per_cell", "20 maximum and exactly 20 planned"),
        ("train_sample_size_per_cell", "5000"),
    ],
)
def test_targeted_workload_does_not_treat_repetitions_per_cell_as_grid_cells(
    parameter_name: str,
    parameter_value: str,
) -> None:
    class Accepted:
        def model_dump(self, *, mode):
            assert mode == "json"
            return {
                "package": {
                    "artifact_plans": [
                        {
                            "input_contract": {
                                "parameters": [
                                    {
                                        "name": parameter_name,
                                        "value": parameter_value,
                                    }
                                ]
                            }
                        }
                    ]
                }
            }

    violations = _targeted_workload_violations(
        Accepted(),
        limits={
            "max_replications": 20,
            "max_resamples": 50,
            "max_grid_cells": 6,
        },
    )

    assert violations == []


@pytest.mark.parametrize("parameter_name", ["max_grid_cells", "scenario_count"])
def test_targeted_workload_still_enforces_grid_cell_counts(
    parameter_name: str,
) -> None:
    class Accepted:
        def model_dump(self, *, mode):
            assert mode == "json"
            return {
                "package": {
                    "artifact_plans": [
                        {
                            "input_contract": {
                                "parameters": [
                                    {"name": parameter_name, "value": "12"}
                                ]
                            }
                        }
                    ]
                }
            }

    violations = _targeted_workload_violations(
        Accepted(),
        limits={
            "max_replications": 20,
            "max_resamples": 50,
            "max_grid_cells": 6,
        },
    )

    assert violations == [
        f"targeted workload parameter {parameter_name}='12' exceeds max_grid_cells=6"
    ]


def test_targeted_completion_maps_blockers_to_persisted_report(tmp_path: Path) -> None:
    run_id = "targeted-completion"
    config = TargetedStudyConfig(
        run_id=run_id,
        mode="smoke",
        brief_path_optional="brief.json",
        model="test-model",
        allow_external_calls=True,
        require_non_fake_backends=True,
        max_total_calls=8,
        max_cost_usd=1.0,
    )
    result = _complete_targeted_run(
        config=config,
        brief=_brief(),
        records=[],
        checkpoints=[],
        status="completed",
        blockers=[],
        completed_calls=8,
        root=tmp_path,
        store=ArtifactStore(tmp_path),
        ledger=ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite"),
    )

    assert result.report.status == "completed"
    assert result.report.blocking_reasons == []
    assert result.report.publication_ready is False


def test_targeted_paper_tail_recognizes_deferred_artifact_report() -> None:
    reason = _stage_result_deferred_reason(
        "manuscript_planning",
        SimpleNamespace(
            manuscript_status="manuscript_deferred",
            blocking_reasons=["required evidence artifact is missing"],
        ),
    )

    assert reason == (
        "manuscript_planning deferred: required evidence artifact is missing"
    )
    assert (
        _stage_result_deferred_reason(
            "manuscript_planning",
            SimpleNamespace(manuscript_status="planned"),
        )
        is None
    )


def test_targeted_resume_reopens_deferred_manuscript_tail(tmp_path: Path) -> None:
    reports = tmp_path / "runs" / "targeted-resume-tail" / "reports"
    reports.mkdir(parents=True)
    (reports / "nucleus-manuscript-synthesis-report-0001.json").write_text(
        '{"manuscript_status":"manuscript_deferred"}',
        encoding="utf-8",
    )
    completed = {
        "adaptive_evidence_loop",
        "scientific_critic_ensemble",
        "cross_package_adjudication",
        "manuscript_planning",
        "manuscript_synthesis",
        "manuscript_revision",
        "final_paper_assembly",
        "final_paper_verification",
        "final_paper_bundle",
        "production_mode_check",
    }

    _reopen_deferred_paper_tail(completed, reports)

    assert "cross_package_adjudication" in completed
    assert "manuscript_planning" not in completed
    assert "final_paper_bundle" not in completed
    assert "production_mode_check" not in completed


def test_targeted_resume_reopens_only_stages_after_valid_synthesis(tmp_path: Path) -> None:
    reports = tmp_path / "runs" / "targeted-resume-synthesis" / "reports"
    reports.mkdir(parents=True)
    (reports / "nucleus-manuscript-synthesis-report-0001.json").write_text(
        '{"phase":"synthesis",'
        '"manuscript_status":"scientific_draft_with_open_obligations"}',
        encoding="utf-8",
    )
    completed = set(_PAPER_TAIL_STAGES) | {"production_mode_check"}

    _reopen_deferred_paper_tail(completed, reports)

    assert "manuscript_planning" in completed
    assert "manuscript_synthesis" in completed
    assert "manuscript_revision" not in completed
    assert "final_paper_bundle" not in completed
    assert "production_mode_check" not in completed


def test_targeted_resume_allows_only_budget_resource_and_adaptive_limit_increases(
    tmp_path: Path,
) -> None:
    run_id = "targeted-resume-budget"
    prior = TargetedStudyConfig(
        run_id=run_id,
        mode="full",
        brief_path_optional="brief.json",
        model="test-model",
        allow_external_calls=True,
        require_non_fake_backends=True,
        max_total_calls=27,
        max_cost_usd=1.0,
    )
    _complete_targeted_run(
        config=prior,
        brief=_brief(),
        records=[],
        checkpoints=[],
        status="deferred",
        blockers=["budget exhausted"],
        completed_calls=7,
        root=tmp_path,
        store=ArtifactStore(tmp_path),
        ledger=ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite"),
    )
    reports = tmp_path / "runs" / run_id / "reports"
    expanded = prior.model_copy(
        update={
            "resume": True,
            "max_total_calls": 30,
            "max_cost_usd": 2.0,
            "experiment_timeout_seconds": 900,
            "experiment_memory_limit_mb": 2048,
            "adaptive_evidence": prior.adaptive_evidence.model_copy(
                update={"max_questioner_iterations": 4}
            ),
        }
    )
    changed_model = expanded.model_copy(update={"model": "different-model"})
    higher_reasoning = expanded.model_copy(update={"reasoning_effort": "high"})
    higher_llm_timeout = expanded.model_copy(update={"llm_timeout_seconds": 300.0})
    reduced = expanded.model_copy(update={"max_total_calls": 26})
    reduced_timeout = expanded.model_copy(update={"experiment_timeout_seconds": 299})

    assert _resume_config_matches(
        "different-checkpoint-hash",
        config=expanded,
        current_hash=_model_hash(expanded.model_copy(update={"resume": False})),
        reports=reports,
    )
    assert _resume_config_matches(
        "different-checkpoint-hash",
        config=higher_reasoning,
        current_hash=_model_hash(
            higher_reasoning.model_copy(update={"resume": False})
        ),
        reports=reports,
    )
    assert _resume_config_matches(
        "different-checkpoint-hash",
        config=higher_llm_timeout,
        current_hash=_model_hash(
            higher_llm_timeout.model_copy(update={"resume": False})
        ),
        reports=reports,
    )
    assert not _resume_config_matches(
        "different-checkpoint-hash",
        config=changed_model,
        current_hash=_model_hash(changed_model.model_copy(update={"resume": False})),
        reports=reports,
    )
    assert not _resume_config_matches(
        "different-checkpoint-hash",
        config=reduced,
        current_hash=_model_hash(reduced.model_copy(update={"resume": False})),
        reports=reports,
    )
    assert not _resume_config_matches(
        "different-checkpoint-hash",
        config=reduced_timeout,
        current_hash=_model_hash(
            reduced_timeout.model_copy(update={"resume": False})
        ),
        reports=reports,
    )


def test_targeted_cli_commands_are_registered_and_preflight_is_read_only(
    tmp_path: Path,
) -> None:
    brief_path = tmp_path / "brief.json"
    brief_path.write_text(_brief().model_dump_json(indent=2), encoding="utf-8")
    runner = CliRunner()

    help_result = runner.invoke(app, ["run-targeted-study", "--help"])
    validation = runner.invoke(
        app,
        ["validate-targeted-research-brief", "--path", str(brief_path)],
    )
    preflight = runner.invoke(
        app,
        [
            "run-targeted-study",
            "--run-id",
            "cli-targeted-preflight",
            "--brief",
            str(brief_path),
            "--mode",
            "preflight",
            "--root",
            str(tmp_path),
        ],
    )

    assert help_result.exit_code == 0
    assert "--max-total-calls" in help_result.output
    assert "--max-cost-usd" in help_result.output
    assert "--max-questioner-iterations" in help_result.output
    assert "--max-code-repair-calls" in help_result.output
    assert "--max-plan-repair-calls" in help_result.output
    assert "--reasoning-effort" in help_result.output
    assert "--llm-timeout-seconds" in help_result.output
    assert "--max-replications" in help_result.output
    assert validation.exit_code == 0
    assert "valid=true" in validation.output
    assert preflight.exit_code == 0
    assert "status=preflight_ready" in preflight.output
    assert not (tmp_path / "runs" / "cli-targeted-preflight").exists()


def test_full_targeted_orchestration_reaches_final_paper_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "targeted-full-paper-tail"
    brief_path = tmp_path / "brief.json"
    brief_path.write_text(_brief().model_dump_json(indent=2), encoding="utf-8")
    calls: list[str] = []
    execution_options: dict[str, object] = {}

    def no_op(name: str):
        def operation(**_kwargs):
            calls.append(name)
            return SimpleNamespace()

        return operation

    def persist_evidence_plan(**_kwargs):
        calls.append("hybrid_evidence_planning")
        _persist_adaptive_negative_fixture(tmp_path, run_id)
        return SimpleNamespace()

    def execute_evidence(**kwargs):
        calls.append("hybrid_evidence_execution")
        execution_options.update(kwargs)
        return SimpleNamespace()

    def final_paper_assembly(**_kwargs):
        calls.append("final_paper_assembly")
        paper_dir = tmp_path / "runs" / run_id / "paper"
        paper_dir.mkdir(parents=True, exist_ok=True)
        markdown = paper_dir / "final-paper-0001.md"
        latex = paper_dir / "final-paper-0001.tex"
        manifest = paper_dir / "final-paper-manifest-0001.json"
        markdown.write_text("# Bounded negative result\n", encoding="utf-8")
        latex.write_text("\\documentclass{article}\n", encoding="utf-8")
        manifest.write_text('{"publication_ready": false}\n', encoding="utf-8")
        return SimpleNamespace(
            report_artifact=SimpleNamespace(
                path=f"runs/{run_id}/paper/final-paper-manifest-0001.json"
            ),
            markdown_artifact=SimpleNamespace(
                path=f"runs/{run_id}/paper/final-paper-0001.md"
            ),
        )

    def final_paper_verification(**_kwargs):
        calls.append("final_paper_verification")
        path = tmp_path / "runs" / run_id / "paper" / "final-paper-verification-0001.json"
        path.write_text('{"status": "verified_with_warnings"}\n', encoding="utf-8")
        return SimpleNamespace(
            report_artifact=SimpleNamespace(
                path=f"runs/{run_id}/paper/final-paper-verification-0001.json"
            )
        )

    def final_paper_bundle(**_kwargs):
        calls.append("final_paper_bundle")
        path = tmp_path / "runs" / run_id / "paper" / "final-paper-bundle-0001.json"
        path.write_text('{"publication_ready": false}\n', encoding="utf-8")
        return SimpleNamespace(
            report_artifact=SimpleNamespace(
                path=f"runs/{run_id}/paper/final-paper-bundle-0001.json"
            )
        )

    monkeypatch.setattr(
        targeted_study_module,
        "discover_deep_opportunities",
        no_op("deep_opportunity_discovery"),
    )
    monkeypatch.setattr(
        targeted_study_module,
        "generate_llm_variance",
        no_op("llm_variance_generation"),
    )
    monkeypatch.setattr(
        targeted_study_module,
        "construct_idea_tree_from_llm_variance",
        no_op("idea_tree_construction"),
    )
    monkeypatch.setattr(
        targeted_study_module,
        "construct_llm_substrates",
        no_op("llm_substrate_construction"),
    )
    monkeypatch.setattr(
        targeted_study_module,
        "plan_llm_routes",
        no_op("llm_route_planning"),
    )
    monkeypatch.setattr(
        targeted_study_module,
        "plan_hybrid_evidence_packages",
        persist_evidence_plan,
    )
    monkeypatch.setattr(
        targeted_study_module,
        "execute_hybrid_evidence_packages",
        execute_evidence,
    )
    monkeypatch.setattr(
        targeted_study_module,
        "critique_evidence_packages",
        no_op("scientific_critic_ensemble"),
    )
    monkeypatch.setattr(
        targeted_study_module,
        "adjudicate_evidence_packages",
        no_op("cross_package_adjudication"),
    )
    monkeypatch.setattr(
        targeted_study_module,
        "plan_nucleus_manuscript",
        no_op("manuscript_planning"),
    )
    monkeypatch.setattr(
        targeted_study_module,
        "synthesize_nucleus_manuscript",
        no_op("manuscript_synthesis"),
    )
    monkeypatch.setattr(
        targeted_study_module,
        "revise_nucleus_manuscript",
        no_op("manuscript_revision"),
    )
    monkeypatch.setattr(
        targeted_study_module,
        "assemble_final_paper",
        final_paper_assembly,
    )
    monkeypatch.setattr(
        targeted_study_module,
        "verify_final_paper",
        final_paper_verification,
    )
    monkeypatch.setattr(
        targeted_study_module,
        "build_final_paper_bundle",
        final_paper_bundle,
    )
    monkeypatch.setattr(
        targeted_study_module,
        "check_production_mode",
        no_op("production_mode_check"),
    )

    class NonFakeClient:
        backend_name = "llm-openai-test"
        backend_kind = BackendKind.LLM_OPENAI
        model = "test-model"
        fallback_used = False
        fallback_disclosed = True

    client = NonFakeClient()
    result = run_targeted_study(
        config=TargetedStudyConfig(
            run_id=run_id,
            mode="full",
            brief_path_optional=brief_path.as_posix(),
            model="test-model",
            allow_external_calls=True,
            require_non_fake_backends=True,
            max_total_calls=35,
            max_cost_usd=1.3,
        ),
        root=tmp_path,
        clients=TargetedStudyClients(
            opportunity_generator=client,
            retriever=client,
            variance_generator=client,
            substrate_generator=client,
            route_planner=client,
            hybrid_planner=client,
            code_generator=client,
            adaptive_questioner=_AcceptNegativeQuestioner(),
            scientific_critic=client,
            manuscript_client=client,
        ),
    )

    assert result.report.status == "completed"
    assert result.report.production_ready is True
    assert result.report.publication_ready is False
    assert execution_options["max_runtime_repair_calls"] == 1
    assert calls[-9:] == [
        "scientific_critic_ensemble",
        "cross_package_adjudication",
        "manuscript_planning",
        "manuscript_synthesis",
        "manuscript_revision",
        "final_paper_assembly",
        "final_paper_verification",
        "final_paper_bundle",
        "production_mode_check",
    ]
    paper_dir = tmp_path / "runs" / run_id / "paper"
    assert (paper_dir / "final-paper-0001.md").is_file()
    assert (paper_dir / "final-paper-0001.tex").is_file()
    assert (paper_dir / "final-paper-manifest-0001.json").is_file()
    assert (paper_dir / "final-paper-verification-0001.json").is_file()
    assert (paper_dir / "final-paper-bundle-0001.json").is_file()


def test_candidate_preflight_is_read_only_and_generic(tmp_path: Path) -> None:
    source_run = "source-stage-a"
    target_run = "targeted-preflight"
    candidate = Candidate(
        id="candidate-001",
        domain="classification",
        method="calibration",
        question="Does corruption damage probability reliability?",
        experiment="Use controlled synthetic data.",
        baseline="uncalibrated logistic regression",
        data_requirement=DataRequirement.SYNTHETIC_ONLY,
        symbolic_state={
            "title": "Controlled calibration study",
            "adapter_backend": "openai",
            "llm_proposed": True,
        },
    )
    ArtifactStore(tmp_path).write_json(
        run_id=source_run,
        artifact_id=candidate.id,
        artifact_type=ArtifactType.CANDIDATE,
        data=candidate,
    )
    config = TargetedStudyConfig(
        run_id=target_run,
        mode="preflight",
        source_run_id_optional=source_run,
        candidate_id_optional=candidate.id,
        model="test-model",
        max_total_calls=0,
        max_cost_usd=0,
    )

    report = preflight_targeted_study(config=config, root=tmp_path)

    assert report.status == "preflight_ready"
    assert report.brief.central_question == candidate.question
    assert report.brief.authoring_backend_kind == BackendKind.LLM_OPENAI
    assert report.brief.selection_backend_kind == BackendKind.HUMAN
    assert report.planned_external_call_count == 6
    assert not (tmp_path / "runs" / target_run).exists()


def test_full_targeted_preflight_separates_minimum_budget_from_upper_bound(
    tmp_path: Path,
) -> None:
    brief_path = tmp_path / "brief.json"
    brief_path.write_text(_brief().model_dump_json(indent=2), encoding="utf-8")
    config = TargetedStudyConfig(
        run_id="targeted-full-preflight",
        mode="full",
        brief_path_optional=brief_path.as_posix(),
        model="test-model",
        allow_external_calls=True,
        require_non_fake_backends=True,
        max_total_calls=35,
        max_cost_usd=1.3,
    )

    report = preflight_targeted_study(config=config, root=tmp_path)

    assert report.status == "preflight_ready"
    assert report.minimum_required_external_call_count == 35
    assert report.planned_external_call_count == 55
    assert report.minimum_estimated_cost_usd <= config.max_cost_usd
    assert report.estimated_cost_usd > config.max_cost_usd
    assert not (tmp_path / "runs" / config.run_id).exists()


def test_targeted_m98_requires_no_atlas_and_repairs_variance_coverage(
    tmp_path: Path,
) -> None:
    run_id = "targeted-m98"
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    brief = _brief()
    brief_ref = store.write_json(
        run_id=run_id,
        artifact_id=brief.brief_id,
        artifact_type=ArtifactType.REPORT,
        data=brief,
    )

    result = discover_deep_opportunities(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=_OpportunityGenerator(),
        retriever=MockedOpportunityRetriever(),
        config=DeepOpportunityDiscoveryConfig(
            run_id=run_id,
            backend="llm-openai",
            retrieval_mode="mocked_retrieval",
            source_mode="targeted_brief",
            targeted_brief_path_optional=brief_ref.path,
            max_pairs=1,
            max_generation_calls=1,
            opportunities_per_pair=2,
            max_selected_opportunities=1,
            min_domain_family_coverage=1,
            min_method_family_coverage=1,
            max_opportunities_per_domain_family=1,
            max_opportunities_per_method_family=1,
        ),
    )

    assert not list((tmp_path / "runs" / run_id / "reports").glob("atlas-scan-*.json"))
    assert result.report.source_context_kind == "targeted_brief"
    assert result.report.source_atlas_scan_path is None
    assert result.report.source_targeted_brief_path_optional == brief_ref.path
    assert len(result.report.source_pairs) == 1
    assert result.report.selected_pair_count == 1
    assert result.report.selected_opportunity_count == 1

    variance_generator = _CoverageRepairVarianceGenerator()
    variance = generate_llm_variance(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=variance_generator,
        config=LLMVarianceGenerationConfig(
            run_id=run_id,
            backend="llm-openai",
            max_source_opportunities=1,
            variants_per_opportunity=3,
            max_variants_total=3,
            max_selected_variants=3,
            max_generation_calls=2,
            min_variant_family_coverage=2,
            min_domain_family_coverage=1,
            min_method_family_coverage=1,
        ),
    )
    assert variance.report.generated_variant_count == 3
    assert variance.report.selected_variant_count == 3
    assert len(variance.report.raw_artifact_paths) == 2
    assert variance_generator.call_count == 2
    assert variance_generator.repair_payload is not None
    assert any("family-coverage repair" in item for item in variance.report.warnings)


def test_targeted_m98_compacts_oversized_retrieval_query() -> None:
    brief = _brief().model_copy(
        update={
            "domain": "Synthetic probabilistic binary classification " * 20,
            "method": (
                "Evaluate logistic regression under symmetric and class conditional "
                "training label noise with temperature scaling and beta calibration "
            )
            * 20,
        }
    )
    _, domains, methods = _targeted_source_metadata(brief)

    query = _retrieval_query(domain=domains[0], method=methods[0])

    assert len(query) <= 1000
    assert "logistic" in query
    assert "noise" in query
    assert query.endswith("baseline")


def test_targeted_openalex_retrieval_recovers_with_bounded_query_variants() -> None:
    pairs, domains, methods = _targeted_source_metadata(_brief())
    relevant = SimpleNamespace(
        source_id="W100",
        title="Label noise and probability calibration for logistic regression",
        authors=["A. Researcher"],
        year=2022,
        venue="Calibration Journal",
        abstract="Symmetric label noise changes calibrated probability estimates.",
        snippet=None,
        doi="10.1000/calibration.1",
        score=0.92,
        provider="openalex",
    )
    duplicate = SimpleNamespace(**{**vars(relevant), "source_id": "W101"})
    second = SimpleNamespace(
        source_id="W200",
        title="Class conditional label noise in probabilistic classification",
        authors=["B. Scientist"],
        year=2021,
        venue="Machine Learning Review",
        abstract="Calibration and logistic regression are evaluated under noisy labels.",
        snippet=None,
        doi="10.1000/calibration.2",
        score=0.84,
        provider="openalex",
    )
    irrelevant = SimpleNamespace(
        source_id="W300",
        title="Temperature observations in coastal ecosystems",
        authors=["C. Observer"],
        year=2020,
        venue="Marine Science",
        abstract="A longitudinal study of ocean temperature.",
        snippet=None,
        doi="10.1000/marine.1",
        score=0.99,
        provider="openalex",
    )

    class QueryClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, int]] = []

        def search(self, query: str, limit: int):
            self.calls.append((query, limit))
            return {
                1: [relevant, irrelevant],
                2: [duplicate, second],
            }.get(len(self.calls), [])

    client = QueryClient()
    retriever = OpenAlexOpportunityRetriever(client=client)  # type: ignore[arg-type]
    context = retriever.retrieve(
        run_id="retrieval-fallback",
        context_id="retrieval-context-0001",
        pair=pairs[0],
        domain=domains[0],
        method=methods[0],
        limit=3,
    )

    assert len(client.calls) == len(_retrieval_queries(domain=domains[0], method=methods[0]))
    assert len(client.calls) >= 3
    assert {source.doi for source in context.sources} == {
        "10.1000/calibration.1",
        "10.1000/calibration.2",
    }
    assert all(source.relevance_score and source.relevance_score > 0 for source in context.sources)
    assert "10.1000/marine.1" not in {source.doi for source in context.sources}
    assert " | " in context.query
    assert any("query variants" in item for item in context.limitations)


def test_targeted_m98_persists_all_rejected_response_before_failing(
    tmp_path: Path,
) -> None:
    run_id = "targeted-m98-rejected"
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    brief_ref = store.write_json(
        run_id=run_id,
        artifact_id=_brief().brief_id,
        artifact_type=ArtifactType.REPORT,
        data=_brief(),
    )

    with pytest.raises(
        DeepOpportunityDiscoveryError,
        match="baseline_candidates field is required",
    ):
        discover_deep_opportunities(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            generator=_RejectedOpportunityGenerator(),
            retriever=MockedOpportunityRetriever(),
            config=DeepOpportunityDiscoveryConfig(
                run_id=run_id,
                backend="llm-openai",
                retrieval_mode="mocked_retrieval",
                source_mode="targeted_brief",
                targeted_brief_path_optional=brief_ref.path,
                max_pairs=1,
                max_generation_calls=1,
                opportunities_per_pair=2,
                max_selected_opportunities=1,
                min_domain_family_coverage=1,
                min_method_family_coverage=1,
                max_opportunities_per_domain_family=1,
                max_opportunities_per_method_family=1,
            ),
        )

    inspected = inspect_deep_opportunities(run_id=run_id, root=tmp_path)
    assert inspected.discovery_status_optional == "failed"
    assert inspected.rejected_opportunity_count == 1
    reports = tmp_path / "runs" / run_id / "reports"
    raw_path = next(
        path
        for path in reports.glob("llm-deep-opportunity-raw-*.json")
        if not path.name.endswith(".meta.json")
    )
    assert "baseline_candidates field is required" in raw_path.read_text(encoding="utf-8")


def test_adaptive_evidence_loop_accepts_a_trustworthy_negative_result(
    tmp_path: Path,
) -> None:
    run_id = "adaptive-negative-result"
    _persist_adaptive_negative_fixture(tmp_path, run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    targeted_config = TargetedStudyConfig(
        run_id=run_id,
        mode="full",
        brief_path_optional="unused-brief.json",
        model="test-model",
        allow_external_calls=True,
        require_non_fake_backends=True,
        max_total_calls=30,
        max_cost_usd=10.0,
    )
    budget = TargetedLLMBudgetManager(
        config=targeted_config,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    delegate = _AcceptNegativeQuestioner()
    questioner = budget.wrap_client(
        delegate,
        {"review_evidence": "llm-stage-b-review"},
    )

    result = run_adaptive_evidence_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        brief=_brief().model_copy(update={"brief_id": "adaptive-brief"}),
        questioner=questioner,
        planner=None,
        plan_repairer=None,
        code_generator=None,
        code_repairer=None,
        budget=budget,
        config=AdaptiveEvidenceLoopConfig(),
        retrieval_mode="real_retrieval",
        require_non_fake_backends=True,
    )

    assert delegate.call_count == 1
    assert result.report.status == "satisfied_negative"
    assert result.report.accepted_result_ids == ["evidence-result-1"]
    assert result.report.code_repair_attempt_count == 0
    assert result.report.plan_repair_attempt_count == 0
    assert result.report.budget_usage.total_calls == 1
    assert len(result.report.call_accounting_paths) == 1
    assert (tmp_path / result.report.call_accounting_paths[0]).is_file()
    assert result.report.production_ready is True
    assert result.report.publication_ready is False
    records, _, _ = collect_backend_records(run_id=run_id, root=tmp_path)
    assert any(
        item.stage_id.endswith("adaptive-questioner")
        and item.backend_kind == BackendKind.LLM_OPENAI
        for item in records
    )


def test_adaptive_diagnostics_surface_invalid_required_metrics(
    tmp_path: Path,
) -> None:
    run_id = "adaptive-invalid-metric"
    _persist_adaptive_negative_fixture(tmp_path, run_id)
    reports = tmp_path / "runs" / run_id / "reports"
    package = HybridEvidencePackageReport.model_validate_json(
        (reports / "hybrid-evidence-package-report-0001.json").read_text(
            encoding="utf-8"
        )
    )
    execution = EvidencePackageExecutionReport.model_validate_json(
        (reports / "evidence-package-execution-report-0001.json").read_text(
            encoding="utf-8"
        )
    )
    invalid_execution = execution.model_copy(
        update={
            "metric_extractions": [
                execution.metric_extractions[0].model_copy(
                    update={
                        "metrics": {},
                        "metric_sources": {},
                        "schema_valid": False,
                        "invalid_metrics": [
                            "permutation_reproducibility_diagnostics"
                        ],
                    }
                )
            ]
        }
    )

    diagnostics = adaptive_evidence_module._diagnose(package, invalid_execution)

    invalid = next(item for item in diagnostics if item.code == "invalid_required_metric")
    assert invalid.category == "implementation_fidelity"
    assert invalid.terminal_block is False
    assert "booleans are invalid" in invalid.message
    assert adaptive_evidence_module._diagnostic_fingerprint(
        package, execution
    ) != adaptive_evidence_module._diagnostic_fingerprint(package, invalid_execution)


def test_adaptive_scientific_repair_includes_latest_runtime_diagnostics(
    tmp_path: Path,
) -> None:
    run_id = "adaptive-runtime-diagnostics"
    _persist_adaptive_negative_fixture(tmp_path, run_id)
    reports = tmp_path / "runs" / run_id / "reports"
    execution = EvidencePackageExecutionReport.model_validate_json(
        (reports / "evidence-package-execution-report-0001.json").read_text(
            encoding="utf-8"
        )
    )
    stderr_path = tmp_path / "runs" / run_id / "logs" / "stderr.txt"
    stderr_path.parent.mkdir(parents=True, exist_ok=True)
    stderr_path.write_text("Traceback\nMemoryError\n", encoding="utf-8")
    failed = execution.sandbox_executions[0].model_copy(
        update={
            "status": "failed",
            "exit_code": 1,
            "failure_reason_optional": "generated experiment exited with code 1",
        }
    )

    diagnostic = adaptive_evidence_module._latest_runtime_diagnostics(
        execution.model_copy(update={"sandbox_executions": [failed]}),
        tmp_path,
    )

    assert diagnostic is not None
    assert diagnostic["execution_id"] == failed.execution_id
    assert diagnostic["stderr_tail"] == "Traceback\nMemoryError\n"


def test_adaptive_evidence_loop_resumes_only_after_limits_expand(
    tmp_path: Path,
) -> None:
    run_id = "adaptive-expanded-limits"
    _persist_adaptive_negative_fixture(tmp_path, run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    targeted_config = TargetedStudyConfig(
        run_id=run_id,
        mode="full",
        brief_path_optional="unused-brief.json",
        model="test-model",
        allow_external_calls=True,
        require_non_fake_backends=True,
        max_total_calls=30,
        max_cost_usd=10.0,
    )
    first_budget = TargetedLLMBudgetManager(
        config=targeted_config,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    stop_delegate = _StopNoProgressQuestioner()
    first = run_adaptive_evidence_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        brief=_brief().model_copy(update={"brief_id": "adaptive-resume-brief"}),
        questioner=first_budget.wrap_client(
            stop_delegate,
            {"review_evidence": "llm-stage-b-review"},
        ),
        planner=None,
        plan_repairer=None,
        code_generator=None,
        code_repairer=None,
        budget=first_budget,
        config=AdaptiveEvidenceLoopConfig(max_questioner_iterations=1),
        retrieval_mode="real_retrieval",
        require_non_fake_backends=True,
    )

    assert first.report.status == "stopped_no_progress"
    assert not adaptive_loop_can_resume(
        first.report,
        AdaptiveEvidenceLoopConfig(max_questioner_iterations=1),
    )
    expanded = AdaptiveEvidenceLoopConfig(
        max_questioner_iterations=2,
        no_progress_limit=2,
    )
    assert adaptive_loop_can_resume(first.report, expanded)

    code_limited = first.report.model_copy(
        update={
            "code_repair_attempt_count": 2,
            "terminal_reason": (
                "The bounded scientific code-repair allowance was exhausted."
            ),
            "iterations": [
                first.report.iterations[-1].model_copy(
                    update={
                        "decision": first.report.iterations[-1].decision.model_copy(
                            update={
                                "action": "repair_code",
                                "repair_instructions": ["Repair the runtime defect."],
                            }
                        )
                    }
                )
            ],
        }
    )
    assert adaptive_loop_can_resume(
        code_limited,
        AdaptiveEvidenceLoopConfig(
            max_questioner_iterations=2,
            max_code_repair_calls=3,
        ),
    )
    transport_blocked = code_limited.model_copy(
        update={
            "status": "blocked",
            "terminal_reason": (
                "Scientific code repair failed closed: Scientific code repair failed for "
                "artifact-1: Adapter transport failed; backend=openai; provider=openai; "
                "operation=responses.create; message=request failed before a valid response "
                "was received"
            ),
        }
    )
    assert adaptive_loop_can_resume(
        transport_blocked,
        AdaptiveEvidenceLoopConfig(
            max_questioner_iterations=2,
            max_code_repair_calls=2,
        ),
    )
    assert not _iteration_counts_for_no_progress(code_limited.iterations[-1])
    executed_repair = code_limited.iterations[-1].model_copy(
        update={
            "produced_execution_report_path_optional": (
                "runs/adaptive-expanded-limits/reports/"
                "evidence-package-execution-report-0002.json"
            )
        }
    )
    assert _iteration_counts_for_no_progress(executed_repair)
    assert (
        _premature_stop_repair_action(
            proposed_action="stop_no_progress",
            answers=[],
            diagnostics=[
                _Diagnostic(
                    code="execution_failure",
                    message="The generated experiment exited before output.json.",
                    category="implementation_fidelity",
                )
            ],
            has_code=True,
            iterations=code_limited.iterations,
            fingerprint=code_limited.iterations[-1].before_fingerprint,
            config=AdaptiveEvidenceLoopConfig(
                max_questioner_iterations=2,
                max_code_repair_calls=3,
                no_progress_limit=2,
            ),
            code_attempts=2,
            plan_attempts=0,
        )
        == "repair_code"
    )
    repairable_stop = code_limited.model_copy(
        update={
            "code_repair_attempt_count": 2,
            "iterations": [
                code_limited.iterations[-1].model_copy(
                    update={
                        "decision": code_limited.iterations[-1].decision.model_copy(
                            update={
                                "action": "stop_no_progress",
                                "questions": [
                                    code_limited.iterations[-1]
                                    .decision.questions[0]
                                    .model_copy(
                                        update={
                                            "category": "implementation_fidelity",
                                            "status": "fail",
                                            "blocking": True,
                                        }
                                    )
                                ],
                            }
                        )
                    }
                )
            ],
        }
    )
    remaining_repair = AdaptiveEvidenceLoopConfig(
        max_questioner_iterations=2,
        max_code_repair_calls=3,
    )
    assert adaptive_loop_can_resume(repairable_stop, remaining_repair)
    assert (
        _premature_stop_repair_action(
            proposed_action="stop_no_progress",
            answers=repairable_stop.iterations[-1].decision.questions,
            diagnostics=[],
            has_code=True,
            iterations=repairable_stop.iterations,
            fingerprint=repairable_stop.iterations[-1].before_fingerprint,
            config=remaining_repair,
            code_attempts=2,
            plan_attempts=0,
        )
        == "repair_code"
    )
    assert (
        _premature_stop_repair_action(
            proposed_action="stop_weak_branch",
            answers=repairable_stop.iterations[-1].decision.questions,
            diagnostics=[],
            has_code=True,
            iterations=repairable_stop.iterations,
            fingerprint=repairable_stop.iterations[-1].before_fingerprint,
            config=remaining_repair,
            code_attempts=2,
            plan_attempts=0,
        )
        == "repair_code"
    )
    assert (
        _premature_stop_repair_action(
            proposed_action="blocked",
            answers=repairable_stop.iterations[-1].decision.questions,
            diagnostics=[],
            has_code=True,
            iterations=repairable_stop.iterations,
            fingerprint=repairable_stop.iterations[-1].before_fingerprint,
            config=remaining_repair,
            code_attempts=2,
            plan_attempts=0,
        )
        == "repair_code"
    )
    override_instructions = _repair_instructions_for_override(
        repairable_stop.iterations[-1].decision.questions,
        [],
    )
    overridden_decision = repairable_stop.iterations[-1].decision.model_copy(
        update={
            "action": "repair_code",
            "repair_instructions": override_instructions,
        }
    )
    type(overridden_decision).model_validate(
        overridden_decision.model_dump(mode="json")
    )
    execution_path = (
        tmp_path
        / "runs"
        / run_id
        / "reports"
        / "evidence-package-execution-report-0001.json"
    )
    execution = EvidencePackageExecutionReport.model_validate_json(
        execution_path.read_text(encoding="utf-8")
    )
    assert adaptive_evidence_module._code_repair_target(execution) is None
    assert adaptive_evidence_module._code_repair_target(
        execution,
        include_completed=True,
    ) == execution.results[0].artifact_plan_id
    blocked_after_completed_execution = code_limited.model_copy(
        update={
            "status": "blocked",
            "terminal_reason": "No prior executable artifact was available for code repair.",
        }
    )
    assert adaptive_loop_can_resume(
        blocked_after_completed_execution,
        remaining_repair,
        latest_execution=execution,
    )
    stopped_before_new_execution = code_limited.model_copy(
        update={
            "status": "stopped_weak_branch",
            "iterations": [
                code_limited.iterations[-1].model_copy(
                    update={
                        "decision": code_limited.iterations[-1].decision.model_copy(
                            update={"action": "stop_weak_branch"}
                        )
                    }
                )
            ],
        }
    )
    newer_execution = execution.model_copy(
        update={
            "report_id": "evidence-package-execution-report-0002",
            "adjudication_ready": True,
        }
    )
    assert adaptive_loop_can_resume(
        stopped_before_new_execution,
        remaining_repair,
        latest_execution=newer_execution,
    )
    history_recovered = adaptive_evidence_module.recover_historical_code_artifacts(
        execution.model_copy(
            update={
                "code_artifacts": [],
                "incomplete_required_artifact_plan_ids": [
                    execution.results[0].artifact_plan_id
                ],
            }
        ),
        execution_path.parent,
    )
    assert history_recovered.code_artifacts == execution.code_artifacts
    assert history_recovered.safety_audits == execution.safety_audits
    assert history_recovered.sandbox_executions == execution.sandbox_executions
    assert adaptive_loop_can_resume(
        repairable_stop,
        remaining_repair,
        latest_execution=history_recovered,
    )
    incomplete_execution = execution.model_copy(
        update={
            "budget_deferred_artifact_count": 1,
            "incomplete_required_artifact_plan_ids": ["missing-negative-control"],
        }
    )
    assert adaptive_evidence_module._has_budget_deferred_required_artifact(
        incomplete_execution
    )
    plan_limited = code_limited.model_copy(
        update={
            "code_repair_attempt_count": 3,
            "plan_repair_attempt_count": 1,
            "iterations": [
                code_limited.iterations[-1].model_copy(
                    update={
                        "decision": code_limited.iterations[-1].decision.model_copy(
                            update={"action": "repair_evidence_plan"}
                        )
                    }
                )
            ],
        }
    )
    assert adaptive_loop_can_resume(
        plan_limited,
        AdaptiveEvidenceLoopConfig(
            max_questioner_iterations=2,
            max_code_repair_calls=4,
            max_plan_repair_calls=1,
        ),
        latest_execution=incomplete_execution,
    )
    weak_stop = plan_limited.model_copy(update={"status": "stopped_weak_branch"})
    assert adaptive_loop_can_resume(
        weak_stop,
        AdaptiveEvidenceLoopConfig(
            max_questioner_iterations=2,
            max_code_repair_calls=3,
            max_plan_repair_calls=1,
        ),
        latest_execution=incomplete_execution,
    )
    safety_blocked_execution = execution.model_copy(
        update={
            "results": [
                execution.results[0].model_copy(
                    update={
                        "status": "blocked_safety_audit",
                        "execution_completed": False,
                        "supports_adjudication": False,
                    }
                )
            ],
            "incomplete_required_artifact_plan_ids": [
                execution.results[0].artifact_plan_id
            ],
        }
    )
    assert adaptive_loop_can_resume(
        weak_stop,
        AdaptiveEvidenceLoopConfig(
            max_questioner_iterations=2,
            max_code_repair_calls=4,
            max_plan_repair_calls=1,
        ),
        latest_execution=safety_blocked_execution,
    )
    timed_out_execution = execution.model_copy(
        update={
            "code_artifacts": [
                execution.code_artifacts[0].model_copy(update={"timeout_seconds": 120})
            ],
            "sandbox_executions": [
                execution.sandbox_executions[0].model_copy(
                    update={"status": "timed_out", "timeout": True, "exit_code": None}
                )
            ],
        }
    )
    assert adaptive_loop_can_resume(
        first.report,
        AdaptiveEvidenceLoopConfig(max_questioner_iterations=2),
        latest_execution=timed_out_execution,
        authorized_timeout_seconds=300,
    )

    resumed_budget = TargetedLLMBudgetManager(
        config=targeted_config.model_copy(update={"resume": True}),
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    accept_delegate = _AcceptNegativeQuestioner()
    resumed = run_adaptive_evidence_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        brief=_brief().model_copy(update={"brief_id": "adaptive-resume-brief"}),
        questioner=resumed_budget.wrap_client(
            accept_delegate,
            {"review_evidence": "llm-stage-b-review"},
        ),
        planner=None,
        plan_repairer=None,
        code_generator=None,
        code_repairer=None,
        budget=resumed_budget,
        config=expanded,
        retrieval_mode="real_retrieval",
        require_non_fake_backends=True,
    )

    assert accept_delegate.call_count == 1
    assert resumed.report.status == "satisfied_negative"
    assert len(resumed.report.iterations) == 2
    assert resumed.report.budget_usage.total_calls == 2


def test_adaptive_questioner_normalizes_passing_blocking_answer() -> None:
    accepted, reasons = parse_adaptive_questioner_response(
        {
            "decisions": [
                {
                    "answers": [
                        {
                            "question_id": "stopping",
                            "category": "stopping",
                            "status": "pass",
                            "explanation": "The bounded branch should continue.",
                            "evidence_artifact_ids": [],
                            "blocking": True,
                        }
                    ],
                    "recommended_action": "stop_no_progress",
                    "rationale": "No additional repair is justified.",
                    "repair_instructions": [],
                    "unresolved_questions": [],
                    "claim_disposition": "deferred",
                }
            ]
        },
        questions_payload=[
            {
                "question_id": "stopping",
                "category": "stopping",
            }
        ],
    )

    assert reasons == []
    assert accepted is not None
    assert accepted.answers[0].blocking is False


@pytest.mark.parametrize("envelope", ["mapping", "bare"])
def test_adaptive_questioner_normalizes_unambiguous_single_decision_envelopes(
    envelope: str,
) -> None:
    decision = {
        "answers": [
            {
                "question_id": "stopping",
                "category": "stopping",
                "status": "pass",
                "explanation": "The bounded result is ready for adjudication.",
                "evidence_artifact_ids": [],
                "blocking": False,
            }
        ],
        "recommended_action": "accept_supported_result",
        "rationale": "The selected checks passed.",
        "repair_instructions": [],
        "unresolved_questions": [],
        "claim_disposition": "supported",
    }
    payload = {"decisions": decision} if envelope == "mapping" else decision

    accepted, reasons = parse_adaptive_questioner_response(
        payload,
        questions_payload=[
            {
                "question_id": "stopping",
                "category": "stopping",
            }
        ],
    )

    assert reasons == []
    assert accepted is not None
    assert accepted.recommended_action == "accept_supported_result"


def test_adaptive_timeout_retry_requires_explicitly_larger_authorized_limit(
    tmp_path: Path,
) -> None:
    run_id = "adaptive-timeout-retry"
    _persist_adaptive_negative_fixture(tmp_path, run_id)
    report_path = (
        tmp_path
        / "runs"
        / run_id
        / "reports"
        / "evidence-package-execution-report-0001.json"
    )
    report = EvidencePackageExecutionReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
    timed_out = report.model_copy(
        update={
            "code_artifacts": [
                report.code_artifacts[0].model_copy(update={"timeout_seconds": 120})
            ],
            "sandbox_executions": [
                report.sandbox_executions[0].model_copy(
                    update={
                        "status": "timed_out",
                        "exit_code": None,
                        "timeout": True,
                        "failure_reason_optional": "generated experiment exceeded its timeout",
                    }
                )
            ],
        }
    )

    assert not _can_retry_timed_out_execution(
        timed_out,
        authorized_timeout_seconds=120,
    )
    assert _can_retry_timed_out_execution(
        timed_out,
        authorized_timeout_seconds=300,
    )


def test_adaptive_evidence_loop_stops_before_call_when_optional_budget_is_reserved(
    tmp_path: Path,
) -> None:
    run_id = "adaptive-budget-stop"
    _persist_adaptive_negative_fixture(tmp_path, run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    targeted_config = TargetedStudyConfig(
        run_id=run_id,
        mode="full",
        brief_path_optional="unused-brief.json",
        model="test-model",
        allow_external_calls=True,
        require_non_fake_backends=True,
        max_total_calls=1,
        max_cost_usd=1.0,
    )
    budget = TargetedLLMBudgetManager(
        config=targeted_config,
        root=tmp_path,
        store=store,
        ledger=ledger,
        reserve_calls=1,
    )
    questioner = _AcceptNegativeQuestioner()

    result = run_adaptive_evidence_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        brief=_brief().model_copy(update={"brief_id": "adaptive-budget-brief"}),
        questioner=questioner,
        planner=None,
        plan_repairer=None,
        code_generator=None,
        code_repairer=None,
        budget=budget,
        config=AdaptiveEvidenceLoopConfig(),
        retrieval_mode="real_retrieval",
        require_non_fake_backends=True,
    )

    assert questioner.call_count == 0
    assert result.report.status == "budget_exhausted"
    assert result.report.iterations == []
    assert result.report.accepted_result_ids == []
    assert result.report.publication_ready is False


def test_targeted_budget_prechecks_quality_repair_capacity(tmp_path: Path) -> None:
    config = TargetedStudyConfig(
        run_id="targeted-quality-budget",
        mode="full",
        brief_path_optional="brief.json",
        model="test-model",
        allow_external_calls=True,
        require_non_fake_backends=True,
        max_total_calls=100,
        max_cost_usd=100.0,
    )
    budget = TargetedLLMBudgetManager(
        config=config,
        root=tmp_path,
        store=ArtifactStore(tmp_path),
        ledger=ResearchLedger(
            tmp_path / "runs" / config.run_id / "ledger.sqlite"
        ),
    )
    max_quality_calls = (
        1
        + config.adaptive_evidence.max_code_repair_calls
        + 2 * config.adaptive_evidence.max_plan_repair_calls
    )

    assert budget.can_spend_optional(
        max_quality_calls,
        quality_repair_calls=max_quality_calls,
    )
    assert not budget.can_spend_optional(
        max_quality_calls + 1,
        quality_repair_calls=max_quality_calls + 1,
    )


def test_transport_failure_does_not_reduce_resumed_quality_repair_capacity(
    tmp_path: Path,
) -> None:
    run_id = "targeted-quality-transport"
    config = TargetedStudyConfig(
        run_id=run_id,
        mode="full",
        brief_path_optional="brief.json",
        model="test-model",
        allow_external_calls=True,
        require_non_fake_backends=True,
        max_total_calls=100,
        max_cost_usd=100.0,
        adaptive_evidence=AdaptiveEvidenceLoopConfig(
            max_questioner_iterations=1,
            max_code_repair_calls=0,
            max_plan_repair_calls=0,
        ),
    )
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    budget = TargetedLLMBudgetManager(
        config=config,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    client = SimpleNamespace(backend_name="llm-openai", model="test-model")

    def fail_transport() -> None:
        raise AdapterTransportError(
            backend="openai",
            provider="openai",
            operation="responses.create",
            message="temporary transport failure",
        )

    with pytest.raises(AdapterTransportError):
        budget.call(
            step_name="llm-quality-repair",
            client=client,
            method_name="repair_code",
            operation=fail_transport,
            request_payload={"request": "bounded repair"},
        )

    resumed = TargetedLLMBudgetManager(
        config=config.model_copy(update={"resume": True}),
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    assert resumed.usage.quality_repair_calls == 1
    assert resumed.can_spend_optional(1, quality_repair_calls=1)


def test_budget_blocked_plan_repair_does_not_consume_scientific_attempt(
    tmp_path: Path,
) -> None:
    run_id = "adaptive-plan-budget-stop"
    _persist_adaptive_negative_fixture(tmp_path, run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    targeted_config = TargetedStudyConfig(
        run_id=run_id,
        mode="full",
        brief_path_optional="brief.json",
        model="test-model",
        allow_external_calls=True,
        require_non_fake_backends=True,
        max_total_calls=1,
        max_cost_usd=1.0,
        adaptive_evidence=AdaptiveEvidenceLoopConfig(
            max_questioner_iterations=1,
            max_code_repair_calls=0,
            max_plan_repair_calls=1,
        ),
    )
    budget = TargetedLLMBudgetManager(
        config=targeted_config,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    questioner = budget.wrap_client(
        _RepairPlanQuestioner(),
        {"review_evidence": "llm-stage-b-review"},
    )

    result = run_adaptive_evidence_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        brief=_brief(),
        questioner=questioner,
        planner=None,
        plan_repairer=None,
        code_generator=None,
        code_repairer=None,
        budget=budget,
        config=targeted_config.adaptive_evidence,
        retrieval_mode="real_retrieval",
        require_non_fake_backends=True,
    )

    assert result.report.status == "budget_exhausted"
    assert result.report.plan_repair_attempt_count == 0
    assert result.report.plan_repair_success_count == 0
    assert "runtime repair" in result.report.terminal_reason


def test_replanned_evidence_execution_gets_one_runtime_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "adaptive-replanned-runtime-repair"
    _persist_adaptive_negative_fixture(tmp_path, run_id)
    reports = tmp_path / "runs" / run_id / "reports"
    package_report = HybridEvidencePackageReport.model_validate_json(
        (reports / "hybrid-evidence-package-report-0001.json").read_text(
            encoding="utf-8"
        )
    )
    execution_report = EvidencePackageExecutionReport.model_validate_json(
        (reports / "evidence-package-execution-report-0001.json").read_text(
            encoding="utf-8"
        )
    )
    execution_options: dict[str, object] = {}

    def replan(**_kwargs):
        return SimpleNamespace(
            report=package_report,
            report_artifact=SimpleNamespace(
                path=f"runs/{run_id}/reports/hybrid-evidence-package-report-0001.json"
            ),
        )

    def execute(**kwargs):
        execution_options.update(kwargs)
        return SimpleNamespace(
            report=execution_report,
            report_artifact=SimpleNamespace(
                path=f"runs/{run_id}/reports/evidence-package-execution-report-0001.json"
            ),
        )

    monkeypatch.setattr(
        adaptive_evidence_module,
        "plan_hybrid_evidence_packages",
        replan,
    )
    monkeypatch.setattr(
        adaptive_evidence_module,
        "execute_hybrid_evidence_packages",
        execute,
    )
    config = TargetedStudyConfig(
        run_id=run_id,
        mode="full",
        brief_path_optional="brief.json",
        model="test-model",
        allow_external_calls=True,
        require_non_fake_backends=True,
        max_total_calls=10,
        max_cost_usd=10.0,
        adaptive_evidence=AdaptiveEvidenceLoopConfig(
            max_questioner_iterations=1,
            max_code_repair_calls=0,
            max_plan_repair_calls=1,
        ),
    )
    budget = TargetedLLMBudgetManager(
        config=config,
        root=tmp_path,
        store=ArtifactStore(tmp_path),
        ledger=ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite"),
    )
    dummy = SimpleNamespace()

    result = run_adaptive_evidence_loop(
        run_id=run_id,
        root=tmp_path,
        store=ArtifactStore(tmp_path),
        ledger=ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite"),
        brief=_brief(),
        questioner=_RepairPlanQuestioner(),
        planner=dummy,
        plan_repairer=dummy,
        code_generator=dummy,
        code_repairer=dummy,
        budget=budget,
        config=config.adaptive_evidence,
        retrieval_mode="real_retrieval",
        require_non_fake_backends=True,
    )

    assert result.report.plan_repair_attempt_count == 1
    assert result.report.plan_repair_success_count == 1
    assert execution_options["max_runtime_repair_calls"] == 1


def test_terminal_targeted_inspection_has_no_misleading_next_stage(
    tmp_path: Path,
) -> None:
    run_id = "targeted-terminal-inspection"
    _persist_adaptive_negative_fixture(tmp_path, run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    config = TargetedStudyConfig(
        run_id=run_id,
        mode="full",
        brief_path_optional="brief.json",
        model="test-model",
        allow_external_calls=True,
        require_non_fake_backends=True,
        max_total_calls=27,
        max_cost_usd=1.0,
    )
    budget = TargetedLLMBudgetManager(
        config=config,
        root=tmp_path,
        store=store,
        ledger=ledger,
        reserve_calls=27,
    )
    adaptive = run_adaptive_evidence_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        brief=_brief(),
        questioner=_AcceptNegativeQuestioner(),
        planner=None,
        plan_repairer=None,
        code_generator=None,
        code_repairer=None,
        budget=budget,
        config=AdaptiveEvidenceLoopConfig(),
        retrieval_mode="real_retrieval",
        require_non_fake_backends=True,
    )
    _complete_targeted_run(
        config=config,
        brief=_brief(),
        records=[],
        checkpoints=[],
        status="deferred",
        blockers=[adaptive.report.terminal_reason],
        completed_calls=0,
        root=tmp_path,
        store=store,
        ledger=ledger,
        budget=budget,
    )

    inspection = inspect_targeted_study(run_id=run_id, root=tmp_path)

    assert inspection.adaptive_evidence_status_optional == "budget_exhausted"
    assert inspection.next_stage_optional is None
