from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from factori.adapters.atlas_ranking import build_pair_ranking_prompt
from factori.adapters.deep_opportunity import (
    OpportunityGenerationResponse,
    OpportunityProposal,
    OpportunityProposalEnvelope,
    OpportunityProposalItem,
    OpportunityScoreProposal,
)
from factori.adapters.fake import FakeProseGenerator
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.full_paper_generation import (
    generate_full_paper,
    inspect_paper_bundle_summary,
    lint_paper_bundle_summary,
)
from factori.full_paper_release import run_full_paper_release_gate
from factori.hashing import sha256_file
from factori.ledger import ResearchLedger
from factori.llm_orchestration import (
    LLMOrchestrationError,
    build_llm_orchestration_preflight_summary,
    run_llm_paper_orchestration,
)
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    BackendKind,
    BibliographyEntry,
    CitationRecord,
    CitationRegistry,
    ClaimSupportAuditReport,
    ClaimSupportItem,
    FullPaperGenerationConfig,
    FullPaperReleaseGateConfig,
    LLMBudgetConfig,
    LLMOrchestrationConfig,
    LLMPairRankingResult,
    PipelineRunConfig,
    PipelineStage,
    RetrievalQualityReport,
)


def test_inspect_llm_run_cli_is_registered() -> None:
    result = CliRunner().invoke(app, ["inspect-llm-run", "--help"])

    assert result.exit_code == 0, result.output
    assert "--run-id" in result.output
    assert "--json" in result.output


def test_inspect_paper_bundle_cli_is_registered() -> None:
    result = CliRunner().invoke(app, ["inspect-paper-bundle", "--help"])

    assert result.exit_code == 0, result.output
    assert "--run-id" in result.output
    assert "--json" in result.output


def test_inspect_reviewer_summary_cli_is_registered() -> None:
    result = CliRunner().invoke(app, ["inspect-reviewer-summary", "--help"])

    assert result.exit_code == 0, result.output
    assert "--run-id" in result.output
    assert "--json" in result.output


def test_capability_escalation_cli_commands_are_registered() -> None:
    escalate = CliRunner().invoke(app, ["escalate-capabilities", "--help"])
    inspect = CliRunner().invoke(app, ["inspect-capability-escalation", "--help"])

    assert escalate.exit_code == 0, escalate.output
    assert "--allow-network" in escalate.output
    assert "--allow-external-proof-tools" in escalate.output
    assert "--allow-external-retrieval-" in escalate.output
    assert inspect.exit_code == 0, inspect.output
    assert "--json" in inspect.output


def test_final_manuscript_cli_commands_are_registered() -> None:
    regenerate = CliRunner().invoke(app, ["regenerate-final-manuscript", "--help"])
    inspect = CliRunner().invoke(app, ["inspect-final-manuscript", "--help"])

    assert regenerate.exit_code == 0, regenerate.output
    assert "--regeneration-backend" in regenerate.output
    assert inspect.exit_code == 0, inspect.output
    assert "--json" in inspect.output


def test_nucleus_manuscript_cli_commands_are_registered() -> None:
    plan = CliRunner().invoke(app, ["plan-nucleus-manuscript", "--help"])
    synthesize = CliRunner().invoke(app, ["synthesize-nucleus-manuscript", "--help"])
    revise = CliRunner().invoke(app, ["revise-nucleus-manuscript", "--help"])
    inspect = CliRunner().invoke(app, ["inspect-nucleus-manuscript", "--help"])

    assert plan.exit_code == 0, plan.output
    assert "--backend" in plan.output
    assert "--require-non-fake-backends" in plan.output
    assert synthesize.exit_code == 0, synthesize.output
    assert "--allow-external-calls" in synthesize.output
    assert revise.exit_code == 0, revise.output
    assert "--max-revision-attempts" in revise.output
    assert inspect.exit_code == 0, inspect.output
    assert "--json" in inspect.output


def test_final_paper_cli_commands_are_registered() -> None:
    assemble = CliRunner().invoke(app, ["assemble-final-paper", "--help"])
    verify = CliRunner().invoke(app, ["verify-final-paper", "--help"])
    inspect = CliRunner().invoke(app, ["inspect-final-paper", "--help"])
    bundle = CliRunner().invoke(app, ["build-final-paper-bundle", "--help"])

    assert assemble.exit_code == 0, assemble.output
    assert "--require-non-fake-backends" in assemble.output
    assert verify.exit_code == 0, verify.output
    assert "--require-non-fake-backends" in verify.output
    assert inspect.exit_code == 0, inspect.output
    assert "--json" in inspect.output
    assert bundle.exit_code == 0, bundle.output
    assert "--run-id" in bundle.output


def test_final_release_bundle_cli_commands_are_registered() -> None:
    build = CliRunner().invoke(app, ["build-final-release-bundle", "--help"])
    inspect = CliRunner().invoke(app, ["inspect-final-release-bundle", "--help"])
    verify = CliRunner().invoke(app, ["verify-final-release-bundle", "--help"])

    assert build.exit_code == 0, build.output
    assert "--compile-pdf" in build.output
    assert "--strict-export" in build.output
    assert "--json" in build.output
    assert inspect.exit_code == 0, inspect.output
    assert "--json" in inspect.output
    assert verify.exit_code == 0, verify.output
    assert "--bundle-path" in verify.output
    assert "--run-id" in verify.output
    assert "--write-report" in verify.output
    assert "--json" in verify.output


def test_autonomous_paper_cli_commands_are_registered() -> None:
    run = CliRunner().invoke(app, ["run-autonomous-paper", "--help"])
    inspect = CliRunner().invoke(app, ["inspect-autonomous-paper-run", "--help"])

    assert run.exit_code == 0, run.output
    assert "--max-loop-iterat" in run.output
    assert "--enable-empirica" in run.output
    assert "--build-final-bun" in run.output
    assert "--verify-final-bu" in run.output
    assert "--resume-existing" in run.output
    assert inspect.exit_code == 0, inspect.output
    assert "--run-id" in inspect.output
    assert "--json" in inspect.output


def test_autonomous_paper_checkpoint_cli_commands_are_registered() -> None:
    checkpoints = CliRunner().invoke(app, ["inspect-autonomous-paper-checkpoints", "--help"])
    resume = CliRunner().invoke(app, ["inspect-autonomous-paper-resume", "--help"])

    assert checkpoints.exit_code == 0, checkpoints.output
    assert "--run-id" in checkpoints.output
    assert "--json" in checkpoints.output
    assert resume.exit_code == 0, resume.output
    assert "--run-id" in resume.output
    assert "--json" in resume.output


def test_idea_tree_cli_commands_are_registered() -> None:
    build_atlas = CliRunner().invoke(app, ["build-domain-method-atlas", "--help"])
    scan_atlas = CliRunner().invoke(app, ["scan-domain-method-pairs", "--help"])
    inspect_atlas = CliRunner().invoke(app, ["inspect-atlas-scan", "--help"])
    discover_deep = CliRunner().invoke(app, ["discover-deep-opportunities", "--help"])
    inspect_deep = CliRunner().invoke(app, ["inspect-deep-opportunities", "--help"])
    generate_llm_variance = CliRunner().invoke(app, ["generate-llm-variance", "--help"])
    inspect_llm_variance = CliRunner().invoke(app, ["inspect-llm-variance", "--help"])
    construct_llm_tree = CliRunner().invoke(
        app, ["construct-idea-tree-from-llm-variance", "--help"]
    )
    construct_llm_substrates = CliRunner().invoke(
        app, ["construct-llm-substrates", "--help"]
    )
    inspect_llm_substrates = CliRunner().invoke(
        app, ["inspect-llm-substrates", "--help"]
    )
    plan_llm_routes = CliRunner().invoke(app, ["plan-llm-routes", "--help"])
    inspect_llm_routes = CliRunner().invoke(app, ["inspect-llm-routes", "--help"])
    generate_experiment_code = CliRunner().invoke(
        app, ["generate-experiment-code", "--help"]
    )
    inspect_experiment_code = CliRunner().invoke(
        app, ["inspect-experiment-code", "--help"]
    )
    run_generated_experiments = CliRunner().invoke(
        app, ["run-generated-experiments", "--help"]
    )
    inspect_generated_results = CliRunner().invoke(
        app, ["inspect-generated-experiment-results", "--help"]
    )
    plan_hybrid_packages = CliRunner().invoke(
        app, ["plan-hybrid-evidence-packages", "--help"]
    )
    inspect_hybrid_packages = CliRunner().invoke(
        app, ["inspect-hybrid-evidence-packages", "--help"]
    )
    execute_hybrid_packages = CliRunner().invoke(
        app, ["execute-hybrid-evidence-packages", "--help"]
    )
    inspect_package_execution = CliRunner().invoke(
        app, ["inspect-evidence-package-execution", "--help"]
    )
    critique_packages = CliRunner().invoke(app, ["critique-evidence-packages", "--help"])
    adjudicate_packages = CliRunner().invoke(
        app, ["adjudicate-evidence-packages", "--help"]
    )
    inspect_adjudication = CliRunner().invoke(
        app, ["inspect-package-adjudication", "--help"]
    )
    discover = CliRunner().invoke(app, ["discover-opportunities", "--help"])
    inspect_opps = CliRunner().invoke(app, ["inspect-opportunities", "--help"])
    augment = CliRunner().invoke(app, ["augment-variance", "--help"])
    inspect_augmentation = CliRunner().invoke(app, ["inspect-variance-augmentation", "--help"])
    apply_augmentation = CliRunner().invoke(app, ["apply-variance-augmentation", "--help"])
    promote_substrates = CliRunner().invoke(app, ["promote-variance-substrates", "--help"])
    inspect_promotion = CliRunner().invoke(app, ["inspect-substrate-promotion", "--help"])
    route_branches = CliRunner().invoke(app, ["route-branches", "--help"])
    inspect_routes = CliRunner().invoke(app, ["inspect-branch-routes", "--help"])
    build_route_specs = CliRunner().invoke(app, ["build-route-execution-specs", "--help"])
    run_route_execution = CliRunner().invoke(app, ["run-route-execution", "--help"])
    inspect_route_execution = CliRunner().invoke(app, ["inspect-route-execution", "--help"])
    inspect_backends = CliRunner().invoke(app, ["inspect-backends", "--help"])
    check_production = CliRunner().invoke(app, ["check-production-mode", "--help"])
    inspect = CliRunner().invoke(app, ["inspect-idea-tree", "--help"])
    export = CliRunner().invoke(app, ["export-idea-tree", "--help"])
    inspect_space = CliRunner().invoke(app, ["inspect-idea-space", "--help"])
    export_space = CliRunner().invoke(app, ["export-idea-space-report", "--help"])
    build_substrate = CliRunner().invoke(app, ["build-scientific-substrate", "--help"])
    inspect_substrate = CliRunner().invoke(app, ["inspect-scientific-substrate", "--help"])

    assert discover.exit_code == 0, discover.output
    assert build_atlas.exit_code == 0, build_atlas.output
    assert "--run-id" in build_atlas.output
    assert scan_atlas.exit_code == 0, scan_atlas.output
    assert "--require-non-fake" in scan_atlas.output
    assert "--allow-external-calls" in scan_atlas.output
    assert inspect_atlas.exit_code == 0, inspect_atlas.output
    assert "--json" in inspect_atlas.output
    assert discover_deep.exit_code == 0, discover_deep.output
    assert "--retrieval-mode" in discover_deep.output
    assert "--require-non-fake" in discover_deep.output
    assert "--allow-external-calls" in discover_deep.output
    assert inspect_deep.exit_code == 0, inspect_deep.output
    assert "--json" in inspect_deep.output
    assert generate_llm_variance.exit_code == 0, generate_llm_variance.output
    assert "--require-non-fake" in generate_llm_variance.output
    assert "--allow-external-calls" in generate_llm_variance.output
    assert inspect_llm_variance.exit_code == 0, inspect_llm_variance.output
    assert "--json" in inspect_llm_variance.output
    assert construct_llm_tree.exit_code == 0, construct_llm_tree.output
    assert "--run-id" in construct_llm_tree.output
    assert construct_llm_substrates.exit_code == 0, construct_llm_substrates.output
    assert "--require-non-fake" in construct_llm_substrates.output
    assert "--allow-external-calls" in construct_llm_substrates.output
    assert inspect_llm_substrates.exit_code == 0, inspect_llm_substrates.output
    assert "--json" in inspect_llm_substrates.output
    assert plan_llm_routes.exit_code == 0, plan_llm_routes.output
    assert "--require-non-fake" in plan_llm_routes.output
    assert "--allow-external-calls" in plan_llm_routes.output
    assert inspect_llm_routes.exit_code == 0, inspect_llm_routes.output
    assert "--json" in inspect_llm_routes.output
    assert generate_experiment_code.exit_code == 0, generate_experiment_code.output
    assert "--allow-external-calls" in generate_experiment_code.output
    assert "--require-non-fake" in generate_experiment_code.output
    assert inspect_experiment_code.exit_code == 0, inspect_experiment_code.output
    assert "--json" in inspect_experiment_code.output
    assert run_generated_experiments.exit_code == 0, run_generated_experiments.output
    assert "--require-non-fake" in run_generated_experiments.output
    assert inspect_generated_results.exit_code == 0, inspect_generated_results.output
    assert "--json" in inspect_generated_results.output
    assert plan_hybrid_packages.exit_code == 0, plan_hybrid_packages.output
    assert "--require-non-fake" in plan_hybrid_packages.output
    assert "--allow-external-calls" in plan_hybrid_packages.output
    assert inspect_hybrid_packages.exit_code == 0, inspect_hybrid_packages.output
    assert "--json" in inspect_hybrid_packages.output
    assert execute_hybrid_packages.exit_code == 0, execute_hybrid_packages.output
    assert "--retrieval-mode" in execute_hybrid_packages.output
    assert "--require-non-fake" in execute_hybrid_packages.output
    assert "--allow-external-calls" in execute_hybrid_packages.output
    assert inspect_package_execution.exit_code == 0, inspect_package_execution.output
    assert "--json" in inspect_package_execution.output
    assert critique_packages.exit_code == 0, critique_packages.output
    assert "--require-non-fake" in critique_packages.output
    assert "--allow-external-calls" in critique_packages.output
    assert adjudicate_packages.exit_code == 0, adjudicate_packages.output
    assert "--allow-symbolic-primary" in adjudicate_packages.output
    assert "--require-non-fake" in adjudicate_packages.output
    assert inspect_adjudication.exit_code == 0, inspect_adjudication.output
    assert "--json" in inspect_adjudication.output
    assert "--domain" in discover.output
    assert "--max-methods" in discover.output
    assert inspect_opps.exit_code == 0, inspect_opps.output
    assert "--json" in inspect_opps.output
    assert augment.exit_code == 0, augment.output
    assert "--candidates-per-seed" in augment.output
    assert "--max-total-candidates" in augment.output
    assert inspect_augmentation.exit_code == 0, inspect_augmentation.output
    assert "--json" in inspect_augmentation.output
    assert apply_augmentation.exit_code == 0, apply_augmentation.output
    assert "--run-id" in apply_augmentation.output
    assert promote_substrates.exit_code == 0, promote_substrates.output
    assert "--max-substrates" in promote_substrates.output
    assert inspect_promotion.exit_code == 0, inspect_promotion.output
    assert "--json" in inspect_promotion.output
    assert route_branches.exit_code == 0, route_branches.output
    assert "--run-id" in route_branches.output
    assert inspect_routes.exit_code == 0, inspect_routes.output
    assert "--json" in inspect_routes.output
    assert build_route_specs.exit_code == 0, build_route_specs.output
    assert "--run-id" in build_route_specs.output
    assert run_route_execution.exit_code == 0, run_route_execution.output
    assert "--json" in run_route_execution.output
    assert inspect_route_execution.exit_code == 0, inspect_route_execution.output
    assert "--json" in inspect_route_execution.output
    assert inspect_backends.exit_code == 0, inspect_backends.output
    assert "--json" in inspect_backends.output
    assert check_production.exit_code == 0, check_production.output
    assert "--require-non-fake" in check_production.output
    assert inspect.exit_code == 0, inspect.output
    assert "--run-id" in inspect.output
    assert "--json" in inspect.output
    assert export.exit_code == 0, export.output
    assert "--run-id" in export.output
    assert "--format" in export.output
    assert inspect_space.exit_code == 0, inspect_space.output
    assert "--run-id" in inspect_space.output
    assert "--json" in inspect_space.output
    assert export_space.exit_code == 0, export_space.output
    assert "--run-id" in export_space.output
    assert "--format" in export_space.output
    assert build_substrate.exit_code == 0, build_substrate.output
    assert "--max-substrates" in build_substrate.output
    assert "--mutation-axis" in build_substrate.output
    assert inspect_substrate.exit_code == 0, inspect_substrate.output
    assert "--json" in inspect_substrate.output


def test_atlas_cli_builds_scans_and_inspects_with_mocked_llm(tmp_path, monkeypatch) -> None:
    class MockRanker:
        backend_name = "llm-openai-mocked-transport"
        backend_kind = BackendKind.LLM_OPENAI
        model = "mock-atlas-model"
        fallback_used = False
        fallback_disclosed = True

        def rank_batch(self, *, pair_payloads, batch_index, prompt_id):
            prompt = build_pair_ranking_prompt(
                pair_payloads=pair_payloads,
                batch_index=batch_index,
                prompt_id=prompt_id,
                backend_name=self.backend_name,
                model=self.model,
            )
            return prompt, [
                LLMPairRankingResult(
                    pair_id=payload["pair_id"],
                    rank_score=0.8,
                    scientific_fit=0.8,
                    tractability=0.8,
                    question_abundance=0.8,
                    baseline_clarity=0.8,
                    verification_feasibility=0.8,
                    paper_shape_clarity=0.8,
                    false_bridge_risk=0.2,
                    tautology_risk=0.2,
                    novelty_hypothesis="Hypothesis: novelty requires retrieval.",
                    underuse_hypothesis="Hypothesis: underuse requires retrieval.",
                    ranking_explanation="Mocked LLM ranking.",
                    recommended_for_deep_discovery=True,
                )
                for payload in pair_payloads
            ]

    monkeypatch.setattr(
        "factori.cli.OpenAIAtlasPairRanker",
        lambda **kwargs: MockRanker(),
    )
    run_id = "cli-atlas-scan"
    runner = CliRunner()
    built = runner.invoke(
        app,
        [
            "build-domain-method-atlas",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    scanned = runner.invoke(
        app,
        [
            "scan-domain-method-pairs",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--backend",
            "llm-openai",
            "--allow-external-calls",
            "--require-non-fake-backends",
            "--batch-size",
            "100",
            "--max-ranking-calls",
            "10",
            "--json",
        ],
    )
    inspected = runner.invoke(
        app,
        [
            "inspect-atlas-scan",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )

    assert built.exit_code == 0, built.output
    built_payload = json.loads(built.output)
    assert built_payload["domain_count"] == 42
    assert built_payload["method_count"] == 32
    assert scanned.exit_code == 0, scanned.output
    scan_payload = json.loads(scanned.output)
    assert scan_payload["raw_pair_count"] == 1344
    assert scan_payload["llm_ranked_pair_count"] > 0
    assert scan_payload["selected_pair_count"] == 30
    assert scan_payload["domain_family_coverage"] >= 8
    assert scan_payload["method_family_coverage"] >= 8
    assert scan_payload["production_ready"] is True
    assert scan_payload["publication_ready"] is False
    assert inspected.exit_code == 0, inspected.output
    inspected_payload = json.loads(inspected.output)
    assert inspected_payload["atlas_scan_present"] is True
    assert inspected_payload["selected_pair_count"] == 30


def test_deep_opportunity_cli_discovers_and_inspects_with_mocked_backends(
    tmp_path, monkeypatch
) -> None:
    class MockRanker:
        backend_name = "llm-openai-mocked-transport"
        backend_kind = BackendKind.LLM_OPENAI
        model = "mock-atlas-model"
        fallback_used = False
        fallback_disclosed = True

        def rank_batch(self, *, pair_payloads, batch_index, prompt_id):
            prompt = build_pair_ranking_prompt(
                pair_payloads=pair_payloads,
                batch_index=batch_index,
                prompt_id=prompt_id,
                backend_name=self.backend_name,
                model=self.model,
            )
            return prompt, [
                LLMPairRankingResult(
                    pair_id=payload["pair_id"],
                    rank_score=0.82,
                    scientific_fit=0.82,
                    tractability=0.8,
                    question_abundance=0.8,
                    baseline_clarity=0.8,
                    verification_feasibility=0.8,
                    paper_shape_clarity=0.8,
                    false_bridge_risk=0.2,
                    tautology_risk=0.2,
                    novelty_hypothesis="Hypothesis: novelty requires retrieval.",
                    underuse_hypothesis="Hypothesis: underuse requires retrieval.",
                    ranking_explanation="Mocked LLM ranking.",
                    recommended_for_deep_discovery=True,
                )
                for payload in pair_payloads
            ]

    class MockGenerator:
        backend_name = "llm-openai-mocked-transport"
        backend_kind = BackendKind.LLM_OPENAI
        model = "mock-deep-model"
        fallback_used = False
        fallback_disclosed = True

        def generate_for_pair(
            self, *, pair_payload, retrieval_payload, opportunities_per_pair
        ):
            pair = pair_payload["pair"]
            items = []
            for index in range(opportunities_per_pair):
                items.append(
                    OpportunityProposalItem(
                        candidate=OpportunityProposal(
                            research_question=(
                                f"What bounded mechanism {index} can test {pair['pair_id']}?"
                            ),
                            hypothesis="The mechanism changes a declared synthetic metric.",
                            theory_or_model_object=f"Concrete operator T_{index}",
                            mathematical_or_computational_form=f"T_{index}(x)=x",
                            experiment_or_proof_plan="Run a fixed-seed synthetic comparison.",
                            benchmark_plan="Compare against null and standard baselines.",
                            baseline_candidates=["null baseline"],
                            expected_metrics=["held_out_error"],
                            failure_modes=["no improvement"],
                            negative_controls=["remove the mechanism"],
                            data_regime="synthetic_only",
                            verification_path="bounded synthetic benchmark",
                            paper_shape="model, result, negative control, limitations",
                            novelty_risk="Hypothesis: a close prior may exist.",
                            underuse_hypothesis=(
                                "Hypothesis: underuse is not established by mocked retrieval."
                            ),
                            retrieval_support_summary=(
                                f"Context mode is {retrieval_payload['retrieval_mode']}."
                            ),
                            retrieval_contradictions=[],
                            false_bridge_risks=[],
                            tautology_risks=["DGP construction risk"],
                            recommended_next_stage="variance_generation",
                        ),
                        score=OpportunityScoreProposal(
                            scientific_fit=0.8,
                            tractability=0.8,
                            question_specificity=0.8,
                            baseline_strength=0.8,
                            verification_feasibility=0.8,
                            expected_signal=0.8,
                            failure_mode_value=0.8,
                            paper_coherence=0.8,
                            novelty_risk_penalty=0.2,
                            false_bridge_penalty=0.2,
                            tautology_penalty=0.2,
                            retrieval_confidence=retrieval_payload[
                                "retrieval_confidence"
                            ],
                            final_score=0.8 - index * 0.01,
                            score_explanation="Mocked score.",
                        ),
                    )
                )
            return OpportunityGenerationResponse(
                prompt_text="mock deep opportunity prompt",
                requested_output_schema=OpportunityProposalEnvelope.model_json_schema(),
                raw_response={
                    "opportunities": [item.model_dump(mode="json") for item in items]
                },
                accepted=items,
                rejected=[],
            )

    monkeypatch.setattr("factori.cli.OpenAIAtlasPairRanker", lambda **kwargs: MockRanker())
    monkeypatch.setattr(
        "factori.cli.OpenAIDeepOpportunityGenerator",
        lambda **kwargs: MockGenerator(),
    )
    run_id = "cli-deep-opportunity"
    runner = CliRunner()
    assert runner.invoke(
        app,
        [
            "build-domain-method-atlas",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
        ],
    ).exit_code == 0
    scan = runner.invoke(
        app,
        [
            "scan-domain-method-pairs",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--allow-external-calls",
            "--batch-size",
            "100",
            "--max-ranking-calls",
            "10",
        ],
    )
    discovered = runner.invoke(
        app,
        [
            "discover-deep-opportunities",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--retrieval-mode",
            "mocked",
            "--allow-external-calls",
            "--max-pairs",
            "4",
            "--max-generation-calls",
            "4",
            "--opportunities-per-pair",
            "2",
            "--json",
        ],
    )
    inspected = runner.invoke(
        app,
        [
            "inspect-deep-opportunities",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )

    assert scan.exit_code == 0, scan.output
    assert discovered.exit_code == 0, discovered.output
    payload = json.loads(discovered.output)
    assert payload["selected_pair_count"] == 4
    assert payload["generated_opportunity_count"] == 8
    assert payload["selected_opportunity_count"] > 0
    assert payload["config"]["retrieval_mode"] == "mocked_retrieval"
    assert payload["production_ready"] is False
    assert payload["publication_ready"] is False
    assert inspected.exit_code == 0, inspected.output
    inspection = json.loads(inspected.output)
    assert inspection["deep_opportunity_discovery_present"] is True
    assert inspection["selected_opportunity_count"] == payload[
        "selected_opportunity_count"
    ]


def test_opportunity_discovery_cli_discovers_and_inspects(tmp_path) -> None:
    run_id = "cli-opportunity-discovery"
    runner = CliRunner()

    discovered = runner.invoke(
        app,
        [
            "discover-opportunities",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--domain",
            "human geography",
            "--max-methods",
            "20",
        ],
    )
    inspected = runner.invoke(
        app,
        [
            "inspect-opportunities",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )

    assert discovered.exit_code == 0, discovered.output
    assert "primitive_count=" in discovered.output
    assert "method_lens_count=" in discovered.output
    assert "promoted_count=" in discovered.output
    assert "publication_ready=false" in discovered.output
    assert inspected.exit_code == 0, inspected.output
    payload = json.loads(inspected.output)
    assert payload["opportunity_discovery_present"] is True
    assert payload["primitive_count"] > 0
    assert payload["method_lens_count"] >= 10
    assert payload["opportunity_count"] >= 10
    assert payload["promoted_count"] >= 2
    assert payload["seed_constraint_count"] == payload["promoted_count"]
    assert payload["publication_ready"] is False


def test_variance_augmentation_cli_generates_inspects_and_applies(tmp_path) -> None:
    run_id = "cli-variance-augmentation"
    runner = CliRunner()
    discovery = runner.invoke(
        app,
        [
            "discover-opportunities",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--domain",
            "human geography",
        ],
    )
    generated = runner.invoke(
        app,
        [
            "augment-variance",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--candidates-per-seed",
            "4",
            "--max-total-candidates",
            "40",
        ],
    )
    inspected = runner.invoke(
        app,
        [
            "inspect-variance-augmentation",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    applied = runner.invoke(
        app,
        [
            "apply-variance-augmentation",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    promoted = runner.invoke(
        app,
        [
            "promote-variance-substrates",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--max-substrates",
            "8",
            "--json",
        ],
    )
    promotion_inspection = runner.invoke(
        app,
        [
            "inspect-substrate-promotion",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    routed = runner.invoke(
        app,
        [
            "route-branches",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    route_inspection = runner.invoke(
        app,
        [
            "inspect-branch-routes",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    specs_built = runner.invoke(
        app,
        [
            "build-route-execution-specs",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    routes_executed = runner.invoke(
        app,
        [
            "run-route-execution",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    execution_inspection = runner.invoke(
        app,
        [
            "inspect-route-execution",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    backend_inspection = runner.invoke(
        app,
        [
            "inspect-backends",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    development_check = runner.invoke(
        app,
        [
            "check-production-mode",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    strict_check = runner.invoke(
        app,
        [
            "check-production-mode",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--require-non-fake-backends",
            "--json",
        ],
    )
    tree = runner.invoke(
        app,
        [
            "inspect-idea-tree",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )

    assert discovery.exit_code == 0, discovery.output
    assert generated.exit_code == 0, generated.output
    assert "candidate_count=32" in generated.output
    assert inspected.exit_code == 0, inspected.output
    inspected_payload = json.loads(inspected.output)
    assert inspected_payload["variance_augmentation_present"] is True
    assert inspected_payload["seed_count"] == 8
    assert inspected_payload["candidate_count"] == 32
    assert inspected_payload["selected_candidate_count"] >= 16
    assert inspected_payload["method_lens_coverage"] >= 6
    assert inspected_payload["diversity_score_optional"] != "low"
    assert inspected_payload["publication_ready"] is False
    assert applied.exit_code == 0, applied.output
    applied_payload = json.loads(applied.output)
    assert applied_payload["applied_to_idea_tree"] is True
    assert applied_payload["idea_tree_nodes_added"] >= 16
    assert promoted.exit_code == 0, promoted.output
    promoted_payload = json.loads(promoted.output)
    assert promoted_payload["promoted_substrate_count"] == 8
    assert promoted_payload["method_lens_coverage"] == 8
    assert promoted_payload["branch_family_coverage"] >= 3
    assert promoted_payload["publication_ready"] is False
    assert promotion_inspection.exit_code == 0, promotion_inspection.output
    promotion_payload = json.loads(promotion_inspection.output)
    assert promotion_payload["substrate_promotion_present"] is True
    assert promotion_payload["idea_tree_substrate_links_present"] is True
    assert routed.exit_code == 0, routed.output
    routed_payload = json.loads(routed.output)
    assert routed_payload["substrate_count"] == 8
    assert routed_payload["route_count"] == 8
    assert len(routed_payload["route_type_counts"]) >= 3
    assert routed_payload["publication_ready"] is False
    assert route_inspection.exit_code == 0, route_inspection.output
    route_payload = json.loads(route_inspection.output)
    assert route_payload["branch_routes_present"] is True
    assert route_payload["route_count"] == 8
    assert specs_built.exit_code == 0, specs_built.output
    specs_payload = json.loads(specs_built.output)
    assert specs_payload["spec_count"] == 8
    assert specs_payload["report_status"] == "spec_created"
    assert routes_executed.exit_code == 0, routes_executed.output
    execution_payload = json.loads(routes_executed.output)
    assert execution_payload["result_count"] == 8
    assert execution_payload["executed_count"] == 8
    assert execution_payload["failed_count"] == 0
    assert execution_inspection.exit_code == 0, execution_inspection.output
    execution_inspection_payload = json.loads(execution_inspection.output)
    assert execution_inspection_payload["route_execution_present"] is True
    assert execution_inspection_payload["publication_ready"] is False
    assert backend_inspection.exit_code == 0, backend_inspection.output
    backend_payload = json.loads(backend_inspection.output)
    assert backend_payload["stage_count"] == 7
    assert backend_payload["blocking_violation_count"] == 0
    assert development_check.exit_code == 0, development_check.output
    development_payload = json.loads(development_check.output)
    assert development_payload["blocking_violation_count"] == 0
    assert development_payload["production_ready"] is False
    assert strict_check.exit_code == 1, strict_check.output
    strict_payload = json.loads(strict_check.output)
    assert strict_payload["blocking_violation_count"] == 7
    assert strict_payload["production_ready"] is False
    assert strict_payload["publication_ready"] is False
    assert tree.exit_code == 0, tree.output
    tree_payload = json.loads(tree.output)
    variance_nodes = [
        node for node in tree_payload["nodes"] if node["stage_origin"] == "variance_augmentation"
    ]
    assert len(variance_nodes) == applied_payload["idea_tree_nodes_added"]
    assert all(node["source_opportunity_id_optional"] for node in variance_nodes)
    assert all(node["source_method_lens_id_optional"] for node in variance_nodes)
    assert tree_payload["publication_ready"] is False


def test_strict_non_fake_flag_rejects_template_stage_before_writing(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "discover-opportunities",
            "--run-id",
            "strict-template-stage",
            "--root",
            str(tmp_path),
            "--domain",
            "human geography",
            "--require-non-fake-backends",
        ],
    )

    assert result.exit_code == 1
    assert "Strict non-fake production mode rejected opportunity_discovery" in result.output
    assert not (tmp_path / "runs" / "strict-template-stage").exists()


def test_idea_tree_cli_inspects_and_exports_deterministic_run(tmp_path) -> None:
    run_id = "cli-idea-tree"
    run_deterministic_pipeline(
        PipelineRunConfig(run_id=run_id, domain="human geography", root=tmp_path)
    )
    runner = CliRunner()

    inspected = runner.invoke(
        app,
        ["inspect-idea-tree", "--run-id", run_id, "--root", str(tmp_path), "--json"],
    )
    exported = runner.invoke(
        app,
        [
            "export-idea-tree",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--format",
            "markdown",
        ],
    )

    assert inspected.exit_code == 0, inspected.output
    payload = json.loads(inspected.output)
    assert payload["tree_present"] is True
    assert payload["node_count"] > 1
    assert payload["edge_count"] > 0
    assert payload["publication_ready"] is False
    assert exported.exit_code == 0, exported.output
    assert "artifact=runs/cli-idea-tree/reports/idea-tree-0001.md" in exported.output
    assert "publication_ready=false" in exported.output


def test_idea_space_cli_inspects_and_exports_deterministic_run(tmp_path) -> None:
    run_id = "cli-idea-space"
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id=run_id,
            domain="spatial heterogeneity in human geography",
            root=tmp_path,
        )
    )
    runner = CliRunner()

    inspected = runner.invoke(
        app,
        ["inspect-idea-space", "--run-id", run_id, "--root", str(tmp_path), "--json"],
    )
    exported = runner.invoke(
        app,
        [
            "export-idea-space-report",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--format",
            "markdown",
        ],
    )

    assert inspected.exit_code == 0, inspected.output
    payload = json.loads(inspected.output)
    assert payload["tree_present"] is True
    assert payload["diversity_score"] == "low"
    assert payload["effective_rank"] >= 0
    assert payload["publication_ready"] is False
    assert "PCA/low-rank OD-flow representation model" in (payload["recommended_mutation_axes"])
    assert exported.exit_code == 0, exported.output
    assert "artifact=runs/cli-idea-space/reports/idea-space-report-0001.md" in exported.output
    assert "publication_ready=false" in exported.output


def test_scientific_substrate_cli_builds_and_inspects_deterministic_run(
    tmp_path,
) -> None:
    run_id = "cli-scientific-substrate"
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id=run_id,
            domain="spatial heterogeneity in human geography",
            root=tmp_path,
        )
    )
    runner = CliRunner()

    build = runner.invoke(
        app,
        [
            "build-scientific-substrate",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--max-substrates",
            "2",
            "--json",
        ],
    )
    inspect = runner.invoke(
        app,
        [
            "inspect-scientific-substrate",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )

    assert build.exit_code == 0, build.output
    build_payload = json.loads(build.output)
    assert build_payload["scientific_substrate_present"] is True
    assert build_payload["build_report"]["substrate_count"] >= 2
    assert build_payload["build_report"]["publication_ready"] is False
    assert inspect.exit_code == 0, inspect.output
    inspected = json.loads(inspect.output)
    assert inspected["scientific_substrate_present"] is True
    assert inspected["substrate_count"] >= 2
    assert inspected["selected_substrate_title_optional"].startswith(
        "Region-Specific Distance Decay"
    )
    assert inspected["pca_low_rank_substrate_present"] is True
    assert inspected["publication_ready"] is False


def test_substrate_experiment_routing_commands_are_registered() -> None:
    route = CliRunner().invoke(app, ["route-substrate-experiment", "--help"])
    inspect = CliRunner().invoke(
        app,
        ["inspect-substrate-experiment-routing", "--help"],
    )
    tournament = CliRunner().invoke(app, ["run-substrate-tournament", "--help"])
    inspect_tournament = CliRunner().invoke(
        app,
        ["inspect-substrate-tournament", "--help"],
    )
    plan_mutations = CliRunner().invoke(app, ["plan-creative-mutations", "--help"])
    inspect_mutations = CliRunner().invoke(app, ["inspect-creative-mutations", "--help"])
    apply_mutations = CliRunner().invoke(app, ["apply-creative-mutations", "--help"])
    mutation_tournament = CliRunner().invoke(app, ["run-mutation-tournament", "--help"])
    inspect_mutation_tournament = CliRunner().invoke(
        app,
        ["inspect-mutation-tournament", "--help"],
    )
    creative_search = CliRunner().invoke(app, ["run-creative-search", "--help"])
    inspect_creative_search = CliRunner().invoke(
        app,
        ["inspect-creative-search", "--help"],
    )
    plan_generation = CliRunner().invoke(app, ["plan-generation-mutations", "--help"])
    inspect_generation = CliRunner().invoke(
        app,
        ["inspect-generation-mutations", "--help"],
    )
    apply_generation = CliRunner().invoke(
        app,
        ["apply-generation-mutations", "--help"],
    )
    assert route.exit_code == 0, route.output
    assert inspect.exit_code == 0, inspect.output
    assert tournament.exit_code == 0, tournament.output
    assert "--run-id" in tournament.output
    assert inspect_tournament.exit_code == 0, inspect_tournament.output
    assert "--json" in inspect_tournament.output
    assert plan_mutations.exit_code == 0, plan_mutations.output
    assert "--max-mutations" in plan_mutations.output
    assert inspect_mutations.exit_code == 0, inspect_mutations.output
    assert "--json" in inspect_mutations.output
    assert apply_mutations.exit_code == 0, apply_mutations.output
    assert "--max-mutations" in apply_mutations.output
    assert mutation_tournament.exit_code == 0, mutation_tournament.output
    assert "--run-id" in mutation_tournament.output
    assert inspect_mutation_tournament.exit_code == 0, inspect_mutation_tournament.output
    assert "--json" in inspect_mutation_tournament.output
    assert creative_search.exit_code == 0, creative_search.output
    assert "--max-cycles" in creative_search.output
    assert "--min-improvement" in creative_search.output
    assert inspect_creative_search.exit_code == 0, inspect_creative_search.output
    assert "--json" in inspect_creative_search.output
    assert plan_generation.exit_code == 0, plan_generation.output
    assert "--cycle-index" in plan_generation.output
    assert "--max-mutations" in plan_generation.output
    assert inspect_generation.exit_code == 0, inspect_generation.output
    assert "--json" in inspect_generation.output
    assert apply_generation.exit_code == 0, apply_generation.output
    assert "--max-mutations" in apply_generation.output


def test_human_review_cli_commands_are_registered() -> None:
    ingest = CliRunner().invoke(app, ["ingest-human-review", "--help"])
    inspect = CliRunner().invoke(app, ["inspect-human-review", "--help"])
    reconcile = CliRunner().invoke(app, ["reconcile-human-review", "--help"])
    inspect_reconciliation = CliRunner().invoke(
        app,
        ["inspect-human-review-reconciliation", "--help"],
    )
    ingest_requests = CliRunner().invoke(
        app,
        ["ingest-reviewer-change-requests", "--help"],
    )
    inspect_requests = CliRunner().invoke(
        app,
        ["inspect-reviewer-change-requests", "--help"],
    )

    assert ingest.exit_code == 0, ingest.output
    assert inspect.exit_code == 0, inspect.output
    assert reconcile.exit_code == 0, reconcile.output
    assert inspect_reconciliation.exit_code == 0, inspect_reconciliation.output
    assert ingest_requests.exit_code == 0, ingest_requests.output
    assert inspect_requests.exit_code == 0, inspect_requests.output
    assert "--run-id" in ingest.output
    assert "--review-file" in ingest.output
    assert "--run-id" in inspect.output
    assert "--json" in inspect.output
    assert "--run-id" in reconcile.output
    assert "--run-id" in inspect_reconciliation.output
    assert "--json" in inspect_reconciliation.output
    assert "--request-file" in ingest_requests.output
    assert "--json" in inspect_requests.output


def test_evidence_artifact_cli_commands_are_registered() -> None:
    runner = CliRunner()
    ingest_proof = runner.invoke(app, ["ingest-proof-artifact", "--help"])
    inspect_proof = runner.invoke(app, ["inspect-proof-artifacts", "--help"])
    ingest_experiment = runner.invoke(app, ["ingest-experiment-artifact", "--help"])
    inspect_experiment = runner.invoke(app, ["inspect-experiment-artifacts", "--help"])
    build_claim_map = runner.invoke(app, ["build-claim-evidence-map", "--help"])
    inspect_claim_map = runner.invoke(app, ["inspect-claim-evidence-map", "--help"])
    build_autonomous_plan = runner.invoke(
        app,
        ["build-autonomous-evidence-plan", "--help"],
    )
    inspect_autonomous_plan = runner.invoke(
        app,
        ["inspect-autonomous-evidence-plan", "--help"],
    )
    execute_autonomous_plan = runner.invoke(
        app,
        ["execute-autonomous-evidence-plan", "--help"],
    )
    inspect_autonomous_execution = runner.invoke(
        app,
        ["inspect-autonomous-plan-execution", "--help"],
    )
    execute_planned_specs = runner.invoke(
        app,
        ["execute-planned-specs", "--help"],
    )
    inspect_planned_specs = runner.invoke(
        app,
        ["inspect-planned-spec-execution", "--help"],
    )
    run_python_sandbox = runner.invoke(
        app,
        ["run-python-experiment-sandbox", "--help"],
    )
    inspect_python_sandbox = runner.invoke(
        app,
        ["inspect-python-experiment-sandbox", "--help"],
    )
    route_experiment_gaps = runner.invoke(app, ["route-experiment-gaps", "--help"])
    inspect_experiment_routing = runner.invoke(
        app,
        ["inspect-experiment-gap-routing", "--help"],
    )
    run_loop = runner.invoke(app, ["run-autonomous-loop", "--help"])
    inspect_loop = runner.invoke(app, ["inspect-autonomous-loop", "--help"])
    inspect_gap_history = runner.invoke(app, ["inspect-gap-attempt-history", "--help"])
    inspect_dedup = runner.invoke(app, ["inspect-planned-spec-dedup", "--help"])
    diversify_strategies = runner.invoke(app, ["diversify-gap-strategies", "--help"])
    inspect_strategies = runner.invoke(
        app,
        ["inspect-gap-strategy-diversification", "--help"],
    )
    refresh_manuscript = runner.invoke(
        app,
        ["refresh-evidence-aware-manuscript", "--help"],
    )

    assert ingest_proof.exit_code == 0, ingest_proof.output
    assert inspect_proof.exit_code == 0, inspect_proof.output
    assert ingest_experiment.exit_code == 0, ingest_experiment.output
    assert inspect_experiment.exit_code == 0, inspect_experiment.output
    assert build_claim_map.exit_code == 0, build_claim_map.output
    assert inspect_claim_map.exit_code == 0, inspect_claim_map.output
    assert build_autonomous_plan.exit_code == 0, build_autonomous_plan.output
    assert inspect_autonomous_plan.exit_code == 0, inspect_autonomous_plan.output
    assert execute_autonomous_plan.exit_code == 0, execute_autonomous_plan.output
    assert inspect_autonomous_execution.exit_code == 0, inspect_autonomous_execution.output
    assert diversify_strategies.exit_code == 0, diversify_strategies.output
    assert inspect_strategies.exit_code == 0, inspect_strategies.output
    assert execute_planned_specs.exit_code == 0, execute_planned_specs.output
    assert inspect_planned_specs.exit_code == 0, inspect_planned_specs.output
    assert run_python_sandbox.exit_code == 0, run_python_sandbox.output
    assert inspect_python_sandbox.exit_code == 0, inspect_python_sandbox.output
    assert route_experiment_gaps.exit_code == 0, route_experiment_gaps.output
    assert inspect_experiment_routing.exit_code == 0, inspect_experiment_routing.output
    assert run_loop.exit_code == 0, run_loop.output
    assert inspect_loop.exit_code == 0, inspect_loop.output
    assert inspect_gap_history.exit_code == 0, inspect_gap_history.output
    assert inspect_dedup.exit_code == 0, inspect_dedup.output
    assert refresh_manuscript.exit_code == 0, refresh_manuscript.output
    assert "--run-id" in ingest_proof.output
    assert "--proof-file" in ingest_proof.output
    assert "--run-id" in inspect_proof.output
    assert "--json" in inspect_proof.output
    assert "--execution-mode" in execute_autonomous_plan.output
    assert "--executor-backend" in execute_autonomous_plan.output
    assert "--json" in inspect_autonomous_execution.output
    assert "--execution-mode" in execute_planned_specs.output
    assert "--spec-executor-backend" in execute_planned_specs.output
    assert "--python-sandbox-backend" in execute_planned_specs.output
    assert "--json" in inspect_planned_specs.output
    assert "--experiment-spec" in run_python_sandbox.output
    assert "--sandbox-backend" in run_python_sandbox.output
    assert "--execution-mode" in run_python_sandbox.output
    assert "--json" in inspect_python_sandbox.output
    assert "--routing-backend" in route_experiment_gaps.output
    assert "--json" in inspect_experiment_routing.output
    assert "--loop-backend" in run_loop.output
    assert "--max-iterations" in run_loop.output
    assert "--max-attempts-per-gap" in run_loop.output
    assert "--enable-experiment-routing" in run_loop.output
    assert "--python-sandbox-backend" in run_loop.output
    assert "--max-sandbox-runs-per-loop" in run_loop.output
    assert "--max-sandbox-runs-per-ite" in run_loop.output
    assert "--json" in inspect_loop.output
    assert "--json" in inspect_gap_history.output
    assert "--json" in inspect_dedup.output
    assert "--run-id" in ingest_experiment.output
    assert "--experiment-file" in ingest_experiment.output
    assert "--run-id" in inspect_experiment.output
    assert "--json" in inspect_experiment.output
    assert "--run-id" in build_claim_map.output
    assert "--json" in build_claim_map.output
    assert "--run-id" in inspect_claim_map.output
    assert "--json" in inspect_claim_map.output
    assert "--planner-backend" in build_autonomous_plan.output
    assert "--run-id" in inspect_autonomous_plan.output
    assert "--json" in inspect_autonomous_plan.output
    assert "--run-id" in refresh_manuscript.output
    assert "--json" in refresh_manuscript.output


def test_autonomous_plan_execution_cli_dry_run_and_inspection(tmp_path) -> None:
    run_id = "cli-autonomous-execution"
    _prepare_paper_bundle(tmp_path, run_id=run_id, revised=True, release=True)
    runner = CliRunner()
    claim_map = runner.invoke(
        app,
        [
            "build-claim-evidence-map",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    plan = runner.invoke(
        app,
        [
            "build-autonomous-evidence-plan",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--planner-backend",
            "deterministic",
            "--json",
        ],
    )
    execute = runner.invoke(
        app,
        [
            "execute-autonomous-evidence-plan",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--execution-mode",
            "dry-run",
            "--executor-backend",
            "deterministic",
            "--json",
        ],
    )
    inspect_json = runner.invoke(
        app,
        [
            "inspect-autonomous-plan-execution",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    inspect_human = runner.invoke(
        app,
        [
            "inspect-autonomous-plan-execution",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
        ],
    )
    lint_result = runner.invoke(
        app,
        [
            "lint-paper-bundle",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    paper_bundle = runner.invoke(
        app,
        ["inspect-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )
    reviewer_summary = runner.invoke(
        app,
        [
            "inspect-reviewer-summary",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )

    assert claim_map.exit_code == 0, claim_map.output
    assert plan.exit_code == 0, plan.output
    assert execute.exit_code == 0, execute.output
    assert inspect_json.exit_code == 0, inspect_json.output
    assert inspect_human.exit_code == 0, inspect_human.output
    assert lint_result.exit_code == 0, lint_result.output
    assert paper_bundle.exit_code == 0, paper_bundle.output
    assert reviewer_summary.exit_code == 0, reviewer_summary.output
    payload = json.loads(execute.output)
    inspected = json.loads(inspect_json.output)
    lint = json.loads(lint_result.output)
    reviewer = json.loads(reviewer_summary.output)
    assert payload["autonomous_plan_execution"]["execution_status"] == ("dry_run_completed")
    assert payload["publication_ready"] is False
    assert inspected["autonomous_execution_count"] == 1
    assert inspected["latest_autonomous_execution_mode"] == "dry_run"
    assert "Autonomous plan execution" in inspect_human.output
    assert "Autonomous execution: present" in paper_bundle.output
    assert "Latest execution mode: dry_run" in paper_bundle.output
    assert lint["autonomous_execution_present"] is True
    assert lint["autonomous_execution_count"] == 1
    assert lint["latest_autonomous_execution_mode"] == "dry_run"
    assert lint["publication_ready"] is False
    assert reviewer["autonomous_execution_present"] is True
    assert reviewer["latest_autonomous_execution_mode"] == "dry_run"
    assert reviewer["publication_ready"] is False


def test_planned_spec_execution_cli_apply_and_inspection(tmp_path) -> None:
    run_id = "cli-planned-spec-execution"
    _prepare_paper_bundle(tmp_path, run_id=run_id, revised=True, release=True)
    reports = tmp_path / "runs" / run_id / "reports"
    _write_cli_planned_experiment_spec(reports, run_id=run_id)
    runner = CliRunner()
    dry_run = runner.invoke(
        app,
        [
            "execute-planned-specs",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--execution-mode",
            "dry-run",
            "--spec-executor-backend",
            "deterministic_local",
            "--json",
        ],
    )
    apply = runner.invoke(
        app,
        [
            "execute-planned-specs",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--execution-mode",
            "apply",
            "--spec-executor-backend",
            "deterministic_local",
            "--json",
        ],
    )
    inspect_json = runner.invoke(
        app,
        [
            "inspect-planned-spec-execution",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    inspect_human = runner.invoke(
        app,
        ["inspect-planned-spec-execution", "--root", str(tmp_path), "--run-id", run_id],
    )
    lint_result = runner.invoke(
        app,
        ["lint-paper-bundle", "--root", str(tmp_path), "--run-id", run_id, "--json"],
    )
    paper_bundle = runner.invoke(
        app,
        ["inspect-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )

    assert dry_run.exit_code == 0, dry_run.output
    assert apply.exit_code == 0, apply.output
    assert inspect_json.exit_code == 0, inspect_json.output
    assert inspect_human.exit_code == 0, inspect_human.output
    assert lint_result.exit_code == 0, lint_result.output
    assert paper_bundle.exit_code == 0, paper_bundle.output
    dry_payload = json.loads(dry_run.output)
    apply_payload = json.loads(apply.output)
    inspected = json.loads(inspect_json.output)
    lint = json.loads(lint_result.output)
    assert dry_payload["planned_spec_execution"]["execution_status"] == "dry_run_completed"
    assert apply_payload["planned_spec_execution"]["execution_status"] == "completed"
    assert apply_payload["planned_spec_execution"]["experiment_artifacts_created"] == 1
    assert inspected["planned_spec_execution_count"] == 2
    assert inspected["latest_planned_spec_execution_mode"] == "apply"
    assert "Planned spec execution" in inspect_human.output
    assert "Planned spec execution: present" in paper_bundle.output
    assert lint["planned_spec_execution_present"] is True
    assert lint["planned_spec_execution_count"] == 2
    assert lint["latest_planned_spec_execution_mode"] == "apply"
    assert lint["experiment_artifacts_created"] == 1
    assert lint["publication_ready"] is False


def test_autonomous_loop_cli_run_and_inspection(tmp_path) -> None:
    run_id = "cli-autonomous-loop"
    _prepare_paper_bundle(tmp_path, run_id=run_id, revised=True, release=True)
    runner = CliRunner()

    run_result = runner.invoke(
        app,
        [
            "run-autonomous-loop",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--loop-backend",
            "deterministic",
            "--max-iterations",
            "1",
            "--json",
        ],
    )
    inspect_json = runner.invoke(
        app,
        ["inspect-autonomous-loop", "--root", str(tmp_path), "--run-id", run_id, "--json"],
    )
    inspect_human = runner.invoke(
        app,
        ["inspect-autonomous-loop", "--root", str(tmp_path), "--run-id", run_id],
    )
    paper_bundle = runner.invoke(
        app,
        ["inspect-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )
    lint_result = runner.invoke(
        app,
        ["lint-paper-bundle", "--root", str(tmp_path), "--run-id", run_id, "--json"],
    )

    assert run_result.exit_code == 0, run_result.output
    assert inspect_json.exit_code == 0, inspect_json.output
    assert inspect_human.exit_code == 0, inspect_human.output
    assert paper_bundle.exit_code == 0, paper_bundle.output
    assert lint_result.exit_code == 0, lint_result.output
    payload = json.loads(run_result.output)
    inspected = json.loads(inspect_json.output)
    lint = json.loads(lint_result.output)
    assert payload["autonomous_loop_present"] is True
    assert payload["autonomous_loop"]["publication_ready"] is False
    assert inspected["autonomous_loop_count"] == 1
    assert inspected["latest_autonomous_loop_iterations_completed"] >= 1
    assert inspected["autonomous_loop_terminal_state"]
    assert "Terminal state:" in inspect_human.output
    assert "Autonomous loop" in inspect_human.output
    assert "Autonomous loop: present" in paper_bundle.output
    assert "Terminal state:" in paper_bundle.output
    assert lint["autonomous_loop_present"] is True
    assert lint["autonomous_loop_count"] == 1
    assert "autonomous_loop_terminal_state" in lint
    assert "autonomous_loop_stopped_before_max_iterations" in lint
    assert lint["publication_ready"] is False


def test_run_llm_paper_accepts_fake_claim_adjudicator_preflight_without_mutation(
    tmp_path,
) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-llm-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "adjudicator-preflight",
            "--domain",
            "human geography",
            "--candidate-backend",
            "fake",
            "--reviewer-backend",
            "fake",
            "--prose-backend",
            "fake",
            "--claim-adjudicator-backend",
            "fake",
            "--claim-adjudicator-model",
            "test-model",
            "--source-relevance-adjudicator-backend",
            "fake",
            "--source-relevance-adjudicator-model",
            "test-source-model",
            "--quality-repair-backend",
            "fake",
            "--quality-repair-model",
            "test-quality-model",
            "--max-claim-adjudication-calls",
            "2",
            "--max-source-relevance-adjudication-calls",
            "3",
            "--max-quality-repair-calls",
            "4",
            "--preflight-only",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    preflight = payload["preflight_summary"]
    assert preflight["claim_adjudicator_backend"] == "fake"
    assert preflight["claim_adjudicator_model"] == "test-model"
    assert preflight["source_relevance_adjudicator_backend"] == "fake"
    assert preflight["source_relevance_adjudicator_model"] == "test-source-model"
    assert preflight["quality_repair_backend"] == "fake"
    assert preflight["quality_repair_model"] == "test-quality-model"
    assert preflight["source_relevance_adjudication_calls"] == 0
    assert preflight["quality_repair_calls"] == 0
    assert not (tmp_path / "runs" / "adjudicator-preflight").exists()


def test_preflight_budget_plans_openai_source_relevance_calls() -> None:
    summary = build_llm_orchestration_preflight_summary(
        LLMOrchestrationConfig(
            run_id="source-relevance-preflight",
            domain="human geography",
            candidate_backend="fake",
            reviewer_backend="fake",
            prose_backend="fake",
            allow_external_calls=True,
            enable_retrieval=True,
            retrieval_backend="local",
            retrieval_local_path="tests/fixtures/retrieval/openalex_style_human_geography_sources.json",
            source_relevance_adjudicator_backend="openai",
            source_relevance_adjudicator_model="test-source-model",
            budget=LLMBudgetConfig(
                max_total_calls=4,
                max_source_relevance_adjudication_calls=4,
                max_total_input_tokens=4000,
                max_total_output_tokens=2000,
                max_estimated_cost_usd=1.0,
            ),
        )
    )

    assert summary["source_relevance_adjudicator_backend"] == "openai"
    assert summary["source_relevance_adjudication_calls"] == 4
    assert summary["estimated_max_calls"] == 4


def test_hybrid_preflight_does_not_charge_fake_candidate_or_reviewer_calls() -> None:
    summary = build_llm_orchestration_preflight_summary(
        LLMOrchestrationConfig(
            run_id="hybrid-budget-preflight",
            domain="human geography",
            candidate_backend="fake",
            reviewer_backend="fake",
            prose_backend="openai",
            allow_external_calls=True,
            enable_retrieval=True,
            retrieval_backend="local",
            retrieval_local_path="tests/fixtures/retrieval/openalex_style_human_geography_sources.json",
            citation_policy="registry-only",
            claim_adjudicator_backend="openai",
            source_relevance_adjudicator_backend="openai",
            budget=LLMBudgetConfig(
                max_total_calls=35,
                max_candidate_generation_calls=0,
                max_review_calls=0,
                max_prose_calls=12,
                max_claim_adjudication_calls=12,
                max_source_relevance_adjudication_calls=4,
                max_total_input_tokens=40000,
                max_total_output_tokens=20000,
                max_estimated_cost_usd=1.0,
            ),
        )
    )

    assert summary["candidate_backend"] == "fake"
    assert summary["reviewer_backend"] == "fake"
    assert summary["prose_backend"] == "openai"
    assert summary["candidate_generation_calls"] == 0
    assert summary["review_calls"] == 0
    assert summary["prose_calls"] > 0
    assert summary["claim_adjudication_calls"] == 12
    assert summary["source_relevance_adjudication_calls"] == 4
    assert summary["estimated_max_calls"] == (summary["prose_calls"] + 12 + 4)


def test_zero_candidate_call_budget_blocks_only_real_candidate_backend(tmp_path) -> None:
    with pytest.raises(LLMOrchestrationError, match="max_candidate_generation_calls"):
        run_llm_paper_orchestration(
            config=LLMOrchestrationConfig(
                run_id="openai-candidate-budget-block",
                domain="human geography",
                candidate_backend="openai",
                reviewer_backend="fake",
                prose_backend="fake",
                allow_external_calls=True,
                budget=LLMBudgetConfig(
                    max_total_calls=10,
                    max_candidate_generation_calls=0,
                    max_total_input_tokens=10000,
                    max_total_output_tokens=5000,
                    max_estimated_cost_usd=1.0,
                ),
            ),
            root=tmp_path,
            preflight_only=True,
        )

    fake_summary = build_llm_orchestration_preflight_summary(
        LLMOrchestrationConfig(
            run_id="fake-candidate-budget-ok",
            domain="human geography",
            candidate_backend="fake",
            reviewer_backend="fake",
            prose_backend="fake",
            allow_external_calls=True,
            budget=LLMBudgetConfig(
                max_total_calls=0,
                max_candidate_generation_calls=0,
                max_review_calls=0,
                max_total_input_tokens=10000,
                max_total_output_tokens=5000,
                max_estimated_cost_usd=1.0,
            ),
        )
    )
    assert fake_summary["candidate_generation_calls"] == 0
    assert fake_summary["review_calls"] == 0
    assert fake_summary["estimated_max_calls"] == 0


def test_preflight_budget_plans_openai_quality_repair_calls() -> None:
    summary = build_llm_orchestration_preflight_summary(
        LLMOrchestrationConfig(
            run_id="quality-repair-preflight",
            domain="human geography",
            candidate_backend="fake",
            reviewer_backend="fake",
            prose_backend="fake",
            allow_external_calls=True,
            quality_repair_backend="openai",
            quality_repair_model="test-quality-model",
            budget=LLMBudgetConfig(
                max_total_calls=2,
                max_quality_repair_calls=2,
                max_total_input_tokens=2000,
                max_total_output_tokens=1000,
                max_estimated_cost_usd=1.0,
            ),
        )
    )

    assert summary["quality_repair_backend"] == "openai"
    assert summary["quality_repair_model"] == "test-quality-model"
    assert summary["quality_repair_calls"] == 2
    assert summary["estimated_max_calls"] == 2


def test_deterministic_quality_repair_preflight_is_local_without_external_gate(
    tmp_path,
) -> None:
    result = CliRunner().invoke(
        app,
        [
            "run-llm-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "quality-repair-local-preflight",
            "--domain",
            "human geography",
            "--candidate-backend",
            "fake",
            "--reviewer-backend",
            "fake",
            "--prose-backend",
            "fake",
            "--quality-repair-backend",
            "deterministic",
            "--preflight-only",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["preflight_summary"]["quality_repair_backend"] == "deterministic"
    assert payload["preflight_summary"]["quality_repair_calls"] == 0
    assert payload["preflight_summary"]["estimated_max_calls"] == 0
    assert not (tmp_path / "runs" / "quality-repair-local-preflight").exists()


def test_lint_paper_bundle_cli_is_registered() -> None:
    result = CliRunner().invoke(app, ["lint-paper-bundle", "--help"])

    assert result.exit_code == 0, result.output
    assert "--run-id" in result.output
    assert "--json" in result.output
    assert "--min-words" in result.output
    assert "--min-avg-words-per-section" in result.output
    assert "--min-citation-markers" in result.output


def test_inspect_paper_bundle_with_revised_artifacts_is_read_only(tmp_path) -> None:
    run_id = "inspect-paper-revised"
    _prepare_paper_bundle(tmp_path, run_id=run_id, revised=True, release=True)
    before = _run_file_snapshot(tmp_path, run_id)

    json_result = CliRunner().invoke(
        app,
        [
            "inspect-paper-bundle",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )

    assert json_result.exit_code == 0, json_result.output
    assert _run_file_snapshot(tmp_path, run_id) == before
    payload = json.loads(json_result.output)
    assert payload["run_id"] == run_id
    assert payload["paper_exists"] is True
    assert payload["revised_paper_exists"] is True
    assert payload["complete_manuscript_draft_exists"] is True
    assert payload["revised_manuscript_draft_exists"] is True
    assert payload["latex_exists"] is True
    assert payload["revised_latex_exists"] is True
    assert payload["safe_repair_report_exists"] is True
    assert payload["release_report_exists"] is True
    assert payload["reviewer_bundle_summary_present"] is True
    assert payload["reviewer_summary_evidence_gap_count"] > 0
    assert payload["reviewer_summary_human_checklist_count"] > 0
    assert payload["reviewer_summary_recommended_action_count"] > 0
    assert payload["generation_report_exists"] is True
    assert payload["primary_artifact_to_read"].endswith("reports/revised-manuscript-draft.md")
    assert payload["primary_latex_to_read"].endswith("latex/revised-paper.tex")
    assert payload["line_count"] > 0
    assert payload["word_count"] > 0
    assert payload["section_count"] > 0
    assert payload["main_body_section_count"] > 0
    assert payload["appendix_section_count"] >= 2
    assert payload["total_heading_count"] == payload["section_count"]
    assert payload["section_headings_detected"]
    assert payload["title_detected"]
    assert payload["safe_repair_applied_count"] >= 0
    assert payload["citations_present"] is False
    assert payload["artifacts"]["revised_paper"].endswith("latex/revised-paper.tex")

    human_result = CliRunner().invoke(
        app,
        ["inspect-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )

    assert human_result.exit_code == 0, human_result.output
    assert f"Paper bundle: {run_id}" in human_result.output
    assert "Primary draft: revised-manuscript-draft.md" in human_result.output
    assert "Release: ReadyForHumanReviewWithWarnings" in human_result.output
    assert "Safe repair: present" in human_result.output
    assert "Reviewer summary: present" in human_result.output
    assert "Reviewer summary status: present" in human_result.output
    assert "Evidence gaps:" in human_result.output
    assert "Human-review checklist items:" in human_result.output
    assert "Main-body sections:" in human_result.output
    assert "Appendix sections:" in human_result.output
    assert "Total headings:" in human_result.output
    assert "Citations: absent" in human_result.output
    assert "Artifacts:" in human_result.output
    assert "- revised manuscript:" in human_result.output
    assert "- revised latex:" in human_result.output
    assert "- release report:" in human_result.output
    assert "- reviewer summary:" in human_result.output
    assert _run_file_snapshot(tmp_path, run_id) == before


def test_inspect_reviewer_summary_command_is_read_only(tmp_path) -> None:
    run_id = "inspect-reviewer-summary"
    _prepare_paper_bundle(tmp_path, run_id=run_id, revised=True, release=True)
    before = _run_file_snapshot(tmp_path, run_id)

    json_result = CliRunner().invoke(
        app,
        [
            "inspect-reviewer-summary",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )

    assert json_result.exit_code == 0, json_result.output
    assert _run_file_snapshot(tmp_path, run_id) == before
    payload = json.loads(json_result.output)
    assert payload["run_id"] == run_id
    assert payload["publication_ready"] is False
    assert payload["release_status"] == "ReadyForHumanReviewWithWarnings"
    assert payload["claim_support_status"] == "clean"
    assert payload["citation_status"] in {"registry-backed", "no-citations-required"}
    assert len(payload["evidence_gaps"]) > 0
    assert len(payload["human_review_checklist"]) > 0
    assert len(payload["recommended_next_actions"]) > 0
    assert payload["creates_scientific_validation"] is False
    assert payload["implies_publication_readiness"] is False
    assert payload["is_verification_evidence"] is False

    human_result = CliRunner().invoke(
        app,
        ["inspect-reviewer-summary", "--root", str(tmp_path), "--run-id", run_id],
    )

    assert human_result.exit_code == 0, human_result.output
    assert f"Reviewer summary: {run_id}" in human_result.output
    assert "Publication ready: false" in human_result.output
    assert "Evidence gaps:" in human_result.output
    assert "Human-review checklist:" in human_result.output
    assert "Recommended next actions:" in human_result.output
    assert _run_file_snapshot(tmp_path, run_id) == before

    lint_result = CliRunner().invoke(
        app,
        [
            "lint-paper-bundle",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    assert lint_result.exit_code == 0, lint_result.output
    lint_payload = json.loads(lint_result.output)
    assert lint_payload["reviewer_bundle_summary_present"] is True
    assert lint_payload["reviewer_summary_evidence_gap_count"] > 0
    assert lint_payload["reviewer_summary_human_checklist_count"] > 0
    assert lint_payload["reviewer_summary_recommended_action_count"] > 0
    assert _run_file_snapshot(tmp_path, run_id) == before


def test_human_review_intake_and_inspection_cli(tmp_path) -> None:
    run_id = "inspect-human-review"
    _prepare_paper_bundle(tmp_path, run_id=run_id, revised=True, release=True)
    review_file = _write_human_review_fixture(tmp_path, run_id=run_id)

    ingest_result = CliRunner().invoke(
        app,
        [
            "ingest-human-review",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--review-file",
            str(review_file),
            "--json",
        ],
    )

    assert ingest_result.exit_code == 0, ingest_result.output
    ingest_payload = json.loads(ingest_result.output)
    assert ingest_payload["publication_ready"] is False
    assert ingest_payload["creates_scientific_validation"] is False
    assert ingest_payload["is_verification_evidence"] is False
    assert (
        ingest_payload["human_review_artifact"]["review_status"]
        == "reviewed_ready_for_evidence_generation"
    )

    inspect_json = CliRunner().invoke(
        app,
        [
            "inspect-human-review",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    inspect_human = CliRunner().invoke(
        app,
        ["inspect-human-review", "--root", str(tmp_path), "--run-id", run_id],
    )
    reviewer_summary = CliRunner().invoke(
        app,
        [
            "inspect-reviewer-summary",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    paper_bundle = CliRunner().invoke(
        app,
        ["inspect-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )
    lint_result = CliRunner().invoke(
        app,
        [
            "lint-paper-bundle",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )

    assert inspect_json.exit_code == 0, inspect_json.output
    assert inspect_human.exit_code == 0, inspect_human.output
    assert reviewer_summary.exit_code == 0, reviewer_summary.output
    assert paper_bundle.exit_code == 0, paper_bundle.output
    assert lint_result.exit_code == 0, lint_result.output

    inspected = json.loads(inspect_json.output)
    assert inspected["human_review_artifact_present"] is True
    assert inspected["review_status"] == "reviewed_ready_for_evidence_generation"
    assert inspected["human_review_blocking_concern_count"] == 0
    assert inspected["human_review_requested_change_count"] == 0
    assert inspected["publication_ready"] is False
    assert "Status: reviewed_ready_for_evidence_generation" in inspect_human.output
    assert "Publication ready: false" in inspect_human.output

    summary = json.loads(reviewer_summary.output)
    assert summary["human_review_artifact_present"] is True
    assert summary["human_review_status"] == "reviewed_ready_for_evidence_generation"
    assert not any("No human-review artifact" in gap for gap in summary["evidence_gaps"])
    assert any("No proof artifact" in gap for gap in summary["evidence_gaps"])
    assert any("No experiment artifact" in gap for gap in summary["evidence_gaps"])
    assert "Human review: present" in paper_bundle.output
    assert "Human review status: reviewed_ready_for_evidence_generation" in (paper_bundle.output)

    lint_payload = json.loads(lint_result.output)
    assert lint_payload["human_review_artifact_present"] is True
    assert lint_payload["human_review_status"] == "reviewed_ready_for_evidence_generation"
    assert lint_payload["human_review_blocking_concern_count"] == 0
    assert lint_payload["human_review_requested_change_count"] == 0
    assert lint_payload["publication_ready"] is False


def test_evidence_artifact_intake_and_inspection_cli(tmp_path) -> None:
    run_id = "inspect-evidence-artifacts"
    _prepare_paper_bundle(tmp_path, run_id=run_id, revised=True, release=True)
    proof_file = _write_proof_artifact_fixture(tmp_path, run_id=run_id)
    experiment_file = _write_experiment_artifact_fixture(tmp_path, run_id=run_id)
    runner = CliRunner()

    proof_ingest = runner.invoke(
        app,
        [
            "ingest-proof-artifact",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--proof-file",
            str(proof_file),
            "--json",
        ],
    )
    experiment_ingest = runner.invoke(
        app,
        [
            "ingest-experiment-artifact",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--experiment-file",
            str(experiment_file),
            "--json",
        ],
    )

    assert proof_ingest.exit_code == 0, proof_ingest.output
    assert experiment_ingest.exit_code == 0, experiment_ingest.output
    proof_payload = json.loads(proof_ingest.output)
    experiment_payload = json.loads(experiment_ingest.output)
    assert proof_payload["publication_ready"] is False
    assert proof_payload["is_verification_evidence"] is True
    assert proof_payload["creates_scientific_validation"] is False
    assert experiment_payload["publication_ready"] is False
    assert experiment_payload["is_verification_evidence"] is False
    assert experiment_payload["creates_scientific_validation"] is False

    claim_map_build = runner.invoke(
        app,
        [
            "build-claim-evidence-map",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    assert claim_map_build.exit_code == 0, claim_map_build.output
    claim_map_build_payload = json.loads(claim_map_build.output)
    assert claim_map_build_payload["publication_ready"] is False
    assert claim_map_build_payload["claim_evidence_map_present"] is True
    autonomous_plan_build = runner.invoke(
        app,
        [
            "build-autonomous-evidence-plan",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--planner-backend",
            "deterministic",
            "--json",
        ],
    )
    assert autonomous_plan_build.exit_code == 0, autonomous_plan_build.output
    autonomous_plan_payload = json.loads(autonomous_plan_build.output)
    assert autonomous_plan_payload["publication_ready"] is False
    assert autonomous_plan_payload["autonomous_evidence_plan_present"] is True
    assert (
        autonomous_plan_payload["autonomous_evidence_gap_plan"]["requires_human_intervention"]
        is False
    )
    inspect_autonomous_plan_json = runner.invoke(
        app,
        [
            "inspect-autonomous-evidence-plan",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    inspect_autonomous_plan_human = runner.invoke(
        app,
        [
            "inspect-autonomous-evidence-plan",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
        ],
    )
    assert inspect_autonomous_plan_json.exit_code == 0, inspect_autonomous_plan_json.output
    assert inspect_autonomous_plan_human.exit_code == 0, inspect_autonomous_plan_human.output
    autonomous_inspected = json.loads(inspect_autonomous_plan_json.output)
    assert autonomous_inspected["autonomous_plan_item_count"] > 0
    assert autonomous_inspected["autonomous_human_intervention_required"] is False
    assert "Autonomous evidence-gap plan" in inspect_autonomous_plan_human.output
    refresh = runner.invoke(
        app,
        [
            "refresh-evidence-aware-manuscript",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--evidence-aware-refresh-backend",
            "deterministic",
            "--json",
        ],
    )
    assert refresh.exit_code == 0, refresh.output
    refresh_payload = json.loads(refresh.output)
    assert refresh_payload["publication_ready"] is False
    assert refresh_payload["evidence_aware_refresh_report"]["proof_language_inserted"] is True
    assert refresh_payload["evidence_aware_refresh_report"]["experiment_language_inserted"] is False

    inspect_proof_json = runner.invoke(
        app,
        [
            "inspect-proof-artifacts",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    inspect_proof_human = runner.invoke(
        app,
        ["inspect-proof-artifacts", "--root", str(tmp_path), "--run-id", run_id],
    )
    inspect_experiment_json = runner.invoke(
        app,
        [
            "inspect-experiment-artifacts",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    inspect_experiment_human = runner.invoke(
        app,
        [
            "inspect-experiment-artifacts",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
        ],
    )
    inspect_claim_map_json = runner.invoke(
        app,
        [
            "inspect-claim-evidence-map",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    inspect_claim_map_human = runner.invoke(
        app,
        ["inspect-claim-evidence-map", "--root", str(tmp_path), "--run-id", run_id],
    )
    reviewer_summary = runner.invoke(
        app,
        [
            "inspect-reviewer-summary",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    paper_bundle = runner.invoke(
        app,
        ["inspect-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )
    lint_result = runner.invoke(
        app,
        [
            "lint-paper-bundle",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )

    assert inspect_proof_json.exit_code == 0, inspect_proof_json.output
    assert inspect_proof_human.exit_code == 0, inspect_proof_human.output
    assert inspect_experiment_json.exit_code == 0, inspect_experiment_json.output
    assert inspect_experiment_human.exit_code == 0, inspect_experiment_human.output
    assert inspect_claim_map_json.exit_code == 0, inspect_claim_map_json.output
    assert inspect_claim_map_human.exit_code == 0, inspect_claim_map_human.output
    assert reviewer_summary.exit_code == 0, reviewer_summary.output
    assert paper_bundle.exit_code == 0, paper_bundle.output
    assert lint_result.exit_code == 0, lint_result.output

    proof_summary = json.loads(inspect_proof_json.output)
    assert proof_summary["proof_artifact_count"] == 1
    assert proof_summary["formal_verification_passed_count"] == 1
    assert proof_summary["proof_evidence_gap_present"] is False
    assert "Formal verification artifacts passed: 1" in inspect_proof_human.output

    experiment_summary = json.loads(inspect_experiment_json.output)
    assert experiment_summary["experiment_artifact_count"] == 1
    assert experiment_summary["completed_experiment_count"] == 1
    assert experiment_summary["experiment_evidence_gap_present"] is False
    assert "Completed experiments: 1" in inspect_experiment_human.output

    claim_map_summary = json.loads(inspect_claim_map_json.output)
    assert claim_map_summary["claim_evidence_map_present"] is True
    assert claim_map_summary["proof_supported_claim_count"] >= 1
    assert claim_map_summary["experiment_supported_claim_count"] >= 0
    assert claim_map_summary["citation_supported_claim_count"] >= 0
    assert "Proof-supported claims:" in inspect_claim_map_human.output

    summary = json.loads(reviewer_summary.output)
    assert summary["proof_artifact_count"] == 1
    assert summary["formal_verification_artifact_count"] == 1
    assert summary["experiment_artifact_count"] == 1
    assert summary["completed_experiment_count"] == 1
    assert summary["claim_evidence_map_present"] is True
    assert summary["proof_supported_claim_count"] >= 1
    assert summary["experiment_supported_claim_count"] >= 0
    assert summary["publication_ready"] is False
    assert not any("No formal proof artifact" in gap for gap in summary["evidence_gaps"])
    assert not any("No completed experiment artifact" in gap for gap in summary["evidence_gaps"])

    assert "Proof artifacts: 1" in paper_bundle.output
    assert "Formal verification artifacts passed: 1" in paper_bundle.output
    assert "Experiment artifacts: 1" in paper_bundle.output
    assert "Completed experiments: 1" in paper_bundle.output
    assert "Claim-evidence map: present" in paper_bundle.output
    assert "Proof-supported claims:" in paper_bundle.output
    assert "Evidence-aware refresh: present" in paper_bundle.output
    assert "Proof language inserted: true" in paper_bundle.output
    assert "Experiment language inserted: false" in paper_bundle.output

    lint_payload = json.loads(lint_result.output)
    assert lint_payload["proof_artifact_count"] == 1
    assert lint_payload["formal_verification_passed_count"] == 1
    assert lint_payload["experiment_artifact_count"] == 1
    assert lint_payload["completed_experiment_count"] == 1
    assert lint_payload["proof_evidence_gap_present"] is False
    assert lint_payload["experiment_evidence_gap_present"] is False
    assert lint_payload["claim_evidence_map_present"] is True
    assert lint_payload["proof_supported_claim_count"] >= 1
    assert lint_payload["experiment_supported_claim_count"] >= 0
    assert lint_payload["evidence_aware_refresh_report_present"] is True
    assert lint_payload["evidence_aware_refresh_backend"] == "deterministic"
    assert lint_payload["proof_language_inserted"] is True
    assert lint_payload["experiment_language_inserted"] is False
    assert lint_payload["autonomous_evidence_plan_present"] is True
    assert lint_payload["autonomous_plan_item_count"] > 0
    assert lint_payload["autonomous_human_intervention_required"] is False
    assert lint_payload["claim_evidence_map_rechecked_after_refresh"] is True
    assert lint_payload["claim_support_rechecked_after_refresh"] is True
    assert lint_payload["citation_safety_rechecked_after_refresh"] is True
    assert lint_payload["publication_ready"] is False

    review_file = _write_human_review_fixture(tmp_path, run_id=run_id)
    review_ingest = runner.invoke(
        app,
        [
            "ingest-human-review",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--review-file",
            str(review_file),
            "--json",
        ],
    )
    assert review_ingest.exit_code == 0, review_ingest.output
    reconciliation = runner.invoke(
        app,
        [
            "reconcile-human-review",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    assert reconciliation.exit_code == 0, reconciliation.output
    reconciliation_payload = json.loads(reconciliation.output)
    assert reconciliation_payload["publication_ready"] is False
    assert (
        reconciliation_payload["human_review_reconciliation"]["reconciliation_status"]
        == "no_action_needed"
    )

    inspect_reconciliation_json = runner.invoke(
        app,
        [
            "inspect-human-review-reconciliation",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    inspect_reconciliation_human = runner.invoke(
        app,
        [
            "inspect-human-review-reconciliation",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
        ],
    )
    assert inspect_reconciliation_json.exit_code == 0
    assert inspect_reconciliation_human.exit_code == 0
    reconciled = json.loads(inspect_reconciliation_json.output)
    assert reconciled["human_review_reconciliation_present"] is True
    assert reconciled["applied_change_count"] == 0
    assert reconciled["publication_ready"] is False
    assert "Status: no_action_needed" in inspect_reconciliation_human.output

    post_bundle = runner.invoke(
        app,
        ["inspect-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )
    post_lint = runner.invoke(
        app,
        [
            "lint-paper-bundle",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    assert post_bundle.exit_code == 0, post_bundle.output
    assert post_lint.exit_code == 0, post_lint.output
    assert "Human-review reconciliation: present" in post_bundle.output
    post_lint_payload = json.loads(post_lint.output)
    assert post_lint_payload["human_review_reconciliation_present"] is True
    assert post_lint_payload["human_review_reconciliation_status"] == "no_action_needed"


def test_inspect_paper_bundle_without_revised_artifacts_degrades_gracefully(tmp_path) -> None:
    run_id = "inspect-paper-unrevised"
    _prepare_paper_bundle(tmp_path, run_id=run_id, revised=False, release=False)

    summary = inspect_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert summary["paper_exists"] is True
    assert summary["revised_paper_exists"] is False
    assert summary["complete_manuscript_draft_exists"] is True
    assert summary["revised_manuscript_draft_exists"] is False
    assert summary["safe_repair_report_exists"] is False
    assert summary["release_report_exists"] is False
    assert summary["generation_report_exists"] is True
    assert summary["primary_artifact_to_read"].endswith("reports/complete-manuscript-draft.md")
    assert summary["primary_latex_to_read"].endswith("latex/paper.tex")
    assert "safe_repair_report" not in summary["artifacts"]
    assert summary["release_status"] is None


def test_inspect_paper_bundle_reports_registry_backed_citations(tmp_path) -> None:
    run_id = "inspect-paper-citations"
    markdown = _acceptable_markdown(include_citation=True) + (
        "\n## Bibliography\n\n- [@Smith2024] Fixture metadata only.\n"
    )
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=markdown,
    )
    _write_fixture_citation_registry(tmp_path, run_id)
    _write_fixture_claim_support_audit(tmp_path, run_id)

    summary = inspect_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert summary["citation_registry_present"] is True
    assert summary["citation_registry_source_count"] == 1
    assert summary["registry_backed_citation_count"] >= 1
    assert summary["unregistered_citation_keys"] == []
    assert summary["bibliography_status"] == "registry-backed"
    assert summary["citation_policy"] == "registry-only"
    assert summary["claim_support_audit_present"] is True
    assert summary["claim_support_registry_supported_count"] == 1
    assert summary["claim_support_missing_required_citation_count"] == 0

    result = CliRunner().invoke(
        app,
        ["inspect-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )
    assert result.exit_code == 0, result.output
    assert "Citation registry: present" in result.output
    assert "Registry sources: 1" in result.output
    assert "Bibliography: registry-backed" in result.output
    assert "Claim support: present" in result.output
    assert "Registry-supported claims: 1" in result.output


def test_inspect_and_lint_paper_bundle_report_retrieval_quality(tmp_path) -> None:
    run_id = "inspect-paper-retrieval-quality"
    markdown = _acceptable_markdown(include_citation=True) + (
        "\n## Bibliography\n\n- [@Smith2024] Fixture metadata only.\n"
    )
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=markdown,
    )
    _write_fixture_citation_registry(tmp_path, run_id)
    _write_fixture_claim_support_audit(tmp_path, run_id)
    _write_fixture_retrieval_quality_report(tmp_path, run_id)

    inspect_summary = inspect_paper_bundle_summary(run_id=run_id, root=tmp_path)
    lint_summary = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert inspect_summary["retrieval_quality_report_present"] is True
    assert inspect_summary["retrieved_source_count"] == 3
    assert inspect_summary["accepted_source_count"] == 1
    assert inspect_summary["rejected_source_count"] == 2
    assert inspect_summary["retrieval_adequacy_status"] == "bounded_context_only"
    assert inspect_summary["source_relevance_adjudication_enabled"] is True
    assert inspect_summary["source_relevance_adjudicator_backend"] == "fake"
    assert inspect_summary["source_relevance_adjudicated_count"] == 2
    assert inspect_summary["source_relevance_llm_accepted_count"] == 1
    assert inspect_summary["source_relevance_llm_rejected_count"] == 1
    assert inspect_summary["source_relevance_hard_reject_count"] == 1
    assert lint_summary["retrieval_quality_report_present"] is True
    assert lint_summary["source_relevance_adjudication_enabled"] is True
    assert lint_summary["source_relevance_adjudicator_backend"] == "fake"
    assert lint_summary["source_relevance_adjudicated_count"] == 2
    assert lint_summary["source_relevance_llm_accepted_count"] == 1
    assert lint_summary["source_relevance_llm_rejected_count"] == 1
    assert lint_summary["source_relevance_hard_reject_count"] == 1
    assert lint_summary["citation_registry_sources_all_accepted"] is True
    assert lint_summary["accepted_source_count"] == 1
    assert any(
        "retrieved sources were rejected" in warning
        for warning in lint_summary["development_warnings"]
    )

    result = CliRunner().invoke(
        app,
        ["inspect-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )
    assert result.exit_code == 0, result.output
    assert "Retrieval quality: present" in result.output
    assert "Accepted sources: 1" in result.output
    assert "Rejected sources: 2" in result.output
    assert "Source relevance adjudication: fake" in result.output
    assert "Adjudicated sources: 2" in result.output
    assert "LLM accepted sources: 1" in result.output
    assert "LLM rejected sources: 1" in result.output
    assert "Hard rejected sources: 1" in result.output


def test_inspect_and_lint_paper_bundle_report_quality_repair(tmp_path) -> None:
    run_id = "inspect-paper-quality-repair"
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id=run_id,
            domain="human geography",
            root=tmp_path,
            stop_after=PipelineStage.PLAN_MANUSCRIPT,
        )
    )
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    generate_full_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        config=FullPaperGenerationConfig(
            run_id=run_id,
            write_report=True,
            quality_repair_backend="deterministic",
        ),
        enable_safe_repair=True,
    )

    inspect_summary = inspect_paper_bundle_summary(run_id=run_id, root=tmp_path)
    lint_summary = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert inspect_summary["quality_repair_report_present"] is True
    assert inspect_summary["quality_repair_backend"] == "deterministic"
    assert inspect_summary["quality_repaired_section_count"] >= 1
    assert inspect_summary["section_depth_targets_present"] is True
    assert (
        inspect_summary["section_depth_target_met_count"]
        == (inspect_summary["section_depth_target_total"])
    )
    assert inspect_summary["sections_below_depth_target"] == []
    assert inspect_summary["warnings_reduced_count"] >= 1
    assert inspect_summary["claim_support_rechecked_after_quality_repair"] is True
    assert inspect_summary["citation_safety_rechecked_after_quality_repair"] is True
    assert lint_summary["quality_repair_report_present"] is True
    assert lint_summary["quality_repair_status"] in {"repaired", "no_action_needed"}
    assert lint_summary["quality_repaired_section_count"] >= 1
    assert lint_summary["section_depth_targets_present"] is True
    assert lint_summary["sections_below_depth_target"] == []
    assert lint_summary["placeholder_sections_after_quality_repair"] == []
    assert lint_summary["warnings_reduced_count"] >= 1
    assert lint_summary["quality_status_before_repair"]
    assert lint_summary["quality_status_after_repair"]
    assert lint_summary["claim_support_rechecked_after_quality_repair"] is True
    assert lint_summary["citation_safety_rechecked_after_quality_repair"] is True

    inspect_result = CliRunner().invoke(
        app,
        ["inspect-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )
    assert inspect_result.exit_code == 0, inspect_result.output
    assert "Quality repair: present" in inspect_result.output
    assert "Quality repair backend: deterministic" in inspect_result.output
    assert "Quality repaired sections:" in inspect_result.output
    assert "Depth targets met:" in inspect_result.output
    assert "Warnings reduced:" in inspect_result.output

    lint_result = CliRunner().invoke(
        app,
        ["lint-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )
    assert lint_result.exit_code == 0, lint_result.output
    assert "Quality repair: present" in lint_result.output
    assert "Depth targets met:" in lint_result.output
    assert "Warnings reduced:" in lint_result.output
    assert "Quality repair backend: deterministic" in lint_result.output


def test_inspect_paper_bundle_missing_run_gives_clear_error(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "inspect-paper-bundle",
            "--root",
            str(tmp_path),
            "--run-id",
            "missing-paper-run",
        ],
    )

    assert result.exit_code == 1
    assert "No run directory found for run_id=missing-paper-run" in result.output


def test_lint_paper_bundle_fails_placeholder_short_draft_read_only(tmp_path) -> None:
    run_id = "lint-paper-placeholder"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_short_placeholder_markdown(),
    )
    before = _run_file_snapshot(tmp_path, run_id)

    json_result = CliRunner().invoke(
        app,
        [
            "lint-paper-bundle",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )

    assert json_result.exit_code == 0, json_result.output
    assert _run_file_snapshot(tmp_path, run_id) == before
    payload = json.loads(json_result.output)
    assert payload["run_id"] == run_id
    assert payload["quality_status"] == "DraftQualityFailed"
    assert payload["word_count"] < 1500
    assert payload["section_count"] > 10
    assert payload["title_is_placeholder"] is True
    assert payload["citations_present"] is False
    assert payload["too_many_sections_for_length"] is True
    assert payload["publication_ready"] is False
    assert payload["is_verification_evidence"] is False
    assert "Draft may be skeletal: below proxy word-count target." in payload["warnings"]
    assert "Title appears to be a placeholder." in payload["issues"]
    assert "Severe section fragmentation is present." in payload["issues"]
    assert "Too many headings for the amount of content." in payload["warnings"]
    assert "No citation markers found." in payload["issues"]
    assert "No citation markers found." in payload["warnings"]
    assert payload["semantic_checks"]["central_contribution_present"] is False

    human_result = CliRunner().invoke(
        app,
        ["lint-paper-bundle", "--root", str(tmp_path), "--run-id", run_id],
    )

    assert human_result.exit_code == 0, human_result.output
    assert f"Paper quality: {run_id}" in human_result.output
    assert "Status: DraftQualityFailed" in human_result.output
    assert "Title: placeholder" in human_result.output
    assert "Citations: absent" in human_result.output
    assert "Semantic essentials:" in human_result.output
    assert "Quality failures:" in human_result.output
    assert _run_file_snapshot(tmp_path, run_id) == before


def test_lint_paper_bundle_warns_on_missing_citations(tmp_path) -> None:
    run_id = "lint-paper-no-citations"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_acceptable_markdown(include_citation=False),
    )

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityWarnings"
    assert payload["citation_marker_count"] == 0
    assert payload["blocking_quality_issues"] == []
    assert payload["warnings"] == ["No citation markers found."]
    assert "No citation markers found." in payload["issues"]
    assert payload["semantic_checks"]["central_contribution_present"] is True


def test_lint_paper_bundle_is_not_length_only(tmp_path) -> None:
    run_id = "lint-paper-short-semantic"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_short_semantically_complete_markdown(include_citation=False),
    )

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["word_count"] < payload["thresholds"]["min_words"]
    assert payload["quality_status"] == "DraftQualityWarnings"
    assert payload["quality_failure_reasons"] == []
    assert (
        "Draft may be skeletal: below proxy word-count target." in payload["development_warnings"]
    )
    assert payload["semantic_checks"]["problem_statement_present"] is True
    assert payload["semantic_checks"]["central_contribution_present"] is True
    assert payload["semantic_checks"]["evidence_boundary_statement_present"] is True
    assert payload["main_body_section_count"] == 5
    assert payload["appendix_section_count"] == 2
    assert payload["main_body_heading_fragmentation_detected"] is False
    assert payload["appendix_headings_present"] is True
    assert payload["semantic_section_audit"]
    assert all(
        item["is_verification_evidence"] is False for item in payload["semantic_section_audit"]
    )


def test_lint_paper_bundle_appendices_do_not_trigger_fragmentation_failure(
    tmp_path,
) -> None:
    run_id = "lint-paper-appendices-not-fragmented"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_short_seven_main_sections_with_appendices_markdown(),
    )

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["main_body_section_count"] == 7
    assert payload["appendix_section_count"] == 3
    assert payload["total_heading_count"] == 11
    assert payload["main_body_heading_fragmentation_detected"] is False
    assert payload["heading_fragmentation_detected"] is False
    assert payload["too_many_sections_for_length"] is False
    assert payload["unplanned_main_body_headings"] == []
    assert payload["quality_status"] == "DraftQualityWarnings"
    assert payload["quality_failure_reasons"] == []
    assert "Severe section fragmentation is present." not in payload["issues"]
    assert (
        "Appendices increase the total heading count but do not fragment the main body."
        in payload["development_warnings"]
    )


def test_lint_paper_bundle_concrete_limitations_are_not_placeholder_like(
    tmp_path,
) -> None:
    run_id = "lint-paper-concrete-limitations"
    old_limitations = "## Limitations\n" + _repeated_quality_paragraph(
        "The limitations section keeps the scope bounded"
    )
    concrete_limitations = (
        "## Limitations\n"
        "This limitations section is not placeholder boilerplate. "
        "The accepted source count is local to the citation registry, rejected source "
        "counts constrain what can be cited, hard-rejected source decisions cannot "
        "support manuscript claims, the absence of proof artifacts blocks proof "
        "language, the absence of experiment artifacts blocks experiment language, "
        "and the absence of human-review artifacts blocks human approval language. "
        "The manuscript remains bounded by publication_ready=false and cannot turn "
        "quality repair into validation."
    )
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_acceptable_markdown(include_citation=False).replace(
            old_limitations,
            concrete_limitations,
        ),
    )

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["limitations_concrete_constraint_count"] >= 2
    assert "Limitations" not in {
        item["heading"] for item in payload["empty_or_placeholder_sections"]
    }
    assert all(
        item["section_name"] != "Limitations" or item["placeholder_like"] is False
        for item in payload["semantic_section_audit"]
    )


def test_lint_paper_bundle_detects_standalone_central_message_as_metadata(
    tmp_path,
) -> None:
    run_id = "lint-paper-central-message"
    markdown = _short_semantically_complete_markdown(include_citation=False).replace(
        "## Claim and Evidence Boundaries",
        "## Central Message\n\n"
        "The central contribution of this draft remains bounded and non-evidential.\n\n"
        "## Claim and Evidence Boundaries",
    )
    _write_paper_bundle_markdown(tmp_path, run_id=run_id, complete_markdown=markdown)

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["standalone_central_message_detected"] is True
    assert payload["metadata_section_count"] == 1
    assert payload["heading_fragmentation_detected"] is True
    assert payload["main_body_section_count"] == 5
    assert "Severe section fragmentation is present." not in payload["quality_failure_reasons"]


def test_lint_paper_bundle_fails_missing_central_contribution(tmp_path) -> None:
    run_id = "lint-paper-missing-contribution"
    markdown = _short_semantically_complete_markdown(include_citation=False).replace(
        "The central contribution of this draft",
        "The internal package",
    )
    _write_paper_bundle_markdown(tmp_path, run_id=run_id, complete_markdown=markdown)

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityFailed"
    assert "Central contribution is missing or not explicit." in payload["quality_failure_reasons"]


def test_lint_paper_bundle_fails_missing_problem_statement(tmp_path) -> None:
    run_id = "lint-paper-missing-problem"
    markdown = (
        _short_semantically_complete_markdown(include_citation=False)
        .replace(
            "problem statement",
            "setup note",
        )
        .replace("research problem", "research setting")
    )
    _write_paper_bundle_markdown(tmp_path, run_id=run_id, complete_markdown=markdown)

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityFailed"
    assert "Problem statement is missing or not explicit." in payload["quality_failure_reasons"]


def test_lint_paper_bundle_fails_fake_empirical_claim(tmp_path) -> None:
    run_id = "lint-paper-fake-empirical"
    markdown = (
        _short_semantically_complete_markdown(include_citation=False)
        + "\nThe pipeline is empirically validated for real-world deployment.\n"
    )
    _write_paper_bundle_markdown(tmp_path, run_id=run_id, complete_markdown=markdown)

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityFailed"
    assert (
        "Fake empirical or real-world validation language is present."
        in payload["quality_failure_reasons"]
    )


def test_lint_paper_bundle_fails_external_facts_without_citations(tmp_path) -> None:
    run_id = "lint-paper-uncited-fact"
    markdown = (
        _short_semantically_complete_markdown(include_citation=False)
        + "\nStudies show that this external setting is already established.\n"
    )
    _write_paper_bundle_markdown(tmp_path, run_id=run_id, complete_markdown=markdown)

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityFailed"
    assert (
        "External factual claims appear without citation markers."
        in payload["quality_failure_reasons"]
    )


def test_lint_paper_bundle_passes_acceptable_synthetic_fixture(tmp_path) -> None:
    run_id = "lint-paper-pass"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_acceptable_markdown(include_citation=True),
    )
    _write_fixture_citation_registry(tmp_path, run_id)
    _write_fixture_claim_support_audit(tmp_path, run_id)

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityPass"
    assert payload["word_count"] >= payload["thresholds"]["min_words"]
    assert (
        payload["average_words_per_section"] >= payload["thresholds"]["min_avg_words_per_section"]
    )
    assert payload["citation_marker_count"] >= 1
    assert payload["issues"] == []
    assert payload["warnings"] == []


def test_lint_paper_bundle_rejects_unregistered_citation_key(tmp_path) -> None:
    run_id = "lint-paper-unregistered-citation"
    markdown = _acceptable_markdown(include_citation=False).replace(
        "This introduction gives problem framing for the research problem",
        "This introduction gives problem framing for the research problem [@Invented2026]",
    )
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=markdown,
    )
    _write_fixture_citation_registry(tmp_path, run_id)
    _write_fixture_claim_support_audit(tmp_path, run_id)

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityFailed"
    assert payload["unregistered_citation_keys"] == ["Invented2026"]
    assert any("Unregistered citation keys" in reason for reason in payload["issues"])


def test_lint_paper_bundle_prefers_revised_artifact(tmp_path) -> None:
    run_id = "lint-paper-prefers-revised"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_acceptable_markdown(include_citation=True),
        revised_markdown=_short_placeholder_markdown(),
    )

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["primary_artifact_to_read"].endswith("reports/revised-manuscript-draft.md")
    assert payload["quality_status"] == "DraftQualityFailed"
    assert payload["title_is_placeholder"] is True
    assert payload["heading_fragmentation_detected"] is True


def test_lint_paper_bundle_missing_run_gives_clear_error(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "lint-paper-bundle",
            "--root",
            str(tmp_path),
            "--run-id",
            "missing-paper-run",
        ],
    )

    assert result.exit_code == 1
    assert "No run directory found for run_id=missing-paper-run" in result.output


def test_lint_paper_bundle_missing_optional_artifacts_degrades_gracefully(tmp_path) -> None:
    run_id = "lint-paper-minimal"
    _write_paper_bundle_markdown(
        tmp_path,
        run_id=run_id,
        complete_markdown=_acceptable_markdown(include_citation=True),
    )
    _write_fixture_citation_registry(tmp_path, run_id)
    _write_fixture_claim_support_audit(tmp_path, run_id)

    payload = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert payload["quality_status"] == "DraftQualityPass"
    assert payload["primary_artifact_to_read"].endswith("reports/complete-manuscript-draft.md")
    assert payload["paper_release_status"] is None
    assert payload["release_status_unchanged"] is True
    assert payload["safety_status_unchanged"] is True


def _prepare_paper_bundle(
    tmp_path,
    *,
    run_id: str,
    revised: bool,
    release: bool,
) -> None:
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id=run_id,
            domain="human geography",
            root=tmp_path,
            stop_after=PipelineStage.PLAN_MANUSCRIPT,
        )
    )
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    generate_full_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        config=FullPaperGenerationConfig(run_id=run_id, write_report=True),
        enable_safe_repair=revised,
    )
    if release:
        run_full_paper_release_gate(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            config=FullPaperReleaseGateConfig(run_id=run_id, write_report=True),
        )


def _write_cli_planned_experiment_spec(reports: Path, *, run_id: str) -> Path:
    payload = {
        "run_id": run_id,
        "spec_id": "experiment-spec-cli-001",
        "target_claim_id": "experiment-claim-cli",
        "target_section": "Demonstration Status",
        "hypothesis_or_question": "Can a local synthetic template record bounded metrics?",
        "suggested_dataset": "deterministic synthetic calibration fixture",
        "suggested_metrics": ["bounded_improvement", "method_error"],
        "suggested_baselines": ["deterministic baseline"],
        "suggested_seed_policy": "fixed seed 1729",
        "expected_output_artifacts": ["metrics", "log"],
        "status": "planned",
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
    }
    path = reports / "experiment-spec-cli-001.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_human_review_fixture(tmp_path, *, run_id: str):
    payload = {
        "run_id": run_id,
        "review_id": f"review-{run_id}",
        "reviewer_name_optional": "Fixture Reviewer",
        "reviewer_role": "internal_human_reviewer",
        "reviewer_is_human": True,
        "llm_generated": False,
        "reviewed_artifact_paths": [
            f"runs/{run_id}/reports/revised-manuscript-draft.md",
            f"runs/{run_id}/reports/reviewer-bundle-summary.json",
            f"runs/{run_id}/reports/claim-support-audit.json",
        ],
        "reviewed_at": "2026-06-30T00:00:00Z",
        "review_status": "reviewed_ready_for_evidence_generation",
        "checklist_items": [
            "problem framing checked",
            "citation registry checked",
            "accepted sources checked",
            "claim-support audit checked",
            "evidence gaps acknowledged",
            "proof artifact absent acknowledged",
            "experiment artifact absent acknowledged",
            "publication_ready remains false acknowledged",
        ],
        "blocking_concerns": [],
        "non_blocking_comments": [
            "The draft can proceed to evidence-generation planning with retrieval limits preserved."
        ],
        "requested_changes": [],
        "accepted_limitations": [
            "Retrieval remains bounded background context only.",
            "Proof artifact is absent.",
            "Experiment artifact is absent.",
            "publication_ready remains false.",
        ],
        "recommended_next_action": (
            "Proceed to evidence generation planning without publication-readiness claims."
        ),
        "reviewer_attestation": (
            "I performed this human review locally and understand that it records "
            "review occurrence only."
        ),
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
    }
    path = tmp_path / "human-review.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_proof_artifact_fixture(tmp_path, *, run_id: str) -> Path:
    payload = {
        "run_id": run_id,
        "proof_id": "lean-proof-passed-cli-001",
        "proof_type": "lean_verified",
        "claim_ids_or_statement_ids": [
            "claim-cand-human-geography-optimal-transport-theory-b-theorem-or-conjecture-form"
        ],
        "statement": "A local checker report is linked for this bounded CLI fixture.",
        "artifact_path_optional": f"runs/{run_id}/reports/revised-manuscript-draft.md",
        "checker_name_optional": "fixture-local-checker",
        "checker_version_optional": "0.1.0",
        "checker_status": "passed",
        "checker_log_hash_optional": "2" * 64,
        "proof_hash": "1" * 64,
        "review_status": "artifact_scope_not_human_validated",
        "limitations": [
            "This CLI fixture records proof-artifact intake only.",
            "It does not imply novelty, broad correctness, or publication readiness.",
        ],
        "created_at": "2026-06-30T00:00:00Z",
        "ingested_at": "2026-06-30T00:00:00Z",
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": True,
    }
    path = tmp_path / "proof-artifact-cli.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_experiment_artifact_fixture(tmp_path, *, run_id: str) -> Path:
    payload = {
        "run_id": run_id,
        "experiment_id": "completed-experiment-cli-001",
        "experiment_type": "local_synthetic_fixture",
        "claim_ids_or_section_ids": ["demonstration-status"],
        "hypothesis_or_question": "Can local CLI intake record a bounded experiment artifact?",
        "status": "completed",
        "dataset_name_optional": "fixture-synthetic-dataset",
        "dataset_hash_optional": "6" * 64,
        "config_hash": "7" * 64,
        "code_commit_hash_optional": "abc123fixture",
        "command_optional": "factori fixture-experiment --local",
        "metrics": {"fixture_metric": 1.0, "sample_count": 3},
        "result_summary": (
            "The local CLI fixture completed and reports bounded metrics for this run only."
        ),
        "artifact_paths": [f"runs/{run_id}/reports/revised-manuscript-draft.md"],
        "limitations": [
            "This experiment artifact is local to the fixture run.",
            "It does not imply broad empirical validation or publication readiness.",
        ],
        "created_at": "2026-06-30T00:00:00Z",
        "ingested_at": "2026-06-30T00:00:00Z",
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
    }
    path = tmp_path / "experiment-artifact-cli.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _run_file_snapshot(tmp_path, run_id: str) -> dict[str, str]:
    run_path = tmp_path / "runs" / run_id
    return {
        path.relative_to(run_path).as_posix(): sha256_file(path)
        for path in sorted(run_path.rglob("*"))
        if path.is_file()
    }


def _write_paper_bundle_markdown(
    tmp_path,
    *,
    run_id: str,
    complete_markdown: str,
    revised_markdown: str | None = None,
) -> None:
    reports = tmp_path / "runs" / run_id / "reports"
    reports.mkdir(parents=True)
    (reports / "complete-manuscript-draft.md").write_text(
        complete_markdown,
        encoding="utf-8",
    )
    if revised_markdown is not None:
        (reports / "revised-manuscript-draft.md").write_text(
            revised_markdown,
            encoding="utf-8",
        )


def _write_fixture_citation_registry(tmp_path, run_id: str) -> None:
    record = CitationRecord(
        citation_id="citation-fixture-smith",
        citation_key="Smith2024",
        source_id="fixture-smith",
        title="Fixture bounded context",
        authors=["Smith, Fixture"],
        year=2024,
        provider="fake",
        retrieval_backend="fake",
        retrieved_at="1970-01-01T00:00:00Z",
        raw_metadata_hash="0" * 64,
        source_type="test_fixture",
        allowed_citation_key="Smith2024",
        trust_level="fixture_only",
        source_status="fixture",
    )
    registry = CitationRegistry(
        run_id=run_id,
        citations=[record],
        bibliography=[
            BibliographyEntry(
                citation_id=record.citation_id,
                citation_key=record.citation_key,
                source_id=record.source_id,
                markdown="- [@Smith2024] Fixture metadata only.",
                has_source_provenance=True,
            )
        ],
        citation_key_policy="fixture",
        citation_policy="registry-only",
        retrieval_backend="fake",
        retrieval_scope="bounded-fixture",
        source_registry_hash="1" * 64,
        source_count=1,
        accepted_source_count=1,
    )
    reports = tmp_path / "runs" / run_id / "reports"
    (reports / "citation-registry.json").write_text(
        registry.model_dump_json(indent=2),
        encoding="utf-8",
    )


def _write_fixture_retrieval_quality_report(tmp_path, run_id: str) -> None:
    report = RetrievalQualityReport(
        run_id=run_id,
        retrieval_backend="local",
        total_retrieved_sources=3,
        accepted_source_count=1,
        rejected_source_count=2,
        duplicate_count=1,
        low_relevance_count=1,
        metadata_incomplete_count=0,
        mean_relevance_score=0.5,
        min_relevance_score=0.2,
        queries_used=["human geography bounded literature context"],
        coverage_limitations=["Bounded local source set; not validation or publication readiness."],
        adequacy_status="bounded_context_only",
        source_relevance_adjudication_enabled=True,
        source_relevance_adjudicator_backend="fake",
        source_relevance_adjudicator_model="test-model",
        source_relevance_adjudication_calls=1,
        adjudicated_source_count=2,
        deterministic_accept_count=0,
        deterministic_reject_count=0,
        llm_accepted_count=1,
        llm_rejected_count=1,
        hard_reject_count=1,
        accepted_source_ids=["fixture-smith"],
        rejected_source_ids=["duplicate", "irrelevant"],
        rejection_reasons={
            "duplicate": "duplicate_source",
            "irrelevant": "low_relevance",
        },
    )
    reports = tmp_path / "runs" / run_id / "reports"
    (reports / "retrieval-quality-report.json").write_text(
        report.model_dump_json(indent=2),
        encoding="utf-8",
    )


def _write_fixture_claim_support_audit(tmp_path, run_id: str) -> None:
    audit = ClaimSupportAuditReport(
        run_id=run_id,
        citation_registry_present=True,
        citation_policy="registry-only",
        claim_support_items=[
            ClaimSupportItem(
                sentence_id="introduction-p0-s0",
                section_name="Introduction and Problem Framing",
                sentence_text_hash="0" * 64,
                sentence_snippet="Citation registry records bounded context [@Smith2024].",
                claim_class="source_context_claim",
                citation_keys_present=["Smith2024"],
                required_support_type="registry_background_context",
                supporting_source_ids=["fixture-smith"],
                support_status="registry_supported",
            )
        ],
        summary_counts={
            "total_sentences": 1,
            "registry_supported": 1,
            "scaffold_not_required": 0,
            "missing_required_citation": 0,
            "scope_mismatch": 0,
            "forbidden_claim": 0,
            "citation_as_validation_misuse": 0,
        },
    )
    reports = tmp_path / "runs" / run_id / "reports"
    (reports / "claim-support-audit.json").write_text(
        audit.model_dump_json(indent=2),
        encoding="utf-8",
    )


def _short_placeholder_markdown() -> str:
    sections = "\n".join(
        f"## Section {index}\nPlaceholder text for section {index}." for index in range(1, 15)
    )
    return f"# Deterministic Branch Manuscript Plan\n\n{sections}\n"


def _acceptable_markdown(*, include_citation: bool) -> str:
    citation = " [@Smith2024]" if include_citation else ""
    sections = [
        ("Abstract", "This abstract summarizes the bounded argument"),
        (
            "Introduction",
            f"This introduction gives problem framing for the research problem{citation}",
        ),
        (
            "Central Contribution",
            "The central contribution of this draft is a bounded manuscript "
            "pipeline for human review only",
        ),
        ("Problem Framing", "The problem statement explains why the setting matters"),
        ("Method Summary", "The method summary describes the model and approach"),
        ("Results", "The results section reports only supported internal findings"),
        ("Limitations", "The limitations section keeps the scope bounded"),
        (
            "Claim/Evidence Appendix",
            "The claim/evidence appendix links claims to available artifacts",
        ),
        (
            "Provenance Appendix",
            "The provenance appendix records artifact and run context",
        ),
    ]
    body = "\n\n".join(
        f"## {heading}\n{_repeated_quality_paragraph(seed)}" for heading, seed in sections
    )
    return f"# Bounded Transport Calibration\n\n{body}\n"


def _repeated_quality_paragraph(seed: str) -> str:
    sentence = (
        f"{seed} while preserving evidence boundaries, citation safety, "
        "problem framing, method summary, limitations, claim evidence traceability, "
        "and provenance context for human review only."
    )
    return " ".join(sentence for _ in range(8))


def _short_semantically_complete_markdown(*, include_citation: bool) -> str:
    citation = " [@Smith2024]" if include_citation else ""
    return (
        "# Evidence-Bounded Manuscript Generation for Human Geography Research Candidates\n\n"
        "## Abstract\n"
        "The problem statement is to turn bounded research candidates into safe manuscript "
        "drafts. The central contribution of this draft is a deterministic manuscript package "
        "with evidence boundary checks. It is not proof evidence and does not provide "
        "empirical validation.\n\n"
        "## Introduction and Problem Framing\n"
        f"The research problem is manuscript usefulness under strict evidence limits{citation}. "
        "No retrieval-backed citations are invented.\n\n"
        "## Method and Model\n"
        "The method summary describes a pipeline that assembles approved section drafts, "
        "claim links, and audit context mechanically.\n\n"
        "## Claim and Evidence Boundaries\n"
        "The evidence boundary statement is that generated prose cannot create evidence, "
        "upgrade labels, or imply publication readiness.\n\n"
        "## Limitations\n"
        "Limitations include missing retrieval coverage, proof validation, experiment evidence, "
        "citation coverage, and human validation.\n\n"
        "## Claim/Evidence Appendix\n"
        "- `claim-main`: evidence artifacts are context only.\n\n"
        "## Provenance Appendix\n"
        "- Run ID: `run-1`; artifact and ledger audit context remain non-evidence.\n"
    )


def _short_seven_main_sections_with_appendices_markdown() -> str:
    return (
        "# Evidence-Bounded Manuscript Generation for Human Geography Research Candidates\n\n"
        "## Abstract\n"
        "This abstract states the research problem, central contribution, evidence boundary, "
        "and human-review-only status.\n\n"
        "## Introduction and Problem Framing\n"
        "The problem statement is how to keep manuscript generation useful while preserving "
        "strict evidence boundaries and avoiding publication readiness claims.\n\n"
        "## Method and Model\n"
        "The method summary describes deterministic planning, bounded drafting, safe repair, "
        "and audit reporting.\n\n"
        "## Claim and Evidence Boundaries\n"
        "The central contribution of this draft is a bounded paper package that keeps proof, "
        "experiment, citation, and provenance roles separate.\n\n"
        "## Demonstration Status\n"
        "The demonstration status records pipeline behavior only and is not proof evidence or "
        "empirical validation.\n\n"
        "## Limitations\n"
        "Limitations include missing real retrieval coverage, proof artifacts, experiment "
        "evidence, and human validation.\n\n"
        "## Conclusion\n"
        "The conclusion restates the bounded contribution and identifies future evidence work.\n\n"
        "## Claim/Evidence Appendix\n"
        "- Claims and evidence links remain audit context only.\n\n"
        "## Source/Citation Appendix\n"
        "- No registry-backed citation entries are asserted by this fixture.\n\n"
        "## Provenance Appendix\n"
        "- Run artifacts, reports, and ledger context remain non-evidence.\n"
    )
