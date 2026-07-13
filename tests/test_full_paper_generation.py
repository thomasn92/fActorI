from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import factori.autonomous_paper_run as autonomous_paper_module
from factori.adapters.atlas_ranking import OpenAIAtlasPairRanker, build_pair_ranking_prompt
from factori.adapters.deep_opportunity import (
    OpportunityGenerationResponse,
    OpportunityProposal,
    OpportunityProposalEnvelope,
    OpportunityProposalItem,
    OpportunityScoreProposal,
    parse_opportunity_items,
)
from factori.adapters.fake import FakeProseGenerator
from factori.adapters.hybrid_evidence import (
    PACKAGE_ALLOWED_LABELS,
    EvidenceArtifactPlanProposal,
    HybridEvidenceDraftArtifact,
    HybridEvidenceDraftResponse,
    HybridEvidencePackageProposal,
    HybridEvidencePackageProposalItem,
    HybridEvidencePackageResponse,
    HybridEvidencePackageScoreProposal,
    build_hybrid_draft_prompt,
    build_hybrid_package_prompt,
    parse_hybrid_package_response,
)
from factori.adapters.llm_experiment_codegen import (
    ExperimentCodeGenerationResponse,
    ExperimentCodeProposal,
    ExperimentCodeProposalEnvelope,
    build_experiment_codegen_prompt,
    parse_experiment_codegen_response,
)
from factori.adapters.llm_route_planning import (
    ROUTE_ALLOWED_LABELS,
    ExecutionSpecProposal,
    RouteDecisionProposal,
    RoutePlanningProposalItem,
    RoutePlanningResponse,
    RoutePlanningScoreProposal,
    build_llm_route_planning_prompt,
    parse_route_planning_response,
)
from factori.adapters.llm_substrate import (
    SubstrateCandidateProposal,
    SubstrateGenerationResponse,
    SubstrateProposalItem,
    SubstrateScoreProposal,
    build_llm_substrate_prompt,
    parse_substrate_response,
)
from factori.adapters.llm_variance import (
    VarianceCandidateProposal,
    VarianceGenerationResponse,
    VarianceProposalItem,
    VarianceScoreProposal,
    build_llm_variance_prompt,
    parse_variance_items,
)
from factori.adapters.nucleus_manuscript import (
    ManuscriptCriticProposal,
    ManuscriptDraftProposal,
    ManuscriptPlanProposal,
    ManuscriptRevisionProposal,
    ManuscriptSectionProposal,
    NucleusManuscriptResponse,
)
from factori.adapters.scientific_critic import (
    AdjudicationDecisionProposal,
    CriticFindingProposal,
    CriticReviewProposal,
    CrossPackageAdjudicationProposal,
    CrossPackageAdjudicationResponse,
    PaperNucleusProposal,
    ScientificCriticResponse,
)
from factori.artifacts import ArtifactStore
from factori.autonomous_evidence_plan import (
    build_autonomous_evidence_gap_plan,
    inspect_autonomous_evidence_gap_plan,
    persist_autonomous_evidence_gap_plan,
)
from factori.autonomous_loop import (
    _decide_iteration,
    _ProgressSnapshot,
    _TerminalSummary,
    inspect_autonomous_loop,
    run_autonomous_loop,
)
from factori.autonomous_paper_checkpoint import (
    inspect_autonomous_paper_checkpoints,
    inspect_autonomous_paper_resume,
    verify_autonomous_paper_checkpoints,
)
from factori.autonomous_paper_run import (
    AutonomousPaperInjectedCrash,
    AutonomousPaperRunError,
    _final_bundle_is_complete,
    _final_manuscript_is_safe,
    _final_verification_is_safe,
    _lint_has_safety_block,
    inspect_autonomous_paper_run,
    run_autonomous_paper,
)
from factori.autonomous_plan_execution import (
    execute_autonomous_evidence_plan,
    inspect_autonomous_plan_execution,
)
from factori.branch_routing import (
    build_branch_route_decision,
    inspect_branch_routes,
    route_branches,
)
from factori.capability_escalation import (
    escalate_capabilities,
    inspect_capability_escalation,
)
from factori.citations import build_claim_support_audit, classify_claim_sentence
from factori.claim_adjudication import FakeClaimAdjudicator, OpenAIClaimAdjudicator
from factori.claim_evidence import (
    BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID,
    build_claim_evidence_map,
    inspect_claim_evidence_map,
    latest_claim_evidence_map_path,
    persist_claim_evidence_map,
)
from factori.cli import app
from factori.creative_mutations import (
    apply_creative_mutations,
    inspect_creative_mutations,
    plan_creative_mutations,
)
from factori.creative_search import (
    _cycle_stop_decision,
    inspect_creative_search,
    run_creative_search,
)
from factori.deep_opportunity_discovery import (
    DeepOpportunityDiscoveryError,
    MockedOpportunityRetriever,
    discover_deep_opportunities,
    inspect_deep_opportunities,
)
from factori.domain_method_atlas import (
    AtlasScanError,
    build_compatibility_filter_report,
    build_domain_method_atlas,
    domain_atlas,
    evaluate_pair_compatibility,
    inspect_atlas_scan,
    method_atlas,
    scan_domain_method_pairs,
    select_diverse_ranked_pairs,
)
from factori.evidence_artifact_intake import (
    EvidenceArtifactIntakeError,
    ingest_experiment_artifact,
    ingest_proof_artifact,
    inspect_experiment_artifacts,
    inspect_proof_artifacts,
)
from factori.evidence_aware_refresh import (
    EvidenceAwareRefreshError,
    refresh_evidence_aware_manuscript,
)
from factori.evidence_package_adjudication import (
    EvidencePackageAdjudicationError,
    adjudicate_evidence_packages,
    critique_evidence_packages,
    inspect_package_adjudication,
)
from factori.experiment_template_routing import (
    build_default_experiment_template_registry,
    inspect_experiment_gap_routing,
    route_experiment_gaps,
)
from factori.final_bundle_verification import (
    latest_final_bundle_verification,
    verify_final_release_bundle,
)
from factori.final_manuscript_regeneration import (
    inspect_final_manuscript,
    latest_final_manuscript_regeneration,
    regenerate_final_manuscript,
)
from factori.final_paper import (
    assemble_final_paper,
    build_final_paper_bundle,
    inspect_final_paper,
    verify_final_paper,
)
from factori.final_release_bundle import (
    build_final_release_bundle,
    build_references_bib,
    inspect_final_release_bundle,
    latest_final_release_bundle,
)
from factori.full_paper_generation import (
    generate_full_paper,
    inspect_paper_bundle_summary,
    inspect_reviewer_bundle_summary,
    lint_paper_bundle_summary,
)
from factori.full_paper_release import run_full_paper_release_gate
from factori.gap_attempts import (
    gap_fingerprint_for_plan_item,
    inspect_gap_attempt_history,
    inspect_planned_spec_dedup,
    planned_spec_fingerprint,
)
from factori.gap_strategy_diversification import (
    build_gap_strategy_diversification,
    inspect_gap_strategy_diversification,
    persist_gap_strategy_diversification,
    strategy_fingerprint,
    strategy_is_automation_ready,
)
from factori.generated_experiment_safety import audit_generated_experiment_code
from factori.generated_experiments import (
    GeneratedExperimentError,
    extract_metrics_from_output,
    generate_experiment_code,
    inspect_experiment_code,
    inspect_generated_experiment_results,
    run_generated_experiments,
)
from factori.generation_mutations import (
    inspect_generation_mutations,
    plan_generation_mutations,
)
from factori.hashing import sha256_file
from factori.human_review import (
    HumanReviewIntakeError,
    ingest_human_review,
    inspect_human_review,
)
from factori.human_review_reconciliation import (
    inspect_human_review_reconciliation,
    reconcile_human_review,
)
from factori.hybrid_evidence_packages import (
    HybridEvidencePackageError,
    execute_hybrid_evidence_packages,
    inspect_evidence_package_execution,
    inspect_hybrid_evidence_packages,
    plan_hybrid_evidence_packages,
)
from factori.idea_space import export_idea_space_report, inspect_idea_space
from factori.idea_tree import export_idea_tree, inspect_idea_tree
from factori.ledger import ResearchLedger
from factori.llm_orchestration import LLMOrchestrationError
from factori.llm_route_planning import (
    LLMRoutePlanningError,
    inspect_llm_routes,
    plan_llm_routes,
)
from factori.llm_substrate import (
    LLMSubstrateError,
    _decorative_method_reasons,
    construct_llm_substrates,
    inspect_llm_substrates,
    select_llm_substrates,
)
from factori.llm_variance import (
    LLMVarianceError,
    construct_idea_tree_from_llm_variance,
    generate_llm_variance,
    inspect_llm_variance,
)
from factori.mutation_tournament import (
    inspect_mutation_tournament,
    run_mutation_tournament,
)
from factori.nucleus_manuscript import (
    inspect_nucleus_manuscript,
    plan_nucleus_manuscript,
    revise_nucleus_manuscript,
    synthesize_nucleus_manuscript,
)
from factori.opportunity_discovery import (
    OPPORTUNITY_THRESHOLD,
    build_opportunity_discovery_report,
    discover_opportunities,
    extract_domain_primitives,
    inspect_opportunities,
    method_lens_library,
)
from factori.planned_spec_execution import (
    execute_planned_specs,
    inspect_planned_spec_execution,
)
from factori.production_mode import (
    check_production_mode,
    evaluate_production_mode,
    inspect_backends,
    stage_backend_record,
)
from factori.python_experiment_sandbox import (
    PythonExperimentSandboxError,
    inspect_python_experiment_sandbox,
    run_python_experiment_sandbox,
)
from factori.reviewer_change_requests import (
    ReviewerChangeRequestError,
    ingest_reviewer_change_requests,
    inspect_reviewer_change_requests,
)
from factori.route_execution import (
    build_route_execution_specs,
    execute_route_spec,
    inspect_route_execution,
    run_route_execution,
)
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AtlasScanInspectionReport,
    AtlasScanReport,
    AutonomousEvidenceGapPlan,
    AutonomousEvidenceGapPlanItem,
    AutonomousLoopGapTerminalClassification,
    AutonomousLoopIndex,
    AutonomousLoopRunReport,
    AutonomousPaperRunHandoff,
    AutonomousPaperRunIndex,
    AutonomousPaperRunReport,
    AutonomousPaperRunStage,
    AutonomousPlanExecutionReport,
    BackendKind,
    BranchRouteDecision,
    BranchRouteExecutionHint,
    BranchRouteInspectionReport,
    BranchRoutePlan,
    BranchRouteType,
    CapabilityEscalationIndex,
    CapabilityEscalationItem,
    CapabilityEscalationPolicy,
    CapabilityEscalationReport,
    CitationRecord,
    CitationRegistry,
    ClaimArtifactBinding,
    ClaimEvidenceMap,
    ClaimEvidenceMapLink,
    ClaimSupportAuditReport,
    ClaimSupportItem,
    CompatibilityExclusion,
    CompatibilityFilterReport,
    ControllerActionType,
    CreativeMutationCandidate,
    CreativeMutationInspectionReport,
    CreativeMutationPlan,
    CreativeMutationReport,
    CreativeSearchControllerConfig,
    CreativeSearchControllerReport,
    CreativeSearchCycle,
    CreativeSearchInspectionReport,
    CreativeSearchLineageEntry,
    CreativeSearchStopReason,
    CrossPackageAdjudicationInspectionReport,
    CrossPackageAdjudicationReport,
    DeepOpportunityCandidate,
    DeepOpportunityDiscoveryConfig,
    DeepOpportunityDiscoveryInspectionReport,
    DeepOpportunityDiscoveryReport,
    DeepOpportunityScore,
    DomainAtlasEntry,
    DomainMethodPair,
    DomainPrimitive,
    EvidenceArtifactPlan,
    EvidenceArtifactType,
    EvidenceAwareRefreshReport,
    EvidenceCitationBinding,
    EvidencePackageAdjudicationDecision,
    EvidencePackageAdjudicationScore,
    EvidencePackageDecision,
    EvidencePackageExecutionInspectionReport,
    EvidencePackageExecutionReport,
    EvidencePackageExecutionResult,
    ExperimentArtifact,
    ExperimentCodeSafetyAudit,
    ExperimentGapRoutingIndex,
    ExperimentGapRoutingReport,
    ExperimentTemplate,
    ExperimentTemplateRegistry,
    FinalManuscriptClaimSummary,
    FinalManuscriptRegenerationIndex,
    FinalManuscriptRegenerationReport,
    FinalManuscriptSection,
    FinalManuscriptStructuredDocument,
    FinalPaperAssemblyConfig,
    FinalPaperAssemblyReport,
    FinalPaperManifest,
    FinalPaperVerificationReport,
    FinalReleaseBundle,
    FinalReleaseBundleArtifact,
    FinalReleaseBundleIndex,
    FinalReleaseBundleManifest,
    FinalReleaseBundleReport,
    FinalReleaseReproducibilityManifest,
    FullPaperArtifactBundle,
    FullPaperGenerationConfig,
    FullPaperGenerationReport,
    FullPaperGenerationStatus,
    FullPaperReleaseGateConfig,
    GapAttemptHistory,
    GapAttemptRecord,
    GapStrategyOption,
    GeneratedExperimentExecutionReport,
    GeneratedExperimentInspectionReport,
    GeneratedExperimentResult,
    GeneratedSectionDraft,
    GenerationMutationCandidate,
    GenerationMutationContext,
    GenerationMutationDiversityCheck,
    GenerationMutationInspectionReport,
    GenerationMutationOperator,
    GenerationMutationPlan,
    HumanReviewArtifact,
    HumanReviewReconciliationIndex,
    HumanReviewReconciliationReport,
    HybridEvidencePackageCandidate,
    HybridEvidencePackageConfig,
    HybridEvidencePackageInspectionReport,
    HybridEvidencePackageRawArtifact,
    HybridEvidencePackageReport,
    HybridEvidencePackageScore,
    IdeaClusterDiagnostic,
    IdeaEdge,
    IdeaNode,
    IdeaNodeFeatureVector,
    IdeaSpaceAxis,
    IdeaSpaceDiversityReport,
    IdeaSpaceInspectionReport,
    IdeaSpacePCADiagnostic,
    IdeaTree,
    IdeaTreeConstructionReport,
    IdeaTreeExportReport,
    IdeaTreeInspectionReport,
    LLMExecutionSpecCandidate,
    LLMExperimentCodeArtifact,
    LLMExperimentCodegenConfig,
    LLMExperimentCodeRawArtifact,
    LLMOpportunityDiscoveryRawArtifact,
    LLMOrchestrationConfig,
    LLMPairRankingPrompt,
    LLMPairRankingReport,
    LLMPairRankingResult,
    LLMRouteDecisionCandidate,
    LLMRoutePlanningConfig,
    LLMRoutePlanningInspectionReport,
    LLMRoutePlanningPrompt,
    LLMRoutePlanningRawArtifact,
    LLMRoutePlanningReport,
    LLMRoutePlanningScore,
    LLMScientificSubstrateCandidate,
    LLMSubstrateConstructionConfig,
    LLMSubstrateConstructionInspectionReport,
    LLMSubstrateConstructionReport,
    LLMSubstrateConstructionScore,
    LLMSubstratePrompt,
    LLMSubstrateRawArtifact,
    LLMVarianceBatch,
    LLMVarianceCandidate,
    LLMVarianceGenerationConfig,
    LLMVarianceGenerationInspectionReport,
    LLMVarianceGenerationReport,
    LLMVariancePrompt,
    LLMVarianceRawArtifact,
    LLMVarianceScore,
    ManuscriptCriticReview,
    ManuscriptCriticRole,
    ManuscriptRevisionReport,
    MethodAtlasEntry,
    MethodLens,
    MetricExtractionResult,
    MutationTournamentComparison,
    MutationTournamentEntry,
    MutationTournamentInspectionReport,
    MutationTournamentResult,
    MutationTournamentSpec,
    NucleusManuscriptConfig,
    NucleusManuscriptDraft,
    NucleusManuscriptInspectionReport,
    NucleusManuscriptPlan,
    NucleusManuscriptRawArtifact,
    NucleusManuscriptStatus,
    NucleusManuscriptSynthesisReport,
    NucleusPaperType,
    OpportunityCandidate,
    OpportunityDiscoveryInspectionReport,
    OpportunityDiscoveryReport,
    OpportunityScoreBreakdown,
    OpportunitySeedConstraint,
    PaperNucleusSelection,
    PipelineRunConfig,
    PipelineStage,
    PlannedExperimentSpec,
    PlannedSpecDedupIndex,
    PlannedSpecDuplicateRecord,
    PlannedSpecExecutionReport,
    ProductionModePolicy,
    ProductionModeReport,
    ProductionModeViolation,
    ProofArtifact,
    ProofObligationSpec,
    QualityRepairReport,
    RetrievalContext,
    RetrievalExpansionRequest,
    RetrievalQualityReport,
    RetrievedSourceSummary,
    ReviewerBundleSummary,
    RouteExecutionInputContract,
    RouteExecutionInspectionReport,
    RouteExecutionOutputContract,
    RouteExecutionReport,
    RouteExecutionResult,
    RouteExecutionSpec,
    RouteExecutionStatus,
    SandboxBudgetPolicy,
    SandboxBudgetReport,
    SandboxExecutionConfig,
    SandboxExecutionResult,
    ScientificCriticFinding,
    ScientificCriticFindingSeverity,
    ScientificCriticFindingType,
    ScientificCriticRawArtifact,
    ScientificCriticReview,
    ScientificCriticRole,
    ScientificStageKind,
    ScientificSubstrate,
    ScientificSubstrateAssumption,
    ScientificSubstrateBuildReport,
    ScientificSubstrateExperimentDesign,
    ScientificSubstrateInspectionReport,
    ScientificSubstrateModelObject,
    ScientificSubstrateResultSchema,
    ScientificSubstrateVariable,
    StageBackendRecord,
    SubstrateExperimentComparisonTable,
    SubstrateExperimentResult,
    SubstrateExperimentRoutingReport,
    SubstrateExperimentSpec,
    SubstratePromotionCandidate,
    SubstratePromotionConfig,
    SubstratePromotionDecision,
    SubstratePromotionInspectionReport,
    SubstratePromotionReport,
    SubstrateTournamentComparison,
    SubstrateTournamentEntry,
    SubstrateTournamentInspectionReport,
    SubstrateTournamentResult,
    SubstrateTournamentSpec,
    VarianceAugmentationBatch,
    VarianceAugmentationConfig,
    VarianceAugmentationInspectionReport,
    VarianceAugmentationReport,
    VarianceAugmentedCandidate,
    VarianceDiversityDiagnostic,
)
from factori.scientific_substrate import (
    build_scientific_substrate,
    inspect_scientific_substrate,
)
from factori.substrate_experiment_routing import (
    inspect_substrate_experiment_routing,
    route_substrate_experiment,
)
from factori.substrate_promotion import (
    inspect_substrate_promotion,
    promote_variance_substrates,
)
from factori.substrate_tournament import (
    inspect_substrate_tournament,
    run_substrate_tournament,
)
from factori.variance_augmentation import (
    apply_variance_augmentation,
    augment_variance,
    build_variance_diversity_diagnostic,
    inspect_variance_augmentation,
)


class MockAtlasPairRanker:
    backend_name = "llm-openai-mocked-transport"
    backend_kind = BackendKind.LLM_OPENAI
    model = "mock-atlas-model"
    fallback_used = False
    fallback_disclosed = True

    def __init__(self) -> None:
        self.received_pair_ids: list[str] = []

    def rank_batch(self, *, pair_payloads, batch_index, prompt_id):
        prompt = build_pair_ranking_prompt(
            pair_payloads=pair_payloads,
            batch_index=batch_index,
            prompt_id=prompt_id,
            backend_name=self.backend_name,
            model=self.model,
        )
        results = []
        for payload in pair_payloads:
            pair_id = payload["pair_id"]
            self.received_pair_ids.append(pair_id)
            offset = sum(ord(char) for char in pair_id) % 20
            score = 0.70 + offset / 100.0
            results.append(
                LLMPairRankingResult(
                    pair_id=pair_id,
                    rank_score=score,
                    scientific_fit=score,
                    tractability=0.78,
                    question_abundance=0.80,
                    baseline_clarity=0.82,
                    verification_feasibility=0.84,
                    paper_shape_clarity=0.76,
                    false_bridge_risk=0.20,
                    tautology_risk=0.18,
                    novelty_hypothesis=(
                        "Hypothesis: this pair may expose a bounded question worth retrieval."
                    ),
                    underuse_hypothesis=(
                        "Hypothesis: this pairing may be underexplored; retrieval is required."
                    ),
                    ranking_explanation="Structured mocked LLM judgment for offline testing.",
                    recommended_for_deep_discovery=True,
                )
            )
        return prompt, results


class MockDeepOpportunityGenerator:
    backend_name = "llm-openai-mocked-transport"
    backend_kind = BackendKind.LLM_OPENAI
    model = "mock-deep-opportunity-model"
    fallback_used = False
    fallback_disclosed = True

    def __init__(self, *, duplicate: bool = False) -> None:
        self.duplicate = duplicate
        self.received_pair_ids: list[str] = []

    def generate_for_pair(
        self,
        *,
        pair_payload,
        retrieval_payload,
        opportunities_per_pair,
    ):
        pair = pair_payload["pair"]
        domain = pair_payload["domain"]
        method = pair_payload["method"]
        pair_id = pair["pair_id"]
        self.received_pair_ids.append(pair_id)
        accepted = []
        for index in range(opportunities_per_pair):
            suffix = 0 if self.duplicate else index
            proposal = OpportunityProposal(
                research_question=(
                    f"How does {method['name']} change bounded {domain['name']} "
                    f"behavior under perturbation family {suffix}?"
                ),
                hypothesis=(
                    f"A concrete {method['name']} object improves metric {suffix} over "
                    "the declared baseline in a bounded synthetic regime."
                ),
                theory_or_model_object=(
                    f"{method['canonical_objects'][0]} object over {domain['canonical_objects'][0]}"
                ),
                mathematical_or_computational_form=f"T_{suffix}(x) = x + {suffix}",
                experiment_or_proof_plan=(
                    f"Run a fixed-seed synthetic comparison for perturbation {suffix}."
                ),
                benchmark_plan="Compare the proposed object with null and standard baselines.",
                baseline_candidates=["null baseline", "standard domain baseline"],
                expected_metrics=["held_out_error", "stability"],
                failure_modes=["no improvement", "unstable behavior"],
                negative_controls=["remove the proposed mechanism"],
                data_regime="synthetic_only",
                verification_path="bounded synthetic benchmark",
                paper_shape="model, benchmark, negative control, and limitations",
                novelty_risk="Hypothesis: retrieval may reveal a close prior formulation.",
                underuse_hypothesis=(
                    "Hypothesis: the concrete pairing may be underexplored; bounded "
                    "retrieval cannot establish this."
                ),
                retrieval_support_summary=(
                    f"The {retrieval_payload['retrieval_mode']} context supplies only "
                    "bounded metadata."
                ),
                retrieval_contradictions=[],
                false_bridge_risks=["object mapping may be too generic"],
                tautology_risks=["DGP could encode the expected result"],
                recommended_next_stage="variance_generation",
            )
            score = OpportunityScoreProposal(
                scientific_fit=0.82,
                tractability=0.80,
                question_specificity=0.78,
                baseline_strength=0.81,
                verification_feasibility=0.84,
                expected_signal=0.75,
                failure_mode_value=0.77,
                paper_coherence=0.79,
                novelty_risk_penalty=0.20,
                false_bridge_penalty=0.15,
                tautology_penalty=0.18,
                retrieval_confidence=retrieval_payload["retrieval_confidence"],
                final_score=0.84 - 0.01 * index,
                score_explanation="Mocked structured LLM score for offline testing.",
            )
            accepted.append(OpportunityProposalItem(candidate=proposal, score=score))
        return OpportunityGenerationResponse(
            prompt_text=f"mock prompt for {pair_id}",
            requested_output_schema=OpportunityProposalEnvelope.model_json_schema(),
            raw_response={"opportunities": [item.model_dump(mode="json") for item in accepted]},
            accepted=accepted,
            rejected=[],
        )


class MockRealOpportunityRetriever:
    backend_name = "openalex-mocked-transport"
    backend_kind = BackendKind.RETRIEVAL_REAL
    retrieval_mode = "real_retrieval"
    fallback_used = False
    fallback_disclosed = True

    def retrieve(self, **kwargs):
        context = MockedOpportunityRetriever().retrieve(**kwargs)
        pair = kwargs["pair"]
        return context.model_copy(
            update={
                "retrieval_mode": "real_retrieval",
                "backend_name": self.backend_name,
                "retrieval_confidence": 0.7,
                "sources": [
                    RetrievedSourceSummary(
                        source_id=f"openalex-{pair.pair_id}",
                        title="Mocked transport result representing real retrieval metadata",
                        authors=["Test Author"],
                        year=2025,
                        venue="Test Venue",
                        abstract_or_snippet="Bounded source metadata for injected transport tests.",
                        doi="10.0000/test",
                        relevance_score=0.8,
                        provider="openalex",
                        fake_or_mocked=False,
                    )
                ],
                "limitations": [
                    "Injected transport test; no network was used.",
                    "Novelty and underuse remain hypotheses.",
                ],
            }
        )


class MockLLMVarianceGenerator:
    backend_name = "llm-openai-mocked-transport"
    backend_kind = BackendKind.LLM_OPENAI
    model = "mock-variance-model"
    fallback_used = False
    fallback_disclosed = True

    def __init__(self, *, include_filtered_variants: bool = False) -> None:
        self.received_source_ids: list[str] = []
        self.include_filtered_variants = include_filtered_variants

    def generate_variants(
        self,
        *,
        prompt_id,
        source_payload,
        retrieval_context_payload,
        variants_per_opportunity,
    ):
        families = [
            "mechanism",
            "benchmark",
            "robustness",
            "baseline_strengthening",
            "negative_control",
            "representation",
            "counterexample",
        ][:variants_per_opportunity]
        source_id = source_payload["opportunity_id"]
        self.received_source_ids.append(source_id)
        prompt = build_llm_variance_prompt(
            prompt_id=prompt_id,
            backend_name=self.backend_name,
            model=self.model,
            source_payload=source_payload,
            retrieval_context_payload=retrieval_context_payload,
            variants_per_opportunity=variants_per_opportunity,
        )
        items = []
        for index, family in enumerate(families):
            proposal = VarianceCandidateProposal(
                variant_family=family,
                title=f"{family.title()} variant {index} for {source_id}",
                research_question=(
                    f"How does {family} perturbation {index} change the bounded "
                    f"mechanism in {source_id}?"
                ),
                hypothesis=(
                    f"The {family} branch changes metric {index} relative to both "
                    "declared baselines in a synthetic regime."
                ),
                theory_or_model_object=f"Variant operator V_{index} for {family}",
                mathematical_or_computational_form=f"V_{index}(x)=x+{index + 1}",
                experiment_or_proof_plan=(
                    f"Run fixed-seed {family} perturbations and retain failures."
                ),
                benchmark_plan=(
                    "Compare with a null baseline and a stronger regularized baseline."
                ),
                baseline_candidates=["null baseline", "regularized baseline"],
                negative_controls=[f"remove {family} mechanism"],
                failure_modes=["no improvement", "unstable response"],
                verification_path="bounded synthetic comparison",
                expected_metrics=["held_out_error", "stability"],
                data_regime="synthetic_only",
                paper_role=f"{family} branch",
                scientific_rationale=(
                    f"This changes the source mechanism along the {family} axis."
                ),
                novelty_risk="Hypothesis: retrieval may contain a close branch.",
                false_bridge_risk="The object mapping may fail under perturbation.",
                tautology_risk="The synthetic design may favor the mechanism.",
            )
            if self.include_filtered_variants and index == 5:
                proposal = items[0].candidate.model_copy(
                    update={"variant_family": "representation"}
                )
            if self.include_filtered_variants and index == 6:
                proposal = proposal.model_copy(
                    update={
                        "research_question": source_payload["research_question"],
                        "theory_or_model_object": source_payload["theory_or_model_object"],
                        "mathematical_or_computational_form": source_payload[
                            "mathematical_or_computational_form"
                        ],
                        "experiment_or_proof_plan": source_payload["experiment_or_proof_plan"],
                    }
                )
            items.append(
                VarianceProposalItem(
                    candidate=proposal,
                    score=VarianceScoreProposal(
                        specificity=0.82,
                        branch_diversity=0.84,
                        baseline_quality=0.8,
                        verification_feasibility=0.83,
                        failure_mode_value=0.79,
                        paper_coherence=0.81,
                        novelty_risk_penalty=0.2,
                        false_bridge_penalty=0.15,
                        tautology_penalty=0.18,
                        final_score=0.86 - 0.01 * index,
                        score_explanation="Mocked structured LLM variance score.",
                    ),
                )
            )
        return VarianceGenerationResponse(
            prompt=prompt,
            raw_response={"variants": [item.model_dump(mode="json") for item in items]},
            accepted=items,
            rejected=[],
        )


class MockLLMSubstrateGenerator:
    backend_name = "llm-openai-mocked-transport"
    backend_kind = BackendKind.LLM_OPENAI
    model = "mock-substrate-model"
    fallback_used = False
    fallback_disclosed = True

    def __init__(self, *, reject_first: bool = False) -> None:
        self.reject_first = reject_first
        self.received_variant_ids: list[str] = []

    def construct_substrate(
        self,
        *,
        prompt_id,
        source_payload,
        opportunity_payload,
        retrieval_context_payload,
    ):
        variant_id = source_payload["variant_id"]
        method_id = source_payload["method_id"]
        call_index = len(self.received_variant_ids)
        self.received_variant_ids.append(variant_id)
        prompt = build_llm_substrate_prompt(
            prompt_id=prompt_id,
            backend_name=self.backend_name,
            model=self.model,
            source_payload=source_payload,
            opportunity_payload=opportunity_payload,
            retrieval_context_payload=retrieval_context_payload,
        )
        routes = [
            "synthetic_experiment",
            "benchmark_tournament",
            "counterexample_search",
            "applied_math_reduction",
        ]
        candidate = SubstrateCandidateProposal(
            title=f"Concrete {method_id} substrate for {variant_id}",
            domain_problem=source_payload["research_question"],
            central_tension=(
                "The proposed mechanism must outperform declared baselines without encoding "
                "the result in the synthetic design."
            ),
            concrete_model_object=ScientificSubstrateModelObject(
                model_type=f"bounded {method_id} operator",
                equations=[f"{method_id}_theta(x) = x + theta_{call_index}"],
                algorithm_optional=f"Fit the {method_id} operator on training observations.",
                parameter_interpretation=["theta controls the proposed bounded mechanism"],
                identifiability_notes="Identify theta only within the declared synthetic regime.",
                what_would_falsify_it="No held-out improvement over both baselines.",
            ),
            mathematical_or_computational_form=[f"{method_id}_theta(x) = x + theta_{call_index}"],
            variables_and_notation=[
                ScientificSubstrateVariable(
                    symbol="x",
                    definition="bounded domain observation",
                    role="input",
                ),
                ScientificSubstrateVariable(
                    symbol="theta",
                    definition="mechanism parameter",
                    role="estimated parameter",
                ),
            ],
            assumptions=[
                ScientificSubstrateAssumption(
                    assumption_id="A1",
                    statement="Synthetic observations follow the declared bounded DGP.",
                    rationale="The experiment tests mechanism behavior under controlled inputs.",
                    violation_consequence="The result cannot be transferred outside scope.",
                )
            ],
            hypothesis=(
                "The proposed bounded mechanism improves held-out error relative to the null "
                "and regularized baselines."
            ),
            baseline_candidates=["null baseline", "regularized baseline"],
            experiment_or_proof_design=ScientificSubstrateExperimentDesign(
                target_claim="Bounded synthetic held-out improvement.",
                data_regime="synthetic_only",
                dgp="Generate fixed-seed observations under mechanism and null regimes.",
                train_test_split_optional="80/20 deterministic split",
                baseline="null and regularized baselines",
                method=f"fit the {method_id} operator",
                metrics=["held_out_mae", "held_out_rmse"],
                seed_plan="Seeds 11, 17, and 23.",
                ablation_or_stress_test="Remove the mechanism and increase noise.",
                success_criterion="Both held-out metrics improve across declared seeds.",
                failure_criterion="Either metric fails to improve or reverses under control.",
            ),
            benchmark_design="Compare proposed, null, and regularized models on held-out data.",
            negative_controls=["set theta to zero", "shuffle the mechanism labels"],
            result_schema=ScientificSubstrateResultSchema(
                baseline_metric_names=["baseline_mae", "baseline_rmse"],
                method_metric_names=["method_mae", "method_rmse"],
                comparison_direction="lower is better",
                required_table_columns=["seed", "baseline_mae", "method_mae"],
                claim_supported_if="method_mae < baseline_mae and method_rmse <= baseline_rmse",
                claim_not_supported_if="the bounded comparison rule is not satisfied",
            ),
            expected_metrics=["held_out_mae", "held_out_rmse"],
            failure_modes=["no improvement", "control reversal", "unstable parameter recovery"],
            limitations=["synthetic scope only", "finite seed set"],
            scope_boundary="This is a proposed bounded synthetic evaluation, not validation.",
            verification_path="Execute the declared local synthetic benchmark and retain failures.",
            route_hint=routes[call_index % len(routes)],
            novelty_risk="Hypothesis: retrieval may reveal a closely related construction.",
            false_bridge_risk="The method-object mapping may not survive the negative control.",
            tautology_risk="The DGP may favor the proposed operator.",
        )
        item = SubstrateProposalItem(
            candidate=candidate,
            score=SubstrateScoreProposal(
                model_concreteness=0.88,
                baseline_quality=0.85,
                verification_feasibility=0.84,
                assumption_clarity=0.82,
                metric_clarity=0.86,
                negative_control_quality=0.81,
                failure_mode_quality=0.80,
                paper_coherence=0.83,
                false_bridge_penalty=0.14,
                tautology_penalty=0.16,
                scope_risk_penalty=0.12,
                final_score=0.88 - call_index * 0.005,
                score_explanation="Mocked structured LLM substrate score.",
            ),
        )
        reasons = []
        accepted = item
        if self.reject_first and call_index == 0:
            accepted = None
            reasons = ["method vocabulary is decorative"]
        return SubstrateGenerationResponse(
            prompt=prompt,
            raw_response={"substrates": [item.model_dump(mode="json")]},
            accepted=accepted,
            rejection_reasons=reasons,
        )


class MockLLMRoutePlanner:
    backend_name = "llm-openai-mocked-transport"
    backend_kind = BackendKind.LLM_OPENAI
    model = "mock-route-planning-model"
    fallback_used = False
    fallback_disclosed = True

    def __init__(self) -> None:
        self.received_substrate_ids: list[str] = []
        self.raw_items: list[dict[str, object]] = []

    def plan_route(
        self,
        *,
        prompt_id,
        substrate_payload,
        source_metadata_payload,
        retrieval_context_payload,
    ):
        index = len(self.received_substrate_ids)
        substrate_id = substrate_payload["substrate_id"]
        self.received_substrate_ids.append(substrate_id)
        routes = [
            BranchRouteType.SYNTHETIC_EXPERIMENT,
            BranchRouteType.BENCHMARK_TOURNAMENT,
            BranchRouteType.COUNTEREXAMPLE_SEARCH,
            BranchRouteType.APPLIED_MATH_REDUCTION,
            BranchRouteType.LITERATURE_NOVELTY_CHECK,
            BranchRouteType.PROOF_PLAN,
        ]
        route = routes[index % len(routes)]
        prompt = build_llm_route_planning_prompt(
            prompt_id=prompt_id,
            backend_name=self.backend_name,
            model=self.model,
            substrate_payload=substrate_payload,
            source_metadata_payload=source_metadata_payload,
            retrieval_context_payload=retrieval_context_payload,
        )
        code_route = route in {
            BranchRouteType.SYNTHETIC_EXPERIMENT,
            BranchRouteType.BENCHMARK_TOURNAMENT,
            BranchRouteType.COUNTEREXAMPLE_SEARCH,
        }
        literature_route = route == BranchRouteType.LITERATURE_NOVELTY_CHECK
        proof_route = route == BranchRouteType.PROOF_PLAN
        labels = list(ROUTE_ALLOWED_LABELS[route])
        decision = RouteDecisionProposal(
            route_type=route,
            route_confidence=0.86,
            scientific_reason=(
                f"The {route.value} route directly tests the substrate's declared bounded "
                "question and retains negative outcomes."
            ),
            why_not_other_routes=[
                "A broader route would add authority not supported by the current substrate.",
                "The selected route has the clearest bounded failure criterion.",
            ],
            required_artifacts=["scientific_substrate", "route_execution_contract"],
            allowed_evidence_labels=labels,
            forbidden_claims=["publication ready"],
        )
        standard = source_metadata_payload["standard_scientific_substrate"]
        design = substrate_payload["experiment_or_proof_design"]
        spec = ExecutionSpecProposal(
            route_type=route,
            title=f"Bounded {route.value} for {substrate_id}",
            objective="Attempt the declared bounded claim while preserving negative results.",
            input_contract=RouteExecutionInputContract(
                substrate_title=substrate_payload["title"],
                domain=standard["domain"],
                model_type=substrate_payload["concrete_model_object"]["model_type"],
                equations=substrate_payload["concrete_model_object"]["equations"],
                variables_and_notation=substrate_payload["variables_and_notation"],
                assumptions=[item["statement"] for item in substrate_payload["assumptions"]],
                dgp_or_dataset=design["dgp"],
                baseline="; ".join(substrate_payload["baseline_candidates"]),
                proposed_method=design["method"],
                measurable_hypothesis=substrate_payload["hypothesis"],
                metrics=substrate_payload["expected_metrics"],
                seed=1729,
                route_parameters={"route_family": route.value, "bounded": True},
            ),
            output_contract=RouteExecutionOutputContract(
                required_metrics=substrate_payload["expected_metrics"],
                required_payload_fields=["status", "limitations", "negative_control_result"],
                scope_label="bounded planning output; no evidence until M102 execution",
                success_criterion=design["success_criterion"],
                failure_criterion=design["failure_criterion"],
            ),
            baseline_plan=substrate_payload["baseline_candidates"],
            control_plan=["hold the data regime and seed plan fixed"],
            negative_control_plan=substrate_payload["negative_controls"],
            robustness_plan=[design["ablation_or_stress_test"]],
            metric_plan=substrate_payload["expected_metrics"],
            success_criteria=[design["success_criterion"]],
            failure_criteria=[design["failure_criterion"]],
            proof_obligations=(
                ["Formalize the bounded proposition and discharge each assumption-dependent step."]
                if proof_route
                else []
            ),
            formalization_target_optional=(
                "A checker-readable bounded proposition" if proof_route else None
            ),
            retrieval_queries=(
                [f"{substrate_payload['title']} closest prior formulations"]
                if literature_route
                else []
            ),
            expected_artifacts=["execution_manifest", "bounded_result_or_draft"],
            sandbox_requirements=(
                ["offline local sandbox", "fixed seeds", "resource limits"] if code_route else []
            ),
            allowed_evidence_labels=labels,
            forbidden_claims=["publication ready"],
            execution_backend_required=(
                "uv_local"
                if code_route
                else "retrieval_real"
                if literature_route
                else "local_symbolic_draft"
            ),
            requires_code_generation=code_route,
            requires_literature_retrieval=literature_route,
            requires_symbolic_checker=False,
            requires_human_review=False,
        )
        item = RoutePlanningProposalItem(
            decision=decision,
            execution_spec=spec,
            score=RoutePlanningScoreProposal(
                route_fit=0.88,
                baseline_quality=0.84,
                control_quality=0.83,
                metric_clarity=0.86,
                execution_feasibility=0.80,
                claim_safety=0.92,
                failure_mode_value=0.82,
                paper_coherence=0.81,
                false_bridge_penalty=0.12,
                tautology_penalty=0.14,
                scope_risk_penalty=0.10,
                final_score=0.87 - index * 0.005,
                score_explanation="Mocked structured LLM route-planning score.",
            ),
        )
        self.raw_items.append(item.model_dump(mode="json"))
        accepted, reasons, repairs = parse_route_planning_response(
            {"plans": [item.model_dump(mode="json")]}
        )
        return RoutePlanningResponse(
            prompt=prompt,
            raw_response={"plans": [item.model_dump(mode="json")]},
            accepted=accepted,
            rejection_reasons=reasons,
            repair_actions=repairs,
        )


class MockHybridEvidencePlanner:
    backend_name = "llm-openai-mocked-transport"
    backend_kind = BackendKind.LLM_OPENAI
    model = "mock-hybrid-evidence-model"
    fallback_used = False
    fallback_disclosed = True

    def __init__(self) -> None:
        self.received_substrate_ids: list[str] = []
        self.drafted_artifact_types: list[str] = []
        self.raw_items: list[dict[str, object]] = []

    def plan_package(
        self,
        *,
        prompt_id,
        substrate_payload,
        route_payload,
        retrieval_context_payload,
    ):
        index = len(self.received_substrate_ids)
        self.received_substrate_ids.append(substrate_payload["substrate_id"])
        prompt_text, schema = build_hybrid_package_prompt(
            prompt_id=prompt_id,
            substrate_payload=substrate_payload,
            route_payload=route_payload,
            retrieval_context_payload=retrieval_context_payload,
        )
        artifact_sets = [
            [
                EvidenceArtifactType.SYMBOLIC_REDUCTION,
                EvidenceArtifactType.NUMERICAL_ILLUSTRATION,
                EvidenceArtifactType.LITERATURE_NOVELTY_CHECK,
                EvidenceArtifactType.PROOF_PLAN,
            ],
            [
                EvidenceArtifactType.BENCHMARK_TOURNAMENT,
                EvidenceArtifactType.NEGATIVE_CONTROL,
                EvidenceArtifactType.ROBUSTNESS_SWEEP,
            ],
            [
                EvidenceArtifactType.COUNTEREXAMPLE_SEARCH,
                EvidenceArtifactType.SYMBOLIC_DERIVATION,
            ],
            [EvidenceArtifactType.SYNTHETIC_EXPERIMENT],
        ]
        plans = [
            self._artifact_plan(artifact_type, substrate_payload, plan_index)
            for plan_index, artifact_type in enumerate(
                artifact_sets[index % len(artifact_sets)], start=1
            )
        ]
        package = HybridEvidencePackageProposal(
            title=f"Hybrid package for {substrate_payload['title']}",
            primary_claim_draft=(
                "Within the declared synthetic or draft scope, the package can support a "
                "bounded methodological claim only if required components succeed."
            ),
            allowed_claim_scope=(
                "bounded synthetic, draft-symbolic, or retrieval-risk context only"
            ),
            package_rationale=(
                "The package combines executable and draft artifacts so claims remain scoped "
                "to observed metrics, unresolved obligations, and retrieval-risk context."
            ),
            artifact_plans=plans,
            minimum_required_artifacts=[plans[0].artifact_type.value],
            optional_supporting_artifacts=[plan.artifact_type.value for plan in plans[1:]],
            artifact_dependency_graph={plan.artifact_type.value: [] for plan in plans},
            claim_support_map={"bounded_claim": [plan.artifact_type.value for plan in plans]},
            known_gaps=[
                "No artifact establishes novelty, theorem verification, or real-world validation."
            ],
            unresolved_obligations=["Formal proof and literature completeness remain unresolved."],
            recommended_next_action="Execute code components and retain symbolic obligations.",
        )
        item = HybridEvidencePackageProposalItem(
            package=package,
            score=HybridEvidencePackageScoreProposal(
                claim_specificity=0.86,
                artifact_coherence=0.84,
                verification_feasibility=0.80,
                baseline_quality=0.82,
                control_quality=0.81,
                negative_control_quality=0.79,
                symbolic_obligation_clarity=0.78,
                retrieval_need_clarity=0.77,
                execution_feasibility=0.76,
                paper_shape_clarity=0.83,
                false_bridge_penalty=0.12,
                tautology_penalty=0.13,
                scope_risk_penalty=0.14,
                final_score=0.84 - index * 0.01,
                score_explanation="Mocked structured LLM hybrid package score.",
            ),
        )
        payload = {"packages": [item.model_dump(mode="json")]}
        self.raw_items.append(item.model_dump(mode="json"))
        accepted, reasons, repairs = parse_hybrid_package_response(payload)
        return HybridEvidencePackageResponse(
            prompt_text=prompt_text,
            requested_output_schema=schema,
            raw_response=payload,
            accepted=accepted,
            rejection_reasons=reasons,
            repair_actions=repairs,
        )

    def draft_artifact(
        self,
        *,
        prompt_id,
        package_payload,
        artifact_plan_payload,
        retrieval_context_payload,
    ):
        artifact_type = EvidenceArtifactType(artifact_plan_payload["artifact_type"])
        self.drafted_artifact_types.append(artifact_type.value)
        prompt_text, schema = build_hybrid_draft_prompt(
            prompt_id=prompt_id,
            package_payload=package_payload,
            artifact_plan_payload=artifact_plan_payload,
            retrieval_context_payload=retrieval_context_payload,
        )
        draft = HybridEvidenceDraftArtifact(
            artifact_type=artifact_type,
            definitions=[
                "All objects are bounded to the package's declared synthetic or draft scope."
            ],
            assumptions=["The artifact is not checker-verified."],
            steps_or_plan=[
                "State definitions.",
                "List obligations.",
                "Identify unresolved proof or retrieval risks.",
            ],
            unresolved_obligations=[
                "A future checker or retrieval pass must discharge this obligation."
            ],
            novelty_risk_assessment=(
                "Closest-prior overlap remains a risk assessment, not novelty proof."
                if artifact_type == EvidenceArtifactType.LITERATURE_NOVELTY_CHECK
                else None
            ),
            overlap_risks=["Similar baselines may exist in adjacent literatures."],
            closest_prior_work=["retrieval-context source summaries only"],
            underuse_hypothesis="Hypothesis: the combination may be underused.",
        )
        payload = {"artifact": draft.model_dump(mode="json")}
        return HybridEvidenceDraftResponse(
            prompt_text=prompt_text,
            requested_output_schema=schema,
            raw_response=payload,
            accepted=draft,
            rejection_reasons=[],
        )

    def _artifact_plan(
        self,
        artifact_type: EvidenceArtifactType,
        substrate_payload: dict[str, object],
        index: int,
    ) -> EvidenceArtifactPlanProposal:
        code_type = artifact_type in {
            EvidenceArtifactType.NUMERICAL_ILLUSTRATION,
            EvidenceArtifactType.SYNTHETIC_EXPERIMENT,
            EvidenceArtifactType.BENCHMARK_TOURNAMENT,
            EvidenceArtifactType.COUNTEREXAMPLE_SEARCH,
            EvidenceArtifactType.NEGATIVE_CONTROL,
            EvidenceArtifactType.ROBUSTNESS_SWEEP,
        }
        symbolic_type = artifact_type in {
            EvidenceArtifactType.SYMBOLIC_REDUCTION,
            EvidenceArtifactType.SYMBOLIC_DERIVATION,
            EvidenceArtifactType.PROOF_PLAN,
        }
        retrieval_type = artifact_type == EvidenceArtifactType.LITERATURE_NOVELTY_CHECK
        metrics = ["held_out_mae", "held_out_rmse"] if code_type else []
        return EvidenceArtifactPlanProposal(
            artifact_type=artifact_type,
            purpose=f"Support package component {index} via {artifact_type.value}.",
            claim_component_supported="bounded_claim",
            input_contract={
                "substrate_title": substrate_payload["title"],
                "data_regime": "synthetic or draft only",
                "seed": 1729,
            },
            output_contract={"required_metrics": metrics} if metrics else {"draft_fields": True},
            baseline_or_comparator_plan=(
                ["declared bounded baseline or comparator"] if code_type else []
            ),
            control_plan_optional=["hold seed and data regime fixed"] if code_type else None,
            negative_control_plan_optional=(
                ["disable the proposed mechanism"] if code_type else None
            ),
            metric_plan_optional=metrics or None,
            symbolic_obligations_optional=(
                ["state assumptions", "mark checker status not_checked"] if symbolic_type else None
            ),
            retrieval_requirements_optional=(
                ["closest prior work query", "overlap-risk summary"] if retrieval_type else None
            ),
            checker_requirements_optional=(
                ["future formal checker"]
                if artifact_type == EvidenceArtifactType.PROOF_PLAN
                else None
            ),
            execution_backend_required=(
                "uv_local" if code_type else "retrieval_real" if retrieval_type else "llm_draft"
            ),
            requires_code_generation=code_type,
            requires_local_execution=code_type,
            requires_retrieval=retrieval_type,
            requires_symbolic_checker=False,
            requires_llm_drafting=symbolic_type or retrieval_type,
            allowed_evidence_labels=list(PACKAGE_ALLOWED_LABELS[artifact_type]),
            forbidden_claims=["publication ready"],
            success_criteria=["component succeeds only within declared package scope"],
            failure_criteria=["component is inconclusive if checks fail"],
        )


class MockScientificCritic:
    backend_name = "llm-openai-mocked-transport"
    backend_kind = BackendKind.LLM_OPENAI
    model = "mock-scientific-critic-model"
    fallback_used = False
    fallback_disclosed = True

    def __init__(self, *, block_primary: bool = False) -> None:
        self.block_primary = block_primary
        self.review_calls: list[tuple[str, ScientificCriticRole]] = []
        self.package_ids: list[str] = []
        self.adjudication_calls = 0

    def critique_package(
        self,
        *,
        prompt_id,
        critic_role,
        package_payload,
        execution_payload,
    ):
        package_id = package_payload["package_id"]
        self.review_calls.append((package_id, critic_role))
        if package_id not in self.package_ids:
            self.package_ids.append(package_id)
        package_index = self.package_ids.index(package_id)
        findings = []
        if critic_role == ScientificCriticRole.BASELINE_ADVERSARY and (
            package_index == 1 or (self.block_primary and package_index == 0)
        ):
            findings.append(
                CriticFindingProposal(
                    severity=ScientificCriticFindingSeverity.BLOCKING,
                    finding_type=ScientificCriticFindingType.WEAK_BASELINE,
                    description="The declared comparator is too weak for a primary claim.",
                    affected_claims=["bounded_claim"],
                    recommended_fix="Add a stronger comparator before synthesis.",
                    blocking=True,
                )
            )
        if critic_role == ScientificCriticRole.FALSE_BRIDGE_ADVERSARY and package_index == 2:
            findings.append(
                CriticFindingProposal(
                    severity=ScientificCriticFindingSeverity.BLOCKING,
                    finding_type=ScientificCriticFindingType.FALSE_BRIDGE,
                    description="The method does not survive the declared object mapping.",
                    affected_claims=["bounded_claim"],
                    recommended_fix="Reject the decorative bridge or rebuild the substrate.",
                    blocking=True,
                )
            )
        if critic_role == ScientificCriticRole.CLAIM_SCOPE_ADVERSARY and package_index == 3:
            findings.append(
                CriticFindingProposal(
                    severity=ScientificCriticFindingSeverity.BLOCKING,
                    finding_type=ScientificCriticFindingType.OVERCLAIM,
                    description="The claim scope exceeds the synthetic execution boundary.",
                    affected_claims=["bounded_claim"],
                    recommended_fix="Keep the claim within the executed synthetic setting.",
                    blocking=True,
                )
            )
        proposal = CriticReviewProposal(
            summary="Mocked role-specific scientific critique over persisted package artifacts.",
            findings=findings,
            score_delta=(
                0.12 if package_index == 0 and not findings else -0.35 if findings else -0.05
            ),
            recommended_decision=(
                EvidencePackageDecision.NEEDS_REPAIR
                if findings
                else EvidencePackageDecision.SUPPORTING_PACKAGE
            ),
        )
        payload = {"reviews": [proposal.model_dump(mode="json")]}
        return ScientificCriticResponse(
            prompt_text=f"mock critic prompt {prompt_id}",
            requested_output_schema={"type": "object"},
            raw_response=payload,
            accepted=proposal,
            rejection_reasons=[],
        )

    def adjudicate_packages(
        self,
        *,
        prompt_id,
        packages_payload,
        execution_payload,
        critic_reviews_payload,
        score_payload,
    ):
        self.adjudication_calls += 1
        results_by_package = {}
        for result in execution_payload:
            results_by_package.setdefault(result["package_id"], []).append(result)
        has_blocking_finding = {
            review["package_id"]
            for review in critic_reviews_payload
            if any(finding["blocking"] for finding in review["findings"])
        }
        eligible_package_ids = [
            package["package_id"]
            for package in packages_payload
            if any(
                result["status"] in {"completed", "negative_result"}
                for result in results_by_package[package["package_id"]]
            )
            and not any(
                result["artifact_type"] == EvidenceArtifactType.NEGATIVE_CONTROL.value
                and result["status"] != "completed"
                for result in results_by_package[package["package_id"]]
            )
            and package["package_id"] not in has_blocking_finding
        ]
        primary_id = (
            packages_payload[0]["package_id"] if self.block_primary else eligible_package_ids[0]
        )
        decisions = []
        for index, package in enumerate(packages_payload):
            if package["package_id"] == primary_id:
                decision = EvidencePackageDecision.PRIMARY_NUCLEUS
            elif index == 1:
                decision = EvidencePackageDecision.NEEDS_REPAIR
            elif index == 2:
                decision = EvidencePackageDecision.REJECT_FALSE_BRIDGE
            else:
                decision = EvidencePackageDecision.APPENDIX_PACKAGE
            decisions.append(
                AdjudicationDecisionProposal(
                    package_id=package["package_id"],
                    decision=decision,
                    rank=index + 1,
                    role=decision.value,
                    reason="Mocked bounded cross-package adjudication.",
                    required_repairs=(
                        ["Strengthen baseline"]
                        if decision == EvidencePackageDecision.NEEDS_REPAIR
                        else []
                    ),
                    allowed_claim_scope="bounded synthetic or draft scope only",
                    forbidden_claims=["publication ready"],
                    supporting_artifact_ids=[],
                    blocking_findings=[],
                    recommended_next_action="Retain bounded evidence boundaries.",
                )
            )
        nucleus = PaperNucleusProposal(
            primary_package_id=primary_id,
            central_claim_draft=(
                "In the executed synthetic setting, the selected method is compared with its "
                "declared baseline; the claim remains bounded to the observed artifacts."
            ),
            allowed_claim_scope="controlled synthetic execution only",
            forbidden_claims=["publication ready"],
            supporting_package_ids=[],
            appendix_package_ids=[packages_payload[3]["package_id"]],
            negative_package_ids=[],
            rejected_package_ids=[packages_payload[2]["package_id"]],
            required_repairs_before_manuscript=[],
            required_additional_checks=["Retain negative-control boundaries."],
        )
        proposal = CrossPackageAdjudicationProposal(
            decisions=decisions,
            paper_nucleus_selection_optional=nucleus,
        )
        payload = {"adjudications": [proposal.model_dump(mode="json")]}
        return CrossPackageAdjudicationResponse(
            prompt_text=f"mock adjudication prompt {prompt_id}",
            requested_output_schema={"type": "object"},
            raw_response=payload,
            accepted=proposal,
            rejection_reasons=[],
        )


class MockNucleusManuscriptPlanner:
    backend_name = "llm-openai-mocked-transport"
    backend_kind = BackendKind.LLM_OPENAI
    model = "mock-nucleus-manuscript-model"
    fallback_used = False
    fallback_disclosed = True

    def __init__(self, *, unsafe_draft: bool = False, block_after_revision: bool = False) -> None:
        self.unsafe_draft = unsafe_draft
        self.block_after_revision = block_after_revision
        self.critic_calls: list[tuple[str, ManuscriptCriticRole]] = []

    def plan_manuscript(self, *, prompt_id, nucleus_payload, evidence_payload):
        claim_id = evidence_payload["claim_artifact_bindings"][0]["claim_id"]
        citation_ids = [
            item["binding_id"] for item in evidence_payload["evidence_citation_bindings"]
        ]
        sections = [
            ManuscriptSectionProposal(
                section_id=f"section-{index:02d}",
                title=title,
                purpose=purpose,
                claim_ids=[claim_id] if include_claim else [],
                artifact_ids=evidence_payload["claim_artifact_bindings"][0][
                    "supporting_artifact_ids"
                ],
                supporting_package_ids=[],
                required_citations=citation_ids if include_claim else [],
                scope_constraints=[
                    "Keep all claims within the persisted synthetic or draft scope."
                ],
                bullets=[purpose],
            )
            for index, (title, purpose, include_claim) in enumerate(
                [
                    ("Introduction", "State the bounded research question.", False),
                    ("Problem Definition", "Define the declared synthetic problem.", True),
                    ("Method and Baseline", "Describe the method and comparator.", True),
                    ("Results", "Interpret only artifact-bound results.", True),
                    ("Limitations", "Expose unresolved obligations and scope limits.", False),
                    ("Conclusion", "Summarize the bounded contribution.", False),
                    (
                        "Appendix: Secondary and Negative Packages",
                        "Separate non-primary branches.",
                        False,
                    ),
                ],
                start=1,
            )
        ]
        proposal = ManuscriptPlanProposal(
            working_title="Bounded Synthetic Comparison of a Selected Mechanism",
            paper_type=NucleusPaperType.SYNTHETIC_BENCHMARK,
            central_question="How does the selected mechanism compare with its declared baseline?",
            central_claim=nucleus_payload["central_claim_draft"],
            section_plans=sections,
            supporting_package_roles={},
            appendix_package_roles={},
            negative_result_roles={},
        )
        payload = {"plans": [proposal.model_dump(mode="json")]}
        return NucleusManuscriptResponse(
            prompt_text=f"mock nucleus plan {prompt_id}",
            requested_output_schema={"type": "object"},
            raw_response=payload,
            accepted=proposal,
            rejection_reasons=[],
        )

    def synthesize_manuscript(self, *, prompt_id, plan_payload, evidence_payload):
        claim_id = evidence_payload["claim_artifact_bindings"][0]["claim_id"]
        markdown = """# Bounded Synthetic Comparison

## Introduction
This draft frames a bounded synthetic comparison rather than a workflow audit.

## Problem Definition
The question is restricted to the declared synthetic setting.

## Method and Baseline
The method is compared with the declared baseline and controls.

## Results
The artifact-bound claim is interpreted only under the declared metrics.

## Limitations
Unresolved obligations and synthetic-scope boundaries remain explicit.

## Conclusion
The result is a bounded draft.

## Appendix: Secondary and Negative Packages
Secondary packages are not used as support for the central claim.
"""
        if self.unsafe_draft:
            markdown += "\nThis establishes real-world validation.\n"
        proposal = ManuscriptDraftProposal(
            title=plan_payload["working_title"],
            abstract="A bounded synthetic manuscript draft with artifact-linked claims.",
            markdown=markdown,
            latex="\\section*{Introduction}\nBounded synthetic draft.\n",
            claim_ids_used=[claim_id],
            citation_binding_ids=[
                item["binding_id"] for item in evidence_payload["evidence_citation_bindings"]
            ],
        )
        payload = {"drafts": [proposal.model_dump(mode="json")]}
        return NucleusManuscriptResponse(
            prompt_text=f"mock nucleus synthesis {prompt_id}",
            requested_output_schema={"type": "object"},
            raw_response=payload,
            accepted=proposal,
            rejection_reasons=[],
        )

    def critique_manuscript(self, *, prompt_id, critic_role, draft_payload, evidence_payload):
        self.critic_calls.append((draft_payload["draft_id"], critic_role))
        post_revision = draft_payload.get("source_draft_id_optional") is not None
        blocking = (
            ["The revised claim still lacks the required qualification."]
            if post_revision and self.block_after_revision
            else []
        )
        proposal = ManuscriptCriticProposal(
            findings=["Keep the synthetic-scope qualification visible."],
            blocking_findings=blocking,
            recommended_revisions=["Retain limitations and artifact-bound metric table."],
            score=0.78,
        )
        payload = {"reviews": [proposal.model_dump(mode="json")]}
        return NucleusManuscriptResponse(
            prompt_text=f"mock manuscript critic {prompt_id}",
            requested_output_schema={"type": "object"},
            raw_response=payload,
            accepted=proposal,
            rejection_reasons=[],
        )

    def revise_manuscript(
        self, *, prompt_id, draft_payload, critic_reviews_payload, evidence_payload
    ):
        proposal = ManuscriptRevisionProposal(
            title=draft_payload["title"],
            abstract=draft_payload["abstract"],
            markdown=draft_payload["markdown"],
            latex=draft_payload["latex"],
            claim_ids_used=draft_payload["claim_ids_used"],
            citation_binding_ids=draft_payload["citation_binding_ids"],
            applied_recommendations=["Retained explicit synthetic-scope limitation."],
        )
        payload = {"revisions": [proposal.model_dump(mode="json")]}
        return NucleusManuscriptResponse(
            prompt_text=f"mock manuscript revision {prompt_id}",
            requested_output_schema={"type": "object"},
            raw_response=payload,
            accepted=proposal,
            rejection_reasons=[],
        )


class MockLLMExperimentCodeGenerator:
    backend_name = "llm-openai-mocked-transport"
    backend_kind = BackendKind.LLM_OPENAI
    model = "mock-experiment-code-model"
    fallback_used = False
    fallback_disclosed = True

    def __init__(
        self,
        *,
        unsafe_index: int = 2,
        negative_control_failure_index: int = 3,
        runtime_failure_index: int = 4,
    ) -> None:
        self.received_spec_ids: list[str] = []
        self.unsafe_index = unsafe_index
        self.negative_control_failure_index = negative_control_failure_index
        self.runtime_failure_index = runtime_failure_index

    def generate_code(self, *, spec_payload, substrate_payload, allowed_dependencies):
        index = len(self.received_spec_ids)
        self.received_spec_ids.append(spec_payload["spec_id"])
        prompt, schema = build_experiment_codegen_prompt(
            spec_payload=spec_payload,
            substrate_payload=substrate_payload,
            allowed_dependencies=allowed_dependencies,
        )
        required_metrics = spec_payload["output_contract"]["required_metrics"]
        metric_assignments = "\n".join(
            f"metric_{metric_index} = held_out_mae + ({metric_index} * 0.0)"
            for metric_index, _ in enumerate(required_metrics)
        )
        metric_items = ",\n        ".join(
            f"{metric!r}: metric_{metric_index}"
            for metric_index, metric in enumerate(required_metrics)
        )
        negative_controls_passed = index != self.negative_control_failure_index
        runtime_failure = index == self.runtime_failure_index
        unsafe_import = "import subprocess\n" if index == self.unsafe_index else ""
        failure_line = (
            "raise RuntimeError('intentional execution failure')\n" if runtime_failure else ""
        )
        code = f"""import json
import random
{unsafe_import}
SEED = 1729
rng = random.Random(SEED)
observations = [rng.random() for _ in range(24)]
baseline_predictions = [0.0 for _ in observations]
method_predictions = [value * 0.8 for value in observations]
baseline_errors = [
    abs(value - prediction)
    for value, prediction in zip(observations, baseline_predictions)
]
method_errors = [
    abs(value - prediction)
    for value, prediction in zip(observations, method_predictions)
]
baseline_mae = sum(baseline_errors) / len(baseline_errors)
held_out_mae = sum(method_errors) / len(method_errors)
negative_control_values = list(baseline_errors)
{metric_assignments}
{failure_line}payload = {{
    "metrics": {{
        {metric_items}
    }},
    "baseline_summary": "Computed null baseline on generated observations.",
    "control_summary": "Seed and sample count were held fixed.",
    "negative_control_summary": "Computed mechanism-disabled negative control.",
    "negative_controls_passed": {negative_controls_passed!r},
    "success_criteria_satisfied": True,
    "failure_criteria_satisfied": False,
}}
with open("output.json", "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
print("completed bounded experiment")
"""
        proposal = ExperimentCodeProposal(
            code=code,
            expected_output_files=["output.json"],
            required_inputs=[],
            declared_dependencies=[],
            random_seed=1729,
            timeout_seconds=10,
        )
        payload = ExperimentCodeProposalEnvelope(experiment=proposal).model_dump(mode="json")
        accepted, reasons = parse_experiment_codegen_response(
            payload,
            allowed_dependencies=allowed_dependencies,
        )
        return ExperimentCodeGenerationResponse(
            prompt_text=prompt,
            requested_output_schema=schema,
            raw_response=payload,
            accepted=accepted,
            rejection_reasons=reasons,
        )


def _prepare_m102_route_fixture(tmp_path: Path, run_id: str):
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    build_domain_method_atlas(run_id=run_id, root=tmp_path, store=store, ledger=ledger)
    scan_domain_method_pairs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        ranker=MockAtlasPairRanker(),
        top_pairs=8,
        require_non_fake_backends=True,
        batch_size=100,
        max_ranking_calls=10,
    )
    discover_deep_opportunities(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=MockDeepOpportunityGenerator(),
        retriever=MockRealOpportunityRetriever(),
        config=DeepOpportunityDiscoveryConfig(
            run_id=run_id,
            backend="llm-openai",
            retrieval_mode="real_retrieval",
            max_pairs=8,
            max_generation_calls=8,
            opportunities_per_pair=2,
            max_selected_opportunities=16,
            require_non_fake_backends=True,
        ),
    )
    generate_llm_variance(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=MockLLMVarianceGenerator(),
        config=LLMVarianceGenerationConfig(
            run_id=run_id,
            backend="llm-openai",
            max_source_opportunities=6,
            variants_per_opportunity=5,
            max_variants_total=30,
            max_selected_variants=20,
            max_generation_calls=6,
            min_variant_family_coverage=5,
            min_domain_family_coverage=4,
            min_method_family_coverage=4,
            require_non_fake_backends=True,
        ),
    )
    construct_idea_tree_from_llm_variance(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    construct_llm_substrates(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=MockLLMSubstrateGenerator(),
        config=LLMSubstrateConstructionConfig(
            run_id=run_id,
            backend="llm-openai",
            max_source_variants=10,
            max_constructed_substrates=10,
            max_selected_substrates=8,
            max_generation_calls=10,
            min_domain_family_coverage=4,
            min_method_family_coverage=4,
            min_route_hint_coverage=3,
            require_non_fake_backends=True,
        ),
    )
    route_result = plan_llm_routes(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=MockLLMRoutePlanner(),
        config=LLMRoutePlanningConfig(
            run_id=run_id,
            backend="llm-openai",
            max_source_substrates=8,
            max_planning_calls=8,
            require_non_fake_backends=True,
        ),
    )
    return store, ledger, route_result


def test_full_paper_generation_models_are_importable() -> None:
    assert FullPaperGenerationConfig
    assert FullPaperArtifactBundle
    assert FullPaperGenerationReport
    assert BranchRouteType
    assert BranchRouteExecutionHint
    assert BranchRouteDecision
    assert BranchRoutePlan
    assert BranchRouteInspectionReport
    assert RouteExecutionStatus
    assert RouteExecutionInputContract
    assert RouteExecutionOutputContract
    assert RouteExecutionSpec
    assert RouteExecutionResult
    assert RouteExecutionReport
    assert RouteExecutionInspectionReport
    assert BackendKind
    assert ScientificStageKind
    assert StageBackendRecord
    assert ProductionModePolicy
    assert ProductionModeViolation
    assert ProductionModeReport
    assert DomainAtlasEntry
    assert MethodAtlasEntry
    assert DomainMethodPair
    assert CompatibilityExclusion
    assert CompatibilityFilterReport
    assert LLMPairRankingPrompt
    assert LLMPairRankingResult
    assert LLMPairRankingReport
    assert AtlasScanReport
    assert DeepOpportunityDiscoveryConfig
    assert RetrievedSourceSummary
    assert RetrievalContext
    assert DeepOpportunityCandidate
    assert DeepOpportunityScore
    assert LLMOpportunityDiscoveryRawArtifact
    assert LLMSubstrateConstructionConfig
    assert LLMSubstratePrompt
    assert LLMScientificSubstrateCandidate
    assert LLMSubstrateConstructionScore
    assert LLMSubstrateConstructionReport
    assert LLMSubstrateConstructionInspectionReport
    assert LLMSubstrateRawArtifact
    assert LLMRoutePlanningConfig
    assert LLMRoutePlanningPrompt
    assert LLMRouteDecisionCandidate
    assert LLMExecutionSpecCandidate
    assert LLMRoutePlanningScore
    assert LLMRoutePlanningRawArtifact
    assert LLMRoutePlanningReport
    assert LLMRoutePlanningInspectionReport
    assert LLMExperimentCodegenConfig
    assert LLMExperimentCodeArtifact
    assert ExperimentCodeSafetyAudit
    assert SandboxExecutionConfig
    assert SandboxExecutionResult
    assert MetricExtractionResult
    assert GeneratedExperimentResult
    assert GeneratedExperimentExecutionReport
    assert GeneratedExperimentInspectionReport
    assert LLMExperimentCodeRawArtifact
    assert EvidenceArtifactType
    assert HybridEvidencePackageConfig
    assert EvidenceArtifactPlan
    assert HybridEvidencePackageCandidate
    assert HybridEvidencePackageScore
    assert HybridEvidencePackageRawArtifact
    assert HybridEvidencePackageReport
    assert HybridEvidencePackageInspectionReport
    assert EvidencePackageExecutionResult
    assert EvidencePackageExecutionReport
    assert EvidencePackageExecutionInspectionReport
    assert ScientificCriticRole
    assert ScientificCriticFindingSeverity
    assert ScientificCriticFindingType
    assert EvidencePackageDecision
    assert ScientificCriticFinding
    assert ScientificCriticReview
    assert EvidencePackageAdjudicationScore
    assert EvidencePackageAdjudicationDecision
    assert PaperNucleusSelection
    assert CrossPackageAdjudicationReport
    assert CrossPackageAdjudicationInspectionReport
    assert ScientificCriticRawArtifact
    assert NucleusPaperType
    assert ManuscriptCriticRole
    assert NucleusManuscriptStatus
    assert NucleusManuscriptConfig
    assert ClaimArtifactBinding
    assert EvidenceCitationBinding
    assert NucleusManuscriptPlan
    assert NucleusManuscriptDraft
    assert ManuscriptCriticReview
    assert ManuscriptRevisionReport
    assert NucleusManuscriptSynthesisReport
    assert NucleusManuscriptInspectionReport
    assert NucleusManuscriptRawArtifact
    assert FinalPaperAssemblyConfig
    assert FinalPaperManifest
    assert FinalPaperAssemblyReport
    assert FinalPaperVerificationReport
    assert DeepOpportunityDiscoveryReport
    assert DeepOpportunityDiscoveryInspectionReport
    assert LLMVarianceGenerationConfig
    assert LLMVariancePrompt
    assert LLMVarianceCandidate
    assert LLMVarianceScore
    assert LLMVarianceBatch
    assert LLMVarianceRawArtifact
    assert LLMVarianceGenerationReport
    assert LLMVarianceGenerationInspectionReport
    assert IdeaTreeConstructionReport
    assert AtlasScanInspectionReport
    assert QualityRepairReport
    assert ReviewerBundleSummary
    assert HumanReviewArtifact
    assert ProofArtifact
    assert ExperimentArtifact
    assert ClaimEvidenceMap
    assert ClaimEvidenceMapLink
    assert EvidenceAwareRefreshReport
    assert HumanReviewReconciliationReport
    assert AutonomousEvidenceGapPlan
    assert AutonomousPlanExecutionReport
    assert PlannedSpecExecutionReport
    assert AutonomousLoopRunReport
    assert AutonomousLoopIndex
    assert GapAttemptRecord
    assert GapAttemptHistory
    assert PlannedSpecDuplicateRecord
    assert PlannedSpecDedupIndex
    assert ExperimentTemplate
    assert ExperimentTemplateRegistry
    assert ExperimentGapRoutingReport
    assert ExperimentGapRoutingIndex
    assert SandboxBudgetPolicy
    assert SandboxBudgetReport
    assert CapabilityEscalationPolicy
    assert CapabilityEscalationItem
    assert CapabilityEscalationReport
    assert CapabilityEscalationIndex
    assert FinalManuscriptSection
    assert FinalManuscriptClaimSummary
    assert FinalManuscriptStructuredDocument
    assert FinalManuscriptRegenerationReport
    assert FinalManuscriptRegenerationIndex
    assert FinalReleaseBundleArtifact
    assert FinalReleaseBundleManifest
    assert FinalReleaseReproducibilityManifest
    assert FinalReleaseBundle
    assert FinalReleaseBundleReport
    assert FinalReleaseBundleIndex
    assert AutonomousPaperRunStage
    assert AutonomousPaperRunHandoff
    assert AutonomousPaperRunReport
    assert AutonomousPaperRunIndex
    assert IdeaNode
    assert IdeaEdge
    assert IdeaTree
    assert IdeaTreeInspectionReport
    assert IdeaTreeExportReport
    assert DomainPrimitive
    assert MethodLens
    assert OpportunityCandidate
    assert OpportunityScoreBreakdown
    assert OpportunityDiscoveryReport
    assert OpportunityDiscoveryInspectionReport
    assert OpportunitySeedConstraint
    assert VarianceAugmentationConfig
    assert VarianceAugmentedCandidate
    assert VarianceAugmentationBatch
    assert VarianceAugmentationReport
    assert VarianceAugmentationInspectionReport
    assert VarianceDiversityDiagnostic
    assert IdeaNodeFeatureVector
    assert IdeaSpaceAxis
    assert IdeaSpacePCADiagnostic
    assert IdeaClusterDiagnostic
    assert IdeaSpaceDiversityReport
    assert IdeaSpaceInspectionReport
    assert ScientificSubstrateVariable
    assert ScientificSubstrateAssumption
    assert ScientificSubstrateModelObject
    assert ScientificSubstrateExperimentDesign
    assert ScientificSubstrateResultSchema
    assert ScientificSubstrate
    assert ScientificSubstrateBuildReport
    assert ScientificSubstrateInspectionReport
    assert SubstratePromotionConfig
    assert SubstratePromotionCandidate
    assert SubstratePromotionDecision
    assert SubstratePromotionReport
    assert SubstratePromotionInspectionReport
    assert SubstrateExperimentSpec
    assert SubstrateExperimentRoutingReport
    assert SubstrateExperimentResult
    assert SubstrateExperimentComparisonTable
    assert SubstrateTournamentSpec
    assert SubstrateTournamentEntry
    assert SubstrateTournamentResult
    assert SubstrateTournamentComparison
    assert SubstrateTournamentInspectionReport
    assert CreativeMutationCandidate
    assert CreativeMutationPlan
    assert CreativeMutationReport
    assert CreativeMutationInspectionReport
    assert MutationTournamentSpec
    assert MutationTournamentEntry
    assert MutationTournamentResult
    assert MutationTournamentComparison
    assert MutationTournamentInspectionReport
    assert CreativeSearchControllerConfig
    assert CreativeSearchCycle
    assert CreativeSearchLineageEntry
    assert CreativeSearchControllerReport
    assert CreativeSearchInspectionReport
    assert GenerationMutationContext
    assert GenerationMutationOperator
    assert GenerationMutationCandidate
    assert GenerationMutationPlan
    assert GenerationMutationDiversityCheck
    assert GenerationMutationInspectionReport


def test_opportunity_discovery_extracts_primitives_and_scores_easy_wins(
    tmp_path,
) -> None:
    run_id = "run-opportunity-discovery"

    human_primitives = extract_domain_primitives("human geography")
    market_primitives = extract_domain_primitives("market microstructure")
    finance_primitives = extract_domain_primitives("robust finance")
    generic_primitives = extract_domain_primitives("comparative ritual systems")
    lenses = method_lens_library()
    report = build_opportunity_discovery_report(
        run_id=run_id,
        domain="human geography",
        max_methods=20,
    )
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    result = discover_opportunities(
        run_id=run_id,
        domain="human geography",
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_methods=20,
    )
    inspection = inspect_opportunities(run_id=run_id, root=tmp_path)

    assert {primitive.name for primitive in human_primitives} >= {
        "flows",
        "distance",
        "mobility",
        "boundary effects",
    }
    assert {primitive.name for primitive in market_primitives} >= {
        "order flow",
        "limit order book",
        "arrival intensity",
        "price impact",
    }
    assert {primitive.name for primitive in finance_primitives} >= {
        "loss distributions",
        "risk measures",
        "ambiguity sets",
        "Wasserstein distance",
    }
    assert generic_primitives
    assert {lens.name for lens in lenses} >= {
        "optimal transport",
        "copulas",
        "graph curvature",
        "spatial statistics",
        "stochastic processes",
        "topological data analysis",
        "causal inference",
        "information geometry",
        "robust optimization",
        "distributionally robust optimization",
        "kernel methods",
        "matrix factorization",
        "agent-based modeling",
        "convex duality",
        "PDE / diffusion",
        "network science",
    }
    assert report.primitive_count > 0
    assert report.method_lens_count >= 10
    assert report.opportunity_count >= 10
    assert report.promoted_count >= 2
    assert report.seed_constraint_count == report.promoted_count
    assert all(
        opportunity.score_breakdown.O_final >= OPPORTUNITY_THRESHOLD
        for opportunity in report.opportunities
        if opportunity.score_breakdown.promoted
    )
    assert all(
        opportunity.score_breakdown.O_final < OPPORTUNITY_THRESHOLD
        for opportunity in report.opportunities
        if not opportunity.score_breakdown.promoted
    )
    assert all(
        opportunity.score_breakdown.S_underuse == 0
        for opportunity in report.opportunities
        if opportunity.score_breakdown.S_fit < 0.70 or opportunity.score_breakdown.S_verify < 0.60
    )
    assert any(
        opportunity.false_bridge_reasons and not opportunity.score_breakdown.promoted
        for opportunity in report.opportunities
    )
    assert any(
        opportunity.method_lens.name != "spatial statistics"
        for opportunity in report.opportunities
        if opportunity.score_breakdown.promoted
    )
    assert result.persistence.commit.action_type == (
        ControllerActionType.OPPORTUNITY_DISCOVERY_WRITTEN
    )
    assert result.report_artifact.path.endswith("opportunity-discovery-0001.json")
    assert (tmp_path / "runs" / run_id / "reports" / "opportunity-discovery-0001.md").is_file()
    assert inspection.opportunity_discovery_present is True
    assert inspection.promoted_count == result.report.promoted_count
    assert inspection.seed_constraint_count == result.report.seed_constraint_count
    assert inspection.report_optional is not None
    assert inspection.report_optional.publication_ready is False
    assert inspection.report_optional.creates_scientific_validation is False
    assert inspection.report_optional.is_verification_evidence is False


def test_opportunity_seeded_variance_augments_and_applies_idea_tree(tmp_path) -> None:
    run_id = "run-variance-augmentation"
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    discovery = discover_opportunities(
        run_id=run_id,
        domain="human geography",
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_methods=20,
    )

    result = augment_variance(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        candidates_per_seed=4,
        max_total_candidates=40,
    )
    inspection = inspect_variance_augmentation(run_id=run_id, root=tmp_path)

    assert result.report.seed_count == discovery.report.seed_constraint_count == 8
    assert result.report.candidate_count == 32
    assert result.report.selected_candidate_count >= 16
    assert result.report.diversity_diagnostic.method_lens_coverage >= 6
    assert result.report.diversity_diagnostic.diversity_score in {"moderate", "high"}
    assert set(result.report.method_lens_candidate_counts) >= {
        "optimal transport",
        "matrix factorization",
        "graph curvature",
        "topological data analysis",
        "agent-based modeling",
        "spatial statistics",
        "network science",
        "kernel methods",
    }
    assert all(batch.candidate_count == 4 for batch in result.report.batches)
    assert all(batch.selected_candidate_count >= 2 for batch in result.report.batches)
    assert {
        candidate.variant_family
        for candidate in result.report.candidates
        if candidate.selected_for_idea_tree
    } == {
        "mechanism_variant",
        "robustness_variant",
        "counterexample_variant",
        "benchmark_variant",
        "representation_variant",
    }
    for batch in result.report.batches:
        assert len({candidate.research_question for candidate in batch.candidates}) == 4
        assert len({candidate.hypothesis for candidate in batch.candidates}) == 4
        assert len({candidate.theory_object for candidate in batch.candidates}) == 4
        assert len({candidate.experiment_or_proof_plan for candidate in batch.candidates}) == 4
        assert len({candidate.baseline for candidate in batch.candidates}) >= 2
    assert inspection.variance_augmentation_present is True
    assert inspection.candidate_count == 32
    assert inspection.publication_ready is False
    assert result.report.creates_scientific_validation is False
    assert result.report.is_verification_evidence is False
    assert (
        result.persistence.commit.action_type == ControllerActionType.VARIANCE_AUGMENTATION_WRITTEN
    )

    duplicated = list(result.report.candidates)
    duplicated[1] = duplicated[1].model_copy(
        update={"research_question": duplicated[0].research_question}
    )
    duplicate_diagnostic = build_variance_diversity_diagnostic(
        candidates=duplicated,
        expected_method_lenses=[batch.method_lens for batch in result.report.batches],
    )
    assert duplicate_diagnostic.research_question_duplicate_count >= 1

    applied = apply_variance_augmentation(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    tree = inspect_idea_tree(run_id=run_id, root=tmp_path)
    variance_nodes = [node for node in tree.nodes if node.stage_origin == "variance_augmentation"]
    seed_nodes = [node for node in tree.nodes if node.stage_origin == "opportunity_seed"]

    assert applied.report.idea_tree_nodes_added == result.report.selected_candidate_count
    assert (
        applied.persistence.commit.action_type == ControllerActionType.VARIANCE_AUGMENTATION_APPLIED
    )
    assert len(variance_nodes) == result.report.selected_candidate_count
    assert len(seed_nodes) == result.report.seed_count
    assert all(node.source_opportunity_id_optional for node in variance_nodes)
    assert all(node.source_method_lens_id_optional for node in variance_nodes)
    seed_node_ids = {node.node_id for node in seed_nodes}
    assert all(node.parent_id_optional in seed_node_ids for node in variance_nodes)
    assert tree.publication_ready is False
    assert tree.creates_scientific_validation is False

    augment_variance(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        candidates_per_seed=4,
        max_total_candidates=40,
    )
    pending = inspect_variance_augmentation(run_id=run_id, root=tmp_path)
    assert pending.latest_augmentation_id_optional == "variance-augmentation-0002"
    assert pending.applied_to_idea_tree is False


def test_variance_substrate_promotion_preserves_method_and_branch_diversity(
    tmp_path,
) -> None:
    run_id = "run-substrate-promotion"
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    discover_opportunities(
        run_id=run_id,
        domain="human geography",
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_methods=20,
    )
    augment_variance(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        candidates_per_seed=4,
        max_total_candidates=40,
    )
    apply_variance_augmentation(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    result = promote_variance_substrates(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_substrates=8,
    )
    inspection = inspect_substrate_promotion(run_id=run_id, root=tmp_path)
    substrate_inspection = inspect_scientific_substrate(
        run_id=run_id,
        root=tmp_path,
    )
    tree = inspect_idea_tree(run_id=run_id, root=tmp_path)

    assert result.report.promoted_substrate_count == 8
    assert result.report.promoted_substrate_count <= 8
    assert result.report.method_lens_coverage == 8
    assert result.report.branch_family_coverage >= 3
    assert len(set(result.report.created_substrate_ids)) == 8
    assert {substrate.source_method_lens_id_optional for substrate in result.substrates} == {
        "optimal_transport",
        "matrix_factorization",
        "graph_curvature",
        "topological_data_analysis",
        "agent_based_modeling",
        "spatial_statistics",
        "network_science",
        "kernel_methods",
    }
    assert {substrate.title for substrate in result.substrates} >= {
        "Wasserstein Robustness of Synthetic Spatial Accessibility Rankings",
        "Low-Rank Residual Structure in Synthetic OD-Flow Heterogeneity",
        "Curvature-Based Bottleneck Diagnostics in Synthetic Mobility Networks",
        "Persistent Accessibility Structure Under Boundary Perturbation",
        "Emergent Distance Decay from Heterogeneous Agent Accessibility Rules",
        "Spatial Autocorrelation Diagnostics for Gravity Residual Misspecification",
        "Boundary Stability of Mobility Communities in Synthetic OD Networks",
        "Kernelized Spatial Interaction Under Nonmonotone Synthetic Regional Affinity",
    }
    for substrate in result.substrates:
        assert substrate.concrete_model_object.equations
        assert substrate.baseline
        assert substrate.measurable_hypothesis
        assert substrate.experiment_design.target_claim
        assert substrate.experiment_design.metrics
        assert substrate.result_schema.required_table_columns
        assert substrate.limitations
        assert substrate.failure_modes
        assert substrate.source_variance_candidate_id_optional
        assert substrate.source_opportunity_id_optional
        assert substrate.source_method_lens_id_optional
        assert substrate.publication_ready is False
        assert substrate.creates_scientific_validation is False
        assert substrate.is_verification_evidence is False
    assert result.persistence.commit.action_type == (
        ControllerActionType.VARIANCE_SUBSTRATES_PROMOTED
    )
    assert inspection.substrate_promotion_present is True
    assert inspection.idea_tree_substrate_links_present is True
    assert inspection.promoted_substrate_count == 8
    assert all(decision.reason for decision in inspection.rejected_candidates)
    assert substrate_inspection.substrate_count == 8
    assert substrate_inspection.pca_low_rank_substrate_present is True
    linked_nodes = [node for node in tree.nodes if node.scientific_substrate_ids]
    assert len(linked_nodes) == 8
    assert all(node.scientific_substrate_paths for node in linked_nodes)
    assert tree.publication_ready is False


def test_general_branch_router_routes_promoted_substrates_and_fails_closed(
    tmp_path,
) -> None:
    run_id = "run-general-branch-router"
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    discover_opportunities(
        run_id=run_id,
        domain="human geography",
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_methods=20,
    )
    augment_variance(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        candidates_per_seed=4,
        max_total_candidates=40,
    )
    apply_variance_augmentation(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    promotion = promote_variance_substrates(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_substrates=8,
    )

    result = route_branches(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    inspection = inspect_branch_routes(run_id=run_id, root=tmp_path)

    assert result.plan.substrate_count == 8
    assert result.plan.route_count == result.plan.substrate_count
    assert result.plan.routed_count == 8
    assert result.plan.deferred_count == 0
    assert result.plan.rejected_count == 0
    assert len(result.plan.route_type_counts) >= 3
    assert result.plan.route_type_counts == {
        "applied_math_reduction": 1,
        "benchmark_tournament": 5,
        "synthetic_experiment": 2,
    }
    by_method = {decision.method_lens: decision for decision in result.plan.decisions}
    assert by_method["optimal transport"].route_type == (BranchRouteType.APPLIED_MATH_REDUCTION)
    assert by_method["matrix factorization"].route_type == (BranchRouteType.BENCHMARK_TOURNAMENT)
    assert by_method["graph curvature"].route_type == (BranchRouteType.SYNTHETIC_EXPERIMENT)
    assert by_method["agent based modeling"].route_type == (BranchRouteType.SYNTHETIC_EXPERIMENT)
    assert by_method["network science"].route_type == (BranchRouteType.BENCHMARK_TOURNAMENT)
    assert all(decision.execution_hint.executes_now is False for decision in result.plan.decisions)
    assert all(
        decision.execution_hint.network_required is False for decision in result.plan.decisions
    )
    assert all(decision.publication_ready is False for decision in result.plan.decisions)
    assert all(
        decision.creates_scientific_validation is False
        and decision.is_verification_evidence is False
        for decision in result.plan.decisions
    )
    assert result.persistence.commit.action_type == ControllerActionType.BRANCH_ROUTES_WRITTEN
    assert inspection.branch_routes_present is True
    assert inspection.route_count == 8
    assert inspection.publication_ready is False
    assert (tmp_path / "runs" / run_id / "reports" / "branch-route-plan-0001.md").is_file()

    source = promotion.substrates[0]
    counterexample = source.model_copy(
        update={
            "substrate_id": "counterexample-substrate",
            "title": "Counterexample: when does the benchmark fail?",
            "source_mutation_axis_optional": "matrix factorization / counterexample_variant",
        }
    )
    counterexample_decision = build_branch_route_decision(
        run_id=run_id,
        route_id="counterexample-route",
        substrate=counterexample,
    )
    assert counterexample_decision.route_type == BranchRouteType.COUNTEREXAMPLE_SEARCH

    weak_design = source.experiment_design.model_copy(update={"baseline": "none"})
    weak_model = source.concrete_model_object.model_copy(
        update={"model_type": "placeholder", "equations": ["none"]}
    )
    weak = source.model_copy(
        update={
            "substrate_id": "weak-substrate",
            "concrete_model_object": weak_model,
            "baseline": "none",
            "experiment_design": weak_design,
        }
    )
    weak_decision = build_branch_route_decision(
        run_id=run_id,
        route_id="weak-route",
        substrate=weak,
    )
    assert weak_decision.route_type == BranchRouteType.DEFER_INSUFFICIENT_SUBSTRATE
    assert weak_decision.defer_or_reject_reason_optional
    assert weak_decision.execution_hint.ready_for_execution is False

    false_bridge_model = source.concrete_model_object.model_copy(
        update={"model_type": "decorative"}
    )
    false_bridge = source.model_copy(
        update={
            "substrate_id": "false-bridge-substrate",
            "concrete_model_object": false_bridge_model,
            "mechanism": "Decorative method vocabulary with no primitive mapping.",
        }
    )
    false_bridge_decision = build_branch_route_decision(
        run_id=run_id,
        route_id="false-bridge-route",
        substrate=false_bridge,
    )
    assert false_bridge_decision.route_type == BranchRouteType.REJECT_FALSE_BRIDGE
    assert false_bridge_decision.defer_or_reject_reason_optional
    assert false_bridge_decision.publication_ready is False


def test_route_execution_builds_specs_and_runs_general_back_half(tmp_path) -> None:
    run_id = "run-route-execution"
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    discover_opportunities(
        run_id=run_id,
        domain="human geography",
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_methods=20,
    )
    augment_variance(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        candidates_per_seed=4,
        max_total_candidates=40,
    )
    apply_variance_augmentation(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    promote_variance_substrates(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_substrates=8,
    )
    route_branches(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    built = build_route_execution_specs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    executed = run_route_execution(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    inspection = inspect_route_execution(run_id=run_id, root=tmp_path)

    assert built.report.report_status == RouteExecutionStatus.SPEC_CREATED
    assert built.report.route_count == built.report.spec_count == 8
    assert len(built.specs) == len({spec.route_id for spec in built.specs}) == 8
    assert all(spec.input_contract.equations for spec in built.specs)
    assert all(spec.input_contract.baseline for spec in built.specs)
    assert all(spec.input_contract.measurable_hypothesis for spec in built.specs)
    assert all(spec.output_contract.scope_label for spec in built.specs)
    assert all("real-world validation" in spec.forbidden_claims for spec in built.specs)
    assert all(spec.creates_real_world_validation is False for spec in built.specs)
    assert built.persistence.commit.action_type == (
        ControllerActionType.ROUTE_EXECUTION_SPECS_WRITTEN
    )

    report = executed.report
    assert report.report_status == RouteExecutionStatus.COMPLETED
    assert report.spec_count == report.result_count == report.executed_count == 8
    assert report.deferred_count == 0
    assert report.failed_count == 0
    assert report.synthetic_experiment_count == 2
    assert report.benchmark_tournament_count == 5
    assert report.applied_math_reduction_count == 1
    assert report.evidence_label_counts == {
        "BenchmarkEvidence": 5,
        "SymbolicReductionDraft": 1,
        "SyntheticExperimentEvidence": 2,
    }
    assert executed.persistence.commit.action_type == ControllerActionType.ROUTE_EXECUTION_RUN
    assert all(result.creates_scientific_validation is False for result in executed.results)
    assert all(result.creates_real_world_validation is False for result in executed.results)
    assert all(result.publication_ready is False for result in executed.results)

    by_backend = {result.execution_backend: result for result in executed.results}
    graph = by_backend["graph_curvature_bottleneck_synthetic"]
    assert set(graph.metrics) >= {
        "precision_at_k",
        "recall_at_k",
        "auc_proxy",
        "false_positive_rate",
    }
    assert graph.scope_label == "fixed-seed synthetic substrate evaluation only"
    agent = by_backend["agent_based_distance_decay_synthetic"]
    assert agent.metrics["held_out_mae"] < agent.metrics["baseline_held_out_mae"]
    assert agent.metrics["held_out_rmse"] < agent.metrics["baseline_held_out_rmse"]
    benchmark_results = [
        result
        for result in executed.results
        if result.route_type == BranchRouteType.BENCHMARK_TOURNAMENT
    ]
    assert len(benchmark_results) == 5
    assert all(result.output_payload["comparison_table"] for result in benchmark_results)
    reduction = by_backend["wasserstein_accessibility_symbolic_reduction"]
    assert reduction.evidence_label == "SymbolicReductionDraft"
    assert reduction.output_payload["symbolic_reduction_steps"]
    assert reduction.output_payload["assumptions"]
    assert reduction.output_payload["finite_dimensional_target"]
    assert reduction.output_payload["unresolved_steps"]
    assert "not proof" in reduction.scope_label

    unsupported_spec = built.specs[0].model_copy(
        update={
            "spec_id": "unsupported-proof-plan-spec",
            "route_type": BranchRouteType.PROOF_PLAN,
            "execution_backend": "unsupported_route_deferred",
            "allowed_evidence_labels": ["UnsupportedRouteDeferred"],
        }
    )
    unsupported = execute_route_spec(
        spec=unsupported_spec,
        result_id="unsupported-proof-plan-result",
    )
    assert unsupported.status == RouteExecutionStatus.DEFERRED_UNSUPPORTED_ROUTE
    assert unsupported.evidence_label == "UnsupportedRouteDeferred"
    assert unsupported.failure_reason_optional
    assert unsupported.publication_ready is False

    assert inspection.route_execution_present is True
    assert inspection.result_count == 8
    assert inspection.failed_count == 0
    assert inspection.creates_real_world_validation is False
    assert inspection.publication_ready is False


def test_strict_production_mode_classifies_and_blocks_template_science(tmp_path) -> None:
    run_id = "run-production-mode"
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    discovery = discover_opportunities(
        run_id=run_id,
        domain="human geography",
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_methods=20,
    )
    variance = augment_variance(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        candidates_per_seed=4,
        max_total_candidates=40,
    )
    apply_variance_augmentation(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    promotion = promote_variance_substrates(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_substrates=8,
    )
    routes = route_branches(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    specs = build_route_execution_specs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    execution = run_route_execution(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    assert discovery.report.backend_records[0].backend_kind == (BackendKind.DETERMINISTIC_TEMPLATE)
    assert variance.report.backend_records[0].stage_kind == (
        ScientificStageKind.VARIANCE_GENERATION
    )
    assert promotion.report.backend_records[0].allowed_in_production is False
    assert routes.plan.backend_records[0].backend_kind == BackendKind.HEURISTIC
    assert specs.report.backend_records[0].stage_kind == ScientificStageKind.EXPERIMENT_DESIGN
    assert {record.backend_kind for record in execution.report.backend_records} == {
        BackendKind.FIXTURE
    }
    assert all(result.backend_records for result in execution.results)

    inventory = inspect_backends(run_id=run_id, root=tmp_path)
    assert inventory.stage_count == 7
    assert inventory.blocking_violation_count == 0
    assert inventory.production_ready is False
    assert inventory.publication_ready is False

    development = check_production_mode(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=False,
    )
    strict = check_production_mode(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert development.report.blocking_violation_count == 0
    assert strict.report.blocking_violation_count == 7
    assert strict.report.production_ready is False
    assert strict.report.publication_ready is False
    assert {violation.stage_kind for violation in strict.report.violations} >= {
        ScientificStageKind.OPPORTUNITY_DISCOVERY,
        ScientificStageKind.VARIANCE_GENERATION,
        ScientificStageKind.SUBSTRATE_CONSTRUCTION,
        ScientificStageKind.BRANCH_ROUTING,
        ScientificStageKind.EXPERIMENT_DESIGN,
        ScientificStageKind.EXPERIMENT_EXECUTION,
        ScientificStageKind.METRIC_COMPUTATION,
    }
    reports = tmp_path / "runs" / run_id / "reports"
    assert (reports / "production-mode-report-0001.json").is_file()
    assert (reports / "production-mode-report-0002.json").is_file()


def test_production_policy_allows_execution_audits_and_blocks_missing_or_fallback() -> None:
    allowed = [
        stage_backend_record(
            stage_id="local-execution",
            stage_kind=ScientificStageKind.EXPERIMENT_EXECUTION,
            backend_kind=BackendKind.LOCAL_EXECUTION,
            backend_name="uv_local",
            is_scientific_generation=False,
            is_scientific_judgment=False,
            is_execution_or_verification=True,
            reason="Actual local execution artifact.",
            artifact_ids=["local-execution-artifact"],
        ),
        stage_backend_record(
            stage_id="metric-computation",
            stage_kind=ScientificStageKind.METRIC_COMPUTATION,
            backend_kind=BackendKind.LOCAL_EXECUTION,
            backend_name="computed_metrics",
            is_scientific_generation=False,
            is_scientific_judgment=False,
            is_execution_or_verification=True,
            reason="Metrics computed from local execution output.",
            artifact_ids=["metric-artifact"],
        ),
        stage_backend_record(
            stage_id="claim-audit",
            stage_kind=ScientificStageKind.CLAIM_AUDIT,
            backend_kind=BackendKind.HEURISTIC,
            backend_name="deterministic_claim_audit",
            is_scientific_generation=False,
            is_scientific_judgment=False,
            is_execution_or_verification=True,
            reason="Deterministic claim-boundary verification.",
            artifact_ids=["claim-audit-artifact"],
        ),
        stage_backend_record(
            stage_id="bundle-verification",
            stage_kind=ScientificStageKind.BUNDLE_VERIFICATION,
            backend_kind=BackendKind.HEURISTIC,
            backend_name="hash_bundle_verifier",
            is_scientific_generation=False,
            is_scientific_judgment=False,
            is_execution_or_verification=True,
            reason="Deterministic hash and manifest verification.",
            artifact_ids=["bundle-verification-artifact"],
        ),
        stage_backend_record(
            stage_id="llm-opportunity",
            stage_kind=ScientificStageKind.OPPORTUNITY_DISCOVERY,
            backend_kind=BackendKind.LLM_OPENAI,
            backend_name="openai-opportunity-generator",
            is_scientific_generation=True,
            is_scientific_judgment=False,
            is_execution_or_verification=False,
            reason="Non-fake scientific generation backend.",
            artifact_ids=["llm-opportunity-artifact"],
        ),
    ]
    policy = ProductionModePolicy(require_non_fake_backends=True)
    passing = evaluate_production_mode(
        run_id="production-policy-pass",
        records=allowed,
        policy=policy,
        expected_stage_kinds=[record.stage_kind for record in allowed],
    )
    assert passing.blocking_violation_count == 0
    assert passing.production_ready is True
    assert passing.publication_ready is False

    missing = evaluate_production_mode(
        run_id="production-policy-missing",
        records=allowed[:1],
        policy=policy,
        expected_stage_kinds=[
            ScientificStageKind.EXPERIMENT_EXECUTION,
            ScientificStageKind.MANUSCRIPT_SYNTHESIS,
        ],
    )
    assert any(
        violation.violation_type == "missing_backend_record" for violation in missing.violations
    )

    silent_fallback = allowed[-1].model_copy(
        update={"fallback_used": True, "fallback_disclosed": False}
    )
    fallback = evaluate_production_mode(
        run_id="production-policy-fallback",
        records=[silent_fallback],
        policy=policy,
        expected_stage_kinds=[ScientificStageKind.OPPORTUNITY_DISCOVERY],
    )
    assert any(violation.violation_type == "silent_fallback" for violation in fallback.violations)


def test_domain_method_atlas_and_exclusion_only_compatibility_filter() -> None:
    domains = domain_atlas()
    methods = method_atlas()

    assert len(domains) >= 40
    assert len(methods) >= 30
    assert all(domain.canonical_objects for domain in domains)
    assert all(method.false_bridge_patterns for method in methods)

    report = build_compatibility_filter_report(
        run_id="atlas-filter",
        filter_id="compatibility-filter-test",
        source_atlas_path="runs/atlas-filter/reports/domain-method-atlas-0001.json",
        domains=domains,
        methods=methods,
    )
    assert report.raw_pair_count == len(domains) * len(methods)
    assert report.raw_pair_count > 1000
    assert report.excluded_pair_count > 0
    assert report.surviving_pair_count > 0
    assert report.raw_pair_count == (report.excluded_pair_count + report.surviving_pair_count)
    assert "rank_score" not in DomainMethodPair.model_fields
    assert "opportunity_score" not in DomainMethodPair.model_fields
    assert report.backend_records[0].allowed_in_production is True
    assert report.backend_records[0].is_scientific_judgment is False

    domain = next(item for item in domains if item.name == "human geography")
    method = next(item for item in methods if item.name == "spatial statistics")
    missing_object, object_exclusion = evaluate_pair_compatibility(
        domain=domain,
        method=method.model_copy(update={"canonical_objects": ["alien_object"]}),
    )
    assert missing_object.compatibility_status == "excluded"
    assert object_exclusion.missing_object_mapping is True
    _, baseline_exclusion = evaluate_pair_compatibility(
        domain=domain,
        method=method.model_copy(update={"natural_baselines": []}),
    )
    assert baseline_exclusion.missing_baseline is True
    _, verification_exclusion = evaluate_pair_compatibility(
        domain=domain,
        method=method.model_copy(update={"verification_modes": ["proof"]}),
    )
    assert verification_exclusion.missing_verification_path is True
    _, data_exclusion = evaluate_pair_compatibility(
        domain=domain.model_copy(update={"data_types": ["private_data"]}),
        method=method.model_copy(update={"required_inputs": ["private_data"]}),
    )
    assert data_exclusion.missing_data_or_simulation_path is True


def test_openai_atlas_ranker_parses_mocked_structured_response_without_network() -> None:
    class MockTransport:
        def create_response(self, **kwargs):
            del kwargs
            return {
                "results": [
                    {
                        "pair_id": "pair-test",
                        "rank_score": 0.82,
                        "scientific_fit": 0.84,
                        "tractability": 0.80,
                        "question_abundance": 0.81,
                        "baseline_clarity": 0.83,
                        "verification_feasibility": 0.85,
                        "paper_shape_clarity": 0.79,
                        "false_bridge_risk": 0.18,
                        "tautology_risk": 0.16,
                        "novelty_hypothesis": "Hypothesis: novelty requires retrieval.",
                        "underuse_hypothesis": "Hypothesis: underuse requires retrieval.",
                        "ranking_explanation": "Mock transport response.",
                        "recommended_for_deep_discovery": True,
                    }
                ]
            }

    ranker = OpenAIAtlasPairRanker(
        api_key="test-key-not-sent",
        model="mock-model",
        transport=MockTransport(),
        allow_external_calls=True,
    )
    prompt, results = ranker.rank_batch(
        pair_payloads=[{"pair_id": "pair-test"}],
        batch_index=1,
        prompt_id="prompt-test",
    )

    assert prompt.pair_ids == ["pair-test"]
    assert results[0].pair_id == "pair-test"
    assert results[0].novelty_hypothesis.startswith("Hypothesis:")


def test_atlas_scan_uses_only_survivors_and_passes_strict_production(tmp_path) -> None:
    run_id = "run-atlas-scan"
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    built = build_domain_method_atlas(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    ranker = MockAtlasPairRanker()
    scanned = scan_domain_method_pairs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        ranker=ranker,
        top_pairs=30,
        require_non_fake_backends=True,
        batch_size=100,
        max_ranking_calls=10,
    )
    inspection = inspect_atlas_scan(run_id=run_id, root=tmp_path)

    assert built.report.domain_count == 42
    assert built.report.method_count == 32
    assert built.report.backend_records[0].backend_kind == BackendKind.CURATED_CATALOG
    assert scanned.compatibility_report.raw_pair_count == 1344
    survivors = {
        pair.pair_id
        for pair in scanned.compatibility_report.pairs
        if pair.compatibility_status != "excluded"
    }
    assert set(ranker.received_pair_ids) == survivors
    assert scanned.ranking_report.ranked_pair_count == len(survivors)
    assert scanned.report.selected_pair_count == 30
    assert scanned.report.domain_family_coverage >= 8
    assert scanned.report.method_family_coverage >= 8
    assert scanned.report.production_ready is True
    assert scanned.report.publication_ready is False
    assert all(
        item.novelty_hypothesis.startswith("Hypothesis:")
        and item.underuse_hypothesis.startswith("Hypothesis:")
        for item in scanned.ranking_report.results
    )
    assert inspection.atlas_scan_present is True
    assert inspection.llm_ranked_pair_count == len(survivors)

    backend_inventory = inspect_backends(run_id=run_id, root=tmp_path)
    assert backend_inventory.stage_count == 4
    assert {record.stage_kind for record in backend_inventory.stage_records} == {
        ScientificStageKind.ATLAS_CONSTRUCTION,
        ScientificStageKind.COMPATIBILITY_FILTER,
        ScientificStageKind.PAIR_RANKING,
        ScientificStageKind.DIVERSITY_SELECTION,
    }
    strict = check_production_mode(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert strict.report.blocking_violation_count == 0
    assert strict.report.production_ready is True
    assert strict.report.publication_ready is False


def test_atlas_strict_mode_rejects_fake_ranking_fallback_and_suppresses_duplicates(
    tmp_path,
) -> None:
    run_id = "run-atlas-strict-rejection"
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    build_domain_method_atlas(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    fake_ranker = MockAtlasPairRanker()
    fake_ranker.backend_kind = BackendKind.FAKE
    with pytest.raises(AtlasScanError, match="requires a non-fake LLM"):
        scan_domain_method_pairs(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            ranker=fake_ranker,
            require_non_fake_backends=True,
        )

    fallback_ranker = MockAtlasPairRanker()
    fallback_ranker.fallback_used = True
    with pytest.raises(AtlasScanError, match="forbids fallback"):
        scan_domain_method_pairs(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            ranker=fallback_ranker,
            require_non_fake_backends=True,
        )

    pair, _ = evaluate_pair_compatibility(
        domain=domain_atlas()[0],
        method=method_atlas()[2],
    )
    ranking = LLMPairRankingResult(
        pair_id=pair.pair_id,
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
        ranking_explanation="Test ranking.",
        recommended_for_deep_discovery=True,
    )
    selected_pairs, _, duplicate_count = select_diverse_ranked_pairs(
        pairs=[pair],
        rankings=[ranking, ranking],
        top_pairs=2,
        minimum_domain_families=1,
        minimum_method_families=1,
    )
    assert len(selected_pairs) == 1
    assert duplicate_count == 1


def test_deep_opportunity_parser_rejects_missing_contracts_and_scopes_hypotheses() -> None:
    valid = {
        "candidate": {
            "research_question": "Does a bounded model beat a declared baseline?",
            "hypothesis": "The bounded model reduces held-out error in simulation.",
            "theory_or_model_object": "A concrete matrix operator T.",
            "mathematical_or_computational_form": "T(x) = Ax",
            "experiment_or_proof_plan": "Run a fixed-seed synthetic comparison.",
            "benchmark_plan": "Compare T with the identity baseline.",
            "baseline_candidates": ["identity baseline"],
            "expected_metrics": ["held_out_error"],
            "failure_modes": ["no held-out improvement"],
            "negative_controls": ["set A to identity"],
            "data_regime": "synthetic_only",
            "verification_path": "bounded synthetic benchmark",
            "paper_shape": "model, benchmark, limitations",
            "novelty_risk": "A close formulation may already exist.",
            "underuse_hypothesis": "The pairing may be underexplored.",
            "retrieval_support_summary": "One bounded metadata result was supplied.",
            "retrieval_contradictions": [],
            "false_bridge_risks": [],
            "tautology_risks": ["the DGP may favor T"],
            "recommended_next_stage": "variance_generation",
        },
        "score": {
            "scientific_fit": 0.8,
            "tractability": 0.8,
            "question_specificity": 0.8,
            "baseline_strength": 0.8,
            "verification_feasibility": 0.8,
            "expected_signal": 0.7,
            "failure_mode_value": 0.8,
            "paper_coherence": 0.8,
            "novelty_risk_penalty": 0.2,
            "false_bridge_penalty": 0.2,
            "tautology_penalty": 0.2,
            "retrieval_confidence": 0.3,
            "final_score": 0.8,
            "score_explanation": "Structured mocked score.",
        },
    }
    missing_baseline = json.loads(json.dumps(valid))
    missing_baseline["candidate"].pop("baseline_candidates")
    missing_verification = json.loads(json.dumps(valid))
    missing_verification["candidate"].pop("verification_path")
    novelty_as_fact = json.loads(json.dumps(valid))
    novelty_as_fact["candidate"]["research_question"] = (
        "This is novel and should it beat the baseline?"
    )

    accepted, rejected = parse_opportunity_items(
        {
            "opportunities": [
                valid,
                missing_baseline,
                missing_verification,
                novelty_as_fact,
            ]
        }
    )

    assert len(accepted) == 1
    assert len(rejected) == 3
    assert accepted[0].candidate.novelty_risk.startswith("Hypothesis:")
    assert accepted[0].candidate.underuse_hypothesis.startswith("Hypothesis:")
    assert "baseline_candidates" in rejected[0]["reasons"][0]
    assert "verification_path" in rejected[1]["reasons"][0]
    assert "novelty as fact" in rejected[2]["reasons"][0]


def test_deep_opportunity_discovery_with_mocked_retrieval_is_context_only(tmp_path) -> None:
    run_id = "run-deep-opportunity-mocked"
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    build_domain_method_atlas(run_id=run_id, root=tmp_path, store=store, ledger=ledger)
    scan_domain_method_pairs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        ranker=MockAtlasPairRanker(),
        top_pairs=12,
        require_non_fake_backends=True,
        batch_size=100,
        max_ranking_calls=10,
    )
    generator = MockDeepOpportunityGenerator(duplicate=True)
    result = discover_deep_opportunities(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=generator,
        retriever=MockedOpportunityRetriever(),
        config=DeepOpportunityDiscoveryConfig(
            run_id=run_id,
            backend="llm-openai",
            retrieval_mode="mocked_retrieval",
            max_pairs=12,
            max_generation_calls=12,
            opportunities_per_pair=3,
            max_selected_opportunities=20,
        ),
    )
    inspected = inspect_deep_opportunities(run_id=run_id, root=tmp_path)

    assert result.report.selected_pair_count == 12
    assert result.report.generated_opportunity_count == 36
    assert result.report.selected_opportunity_count >= 8
    assert result.report.near_duplicate_suppressed_count == 24
    assert result.report.domain_family_coverage >= 8
    assert result.report.method_family_coverage >= 8
    assert result.report.config.retrieval_mode == "mocked_retrieval"
    assert result.report.production_ready is False
    assert result.report.publication_ready is False
    assert len(result.report.raw_artifact_paths) == 12
    assert len(result.report.retrieval_context_paths) == 12
    assert all((tmp_path / path).is_file() for path in result.report.raw_artifact_paths)
    assert all((tmp_path / path).is_file() for path in result.report.retrieval_context_paths)
    assert set(generator.received_pair_ids) == {
        item.source_pair_id for item in result.report.candidates
    }
    assert all(item.baseline_candidates for item in result.report.candidates)
    assert all(item.verification_path for item in result.report.candidates)
    assert all(item.theory_or_model_object for item in result.report.candidates)
    assert all(item.negative_controls for item in result.report.candidates)
    assert all(item.creates_scientific_validation is False for item in result.report.candidates)
    assert inspected.deep_opportunity_discovery_present is True
    assert inspected.selected_opportunity_count == result.report.selected_opportunity_count
    assert any(
        record.stage_kind == ScientificStageKind.OPPORTUNITY_DISCOVERY
        and record.backend_kind == BackendKind.LLM_OPENAI
        and record.allowed_in_production
        for record in result.report.backend_records
    )
    assert any(
        record.stage_kind == ScientificStageKind.LITERATURE_RETRIEVAL
        and record.backend_kind == BackendKind.FIXTURE
        and not record.allowed_in_production
        for record in result.report.backend_records
    )

    strict_audit = check_production_mode(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert strict_audit.report.blocking_violation_count > 0
    assert any(
        violation.stage_kind == ScientificStageKind.LITERATURE_RETRIEVAL
        for violation in strict_audit.report.violations
    )


def test_deep_opportunity_strict_mode_accepts_injected_real_retrieval_and_no_fallback(
    tmp_path,
) -> None:
    run_id = "run-deep-opportunity-strict"
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    build_domain_method_atlas(run_id=run_id, root=tmp_path, store=store, ledger=ledger)
    scan_domain_method_pairs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        ranker=MockAtlasPairRanker(),
        top_pairs=12,
        require_non_fake_backends=True,
        batch_size=100,
        max_ranking_calls=10,
    )
    config = DeepOpportunityDiscoveryConfig(
        run_id=run_id,
        backend="llm-openai",
        retrieval_mode="real_retrieval",
        max_pairs=12,
        max_generation_calls=12,
        opportunities_per_pair=2,
        max_selected_opportunities=20,
        require_non_fake_backends=True,
    )
    result = discover_deep_opportunities(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=MockDeepOpportunityGenerator(),
        retriever=MockRealOpportunityRetriever(),
        config=config,
    )

    assert result.report.generated_opportunity_count == 24
    assert result.report.selected_opportunity_count > 0
    assert result.report.production_ready is True
    assert result.report.publication_ready is False
    strict = check_production_mode(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert strict.report.blocking_violation_count == 0
    assert strict.report.production_ready is True

    with pytest.raises(DeepOpportunityDiscoveryError, match="requires real retrieval"):
        discover_deep_opportunities(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            generator=MockDeepOpportunityGenerator(),
            retriever=MockedOpportunityRetriever(),
            config=config.model_copy(update={"retrieval_mode": "mocked_retrieval"}),
        )
    fallback = MockDeepOpportunityGenerator()
    fallback.fallback_used = True
    with pytest.raises(DeepOpportunityDiscoveryError, match="forbids deterministic"):
        discover_deep_opportunities(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            generator=fallback,
            retriever=MockRealOpportunityRetriever(),
            config=config,
        )


def test_llm_variance_parser_rejects_missing_contract_fields_and_scopes_novelty() -> None:
    source = DeepOpportunityCandidate(
        opportunity_id="deep-source",
        run_id="variance-parser",
        source_pair_id="pair-source",
        domain_id="domain",
        method_id="method",
        domain_name="Domain",
        method_name="Method",
        research_question="Does the source model change a metric?",
        hypothesis="The source model changes a bounded metric.",
        theory_or_model_object="Source operator S",
        mathematical_or_computational_form="S(x)=x",
        experiment_or_proof_plan="Run a synthetic source comparison.",
        benchmark_plan="Compare with a null baseline.",
        baseline_candidates=["null baseline"],
        expected_metrics=["error"],
        failure_modes=["no change"],
        negative_controls=["remove S"],
        data_regime="synthetic_only",
        verification_path="bounded benchmark",
        paper_shape="model and benchmark",
        novelty_risk="Hypothesis: prior work may be close.",
        underuse_hypothesis="Hypothesis: underuse is not established.",
        retrieval_support_summary="Bounded metadata only.",
        recommended_next_stage="variance_generation",
    )
    response = MockLLMVarianceGenerator().generate_variants(
        prompt_id="variance-prompt",
        source_payload=source.model_dump(mode="json"),
        retrieval_context_payload={"retrieval_mode": "real_retrieval"},
        variants_per_opportunity=3,
    )
    valid = response.accepted[0].model_dump(mode="json")
    valid["candidate"]["novelty_risk"] = "A close branch may exist."
    missing_baseline = json.loads(json.dumps(valid))
    missing_baseline["candidate"].pop("baseline_candidates")
    missing_verification = json.loads(json.dumps(valid))
    missing_verification["candidate"].pop("verification_path")

    accepted, rejected = parse_variance_items(
        {"variants": [valid, missing_baseline, missing_verification]}
    )

    assert len(accepted) == 1
    assert len(rejected) == 2
    assert accepted[0].candidate.novelty_risk.startswith("Hypothesis:")
    assert "baseline_candidates" in rejected[0]["reasons"][0]
    assert "verification_path" in rejected[1]["reasons"][0]


def test_llm_variance_parser_allows_bounded_safety_caveats() -> None:
    generator = MockLLMVarianceGenerator()
    response = generator.generate_variants(
        prompt_id="variance-safety-caveat",
        source_payload={"opportunity_id": "deep-safety-source"},
        retrieval_context_payload={"retrieval_mode": "real_retrieval"},
        variants_per_opportunity=3,
    )
    payload = response.accepted[0].model_dump(mode="json")
    payload["candidate"]["scientific_rationale"] = (
        "This bounded variant does not establish real-world validation; it remains "
        "a synthetic planning proposal."
    )
    accepted, rejected = parse_variance_items({"variants": [payload]})
    assert len(accepted) == 1
    assert rejected == []

    unsafe = json.loads(json.dumps(payload))
    unsafe["candidate"]["scientific_rationale"] = (
        "This variant establishes real-world validation."
    )
    accepted, rejected = parse_variance_items({"variants": [unsafe]})
    assert accepted == []
    assert any("real-world validation" in reason for reason in rejected[0]["reasons"])


def test_llm_variance_persists_failed_batches_and_reuses_valid_batches(tmp_path) -> None:
    run_id = "run-llm-variance-resume"
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    build_domain_method_atlas(run_id=run_id, root=tmp_path, store=store, ledger=ledger)
    scan_domain_method_pairs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        ranker=MockAtlasPairRanker(),
        top_pairs=1,
        require_non_fake_backends=True,
        batch_size=1000,
        max_ranking_calls=1,
    )
    discover_deep_opportunities(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=MockDeepOpportunityGenerator(),
        retriever=MockRealOpportunityRetriever(),
        config=DeepOpportunityDiscoveryConfig(
            run_id=run_id,
            backend="llm-openai",
            retrieval_mode="real_retrieval",
            max_pairs=1,
            max_generation_calls=1,
            opportunities_per_pair=3,
            max_selected_opportunities=3,
            require_non_fake_backends=True,
        ),
    )

    class FailingVarianceGenerator(MockLLMVarianceGenerator):
        def generate_variants(self, *, source_payload, **kwargs):
            if source_payload["opportunity_id"].endswith("-02"):
                prompt = build_llm_variance_prompt(
                    prompt_id=kwargs["prompt_id"],
                    backend_name=self.backend_name,
                    model=self.model,
                    source_payload=source_payload,
                    retrieval_context_payload=kwargs["retrieval_context_payload"],
                    variants_per_opportunity=kwargs["variants_per_opportunity"],
                )
                return VarianceGenerationResponse(
                    prompt=prompt,
                    raw_response={"variants": []},
                    accepted=[],
                    rejected=[
                        {"index": 0, "reasons": ["affirmative safety claim"]}
                    ],
                )
            return super().generate_variants(source_payload=source_payload, **kwargs)

    config = LLMVarianceGenerationConfig(
        run_id=run_id,
        backend="llm-openai",
        max_source_opportunities=3,
        variants_per_opportunity=3,
        max_variants_total=9,
        max_selected_variants=9,
        max_generation_calls=3,
        min_variant_family_coverage=3,
        min_domain_family_coverage=1,
        min_method_family_coverage=1,
        require_non_fake_backends=True,
    )
    with pytest.raises(LLMVarianceError, match="no valid variants"):
        generate_llm_variance(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            generator=FailingVarianceGenerator(),
            config=config,
        )

    failed = inspect_llm_variance(run_id=run_id, root=tmp_path)
    assert failed.llm_variance_present is True
    assert failed.generation_status_optional == "failed"
    assert failed.generated_variant_count == 3
    assert failed.rejected_variant_count == 1
    assert any("affirmative safety claim" in warning for warning in failed.warnings)
    raw_paths = sorted(
        path
        for path in (tmp_path / "runs" / run_id / "reports").glob("llm-variance-raw-*.json")
        if not path.name.endswith(".meta.json")
    )
    assert len(raw_paths) == 2

    retry_generator = MockLLMVarianceGenerator()
    result = generate_llm_variance(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=retry_generator,
        config=config,
    )
    assert result.report.generation_status in {"completed", "completed_with_warnings"}
    assert result.report.selected_variant_count > 0
    assert retry_generator.received_source_ids == [
        "deep-opportunity-0001-agriculture_systems-convex_duality-02",
        "deep-opportunity-0001-agriculture_systems-convex_duality-03",
    ]


def test_llm_variance_constructs_production_safe_idea_tree(tmp_path) -> None:
    run_id = "run-llm-variance-strict"
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    build_domain_method_atlas(run_id=run_id, root=tmp_path, store=store, ledger=ledger)
    scan_domain_method_pairs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        ranker=MockAtlasPairRanker(),
        top_pairs=8,
        require_non_fake_backends=True,
        batch_size=100,
        max_ranking_calls=10,
    )
    discover_deep_opportunities(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=MockDeepOpportunityGenerator(),
        retriever=MockRealOpportunityRetriever(),
        config=DeepOpportunityDiscoveryConfig(
            run_id=run_id,
            backend="llm-openai",
            retrieval_mode="real_retrieval",
            max_pairs=8,
            max_generation_calls=8,
            opportunities_per_pair=2,
            max_selected_opportunities=16,
            require_non_fake_backends=True,
        ),
    )
    generator = MockLLMVarianceGenerator(include_filtered_variants=True)
    result = generate_llm_variance(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=generator,
        config=LLMVarianceGenerationConfig(
            run_id=run_id,
            backend="llm-openai",
            max_source_opportunities=6,
            variants_per_opportunity=7,
            max_variants_total=42,
            max_selected_variants=30,
            max_generation_calls=6,
            min_variant_family_coverage=5,
            min_domain_family_coverage=4,
            min_method_family_coverage=4,
            require_non_fake_backends=True,
        ),
    )
    inspected = inspect_llm_variance(run_id=run_id, root=tmp_path)

    assert result.report.source_opportunity_count == 6
    assert result.report.generated_variant_count == 42
    assert result.report.selected_variant_count == 30
    assert result.report.variant_family_coverage >= 5
    assert result.report.domain_family_coverage >= 4
    assert result.report.method_family_coverage >= 4
    assert result.report.near_duplicate_suppressed_count == 6
    assert result.report.source_repeat_suppressed_count == 6
    assert result.report.production_ready is True
    assert result.report.publication_ready is False
    assert len(result.report.raw_artifact_paths) == 6
    assert all((tmp_path / path).is_file() for path in result.report.raw_artifact_paths)
    assert all(item.baseline_candidates for item in result.report.candidates)
    assert all(item.verification_path for item in result.report.candidates)
    assert all(item.theory_or_model_object for item in result.report.candidates)
    assert all(item.creates_scientific_validation is False for item in result.report.candidates)
    assert inspected.llm_variance_present is True
    assert inspected.selected_variant_count == 30
    assert any(
        record.stage_kind == ScientificStageKind.VARIANCE_GENERATION
        and record.backend_kind == BackendKind.LLM_OPENAI
        and record.allowed_in_production
        for record in result.report.backend_records
    )

    construction = construct_idea_tree_from_llm_variance(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    tree = inspect_idea_tree(run_id=run_id, root=tmp_path)

    assert construction.report.parent_opportunity_node_count == 6
    assert construction.report.variant_node_count == 30
    assert construction.report.idea_tree_nodes_added == 36
    assert construction.report.idea_tree_edges_added == 36
    assert construction.report.production_ready is True
    assert tree.node_count == 37
    llm_nodes = [node for node in tree.nodes if node.stage_origin == "llm_variance"]
    assert len(llm_nodes) == 30
    assert all(node.source_pair_id_optional for node in llm_nodes)
    assert all(node.source_opportunity_id_optional for node in llm_nodes)
    assert all(node.variant_family_optional for node in llm_nodes)
    assert all(node.backend_kind_optional == BackendKind.LLM_OPENAI for node in llm_nodes)
    assert all(node.retrieval_context_id_optional for node in llm_nodes)
    assert any(
        record.stage_kind == ScientificStageKind.IDEA_TREE_CONSTRUCTION
        and record.backend_kind == BackendKind.LOCAL_EXECUTION
        and record.allowed_in_production
        for record in construction.report.backend_records
    )
    strict = check_production_mode(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert strict.report.blocking_violation_count == 0
    assert strict.report.production_ready is True

    fallback = MockLLMVarianceGenerator()
    fallback.fallback_used = True
    with pytest.raises(LLMVarianceError, match="forbids deterministic"):
        generate_llm_variance(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            generator=fallback,
            config=result.report.config,
        )


def test_llm_substrate_parser_rejects_incomplete_and_authority_claims() -> None:
    generator = MockLLMSubstrateGenerator()
    response = generator.construct_substrate(
        prompt_id="substrate-parser-prompt",
        source_payload={
            "variant_id": "variant-source",
            "method_id": "kernel-methods",
            "research_question": "Does the bounded kernel mechanism improve held-out error?",
        },
        opportunity_payload={"opportunity_id": "opportunity-source"},
        retrieval_context_payload={"retrieval_mode": "real_retrieval"},
    )
    valid = response.accepted.model_dump(mode="json")
    valid["candidate"]["novelty_risk"] = "A close construction may exist."
    accepted, reasons = parse_substrate_response({"substrates": [valid]})

    assert reasons == []
    assert accepted is not None
    assert accepted.candidate.novelty_risk.startswith("Hypothesis:")

    repaired = json.loads(json.dumps(valid))
    repaired["candidate"]["route_hint"] = (
        "Advisory route: run a simulation benchmark before any empirical application."
    )
    for field_name, value in repaired["score"].items():
        if field_name != "score_explanation":
            repaired["score"][field_name] = value * 10
    repaired["candidate"]["scope_boundary"] = (
        "This proposed synthetic study does not establish real-world validation."
    )
    accepted, reasons = parse_substrate_response({"substrates": [repaired]})
    assert reasons == []
    assert accepted is not None
    assert accepted.candidate.route_hint == "benchmark_tournament"
    assert accepted.score.final_score == pytest.approx(0.88)

    for field_name in ["baseline_candidates", "verification_path", "result_schema"]:
        invalid = json.loads(json.dumps(valid))
        invalid["candidate"].pop(field_name)
        parsed, rejected = parse_substrate_response({"substrates": [invalid]})
        assert parsed is None
        assert field_name in rejected[0]

    for unsafe_text, expected in [
        ("The model is proven for all regimes.", "claims proof"),
        ("This supplies real-world validation.", "real-world validation"),
    ]:
        invalid = json.loads(json.dumps(valid))
        invalid["candidate"]["hypothesis"] = unsafe_text
        parsed, rejected = parse_substrate_response({"substrates": [invalid]})
        assert parsed is None
        assert any(expected in item for item in rejected)


def test_llm_substrate_bridge_accepts_causal_object_vocabulary() -> None:
    generator = MockLLMSubstrateGenerator()
    response = generator.construct_substrate(
        prompt_id="substrate-bridge-prompt",
        source_payload={
            "variant_id": "variant-source",
            "method_id": "causal_inference",
            "research_question": "How stable is the policy effect under confounding?",
        },
        opportunity_payload={"opportunity_id": "opportunity-source"},
        retrieval_context_payload={"retrieval_mode": "real_retrieval"},
    )
    assert response.accepted is not None
    candidate = response.accepted.candidate.model_copy(
        update={
            "concrete_model_object": response.accepted.candidate.concrete_model_object.model_copy(
                update={
                    "model_type": "partially identified treatment-effect set",
                    "equations": ["tau in I(Gamma, epsilon)"],
                    "algorithm_optional": "Optimize sensitivity weights under propensity bounds.",
                }
            ),
            "mathematical_or_computational_form": [
                "q_a(x) is bounded by the confounding sensitivity parameter Gamma",
                "the propensity score remains in [epsilon, 1-epsilon]",
            ],
        }
    )
    reasons = _decorative_method_reasons(
        candidate=candidate,
        variant=LLMVarianceCandidate(
            variant_id="variant-source",
            run_id="run-source",
            source_opportunity_id="opportunity-source",
            source_pair_id="pair-source",
            domain_id="causal_policy_evaluation",
            method_id="causal_inference",
            variant_family="mechanism",
            title="Bounded treatment effects",
            research_question="How stable is the policy effect under confounding?",
            hypothesis="The sensitivity set contains the true effect.",
            theory_or_model_object="A partially identified treatment effect.",
            mathematical_or_computational_form="tau in I(Gamma, epsilon)",
            experiment_or_proof_plan="Run a fixed-seed synthetic experiment.",
            benchmark_plan="Compare against a point estimator.",
            baseline_candidates=["point estimator"],
            negative_controls=["randomized treatment"],
            failure_modes=["vacuous bounds"],
            verification_path="Check coverage in simulation.",
            expected_metrics=["coverage"],
            data_regime="synthetic only",
            paper_role="method paper",
            scientific_rationale="The object exposes sensitivity to confounding.",
            novelty_risk="Hypothesis: related work may exist.",
            false_bridge_risk="The mapping could fail.",
            tautology_risk="The DGP could favor the method.",
            selected_for_tree=False,
        ),
    )
    assert reasons == []


def test_llm_substrates_construct_production_safe_scientific_objects(tmp_path) -> None:
    run_id = "run-llm-substrates-strict"
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    build_domain_method_atlas(run_id=run_id, root=tmp_path, store=store, ledger=ledger)
    scan_domain_method_pairs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        ranker=MockAtlasPairRanker(),
        top_pairs=8,
        require_non_fake_backends=True,
        batch_size=100,
        max_ranking_calls=10,
    )
    discover_deep_opportunities(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=MockDeepOpportunityGenerator(),
        retriever=MockRealOpportunityRetriever(),
        config=DeepOpportunityDiscoveryConfig(
            run_id=run_id,
            backend="llm-openai",
            retrieval_mode="real_retrieval",
            max_pairs=8,
            max_generation_calls=8,
            opportunities_per_pair=2,
            max_selected_opportunities=16,
            require_non_fake_backends=True,
        ),
    )
    generate_llm_variance(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=MockLLMVarianceGenerator(),
        config=LLMVarianceGenerationConfig(
            run_id=run_id,
            backend="llm-openai",
            max_source_opportunities=6,
            variants_per_opportunity=5,
            max_variants_total=30,
            max_selected_variants=20,
            max_generation_calls=6,
            min_variant_family_coverage=5,
            min_domain_family_coverage=4,
            min_method_family_coverage=4,
            require_non_fake_backends=True,
        ),
    )
    construct_idea_tree_from_llm_variance(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    generator = MockLLMSubstrateGenerator(reject_first=True)
    result = construct_llm_substrates(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=generator,
        config=LLMSubstrateConstructionConfig(
            run_id=run_id,
            backend="llm-openai",
            max_source_variants=9,
            max_constructed_substrates=9,
            max_selected_substrates=6,
            max_generation_calls=9,
            min_domain_family_coverage=4,
            min_method_family_coverage=4,
            min_route_hint_coverage=3,
            require_non_fake_backends=True,
        ),
    )
    inspected = inspect_llm_substrates(run_id=run_id, root=tmp_path)
    standard = inspect_scientific_substrate(run_id=run_id, root=tmp_path)

    assert result.report.source_variant_count == 9
    assert result.report.constructed_substrate_count == 8
    assert result.report.rejected_substrate_count == 1
    assert result.report.selected_substrate_count == 6
    assert result.report.domain_family_coverage >= 4
    assert result.report.method_family_coverage >= 4
    assert result.report.route_hint_coverage >= 3
    assert result.report.production_ready is True
    assert result.report.publication_ready is False
    assert all((tmp_path / path).is_file() for path in result.report.raw_artifact_paths)
    assert all((tmp_path / path).is_file() for path in result.report.scientific_substrate_paths)
    assert all(item.concrete_model_object.equations for item in result.report.candidates)
    assert all(item.variables_and_notation for item in result.report.candidates)
    assert all(item.assumptions for item in result.report.candidates)
    assert all(item.baseline_candidates for item in result.report.candidates)
    assert all(item.verification_path for item in result.report.candidates)
    assert all(item.result_schema.required_table_columns for item in result.report.candidates)
    assert all(item.negative_controls for item in result.report.candidates)
    assert all(item.creates_scientific_validation is False for item in result.report.candidates)
    assert inspected.llm_substrate_construction_present is True
    assert inspected.selected_substrate_count == 6
    assert standard.scientific_substrate_present is True
    assert standard.substrate_count == 6
    assert any(
        record.stage_kind == ScientificStageKind.SUBSTRATE_CONSTRUCTION
        and record.backend_kind == BackendKind.LLM_OPENAI
        and record.allowed_in_production
        for record in result.report.backend_records
    )
    assert any(
        record.stage_kind == ScientificStageKind.SUBSTRATE_SELECTION
        and record.backend_kind == BackendKind.HEURISTIC
        and record.allowed_in_production
        and not record.is_scientific_generation
        and not record.is_scientific_judgment
        for record in result.report.backend_records
    )

    strict = check_production_mode(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert strict.report.blocking_violation_count == 0
    assert strict.report.production_ready is True

    pair_by_id = {
        item.pair_id: item
        for item in inspect_atlas_scan(run_id=run_id, root=tmp_path).selected_pairs
    }
    original = result.report.candidates[0]
    duplicate = original.model_copy(update={"substrate_id": "exact-duplicate"})
    original_score = next(
        item for item in result.report.scores if item.substrate_id == original.substrate_id
    )
    duplicate_score = original_score.model_copy(update={"substrate_id": duplicate.substrate_id})
    selected, _, suppressed = select_llm_substrates(
        candidates=[original, duplicate],
        scores=[original_score, duplicate_score],
        pairs=[pair_by_id[original.source_pair_id]],
        max_selected=2,
        min_domain_families=1,
        min_method_families=1,
        min_route_hints=1,
    )
    assert len(selected) == 1
    assert suppressed == 1

    fallback = MockLLMSubstrateGenerator()
    fallback.fallback_used = True
    with pytest.raises(LLMSubstrateError, match="forbids deterministic fallback"):
        construct_llm_substrates(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            generator=fallback,
            config=result.report.config,
        )


def test_llm_routes_plan_production_safe_execution_contracts(tmp_path) -> None:
    run_id = "run-llm-routes-strict"
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    build_domain_method_atlas(run_id=run_id, root=tmp_path, store=store, ledger=ledger)
    scan_domain_method_pairs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        ranker=MockAtlasPairRanker(),
        top_pairs=8,
        require_non_fake_backends=True,
        batch_size=100,
        max_ranking_calls=10,
    )
    discover_deep_opportunities(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=MockDeepOpportunityGenerator(),
        retriever=MockRealOpportunityRetriever(),
        config=DeepOpportunityDiscoveryConfig(
            run_id=run_id,
            backend="llm-openai",
            retrieval_mode="real_retrieval",
            max_pairs=8,
            max_generation_calls=8,
            opportunities_per_pair=2,
            max_selected_opportunities=16,
            require_non_fake_backends=True,
        ),
    )
    generate_llm_variance(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=MockLLMVarianceGenerator(),
        config=LLMVarianceGenerationConfig(
            run_id=run_id,
            backend="llm-openai",
            max_source_opportunities=6,
            variants_per_opportunity=5,
            max_variants_total=30,
            max_selected_variants=20,
            max_generation_calls=6,
            min_variant_family_coverage=5,
            min_domain_family_coverage=4,
            min_method_family_coverage=4,
            require_non_fake_backends=True,
        ),
    )
    construct_idea_tree_from_llm_variance(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    construct_llm_substrates(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=MockLLMSubstrateGenerator(),
        config=LLMSubstrateConstructionConfig(
            run_id=run_id,
            backend="llm-openai",
            max_source_variants=8,
            max_constructed_substrates=8,
            max_selected_substrates=5,
            max_generation_calls=8,
            min_domain_family_coverage=4,
            min_method_family_coverage=4,
            min_route_hint_coverage=3,
            require_non_fake_backends=True,
        ),
    )
    planner = MockLLMRoutePlanner()
    result = plan_llm_routes(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=planner,
        config=LLMRoutePlanningConfig(
            run_id=run_id,
            backend="llm-openai",
            max_source_substrates=5,
            max_planning_calls=5,
            require_non_fake_backends=True,
        ),
    )
    inspected = inspect_llm_routes(run_id=run_id, root=tmp_path)

    assert result.report.selected_substrate_count == 5
    assert result.report.route_decision_count == 5
    assert result.report.execution_spec_count == 5
    assert result.report.rejected_output_count == 0
    assert result.report.repaired_output_count == 5
    assert result.report.route_type_coverage >= 3
    assert result.report.production_ready is True
    assert result.report.publication_ready is False
    assert len(planner.received_substrate_ids) == 5
    assert all(item.scientific_reason for item in result.report.decisions)
    assert all(item.allowed_evidence_labels for item in result.report.decisions)
    assert all(item.forbidden_claims for item in result.report.decisions)
    assert all(item.input_contract.route_parameters for item in result.report.execution_specs)
    assert all(item.output_contract.required_metrics for item in result.report.execution_specs)
    assert all(item.baseline_plan for item in result.report.execution_specs)
    assert all(item.control_plan for item in result.report.execution_specs)
    assert all(item.negative_control_plan for item in result.report.execution_specs)
    assert all(item.metric_plan for item in result.report.execution_specs)
    assert all(item.success_criteria for item in result.report.execution_specs)
    assert all(item.failure_criteria for item in result.report.execution_specs)
    assert all(
        item.creates_scientific_validation is False for item in result.report.execution_specs
    )
    assert all(
        item.creates_real_world_validation is False for item in result.report.execution_specs
    )
    assert all(
        set(item.allowed_evidence_labels) == set(ROUTE_ALLOWED_LABELS[item.route_type])
        for item in result.report.execution_specs
    )
    assert result.compatibility_spec_report.results == []
    assert result.compatibility_spec_report.evidence_label_counts == {}
    assert (tmp_path / result.report.compatibility_branch_route_plan_path).is_file()
    assert (tmp_path / result.report.compatibility_route_execution_specs_path).is_file()
    assert inspected.llm_route_planning_present is True
    assert inspected.route_decision_count == 5
    assert any(
        item.stage_kind == ScientificStageKind.BRANCH_ROUTING
        and item.backend_kind == BackendKind.LLM_OPENAI
        and item.allowed_in_production
        for item in result.report.backend_records
    )
    assert any(
        item.stage_kind == ScientificStageKind.EXPERIMENT_DESIGN
        and item.backend_kind == BackendKind.LLM_OPENAI
        and item.allowed_in_production
        for item in result.report.backend_records
    )
    assert any(
        item.stage_kind == ScientificStageKind.SPEC_VALIDATION
        and item.backend_kind == BackendKind.LOCAL_EXECUTION
        and item.allowed_in_production
        for item in result.report.backend_records
    )

    strict = check_production_mode(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert strict.report.blocking_violation_count == 0
    assert strict.report.production_ready is True

    valid_raw = planner.raw_items[0]
    nullable_collections = json.loads(json.dumps(valid_raw))
    nullable_collections["execution_spec"]["proof_obligations"] = None
    nullable_collections["execution_spec"]["retrieval_queries"] = None
    accepted, reasons, repairs = parse_route_planning_response(
        {"plans": [nullable_collections]}
    )
    assert accepted is not None
    assert reasons == []
    assert "normalized null execution_spec.proof_obligations to []" in repairs
    assert "normalized null execution_spec.retrieval_queries to []" in repairs

    caveat = json.loads(json.dumps(valid_raw))
    caveat["execution_spec"]["objective"] = (
        "This bounded synthetic plan does not establish real-world validation."
    )
    accepted, reasons, _ = parse_route_planning_response({"plans": [caveat]})
    assert accepted is not None
    assert reasons == []

    policy_caveat = json.loads(json.dumps(valid_raw))
    policy_caveat["execution_spec"]["objective"] = (
        "Real-world validation claims are forbidden for this bounded synthetic plan."
    )
    accepted, reasons, _ = parse_route_planning_response({"plans": [policy_caveat]})
    assert accepted is not None
    assert reasons == []

    scope_caveat = json.loads(json.dumps(valid_raw))
    scope_caveat["execution_spec"]["objective"] = (
        "Real-world validation is out of scope for this execution specification."
    )
    accepted, reasons, _ = parse_route_planning_response({"plans": [scope_caveat]})
    assert accepted is not None
    assert reasons == []

    future_work = json.loads(json.dumps(valid_raw))
    future_work["execution_spec"]["objective"] = (
        "Real-world validation requires future external data and remains outside this plan."
    )
    accepted, reasons, _ = parse_route_planning_response({"plans": [future_work]})
    assert accepted is not None
    assert reasons == []

    prerequisites = json.loads(json.dumps(valid_raw))
    prerequisites["execution_spec"]["objective"] = (
        "This route records prerequisites for real-world validation without claiming it."
    )
    accepted, reasons, _ = parse_route_planning_response({"plans": [prerequisites]})
    assert accepted is not None
    assert reasons == []

    unsafe_cases = []
    real_world = json.loads(json.dumps(valid_raw))
    real_world["execution_spec"]["objective"] = "This establishes real-world validation."
    unsafe_cases.append((real_world, "real-world validation"))
    real_world_constitutes = json.loads(json.dumps(valid_raw))
    real_world_constitutes["execution_spec"]["objective"] = (
        "The completed benchmark constitutes real-world validation."
    )
    unsafe_cases.append((real_world_constitutes, "real-world validation"))
    publication = json.loads(json.dumps(valid_raw))
    publication["decision"]["scientific_reason"] = "The paper is publication ready."
    unsafe_cases.append((publication, "publication readiness"))
    missing_contract = json.loads(json.dumps(valid_raw))
    missing_contract["execution_spec"]["input_contract"]["route_parameters"] = {}
    unsafe_cases.append((missing_contract, "route parameters"))
    wrong_labels = json.loads(json.dumps(valid_raw))
    wrong_labels["decision"]["allowed_evidence_labels"] = ["BenchmarkEvidence"]
    unsafe_cases.append((wrong_labels, "labels do not match"))
    missing_baseline = json.loads(json.dumps(valid_raw))
    missing_baseline["execution_spec"].pop("baseline_plan")
    unsafe_cases.append((missing_baseline, "baseline_plan"))

    proof = json.loads(json.dumps(valid_raw))
    proof["decision"]["route_type"] = "proof_plan"
    proof["decision"]["allowed_evidence_labels"] = list(
        ROUTE_ALLOWED_LABELS[BranchRouteType.PROOF_PLAN]
    )
    proof["execution_spec"]["route_type"] = "proof_plan"
    proof["execution_spec"]["allowed_evidence_labels"] = list(
        ROUTE_ALLOWED_LABELS[BranchRouteType.PROOF_PLAN]
    )
    proof["execution_spec"]["requires_code_generation"] = False
    proof["execution_spec"]["proof_obligations"] = []
    proof["execution_spec"]["formalization_target_optional"] = None
    unsafe_cases.append((proof, "proof obligations"))

    literature = json.loads(json.dumps(valid_raw))
    literature["decision"]["route_type"] = "literature_novelty_check"
    literature["decision"]["allowed_evidence_labels"] = list(
        ROUTE_ALLOWED_LABELS[BranchRouteType.LITERATURE_NOVELTY_CHECK]
    )
    literature["execution_spec"]["route_type"] = "literature_novelty_check"
    literature["execution_spec"]["allowed_evidence_labels"] = list(
        ROUTE_ALLOWED_LABELS[BranchRouteType.LITERATURE_NOVELTY_CHECK]
    )
    literature["execution_spec"]["requires_code_generation"] = False
    literature["execution_spec"]["requires_literature_retrieval"] = False
    literature["execution_spec"]["retrieval_queries"] = []
    unsafe_cases.append((literature, "retrieval requirement"))

    for payload, expected_reason in unsafe_cases:
        accepted, reasons, _ = parse_route_planning_response({"plans": [payload]})
        assert accepted is None
        assert any(expected_reason in reason for reason in reasons)

    fallback = MockLLMRoutePlanner()
    fallback.fallback_used = True
    with pytest.raises(LLMRoutePlanningError, match="forbids deterministic fallback"):
        plan_llm_routes(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            planner=fallback,
            config=result.report.config,
        )


def test_generated_experiment_safety_audit_blocks_unsafe_code() -> None:
    safe_code = """import json
baseline_values = [1.0, 2.0]
method_values = [0.5, 1.0]
negative_control_values = list(baseline_values)
held_out_mae = sum(method_values) / len(method_values)
payload = {"metrics": {"held_out_mae": held_out_mae}}
with open("output.json", "w", encoding="utf-8") as handle:
    json.dump(payload, handle)
"""

    def artifact(code: str) -> LLMExperimentCodeArtifact:
        return LLMExperimentCodeArtifact(
            code_artifact_id="code-audit-test",
            run_id="run-audit",
            source_spec_id="spec-audit",
            source_route_id="route-audit",
            source_substrate_id="substrate-audit",
            route_type=BranchRouteType.SYNTHETIC_EXPERIMENT,
            backend_kind=BackendKind.LLM_OPENAI,
            entrypoint="experiment.py",
            code=code,
            expected_output_files=["output.json"],
            required_inputs=[],
            declared_dependencies=[],
            random_seed=1729,
            timeout_seconds=10,
            filesystem_scope="sandbox_workdir_only",
        )

    safe = audit_generated_experiment_code(
        artifact=artifact(safe_code),
        required_metrics=["held_out_mae"],
        negative_controls_required=True,
        allowed_dependencies=[],
    )
    assert safe.passed is True
    assert safe.blocked is False

    unsafe_cases = [
        ("import subprocess\n" + safe_code, "subprocess_found"),
        ("import requests\n" + safe_code, "network_access_found"),
        (safe_code + "\neval('1 + 1')\n", "unsafe_eval_exec_found"),
        (
            safe_code.replace(
                'open("output.json", "w", encoding="utf-8")',
                'open("../escape.json", "w", encoding="utf-8")',
            ),
            "filesystem_escape_found",
        ),
        (
            safe_code.replace(
                'with open("output.json", "w", encoding="utf-8") as handle:\n'
                "    json.dump(payload, handle)\n",
                "",
            ),
            "reasons",
        ),
    ]
    for code, field in unsafe_cases:
        audit = audit_generated_experiment_code(
            artifact=artifact(code),
            required_metrics=["held_out_mae"],
            negative_controls_required=True,
            allowed_dependencies=[],
        )
        assert audit.blocked is True
        assert getattr(audit, field)


def test_metric_extraction_accepts_only_successful_output_json() -> None:
    execution = SandboxExecutionResult(
        execution_id="execution-1",
        code_artifact_id="code-1",
        status="completed",
        exit_code=0,
        stdout_path="runs/run/reports/stdout.txt",
        stderr_path="runs/run/reports/stderr.txt",
        output_json_path="runs/run/reports/output.json",
        artifact_paths=["runs/run/reports/output.json"],
        runtime_seconds=0.1,
        timeout=False,
        memory_limit_mb=128,
        seed=1729,
    )
    extracted = extract_metrics_from_output(
        execution=execution,
        output_payload={"metrics": {"held_out_mae": 0.25}},
        required_metrics=["held_out_mae"],
        output_json_path=execution.output_json_path,
    )
    missing = extract_metrics_from_output(
        execution=execution,
        output_payload={"metrics": {}},
        required_metrics=["held_out_mae"],
        output_json_path=execution.output_json_path,
    )
    failed = extract_metrics_from_output(
        execution=execution.model_copy(update={"status": "failed", "exit_code": 1}),
        output_payload={"metrics": {"held_out_mae": 0.01}},
        required_metrics=["held_out_mae"],
        output_json_path=execution.output_json_path,
    )

    assert extracted.schema_valid is True
    assert extracted.metrics == {"held_out_mae": 0.25}
    assert extracted.metric_sources["held_out_mae"].endswith("#metrics.held_out_mae")
    assert missing.schema_valid is False
    assert missing.metrics == {}
    assert failed.schema_valid is False
    assert failed.metrics == {}


def test_llm_generated_experiments_execute_and_extract_real_metrics(tmp_path) -> None:
    run_id = "run-generated-experiments-strict"
    store, ledger, route_result = _prepare_m102_route_fixture(tmp_path, run_id)
    generator = MockLLMExperimentCodeGenerator()
    codegen = generate_experiment_code(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        generator=generator,
        config=LLMExperimentCodegenConfig(
            run_id=run_id,
            backend="llm-openai",
            max_executable_specs=8,
            max_codegen_calls=8,
            default_timeout_seconds=10,
            memory_limit_mb=256,
            allowed_dependencies=[],
            require_non_fake_backends=True,
        ),
    )
    code_inspection = inspect_experiment_code(run_id=run_id, root=tmp_path)

    assert route_result.report.execution_spec_count == 8
    assert codegen.report.executable_spec_count == 5
    assert codegen.report.non_executable_spec_count == 3
    assert codegen.report.code_artifact_count == 5
    assert codegen.report.safety_audit_count == 5
    assert codegen.report.blocked_code_count == 1
    assert codegen.report.deferred_non_executable_route_count == 3
    assert codegen.report.fixture_metric_count == 0
    assert codegen.report.production_ready is True
    assert codegen.report.publication_ready is False
    assert len(generator.received_spec_ids) == 5
    assert all((tmp_path / path).is_file() for path in codegen.report.code_artifact_paths)
    blocked = next(item for item in codegen.report.safety_audits if item.blocked)
    assert "subprocess" in blocked.subprocess_found
    assert code_inspection.generated_experiment_present is True
    assert code_inspection.blocked_code_count == 1
    assert any(
        item.stage_kind == ScientificStageKind.EXPERIMENT_CODE_GENERATION
        and item.backend_kind == BackendKind.LLM_OPENAI
        for item in codegen.report.backend_records
    )
    assert any(
        item.stage_kind == ScientificStageKind.CODE_SAFETY_AUDIT
        and item.backend_kind == BackendKind.LOCAL_EXECUTION
        for item in codegen.report.backend_records
    )

    execution = run_generated_experiments(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    inspected = inspect_generated_experiment_results(run_id=run_id, root=tmp_path)

    assert execution.report.executed_code_count == 4
    assert execution.report.failed_execution_count == 1
    assert execution.report.metric_extraction_count == 4
    assert execution.report.fixture_metric_count == 0
    assert execution.report.production_ready is True
    assert execution.report.publication_ready is False
    assert all((tmp_path / path).is_file() for path in execution.report.sandbox_execution_paths)
    assert all((tmp_path / path).is_file() for path in execution.report.metric_extraction_paths)
    successful = [item for item in execution.report.generated_results if item.status == "completed"]
    assert {item.evidence_label for item in successful} >= {
        "SyntheticExperimentEvidence",
        "BenchmarkEvidence",
    }
    assert all(item.metrics for item in successful)
    assert all(
        source.endswith(("#metrics.held_out_mae", "#metrics.held_out_rmse"))
        for item in successful
        for source in item.metric_sources.values()
    )
    negative_control = next(
        item
        for item in execution.report.generated_results
        if "Negative controls did not pass" in " ".join(item.warnings)
    )
    assert negative_control.status == "inconclusive"
    assert negative_control.evidence_label == "InconclusiveResult"
    failed_results = [
        item
        for item in execution.report.generated_results
        if item.status in {"failed", "blocked_safety_audit"}
    ]
    assert failed_results
    assert all(item.metrics == {} for item in failed_results)
    assert inspected.generated_experiment_present is True
    assert inspected.executed_code_count == 4
    assert any(
        item.stage_kind == ScientificStageKind.EXPERIMENT_EXECUTION
        and item.backend_kind == BackendKind.LOCAL_EXECUTION
        for item in execution.report.backend_records
    )
    assert any(
        item.stage_kind == ScientificStageKind.METRIC_COMPUTATION
        and item.backend_kind == BackendKind.LOCAL_EXECUTION
        for item in execution.report.backend_records
    )

    strict = check_production_mode(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert strict.report.blocking_violation_count == 0
    assert strict.report.production_ready is True

    fallback = MockLLMExperimentCodeGenerator()
    fallback.fallback_used = True
    with pytest.raises(GeneratedExperimentError, match="forbids deterministic fallback"):
        generate_experiment_code(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            generator=fallback,
            config=codegen.report.config,
        )


def test_hybrid_evidence_packages_plan_multiple_artifact_types(tmp_path) -> None:
    run_id = "run-hybrid-evidence-package-planning"
    store, ledger, _ = _prepare_m102_route_fixture(tmp_path, run_id)
    planner = MockHybridEvidencePlanner()
    planned = plan_hybrid_evidence_packages(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=planner,
        config=HybridEvidencePackageConfig(
            run_id=run_id,
            backend="llm-openai",
            max_source_substrates=4,
            max_planning_calls=4,
            require_non_fake_backends=True,
        ),
    )
    inspected = inspect_hybrid_evidence_packages(run_id=run_id, root=tmp_path)

    assert planned.report.package_count == 4
    assert planned.report.artifact_plan_count >= 8
    assert planned.report.artifact_type_coverage >= 6
    assert planned.report.production_ready is True
    assert planned.report.publication_ready is False
    assert len(planner.received_substrate_ids) == 4
    assert any(
        plan.artifact_type == EvidenceArtifactType.SYMBOLIC_REDUCTION
        for package in planned.report.packages
        for plan in package.artifact_plans
    )
    assert any(
        plan.artifact_type == EvidenceArtifactType.NUMERICAL_ILLUSTRATION
        for package in planned.report.packages
        for plan in package.artifact_plans
    )
    assert any(
        plan.artifact_type == EvidenceArtifactType.LITERATURE_NOVELTY_CHECK
        for package in planned.report.packages
        for plan in package.artifact_plans
    )
    assert all(package.novelty_proven is False for package in planned.report.packages)
    assert all(package.publication_ready is False for package in planned.report.packages)
    assert inspected.hybrid_evidence_package_present is True
    assert inspected.package_count == 4
    assert any(
        item.stage_kind == ScientificStageKind.HYBRID_EVIDENCE_PLANNING
        and item.backend_kind == BackendKind.LLM_OPENAI
        for item in planned.report.backend_records
    )
    assert any(
        item.stage_kind == ScientificStageKind.SPEC_VALIDATION
        and item.backend_kind == BackendKind.LOCAL_EXECUTION
        for item in planned.report.backend_records
    )

    strict = check_production_mode(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert strict.report.blocking_violation_count == 0
    assert strict.report.production_ready is True

    valid_raw = planner.raw_items[0]
    proof_claim = json.loads(json.dumps(valid_raw))
    proof_claim["package"]["primary_claim_draft"] = "This is a verified theorem."
    accepted, reasons, _ = parse_hybrid_package_response({"packages": [proof_claim]})
    assert accepted is None
    assert any("theorem verification" in reason for reason in reasons)

    wrong_label = json.loads(json.dumps(valid_raw))
    wrong_label["package"]["artifact_plans"][0]["allowed_evidence_labels"] = [
        "SyntheticExperimentEvidence"
    ]
    accepted, reasons, _ = parse_hybrid_package_response({"packages": [wrong_label]})
    assert accepted is None
    assert any("incompatible evidence labels" in reason for reason in reasons)

    no_path = json.loads(json.dumps(valid_raw))
    no_path["package"]["artifact_plans"] = [
        {
            **no_path["package"]["artifact_plans"][0],
            "artifact_type": "defer_insufficient_support",
            "requires_code_generation": False,
            "requires_local_execution": False,
            "requires_retrieval": False,
            "requires_llm_drafting": False,
            "allowed_evidence_labels": ["UnsupportedRouteDeferred"],
            "metric_plan_optional": None,
            "baseline_or_comparator_plan": [],
            "symbolic_obligations_optional": None,
        }
    ]
    accepted, reasons, _ = parse_hybrid_package_response({"packages": [no_path]})
    assert accepted is None
    assert any("no executable" in reason for reason in reasons)


def test_hybrid_evidence_package_execution_uses_sandbox_metrics_and_draft_boundaries(
    tmp_path,
) -> None:
    run_id = "run-hybrid-evidence-package-execution"
    store, ledger, _ = _prepare_m102_route_fixture(tmp_path, run_id)
    planner = MockHybridEvidencePlanner()
    plan_hybrid_evidence_packages(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=planner,
        config=HybridEvidencePackageConfig(
            run_id=run_id,
            backend="llm-openai",
            max_source_substrates=4,
            max_planning_calls=4,
            require_non_fake_backends=True,
        ),
    )
    code_generator = MockLLMExperimentCodeGenerator()
    executed = execute_hybrid_evidence_packages(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=planner,
        code_generator=code_generator,
        retrieval_mode="real_retrieval",
        require_non_fake_backends=True,
        timeout_seconds=10,
        memory_limit_mb=256,
        allowed_dependencies=[],
    )
    inspected = inspect_evidence_package_execution(run_id=run_id, root=tmp_path)

    assert executed.report.package_count == 4
    assert executed.report.executable_artifact_count >= 5
    assert executed.report.symbolic_artifact_count >= 2
    assert executed.report.retrieval_artifact_count >= 1
    assert executed.report.code_artifact_count == executed.report.executable_artifact_count
    assert executed.report.safety_audit_count == executed.report.code_artifact_count
    assert executed.report.blocked_code_count >= 1
    assert executed.report.executed_code_count >= 1
    assert executed.report.metric_extraction_count >= 1
    assert executed.report.production_ready is True
    assert executed.report.publication_ready is False
    assert executed.report.novelty_proven is False
    assert all((tmp_path / path).is_file() for path in executed.report.code_artifact_paths)
    assert all((tmp_path / path).is_file() for path in executed.report.sandbox_execution_paths)
    assert all((tmp_path / path).is_file() for path in executed.report.metric_extraction_paths)
    completed = [item for item in executed.report.results if item.status == "completed"]
    assert completed
    assert all(item.metrics for item in completed)
    assert all(
        "#metrics." in source for item in completed for source in item.metric_sources.values()
    )
    drafts = [item for item in executed.report.results if item.status == "draft_created"]
    assert drafts
    assert any(item.evidence_label == "SymbolicReductionDraft" for item in drafts)
    assert any(item.evidence_label == "ProofPlanDraft" for item in drafts)
    retrieval = next(
        item
        for item in drafts
        if item.artifact_type == EvidenceArtifactType.LITERATURE_NOVELTY_CHECK
    )
    assert retrieval.evidence_label == "RetrievalNoveltyAssessment"
    assert retrieval.novelty_proven is False
    assert retrieval.draft_payload["novelty_proven"] is False
    assert retrieval.creates_real_world_validation is False
    blocked_or_failed = [
        item
        for item in executed.report.results
        if item.status in {"blocked_safety_audit", "failed", "inconclusive"}
    ]
    assert blocked_or_failed
    assert all(item.metrics == {} for item in blocked_or_failed if item.status != "inconclusive")
    assert inspected.evidence_package_execution_present is True
    assert inspected.result_count == executed.report.result_count
    assert any(
        item.stage_kind == ScientificStageKind.EXPERIMENT_CODE_GENERATION
        and item.backend_kind == BackendKind.LLM_OPENAI
        for item in executed.report.backend_records
    )
    assert any(
        item.stage_kind == ScientificStageKind.SYMBOLIC_DERIVATION
        and item.backend_kind == BackendKind.LLM_OPENAI
        for item in executed.report.backend_records
    )
    assert any(
        item.stage_kind == ScientificStageKind.LITERATURE_RETRIEVAL
        and item.backend_kind == BackendKind.RETRIEVAL_REAL
        for item in executed.report.backend_records
    )
    assert any(
        item.stage_kind == ScientificStageKind.METRIC_COMPUTATION
        and item.backend_kind == BackendKind.LOCAL_EXECUTION
        for item in executed.report.backend_records
    )

    strict = check_production_mode(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert strict.report.blocking_violation_count == 0
    assert strict.report.production_ready is True

    fallback = MockHybridEvidencePlanner()
    fallback.fallback_used = True
    with pytest.raises(HybridEvidencePackageError, match="forbids deterministic fallback"):
        plan_hybrid_evidence_packages(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            planner=fallback,
            config=HybridEvidencePackageConfig(
                run_id=run_id,
                backend="llm-openai",
                require_non_fake_backends=True,
            ),
        )


def test_scientific_critic_ensemble_and_cross_package_adjudication(tmp_path) -> None:
    run_id = "run-scientific-critic-adjudication"
    store, ledger, _ = _prepare_m102_route_fixture(tmp_path, run_id)
    package_planner = MockHybridEvidencePlanner()
    plan_hybrid_evidence_packages(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=package_planner,
        config=HybridEvidencePackageConfig(
            run_id=run_id,
            backend="llm-openai",
            max_source_substrates=4,
            max_planning_calls=4,
            require_non_fake_backends=True,
        ),
    )
    execute_hybrid_evidence_packages(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=package_planner,
        code_generator=MockLLMExperimentCodeGenerator(
            unsafe_index=-1,
            negative_control_failure_index=-1,
            runtime_failure_index=-1,
        ),
        retrieval_mode="real_retrieval",
        require_non_fake_backends=True,
        timeout_seconds=10,
        memory_limit_mb=256,
        allowed_dependencies=[],
    )
    critic = MockScientificCritic()
    reviews = critique_evidence_packages(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        critic=critic,
        require_non_fake_backends=True,
    )
    adjudicated = adjudicate_evidence_packages(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        critic=critic,
        require_non_fake_backends=True,
    )
    inspected = inspect_package_adjudication(run_id=run_id, root=tmp_path)

    assert reviews.report.critic_review_count == 4 * len(ScientificCriticRole)
    assert reviews.report.blocking_finding_count >= 3
    finding_types = {
        finding.finding_type for review in reviews.report.reviews for finding in review.findings
    }
    assert ScientificCriticFindingType.WEAK_BASELINE in finding_types
    assert ScientificCriticFindingType.FALSE_BRIDGE in finding_types
    assert ScientificCriticFindingType.OVERCLAIM in finding_types
    assert any(
        item.stage_kind == ScientificStageKind.CRITIC_REVIEW
        and item.backend_kind == BackendKind.LLM_OPENAI
        for item in reviews.report.backend_records
    )
    assert adjudicated.report.adjudicated_package_count == 4
    nucleus = adjudicated.report.paper_nucleus_selection_optional
    assert nucleus is not None
    assert nucleus.primary_package_id == adjudicated.report.decisions[0].package_id
    assert {
        "real-world validation",
        "verified theorem",
        "novelty proven",
        "publication ready",
    } <= set(nucleus.forbidden_claims)
    assert all(item.publication_ready is False for item in adjudicated.report.decisions)
    assert any(
        item.stage_kind == ScientificStageKind.ADJUDICATION
        and item.backend_kind == BackendKind.LLM_OPENAI
        for item in adjudicated.report.backend_records
    )
    assert any(
        item.stage_kind == ScientificStageKind.ADJUDICATION_SCORE_AGGREGATION
        and item.backend_kind == BackendKind.LOCAL_EXECUTION
        for item in adjudicated.report.backend_records
    )
    assert inspected.package_adjudication_present is True
    assert inspected.primary_nucleus_selected is True
    assert inspected.publication_ready is False

    strict = check_production_mode(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert strict.report.blocking_violation_count == 0
    assert strict.report.production_ready is True


def test_blocking_critic_finding_prevents_primary_nucleus_selection(tmp_path) -> None:
    run_id = "run-scientific-critic-blocked-primary"
    store, ledger, _ = _prepare_m102_route_fixture(tmp_path, run_id)
    package_planner = MockHybridEvidencePlanner()
    plan_hybrid_evidence_packages(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=package_planner,
        config=HybridEvidencePackageConfig(
            run_id=run_id,
            backend="llm-openai",
            max_source_substrates=4,
            max_planning_calls=4,
            require_non_fake_backends=True,
        ),
    )
    execute_hybrid_evidence_packages(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=package_planner,
        code_generator=MockLLMExperimentCodeGenerator(
            unsafe_index=-1,
            negative_control_failure_index=-1,
            runtime_failure_index=-1,
        ),
        retrieval_mode="real_retrieval",
        require_non_fake_backends=True,
        timeout_seconds=10,
        memory_limit_mb=256,
        allowed_dependencies=[],
    )
    critic = MockScientificCritic(block_primary=True)
    critique_evidence_packages(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        critic=critic,
        require_non_fake_backends=True,
    )

    with pytest.raises(EvidencePackageAdjudicationError, match="ineligible for primary nucleus"):
        adjudicate_evidence_packages(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            critic=critic,
            require_non_fake_backends=True,
        )


def _prepare_m105_nucleus_fixture(tmp_path, run_id: str):
    store, ledger, _ = _prepare_m102_route_fixture(tmp_path, run_id)
    package_planner = MockHybridEvidencePlanner()
    plan_hybrid_evidence_packages(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=package_planner,
        config=HybridEvidencePackageConfig(
            run_id=run_id,
            backend="llm-openai",
            max_source_substrates=4,
            max_planning_calls=4,
            require_non_fake_backends=True,
        ),
    )
    execute_hybrid_evidence_packages(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=package_planner,
        code_generator=MockLLMExperimentCodeGenerator(
            unsafe_index=-1,
            negative_control_failure_index=-1,
            runtime_failure_index=-1,
        ),
        retrieval_mode="real_retrieval",
        require_non_fake_backends=True,
        timeout_seconds=10,
        memory_limit_mb=256,
        allowed_dependencies=[],
    )
    store.write_json(
        run_id=run_id,
        artifact_id="retrieval-context-9999",
        artifact_type=ArtifactType.REPORT,
        data=RetrievalContext(
            context_id="retrieval-context-9999",
            run_id=run_id,
            source_pair_id="mock-primary-pair",
            retrieval_mode="real_retrieval",
            backend_name="retrieval-real-mocked-record",
            query="bounded synthetic comparison",
            sources=[
                RetrievedSourceSummary(
                    source_id="doi:10.1000/mock",
                    title="Mock prior work for bounded retrieval context",
                    authors=["Mock Author"],
                    year=2024,
                    doi="10.1000/mock",
                    provider="mocked-production-record",
                    fake_or_mocked=False,
                )
            ],
            retrieval_confidence=0.70,
            limitations=["Retrieved context is not novelty proof."],
        ).model_dump(mode="json"),
    )
    critic = MockScientificCritic()
    critique_evidence_packages(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        critic=critic,
        require_non_fake_backends=True,
    )
    adjudicated = adjudicate_evidence_packages(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        critic=critic,
        require_non_fake_backends=True,
    )
    assert adjudicated.report.paper_nucleus_selection_optional is not None
    return store, ledger


def test_nucleus_manuscript_synthesis_and_bounded_revision(tmp_path) -> None:
    run_id = "run-nucleus-manuscript"
    store, ledger = _prepare_m105_nucleus_fixture(tmp_path, run_id)
    planner = MockNucleusManuscriptPlanner()
    config = NucleusManuscriptConfig(
        run_id=run_id,
        backend="llm-openai",
        require_non_fake_backends=True,
    )

    planned = plan_nucleus_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=planner,
        config=config,
    )
    drafted = synthesize_nucleus_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=planner,
        config=config,
    )
    revised = revise_nucleus_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=planner,
        config=config,
    )
    inspection = inspect_nucleus_manuscript(run_id=run_id, root=tmp_path)

    assert planned.report.manuscript_plan_optional is not None
    assert (
        planned.report.manuscript_plan_optional.paper_type == NucleusPaperType.SYNTHETIC_BENCHMARK
    )
    assert drafted.report.draft_optional is not None
    assert "Artifact-Bound Metrics" in drafted.report.draft_optional.markdown
    assert drafted.report.claim_artifact_bindings
    assert all(item.supporting_artifact_ids for item in drafted.report.claim_artifact_bindings)
    assert revised.report.revised_draft_optional is not None
    assert revised.report.revision_report_optional is not None
    assert revised.report.revision_report_optional.claim_artifact_validation_passed is True
    assert len(planner.critic_calls) == 2 * len(ManuscriptCriticRole)
    assert inspection.revised_draft_present is True
    assert inspection.publication_ready is False
    assert any(
        item.stage_kind == ScientificStageKind.MANUSCRIPT_SYNTHESIS
        and item.backend_kind == BackendKind.LLM_OPENAI
        for item in revised.report.backend_records
    )
    assert any(
        item.stage_kind == ScientificStageKind.CLAIM_AUDIT
        and item.backend_kind == BackendKind.LOCAL_EXECUTION
        for item in revised.report.backend_records
    )
    strict = check_production_mode(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert strict.report.blocking_violation_count == 0


def test_nucleus_manuscript_defers_without_primary_nucleus(tmp_path) -> None:
    run_id = "run-nucleus-manuscript-no-nucleus"
    store = ArtifactStore(tmp_path)
    store.init_run(run_id)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    store.write_json(
        run_id=run_id,
        artifact_id="cross-package-adjudication-report-0001",
        artifact_type=ArtifactType.REPORT,
        data=CrossPackageAdjudicationReport(
            run_id=run_id,
            report_id="cross-package-adjudication-report-0001",
            adjudication_status="completed_with_warnings",
            source_package_report_path="runs/missing/reports/packages.json",
            source_execution_report_path="runs/missing/reports/execution.json",
            critic_review_count=0,
            adjudicated_package_count=0,
            blocking_finding_count=0,
            production_ready=False,
        ).model_dump(mode="json"),
    )

    result = plan_nucleus_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=MockNucleusManuscriptPlanner(),
        config=NucleusManuscriptConfig(run_id=run_id, backend="llm-openai"),
    )

    assert result.report.manuscript_status == NucleusManuscriptStatus.MANUSCRIPT_DEFERRED
    assert "No primary paper nucleus" in result.report.blocking_reasons[0]
    assert result.report.publication_ready is False


def test_nucleus_manuscript_rejects_unsafe_draft_and_blocks_after_revision(tmp_path) -> None:
    run_id = "run-nucleus-manuscript-unsafe"
    store, ledger = _prepare_m105_nucleus_fixture(tmp_path, run_id)
    unsafe = MockNucleusManuscriptPlanner(unsafe_draft=True)
    config = NucleusManuscriptConfig(
        run_id=run_id,
        backend="llm-openai",
        require_non_fake_backends=True,
    )
    plan_nucleus_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=unsafe,
        config=config,
    )
    deferred = synthesize_nucleus_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=unsafe,
        config=config,
    )
    assert deferred.report.manuscript_status == NucleusManuscriptStatus.MANUSCRIPT_DEFERRED
    assert any("real-world validation" in item for item in deferred.report.blocking_reasons)

    run_id = "run-nucleus-manuscript-block-after-revision"
    store, ledger = _prepare_m105_nucleus_fixture(tmp_path, run_id)
    blocking = MockNucleusManuscriptPlanner(block_after_revision=True)
    config = NucleusManuscriptConfig(
        run_id=run_id,
        backend="llm-openai",
        require_non_fake_backends=True,
    )
    plan_nucleus_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=blocking,
        config=config,
    )
    synthesize_nucleus_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=blocking,
        config=config,
    )
    result = revise_nucleus_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=blocking,
        config=config,
    )
    assert result.report.manuscript_status == NucleusManuscriptStatus.MANUSCRIPT_DEFERRED
    assert result.report.revised_draft_optional is None
    assert result.report.revision_report_optional is not None
    assert result.report.revision_report_optional.remaining_blocking_findings


def _prepare_m106_final_paper_fixture(tmp_path, run_id: str):
    store, ledger = _prepare_m105_nucleus_fixture(tmp_path, run_id)
    planner = MockNucleusManuscriptPlanner()
    config = NucleusManuscriptConfig(
        run_id=run_id,
        backend="llm-openai",
        require_non_fake_backends=True,
    )
    plan_nucleus_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=planner,
        config=config,
    )
    synthesize_nucleus_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=planner,
        config=config,
    )
    revise_nucleus_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        planner=planner,
        config=config,
    )
    return store, ledger


def test_final_paper_assembly_verification_and_bundle(tmp_path) -> None:
    run_id = "run-final-paper-m106"
    store, ledger = _prepare_m106_final_paper_fixture(tmp_path, run_id)
    assembled = assemble_final_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        config=FinalPaperAssemblyConfig(run_id=run_id, require_non_fake_backends=True),
    )

    assert isinstance(assembled.report, FinalPaperAssemblyReport)
    assert assembled.report.assembly_status == "assembled"
    assert assembled.manifest_optional is not None
    manifest = FinalPaperManifest.model_validate(assembled.manifest_optional)
    assert manifest.table_records
    assert all(item.resolved for item in manifest.artifact_bindings)
    assert all(item.deterministically_assembled for item in manifest.table_records)
    assert (tmp_path / manifest.main_markdown_path).is_file()
    assert (tmp_path / manifest.main_latex_path).is_file()
    assert (tmp_path / manifest.claim_artifact_map_path).is_file()
    assert (tmp_path / manifest.evidence_citation_bindings_path).is_file()
    assert (tmp_path / manifest.provenance_manifest_path).is_file()
    assert assembled.report.publication_ready is False

    verified = verify_final_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert isinstance(verified.report, FinalPaperVerificationReport)
    assert verified.report.verification_status in {"verified", "verified_with_warnings"}
    assert verified.report.publication_ready is False

    bundle = build_final_paper_bundle(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    assert isinstance(bundle.report, FinalPaperAssemblyReport)
    assert bundle.report.assembly_status == "assembled"
    bundle_dir = tmp_path / bundle.report.bundle_path_optional
    assert (bundle_dir / "paper" / "final-paper.md").is_file()
    assert (bundle_dir / "paper" / "final-paper.tex").is_file()
    assert (bundle_dir / "reports" / "final-paper-manifest.json").is_file()
    assert (bundle_dir / "reports" / "verification-report.json").is_file()
    assert (bundle_dir / "reproducibility" / "hashes.sha256").is_file()
    assert (bundle_dir / "provenance" / "open-obligations.json").is_file()
    assert not list(bundle_dir.rglob("*raw*llm*"))

    inspected = inspect_final_paper(run_id=run_id, root=tmp_path)
    assert inspected.final_paper_present is True
    assert inspected.verification_present is True
    assert inspected.bundle_present is True
    assert inspected.table_count >= 1
    assert inspected.publication_ready is False
    strict = check_production_mode(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert strict.report.blocking_violation_count == 0


def test_final_paper_defers_for_missing_revision_artifact_or_figure(tmp_path) -> None:
    run_id = "run-final-paper-m106-missing"
    store = ArtifactStore(tmp_path)
    store.init_run(run_id)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    deferred = assemble_final_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        config=FinalPaperAssemblyConfig(run_id=run_id),
    )
    assert deferred.report.assembly_status == "deferred"
    assert "No valid revised nucleus manuscript" in deferred.report.blocking_findings[0]

    run_id = "run-final-paper-m106-missing-figure"
    store, ledger = _prepare_m106_final_paper_fixture(tmp_path, run_id)
    reports = tmp_path / "runs" / run_id / "reports"
    revision_path = sorted(
        path
        for path in reports.glob("nucleus-manuscript-synthesis-report-*.json")
        if re.fullmatch(r"nucleus-manuscript-synthesis-report-\d{4}\.json", path.name)
    )[-1]
    payload = json.loads(revision_path.read_text(encoding="utf-8"))
    payload["revised_draft_optional"]["markdown"] += (
        "\n![Missing result figure](runs/" + run_id + "/experiments/missing-result.png)\n"
    )
    revision_path.write_text(json.dumps(payload), encoding="utf-8")
    deferred = assemble_final_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        config=FinalPaperAssemblyConfig(run_id=run_id, require_non_fake_backends=True),
    )
    assert deferred.report.assembly_status == "deferred"
    assert any("Referenced figure" in item for item in deferred.report.blocking_findings)


def test_final_paper_verifier_detects_metric_hash_scope_and_citation_tampering(tmp_path) -> None:
    run_id = "run-final-paper-m106-tampering"
    store, ledger = _prepare_m106_final_paper_fixture(tmp_path, run_id)
    assembled = assemble_final_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        config=FinalPaperAssemblyConfig(run_id=run_id, require_non_fake_backends=True),
    )
    manifest = assembled.manifest_optional
    assert manifest is not None
    source = Path(manifest.table_records[0].rows[0]["metric_source"].split("#", 1)[0])
    source_path = tmp_path / source
    source_payload = json.loads(source_path.read_text(encoding="utf-8"))
    metric_name = manifest.table_records[0].rows[0]["metric"]
    source_payload["metrics"][metric_name] += 0.01
    source_path.write_text(json.dumps(source_payload), encoding="utf-8")
    result = verify_final_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert result.report.verification_status == "failed"
    assert any(item.finding_type == "metric_mismatch" for item in result.report.findings)

    final_markdown = tmp_path / manifest.main_markdown_path
    final_markdown.write_text("This establishes real-world validation.\n", encoding="utf-8")
    result = verify_final_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert any(item.finding_type == "forbidden_claim" for item in result.report.findings)
    assert any(
        item.finding_type == "missing_scope_qualification" for item in result.report.findings
    )


def test_final_paper_requires_validated_sandbox_metric_sources_and_matching_manuscript_values(
    tmp_path,
) -> None:
    run_id = "run-final-paper-m106-metric-contract"
    store, ledger = _prepare_m106_final_paper_fixture(tmp_path, run_id)
    reports = tmp_path / "runs" / run_id / "reports"
    execution_path = sorted(
        path
        for path in reports.glob("evidence-package-execution-report-*.json")
        if re.fullmatch(r"evidence-package-execution-report-\d{4}\.json", path.name)
    )[-1]
    execution_payload = json.loads(execution_path.read_text(encoding="utf-8"))
    result = next(item for item in execution_payload["results"] if item["metrics"])
    metric = next(iter(result["metrics"]))
    result["metric_sources"][metric] = (
        f"runs/{run_id}/reports/{result['result_id']}.json#metrics.{metric}"
    )
    execution_path.write_text(json.dumps(execution_payload), encoding="utf-8")

    deferred = assemble_final_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        config=FinalPaperAssemblyConfig(run_id=run_id, require_non_fake_backends=True),
    )
    assert deferred.report.assembly_status == "deferred"
    assert any(
        "validated execution output source" in item
        for item in deferred.report.blocking_findings
    )

    run_id = "run-final-paper-m106-manuscript-metric-mismatch"
    store, ledger = _prepare_m106_final_paper_fixture(tmp_path, run_id)
    reports = tmp_path / "runs" / run_id / "reports"
    revision_path = sorted(
        path
        for path in reports.glob("nucleus-manuscript-synthesis-report-*.json")
        if re.fullmatch(r"nucleus-manuscript-synthesis-report-\d{4}\.json", path.name)
    )[-1]
    revision_payload = json.loads(revision_path.read_text(encoding="utf-8"))
    revision_payload["revised_draft_optional"]["markdown"] = revision_payload[
        "revised_draft_optional"
    ]["markdown"].replace("0.100114450153294", "0.999", 1)
    revision_path.write_text(json.dumps(revision_payload), encoding="utf-8")

    deferred = assemble_final_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        config=FinalPaperAssemblyConfig(run_id=run_id, require_non_fake_backends=True),
    )
    assert deferred.report.assembly_status == "deferred"
    assert any(
        "Manuscript metric value differs" in item for item in deferred.report.blocking_findings
    )


def test_final_paper_verifier_handles_missing_bibliography_and_bound_artifact_hashes(
    tmp_path,
) -> None:
    run_id = "run-final-paper-m106-missing-citation"
    store, ledger = _prepare_m106_final_paper_fixture(tmp_path, run_id)
    assembled = assemble_final_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        config=FinalPaperAssemblyConfig(run_id=run_id, require_non_fake_backends=True),
    )
    manifest = assembled.manifest_optional
    assert manifest is not None
    assert manifest.bibliography_path_optional is not None

    bound_artifact = next(
        item for item in manifest.artifact_bindings if item.artifact_type != "retrieval_context"
    )
    artifact_path = tmp_path / "runs" / run_id / "reports" / f"{bound_artifact.artifact_id}.json"
    artifact_path.write_text(artifact_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    (tmp_path / manifest.bibliography_path_optional).unlink()

    verified = verify_final_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert verified.report.verification_status == "failed"
    assert any(item.finding_type == "hash_mismatch" for item in verified.report.findings)
    assert any(item.finding_type == "missing_citation" for item in verified.report.findings)


def test_final_paper_inserts_assembled_latex_assets_before_document_terminator(tmp_path) -> None:
    run_id = "run-final-paper-m106-latex-terminator"
    store, ledger = _prepare_m106_final_paper_fixture(tmp_path, run_id)
    reports = tmp_path / "runs" / run_id / "reports"
    revision_path = sorted(
        path
        for path in reports.glob("nucleus-manuscript-synthesis-report-*.json")
        if re.fullmatch(r"nucleus-manuscript-synthesis-report-\d{4}\.json", path.name)
    )[-1]
    revision_payload = json.loads(revision_path.read_text(encoding="utf-8"))
    revision_payload["revised_draft_optional"]["latex"] += "\n\\end{document}\n"
    revision_path.write_text(json.dumps(revision_payload), encoding="utf-8")

    assembled = assemble_final_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        config=FinalPaperAssemblyConfig(run_id=run_id, require_non_fake_backends=True),
    )
    manifest = assembled.manifest_optional
    assert manifest is not None
    latex = (tmp_path / manifest.main_latex_path).read_text(encoding="utf-8")
    assert latex.index(r"\section*{Reconstructed Result Tables}") < latex.rindex(
        r"\end{document}"
    )

    result_artifact = tmp_path / "runs" / run_id / "reports" / (
        manifest.artifact_bindings[0].artifact_id + ".json"
    )
    result_artifact.write_text("{}\n", encoding="utf-8")
    result = verify_final_paper(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        require_non_fake_backends=True,
    )
    assert any(item.finding_type == "hash_mismatch" for item in result.report.findings)


def test_deterministic_run_exposes_context_only_idea_tree(tmp_path) -> None:
    run_id = "run-idea-tree"
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id=run_id,
            domain="human geography",
            root=tmp_path,
        )
    )
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    commit_count = len(ledger.list_commits(run_id))

    report = inspect_idea_tree(run_id=run_id, root=tmp_path)
    markdown_export = export_idea_tree(
        run_id=run_id,
        root=tmp_path,
        export_format="markdown",
    )
    json_export = export_idea_tree(
        run_id=run_id,
        root=tmp_path,
        export_format="json",
    )

    root_node = next(node for node in report.nodes if node.node_id == report.root_node_id)
    stage_a_nodes = [node for node in report.nodes if node.stage_origin == "stage_a"]
    stage_b_nodes = [node for node in report.nodes if node.stage_origin == "stage_b"]
    selected_nodes = [node for node in report.nodes if node.selected_for_stage_c]
    pruned_nodes = [node for node in report.nodes if node.status == "pruned"]

    assert report.tree_present is True
    assert root_node.status == "root"
    assert root_node.title == "human geography"
    assert report.node_count > 1
    assert report.edge_count > 0
    assert stage_a_nodes
    assert stage_b_nodes
    assert all(node.parent_id_optional == report.root_node_id for node in stage_a_nodes)
    assert all(node.parent_id_optional for node in stage_b_nodes)
    assert selected_nodes
    assert all(node.status == "selected" for node in selected_nodes)
    assert pruned_nodes
    assert all(node.prune_reason_optional for node in pruned_nodes)
    assert report.publication_ready is False
    assert report.creates_scientific_validation is False
    assert report.is_verification_evidence is False
    assert len(ledger.list_commits(run_id)) == commit_count

    markdown_path = tmp_path / markdown_export.export_path
    json_path = tmp_path / json_export.export_path
    markdown = markdown_path.read_text(encoding="utf-8")
    exported = IdeaTreeInspectionReport.model_validate_json(json_path.read_text(encoding="utf-8"))
    assert "Root: human geography" in markdown
    assert "Candidate 1:" in markdown
    assert "Variant" in markdown
    assert "provenance/context only" in markdown
    assert "publication_ready=false" in markdown
    assert exported.node_count == report.node_count
    assert exported.edge_count == report.edge_count
    assert exported.publication_ready is False
    assert len(ledger.list_commits(run_id)) == commit_count

    stage_b_report = tmp_path / "runs" / run_id / "reports" / "stage-b-report.md"
    stage_b_report.unlink()
    degraded = inspect_idea_tree(run_id=run_id, root=tmp_path)
    assert degraded.tree_present is True
    assert any("Stage B report is unavailable" in warning for warning in degraded.warnings)


def test_idea_space_diagnostic_flags_collapsed_scientific_axes(tmp_path) -> None:
    run_id = "run-idea-space"
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id=run_id,
            domain="spatial heterogeneity in human geography",
            root=tmp_path,
        )
    )
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    commit_count = len(ledger.list_commits(run_id))

    report = inspect_idea_space(run_id=run_id, root=tmp_path)
    markdown_path = export_idea_space_report(
        run_id=run_id,
        root=tmp_path,
        export_format="markdown",
    )
    json_path = export_idea_space_report(
        run_id=run_id,
        root=tmp_path,
        export_format="json",
    )

    markdown = (tmp_path / markdown_path).read_text(encoding="utf-8")
    exported = IdeaSpaceInspectionReport.model_validate_json(
        (tmp_path / json_path).read_text(encoding="utf-8")
    )

    assert report.tree_present is True
    assert report.node_count > 1
    assert report.feature_count >= 20
    assert report.feature_vectors
    assert any(vector.uses_synthetic_data for vector in report.feature_vectors)
    assert report.diversity_score == "low"
    assert report.near_duplicate_node_pairs
    assert any("synthetic stress testing" in warning for warning in report.collapsed_axis_warnings)
    assert any("concrete model" in warning for warning in report.missing_axis_warnings)
    assert any("equation" in warning for warning in report.missing_axis_warnings)
    assert any("validation-mode variants" in warning for warning in report.collapsed_axis_warnings)
    assert "region-specific distance-decay gravity model" in (report.recommended_mutation_axes)
    assert "PCA/low-rank OD-flow representation model" in (report.recommended_mutation_axes)
    assert report.pca_inspired_branch["model_idea"].startswith(
        "Represent regional OD-flow residuals"
    )
    assert report.effective_rank >= 0
    assert 0.0 <= report.pc1_explained_variance <= 1.0
    assert "Idea-space diversity: low" in markdown
    assert "PCA/low-rank OD-flow representation model" in markdown
    assert "provenance/context only" in markdown
    assert "publication_ready=false" in markdown
    assert exported.diversity_score == "low"
    assert exported.recommended_mutation_axes == report.recommended_mutation_axes
    assert exported.publication_ready is False
    assert exported.is_verification_evidence is False
    assert len(ledger.list_commits(run_id)) == commit_count


def test_scientific_substrate_builds_concrete_models_from_idea_space(
    tmp_path,
) -> None:
    run_id = "run-scientific-substrate"
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id=run_id,
            domain="spatial heterogeneity in human geography",
            root=tmp_path,
        )
    )
    idea_space = inspect_idea_space(run_id=run_id, root=tmp_path)
    assert "region-specific distance-decay gravity model" in (idea_space.recommended_mutation_axes)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")

    result = build_scientific_substrate(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_substrates=2,
    )
    inspection = inspect_scientific_substrate(run_id=run_id, root=tmp_path)
    markdown = (
        tmp_path / "runs" / run_id / "reports" / "scientific-substrate-build-0001.md"
    ).read_text(encoding="utf-8")

    assert result.persistence.commit.action_type == ControllerActionType.SCIENTIFIC_SUBSTRATE_BUILT
    assert result.report.substrate_count >= 2
    assert inspection.scientific_substrate_present is True
    assert inspection.substrate_count >= 2
    assert inspection.equation_present is True
    assert inspection.baseline_present is True
    assert inspection.experiment_design_present is True
    assert inspection.result_schema_present is True
    assert inspection.publication_ready is False
    distance = next(
        substrate for substrate in result.substrates if "Distance Decay" in substrate.title
    )
    pca = next(
        substrate for substrate in result.substrates if "Low-Rank Residual" in substrate.title
    )
    assert distance.selected_for_next_experiment is True
    assert (
        "F_ij = A_i B_j d_ij^{-alpha_i} exp(epsilon_ij)" in distance.concrete_model_object.equations
    )
    assert distance.baseline == "pooled-alpha gravity baseline"
    assert distance.measurable_hypothesis.startswith(
        "A heterogeneous-alpha spatial interaction model improves held-out"
    )
    assert {"MAE", "RMSE"} <= set(distance.experiment_design.metrics)
    assert "Vary heterogeneity strength" in distance.experiment_design.ablation_or_stress_test
    assert distance.variables_and_notation
    assert distance.assumptions
    assert distance.publication_ready is False
    assert distance.creates_scientific_validation is False
    assert "R_{ij}" in " ".join(pca.concrete_model_object.equations)
    assert "R ≈ U_k S_k V_k^T" in pca.concrete_model_object.equations
    assert "latent-factor recovery correlation" in pca.experiment_design.metrics
    assert pca.selected_for_next_experiment is False
    assert "Region-Specific Distance Decay" in markdown
    assert "Low-Rank Residual Axes" in markdown
    assert "publication_ready: false" in markdown


def test_substrate_experiment_routes_executes_and_links_bounded_result(
    tmp_path,
) -> None:
    run_id = "run-substrate-experiment"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    source_bundle = (
        Path(__file__).parent
        / "fixtures"
        / "experiments"
        / "bundles"
        / "distance_decay_spatial_interaction"
    )
    target_bundle = (
        tmp_path
        / "tests"
        / "fixtures"
        / "experiments"
        / "bundles"
        / "distance_decay_spatial_interaction"
    )
    target_bundle.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_bundle, target_bundle)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        enable_empirical_demonstration_gaps=True,
    )
    build_scientific_substrate(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_substrates=2,
    )

    routed = route_substrate_experiment(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    assert routed.report.substrate_experiment_routed is True
    assert routed.report.experiment_bundle_optional == (
        "tests/fixtures/experiments/bundles/distance_decay_spatial_interaction"
    )
    assert routed.spec is not None
    assert routed.spec.model_equation == ("F_ij = A_i B_j d_ij^{-alpha_i} exp(epsilon_ij)")
    assert routed.spec.baseline_model == "pooled-alpha gravity model"
    assert routed.spec.method_model == "heterogeneous-alpha spatial interaction model"
    assert {"MAE", "RMSE"} <= set(routed.spec.metric_names)
    assert routed.spec.heterogeneity_settings == [
        "low_heterogeneity",
        "high_heterogeneity",
    ]

    sandbox = run_python_experiment_sandbox(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_spec=tmp_path / routed.spec_artifact.path,
        sandbox_backend="uv_local",
        execution_mode="apply",
    )
    inspected = inspect_substrate_experiment_routing(run_id=run_id, root=tmp_path)
    experiment = ExperimentArtifact.model_validate_json(
        (tmp_path / sandbox.report.ingested_experiment_artifact_path_optional).read_text(
            encoding="utf-8"
        )
    )
    substrate_result_path = next(
        path
        for path in experiment.artifact_paths
        if path.endswith("substrate-experiment-result.json")
    )
    substrate_result = SubstrateExperimentResult.model_validate_json(
        (tmp_path / substrate_result_path).read_text(encoding="utf-8")
    )
    claim_map = ClaimEvidenceMap.model_validate_json(
        latest_claim_evidence_map_path(tmp_path, run_id).read_text(encoding="utf-8")
    )
    bounded_link = next(
        link
        for link in claim_map.links
        if link.claim_id == BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID
    )

    assert sandbox.report.sandbox_status == "completed"
    assert experiment.experiment_type == "substrate_distance_decay_uv_local"
    assert experiment.status == "completed"
    assert experiment.metrics["claim_support_satisfied"] is True
    assert len(experiment.metrics["comparison_table"]) == 2
    assert experiment.metrics["test_mae_method"] < experiment.metrics["test_mae_baseline"]
    assert experiment.metrics["test_rmse_method"] <= experiment.metrics["test_rmse_baseline"]
    assert inspected["comparison_table_present"] is True
    assert inspected["heterogeneity_ablation_present"] is True
    assert inspected["method_beat_baseline"] is True
    assert inspected["claim_evidence_linked"] is True
    assert bounded_link.classification == "experiment_supported_claim"
    assert experiment.experiment_id in bounded_link.supporting_experiment_artifact_ids
    assert substrate_result.result_label == "SyntheticExperimentVerified"
    assert substrate_result.publication_ready is False
    assert experiment.creates_scientific_validation is False


def test_substrate_experiment_negative_result_is_inconclusive_not_crash() -> None:
    table = SubstrateExperimentComparisonTable(
        columns=["setting", "baseline_mae", "method_mae"],
        rows=[
            {"setting": "low_heterogeneity", "baseline_mae": 1.0, "method_mae": 1.1},
            {"setting": "high_heterogeneity", "baseline_mae": 1.0, "method_mae": 1.2},
        ],
        heterogeneity_ablation_present=True,
    )
    result = SubstrateExperimentResult(
        run_id="negative-substrate-run",
        experiment_spec_id="negative-spec",
        substrate_id="distance-substrate",
        target_claim_id="bounded-claim",
        result_status="negative_result",
        result_label="NegativeResult",
        claim_support_satisfied=False,
        comparison_table=table,
        bounded_result_summary=(
            "The method did not satisfy the bounded synthetic comparison rule."
        ),
        limitations=["Synthetic scope only."],
    )
    assert result.result_label == "NegativeResult"
    assert result.claim_support_satisfied is False
    assert result.publication_ready is False
    assert result.is_verification_evidence is False


def test_autonomous_loop_prefers_selected_substrate_experiment_route(tmp_path) -> None:
    run_id = "run-loop-substrate-experiment"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    source_bundle = (
        Path(__file__).parent
        / "fixtures"
        / "experiments"
        / "bundles"
        / "distance_decay_spatial_interaction"
    )
    target_bundle = (
        tmp_path
        / "tests"
        / "fixtures"
        / "experiments"
        / "bundles"
        / "distance_decay_spatial_interaction"
    )
    target_bundle.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_bundle, target_bundle)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        enable_empirical_demonstration_gaps=True,
    )
    build_scientific_substrate(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_substrates=2,
    )

    loop = run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        loop_backend="deterministic",
        max_iterations=2,
        max_attempts_per_gap=1,
        enable_experiment_routing=True,
        enable_empirical_demonstration_gaps=True,
        python_sandbox_backend="uv_local",
        max_sandbox_runs_per_loop=1,
        max_sandbox_runs_per_iteration=1,
    )
    routing = inspect_substrate_experiment_routing(run_id=run_id, root=tmp_path)

    assert routing["substrate_experiment_routed"] is True
    assert routing["experiment_bundle_optional"].endswith("distance_decay_spatial_interaction")
    assert routing["sandbox_status"] == "completed"
    assert routing["comparison_table_present"] is True
    assert loop.report.publication_ready is False
    assert not list(
        (tmp_path / "runs" / run_id / "reports").glob(
            "experiment-gap-routing-[0-9][0-9][0-9][0-9].json"
        )
    )


def test_substrate_tournament_runs_distance_and_pca_branches_and_updates_final_manuscript(
    tmp_path,
) -> None:
    run_id = "run-substrate-tournament"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    fixture_root = Path(__file__).parent / "fixtures" / "experiments" / "bundles"
    target_root = tmp_path / "tests" / "fixtures" / "experiments" / "bundles"
    target_root.mkdir(parents=True, exist_ok=True)
    for bundle_name in [
        "distance_decay_spatial_interaction",
        "pca_low_rank_od_residual",
        "hierarchical_alpha_spatial_interaction",
        "gravity_low_rank_residual_hybrid",
        "boundary_perturbation_distance_decay",
    ]:
        shutil.copytree(fixture_root / bundle_name, target_root / bundle_name)
    retrieval_quality = RetrievalQualityReport(
        run_id=run_id,
        retrieval_backend="local",
        total_retrieved_sources=2,
        accepted_source_count=1,
        rejected_source_count=1,
        accepted_source_ids=["fixture-smith"],
        rejected_source_ids=["fixture-rejected"],
        adequacy_status="bounded_context_only",
        coverage_limitations=["Local fixture coverage is bounded context only."],
    )
    reports = tmp_path / "runs" / run_id / "reports"
    (reports / "llm-orchestration-config.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "domain": "spatial heterogeneity in human geography",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (reports / "retrieval-quality-report.json").write_text(
        retrieval_quality.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        enable_empirical_demonstration_gaps=True,
    )
    run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        loop_backend="deterministic",
        max_iterations=1,
        max_attempts_per_gap=1,
    )
    build_scientific_substrate(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_substrates=2,
    )

    tournament = run_substrate_tournament(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    inspected = inspect_substrate_tournament(run_id=run_id, root=tmp_path)
    pca_spec_path = next(
        path for path in tournament.result.generated_experiment_spec_paths if "pca-low-rank" in path
    )
    pca_spec = SubstrateExperimentSpec.model_validate_json(
        (tmp_path / pca_spec_path).read_text(encoding="utf-8")
    )
    pca_entry = next(
        entry
        for entry in tournament.result.entries
        if entry.substrate_model_type == "low_rank_gravity_residual_representation"
    )
    distance_entry = next(
        entry
        for entry in tournament.result.entries
        if entry.substrate_model_type == "region_specific_distance_decay_gravity"
    )
    pca_experiment = ExperimentArtifact.model_validate_json(
        (tmp_path / pca_entry.experiment_artifact_path_optional).read_text(encoding="utf-8")
    )
    claim_map = ClaimEvidenceMap.model_validate_json(
        latest_claim_evidence_map_path(tmp_path, run_id).read_text(encoding="utf-8")
    )

    assert tournament.result.substrate_count >= 2
    assert tournament.result.winner_selected is True
    assert tournament.result.comparison.comparison_table_present is True
    assert inspected.tournament_present is True
    assert inspected.distance_decay_branch_completed is True
    assert inspected.pca_low_rank_branch_completed is True
    assert distance_entry.sandbox_status == "completed"
    assert pca_entry.sandbox_status == "completed"
    assert pca_entry.result_status == "supported"
    assert "R_{ij}" in pca_spec.model_equation
    assert "R ≈ U_k S_k V_k^T" in pca_spec.model_equation
    assert pca_spec.experiment_bundle_id == "pca_low_rank_od_residual"
    assert pca_experiment.experiment_type == "substrate_pca_low_rank_uv_local"
    assert "latent_factor_recovery_correlation" in pca_experiment.metrics
    assert "explained_residual_variance" in pca_experiment.metrics
    assert pca_experiment.metrics["claim_support_satisfied"] is True
    assert claim_map.unsupported_non_scaffold_claim_ids == []

    mutation_plan = plan_creative_mutations(
        run_id=run_id,
        root=tmp_path,
        max_mutations=5,
    )
    mutation_inspection = inspect_creative_mutations(run_id=run_id, root=tmp_path)
    applied_mutations = apply_creative_mutations(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_mutations=3,
    )
    post_mutation_inspection = inspect_creative_mutations(run_id=run_id, root=tmp_path)
    idea_tree = inspect_idea_tree(run_id=run_id, root=tmp_path)
    scientific_substrate_inspection = inspect_scientific_substrate(
        run_id=run_id,
        root=tmp_path,
    )
    idea_space = inspect_idea_space(run_id=run_id, root=tmp_path)

    mutation_titles = {candidate.title for candidate in mutation_plan.candidates}
    assert mutation_plan.mutation_count >= 4
    assert mutation_plan.selected_for_substrate_build_count >= 3
    assert mutation_inspection.creative_mutation_plan_present is True
    assert mutation_inspection.includes_hierarchical_alpha_mutation is True
    assert mutation_inspection.includes_gravity_low_rank_hybrid is True
    assert mutation_inspection.includes_boundary_perturbation_robustness is True
    assert mutation_inspection.includes_kernelized_spatial_interaction is True
    assert (
        "Hierarchical Region-Cluster Distance Decay in Synthetic Spatial Interaction"
        in mutation_titles
    )
    assert (
        "Gravity Plus Low-Rank Residual Correction for Synthetic OD Heterogeneity"
        in mutation_titles
    )
    assert "Boundary-Perturbation Robustness of Region-Specific Distance Decay" in (mutation_titles)
    assert "Kernelized Spatial Interaction Under Synthetic Regional Heterogeneity" in (
        mutation_titles
    )
    assert applied_mutations.report.applied_mutation_count == 3
    assert applied_mutations.report.new_idea_tree_node_count == 3
    assert applied_mutations.report.new_scientific_substrate_count == 3
    assert post_mutation_inspection.new_idea_tree_nodes_added is True
    assert post_mutation_inspection.new_scientific_substrates_created is True
    mutation_nodes = [node for node in idea_tree.nodes if node.stage_origin == "creative_mutation"]
    assert len(mutation_nodes) >= 3
    assert any("Hierarchical Region-Cluster" in node.title for node in mutation_nodes)
    assert any("Gravity Plus Low-Rank" in node.title for node in mutation_nodes)
    assert any("Boundary-Perturbation" in node.title for node in mutation_nodes)
    assert scientific_substrate_inspection.substrate_count >= 5
    assert any(
        substrate.concrete_model_object.model_type == "hierarchical_region_cluster_distance_decay"
        for substrate in scientific_substrate_inspection.substrates
    )
    assert any(
        substrate.concrete_model_object.model_type == "gravity_low_rank_residual_hybrid"
        for substrate in scientific_substrate_inspection.substrates
    )
    assert any(
        substrate.concrete_model_object.model_type
        == "boundary_perturbation_distance_decay_robustness"
        for substrate in scientific_substrate_inspection.substrates
    )
    assert any(vector.stage_origin == "creative_mutation" for vector in idea_space.feature_vectors)
    assert all(
        not candidate.publication_ready
        and not candidate.creates_scientific_validation
        and not candidate.implies_publication_readiness
        and not candidate.is_verification_evidence
        for candidate in mutation_plan.candidates
    )

    mutation_tournament = run_mutation_tournament(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    inspected_mutation_tournament = inspect_mutation_tournament(
        run_id=run_id,
        root=tmp_path,
    )
    mutation_entries = mutation_tournament.result.entries
    hierarchical_entry = next(
        entry
        for entry in mutation_entries
        if entry.substrate_model_type == "hierarchical_region_cluster_distance_decay"
    )
    hybrid_entry = next(
        entry
        for entry in mutation_entries
        if entry.substrate_model_type == "gravity_low_rank_residual_hybrid"
    )
    boundary_entry = next(
        entry
        for entry in mutation_entries
        if entry.substrate_model_type == "boundary_perturbation_distance_decay_robustness"
    )
    original_entry = next(
        entry for entry in mutation_entries if entry.branch_role == "original_winner"
    )
    hybrid_spec = SubstrateExperimentSpec.model_validate_json(
        (tmp_path / hybrid_entry.experiment_spec_path_optional).read_text(encoding="utf-8")
    )

    assert mutation_tournament.result.original_winner_included is True
    assert mutation_tournament.result.mutation_substrate_count == 3
    assert mutation_tournament.result.second_generation_winner_selected is True
    assert mutation_tournament.result.comparison.comparison_table_present is True
    assert inspected_mutation_tournament.mutation_tournament_present is True
    assert inspected_mutation_tournament.hierarchical_alpha_branch_completed is True
    assert inspected_mutation_tournament.hybrid_low_rank_branch_completed is True
    assert inspected_mutation_tournament.boundary_robustness_branch_completed is True
    assert original_entry.status == "completed"
    assert hierarchical_entry.status == "completed"
    assert hybrid_entry.status == "completed"
    assert boundary_entry.status == "completed"
    assert hierarchical_entry.complexity_penalty_optional is not None
    assert hybrid_entry.experiment_bundle_id == "gravity_low_rank_residual_hybrid"
    assert boundary_entry.experiment_bundle_id == "boundary_perturbation_distance_decay"
    assert "U_k S_k V_k^T" in hybrid_spec.model_equation
    assert mutation_tournament.result.tournament_outcome in {
        "original_winner_remains_best",
        "hierarchical_alpha_wins_by_parsimony",
        "hybrid_wins_when_low_rank_residual_structure_exists",
        "robustness_branch_wins_by_stability",
    }
    assert mutation_tournament.result.unsupported_claim_count == 0
    assert mutation_tournament.result.publication_ready is False

    final = regenerate_final_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )
    markdown = final.manuscript_markdown
    title = markdown.splitlines()[0]
    bundle = build_final_release_bundle(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    verification = verify_final_release_bundle(bundle_path=tmp_path / bundle.report.bundle_path)

    assert mutation_tournament.result.second_generation_winner_title_optional in title
    assert "A substrate tournament compared serious synthetic branches" in markdown
    assert "A second-generation mutation tournament compared" in markdown
    assert (
        "| branch | role | outcome | improvement | complexity penalty | robustness | score |"
        in markdown
    )
    assert mutation_tournament.result.second_generation_winner_title_optional in markdown
    assert "Boundary-Perturbation Robustness" in markdown
    assert "Gravity Plus Low-Rank Residual Correction" in markdown
    assert "Hierarchical Region-Cluster Distance Decay" in markdown
    assert "| substrate | result | MAE ratio | RMSE ratio | ablation | score |" in markdown
    assert tournament.result.winner_substrate_title_optional in markdown
    assert "Low-Rank Residual Axes" in markdown
    assert "latent-factor recovery correlation" in markdown
    assert final.report.unsupported_claim_count == 0
    assert final.report.publication_ready is False
    assert bundle.report.bundle_status == "complete"
    assert verification.verification_status in {"verified", "verified_with_warnings"}
    assert verification.unsupported_claim_count == 0
    assert verification.publication_ready is False

    generation_preview = plan_generation_mutations(
        run_id=run_id,
        root=tmp_path,
        cycle_index=2,
        max_mutations=5,
        write_report=False,
    )
    generation_titles = {candidate.title for candidate in generation_preview.candidates}
    fixed_titles = {candidate.title for candidate in mutation_plan.candidates}
    assert generation_preview.context.current_winner_title == (
        mutation_tournament.result.second_generation_winner_title_optional
    )
    assert generation_preview.context.source_creative_search_report_path is None
    assert all(
        candidate.source_idea_node_ids == ["creative-mutation-boundary-perturbation-robustness"]
        for candidate in generation_preview.candidates
    )
    assert generation_preview.mutation_count == 5
    assert generation_preview.selected_for_substrate_build_count >= 3
    assert "Multi-Scale Boundary Robustness for Region-Specific Distance Decay" in (
        generation_titles
    )
    assert "Clustered Distance Decay Under Boundary Perturbation" in generation_titles
    assert (
        "Low-Rank Residual Diagnostics for Boundary-Induced Spatial Heterogeneity"
        in generation_titles
    )
    assert (
        "Adversarial Boundary Perturbation Stress Test for Distance-Decay Models"
        in generation_titles
    )
    assert "Null Heterogeneity Boundary Stress Test" in generation_titles
    assert generation_titles.isdisjoint(fixed_titles)
    assert generation_preview.diversity_check.diversity_check_passed is True

    creative_search = run_creative_search(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_cycles=1,
        min_improvement=0.01,
    )
    search_inspection = inspect_creative_search(run_id=run_id, root=tmp_path)
    search_markdown = (tmp_path / creative_search.report.final_manuscript_path_optional).read_text(
        encoding="utf-8"
    )
    search_verification = verify_final_release_bundle(
        bundle_path=tmp_path / creative_search.report.final_bundle_path_optional
    )

    assert creative_search.report.cycle_count >= 1
    assert creative_search.report.stop_reason in {
        CreativeSearchStopReason.MAX_CYCLES_REACHED,
        CreativeSearchStopReason.NO_NEW_MUTATIONS,
    }
    assert creative_search.report.lineage_present is True
    assert creative_search.report.score_improvement_recorded is True
    assert (
        creative_search.report.starting_winner == tournament.result.winner_substrate_title_optional
    )
    assert (
        creative_search.report.ending_winner
        == mutation_tournament.result.second_generation_winner_title_optional
    )
    assert creative_search.report.ending_score > creative_search.report.starting_score
    assert search_inspection.creative_search_present is True
    assert search_inspection.lineage_present is True
    assert "| cycle | starting branch | starting score | ending branch |" in search_markdown
    assert "Winning lineage:" in search_markdown
    assert creative_search.report.unsupported_claim_count == 0
    assert creative_search.report.publication_ready is False
    assert search_verification.verification_status in {"verified", "verified_with_warnings"}
    assert search_verification.unsupported_claim_count == 0
    assert search_verification.publication_ready is False

    generation_inspection = inspect_generation_mutations(
        run_id=run_id,
        root=tmp_path,
    )
    generation_tree = inspect_idea_tree(run_id=run_id, root=tmp_path)
    generation_substrates = inspect_scientific_substrate(run_id=run_id, root=tmp_path)
    generation_cycle = creative_search.report.cycles[0]
    assert creative_search.report.stop_reason is not (CreativeSearchStopReason.NO_NEW_MUTATIONS)
    assert generation_cycle.new_idea_nodes_added >= 3
    assert generation_cycle.new_substrates_added >= 3
    assert "generation_mutation_apply" in generation_cycle.steps_executed
    assert generation_inspection.applied_mutation_count >= 3
    assert generation_inspection.new_idea_tree_node_count >= 3
    assert generation_inspection.new_scientific_substrate_count >= 3
    assert sum(node.stage_origin == "generation_mutation" for node in generation_tree.nodes) >= 3
    assert generation_substrates.substrate_count >= 8
    assert creative_search.report.unsupported_claim_count == 0
    assert creative_search.report.publication_ready is False

    exhausted = plan_generation_mutations(
        run_id=run_id,
        root=tmp_path,
        cycle_index=3,
        max_mutations=5,
        write_report=False,
    )
    assert exhausted.planning_status == "no_new_generation_mutations"
    assert exhausted.mutation_count == 0


def test_creative_search_stop_policy_handles_budget_no_mutations_and_no_improvement() -> None:
    config = CreativeSearchControllerConfig(
        run_id="creative-search-policy",
        max_cycles=3,
        min_improvement=0.01,
    )
    reason, _ = _cycle_stop_decision(
        cycle_index=1,
        config=config,
        no_improvement_cycles=0,
        no_new_mutations=True,
        all_mutations_inconclusive=False,
        diversity_collapsed=False,
    )
    assert reason is CreativeSearchStopReason.NO_NEW_MUTATIONS

    reason, _ = _cycle_stop_decision(
        cycle_index=2,
        config=config,
        no_improvement_cycles=2,
        no_new_mutations=False,
        all_mutations_inconclusive=False,
        diversity_collapsed=False,
    )
    assert reason is CreativeSearchStopReason.NO_SCORE_IMPROVEMENT

    reason, _ = _cycle_stop_decision(
        cycle_index=3,
        config=config,
        no_improvement_cycles=0,
        no_new_mutations=False,
        all_mutations_inconclusive=False,
        diversity_collapsed=False,
    )
    assert reason is CreativeSearchStopReason.MAX_CYCLES_REACHED


def test_generate_full_paper_library_writes_expected_bundle(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-1")
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    result = generate_full_paper(
        run_id="run-1",
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        config=FullPaperGenerationConfig(run_id="run-1", write_report=True),
    )

    bundle = result.artifact_bundle
    assert result.report.generation_status in {
        FullPaperGenerationStatus.PAPER_GENERATION_SUCCEEDED,
        FullPaperGenerationStatus.PAPER_GENERATION_SUCCEEDED_WITH_WARNINGS,
    }
    assert bundle.citation_registry_artifact_id == "citation-registry"
    assert bundle.complete_manuscript_draft_artifact_id == "complete-manuscript-draft"
    assert bundle.latex_artifact_id == "paper"
    assert bundle.latex_source_map_artifact_id == "latex-source-map"
    assert bundle.paper_critic_report_artifact_id == "paper-critic-report"
    assert bundle.full_paper_generation_report_artifact_id == "full-paper-generation-report"
    assert bundle.full_paper_artifact_bundle_artifact_id == "full-paper-artifact-bundle"
    assert bundle.claim_support_audit_artifact_id == "claim-support-audit"
    assert result.report.publication_ready is False
    assert result.report.is_verification_evidence is False
    for ref in (result.report_artifact, result.bundle_artifact):
        assert ref is not None
        _assert_non_evidence_artifact(tmp_path, ref)


def test_full_paper_generation_writes_fake_semantic_adjudication_audit(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-adjudicated")
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs/run-adjudicated/ledger.sqlite")

    generate_full_paper(
        run_id="run-adjudicated",
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        claim_adjudicator=FakeClaimAdjudicator(),
        config=FullPaperGenerationConfig(run_id="run-adjudicated", write_report=True),
    )

    path = tmp_path / "runs/run-adjudicated/reports/claim-support-audit.json"
    audit = ClaimSupportAuditReport.model_validate_json(path.read_text(encoding="utf-8"))
    assert audit.claim_adjudication_enabled is True
    assert audit.claim_adjudicator_backend == "fake"
    assert audit.claim_adjudication_calls >= 1
    assert audit.creates_scientific_validation is False
    assert audit.implies_publication_readiness is False


def test_openai_adjudication_normalizes_negated_evidence_boundary_claims() -> None:
    markdown = (
        "# Evidence Boundaries\n\n"
        "Source relevance adjudication does not create proof, experiment evidence, "
        "novelty evidence, empirical validation, correctness, or human approval. "
        "The absence of proof artifacts and experiment artifacts remains a visible "
        "workflow limitation.\n"
    )

    class MisclassifyingTransport:
        endpoint = "mock://openai"

        def create_response(
            self,
            *,
            api_key: str,
            model: str,
            prompt: str,
            response_schema: dict[str, object],
        ) -> dict[str, object]:
            del api_key, model, response_schema
            payload = json.loads(prompt.split("\n\n", 1)[1])
            rows = []
            for sentence in payload["sentences"]:
                rows.append(
                    {
                        "sentence_id": sentence["sentence_id"],
                        "claim_class": "proof_claim",
                        "requires_citation": False,
                        "citation_use": "none",
                        "forbidden_claim_detected": True,
                        "citation_as_validation_misuse": False,
                        "publication_readiness_claim": False,
                        "reasoning_brief": "Deliberately over-classified by mocked OpenAI.",
                        "confidence": 0.9,
                    }
                )
            return {"adjudications": rows}

    audit = build_claim_support_audit(
        run_id="negated-boundary-openai",
        markdown=markdown,
        citation_registry=None,
        claim_adjudicator=OpenAIClaimAdjudicator(
            api_key="test-key",
            model="test-model",
            transport=MisclassifyingTransport(),
            allow_external_calls=True,
            max_calls=1,
        ),
    )

    risky_items = [
        item
        for item in audit.claim_support_items
        if item.adjudication_reasoning_brief
        and "Authority claim class suppressed" in item.adjudication_reasoning_brief
    ]
    assert risky_items
    assert audit.summary_counts["forbidden_claim"] == 0
    assert audit.citation_as_validation_misuse_count == 0
    assert {item.claim_class for item in risky_items} == {"evidence_boundary_statement"}
    assert (
        classify_claim_sentence(
            "Source relevance adjudication does not create proof, experiment evidence, "
            "novelty evidence, empirical validation, correctness, or human approval."
        )
        == "evidence_boundary_statement"
    )


def test_generate_paper_cli_works_and_json_is_valid(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-json")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-json",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    generated = payload["full_paper_generation_result"]
    assert generated["is_verification_evidence"] is False
    assert generated["report"]["publication_ready"] is False
    bundle = generated["artifact_bundle"]
    assert bundle["complete_manuscript_draft_artifact_id"] == "complete-manuscript-draft"
    assert bundle["latex_artifact_id"] == "paper"
    assert bundle["paper_critic_report_artifact_id"] == "paper-critic-report"


def test_generate_paper_write_report_writes_full_report_and_bundle(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-report")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-report",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    artifacts = payload["artifacts"]
    report_ref = ArtifactRef.model_validate(artifacts["full_paper_generation_report"])
    bundle_ref = ArtifactRef.model_validate(artifacts["full_paper_artifact_bundle"])
    _assert_non_evidence_artifact(tmp_path, report_ref)
    _assert_non_evidence_artifact(tmp_path, bundle_ref)
    assert (tmp_path / "runs" / "run-report" / "reports" / "citation-registry.json").is_file()
    assert (tmp_path / "runs" / "run-report" / "reports" / "complete-manuscript-draft.md").is_file()
    assert (tmp_path / "runs" / "run-report" / "latex" / "paper.tex").is_file()
    assert (tmp_path / "runs" / "run-report" / "reports" / "paper-critic-report.json").is_file()
    claim_support_path = tmp_path / "runs" / "run-report" / "reports" / "claim-support-audit.json"
    assert claim_support_path.is_file()
    claim_support = json.loads(claim_support_path.read_text(encoding="utf-8"))
    assert claim_support["creates_scientific_validation"] is False


def test_generate_paper_without_revision_does_not_write_revised_draft(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-no-revision")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-no-revision",
            "--write-report",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not (
        tmp_path / "runs" / "run-no-revision" / "reports" / "revised-manuscript-draft.md"
    ).exists()


def test_generate_paper_with_safe_fake_revision_writes_revised_draft(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-revision")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-revision",
            "--apply-safe-fake-revision",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    bundle = payload["full_paper_generation_result"]["artifact_bundle"]
    assert bundle["revised_manuscript_draft_artifact_id"] == "revised-manuscript-draft"
    revised = tmp_path / "runs" / "run-revision" / "reports" / "revised-manuscript-draft.md"
    assert revised.is_file()
    linked = ArtifactRef.model_validate_json(
        (tmp_path / "runs/run-revision/reports/revised-manuscript-draft.md.meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert linked.metadata["is_verification_evidence"] is False
    assert "publication ready" not in revised.read_text(encoding="utf-8").lower()


def test_generate_paper_reexports_latex_after_revision(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-reexport")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-reexport",
            "--apply-safe-fake-revision",
            "--reexport-latex-after-revision",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    artifacts = payload["artifacts"]
    revised_ref = ArtifactRef.model_validate(artifacts["revised_paper"])
    assert revised_ref.path.endswith("latex/revised-paper.tex")
    _assert_non_evidence_artifact(tmp_path, revised_ref)
    bundle = payload["full_paper_generation_result"]["artifact_bundle"]
    assert bundle["revised_latex_artifact_id"] == "revised-paper"
    assert bundle["revised_latex_source_map_artifact_id"] == "revised-latex-source-map"


def test_safe_repair_writes_hashed_non_evidence_audit_artifact(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-safe-repair")
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs/run-safe-repair/ledger.sqlite")

    result = generate_full_paper(
        run_id="run-safe-repair",
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        config=FullPaperGenerationConfig(
            run_id="run-safe-repair",
            write_report=True,
        ),
        enable_safe_repair=True,
    )

    assert result.revision_result is not None
    repair_ref = result.revision_result.safe_repair_report_artifact
    assert repair_ref is not None
    _assert_non_evidence_artifact(tmp_path, repair_ref)
    payload = json.loads((tmp_path / repair_ref.path).read_text(encoding="utf-8"))
    assert payload["before_content_hash"]
    assert payload["after_content_hash"]
    assert payload["invented_citations"] is False
    assert payload["created_or_upgraded_labels"] is False
    assert payload["source_aware_missing_citation_repairs_attempted"] >= 0
    assert payload["source_aware_citations_added"] >= 0
    assert payload["source_aware_claims_downgraded"] >= 0
    assert payload["source_aware_claims_removed"] >= 0
    assert payload["source_aware_repairs_unresolved"] >= 0
    assert payload["source_aware_repair_used_rejected_source"] is False
    assert payload["source_aware_repair_used_hard_rejected_source"] is False
    assert payload["citation_required_items_adjudicated_or_repaired"] is True
    assert payload["creates_scientific_validation"] is False
    assert payload["implies_publication_readiness"] is False
    assert payload["is_verification_evidence"] is False
    assert result.artifact_bundle.revised_manuscript_draft_artifact_id == (
        "revised-manuscript-draft"
    )
    assert result.artifact_bundle.revised_latex_artifact_id == "revised-paper"
    revised_markdown = (
        tmp_path / "runs/run-safe-repair/reports/revised-manuscript-draft.md"
    ).read_text(encoding="utf-8")
    assert "## Central Message" not in revised_markdown
    assert "**Central message.**" in revised_markdown
    lint = lint_paper_bundle_summary(run_id="run-safe-repair", root=tmp_path)
    assert lint["main_body_section_count"] == 7
    assert lint["appendix_section_count"] == 2
    assert lint["standalone_central_message_detected"] is False
    assert lint["central_message_merged"] is True


def test_deterministic_quality_repair_writes_safe_report_and_revised_draft(
    tmp_path,
) -> None:
    _prepare_run(tmp_path, run_id="run-quality-repair")
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs/run-quality-repair/ledger.sqlite")

    result = generate_full_paper(
        run_id="run-quality-repair",
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        config=FullPaperGenerationConfig(
            run_id="run-quality-repair",
            write_report=True,
            quality_repair_backend="deterministic",
            quality_repair_model="unused",
        ),
        enable_safe_repair=True,
    )

    assert result.quality_repair_report_artifact is not None
    _assert_non_evidence_artifact(tmp_path, result.quality_repair_report_artifact)
    report_path = tmp_path / result.quality_repair_report_artifact.path
    report = QualityRepairReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    assert report.quality_repair_enabled is True
    assert report.quality_repair_backend == "deterministic"
    assert report.quality_repair_status in {"repaired", "no_action_needed"}
    assert report.claim_support_rechecked_after_repair is True
    assert report.citation_safety_rechecked_after_repair is True
    assert report.section_depth_targets["Abstract"]["min_words"] == 130
    assert report.sections_below_target_after == []
    assert report.placeholder_like_sections_after == []
    assert report.warnings_reduced_count >= 1
    assert "Draft may be skeletal: below proxy word-count target." in (
        report.quality_warnings_before
    )
    assert "Draft may be skeletal: below proxy word-count target." not in (
        report.quality_warnings_after
    )
    for heading, target in report.section_depth_targets.items():
        assert report.section_word_counts_after[heading] >= target["min_words"]
    assert report.creates_scientific_validation is False
    assert report.implies_publication_readiness is False
    assert report.is_verification_evidence is False
    assert result.artifact_bundle.quality_repair_report_artifact_id == ("quality-repair-report")
    assert result.artifact_bundle.revised_manuscript_draft_artifact_id == (
        "revised-manuscript-draft"
    )
    revised = (tmp_path / "runs/run-quality-repair/reports/revised-manuscript-draft.md").read_text(
        encoding="utf-8"
    )
    lowered = revised.lower()
    assert "publication_ready=false" in revised
    assert "publication ready" not in lowered
    assert "empirically validated" not in lowered
    assert "source relevance and retrieval adequacy remain non-evidential" in lowered
    assert "accepted_source_count" in revised
    assert "absence of proof artifacts" in lowered
    assert "absence of experiment artifacts" in lowered
    lint = lint_paper_bundle_summary(run_id="run-quality-repair", root=tmp_path)
    assert lint["quality_repair_report_present"] is True
    assert lint["quality_repair_backend"] == "deterministic"
    assert lint["quality_repaired_section_count"] >= 1
    assert lint["section_depth_targets_present"] is True
    assert lint["sections_below_depth_target"] == []
    assert lint["placeholder_sections_after_quality_repair"] == []
    assert lint["warnings_reduced_count"] >= 1
    assert lint["limitations_concrete_constraint_count"] >= 2
    assert lint["claim_support_rechecked_after_quality_repair"] is True
    assert lint["citation_safety_rechecked_after_quality_repair"] is True
    assert lint["claim_support_forbidden_claim_count"] == 0
    assert lint["citation_as_validation_misuse_count"] == 0
    assert lint["unregistered_citation_keys"] == []
    assert lint["publication_ready"] is False


def test_reviewer_bundle_summary_is_written_after_release(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-reviewer-summary")
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs/run-reviewer-summary/ledger.sqlite")

    generate_full_paper(
        run_id="run-reviewer-summary",
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        config=FullPaperGenerationConfig(
            run_id="run-reviewer-summary",
            write_report=True,
            quality_repair_backend="deterministic",
        ),
        enable_safe_repair=True,
    )
    release = run_full_paper_release_gate(
        run_id="run-reviewer-summary",
        root=tmp_path,
        store=store,
        ledger=ledger,
        config=FullPaperReleaseGateConfig(
            run_id="run-reviewer-summary",
            write_report=True,
        ),
    )

    json_path = tmp_path / "runs/run-reviewer-summary/reports/reviewer-bundle-summary.json"
    markdown_path = tmp_path / "runs/run-reviewer-summary/reports/reviewer-bundle-summary.md"
    assert json_path.is_file()
    assert markdown_path.is_file()
    assert release.reviewer_summary_artifact is not None
    assert release.reviewer_summary_markdown_artifact is not None
    _assert_non_evidence_artifact(tmp_path, release.reviewer_summary_artifact)
    _assert_non_evidence_artifact(
        tmp_path,
        release.reviewer_summary_markdown_artifact,
    )

    summary = ReviewerBundleSummary.model_validate_json(json_path.read_text(encoding="utf-8"))
    inspected = inspect_reviewer_bundle_summary(
        run_id="run-reviewer-summary",
        root=tmp_path,
    )
    markdown = markdown_path.read_text(encoding="utf-8")
    lowered = markdown.casefold()

    assert summary.release_status == release.report.decision.status.value
    assert summary.publication_ready is False
    assert summary.claim_support_status == "clean"
    assert summary.citation_status in {"registry-backed", "no-citations-required"}
    assert summary.retrieval_quality_status
    assert summary.source_relevance_status
    assert summary.quality_repair_status in {"repaired", "no_action_needed"}
    assert summary.creates_scientific_validation is False
    assert summary.implies_publication_readiness is False
    assert summary.is_verification_evidence is False
    assert any("No proof artifact" in gap for gap in summary.evidence_gaps)
    assert any("No experiment artifact" in gap for gap in summary.evidence_gaps)
    assert any("No human-review artifact" in gap for gap in summary.evidence_gaps)
    assert len(summary.human_review_checklist) > 0
    assert len(summary.recommended_next_actions) > 0
    assert inspected["release_status"] == summary.release_status
    assert inspected["publication_ready"] is False
    assert inspected["summary_path"].endswith("reviewer-bundle-summary.json")
    assert inspected["markdown_summary_path"].endswith("reviewer-bundle-summary.md")
    for phrase in (
        "scientifically validated",
        "validated result",
        "proves novelty",
        "establishes correctness",
        "ready to submit",
        "ready for publication",
        "approved",
    ):
        assert phrase not in lowered


def test_valid_human_review_artifact_is_ingested_and_updates_summary(tmp_path) -> None:
    run_id = "run-human-review"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    review_file = _write_human_review_fixture(tmp_path, run_id=run_id)

    result = ingest_human_review(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        review_file=review_file,
    )

    assert result.review.review_status == "reviewed_ready_for_evidence_generation"
    assert result.review.creates_scientific_validation is False
    assert result.review.implies_publication_readiness is False
    assert result.review.is_verification_evidence is False
    assert result.persistence.commit.action_type == ControllerActionType.HUMAN_REVIEW_INGESTED
    _assert_non_evidence_artifact(tmp_path, result.review_artifact)
    _assert_non_evidence_artifact(tmp_path, result.review_summary_artifact)
    _assert_non_evidence_artifact(tmp_path, result.reviewer_summary_artifact)

    inspected_review = inspect_human_review(run_id=run_id, root=tmp_path)
    assert inspected_review["human_review_artifact_present"] is True
    assert inspected_review["publication_ready"] is False

    summary = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert summary["summary_path"].endswith("reviewer-bundle-summary-after-human-review.json")
    assert summary["human_review_artifact_present"] is True
    assert summary["human_review_status"] == "reviewed_ready_for_evidence_generation"
    assert summary["human_review_blocking_concern_count"] == 0
    assert summary["human_review_requested_change_count"] == 0
    assert summary["publication_ready"] is False
    assert not any("No human-review artifact" in gap for gap in summary["evidence_gaps"])
    assert any("No proof artifact" in gap for gap in summary["evidence_gaps"])
    assert any("No experiment artifact" in gap for gap in summary["evidence_gaps"])

    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["human_review_artifact_present"] is True
    assert lint["human_review_status"] == "reviewed_ready_for_evidence_generation"
    assert lint["publication_ready"] is False


def test_blocking_human_review_concerns_are_surfaced(tmp_path) -> None:
    run_id = "run-human-review-blocking"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    review_file = _write_human_review_fixture(
        tmp_path,
        run_id=run_id,
        review_status="reviewed_with_blocking_changes",
        blocking_concerns=["Problem framing needs human revision."],
        requested_changes=["Revise problem framing before evidence generation."],
        recommended_next_action="Address blocking human-review concerns first.",
    )

    ingest_human_review(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        review_file=review_file,
    )

    summary = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert summary["human_review_blocking_concern_count"] == 1
    assert summary["human_review_requested_change_count"] == 1
    assert "Problem framing needs human revision." in summary["blocking_issues"]
    assert any(
        "blocking human-review concerns" in action for action in summary["recommended_next_actions"]
    )


def test_human_review_run_id_mismatch_is_rejected(tmp_path) -> None:
    run_id = "run-human-review-mismatch"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    review_file = _write_human_review_fixture(
        tmp_path,
        run_id="different-run",
        reviewed_run_id=run_id,
    )

    with pytest.raises(HumanReviewIntakeError, match="run_id does not match"):
        ingest_human_review(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            review_file=review_file,
        )


def test_human_review_missing_checklist_is_rejected(tmp_path) -> None:
    run_id = "run-human-review-missing-checklist"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    review_file = _write_human_review_fixture(
        tmp_path,
        run_id=run_id,
        checklist_items=[],
    )

    with pytest.raises(HumanReviewIntakeError, match="Invalid human review artifact"):
        ingest_human_review(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            review_file=review_file,
        )


def test_human_review_missing_attestation_is_rejected(tmp_path) -> None:
    run_id = "run-human-review-missing-attestation"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    review_file = _write_human_review_fixture(
        tmp_path,
        run_id=run_id,
        reviewer_attestation="   ",
    )

    with pytest.raises(HumanReviewIntakeError, match="attestation is required"):
        ingest_human_review(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            review_file=review_file,
        )


def test_human_review_publication_ready_claim_is_rejected(tmp_path) -> None:
    run_id = "run-human-review-publication-ready"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    review_file = _write_human_review_fixture(
        tmp_path,
        run_id=run_id,
        non_blocking_comments=["This draft is ready for publication."],
    )

    with pytest.raises(HumanReviewIntakeError, match="forbidden publication"):
        ingest_human_review(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            review_file=review_file,
        )


@pytest.mark.parametrize(
    "unsafe_claim",
    [
        "The proof is verified.",
        "The experiment is validated.",
        "Novelty confirmed.",
        "Correctness is established.",
    ],
)
def test_human_review_validation_authority_claims_are_rejected(
    tmp_path,
    unsafe_claim: str,
) -> None:
    run_id = "run-human-review-validation-claims"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    review_file = _write_human_review_fixture(
        tmp_path,
        run_id=run_id,
        non_blocking_comments=[unsafe_claim],
        filename=f"{unsafe_claim.casefold().replace(' ', '-')}.json",
    )

    with pytest.raises(HumanReviewIntakeError, match="forbidden publication"):
        ingest_human_review(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            review_file=review_file,
        )


def test_valid_formal_proof_artifact_is_ingested_and_removes_proof_gap(tmp_path) -> None:
    run_id = "run-proof-formal"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(tmp_path, run_id=run_id)

    result = ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )

    assert result.persistence.commit.action_type == ControllerActionType.PROOF_ARTIFACT_INGESTED
    assert result.proof.is_verification_evidence is True
    _assert_artifact_boundary_flags(
        tmp_path,
        result.proof_artifact,
        is_verification_evidence=True,
    )
    _assert_artifact_boundary_flags(tmp_path, result.proof_index_artifact)
    _assert_artifact_boundary_flags(tmp_path, result.reviewer_summary_artifact)

    inspected = inspect_proof_artifacts(run_id=run_id, root=tmp_path)
    assert inspected["proof_artifact_count"] == 1
    assert inspected["formal_verification_passed_count"] == 1
    assert inspected["proof_evidence_gap_present"] is False

    summary = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert summary["proof_artifact_count"] == 1
    assert summary["formal_verification_artifact_count"] == 1
    assert summary["publication_ready"] is False
    assert not any("No formal proof artifact" in gap for gap in summary["evidence_gaps"])
    assert any("No completed experiment artifact" in gap for gap in summary["evidence_gaps"])

    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["proof_artifact_count"] == 1
    assert lint["formal_verification_passed_count"] == 1
    assert lint["proof_evidence_gap_present"] is False
    assert lint["publication_ready"] is False


def test_informal_proof_note_is_ingested_without_formal_verification(tmp_path) -> None:
    run_id = "run-proof-informal"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        proof_id="informal-proof-note-001",
        proof_type="informal_proof_note",
        checker_status="not_checked",
        is_verification_evidence=False,
        proof_hash="3333333333333333333333333333333333333333333333333333333333333333",
    )

    ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )

    inspected = inspect_proof_artifacts(run_id=run_id, root=tmp_path)
    assert inspected["proof_artifact_count"] == 1
    assert inspected["informal_proof_artifact_count"] == 1
    assert inspected["formal_verification_passed_count"] == 0
    assert inspected["proof_evidence_gap_present"] is True


def test_failed_proof_check_is_ingested_without_removing_proof_gap(tmp_path) -> None:
    run_id = "run-proof-failed"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        proof_id="failed-proof-check-001",
        checker_status="failed",
        is_verification_evidence=False,
        proof_hash="4444444444444444444444444444444444444444444444444444444444444444",
    )

    ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )

    inspected = inspect_proof_artifacts(run_id=run_id, root=tmp_path)
    assert inspected["proof_artifact_count"] == 1
    assert inspected["failed_or_inconclusive_proof_artifact_count"] == 1
    assert inspected["formal_verification_passed_count"] == 0
    assert inspected["proof_evidence_gap_present"] is True


def test_proof_artifact_run_id_mismatch_is_rejected(tmp_path) -> None:
    run_id = "run-proof-mismatch"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id="different-run",
        artifact_run_id=run_id,
    )

    with pytest.raises(EvidenceArtifactIntakeError, match="run_id does not match"):
        ingest_proof_artifact(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            proof_file=proof_file,
        )


def test_proof_artifact_publication_ready_claim_is_rejected(tmp_path) -> None:
    run_id = "run-proof-publication-ready"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        statement="This fixture wrongly says the bundle is publication ready.",
    )

    with pytest.raises(EvidenceArtifactIntakeError, match="forbidden publication"):
        ingest_proof_artifact(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            proof_file=proof_file,
        )


def test_proof_artifact_formal_verification_without_passed_checker_is_rejected(
    tmp_path,
) -> None:
    run_id = "run-proof-bad-formal"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        checker_status="failed",
        is_verification_evidence=True,
        statement="This fixture wrongly says proof verified despite a failed checker.",
    )

    with pytest.raises(EvidenceArtifactIntakeError, match="verification-evidence flag"):
        ingest_proof_artifact(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            proof_file=proof_file,
        )


def test_llm_generated_formal_proof_artifact_is_rejected(tmp_path) -> None:
    run_id = "run-proof-llm-generated"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        statement="As an AI language model, I generated this formal proof text.",
    )

    with pytest.raises(EvidenceArtifactIntakeError, match="LLM-generated proof"):
        ingest_proof_artifact(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            proof_file=proof_file,
        )


def test_completed_experiment_artifact_is_ingested_and_removes_experiment_gap(
    tmp_path,
) -> None:
    run_id = "run-experiment-completed"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    experiment_file = _write_experiment_artifact_fixture(tmp_path, run_id=run_id)

    result = ingest_experiment_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_file=experiment_file,
    )

    assert result.persistence.commit.action_type == (
        ControllerActionType.EXPERIMENT_ARTIFACT_INGESTED
    )
    _assert_artifact_boundary_flags(tmp_path, result.experiment_artifact)
    _assert_artifact_boundary_flags(tmp_path, result.experiment_index_artifact)
    _assert_artifact_boundary_flags(tmp_path, result.reviewer_summary_artifact)

    inspected = inspect_experiment_artifacts(run_id=run_id, root=tmp_path)
    assert inspected["experiment_artifact_count"] == 1
    assert inspected["completed_experiment_count"] == 1
    assert inspected["experiment_evidence_gap_present"] is False

    summary = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert summary["experiment_artifact_count"] == 1
    assert summary["completed_experiment_count"] == 1
    assert not any("No completed experiment artifact" in gap for gap in summary["evidence_gaps"])
    assert any("No formal proof artifact" in gap for gap in summary["evidence_gaps"])

    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["experiment_artifact_count"] == 1
    assert lint["completed_experiment_count"] == 1
    assert lint["experiment_evidence_gap_present"] is False
    assert lint["publication_ready"] is False


@pytest.mark.parametrize("status", ["inconclusive", "failed"])
def test_non_completed_experiment_artifact_does_not_remove_experiment_gap(
    tmp_path,
    status: str,
) -> None:
    run_id = f"run-experiment-{status}"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id=run_id,
        experiment_id=f"{status}-experiment-001",
        status=status,
        config_hash=(
            "8888888888888888888888888888888888888888888888888888888888888888"
            if status == "inconclusive"
            else "9999999999999999999999999999999999999999999999999999999999999999"
        ),
    )

    ingest_experiment_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_file=experiment_file,
    )

    inspected = inspect_experiment_artifacts(run_id=run_id, root=tmp_path)
    assert inspected["experiment_artifact_count"] == 1
    assert inspected["completed_experiment_count"] == 0
    assert inspected["experiment_evidence_gap_present"] is True


def test_experiment_artifact_run_id_mismatch_is_rejected(tmp_path) -> None:
    run_id = "run-experiment-mismatch"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id="different-run",
        artifact_run_id=run_id,
    )

    with pytest.raises(EvidenceArtifactIntakeError, match="run_id does not match"):
        ingest_experiment_artifact(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            experiment_file=experiment_file,
        )


def test_experiment_artifact_broad_validation_claim_is_rejected(tmp_path) -> None:
    run_id = "run-experiment-broad-validation"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id=run_id,
        result_summary="This fixture wrongly says the experiment validated the paper.",
    )

    with pytest.raises(EvidenceArtifactIntakeError, match="forbidden publication"):
        ingest_experiment_artifact(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            experiment_file=experiment_file,
        )


def test_experiment_artifact_publication_ready_claim_is_rejected(tmp_path) -> None:
    run_id = "run-experiment-publication-ready"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id=run_id,
        result_summary="This fixture wrongly says the bundle is publication ready.",
    )

    with pytest.raises(EvidenceArtifactIntakeError, match="forbidden publication"):
        ingest_experiment_artifact(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            experiment_file=experiment_file,
        )


def test_proof_and_experiment_artifacts_update_reviewer_summary_together(
    tmp_path,
) -> None:
    run_id = "run-proof-experiment-summary"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(tmp_path, run_id=run_id)
    experiment_file = _write_experiment_artifact_fixture(tmp_path, run_id=run_id)

    ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )
    ingest_experiment_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_file=experiment_file,
    )

    summary = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert summary["proof_artifact_count"] == 1
    assert summary["formal_verification_artifact_count"] == 1
    assert summary["experiment_artifact_count"] == 1
    assert summary["completed_experiment_count"] == 1
    assert summary["publication_ready"] is False
    assert not any("No formal proof artifact" in gap for gap in summary["evidence_gaps"])
    assert not any("No completed experiment artifact" in gap for gap in summary["evidence_gaps"])
    assert any("No human-review artifact" in gap for gap in summary["evidence_gaps"])

    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["proof_evidence_gap_present"] is False
    assert lint["experiment_evidence_gap_present"] is False
    assert lint["remaining_evidence_gap_count"] == 1


def test_claim_evidence_map_is_persisted_and_summarized(tmp_path) -> None:
    run_id = "run-claim-evidence-persist"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")

    result = persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    assert result.persistence.commit.action_type == (
        ControllerActionType.CLAIM_EVIDENCE_MAP_WRITTEN
    )
    assert result.claim_evidence_map.publication_ready is False
    assert result.claim_evidence_map.creates_scientific_validation is False
    assert (tmp_path / result.map_artifact.path).is_file()
    assert (tmp_path / result.markdown_artifact.path).is_file()
    inspected = inspect_claim_evidence_map(run_id=run_id, root=tmp_path)
    assert inspected["claim_evidence_map_present"] is True
    summary = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert summary["claim_evidence_map_present"] is True
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["claim_evidence_map_present"] is True
    assert lint["publication_ready"] is False


def test_claim_evidence_map_links_citation_supported_background_claim(tmp_path) -> None:
    run_id = "run-claim-evidence-citation"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        items=[
            _claim_support_item(
                sentence_id="background-claim-1",
                claim_class="literature_background_claim",
                citation_keys_present=["smith2021"],
                supporting_source_ids=["source-1"],
                support_status="registry_supported",
            )
        ],
        citation_registry=_citation_registry_fixture(run_id=run_id),
        retrieval_quality=_retrieval_quality_fixture(run_id=run_id),
    )

    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)

    link = claim_map.links[0]
    assert link.support_status == "supported_within_scope"
    assert link.support_type == "citation_background_context"
    assert link.supporting_source_ids == ["source-1"]
    assert claim_map.summary_counts["citation_supported_background_claim"] == 1


@pytest.mark.parametrize(
    ("source_status", "rejected_source_ids", "rejection_reasons"),
    [
        ("rejected", ["source-1"], {"source-1": "deterministic reject"}),
        ("retrieved", ["source-1"], {"source-1": "hard metadata reject"}),
    ],
)
def test_claim_evidence_map_rejected_sources_cannot_support_claims(
    tmp_path,
    source_status: str,
    rejected_source_ids: list[str],
    rejection_reasons: dict[str, str],
) -> None:
    run_id = "run-claim-evidence-rejected-source"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        items=[
            _claim_support_item(
                sentence_id="background-claim-1",
                claim_class="literature_background_claim",
                citation_keys_present=["smith2021"],
                supporting_source_ids=["source-1"],
                support_status="registry_supported",
            )
        ],
        citation_registry=_citation_registry_fixture(
            run_id=run_id,
            source_status=source_status,
            accepted_for_registry=source_status != "rejected",
        ),
        retrieval_quality=_retrieval_quality_fixture(
            run_id=run_id,
            accepted_source_ids=[],
            rejected_source_ids=rejected_source_ids,
            rejection_reasons=rejection_reasons,
        ),
    )

    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)

    link = claim_map.links[0]
    assert link.support_status in {"partially_supported", "unsupported"}
    assert link.support_type == "unsupported"
    assert claim_map.summary_counts["citation_supported_background_claim"] == 0


@pytest.mark.parametrize(
    (
        "proof_type",
        "checker_status",
        "is_verification_evidence",
        "expected_status",
        "expected_type",
    ),
    [
        (
            "lean_verified",
            "passed",
            True,
            "supported_within_scope",
            "formal_proof_verification",
        ),
        (
            "informal_proof_note",
            "not_checked",
            False,
            "partially_supported",
            "informal_proof_context",
        ),
        ("lean_verified", "failed", False, "unsupported", "unsupported"),
    ],
)
def test_claim_evidence_map_links_proof_artifacts_by_authority(
    tmp_path,
    proof_type: str,
    checker_status: str,
    is_verification_evidence: bool,
    expected_status: str,
    expected_type: str,
) -> None:
    run_id = f"run-claim-evidence-proof-{checker_status}"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        items=[
            _claim_support_item(
                sentence_id="proof-claim-1",
                claim_class="proof_claim",
                support_status="forbidden_claim_without_evidence",
            )
        ],
    )
    _write_proof_artifact_report(
        tmp_path,
        run_id=run_id,
        proof_id=f"proof-{checker_status}",
        proof_type=proof_type,
        claim_ids_or_statement_ids=["proof-claim-1"],
        checker_status=checker_status,
        is_verification_evidence=is_verification_evidence,
    )

    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)

    link = claim_map.links[0]
    assert link.support_status == expected_status
    assert link.support_type == expected_type
    if expected_type == "formal_proof_verification":
        assert claim_map.summary_counts["proof_supported_claim"] == 1


@pytest.mark.parametrize(
    ("status", "expected_status"),
    [
        ("completed", "supported_within_scope"),
        ("inconclusive", "unsupported"),
        ("failed", "unsupported"),
    ],
)
def test_claim_evidence_map_links_completed_experiments_only(
    tmp_path,
    status: str,
    expected_status: str,
) -> None:
    run_id = f"run-claim-evidence-experiment-{status}"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        items=[
            _claim_support_item(
                sentence_id="experiment-claim-1",
                claim_class="experiment_claim",
                support_status="forbidden_claim_without_evidence",
            )
        ],
    )
    _write_experiment_artifact_report(
        tmp_path,
        run_id=run_id,
        experiment_id=f"experiment-{status}",
        claim_ids_or_section_ids=["experiment-claim-1"],
        status=status,
    )

    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)

    link = claim_map.links[0]
    assert link.support_status == expected_status
    if status == "completed":
        assert link.support_type == "experiment_result"
        assert claim_map.summary_counts["experiment_supported_claim"] == 1
    else:
        assert link.support_type == "unsupported"


def test_claim_evidence_map_does_not_let_experiment_support_proof_claim(
    tmp_path,
) -> None:
    run_id = "run-claim-evidence-experiment-no-proof"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        items=[
            _claim_support_item(
                sentence_id="proof-claim-1",
                claim_class="proof_claim",
                support_status="forbidden_claim_without_evidence",
            )
        ],
    )
    _write_experiment_artifact_report(
        tmp_path,
        run_id=run_id,
        claim_ids_or_section_ids=["proof-claim-1"],
        status="completed",
    )

    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)

    assert claim_map.links[0].support_status == "unsupported"
    assert claim_map.summary_counts["experiment_supported_claim"] == 0


def test_claim_evidence_map_blocks_proof_for_novelty_or_readiness_claim(
    tmp_path,
) -> None:
    run_id = "run-claim-evidence-proof-no-novelty"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        items=[
            _claim_support_item(
                sentence_id="novelty-claim-1",
                claim_class="novelty_claim",
                support_status="forbidden_claim_without_evidence",
            )
        ],
    )
    _write_proof_artifact_report(
        tmp_path,
        run_id=run_id,
        claim_ids_or_statement_ids=["novelty-claim-1"],
    )

    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)

    link = claim_map.links[0]
    assert link.support_status == "blocked_forbidden_claim"
    assert link.supporting_proof_artifact_ids == []
    assert claim_map.publication_ready is False


def test_autonomous_evidence_plan_is_persisted_and_exposed(tmp_path) -> None:
    run_id = "run-autonomous-plan-persist"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    result = persist_autonomous_evidence_gap_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )

    assert result.persistence.commit.action_type == (
        ControllerActionType.AUTONOMOUS_EVIDENCE_PLAN_WRITTEN
    )
    assert result.plan.planner_backend == "deterministic"
    assert result.plan.planner_status == "planned"
    assert result.plan.plan_items
    assert result.plan.publication_ready is False
    assert result.plan.creates_scientific_validation is False
    assert result.plan.implies_publication_readiness is False
    assert result.plan.is_verification_evidence is False
    _assert_non_evidence_artifact(tmp_path, result.plan_artifact)
    _assert_non_evidence_artifact(tmp_path, result.markdown_artifact)
    inspected = inspect_autonomous_evidence_gap_plan(run_id=run_id, root=tmp_path)
    assert inspected["autonomous_evidence_plan_present"] is True
    assert inspected["autonomous_plan_item_count"] == len(result.plan.plan_items)
    assert inspected["autonomous_human_intervention_required"] is False

    reviewer = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert reviewer["summary_path"].endswith(
        "reviewer-bundle-summary-after-autonomous-evidence-plan-0001.json"
    )
    assert reviewer["autonomous_evidence_plan_present"] is True
    assert reviewer["automation_ready_item_count"] >= 0
    assert reviewer["human_intervention_required"] is False
    assert reviewer["publication_ready"] is False

    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["autonomous_evidence_plan_present"] is True
    assert lint["autonomous_plan_item_count"] == len(result.plan.plan_items)
    assert lint["autonomous_human_intervention_required"] is False
    assert lint["publication_ready"] is False


def test_autonomous_evidence_plan_classifies_claim_gaps(tmp_path) -> None:
    run_id = "run-autonomous-plan-gaps"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        citation_registry=_citation_registry_fixture(run_id=run_id),
        retrieval_quality=_retrieval_quality_fixture(run_id=run_id),
        items=[
            _claim_support_item(
                sentence_id="empirical-claim",
                claim_class="experiment_claim",
                support_status="forbidden_claim_without_evidence",
            ),
            _claim_support_item(
                sentence_id="theorem-claim",
                claim_class="proof_claim",
                support_status="forbidden_claim_without_evidence",
            ),
            _claim_support_item(
                sentence_id="background-claim",
                claim_class="literature_background_claim",
                support_status="missing_required_citation",
            ),
            _claim_support_item(
                sentence_id="novelty-claim",
                claim_class="novelty_claim",
                support_status="forbidden_claim_without_evidence",
            ),
            _claim_support_item(
                sentence_id="supported-background-claim",
                claim_class="literature_background_claim",
                citation_keys_present=["smith2021"],
                supporting_source_ids=["source-1"],
                support_status="registry_supported",
            ),
        ],
    )
    _write_claim_evidence_map_report(
        tmp_path,
        build_claim_evidence_map(run_id=run_id, root=tmp_path),
    )

    plan = build_autonomous_evidence_gap_plan(
        run_id=run_id,
        root=tmp_path,
        backend="deterministic",
    )

    by_claim = {item.target_claim_id_optional: item for item in plan.plan_items}
    assert by_claim["empirical-claim"].gap_type == "needs_python_experiment"
    assert by_claim["theorem-claim"].gap_type == "needs_formal_proof"
    assert by_claim["background-claim"].gap_type == "needs_retrieval_expansion"
    assert by_claim["novelty-claim"].gap_type == "needs_claim_removal"
    assert by_claim["supported-background-claim"].gap_type == (
        "sufficiently_supported_for_bounded_draft"
    )
    assert plan.ready_for_python_experiment_runner is True
    assert plan.ready_for_formal_proof_attempt is True
    assert plan.ready_for_retrieval_expansion is True
    assert plan.requires_human_intervention is False
    assert plan.creates_scientific_validation is False
    assert plan.implies_publication_readiness is False
    assert plan.is_verification_evidence is False


def test_autonomous_evidence_plan_treats_bounded_retrieval_as_nonblocking(
    tmp_path,
) -> None:
    run_id = "run-autonomous-plan-bounded-retrieval"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        citation_registry=_citation_registry_fixture(run_id=run_id),
        retrieval_quality=_retrieval_quality_fixture(
            run_id=run_id,
            accepted_source_ids=["source-1"],
            rejected_source_ids=["source-2"],
            rejection_reasons={"source-2": "deterministic reject"},
        ),
        items=[
            _claim_support_item(
                sentence_id="supported-background-claim",
                claim_class="literature_background_claim",
                citation_keys_present=["smith2021"],
                supporting_source_ids=["source-1"],
                support_status="registry_supported",
            )
        ],
    )
    _write_claim_evidence_map_report(
        tmp_path,
        build_claim_evidence_map(run_id=run_id, root=tmp_path),
    )

    plan = build_autonomous_evidence_gap_plan(
        run_id=run_id,
        root=tmp_path,
        backend="deterministic",
    )

    retrieval_items = [item for item in plan.plan_items if item.target_type == "retrieval"]
    assert retrieval_items
    assert retrieval_items[0].gap_type == "needs_retrieval_expansion"
    assert retrieval_items[0].blocking is False
    assert retrieval_items[0].automation_ready is True
    assert plan.requires_human_intervention is False


def test_autonomous_evidence_plan_requires_human_for_missing_or_corrupt_map(
    tmp_path,
) -> None:
    run_id = "run-autonomous-plan-missing-map"
    (tmp_path / "runs" / run_id / "reports").mkdir(parents=True)

    missing = build_autonomous_evidence_gap_plan(
        run_id=run_id,
        root=tmp_path,
        backend="deterministic",
    )

    assert missing.planner_status == "blocked_missing_claim_evidence_map"
    assert missing.requires_human_intervention is True
    assert missing.plan_items == []

    corrupt_run_id = "run-autonomous-plan-corrupt-map"
    reports = tmp_path / "runs" / corrupt_run_id / "reports"
    reports.mkdir(parents=True)
    (reports / "claim-evidence-map.json").write_text("{not-json}\n", encoding="utf-8")

    corrupt = build_autonomous_evidence_gap_plan(
        run_id=corrupt_run_id,
        root=tmp_path,
        backend="deterministic",
    )

    assert corrupt.planner_status == "blocked_corrupt_claim_evidence_map"
    assert corrupt.requires_human_intervention is True
    assert corrupt.human_intervention_reason_optional


def test_autonomous_plan_executor_dry_run_and_apply_are_bounded(tmp_path) -> None:
    run_id = "run-autonomous-executor"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    persist_autonomous_evidence_gap_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )
    manuscript = tmp_path / "runs" / run_id / "reports" / "revised-manuscript-draft.md"
    claim_map = tmp_path / "runs" / run_id / "reports" / "claim-evidence-map.json"
    manuscript_hash = sha256_file(manuscript)
    claim_map_hash = sha256_file(claim_map)

    dry_run = execute_autonomous_evidence_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="dry-run",
        executor_backend="deterministic",
    )

    assert dry_run.report.execution_status == "dry_run_completed"
    assert dry_run.report.manuscript_modified is False
    assert dry_run.report.claim_evidence_map_rebuilt is False
    assert sha256_file(manuscript) == manuscript_hash
    assert sha256_file(claim_map) == claim_map_hash
    assert not list((tmp_path / "runs" / run_id / "reports").glob("proof-obligation-spec-*.json"))
    assert not list((tmp_path / "runs" / run_id / "reports").glob("experiment-spec-*.json"))

    applied = execute_autonomous_evidence_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        executor_backend="deterministic",
    )
    inspected = inspect_autonomous_plan_execution(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert applied.report.execution_status in {
        "completed",
        "completed_with_deferred_actions",
    }
    assert applied.report.claim_support_rechecked is True
    assert applied.report.citation_safety_rechecked is True
    assert applied.report.claim_evidence_map_rebuilt is True
    assert applied.report.release_rechecked is True
    assert applied.report.publication_ready is False
    assert applied.report.creates_scientific_validation is False
    assert inspected["autonomous_execution_count"] == 2
    assert inspected["latest_autonomous_execution_mode"] == "apply"
    assert lint["autonomous_execution_present"] is True
    assert lint["autonomous_execution_count"] == 2
    assert lint["publication_ready"] is False
    assert not list((tmp_path / "runs" / run_id / "reports").glob("proof-artifact-*.json"))
    assert not list((tmp_path / "runs" / run_id / "reports").glob("experiment-artifact-*.json"))


def test_autonomous_plan_executor_applies_safe_text_and_creates_planned_specs(
    tmp_path,
) -> None:
    run_id = "run-autonomous-executor-actions"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    map_result = persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    reports = tmp_path / "runs" / run_id / "reports"
    audit = ClaimSupportAuditReport.model_validate_json(
        (reports / "claim-support-audit.json").read_text(encoding="utf-8")
    )
    targets = [
        item
        for item in audit.claim_support_items
        if item.section_name not in {"Bibliography", "References"} and item.sentence_snippet
    ][:2]
    assert len(targets) == 2
    plan = AutonomousEvidenceGapPlan(
        run_id=run_id,
        planner_backend="deterministic",
        planner_status="planned",
        claim_evidence_map_path=map_result.map_artifact.path,
        claim_support_audit_path=f"runs/{run_id}/reports/claim-support-audit.json",
        plan_items=[
            AutonomousEvidenceGapPlanItem(
                item_id="plan-item-downgrade",
                target_type="claim",
                target_claim_id_optional=targets[0].sentence_id,
                target_section_optional=targets[0].section_name,
                current_support_status="unsupported",
                gap_type="needs_claim_downgrade",
                recommended_action="Downgrade to bounded scaffold wording.",
                priority="high",
                blocking=True,
                rationale="Fixture unsupported broad claim.",
                expected_artifact_type="revised_manuscript",
                automation_ready=True,
            ),
            AutonomousEvidenceGapPlanItem(
                item_id="plan-item-removal",
                target_type="claim",
                target_claim_id_optional=targets[1].sentence_id,
                target_section_optional=targets[1].section_name,
                current_support_status="blocked_forbidden_claim",
                gap_type="needs_claim_removal",
                recommended_action="Remove forbidden unsupported wording.",
                priority="blocking",
                blocking=True,
                rationale="Fixture forbidden authority claim.",
                expected_artifact_type="revised_manuscript",
                automation_ready=True,
            ),
            AutonomousEvidenceGapPlanItem(
                item_id="plan-item-experiment",
                target_type="claim",
                target_claim_id_optional="fixture-empirical-claim",
                target_section_optional="Demonstration Status",
                current_support_status="unsupported",
                gap_type="needs_python_experiment",
                recommended_action="Plan a bounded local experiment.",
                priority="high",
                blocking=True,
                rationale="Fixture empirical result requires an experiment.",
                expected_artifact_type="experiment_artifact",
                automation_ready=True,
            ),
            AutonomousEvidenceGapPlanItem(
                item_id="plan-item-proof",
                target_type="claim",
                target_claim_id_optional="fixture-theorem-claim",
                target_section_optional="Method and Model",
                current_support_status="unsupported",
                gap_type="needs_formal_proof",
                recommended_action="Plan a scoped formal proof attempt.",
                priority="high",
                blocking=True,
                rationale="Fixture theorem requires a passed checker.",
                expected_artifact_type="proof_artifact",
                automation_ready=True,
            ),
            AutonomousEvidenceGapPlanItem(
                item_id="plan-item-retrieval",
                target_type="retrieval",
                current_support_status="bounded_context_only",
                gap_type="needs_retrieval_expansion",
                recommended_action="Plan bounded retrieval expansion.",
                priority="low",
                blocking=False,
                rationale="Fixture retrieval remains bounded.",
                expected_artifact_type="retrieval_quality_report",
                automation_ready=True,
            ),
        ],
        ready_for_python_experiment_runner=True,
        ready_for_formal_proof_attempt=True,
        ready_for_retrieval_expansion=True,
    )
    (reports / "autonomous-evidence-gap-plan-9999.json").write_text(
        plan.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    result = execute_autonomous_evidence_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        executor_backend="deterministic",
    )

    assert result.report.manuscript_modified is True
    assert result.report.actions_applied == 5
    experiment_specs = [
        path
        for path in reports.glob("experiment-spec-*.json")
        if not path.name.endswith(".meta.json")
    ]
    proof_specs = [
        path
        for path in reports.glob("proof-obligation-spec-*.json")
        if not path.name.endswith(".meta.json")
    ]
    retrieval_specs = [
        path
        for path in reports.glob("retrieval-expansion-request-*.json")
        if not path.name.endswith(".meta.json")
    ]
    assert len(experiment_specs) == len(proof_specs) == len(retrieval_specs) == 1
    experiment_spec = json.loads(experiment_specs[0].read_text(encoding="utf-8"))
    proof_spec = json.loads(proof_specs[0].read_text(encoding="utf-8"))
    retrieval_spec = json.loads(retrieval_specs[0].read_text(encoding="utf-8"))
    assert experiment_spec["status"] == "planned"
    assert experiment_spec["is_verification_evidence"] is False
    assert proof_spec["status"] == "planned"
    assert proof_spec["is_verification_evidence"] is False
    assert retrieval_spec["status"] == "planned"
    assert retrieval_spec["is_verification_evidence"] is False
    assert not list(reports.glob("proof-artifact-*.json"))
    assert not list(reports.glob("experiment-artifact-*.json"))


def test_gap_and_spec_fingerprints_are_stable() -> None:
    item_a = AutonomousEvidenceGapPlanItem(
        item_id="plan-item-001",
        target_type="claim",
        target_claim_id_optional="claim-1",
        target_section_optional="Method and Model",
        current_support_status="unsupported",
        gap_type="needs_formal_proof",
        recommended_action="Schedule a scoped formal proof attempt.",
        priority="high",
        blocking=True,
        rationale="Theorem-like claim needs proof.",
        required_inputs=["claim_id=claim-1", "claim_text_hash=" + "a" * 64],
        expected_artifact_type="proof_artifact",
        automation_ready=True,
    )
    item_b = item_a.model_copy(update={"item_id": "plan-item-999"})
    assert gap_fingerprint_for_plan_item(run_id="run-1", item=item_a) == (
        gap_fingerprint_for_plan_item(run_id="run-1", item=item_b)
    )

    spec_a = ProofObligationSpec(
        run_id="run-1",
        spec_id="proof-obligation-spec-a",
        target_claim_id="claim-1",
        statement="A scoped statement requires proof evidence.",
        suggested_checker="explicitly configured local formal proof backend",
        required_artifact_type="passed scoped proof artifact",
    )
    spec_b = spec_a.model_copy(update={"spec_id": "proof-obligation-spec-b"})
    assert planned_spec_fingerprint(spec_a) == planned_spec_fingerprint(spec_b)


def test_strategy_fingerprint_is_stable() -> None:
    inputs = {
        "gap_fingerprint": "a" * 64,
        "target_claim_id_optional": "claim-1",
        "target_section_optional": "Method and Model",
        "gap_type": "needs_formal_proof",
        "alternative_action": "Split the statement into scoped subclaims.",
        "strategy_family": "proof_decomposition_variant",
        "expected_artifact_type": "proof_artifact",
        "required_inputs": ["proof_plan_only", "target=claim-1"],
    }
    assert strategy_fingerprint(**inputs) == strategy_fingerprint(**inputs)


@pytest.mark.parametrize(
    ("gap_type", "expected_family"),
    [
        ("needs_retrieval_expansion", "retrieval_query_variant"),
        ("needs_formal_proof", "proof_decomposition_variant"),
        ("needs_python_experiment", "experiment_metric_variant"),
        ("needs_claim_removal", "claim_removal_variant"),
    ],
)
def test_exhausted_gaps_get_safe_diversified_strategies(
    tmp_path,
    gap_type: str,
    expected_family: str,
) -> None:
    run_id = f"run-strategy-{gap_type}"
    _write_exhausted_gap_inputs(tmp_path, run_id=run_id, gap_type=gap_type)

    report = build_gap_strategy_diversification(
        run_id=run_id,
        root=tmp_path,
        backend="deterministic",
    )

    assert report.candidate_gap_count == 1
    assert report.selected_strategy_count == 1
    assert any(option.strategy_family == expected_family for option in report.strategy_options)
    assert all(
        "network" not in option.alternative_action.casefold() for option in report.strategy_options
    )
    assert report.publication_ready is False


def test_unsafe_strategy_is_not_automation_ready() -> None:
    common = {
        "strategy_id": "strategy-unsafe",
        "gap_fingerprint": "b" * 64,
        "target_claim_id_optional": "claim-1",
        "target_section_optional": "Demonstration Status",
        "gap_type": "needs_python_experiment",
        "original_recommended_action": "Plan an experiment.",
        "strategy_family": "experiment_dataset_variant",
        "expected_artifact_type": "experiment_artifact",
        "novel_relative_to_previous_attempts": True,
        "automation_ready": True,
        "selected": False,
        "rationale": "Test safety classification.",
        "safety_notes": [],
    }
    network = GapStrategyOption(
        **common,
        alternative_action="Call an external API over the network.",
        strategy_fingerprint="c" * 64,
        required_inputs=["external api"],
    )
    arbitrary_python = GapStrategyOption(
        **common,
        alternative_action="Execute arbitrary Python supplied by the spec.",
        strategy_fingerprint="d" * 64,
        required_inputs=["arbitrary python"],
    )
    assert strategy_is_automation_ready(network) is False
    assert strategy_is_automation_ready(arbitrary_python) is False


def test_strategy_diversification_persists_and_detects_duplicates(tmp_path) -> None:
    run_id = "run-strategy-persistence"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    _write_exhausted_gap_inputs(
        tmp_path,
        run_id=run_id,
        gap_type="needs_formal_proof",
    )
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")

    first = persist_gap_strategy_diversification(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    second = persist_gap_strategy_diversification(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    third = persist_gap_strategy_diversification(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    fourth = persist_gap_strategy_diversification(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    inspected = inspect_gap_strategy_diversification(run_id=run_id, root=tmp_path)
    cli_json = CliRunner().invoke(
        app,
        [
            "inspect-gap-strategy-diversification",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )

    assert first.report.selected_strategy_count == 1
    assert second.report.selected_strategy_count == 1
    assert second.report.duplicate_strategy_count >= 1
    assert third.report.selected_strategy_count == 1
    assert fourth.report.selected_strategy_count == 0
    assert fourth.report.duplicate_strategy_count == fourth.report.strategy_option_count
    assert first.report_artifact.path.endswith("gap-strategy-diversification-0001.json")
    assert first.report_markdown_artifact.path.endswith("gap-strategy-diversification-0001.md")
    assert inspected["strategy_diversification_present"] is True
    assert inspected["duplicate_strategy_count"] >= 1
    assert inspected["publication_ready"] is False
    assert cli_json.exit_code == 0, cli_json.output
    assert json.loads(cli_json.output)["strategy_diversification_present"] is True


def test_autonomous_plan_executor_deduplicates_equivalent_planned_specs(
    tmp_path,
) -> None:
    run_id = "run-autonomous-dedup"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    map_result = persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    reports = tmp_path / "runs" / run_id / "reports"
    plan = AutonomousEvidenceGapPlan(
        run_id=run_id,
        planner_backend="deterministic",
        planner_status="planned",
        claim_evidence_map_path=map_result.map_artifact.path,
        claim_support_audit_path=f"runs/{run_id}/reports/claim-support-audit.json",
        plan_items=[
            AutonomousEvidenceGapPlanItem(
                item_id="plan-item-proof",
                target_type="claim",
                target_claim_id_optional="fixture-theorem-claim",
                target_section_optional="Method and Model",
                current_support_status="unsupported",
                gap_type="needs_formal_proof",
                recommended_action="Plan a scoped formal proof attempt.",
                priority="high",
                blocking=True,
                rationale="Fixture theorem requires a passed checker.",
                expected_artifact_type="proof_artifact",
                automation_ready=True,
            )
        ],
        ready_for_formal_proof_attempt=True,
    )
    (reports / "autonomous-evidence-gap-plan-9999.json").write_text(
        plan.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    first = execute_autonomous_evidence_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        executor_backend="deterministic",
    )
    second = execute_autonomous_evidence_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        executor_backend="deterministic",
    )
    proof_specs = [
        path
        for path in reports.glob("proof-obligation-spec-*.json")
        if not path.name.endswith(".meta.json")
    ]
    history = inspect_gap_attempt_history(run_id=run_id, root=tmp_path)
    dedup = inspect_planned_spec_dedup(run_id=run_id, root=tmp_path)

    assert first.report.actions_applied == 1
    assert second.report.duplicate_specs_skipped == 1
    assert len(proof_specs) == 1
    assert history["gap_attempt_history_present"] is True
    assert history["gap_attempt_count"] >= 1
    assert dedup["planned_spec_dedup_index_present"] is True
    assert dedup["duplicate_planned_spec_count"] >= 1


def test_autonomous_plan_executor_blocks_corrupt_plan_with_human_intervention(
    tmp_path,
) -> None:
    run_id = "run-autonomous-executor-corrupt"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    reports = tmp_path / "runs" / run_id / "reports"
    (reports / "autonomous-evidence-gap-plan-9999.json").write_text(
        "{not-json}\n",
        encoding="utf-8",
    )

    result = execute_autonomous_evidence_plan(
        run_id=run_id,
        root=tmp_path,
        store=ArtifactStore(tmp_path),
        ledger=ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite"),
        execution_mode="dry-run",
        executor_backend="deterministic",
    )

    assert result.report.execution_status == "blocked"
    assert result.report.requires_human_intervention is True
    assert result.report.human_intervention_reason_optional
    assert result.report.publication_ready is False


def test_planned_spec_execution_dry_run_does_not_create_evidence(tmp_path) -> None:
    run_id = "run-planned-spec-dry-run"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    reports = tmp_path / "runs" / run_id / "reports"
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    _write_planned_experiment_spec(reports, run_id=run_id)
    _write_planned_proof_spec(reports, run_id=run_id)
    _write_retrieval_expansion_request(reports, run_id=run_id)
    claim_map = reports / "claim-evidence-map.json"
    claim_map_hash = sha256_file(claim_map)

    result = execute_planned_specs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="dry-run",
        spec_executor_backend="deterministic_local",
    )
    inspected = inspect_planned_spec_execution(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert result.report.execution_status == "dry_run_completed"
    assert result.report.spec_count == 3
    assert result.report.experiment_artifacts_created == 0
    assert result.report.proof_artifacts_created == 0
    assert result.report.claim_evidence_map_rebuilt is False
    assert sha256_file(claim_map) == claim_map_hash
    assert not list(reports.glob("experiment-artifact-*.json"))
    assert not list(reports.glob("proof-artifact-*.json"))
    assert inspected["planned_spec_execution_count"] == 1
    assert lint["planned_spec_execution_present"] is True
    assert lint["latest_planned_spec_execution_mode"] == "dry_run"
    assert lint["publication_ready"] is False


def test_planned_spec_execution_apply_runs_local_templates_and_rechecks(
    tmp_path,
) -> None:
    run_id = "run-planned-spec-apply"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    reports = tmp_path / "runs" / run_id / "reports"
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    _write_planned_experiment_spec(
        reports,
        run_id=run_id,
        spec_id="experiment-spec-fixture-001",
        target_claim_id="experiment-claim-1",
        target_section="Demonstration Status",
    )
    _write_planned_proof_spec(
        reports,
        run_id=run_id,
        spec_id="proof-obligation-spec-plan-001",
        target_claim_id="fixture-theorem-claim",
    )
    _write_retrieval_expansion_request(reports, run_id=run_id)

    result = execute_planned_specs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        spec_executor_backend="deterministic_local",
    )
    inspected = inspect_planned_spec_execution(run_id=run_id, root=tmp_path)
    proof = inspect_proof_artifacts(run_id=run_id, root=tmp_path)
    experiment = inspect_experiment_artifacts(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert result.report.execution_status == "completed"
    assert result.report.experiment_specs_executed == 1
    assert result.report.proof_specs_executed == 1
    assert result.report.retrieval_specs_executed == 1
    assert result.report.experiment_artifacts_created == 1
    assert result.report.proof_artifacts_created == 1
    assert result.report.retrieval_artifacts_created == 1
    assert result.report.claim_evidence_map_rebuilt is True
    assert result.report.autonomous_plan_rebuilt is True
    assert result.report.release_rechecked is True
    assert result.report.publication_ready is False
    assert result.report.creates_scientific_validation is False
    assert inspected["latest_planned_spec_execution_mode"] == "apply"
    assert experiment["completed_experiment_count"] == 1
    assert proof["informal_proof_artifact_count"] == 1
    assert proof["formal_verification_passed_count"] == 0
    assert lint["planned_spec_execution_present"] is True
    assert lint["experiment_artifacts_created"] == 1
    assert lint["proof_artifacts_created"] == 1
    assert lint["retrieval_artifacts_created"] == 1
    assert lint["publication_ready"] is False


def test_planned_spec_execution_skips_equivalent_duplicate_specs(tmp_path) -> None:
    run_id = "run-planned-spec-dedup"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    reports = tmp_path / "runs" / run_id / "reports"
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    _write_planned_proof_spec(
        reports,
        run_id=run_id,
        spec_id="proof-obligation-spec-dedup-a",
    )
    _write_planned_proof_spec(
        reports,
        run_id=run_id,
        spec_id="proof-obligation-spec-dedup-b",
    )

    result = execute_planned_specs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        spec_executor_backend="deterministic_local",
    )
    proof_artifacts_after_first = sorted(reports.glob("proof-artifact-*.json"))
    second = execute_planned_specs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        spec_executor_backend="deterministic_local",
    )
    dedup = inspect_planned_spec_dedup(run_id=run_id, root=tmp_path)
    history = inspect_gap_attempt_history(run_id=run_id, root=tmp_path)

    assert result.report.spec_count == 2
    assert result.report.proof_specs_executed == 1
    assert result.report.duplicate_specs_skipped == 1
    assert result.report.items[1].execution_status == "skipped"
    assert second.report.proof_specs_executed == 0
    assert second.report.unique_specs_executed == 0
    assert second.report.duplicate_specs_skipped == 2
    assert second.report.proof_artifacts_created == 0
    assert sorted(reports.glob("proof-artifact-*.json")) == proof_artifacts_after_first
    assert dedup["duplicate_planned_spec_count"] >= 1
    assert history["gap_attempt_history_present"] is True
    assert history["gap_attempt_count"] >= 1


def test_planned_spec_execution_fixture_formal_proof_is_scoped(
    tmp_path,
) -> None:
    run_id = "run-planned-spec-formal-fixture"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    reports = tmp_path / "runs" / run_id / "reports"
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    _write_planned_proof_spec(
        reports,
        run_id=run_id,
        spec_id="proof-obligation-spec-formal-001",
        target_claim_id=(
            "claim-cand-human-geography-optimal-transport-theory-b-theorem-or-conjecture-form"
        ),
        suggested_checker="deterministic fixture formal proof checker",
        required_artifact_type="deterministic fixture formal verified passed artifact",
    )

    result = execute_planned_specs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        spec_executor_backend="deterministic_local",
    )
    proof = inspect_proof_artifacts(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert result.report.proof_artifacts_created == 1
    assert proof["formal_verification_passed_count"] == 1
    assert lint["proof_artifact_count"] == 1
    assert lint["formal_verification_passed_count"] == 1
    assert lint["publication_ready"] is False


def test_planned_spec_execution_failed_experiment_does_not_create_artifact(
    tmp_path,
) -> None:
    run_id = "run-planned-spec-failed-experiment"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    reports = tmp_path / "runs" / run_id / "reports"
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    _write_planned_experiment_spec(
        reports,
        run_id=run_id,
        spec_id="experiment-spec-force-failed-001",
        hypothesis_or_question="force_failed_experiment",
    )

    result = execute_planned_specs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        spec_executor_backend="deterministic_local",
    )
    experiment = inspect_experiment_artifacts(run_id=run_id, root=tmp_path)

    assert result.report.execution_status == "completed_with_deferred_specs"
    assert result.report.specs_rejected == 1
    assert result.report.experiment_artifacts_created == 0
    assert experiment["experiment_artifact_count"] == 0
    assert result.report.publication_ready is False


def test_python_experiment_sandbox_dry_run_is_non_evidence(tmp_path) -> None:
    run_id = "run-python-sandbox-dry-run"
    spec = _prepare_python_sandbox_fixture(tmp_path, run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    map_path = tmp_path / "runs" / run_id / "reports" / "claim-evidence-map.json"
    before_hash = sha256_file(map_path)

    result = run_python_experiment_sandbox(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_spec=spec,
        sandbox_backend="uv_local",
        execution_mode="dry-run",
    )

    assert result.report.sandbox_status == "dry_run_ready"
    assert result.report.network_disabled is True
    assert result.report.ingested_experiment_artifact_path_optional is None
    assert result.report.claim_evidence_map_rebuilt is False
    assert sha256_file(map_path) == before_hash
    assert not (tmp_path / "runs" / run_id / "experiments" / result.report.sandbox_run_id).exists()
    assert not list((tmp_path / "runs" / run_id / "reports").glob("experiment-artifact-*.json"))


def test_python_experiment_sandbox_apply_executes_uv_and_ingests(tmp_path) -> None:
    run_id = "run-python-sandbox-apply"
    spec = _prepare_python_sandbox_fixture(tmp_path, run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")

    result = run_python_experiment_sandbox(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_spec=spec,
        sandbox_backend="uv_local",
        execution_mode="apply",
    )
    inspected = inspect_python_experiment_sandbox(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    reviewer = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    workdir = tmp_path / result.report.working_directory

    assert result.report.sandbox_status == "completed"
    assert (workdir / "stdout.txt").is_file()
    assert (workdir / "stderr.txt").is_file()
    assert (workdir / "metrics.json").is_file()
    assert (workdir / "artifact-manifest.json").is_file()
    assert (workdir / "pyproject.toml").is_file()
    assert (workdir / "uv.lock").is_file()
    assert result.report.metrics_hash_optional
    assert result.report.output_hash_optional
    assert result.report.ingested_experiment_artifact_path_optional
    assert result.report.claim_evidence_map_rebuilt is True
    assert result.report.release_rechecked is True
    assert result.report.publication_ready is False
    assert inspected["python_experiment_sandbox_completed_count"] == 1
    assert inspected["python_experiment_artifacts_created_count"] == 1
    assert lint["python_experiment_sandbox_present"] is True
    assert lint["python_experiment_sandbox_network_disabled"] is True
    assert reviewer["python_experiment_sandbox_present"] is True
    assert reviewer["latest_python_sandbox_status"] == "completed"
    artifact = ExperimentArtifact.model_validate_json(
        (tmp_path / result.report.created_experiment_artifact_path_optional).read_text()
    )
    assert artifact.status == "completed"
    assert artifact.experiment_type == "synthetic_uv_local"
    assert artifact.implies_publication_readiness is False
    assert artifact.is_verification_evidence is False


def test_python_experiment_sandbox_rejects_network_and_unapproved_dependencies(
    tmp_path,
) -> None:
    run_id = "run-python-sandbox-policy"
    spec = _prepare_python_sandbox_fixture(tmp_path, run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")

    with pytest.raises(PythonExperimentSandboxError, match="network access"):
        run_python_experiment_sandbox(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            experiment_spec=spec.model_copy(update={"allow_network": True}),
            execution_mode="dry-run",
        )
    with pytest.raises(PythonExperimentSandboxError, match="outside the allowlist"):
        run_python_experiment_sandbox(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            experiment_spec=spec.model_copy(
                update={"requested_dependencies": ["unapproved-package"]}
            ),
            execution_mode="dry-run",
        )


def test_planned_spec_execution_uses_explicit_uv_sandbox_backend(tmp_path) -> None:
    run_id = "run-planned-python-sandbox"
    spec = _prepare_python_sandbox_fixture(tmp_path, run_id)
    reports = tmp_path / "runs" / run_id / "reports"
    (reports / "experiment-spec-synthetic-calibration.json").write_text(
        spec.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    result = execute_planned_specs(
        run_id=run_id,
        root=tmp_path,
        store=ArtifactStore(tmp_path),
        ledger=ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite"),
        execution_mode="apply",
        spec_executor_backend="deterministic_local",
        python_sandbox_backend="uv_local",
    )

    assert result.report.experiment_specs_executed == 1
    assert result.report.experiment_artifacts_created == 1
    assert result.report.items[0].execution_status == "executed"
    assert result.report.items[0].ingested_artifact_path_optional
    assert result.report.publication_ready is False
    assert (
        inspect_python_experiment_sandbox(
            run_id=run_id,
            root=tmp_path,
        )["latest_python_sandbox_status"]
        == "completed"
    )


def test_experiment_template_registry_loads_approved_local_template(tmp_path) -> None:
    run_id = "run-experiment-template-registry"
    _copy_default_experiment_template_bundle(tmp_path)

    registry = build_default_experiment_template_registry(run_id=run_id, root=tmp_path)

    assert registry.templates
    template = registry.templates[0]
    assert template.template_family == "synthetic_calibration"
    assert template.network_required is False
    assert template.arbitrary_code_required is False
    assert template.creates_scientific_validation is False
    assert registry.network_required_template_count == 0


def test_route_experiment_gaps_creates_sandbox_compatible_spec(tmp_path) -> None:
    run_id = "run-experiment-gap-routing"
    _prepare_experiment_routing_fixture(
        tmp_path,
        run_id=run_id,
        claim_class="experiment_claim",
        support_scope="bounded experiment result needs a local synthetic check",
    )
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")

    result = route_experiment_gaps(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        routing_backend="deterministic",
    )
    inspected = inspect_experiment_gap_routing(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    reviewer = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)

    assert result.report.routing_status == "routed"
    assert result.report.routed_gap_count == 1
    assert result.report.created_experiment_spec_count == 1
    assert result.report.publication_ready is False
    assert result.report.creates_scientific_validation is False
    assert result.report.items[0].routing_status == "routed"
    spec_path = tmp_path / result.report.items[0].created_experiment_spec_path_optional
    spec = PlannedExperimentSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    assert spec.sandbox_backend == "uv_local"
    assert spec.template_id_optional == "synthetic_calibration_v1"
    assert spec.experiment_bundle_path_optional
    assert spec.allow_network is False
    assert inspected["experiment_gap_routing_present"] is True
    assert lint["experiment_gap_routing_present"] is True
    assert lint["routed_experiment_gap_count"] == 1
    assert lint["created_experiment_spec_count"] == 1
    assert reviewer["experiment_gap_routing_present"] is True
    assert reviewer["publication_ready"] is False


def test_bounded_empirical_demonstration_gap_requires_experiment_support(
    tmp_path,
) -> None:
    run_id = "run-bounded-empirical-gap"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")

    map_result = persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        enable_empirical_demonstration_gaps=True,
    )
    plan_result = persist_autonomous_evidence_gap_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )

    by_id = {link.claim_id: link for link in map_result.claim_evidence_map.links}
    link = by_id[BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID]
    assert link.claim_class == "bounded_demonstration_claim"
    assert link.requires_support is True
    assert link.support_status == "unsupported"
    assert link.support_type == "unsupported"
    assert map_result.claim_evidence_map.publication_ready is False
    assert plan_result.plan.empirical_demonstration_gap_count == 1
    assert plan_result.plan.needs_python_experiment_count >= 1
    plan_item = next(
        item
        for item in plan_result.plan.plan_items
        if item.target_claim_id_optional == BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID
    )
    assert plan_item.gap_type == "needs_python_experiment"
    assert plan_item.automation_ready is True


def test_bounded_empirical_gap_routes_to_synthetic_template(tmp_path) -> None:
    run_id = "run-bounded-empirical-routing"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    _copy_default_experiment_template_bundle(tmp_path)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        enable_empirical_demonstration_gaps=True,
    )
    persist_autonomous_evidence_gap_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )

    result = route_experiment_gaps(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        routing_backend="deterministic",
    )
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert result.report.bounded_empirical_gaps_routed == 1
    assert result.report.synthetic_template_specs_created == 1
    assert result.report.created_experiment_spec_count == 1
    spec_path = tmp_path / result.report.items[0].created_experiment_spec_path_optional
    spec = PlannedExperimentSpec.model_validate_json(spec_path.read_text(encoding="utf-8"))
    assert spec.target_claim_id == BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID
    assert spec.template_id_optional == "synthetic_calibration_v1"
    assert spec.sandbox_backend == "uv_local"
    assert spec.allow_network is False
    assert lint["bounded_empirical_gap_count"] == 1
    assert lint["needs_python_experiment_count"] >= 1
    assert lint["routed_empirical_gap_count"] == 1


def test_routed_experiment_spec_executes_through_uv_sandbox(tmp_path) -> None:
    run_id = "run-routed-experiment-spec-sandbox"
    _prepare_experiment_routing_fixture(
        tmp_path,
        run_id=run_id,
        claim_class="experiment_claim",
        support_scope="bounded experiment result needs a local synthetic check",
    )
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    route_experiment_gaps(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        routing_backend="deterministic",
    )

    result = execute_planned_specs(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        execution_mode="apply",
        spec_executor_backend="deterministic_local",
        python_sandbox_backend="uv_local",
        max_sandbox_runs=1,
    )
    sandbox = inspect_python_experiment_sandbox(run_id=run_id, root=tmp_path)

    assert result.report.experiment_specs_executed == 1
    assert result.report.experiment_artifacts_created == 1
    assert result.report.items[0].ingested_artifact_path_optional
    assert sandbox["python_experiment_sandbox_present"] is True
    assert sandbox["python_experiment_sandbox_completed_count"] == 1
    assert sandbox["python_experiment_artifacts_created_count"] == 1
    assert result.report.publication_ready is False


def test_route_experiment_gaps_does_not_route_proof_background_or_forbidden_claims(
    tmp_path,
) -> None:
    cases = [
        (
            "proof",
            "proof_claim",
            "A theorem proof claim needs formal proof support.",
            "needs_formal_proof",
        ),
        (
            "background",
            "literature_background_claim",
            "A background literature source claim needs accepted retrieval context.",
            "needs_retrieval_expansion",
        ),
        (
            "forbidden",
            "publication_readiness_claim",
            "The claim asks for publication readiness validation.",
            "needs_claim_removal",
        ),
    ]
    for suffix, claim_class, support_scope, expected_gap_type in cases:
        run_id = f"run-experiment-gap-routing-{suffix}"
        _prepare_experiment_routing_fixture(
            tmp_path,
            run_id=run_id,
            claim_class=claim_class,
            support_scope=support_scope,
        )
        result = route_experiment_gaps(
            run_id=run_id,
            root=tmp_path,
            store=ArtifactStore(tmp_path),
            ledger=ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite"),
            routing_backend="deterministic",
        )
        plan = AutonomousEvidenceGapPlan.model_validate_json(
            (
                tmp_path / "runs" / run_id / "reports" / "autonomous-evidence-gap-plan.json"
            ).read_text(encoding="utf-8")
        )

        assert plan.plan_items[0].gap_type == expected_gap_type
        assert result.report.routed_gap_count == 0
        assert result.report.created_experiment_spec_count == 0
        assert result.report.gap_count == 0
        assert result.report.publication_ready is False


def test_experiment_gap_routing_defers_when_sandbox_budget_exhausted(
    tmp_path,
) -> None:
    run_id = "run-experiment-gap-routing-budget"
    _prepare_experiment_routing_fixture(
        tmp_path,
        run_id=run_id,
        claim_class="experiment_claim",
        support_scope="bounded experiment result needs local synthetic execution",
    )

    result = route_experiment_gaps(
        run_id=run_id,
        root=tmp_path,
        store=ArtifactStore(tmp_path),
        ledger=ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite"),
        routing_backend="deterministic",
        sandbox_budget_remaining=0,
    )

    assert result.report.routed_gap_count == 0
    assert result.report.created_experiment_spec_count == 0
    assert result.report.items[0].routing_status == "deferred_budget_exhausted"
    assert result.report.publication_ready is False


def test_experiment_gap_routing_cli_roundtrip(tmp_path) -> None:
    run_id = "run-experiment-gap-routing-cli"
    _prepare_experiment_routing_fixture(
        tmp_path,
        run_id=run_id,
        claim_class="experiment_claim",
        support_scope="bounded experiment result needs a routed local template",
    )
    runner = CliRunner()

    route = runner.invoke(
        app,
        [
            "route-experiment-gaps",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--routing-backend",
            "deterministic",
            "--json",
        ],
    )
    inspect_json = runner.invoke(
        app,
        [
            "inspect-experiment-gap-routing",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )

    assert route.exit_code == 0, route.output
    assert inspect_json.exit_code == 0, inspect_json.output
    payload = json.loads(route.output)
    inspected = json.loads(inspect_json.output)
    assert payload["experiment_gap_routing_present"] is True
    assert payload["routed_experiment_gap_count"] == 1
    assert inspected["experiment_gap_routing_present"] is True
    assert inspected["created_experiment_spec_count"] == 1


def test_autonomous_loop_empirical_gap_runs_uv_sandbox(tmp_path) -> None:
    run_id = "run-autonomous-loop-empirical-gap"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    _copy_default_experiment_template_bundle(tmp_path)

    result = run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=ArtifactStore(tmp_path),
        ledger=ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite"),
        loop_backend="deterministic",
        max_iterations=6,
        max_attempts_per_gap=1,
        enable_strategy_diversification=True,
        enable_experiment_routing=True,
        enable_empirical_demonstration_gaps=True,
        python_sandbox_backend="uv_local",
        max_sandbox_runs_per_loop=1,
        max_sandbox_runs_per_iteration=1,
    )
    sandbox = inspect_python_experiment_sandbox(run_id=run_id, root=tmp_path)
    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    by_id = {link.claim_id: link for link in claim_map.links}
    empirical_link = by_id[BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID]

    assert result.report.empirical_gaps_created >= 1
    assert result.report.empirical_gaps_routed >= 1
    assert result.report.sandbox_experiments_completed >= 1
    assert result.report.experiment_artifacts_ingested >= 1
    assert result.report.terminal_state in {
        "completed_with_deferred_gaps",
        "completed_all_supported",
    }
    assert result.report.stopped_before_max_iterations is True
    assert result.report.automation_ready_after_history_count == 0
    assert result.report.empirical_paths_resolved >= 1
    assert result.report.sandbox_budget_exhausted is True
    assert sandbox["python_experiment_sandbox_completed_count"] >= 1
    assert sandbox["python_experiment_artifacts_created_count"] >= 1
    assert empirical_link.support_status == "supported_within_scope"
    assert empirical_link.support_type == "experiment_result"
    assert empirical_link.classification == "experiment_supported_claim"
    assert lint["bounded_empirical_gap_count"] >= 1
    assert lint["routed_empirical_gap_count"] >= 1
    assert lint["sandbox_experiment_completed_count"] >= 1
    assert lint["experiment_artifacts_ingested_count"] >= 1
    assert lint["publication_ready"] is False
    assert lint["claim_support_missing_required_citation_count"] == 0
    assert lint["citation_as_validation_misuse_count"] == 0


def test_autonomous_loop_blocks_unsupported_empirical_gap_when_budget_exhausted() -> None:
    classification = AutonomousLoopGapTerminalClassification(
        gap_fingerprint="a" * 64,
        target_claim_id_optional="bounded-empirical-claim",
        gap_type="needs_python_experiment",
        terminal_class="deferred_budget_exhausted",
        blocking=True,
        automation_ready_after_history=False,
        reason="The loop sandbox budget is exhausted.",
    )
    terminal = _TerminalSummary(
        classifications=(classification,),
        resolved_gap_count=0,
        deferred_gap_count=1,
        exhausted_gap_count=0,
        duplicate_only_gap_count=0,
        blocking_gap_count=1,
        automation_ready_after_history_count=0,
        empirical_paths_resolved=0,
        proof_paths_deferred=0,
        retrieval_paths_deferred=0,
    )
    snapshot = _ProgressSnapshot(
        supported=0,
        unsupported=1,
        partial=0,
        automation_ready=1,
        actions_applied=0,
        created_spec_count=0,
        experiment_artifacts_created=0,
        proof_artifacts_created=0,
        retrieval_artifacts_created=0,
        duplicate_specs_skipped=0,
        exhausted_gap_count=0,
        deferred_gap_count=1,
        selected_strategy_count=0,
        routed_experiment_spec_count=0,
        manuscript_before="a" * 64,
        manuscript_after="a" * 64,
    )

    decision = _decide_iteration(
        snapshot=snapshot,
        previous_snapshot=None,
        terminal=terminal,
        iteration_number=1,
        max_iterations=4,
        no_progress_streak=1,
    )

    assert decision.terminal_state == "stopped_no_progress"
    assert decision.blocking_gap_count == 1
    assert decision.automation_ready_after_history_count == 0
    assert decision.publication_ready is False


def test_autonomous_loop_terminal_state_is_completed_all_supported() -> None:
    terminal = _TerminalSummary(
        classifications=(),
        resolved_gap_count=3,
        deferred_gap_count=0,
        exhausted_gap_count=0,
        duplicate_only_gap_count=0,
        blocking_gap_count=0,
        automation_ready_after_history_count=0,
        empirical_paths_resolved=1,
        proof_paths_deferred=0,
        retrieval_paths_deferred=0,
    )
    snapshot = _ProgressSnapshot(
        supported=3,
        unsupported=0,
        partial=0,
        automation_ready=0,
        actions_applied=0,
        created_spec_count=0,
        experiment_artifacts_created=0,
        proof_artifacts_created=0,
        retrieval_artifacts_created=0,
        duplicate_specs_skipped=0,
        exhausted_gap_count=0,
        deferred_gap_count=0,
        selected_strategy_count=0,
        routed_experiment_spec_count=0,
        manuscript_before="a" * 64,
        manuscript_after="a" * 64,
    )

    decision = _decide_iteration(
        snapshot=snapshot,
        previous_snapshot=None,
        terminal=terminal,
        iteration_number=1,
        max_iterations=4,
        no_progress_streak=0,
    )

    assert decision.terminal_state == "completed_all_supported"
    assert decision.loop_status == "completed"
    assert decision.publication_ready is False


def test_planned_spec_execution_defers_uv_sandbox_when_budget_exhausted(
    tmp_path,
) -> None:
    run_id = "run-planned-python-sandbox-budget"
    spec = _prepare_python_sandbox_fixture(tmp_path, run_id)
    reports = tmp_path / "runs" / run_id / "reports"
    (reports / "experiment-spec-synthetic-calibration.json").write_text(
        spec.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )

    result = execute_planned_specs(
        run_id=run_id,
        root=tmp_path,
        store=ArtifactStore(tmp_path),
        ledger=ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite"),
        execution_mode="apply",
        spec_executor_backend="deterministic_local",
        python_sandbox_backend="uv_local",
        max_sandbox_runs=0,
    )

    assert result.report.execution_status == "completed_with_deferred_specs"
    assert result.report.experiment_specs_executed == 0
    assert result.report.experiment_artifacts_created == 0
    assert result.report.specs_deferred == 1
    assert result.report.items[0].execution_status == "deferred"
    assert "budget exhausted" in (result.report.items[0].deferred_reason_optional or "")
    with pytest.raises(PythonExperimentSandboxError):
        inspect_python_experiment_sandbox(run_id=run_id, root=tmp_path)
    assert result.report.publication_ready is False


def test_autonomous_loop_routes_experiment_gaps_and_reports_budget(tmp_path) -> None:
    run_id = "run-autonomous-loop-experiment-routing"
    _prepare_experiment_routing_fixture(
        tmp_path,
        run_id=run_id,
        claim_class="experiment_claim",
        support_scope="bounded demonstration result needs a routed synthetic template",
    )
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")

    result = run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        loop_backend="deterministic",
        max_iterations=2,
        max_attempts_per_gap=1,
        enable_experiment_routing=True,
        python_sandbox_backend="uv_local",
        max_sandbox_runs_per_loop=1,
        max_sandbox_runs_per_iteration=1,
    )
    routing = inspect_experiment_gap_routing(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    reviewer = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)

    assert result.report.experiment_routing_enabled is True
    assert result.report.routed_experiment_gap_count >= 1
    assert result.report.routed_experiment_spec_count >= 1
    assert result.report.sandbox_budget_runs_used <= 1
    assert result.report.publication_ready is False
    assert routing["experiment_gap_routing_present"] is True
    assert lint["experiment_gap_routing_present"] is True
    assert lint["routed_experiment_gap_count"] >= 1
    assert lint["sandbox_budget_runs_used"] <= 1
    assert lint["publication_ready"] is False
    assert reviewer["experiment_gap_routing_present"] is True
    assert reviewer["publication_ready"] is False


def test_autonomous_loop_runs_plan_specs_and_updates_bundle_views(tmp_path) -> None:
    run_id = "run-autonomous-loop"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")

    result = run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        loop_backend="deterministic",
        max_iterations=2,
    )
    inspected = inspect_autonomous_loop(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    reviewer = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    reports = tmp_path / "runs" / run_id / "reports"

    assert result.persistence.commit.action_type == ControllerActionType.AUTONOMOUS_LOOP_WRITTEN
    assert result.report.iterations_completed >= 1
    assert result.report.loop_status in {
        "completed",
        "completed_with_deferred_gaps",
        "stopped_no_progress",
        "stopped_max_iterations",
    }
    assert result.report.publication_ready is False
    assert result.report.creates_scientific_validation is False
    assert result.report.implies_publication_readiness is False
    assert result.report.is_verification_evidence is False
    assert result.report.requires_human_intervention is False
    assert result.report.iterations[0].claim_evidence_map_path
    assert result.report.iterations[0].autonomous_plan_path
    assert result.report.iterations[0].autonomous_execution_report_path
    assert result.report.iterations[0].planned_spec_execution_report_path
    assert result.report.iterations[0].release_report_path
    assert (reports / "autonomous-loop-0001.json").is_file()
    assert (reports / "autonomous-loop-index-0001.json").is_file()
    assert (reports / "autonomous-loop-iteration-0001-001.json").is_file()
    assert inspected["autonomous_loop_present"] is True
    assert inspected["autonomous_loop_count"] == 1
    assert lint["autonomous_loop_present"] is True
    assert lint["autonomous_loop_count"] == 1
    assert lint["latest_autonomous_loop_iterations_completed"] >= 1
    assert lint["autonomous_loop_requires_human_intervention"] is False
    assert lint["publication_ready"] is False
    assert reviewer["autonomous_loop_present"] is True
    assert reviewer["latest_autonomous_loop_status"] == result.report.loop_status
    assert reviewer["publication_ready"] is False


def test_autonomous_loop_stops_before_max_iterations_for_exhausted_gaps(tmp_path) -> None:
    run_id = "run-autonomous-loop-dedup"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")

    result = run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        loop_backend="deterministic",
        max_iterations=5,
        max_attempts_per_gap=1,
    )
    inspected = inspect_autonomous_loop(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    history = inspect_gap_attempt_history(run_id=run_id, root=tmp_path)
    dedup = inspect_planned_spec_dedup(run_id=run_id, root=tmp_path)

    assert result.report.iterations_completed < 5
    assert result.report.loop_status in {
        "completed_with_deferred_gaps",
        "stopped_no_progress",
        "completed",
    }
    assert result.report.stop_reason != "max_iterations_reached"
    assert result.report.terminal_state in {
        "completed_with_deferred_gaps",
        "completed_with_exhausted_noncritical_gaps",
        "completed_all_supported",
        "stopped_no_progress",
    }
    assert result.report.stopped_before_max_iterations is True
    assert result.report.automation_ready_after_history_count == 0
    assert result.report.publication_ready is False
    assert inspected["gap_exhausted_no_progress_count"] >= 0
    assert lint["gap_attempt_history_present"] is True
    assert lint["planned_spec_dedup_index_present"] is True
    assert lint["latest_autonomous_loop_stop_reason"] != "max_iterations_reached"
    assert lint["autonomous_loop_terminal_state"] == result.report.terminal_state
    assert lint["autonomous_loop_stopped_before_max_iterations"] is True
    assert lint["autonomous_loop_automation_ready_after_history_count"] == 0
    assert history["gap_attempt_history_present"] is True
    assert dedup["planned_spec_dedup_index_present"] is True


def test_autonomous_loop_diversifies_before_final_deferral(tmp_path) -> None:
    run_id = "run-autonomous-loop-strategy-diversification"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")

    result = run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        loop_backend="deterministic",
        max_iterations=6,
        max_attempts_per_gap=1,
        enable_strategy_diversification=True,
    )
    strategy = inspect_gap_strategy_diversification(run_id=run_id, root=tmp_path)
    history = inspect_gap_attempt_history(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    reviewer = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)

    assert result.report.strategy_diversification_enabled is True
    assert result.report.strategy_option_count >= 1
    assert result.report.selected_strategy_count >= 1
    assert result.report.iterations_completed <= 6
    assert result.report.stop_reason != "max_iterations_reached"
    assert result.report.terminal_state == "completed_with_deferred_gaps"
    assert result.report.stopped_before_max_iterations is True
    assert result.report.automation_ready_after_history_count == 0
    assert result.report.proof_paths_deferred + result.report.retrieval_paths_deferred >= 1
    assert strategy["strategy_diversification_present"] is True
    assert strategy["strategy_option_count"] >= 1
    assert history["strategy_attempt_count"] >= 1
    assert any(
        record["current_gap_status"]
        in {
            "exhausted_initial_strategy",
            "exhausted_all_strategies",
            "deferred_after_diversification",
            "resolved",
        }
        for record in history["records"]
    )
    assert lint["strategy_diversification_present"] is True
    assert lint["selected_strategy_count"] >= 0
    assert lint["autonomous_loop_terminal_state"] == "completed_with_deferred_gaps"
    assert lint["autonomous_loop_stopped_before_max_iterations"] is True
    assert lint["autonomous_loop_automation_ready_after_history_count"] == 0
    assert lint["publication_ready"] is False
    assert reviewer["strategy_diversification_present"] is True
    assert reviewer["autonomous_loop_terminal_state"] == "completed_with_deferred_gaps"
    assert reviewer["publication_ready"] is False


def test_capability_escalation_defaults_fail_closed_and_reports_counts(tmp_path) -> None:
    run_id = "run-capability-escalation"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    loop = run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        loop_backend="deterministic",
        max_iterations=5,
        max_attempts_per_gap=1,
        enable_strategy_diversification=True,
    )

    result = escalate_capabilities(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        allow_network=False,
        allow_external_proof_tools=False,
        allow_external_retrieval_tools=False,
    )
    summary = inspect_capability_escalation(run_id=run_id, root=tmp_path)
    cli_summary = CliRunner().invoke(
        app,
        [
            "inspect-capability-escalation",
            "--root",
            str(tmp_path),
            "--run-id",
            run_id,
            "--json",
        ],
    )
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    reviewer = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    proof_artifacts = inspect_proof_artifacts(run_id=run_id, root=tmp_path)

    assert loop.report.terminal_state == "completed_with_deferred_gaps"
    assert (
        result.persistence.commit.action_type == ControllerActionType.CAPABILITY_ESCALATION_WRITTEN
    )
    assert result.report.candidate_deferred_gap_count >= 1
    assert (
        result.report.proof_escalation_attempt_count
        + result.report.retrieval_escalation_attempt_count
        >= 1
    )
    assert result.report.network_allowed is False
    assert result.report.external_tools_allowed is False
    assert result.report.publication_ready is False
    assert result.policy.allow_network is False
    assert result.policy.allow_external_proof_tools is False
    assert result.policy.allow_external_retrieval_tools is False
    assert result.report.deferred_after_escalation_count >= 1
    assert summary["capability_escalation_present"] is True
    assert summary["capability_escalation_network_allowed"] is False
    assert summary["capability_escalation_external_tools_allowed"] is False
    assert cli_summary.exit_code == 0, cli_summary.output
    assert json.loads(cli_summary.output)["capability_escalation_present"] is True
    assert proof_artifacts["formal_verification_passed_count"] == 0
    assert proof_artifacts["informal_proof_artifact_count"] >= 1
    assert lint["capability_escalation_present"] is True
    assert lint["capability_escalation_network_allowed"] is False
    assert lint["capability_escalation_external_tools_allowed"] is False
    assert lint["claim_support_missing_required_citation_count"] == 0
    assert lint["citation_as_validation_misuse_count"] == 0
    assert lint["publication_ready"] is False
    assert reviewer["capability_escalation_present"] is True
    assert reviewer["publication_ready"] is False


def test_capability_escalation_retrieval_expansion_filters_local_sources(
    tmp_path,
) -> None:
    run_id = "run-capability-escalation-retrieval"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        loop_backend="deterministic",
        max_iterations=5,
        max_attempts_per_gap=1,
        enable_strategy_diversification=True,
    )
    _write_exhausted_gap_inputs(
        tmp_path,
        run_id=run_id,
        gap_type="needs_retrieval_expansion",
    )

    result = escalate_capabilities(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        allow_network=False,
        allow_external_proof_tools=False,
        allow_external_retrieval_tools=False,
    )
    retrieval_items = [
        item for item in result.report.items if item.gap_type == "needs_retrieval_expansion"
    ]
    assert retrieval_items
    assert all(item.selected_backend == "local_source_pack_expansion" for item in retrieval_items)
    quality_paths = [
        tmp_path / path
        for path in result.report.created_artifact_paths
        if "retrieval-quality" in path
    ]
    assert quality_paths
    quality = RetrievalQualityReport.model_validate_json(
        quality_paths[0].read_text(encoding="utf-8")
    )
    registry_paths = [
        tmp_path / path
        for path in result.report.created_artifact_paths
        if "retrieval-citation-registry" in path
    ]
    registry = CitationRegistry.model_validate_json(registry_paths[0].read_text(encoding="utf-8"))
    assert quality.accepted_source_count >= 1
    assert quality.hard_reject_count >= 1
    assert all(record.accepted_for_registry for record in registry.citations)
    assert len(registry.citations) == quality.accepted_source_count
    assert result.report.publication_ready is False


def test_autonomous_loop_integrates_capability_escalation(tmp_path) -> None:
    run_id = "run-autonomous-loop-capability-escalation"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)

    result = run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=ArtifactStore(tmp_path),
        ledger=ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite"),
        loop_backend="deterministic",
        max_iterations=6,
        max_attempts_per_gap=1,
        enable_strategy_diversification=True,
        enable_capability_escalation=True,
    )
    inspected = inspect_autonomous_loop(run_id=run_id, root=tmp_path)
    escalation = inspect_capability_escalation(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)

    assert result.report.capability_escalation_enabled is True
    assert result.report.capability_escalation_status in {
        "completed",
        "completed_with_deferred_gaps",
        "no_candidate_deferred_gaps",
    }
    assert (
        result.report.proof_escalation_attempt_count
        + result.report.retrieval_escalation_attempt_count
        >= 1
    )
    assert result.report.capability_escalation_network_allowed is False
    assert result.report.capability_escalation_external_tools_allowed is False
    assert result.report.publication_ready is False
    assert inspected["capability_escalation_enabled"] is True
    assert escalation["capability_escalation_present"] is True
    assert lint["capability_escalation_present"] is True
    assert lint["capability_escalation_network_allowed"] is False
    assert lint["capability_escalation_external_tools_allowed"] is False
    assert lint["publication_ready"] is False
    assert lint["claim_support_missing_required_citation_count"] == 0
    assert lint["citation_as_validation_misuse_count"] == 0


def test_final_manuscript_regeneration_is_scoped_safe_and_preferred(tmp_path) -> None:
    run_id = "run-final-manuscript-regeneration"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    retrieval_quality = RetrievalQualityReport(
        run_id=run_id,
        retrieval_backend="local",
        total_retrieved_sources=2,
        accepted_source_count=1,
        rejected_source_count=1,
        accepted_source_ids=["fixture-smith"],
        rejected_source_ids=["fixture-rejected"],
        adequacy_status="bounded_context_only",
        coverage_limitations=["Local fixture coverage is bounded context only."],
    )
    reports = tmp_path / "runs" / run_id / "reports"
    (reports / "llm-orchestration-config.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "domain": "spatial heterogeneity in human geography",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (reports / "retrieval-quality-report.json").write_text(
        retrieval_quality.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id=run_id,
        claim_ids_or_section_ids=[
            BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID,
            "bounded-empirical-demonstration",
        ],
        experiment_type="substrate_distance_decay_uv_local",
        metrics={
            "test_mae_baseline": 2.69526987,
            "test_mae_method": 0.06396946,
            "test_rmse_baseline": 9.07274655,
            "test_rmse_method": 0.17413053,
            "mae_improvement": 2.63130041,
            "rmse_improvement": 8.89861602,
            "claim_support_satisfied": True,
            "heterogeneity_ablation_present": True,
            "comparison_table": [
                {
                    "setting": "low_heterogeneity",
                    "baseline_mae": 0.25385627,
                    "method_mae": 0.05204437,
                    "baseline_rmse": 0.40625991,
                    "method_rmse": 0.09447819,
                    "mae_improvement": 0.2018119,
                    "rmse_improvement": 0.31178172,
                },
                {
                    "setting": "high_heterogeneity",
                    "baseline_mae": 2.69526987,
                    "method_mae": 0.06396946,
                    "baseline_rmse": 9.07274655,
                    "method_rmse": 0.17413053,
                    "mae_improvement": 2.63130041,
                    "rmse_improvement": 8.89861602,
                },
            ],
        },
    )
    ingest_experiment_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_file=experiment_file,
    )
    run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        loop_backend="deterministic",
        max_iterations=3,
        max_attempts_per_gap=1,
        enable_strategy_diversification=True,
    )
    substrate_result = build_scientific_substrate(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        max_substrates=2,
    )
    substrate_inspection = inspect_scientific_substrate(run_id=run_id, root=tmp_path)

    result = regenerate_final_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )
    inspected = inspect_final_manuscript(run_id=run_id, root=tmp_path)
    idea_tree = inspect_idea_tree(run_id=run_id, root=tmp_path)
    bundle = inspect_paper_bundle_summary(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    markdown = result.manuscript_markdown

    assert (
        result.persistence.commit.action_type == ControllerActionType.FINAL_MANUSCRIPT_REGENERATED
    )
    assert result.report.regeneration_status == "completed"
    assert result.report.sections_generated == 11
    assert result.report.unsupported_claim_count == 0
    assert result.report.publication_ready is False
    assert result.structured_manuscript.publication_ready is False
    assert idea_tree.final_node_id_optional is not None
    final_idea = next(
        node for node in idea_tree.nodes if node.node_id == idea_tree.final_node_id_optional
    )
    assert final_idea.status == "final"
    assert final_idea.selected_for_final_manuscript is True
    assert any(
        node.selected_for_final_manuscript and node.status == "selected" for node in idea_tree.nodes
    )
    assert (tmp_path / result.report.final_manuscript_path).is_file()
    assert (tmp_path / result.report.final_manuscript_structured_path).is_file()
    report_markdown = (
        tmp_path / "runs" / run_id / "reports" / "final-manuscript-regeneration-0001.md"
    )
    assert report_markdown.is_file()
    title = markdown.splitlines()[0]
    assert "Region-Specific Distance Decay" in title
    assert "manuscript generation" not in title.casefold()
    assert "claim-evidence" not in title.casefold()
    abstract = markdown.split("## Abstract", maxsplit=1)[1].split("## Introduction", maxsplit=1)[0]
    assert "spatial heterogeneity in human geography" in abstract.casefold()
    assert "F_ij = A_i B_j d_ij^{-alpha_i} exp(epsilon_ij)" in abstract
    assert "pooled-alpha gravity baseline" in abstract
    assert "claim-evidence map" not in abstract.casefold()
    assert "## Abstract" in markdown
    assert "## Introduction" in markdown
    assert "## Related Work and Source Boundaries" in markdown
    assert "## Research Question / Hypothesis" in markdown
    assert "## Method or Model" in markdown
    assert "## Bounded Empirical Demonstration" in markdown
    assert "## Results Within Scope" in markdown
    assert "## Limitations and Deferred Evidence" in markdown
    assert "## Conclusion" in markdown
    assert "## Appendix A: Claim-Evidence Map" in markdown
    assert "## Appendix B: Autonomous Execution and Provenance" in markdown
    main_body = markdown.split("## Appendix A: Claim-Evidence Map", maxsplit=1)[0]
    appendices = markdown.split("## Appendix A: Claim-Evidence Map", maxsplit=1)[1]
    assert "claim-evidence map" not in main_body.casefold()
    assert "autonomous loop" not in main_body.casefold()
    assert "publication_ready=false" not in main_body
    assert "claim-evidence map" in appendices.casefold()
    assert "autonomous loop" in appendices.casefold()
    assert "publication_ready=false" in appendices
    assert substrate_result.report.selected_substrate_title_optional in title
    assert substrate_inspection.substrate_count >= 2
    assert "F_ij = A_i B_j d_ij^{-alpha_i} exp(epsilon_ij)" in markdown
    assert "pooled-alpha gravity baseline" in markdown
    assert "baseline MAE `2.69526987`" in markdown
    assert "method MAE `0.06396946`" in markdown
    assert "low_heterogeneity" in markdown
    assert "high_heterogeneity" in markdown
    assert "The method beat the pooled-alpha baseline" in markdown
    assert "Generate synthetic regions, distances, origin masses" in markdown
    assert "MAE" in markdown
    assert "RMSE" in markdown
    assert "Low-Rank Residual Axes" in appendices
    assert "R ≈ U_k S_k V_k^T" in appendices
    assert "substrate-specific uv-local run" in markdown
    assert "do not provide broad empirical validation" in markdown
    assert "No passed formal proof artifact" in markdown
    assert "retrieval path(s) remain deferred" in markdown
    assert "the method is empirically validated" not in markdown.casefold()
    assert "publication-ready" not in markdown.casefold()
    assert "publication ready" not in markdown.casefold()
    registry = CitationRegistry.model_validate_json(
        (tmp_path / "runs" / run_id / "reports" / "citation-registry.json").read_text()
    )
    rendered_keys = set(re.findall(r"\[@([^\]]+)\]", markdown))
    assert rendered_keys <= {record.citation_key for record in registry.citations}
    assert all(record.accepted_for_registry for record in registry.citations)
    assert inspected["final_manuscript_present"] is True
    assert bundle["primary_artifact_to_read"] == result.report.final_manuscript_path
    assert bundle["final_manuscript_unsupported_claim_count"] == 0
    assert lint["quality_failure_reasons"] == []
    assert lint["claim_support_missing_required_citation_count"] == 0
    assert lint["citation_as_validation_misuse_count"] == 0
    assert lint["unregistered_citation_keys"] == []
    assert lint["publication_ready"] is False

    second = regenerate_final_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )
    assert second.index.regeneration_count == 2
    assert second.report.final_manuscript_path.endswith("final-manuscript-0002.md")
    assert (tmp_path / result.report.final_manuscript_path).is_file()


def test_final_release_bundle_assembles_layout_hashes_and_scoped_exports(tmp_path) -> None:
    run_id = "run-final-release-bundle"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    retrieval_quality = RetrievalQualityReport(
        run_id=run_id,
        retrieval_backend="local",
        total_retrieved_sources=2,
        accepted_source_count=1,
        rejected_source_count=1,
        accepted_source_ids=["fixture-smith"],
        rejected_source_ids=["fixture-rejected"],
        adequacy_status="bounded_context_only",
        coverage_limitations=["Local fixture coverage is bounded context only."],
    )
    reports = tmp_path / "runs" / run_id / "reports"
    (reports / "retrieval-quality-report.json").write_text(
        retrieval_quality.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id=run_id,
        claim_ids_or_section_ids=[BOUNDED_EMPIRICAL_DEMONSTRATION_CLAIM_ID],
    )
    ingest_experiment_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_file=experiment_file,
    )
    run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        loop_backend="deterministic",
        max_iterations=3,
        max_attempts_per_gap=1,
        enable_strategy_diversification=True,
    )
    final_result = regenerate_final_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )

    result = build_final_release_bundle(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    inspected = inspect_final_release_bundle(run_id=run_id, root=tmp_path)
    bundle_summary = inspect_paper_bundle_summary(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    reviewer = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    bundle_dir = tmp_path / result.report.bundle_path

    assert (
        result.persistence.commit.action_type == ControllerActionType.FINAL_RELEASE_BUNDLE_ASSEMBLED
    )
    assert result.report.bundle_status == "complete"
    assert result.report.publication_ready is False
    assert result.bundle.publication_ready is False
    assert result.report.missing_required_artifacts == []
    assert (bundle_dir / "paper" / "paper.md").is_file()
    assert (bundle_dir / "paper" / "paper.tex").is_file()
    assert (bundle_dir / "paper" / "references.bib").is_file()
    assert not (bundle_dir / "paper" / "paper.pdf").exists()
    assert (bundle_dir / "reports" / "claim-evidence-map.json").is_file()
    assert (bundle_dir / "reports" / "release-report.json").is_file()
    assert (bundle_dir / "reports" / "final-audit.json").is_file()
    assert (bundle_dir / "reports" / "reviewer-bundle-summary.json").is_file()
    assert (bundle_dir / "evidence" / "experiments").is_dir()
    assert (bundle_dir / "reproducibility" / "reproducibility-manifest.json").is_file()
    assert (bundle_dir / "reproducibility" / "artifact-manifest.json").is_file()
    assert (bundle_dir / "reproducibility" / "hashes.sha256").is_file()
    assert (bundle_dir / "reproducibility" / "environment.json").is_file()
    assert (bundle_dir / "reproducibility" / "commands.txt").is_file()
    assert (bundle_dir / "paper" / "paper.md").read_text(encoding="utf-8") == (
        tmp_path / final_result.report.final_manuscript_path
    ).read_text(encoding="utf-8")

    paper_tex = (bundle_dir / "paper" / "paper.tex").read_text(encoding="utf-8")
    assert "% publication_ready = false" in paper_tex
    assert r"\section{Abstract}" in paper_tex
    assert r"\bibliography{references}" in paper_tex
    assert "publication ready" not in paper_tex.casefold()
    assert "publication-ready" not in paper_tex.casefold()
    references = (bundle_dir / "paper" / "references.bib").read_text(encoding="utf-8")
    registry = CitationRegistry.model_validate_json(
        (tmp_path / "runs" / run_id / "reports" / "citation-registry.json").read_text()
    )
    accepted_keys = {record.citation_key for record in registry.citations}
    assert set(re.findall(r"@[A-Za-z]+\s*\{\s*([^,\s]+)", references)) <= accepted_keys
    assert "fixture-rejected" not in references

    for line in (bundle_dir / "reproducibility" / "hashes.sha256").read_text().splitlines():
        digest, relative = line.split("  ", maxsplit=1)
        assert sha256_file(bundle_dir / relative) == digest
    manifest = FinalReleaseBundleManifest.model_validate_json(
        (bundle_dir / "reproducibility" / "artifact-manifest.json").read_text()
    )
    for artifact in manifest.artifacts:
        assert sha256_file(bundle_dir / artifact.relative_path) == artifact.sha256
        assert artifact.non_evidence_flag is True
    reproducibility = FinalReleaseReproducibilityManifest.model_validate_json(
        (bundle_dir / "reproducibility" / "reproducibility-manifest.json").read_text()
    )
    assert reproducibility.network_used is False
    assert reproducibility.external_api_used is False
    assert reproducibility.publication_ready is False

    assert inspected["final_release_bundle_status"] == "complete"
    assert bundle_summary["final_release_bundle_present"] is True
    assert bundle_summary["final_release_bundle_status"] == "complete"
    assert bundle_summary["paper_tex_present"] is True
    assert bundle_summary["references_bib_present"] is True
    assert bundle_summary["paper_pdf_present"] is False
    assert lint["final_release_bundle_present"] is True
    assert lint["final_release_bundle_status"] == "complete"
    assert lint["final_release_bundle_missing_required_artifact_count"] == 0
    assert lint["publication_ready"] is False
    assert reviewer["final_release_bundle_present"] is True
    assert reviewer["final_release_bundle_status"] == "complete"
    assert (tmp_path / "runs" / run_id / "reports" / "final-release-bundle-0001.json").is_file()
    assert (tmp_path / "runs" / run_id / "reports" / "final-release-bundle-0001.md").is_file()
    assert (
        tmp_path / "runs" / run_id / "reports" / "final-release-bundle-index-0001.json"
    ).is_file()

    before_verification = {
        path.relative_to(bundle_dir).as_posix(): sha256_file(path)
        for path in bundle_dir.rglob("*")
        if path.is_file()
    }
    verification = verify_final_release_bundle(bundle_path=bundle_dir)
    after_verification = {
        path.relative_to(bundle_dir).as_posix(): sha256_file(path)
        for path in bundle_dir.rglob("*")
        if path.is_file()
    }
    assert verification.verification_status == "verified_with_warnings"
    assert verification.hash_mismatch_count == 0
    assert verification.missing_required_artifact_count == 0
    assert verification.rejected_reference_leak_count == 0
    assert verification.accepted_reference_check_passed is True
    assert verification.paper_tex_citation_check_passed is True
    assert verification.claim_evidence_check_passed is True
    assert verification.release_report_check_passed is True
    assert verification.publication_ready is False
    assert verification.network_used is False
    assert verification.external_api_used is False
    assert verification.bundle_modified is False
    assert verification.replay_summary.commands_reexecuted is False
    assert before_verification == after_verification

    run_lookup_verification = verify_final_release_bundle(
        run_id=run_id,
        root=tmp_path,
        write_report=True,
    )
    assert run_lookup_verification.verification_mode == "run_id_lookup"
    inspected_after_verification = inspect_final_release_bundle(run_id=run_id, root=tmp_path)
    paper_after_verification = inspect_paper_bundle_summary(run_id=run_id, root=tmp_path)
    lint_after_verification = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert inspected_after_verification["final_bundle_verification_present"] is True
    assert inspected_after_verification["final_bundle_hash_mismatch_count"] == 0
    assert paper_after_verification["final_bundle_verified"] is True
    assert lint_after_verification["final_bundle_verification_present"] is True
    assert lint_after_verification["final_bundle_verification_status"] == ("verified_with_warnings")
    assert lint_after_verification["final_bundle_publication_ready_flag"] is False

    cli_by_path = CliRunner().invoke(
        app,
        ["verify-final-release-bundle", "--bundle-path", str(bundle_dir), "--json"],
    )
    assert cli_by_path.exit_code == 0, cli_by_path.output
    assert json.loads(cli_by_path.output)["verification_status"] == "verified_with_warnings"
    cli_by_run = CliRunner().invoke(
        app,
        [
            "verify-final-release-bundle",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    assert cli_by_run.exit_code == 0, cli_by_run.output
    assert json.loads(cli_by_run.output)["verification_mode"] == "run_id_lookup"

    def tampered_bundle(name: str) -> Path:
        target = tmp_path / "tampered-bundles" / name
        shutil.copytree(bundle_dir, target)
        return target

    def relock_bundle(target: Path) -> None:
        manifest_path = target / "reproducibility" / "artifact-manifest.json"
        manifest_payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        for artifact in manifest_payload["artifacts"]:
            artifact["sha256"] = sha256_file(target / artifact["relative_path"])
        manifest_path.write_text(
            json.dumps(manifest_payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        hashes = {
            path.relative_to(target).as_posix(): sha256_file(path)
            for path in target.rglob("*")
            if path.is_file() and path.name != "hashes.sha256"
        }
        (target / "reproducibility" / "hashes.sha256").write_text(
            "".join(f"{digest}  {relative}\n" for relative, digest in sorted(hashes.items())),
            encoding="utf-8",
        )

    changed = tampered_bundle("changed-content")
    (changed / "paper" / "paper.md").write_text("tampered\n", encoding="utf-8")
    assert verify_final_release_bundle(bundle_path=changed).verification_status == "failed"

    missing = tampered_bundle("missing-required")
    (missing / "reports" / "claim-evidence-map.json").unlink()
    missing_report = verify_final_release_bundle(bundle_path=missing)
    assert missing_report.verification_status == "failed"
    assert missing_report.missing_required_artifact_count == 1
    assert missing_report.claim_evidence_check_passed is False

    rejected_reference = tampered_bundle("rejected-reference")
    with (rejected_reference / "paper" / "references.bib").open("a", encoding="utf-8") as file:
        file.write("\n@misc{RejectedSourceFixture, title={Rejected source}}\n")
    rejected_report = verify_final_release_bundle(bundle_path=rejected_reference)
    assert rejected_report.verification_status == "failed"
    assert rejected_report.rejected_reference_leak_count == 1

    hard_rejected_reference = tampered_bundle("hard-rejected-reference")
    with (hard_rejected_reference / "paper" / "references.bib").open("a", encoding="utf-8") as file:
        file.write("\n@misc{HardRejectedSourceFixture, title={Hard-rejected source}}\n")
    hard_rejected_report = verify_final_release_bundle(bundle_path=hard_rejected_reference)
    assert hard_rejected_report.verification_status == "failed"
    assert hard_rejected_report.rejected_reference_leak_count == 1

    bad_tex = tampered_bundle("bad-tex-citation")
    with (bad_tex / "paper" / "paper.tex").open("a", encoding="utf-8") as file:
        file.write("\n\\cite{UnknownSourceFixture}\n")
    bad_tex_report = verify_final_release_bundle(bundle_path=bad_tex)
    assert bad_tex_report.verification_status == "failed"
    assert bad_tex_report.paper_tex_citation_check_passed is False

    unsupported_claim = tampered_bundle("unsupported-claim")
    claim_map_path = unsupported_claim / "reports" / "claim-evidence-map.json"
    claim_map_payload = json.loads(claim_map_path.read_text(encoding="utf-8"))
    claim_map_payload["unsupported_non_scaffold_claim_ids"] = ["tampered-unsupported-claim"]
    claim_map_path.write_text(json.dumps(claim_map_payload), encoding="utf-8")
    unsupported_report = verify_final_release_bundle(bundle_path=unsupported_claim)
    assert unsupported_report.verification_status == "failed"
    assert unsupported_report.unsupported_claim_count == 1

    deferred_claim = tampered_bundle("deferred-nonblocking-claim")
    deferred_map_path = deferred_claim / "reports" / "claim-evidence-map.json"
    deferred_map_payload = json.loads(deferred_map_path.read_text(encoding="utf-8"))
    deferred_id = next(
        item["target_claim_id_optional"]
        for item in json.loads(
            (deferred_claim / "reports" / "autonomous-loop-report.json").read_text(encoding="utf-8")
        )["gap_terminal_classifications"]
        if item["terminal_class"] == "deferred_exhausted_proof"
    )
    deferred_map_payload["unsupported_non_scaffold_claim_ids"] = [deferred_id]
    for link in deferred_map_payload["links"]:
        if link["claim_id"] == deferred_id:
            link["support_status"] = "unsupported"
            link["classification"] = "unsupported_claim"
            link["support_type"] = "unsupported"
            link["unsupported_reason"] = "Formal proof remains explicitly deferred."
    deferred_map_path.write_text(
        json.dumps(deferred_map_payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    deferred_reproducibility_path = (
        deferred_claim / "reproducibility" / "reproducibility-manifest.json"
    )
    deferred_reproducibility = json.loads(deferred_reproducibility_path.read_text(encoding="utf-8"))
    deferred_reproducibility["artifact_hashes"]["claim_evidence_map"] = sha256_file(
        deferred_map_path
    )
    deferred_reproducibility_path.write_text(
        json.dumps(deferred_reproducibility, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    relock_bundle(deferred_claim)
    deferred_claim_report = verify_final_release_bundle(bundle_path=deferred_claim)
    assert deferred_claim_report.verification_status == "verified_with_warnings"
    assert deferred_claim_report.unsupported_claim_count == 1
    assert deferred_claim_report.claim_evidence_check_passed is True

    publication_ready = tampered_bundle("publication-ready")
    release_path = publication_ready / "reports" / "release-report.json"
    release_payload = json.loads(release_path.read_text(encoding="utf-8"))
    release_payload["publication_ready"] = True
    release_path.write_text(json.dumps(release_payload), encoding="utf-8")
    publication_report = verify_final_release_bundle(bundle_path=publication_ready)
    assert publication_report.verification_status == "failed"
    assert publication_report.publication_ready is True

    missing_manifest = tampered_bundle("missing-artifact-manifest")
    (missing_manifest / "reproducibility" / "artifact-manifest.json").unlink()
    missing_manifest_report = verify_final_release_bundle(bundle_path=missing_manifest)
    assert missing_manifest_report.verification_status == "failed"

    missing_reproducibility = tampered_bundle("missing-reproducibility")
    (missing_reproducibility / "reproducibility" / "reproducibility-manifest.json").unlink()
    missing_reproducibility_report = verify_final_release_bundle(
        bundle_path=missing_reproducibility
    )
    assert missing_reproducibility_report.verification_status == "failed"
    assert missing_reproducibility_report.reproducibility_check_passed is False

    missing_environment = tampered_bundle("missing-environment")
    (missing_environment / "reproducibility" / "environment.json").unlink()
    missing_environment_report = verify_final_release_bundle(bundle_path=missing_environment)
    assert missing_environment_report.verification_status == "failed"
    assert missing_environment_report.environment_metadata_present is False

    ledger_mismatch = tampered_bundle("ledger-tip-mismatch")
    reproducibility_path = ledger_mismatch / "reproducibility" / "reproducibility-manifest.json"
    reproducibility_payload = json.loads(reproducibility_path.read_text(encoding="utf-8"))
    reproducibility_payload["ledger_tip_hash_optional"] = "f" * 64
    reproducibility_path.write_text(json.dumps(reproducibility_payload), encoding="utf-8")
    ledger_mismatch_report = verify_final_release_bundle(bundle_path=ledger_mismatch)
    assert ledger_mismatch_report.verification_status == "failed"
    assert ledger_mismatch_report.ledger_check_passed is False

    stale_hash = tampered_bundle("stale-hash")
    hash_path = stale_hash / "reproducibility" / "hashes.sha256"
    hash_lines = hash_path.read_text(encoding="utf-8").splitlines()
    hash_lines[0] = f"{'0' * 64}  {hash_lines[0].split('  ', maxsplit=1)[1]}"
    hash_path.write_text("\n".join(hash_lines) + "\n", encoding="utf-8")
    stale_hash_report = verify_final_release_bundle(bundle_path=stale_hash)
    assert stale_hash_report.verification_status == "failed"
    assert stale_hash_report.hash_mismatch_count == 1

    second = build_final_release_bundle(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    assert second.index.bundle_count == 2
    assert second.report.bundle_path.endswith("final-bundle-0002")
    assert bundle_dir.is_dir()


def test_final_release_bundle_reports_incomplete_when_required_artifact_missing(tmp_path) -> None:
    run_id = "run-final-release-bundle-incomplete"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    reports = tmp_path / "runs" / run_id / "reports"
    retrieval_quality = RetrievalQualityReport(
        run_id=run_id,
        retrieval_backend="local",
        total_retrieved_sources=1,
        accepted_source_count=1,
        rejected_source_count=0,
        accepted_source_ids=["fixture-smith"],
        rejected_source_ids=[],
        adequacy_status="bounded_context_only",
        coverage_limitations=["Local fixture coverage is bounded context only."],
    )
    (reports / "retrieval-quality-report.json").write_text(
        retrieval_quality.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        loop_backend="deterministic",
        max_iterations=1,
    )
    regenerate_final_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )
    release_reports = sorted(
        path
        for path in reports.glob("full-paper-release-report*.json")
        if not path.name.endswith(".meta.json")
    )
    for release_report in release_reports:
        release_report.rename(release_report.with_suffix(".json.bak"))

    result = build_final_release_bundle(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    assert result.report.bundle_status == "incomplete"
    assert "reports/release-report.json" in result.report.missing_required_artifacts
    assert result.report.publication_ready is False


def test_final_release_references_bib_uses_accepted_registry_sources_only() -> None:
    accepted = _citation_registry_fixture(run_id="run-bib").citations[0]
    rejected = accepted.model_copy(
        update={
            "citation_id": "citation-source-rejected",
            "citation_key": "rejected2020",
            "source_id": "source-rejected",
            "title": "Rejected Fixture Source",
            "accepted_for_registry": False,
            "source_status": "rejected",
        }
    )
    registry = CitationRegistry(
        run_id="run-bib",
        citations=[accepted, rejected],
        bibliography=[],
        citation_key_policy="deterministic_fixture",
        citation_policy="registry-only",
        retrieval_backend="local",
        source_registry_hash="c" * 64,
        source_count=2,
        accepted_source_count=1,
        rejected_source_count=1,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )

    bib = build_references_bib(registry)

    assert "@misc{smith2021" in bib
    assert "rejected2020" not in bib
    assert "Rejected Fixture Source" not in bib
    assert "Bounded background context only" in bib


def test_autonomous_paper_controller_runs_full_mvp_and_fails_closed(tmp_path) -> None:
    run_id = "run-autonomous-paper-controller"
    config = LLMOrchestrationConfig(
        run_id=run_id,
        domain="human geography",
        candidate_backend="fake",
        reviewer_backend="fake",
        prose_backend="fake",
        claim_adjudicator_backend="fake",
        source_relevance_adjudicator_backend="fake",
        quality_repair_backend="deterministic",
        enable_retrieval=True,
        retrieval_backend="local",
        retrieval_local_path=(
            "tests/fixtures/retrieval/openalex_style_human_geography_sources.json"
        ),
        max_retrieval_sources=8,
        citation_policy="registry-only",
    )

    result = run_autonomous_paper(
        config=config,
        root=tmp_path,
        enable_safe_repair=True,
        loop_backend="deterministic",
        max_loop_iterations=4,
        max_attempts_per_gap=1,
        enable_strategy_diversification=True,
        enable_experiment_routing=True,
        enable_empirical_demonstration_gaps=True,
        enable_capability_escalation=True,
        python_sandbox_backend="uv_local",
        max_sandbox_runs_per_loop=2,
        max_sandbox_runs_per_iteration=1,
        regeneration_backend="deterministic",
        build_final_bundle=True,
        verify_final_bundle=True,
    )

    report = result.report
    assert (
        result.persistence.commit.action_type == ControllerActionType.AUTONOMOUS_PAPER_RUN_WRITTEN
    )
    assert report.controller_status in {
        "completed_with_warnings",
        "completed_with_deferred_gaps",
    }
    assert report.handoff_status in {
        "handoff_ready_for_human_review_with_warnings",
        "handoff_ready_for_evidence_extension",
    }
    assert [stage.stage_name for stage in report.stages] == [
        "base_generation",
        "autonomous_loop",
        "final_manuscript_regeneration",
        "final_release_bundle_assembly",
        "final_bundle_verification",
        "handoff",
    ]
    assert report.final_manuscript_status == "completed"
    assert report.final_bundle_status == "complete"
    assert report.final_bundle_verification_status == "verified_with_warnings"
    assert report.final_bundle_path_optional is not None
    assert report.final_verification_report_path_optional is not None
    assert report.final_manuscript_path_optional is not None
    assert report.unsupported_claim_count == 0
    assert report.claim_support_missing_required_citation_count == 0
    assert report.citation_as_validation_misuse_count == 0
    assert report.human_intervention_required is False
    assert report.publication_ready is False
    assert report.network_used is False
    assert report.external_api_used is False
    assert report.external_tools_used is False

    reports = tmp_path / "runs" / run_id / "reports"
    assert (reports / "autonomous-paper-run-0001.json").is_file()
    assert (reports / "autonomous-paper-run-0001.md").is_file()
    assert (reports / "autonomous-paper-run-index-0001.json").is_file()
    assert (
        len(
            [
                path
                for path in reports.glob("autonomous-paper-checkpoint-*.json")
                if "-index-" not in path.name and not path.name.endswith(".meta.json")
            ]
        )
        == 6
    )
    markdown = (reports / "autonomous-paper-run-0001.md").read_text(encoding="utf-8")
    assert "publication_ready: false" in markdown
    assert "inspect-final-release-bundle" in markdown

    inspected = inspect_autonomous_paper_run(run_id=run_id, root=tmp_path)
    paper = inspect_paper_bundle_summary(run_id=run_id, root=tmp_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert inspected["autonomous_paper_run_present"] is True
    assert inspected["autonomous_paper_final_bundle_verified"] is True
    assert paper["autonomous_paper_controller_status"] == report.controller_status
    assert paper["autonomous_paper_handoff_status"] == report.handoff_status
    assert lint["autonomous_paper_run_present"] is True
    assert lint["autonomous_paper_final_bundle_verified"] is True
    assert lint["autonomous_paper_unsupported_claim_count"] == 0
    assert lint["autonomous_paper_human_intervention_required"] is False

    cli_inspect = CliRunner().invoke(
        app,
        [
            "inspect-autonomous-paper-run",
            "--run-id",
            run_id,
            "--root",
            str(tmp_path),
            "--json",
        ],
    )
    assert cli_inspect.exit_code == 0, cli_inspect.output
    assert json.loads(cli_inspect.output)["controller_status"] == report.controller_status

    final_manuscript, _ = latest_final_manuscript_regeneration(tmp_path, run_id)
    final_bundle, _ = latest_final_release_bundle(tmp_path, run_id)
    final_verification = latest_final_bundle_verification(tmp_path, run_id)
    assert final_manuscript is not None
    assert final_bundle is not None
    assert final_verification is not None
    assert _final_manuscript_is_safe(final_manuscript)
    assert not _final_manuscript_is_safe(
        final_manuscript.model_copy(update={"regeneration_status": "failed"})
    )
    assert _final_bundle_is_complete(final_bundle)
    assert not _final_bundle_is_complete(
        final_bundle.model_copy(
            update={
                "bundle_status": "incomplete",
                "missing_required_artifacts": ["reports/release-report.json"],
            }
        )
    )
    assert _final_verification_is_safe(final_verification)
    assert not _final_verification_is_safe(
        final_verification.model_copy(
            update={"verification_status": "failed", "hash_mismatch_count": 1}
        )
    )
    assert _lint_has_safety_block(
        {
            "claim_support_missing_required_citation_count": 1,
            "citation_registry_sources_all_accepted": True,
        },
        0,
    )
    assert not _lint_has_safety_block(lint, 0)

    with pytest.raises(AutonomousPaperRunError, match="Run already exists"):
        run_autonomous_paper(config=config, root=tmp_path)

    verification_count = len(list(reports.glob("final-bundle-verification-*.json")))
    resumed = run_autonomous_paper(
        config=config,
        root=tmp_path,
        resume_existing=True,
    )
    assert resumed.report.autonomous_run_id == "autonomous-paper-run-0002"
    assert [stage.stage_status for stage in resumed.report.stages[:4]] == ["reused"] * 4
    assert resumed.report.publication_ready is False
    assert (reports / "autonomous-paper-run-0002.json").is_file()
    assert (reports / "autonomous-paper-run-index-0002.json").is_file()
    assert (reports / "autonomous-paper-resume-0001.json").is_file()
    assert (reports / "autonomous-paper-resume-0001.md").is_file()
    assert len(list(reports.glob("final-bundle-verification-*.json"))) == (verification_count + 1)

    checkpoints = inspect_autonomous_paper_checkpoints(run_id=run_id, root=tmp_path)
    resume = inspect_autonomous_paper_resume(run_id=run_id, root=tmp_path)
    assert checkpoints["checkpoint_count"] == 8
    assert checkpoints["resume_allowed"] is True
    assert checkpoints["checkpoints_failed"] == 0
    assert resume["resume_status"] == "completed_with_warnings"
    assert resume["actual_resume_stage"] == "final_bundle_verification"
    assert resume["stages_reused"] == [
        "base_generation",
        "autonomous_loop",
        "final_manuscript_regeneration",
        "final_release_bundle_assembly",
    ]
    assert resume["stages_rerun"] == ["final_bundle_verification", "handoff"]
    assert resume["final_bundle_verification_rerun"] is True

    lint_after_resume = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint_after_resume["autonomous_paper_checkpoint_present"] is True
    assert lint_after_resume["autonomous_paper_checkpoint_count"] == 8
    assert lint_after_resume["autonomous_paper_resume_allowed"] is True
    assert lint_after_resume["autonomous_paper_latest_resume_status"] == ("completed_with_warnings")
    assert lint_after_resume["autonomous_paper_stages_reused_count"] == 4
    assert lint_after_resume["autonomous_paper_stages_rerun_count"] == 2

    for command in (
        "inspect-autonomous-paper-checkpoints",
        "inspect-autonomous-paper-resume",
    ):
        cli_result = CliRunner().invoke(
            app,
            [command, "--run-id", run_id, "--root", str(tmp_path), "--json"],
        )
        assert cli_result.exit_code == 0, cli_result.output

    source_run = tmp_path / "runs" / run_id

    def copied_root(name: str) -> Path:
        root = tmp_path / name
        shutil.copytree(source_run, root / "runs" / run_id)
        return root

    corrupt_hash_root = copied_root("corrupt-checkpoint-hash")
    corrupt_hash_path = sorted(
        path
        for path in (corrupt_hash_root / "runs" / run_id / "reports").glob(
            "autonomous-paper-checkpoint-*.json"
        )
        if "-index-" not in path.name and not path.name.endswith(".meta.json")
    )[0]
    corrupt_hash = json.loads(corrupt_hash_path.read_text(encoding="utf-8"))
    corrupt_hash["checkpoint_hash"] = "0" * 64
    corrupt_hash_path.write_text(json.dumps(corrupt_hash), encoding="utf-8")
    assert not verify_autonomous_paper_checkpoints(
        run_id=run_id, root=corrupt_hash_root
    ).resume_allowed

    missing_checkpoint_root = copied_root("missing-checkpoint")
    missing_checkpoint_path = sorted(
        path
        for path in (missing_checkpoint_root / "runs" / run_id / "reports").glob(
            "autonomous-paper-checkpoint-*.json"
        )
        if "-index-" not in path.name and not path.name.endswith(".meta.json")
    )[0]
    missing_checkpoint_path.unlink()
    assert not verify_autonomous_paper_checkpoints(
        run_id=run_id, root=missing_checkpoint_root
    ).resume_allowed

    publication_root = copied_root("publication-ready-checkpoint")
    publication_path = sorted(
        path
        for path in (publication_root / "runs" / run_id / "reports").glob(
            "autonomous-paper-checkpoint-*.json"
        )
        if "-index-" not in path.name and not path.name.endswith(".meta.json")
    )[0]
    publication_payload = json.loads(publication_path.read_text(encoding="utf-8"))
    publication_payload["publication_ready"] = True
    publication_path.write_text(json.dumps(publication_payload), encoding="utf-8")
    publication_verification = verify_autonomous_paper_checkpoints(
        run_id=run_id, root=publication_root
    )
    assert not publication_verification.resume_allowed
    assert any("publication_ready=true" in item for item in publication_verification.blockers)

    stale_protocol_root = copied_root("stale-protocol-checkpoint")
    stale_protocol_path = sorted(
        path
        for path in (stale_protocol_root / "runs" / run_id / "reports").glob(
            "autonomous-paper-checkpoint-*.json"
        )
        if "-index-" not in path.name and not path.name.endswith(".meta.json")
    )[0]
    stale_protocol_payload = json.loads(stale_protocol_path.read_text(encoding="utf-8"))
    stale_protocol_payload["protocol_version"] = "0.45.0"
    stale_protocol_path.write_text(json.dumps(stale_protocol_payload), encoding="utf-8")
    stale_protocol_verification = verify_autonomous_paper_checkpoints(
        run_id=run_id, root=stale_protocol_root
    )
    assert not stale_protocol_verification.resume_allowed
    assert any("stale" in item for item in stale_protocol_verification.blockers)

    missing_manuscript_root = copied_root("missing-final-manuscript")
    manuscript_report, _ = latest_final_manuscript_regeneration(missing_manuscript_root, run_id)
    assert manuscript_report is not None
    (missing_manuscript_root / manuscript_report.final_manuscript_path).unlink()
    assert not verify_autonomous_paper_checkpoints(
        run_id=run_id, root=missing_manuscript_root
    ).resume_allowed

    corrupt_map_root = copied_root("corrupt-claim-map")
    corrupt_map_path = latest_claim_evidence_map_path(corrupt_map_root, run_id)
    assert corrupt_map_path is not None
    corrupt_map_path.write_text("{not-json}\n", encoding="utf-8")
    with pytest.raises(AutonomousPaperRunError, match="claim-evidence map is corrupt"):
        run_autonomous_paper(config=config, root=corrupt_map_root, resume_existing=True)

    corrupt_bundle_root = copied_root("corrupt-bundle-hash")
    bundle_report, _ = latest_final_release_bundle(corrupt_bundle_root, run_id)
    assert bundle_report is not None
    bundle_hash_path = (
        corrupt_bundle_root / bundle_report.bundle_path / "reproducibility" / "hashes.sha256"
    )
    bundle_hash_path.write_text(
        bundle_hash_path.read_text(encoding="utf-8") + "stale\n",
        encoding="utf-8",
    )
    assert not verify_autonomous_paper_checkpoints(
        run_id=run_id, root=corrupt_bundle_root
    ).resume_allowed

    corrupt_ledger_root = copied_root("corrupt-ledger")
    (corrupt_ledger_root / "runs" / run_id / "ledger.sqlite").write_bytes(b"corrupt")
    ledger_verification = verify_autonomous_paper_checkpoints(
        run_id=run_id, root=corrupt_ledger_root
    )
    assert not ledger_verification.resume_allowed
    assert any("Ledger" in item for item in ledger_verification.blockers)


def test_autonomous_paper_controller_persists_base_failure(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-autonomous-paper-base-failure"
    config = LLMOrchestrationConfig(run_id=run_id, domain="human geography")

    def fail_base_generation(**_: object) -> None:
        raise LLMOrchestrationError("deterministic base failure")

    monkeypatch.setattr(
        autonomous_paper_module,
        "run_llm_paper_orchestration",
        fail_base_generation,
    )
    result = run_autonomous_paper(config=config, root=tmp_path)

    assert result.report.controller_status == "failed"
    assert result.report.handoff_status == "handoff_failed"
    assert result.report.human_intervention_required is True
    assert result.report.publication_ready is False
    assert result.report.stages[0].stage_status == "failed"
    assert all(stage.stage_status == "skipped" for stage in result.report.stages[1:-1])
    assert (tmp_path / result.report_artifact.path).is_file()


def test_autonomous_paper_reports_stage_c_root_failure_for_openai_candidates(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_id = "run-openai-stage-c-root-failure"
    config = LLMOrchestrationConfig(
        run_id=run_id,
        domain="human geography",
        candidate_backend="openai",
        reviewer_backend="fake",
        prose_backend="openai",
        claim_adjudicator_backend="openai",
        source_relevance_adjudicator_backend="openai",
        allow_external_calls=True,
    )

    def blocked_base_generation(**kwargs: object) -> object:
        root = Path(kwargs["root"])
        reports = root / "runs" / run_id / "reports"
        reports.mkdir(parents=True, exist_ok=True)
        (reports / "stage-a-report.md").write_text(
            "# Stage A\n\n- Generated candidates: 4\n- Passing Stage A gate: 4\n",
            encoding="utf-8",
        )
        (reports / "stage-b-report.md").write_text(
            "# Stage B\n\n- Passing Stage B: 0\n",
            encoding="utf-8",
        )
        (reports / "stage-c-selection-report.md").write_text(
            "# Stage C\n\n- Stage C ready: 0\n",
            encoding="utf-8",
        )
        report = SimpleNamespace(
            orchestration_status="LLMOrchestrationFailed",
            call_accounting=[],
            warnings=[
                "run-stage-c failed: No Stage C-ready candidates found; "
                "run factori select-stage-c first"
            ],
            blocking_issues=["No manuscript plan found. Run plan-manuscript first."],
            publication_ready=False,
            safety_report=SimpleNamespace(safe=False),
            release_status=None,
        )
        return SimpleNamespace(
            report=report,
            config_artifact=None,
            budget_artifact=None,
            accounting_artifact=None,
            report_artifact=None,
            safety_artifact=None,
            generation_result=None,
            release_result=None,
        )

    monkeypatch.setattr(
        autonomous_paper_module,
        "run_llm_paper_orchestration",
        blocked_base_generation,
    )

    result = run_autonomous_paper(config=config, root=tmp_path)

    assert result.report.controller_status == "blocked_safety_gate"
    assert result.report.handoff_status == "handoff_blocked_by_safety_gate"
    assert result.report.root_base_generation_failure_stage == "stage_c_selection"
    assert (
        result.report.root_base_generation_failure_reason
        == "openai_candidate_generation_produced_no_stage_c_ready_candidates"
    )
    assert result.report.candidate_count == 4
    assert result.report.stage_a_survivor_count == 4
    assert result.report.stage_b_survivor_count == 0
    assert result.report.stage_c_ready_count == 0
    assert result.report.manuscript_plan_present is False
    assert (
        result.report.stages[0].blocking_issues[0]
        == "openai_candidate_generation_produced_no_stage_c_ready_candidates"
    )

    inspected = inspect_autonomous_paper_run(run_id=run_id, root=tmp_path)
    assert inspected["root_base_generation_failure_stage"] == "stage_c_selection"
    assert inspected["stage_c_ready_count"] == 0


def test_autonomous_paper_resume_after_injected_base_checkpoint_crash(tmp_path) -> None:
    run_id = "run-autonomous-paper-crash-resume"
    config = LLMOrchestrationConfig(
        run_id=run_id,
        domain="human geography",
        candidate_backend="fake",
        reviewer_backend="fake",
        prose_backend="fake",
        claim_adjudicator_backend="fake",
        source_relevance_adjudicator_backend="fake",
        quality_repair_backend="deterministic",
        enable_retrieval=True,
        retrieval_backend="local",
        retrieval_local_path=(
            "tests/fixtures/retrieval/openalex_style_human_geography_sources.json"
        ),
        max_retrieval_sources=8,
        citation_policy="registry-only",
    )

    with pytest.raises(AutonomousPaperInjectedCrash, match="base_generation"):
        run_autonomous_paper(
            config=config,
            root=tmp_path,
            fault_after_stage="base_generation",
        )

    partial = inspect_autonomous_paper_checkpoints(run_id=run_id, root=tmp_path)
    assert partial["checkpoint_count"] == 1
    assert partial["latest_completed_stage"] == "base_generation"
    assert partial["resume_allowed"] is True
    assert not list((tmp_path / "runs" / run_id / "reports").glob("autonomous-paper-run-*.json"))

    resumed = run_autonomous_paper(
        config=config,
        root=tmp_path,
        resume_existing=True,
    )
    resume = inspect_autonomous_paper_resume(run_id=run_id, root=tmp_path)
    assert resumed.report.stages[0].stage_status == "reused"
    assert resume["actual_resume_stage"] == "autonomous_loop"
    assert resume["stages_reused"] == ["base_generation"]
    assert resume["stages_rerun"] == [
        "autonomous_loop",
        "final_manuscript_regeneration",
        "final_release_bundle_assembly",
        "final_bundle_verification",
        "handoff",
    ]
    assert resume["final_bundle_verification_rerun"] is True
    assert resumed.report.unsupported_claim_count == 0
    assert resumed.report.human_intervention_required is False
    assert resumed.report.publication_ready is False


def test_autonomous_loop_blocks_corrupt_claim_evidence_map(tmp_path) -> None:
    run_id = "run-autonomous-loop-corrupt-map"
    _prepare_run(tmp_path, run_id=run_id)
    reports = tmp_path / "runs" / run_id / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "claim-evidence-map.json").write_text("{not-json}\n", encoding="utf-8")

    result = run_autonomous_loop(
        run_id=run_id,
        root=tmp_path,
        store=ArtifactStore(tmp_path),
        ledger=ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite"),
        loop_backend="deterministic",
        max_iterations=1,
    )

    assert result.report.loop_status == "blocked_requires_human_intervention"
    assert result.report.stop_reason == "safety_gate_blocked"
    assert result.report.iterations_completed == 0
    assert result.report.requires_human_intervention is True
    assert result.report.publication_ready is False


def test_evidence_aware_refresh_writes_bounded_artifact_wording_and_rechecks_gates(
    tmp_path,
) -> None:
    run_id = "run-evidence-aware-refresh"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        claim_ids_or_statement_ids=[
            "claim-cand-human-geography-optimal-transport-theory-b-theorem-or-conjecture-form"
        ],
    )
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id=run_id,
        claim_ids_or_section_ids=["demonstration-status"],
    )
    ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )
    ingest_experiment_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_file=experiment_file,
    )
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    result = refresh_evidence_aware_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )

    report_path = tmp_path / "runs" / run_id / "reports" / "evidence-aware-refresh-report.json"
    refreshed_path = (
        tmp_path / "runs" / run_id / "reports" / "evidence-aware-refreshed-manuscript-draft.md"
    )
    report = EvidenceAwareRefreshReport.model_validate_json(report_path.read_text(encoding="utf-8"))
    refreshed = refreshed_path.read_text(encoding="utf-8")
    lowered = refreshed.casefold()
    assert result.persistence.commit.action_type == (
        ControllerActionType.EVIDENCE_AWARE_REFRESH_WRITTEN
    )
    assert report.refresh_backend == "deterministic"
    assert report.proof_language_inserted is True
    assert report.experiment_language_inserted is True
    assert report.claim_support_rechecked_after_refresh is True
    assert report.claim_evidence_map_rechecked_after_refresh is True
    assert report.citation_safety_rechecked_after_refresh is True
    assert "formal proof artifact linked to a specific mapped claim" in lowered
    assert "completed uv-local synthetic experiment artifact" in lowered
    assert "does not establish novelty" in lowered
    assert "does not imply broad empirical validation" in lowered
    assert "publication readiness" in lowered
    assert "publication ready" not in lowered

    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["evidence_aware_refresh_report_present"] is True
    assert lint["evidence_aware_refresh_backend"] == "deterministic"
    assert lint["proof_language_inserted"] is True
    assert lint["experiment_language_inserted"] is True
    assert lint["claim_evidence_map_rechecked_after_refresh"] is True
    assert lint["claim_support_rechecked_after_refresh"] is True
    assert lint["citation_safety_rechecked_after_refresh"] is True
    assert lint["claim_support_missing_required_citation_count"] == 0
    assert lint["claim_support_forbidden_claim_count"] == 0
    assert lint["citation_as_validation_misuse_count"] == 0
    assert lint["claim_evidence_unsupported_count"] == 0
    assert lint["publication_ready"] is False
    assert result.release_status in {
        "ReadyForHumanReview",
        "ReadyForHumanReviewWithWarnings",
    }

    reviewer = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert reviewer["proof_supported_claim_count"] >= 1
    assert reviewer["experiment_supported_claim_count"] >= 1
    assert reviewer["citation_supported_claim_count"] >= 0
    assert reviewer["publication_ready"] is False


def test_evidence_aware_refresh_does_not_use_informal_proof_as_formal_wording(
    tmp_path,
) -> None:
    run_id = "run-evidence-aware-refresh-informal"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        proof_type="informal_proof_note",
        checker_status="not_checked",
        is_verification_evidence=False,
        claim_ids_or_statement_ids=[
            "claim-cand-human-geography-optimal-transport-theory-b-theorem-or-conjecture-form"
        ],
    )
    ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    result = refresh_evidence_aware_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )

    assert result.report.proof_language_inserted is False
    assert "formal proof artifact linked" not in result.refreshed_markdown.casefold()
    assert result.report.implies_publication_readiness is False
    assert result.report.creates_scientific_validation is False


def test_evidence_aware_refresh_blocks_unsupported_claim_evidence_map(tmp_path) -> None:
    run_id = "run-evidence-aware-refresh-blocked"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        checker_status="failed",
        is_verification_evidence=False,
        claim_ids_or_statement_ids=[
            "claim-cand-human-geography-optimal-transport-theory-b-theorem-or-conjecture-form"
        ],
    )
    ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    with pytest.raises(EvidenceAwareRefreshError, match="unsupported non-scaffold"):
        refresh_evidence_aware_manuscript(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            backend="deterministic",
        )

    assert not (
        tmp_path / "runs" / run_id / "reports" / "evidence-aware-refreshed-manuscript-draft.md"
    ).exists()


def test_human_review_reconciliation_applies_rejects_and_defers_safely(
    tmp_path,
) -> None:
    run_id = "run-human-review-reconciliation"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        claim_ids_or_statement_ids=[
            "claim-cand-human-geography-optimal-transport-theory-b-theorem-or-conjecture-form"
        ],
    )
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id=run_id,
        claim_ids_or_section_ids=["demonstration-status"],
    )
    ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )
    ingest_experiment_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_file=experiment_file,
    )
    review_file = _write_human_review_fixture(
        tmp_path,
        run_id=run_id,
        review_status="reviewed_with_blocking_changes",
        requested_changes=[
            "Clarify the problem framing and intended research question.",
            "Add an evidence-boundary clarification.",
            "Reference the existing formal proof artifact within its mapped scope.",
            "Mention the existing experiment artifact within its bounded result scope.",
            "Say this manuscript is novel.",
            "Say this manuscript is publication ready.",
            "State that the experiment validates the method broadly.",
            "State the theorem is proven without a matching proof artifact.",
            "Cite a rejected source for the background claim.",
            "Run expanded retrieval before stronger background claims.",
        ],
        blocking_concerns=["Requested changes require deterministic reconciliation."],
    )
    ingest_human_review(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        review_file=review_file,
    )
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    refresh_evidence_aware_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )

    result = reconcile_human_review(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )

    assert result.persistence.commit.action_type == (
        ControllerActionType.HUMAN_REVIEW_RECONCILIATION_WRITTEN
    )
    assert result.report.applied_change_count == 4
    assert result.report.rejected_change_count == 4
    assert result.report.deferred_change_count == 2
    assert result.report.requires_new_evidence_count == 2
    assert result.report.claim_support_rechecked_after_reconciliation is True
    assert result.report.claim_evidence_map_rechecked_after_reconciliation is True
    assert result.report.citation_safety_rechecked_after_reconciliation is True
    assert result.report.release_rechecked_after_reconciliation is True
    outcomes = {item.outcome for item in result.report.change_outcomes}
    assert "applied_safe_text_revision" in outcomes
    assert "applied_boundary_clarification" in outcomes
    assert "applied_existing_evidence_reference" in outcomes
    assert "rejected_forbidden_authority_claim" in outcomes
    assert "rejected_unsupported_claim" in outcomes
    assert "deferred_requires_proof_artifact" in outcomes
    assert "deferred_requires_retrieval_expansion" in outcomes
    assert "say this manuscript is novel" not in result.reconciled_markdown.casefold()
    assert "say this manuscript is publication ready" not in (result.reconciled_markdown.casefold())
    assert "formal proof artifact linked to a specific mapped claim" in (
        result.reconciled_markdown.casefold()
    )
    assert "completed experiment artifact linked to a bounded result claim" in (
        result.reconciled_markdown.casefold()
    )
    assert result.claim_evidence_map.unsupported_non_scaffold_claim_ids == []

    report_path = (
        tmp_path / "runs" / run_id / "reports" / "human-review-reconciliation-cycle-001.json"
    )
    markdown_report_path = report_path.with_suffix(".md")
    manuscript_path = tmp_path / "runs" / run_id / "reports" / "reconciled-manuscript-cycle-001.md"
    assert report_path.is_file()
    assert markdown_report_path.is_file()
    assert manuscript_path.is_file()
    report = HumanReviewReconciliationReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )
    assert report.creates_scientific_validation is False
    assert report.implies_publication_readiness is False
    assert report.is_verification_evidence is False
    inspected = inspect_human_review_reconciliation(run_id=run_id, root=tmp_path)
    assert inspected["human_review_reconciliation_present"] is True
    assert inspected["publication_ready"] is False

    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["human_review_reconciliation_present"] is True
    assert lint["human_review_applied_change_count"] == 4
    assert lint["human_review_rejected_change_count"] == 4
    assert lint["human_review_deferred_change_count"] == 2
    assert lint["claim_support_missing_required_citation_count"] == 0
    assert lint["claim_evidence_unsupported_count"] == 0
    assert lint["citation_as_validation_misuse_count"] == 0
    assert lint["citation_registry_sources_all_accepted"] is True
    assert lint["publication_ready"] is False

    reviewer = inspect_reviewer_bundle_summary(run_id=run_id, root=tmp_path)
    assert reviewer["summary_path"].endswith(
        "reviewer-bundle-summary-after-reconciliation-cycle-001.json"
    )
    assert reviewer["human_review_reconciliation_present"] is True
    assert reviewer["human_review_applied_change_count"] == 4
    assert reviewer["human_review_remaining_requested_changes"]
    assert reviewer["publication_ready"] is False


def test_structured_reviewer_requests_support_two_immutable_cycles(tmp_path) -> None:
    run_id = "run-structured-reviewer-cycles"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    proof_file = _write_proof_artifact_fixture(
        tmp_path,
        run_id=run_id,
        claim_ids_or_statement_ids=[
            "claim-cand-human-geography-optimal-transport-theory-b-theorem-or-conjecture-form"
        ],
    )
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id=run_id,
        claim_ids_or_section_ids=["demonstration-status"],
    )
    ingest_proof_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        proof_file=proof_file,
    )
    ingest_experiment_artifact(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        experiment_file=experiment_file,
    )
    review_file = _write_human_review_fixture(tmp_path, run_id=run_id)
    review = ingest_human_review(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        review_file=review_file,
    ).review
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    refresh_evidence_aware_manuscript(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )
    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)
    proof_link = next(
        link for link in claim_map.links if link.support_type == "formal_proof_verification"
    )
    experiment_link = next(
        link for link in claim_map.links if link.support_type == "experiment_result"
    )
    request_file_1 = _write_structured_request_set(
        tmp_path,
        run_id=run_id,
        request_set_id="request-set-001",
        review_id=review.review_id,
        target_artifact_path=(
            f"runs/{run_id}/reports/evidence-aware-refreshed-manuscript-draft.md"
        ),
        requests=[
            {
                "request_id": "clarify",
                "target_type": "section",
                "target_section_optional": "Introduction and Problem Framing",
                "requested_action": "clarify_wording",
                "priority": "high",
                "requires_new_evidence": False,
            },
            {
                "request_id": "proof",
                "target_type": "proof_artifact",
                "target_section_optional": "Claim and Evidence Boundaries",
                "target_claim_id_optional": proof_link.claim_id,
                "target_evidence_artifact_id_optional": (
                    proof_link.supporting_proof_artifact_ids[0]
                ),
                "requested_action": "add_existing_proof_reference",
                "priority": "high",
                "requires_new_evidence": False,
            },
            {
                "request_id": "experiment",
                "target_type": "experiment_artifact",
                "target_section_optional": "Demonstration Status",
                "target_claim_id_optional": experiment_link.claim_id,
                "target_evidence_artifact_id_optional": (
                    experiment_link.supporting_experiment_artifact_ids[0]
                ),
                "requested_action": "add_existing_experiment_reference",
                "priority": "high",
                "requires_new_evidence": False,
            },
            {
                "request_id": "forbidden",
                "target_type": "release_report",
                "requested_action": "forbidden_publication_ready_request",
                "priority": "blocking",
                "requires_new_evidence": False,
            },
            {
                "request_id": "new-proof",
                "target_type": "claim",
                "target_claim_id_optional": proof_link.claim_id,
                "requested_action": "request_new_proof_artifact",
                "priority": "medium",
                "requires_new_evidence": True,
            },
        ],
    )
    intake_1 = ingest_reviewer_change_requests(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        request_file=request_file_1,
    )
    assert intake_1.request_set_number == 1
    cycle_1 = reconcile_human_review(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    assert cycle_1.report.cycle_number == 1
    assert cycle_1.report.applied_change_count == 3
    assert cycle_1.report.rejected_change_count == 1
    assert cycle_1.report.deferred_change_count == 1
    cycle_1_path = tmp_path / "runs" / run_id / "reports" / "reconciled-manuscript-cycle-001.md"
    cycle_1_hash = sha256_file(cycle_1_path)

    request_file_2 = _write_structured_request_set(
        tmp_path,
        run_id=run_id,
        request_set_id="request-set-002",
        review_id=review.review_id,
        target_artifact_path=(f"runs/{run_id}/reports/reconciled-manuscript-cycle-001.md"),
        requests=[
            {
                "request_id": "boundary-cycle-2",
                "target_type": "section",
                "target_section_optional": "Limitations",
                "requested_action": "add_boundary_language",
                "priority": "medium",
                "requires_new_evidence": False,
            }
        ],
    )
    ingest_reviewer_change_requests(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        request_file=request_file_2,
    )
    cycle_2 = reconcile_human_review(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    assert cycle_2.report.cycle_number == 2
    assert cycle_2.report.applied_change_count == 1
    assert sha256_file(cycle_1_path) == cycle_1_hash
    assert (tmp_path / "runs" / run_id / "reports" / "reconciled-manuscript-cycle-002.md").is_file()
    index = HumanReviewReconciliationIndex.model_validate_json(
        (tmp_path / cycle_2.reconciliation_index_artifact.path).read_text()
    )
    assert index.latest_cycle == 2
    assert index.cycle_count == 2
    assert index.current_preferred_reconciled_manuscript.endswith(
        "reconciled-manuscript-cycle-002.md"
    )
    inspected = inspect_reviewer_change_requests(run_id=run_id, root=tmp_path)
    assert inspected["reviewer_request_set_count"] == 2
    lint = lint_paper_bundle_summary(run_id=run_id, root=tmp_path)
    assert lint["human_review_reconciliation_cycle_count"] == 2
    assert lint["latest_reconciliation_cycle"] == 2
    assert lint["claim_support_missing_required_citation_count"] == 0
    assert lint["claim_evidence_unsupported_count"] == 0
    assert lint["publication_ready"] is False


def test_structured_reviewer_request_intake_rejects_invalid_targets(tmp_path) -> None:
    run_id = "run-structured-reviewer-invalid"
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    review = ingest_human_review(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        review_file=_write_human_review_fixture(tmp_path, run_id=run_id),
    ).review
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    target = f"runs/{run_id}/reports/revised-manuscript-draft.md"

    unknown_section = _write_structured_request_set(
        tmp_path,
        run_id=run_id,
        request_set_id="unknown-section",
        review_id=review.review_id,
        target_artifact_path=target,
        requests=[
            {
                "request_id": "unknown-section-request",
                "target_type": "section",
                "target_section_optional": "Unknown Section",
                "requested_action": "clarify_wording",
                "priority": "medium",
                "requires_new_evidence": False,
            }
        ],
    )
    with pytest.raises(ReviewerChangeRequestError, match="unknown target section"):
        ingest_reviewer_change_requests(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            request_file=unknown_section,
        )

    unknown_claim = _write_structured_request_set(
        tmp_path,
        run_id=run_id,
        request_set_id="unknown-claim",
        review_id=review.review_id,
        target_artifact_path=target,
        requests=[
            {
                "request_id": "unknown-claim-request",
                "target_type": "claim",
                "target_claim_id_optional": "missing-claim-id",
                "requested_action": "request_new_proof_artifact",
                "priority": "medium",
                "requires_new_evidence": True,
            }
        ],
    )
    with pytest.raises(ReviewerChangeRequestError, match="unknown claim"):
        ingest_reviewer_change_requests(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            request_file=unknown_claim,
        )

    rejected_citation = _write_structured_request_set(
        tmp_path,
        run_id=run_id,
        request_set_id="rejected-citation",
        review_id=review.review_id,
        target_artifact_path=target,
        requests=[
            {
                "request_id": "rejected-citation-request",
                "target_type": "citation",
                "requested_action": "add_existing_citation",
                "requested_text_optional": "RejectedSourceKey",
                "priority": "medium",
                "requires_new_evidence": False,
            }
        ],
    )
    with pytest.raises(ReviewerChangeRequestError, match="accepted registry"):
        ingest_reviewer_change_requests(
            run_id=run_id,
            root=tmp_path,
            store=store,
            ledger=ledger,
            request_file=rejected_citation,
        )


def test_claim_evidence_map_links_human_review_occurrence_only(tmp_path) -> None:
    run_id = "run-claim-evidence-human-review"
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        items=[
            _claim_support_item(
                sentence_id="human-review-claim-1",
                claim_class="pipeline_status_claim",
                sentence_snippet=("Human review recorded readiness for evidence generation."),
                support_status="not_required_scaffold",
            ),
            _claim_support_item(
                sentence_id="proof-claim-1",
                claim_class="proof_claim",
                sentence_snippet="Human review confirms this theorem.",
                support_status="forbidden_claim_without_evidence",
            ),
        ],
    )
    _write_human_review_artifact_report(tmp_path, run_id=run_id)

    claim_map = build_claim_evidence_map(run_id=run_id, root=tmp_path)

    by_id = {link.claim_id: link for link in claim_map.links}
    assert by_id["human-review-claim-1"].support_type == "human_review_occurrence"
    assert by_id["proof-claim-1"].support_status == "unsupported"
    assert claim_map.summary_counts["human_reviewed_claim"] == 1


def test_safe_repair_separates_pre_and_post_repair_warnings(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-safe-repair-warnings")
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs/run-safe-repair-warnings/ledger.sqlite")

    result = generate_full_paper(
        run_id="run-safe-repair-warnings",
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=_UnsafeFirstProseGenerator(),
        config=FullPaperGenerationConfig(
            run_id="run-safe-repair-warnings",
            write_report=True,
        ),
        enable_safe_repair=True,
    )

    assert result.revision_result is not None
    repair_ref = result.revision_result.safe_repair_report_artifact
    assert repair_ref is not None
    payload = json.loads((tmp_path / repair_ref.path).read_text(encoding="utf-8"))
    assert payload["pre_repair_warnings"]
    assert payload["repaired_warnings"]
    for repaired_warning in payload["repaired_warnings"]:
        assert repaired_warning in payload["pre_repair_warnings"]
        assert repaired_warning not in payload["post_repair_warnings"]
        assert repaired_warning not in result.report.warnings
    assert all(
        "synthetic or MVP evidence is described as real-world empirical validation" not in warning
        for warning in result.report.warnings
    )


def test_generate_paper_render_check_fails_closed_without_external_tools(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-render")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-render",
            "--render-check",
        ],
    )

    assert result.exit_code == 1
    assert "External render tools are disabled" in result.stderr


def test_generate_paper_missing_manuscript_plan_fails_clearly(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "missing-plan",
        ],
    )

    assert result.exit_code == 1
    assert "No manuscript plan found" in result.stderr


def test_repeated_generate_paper_write_report_fails_by_default(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-repeat")
    runner = CliRunner()
    first = runner.invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-repeat",
            "--write-report",
        ],
    )
    second = runner.invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-repeat",
            "--write-report",
        ],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 1
    assert "already exists" in second.stderr


def test_generate_paper_skip_if_complete_reuses_existing_report(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-skip")
    runner = CliRunner()
    first = runner.invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-skip",
            "--write-report",
        ],
    )
    second = runner.invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-skip",
            "--write-report",
            "--rerun-policy",
            "skip-if-complete",
            "--json",
        ],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    payload = json.loads(second.output)
    assert payload["artifacts"]["full_paper_generation_report"] is not None


def test_full_paper_generation_does_not_mutate_claim_or_evidence_tables(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-boundary")
    before = _claim_table_snapshot(tmp_path, "run-boundary")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-boundary",
            "--apply-safe-fake-revision",
            "--write-report",
        ],
    )

    assert result.exit_code == 0, result.output
    assert _claim_table_snapshot(tmp_path, "run-boundary") == before
    ledger = ResearchLedger(tmp_path / "runs" / "run-boundary" / "ledger.sqlite")
    actions = [commit.action_type for commit in ledger.list_commits("run-boundary")]
    assert ControllerActionType.FULL_PAPER_GENERATION_WRITTEN in actions


def test_quality_aware_generation_improves_lint_on_safe_fixture(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-quality-aware")
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs/run-quality-aware/ledger.sqlite")

    result = generate_full_paper(
        run_id="run-quality-aware",
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=_QualityProseGenerator(),
        config=FullPaperGenerationConfig(run_id="run-quality-aware", write_report=True),
    )

    markdown_path = tmp_path / "runs/run-quality-aware/reports/complete-manuscript-draft.md"
    markdown = markdown_path.read_text(encoding="utf-8")
    lint = lint_paper_bundle_summary(run_id="run-quality-aware", root=tmp_path)

    assert result.report.publication_ready is False
    assert lint["paper_release_status"] is None
    assert lint["release_status_unchanged"] is True
    assert lint["quality_status"] in {"DraftQualityPass", "DraftQualityWarnings"}
    assert 7 <= lint["section_count"] <= 10
    assert lint["title_is_placeholder"] is False
    assert lint["semantic_checks"]["problem_statement_present"] is True
    assert lint["semantic_checks"]["central_contribution_present"] is True
    assert lint["semantic_checks"]["method_summary_present"] is True
    assert lint["semantic_checks"]["evidence_boundary_statement_present"] is True
    assert lint["semantic_checks"]["provenance_present"] is True
    assert markdown.lower().count("central contribution") == 1
    assert not any(
        "main result is not stated" in finding.message
        for finding in (result.critic_result.critic_report.findings if result.critic_result else [])
    )
    assert "## Empirical Results and Discussion" not in markdown
    assert "## Bibliography" not in markdown
    assert "## Demonstration Status" in markdown


def _write_claim_map_reports(
    tmp_path,
    *,
    run_id: str,
    items: list[ClaimSupportItem],
    citation_registry: CitationRegistry | None = None,
    retrieval_quality: RetrievalQualityReport | None = None,
) -> Path:
    reports = tmp_path / "runs" / run_id / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    audit = ClaimSupportAuditReport(
        run_id=run_id,
        citation_registry_present=citation_registry is not None,
        citation_policy="registry-only" if citation_registry is not None else "none",
        claim_support_items=items,
        summary_counts={"total_sentences": len(items)},
        unsupported_items=[
            item
            for item in items
            if item.support_status
            in {
                "missing_required_citation",
                "forbidden_claim_without_evidence",
                "unsupported_external_claim",
            }
        ],
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )
    (reports / "claim-support-audit.json").write_text(
        audit.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    if citation_registry is not None:
        (reports / "citation-registry.json").write_text(
            citation_registry.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
    if retrieval_quality is not None:
        (reports / "retrieval-quality-report.json").write_text(
            retrieval_quality.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )
    return reports


def _write_claim_evidence_map_report(
    tmp_path,
    claim_map: ClaimEvidenceMap,
) -> None:
    reports = tmp_path / "runs" / claim_map.run_id / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "claim-evidence-map.json").write_text(
        claim_map.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _claim_support_item(
    *,
    sentence_id: str,
    claim_class: str,
    sentence_hash: str | None = None,
    sentence_snippet: str = "Fixture claim sentence.",
    citation_keys_present: list[str] | None = None,
    supporting_source_ids: list[str] | None = None,
    support_status: str = "not_required_scaffold",
) -> ClaimSupportItem:
    return ClaimSupportItem(
        sentence_id=sentence_id,
        section_name="Fixture Section",
        sentence_text_hash=sentence_hash or "a" * 64,
        sentence_snippet=sentence_snippet,
        claim_class=claim_class,
        citation_keys_present=citation_keys_present or [],
        requires_citation=claim_class
        in {
            "literature_background_claim",
            "source_context_claim",
            "external_factual_claim",
        },
        requires_citation_reason=(
            "positive_literature_claim"
            if claim_class == "literature_background_claim"
            else "positive_source_context_claim"
            if claim_class == "source_context_claim"
            else "positive_external_claim"
            if claim_class == "external_factual_claim"
            else "claim_class_no_citation_required"
        ),
        required_support_type="accepted_registry_source" if citation_keys_present else "none",
        supporting_source_ids=supporting_source_ids or [],
        support_status=support_status,
        unsupported_reason=None
        if support_status in {"registry_supported", "not_required_scaffold"}
        else "fixture unsupported",
        paragraph_index=0,
        sentence_index=0,
        citation_use="background_context" if citation_keys_present else "none",
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _citation_registry_fixture(
    *,
    run_id: str,
    source_status: str = "retrieved",
    accepted_for_registry: bool = True,
) -> CitationRegistry:
    record = CitationRecord(
        citation_id="citation-source-1",
        citation_key="smith2021",
        source_id="source-1",
        title="Fixture Human Geography Source",
        authors=["Smith"],
        year=2021,
        venue="Fixture Journal",
        provider="fixture",
        retrieval_backend="local",
        retrieved_at="2026-06-30T00:00:00Z",
        raw_metadata_hash="b" * 64,
        source_status=source_status,
        source_summary="A bounded fixture source for background context.",
        accepted_for_registry=accepted_for_registry,
        may_support_background_context=True,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )
    return CitationRegistry(
        run_id=run_id,
        citations=[record],
        bibliography=[],
        citation_key_policy="deterministic_fixture",
        citation_policy="registry-only",
        retrieval_backend="local",
        source_registry_hash="c" * 64,
        source_count=1,
        accepted_source_count=1 if accepted_for_registry else 0,
        rejected_source_count=0 if accepted_for_registry else 1,
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _retrieval_quality_fixture(
    *,
    run_id: str,
    accepted_source_ids: list[str] | None = None,
    rejected_source_ids: list[str] | None = None,
    rejection_reasons: dict[str, str] | None = None,
) -> RetrievalQualityReport:
    accepted = ["source-1"] if accepted_source_ids is None else accepted_source_ids
    rejected = rejected_source_ids or []
    return RetrievalQualityReport(
        run_id=run_id,
        retrieval_backend="local",
        total_retrieved_sources=len(accepted) + len(rejected),
        accepted_source_count=len(accepted),
        rejected_source_count=len(rejected),
        queries_used=["fixture query"],
        coverage_limitations=["fixture retrieval is bounded"],
        adequacy_status="bounded_context_only",
        accepted_source_ids=accepted,
        rejected_source_ids=rejected,
        rejection_reasons=rejection_reasons or {},
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def _write_proof_artifact_report(tmp_path, *, run_id: str, **kwargs) -> None:
    proof_file = _write_proof_artifact_fixture(tmp_path, run_id=run_id, **kwargs)
    proof = ProofArtifact.model_validate_json(proof_file.read_text(encoding="utf-8"))
    reports = tmp_path / "runs" / run_id / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"proof-artifact-{proof.proof_id}.json").write_text(
        proof.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _write_experiment_artifact_report(tmp_path, *, run_id: str, **kwargs) -> None:
    experiment_file = _write_experiment_artifact_fixture(
        tmp_path,
        run_id=run_id,
        **kwargs,
    )
    experiment = ExperimentArtifact.model_validate_json(experiment_file.read_text(encoding="utf-8"))
    reports = tmp_path / "runs" / run_id / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / f"experiment-artifact-{experiment.experiment_id}.json").write_text(
        experiment.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _write_human_review_artifact_report(tmp_path, *, run_id: str) -> None:
    review_file = _write_human_review_fixture(tmp_path, run_id=run_id)
    review = HumanReviewArtifact.model_validate_json(review_file.read_text(encoding="utf-8"))
    reports = tmp_path / "runs" / run_id / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    (reports / "human-review-artifact.json").write_text(
        review.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _prepare_python_sandbox_fixture(tmp_path, run_id: str) -> PlannedExperimentSpec:
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    source = (
        Path(__file__).parent / "fixtures" / "experiments" / "bundles" / "synthetic_calibration"
    )
    destination = (
        tmp_path / "runs" / run_id / "approved-experiment-bundles" / "synthetic_calibration"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, destination)
    return PlannedExperimentSpec(
        run_id=run_id,
        spec_id="synthetic-calibration-experiment-spec",
        target_claim_id="demonstration-status-p0-s0",
        target_section="Demonstration Status",
        hypothesis_or_question=(
            "Does the bounded synthetic method reduce calibration error relative to baseline?"
        ),
        suggested_dataset="deterministic synthetic calibration grid",
        suggested_metrics=["baseline_mae", "method_mae", "bounded_improvement"],
        suggested_baselines=["synthetic identity baseline"],
        suggested_seed_policy="fixed seed 1729",
        expected_output_artifacts=["metrics.json", "outputs/summary.json"],
        experiment_bundle_path_optional=destination.relative_to(tmp_path).as_posix(),
        requested_dependencies=[],
        allow_network=False,
        seed=1729,
        timeout_seconds=30,
    )


def _prepare_experiment_routing_fixture(
    tmp_path,
    *,
    run_id: str,
    claim_class: str,
    support_scope: str,
) -> None:
    _prepare_reviewable_bundle(tmp_path, run_id=run_id)
    _copy_default_experiment_template_bundle(tmp_path)
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    support_status = (
        "missing_required_citation"
        if claim_class == "literature_background_claim"
        else "forbidden_claim_without_evidence"
    )
    _write_claim_map_reports(
        tmp_path,
        run_id=run_id,
        items=[
            _claim_support_item(
                sentence_id=f"{claim_class}-fixture-claim",
                claim_class=claim_class,
                sentence_snippet=support_scope,
                support_status=support_status,
            )
        ],
    )
    persist_claim_evidence_map(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
    )
    persist_autonomous_evidence_gap_plan(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        backend="deterministic",
    )


def _copy_default_experiment_template_bundle(tmp_path) -> None:
    source = (
        Path(__file__).parent / "fixtures" / "experiments" / "bundles" / "synthetic_calibration"
    )
    destination = (
        tmp_path / "tests" / "fixtures" / "experiments" / "bundles" / "synthetic_calibration"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        shutil.copytree(source, destination)


def _prepare_run(tmp_path, *, run_id: str) -> None:
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id=run_id,
            domain="human geography",
            root=tmp_path,
            stop_after=PipelineStage.PLAN_MANUSCRIPT,
        )
    )


def _prepare_reviewable_bundle(tmp_path, *, run_id: str) -> None:
    _prepare_run(tmp_path, run_id=run_id)
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
    run_full_paper_release_gate(
        run_id=run_id,
        root=tmp_path,
        store=store,
        ledger=ledger,
        config=FullPaperReleaseGateConfig(run_id=run_id, write_report=True),
    )


def _write_human_review_fixture(
    tmp_path,
    *,
    run_id: str,
    reviewed_run_id: str | None = None,
    review_status: str = "reviewed_ready_for_evidence_generation",
    checklist_items: list[str] | None = None,
    blocking_concerns: list[str] | None = None,
    non_blocking_comments: list[str] | None = None,
    requested_changes: list[str] | None = None,
    recommended_next_action: str = (
        "Proceed to evidence generation planning without publication-readiness claims."
    ),
    reviewer_attestation: str = (
        "I performed this human review locally and understand that it records review "
        "occurrence only."
    ),
    filename: str = "human-review.json",
) -> Path:
    artifact_run_id = reviewed_run_id or run_id
    payload = {
        "run_id": run_id,
        "review_id": f"review-{run_id}",
        "reviewer_name_optional": "Fixture Reviewer",
        "reviewer_role": "internal_human_reviewer",
        "reviewer_is_human": True,
        "llm_generated": False,
        "reviewed_artifact_paths": [
            f"runs/{artifact_run_id}/reports/revised-manuscript-draft.md",
            f"runs/{artifact_run_id}/reports/reviewer-bundle-summary.json",
            f"runs/{artifact_run_id}/reports/claim-support-audit.json",
        ],
        "reviewed_at": "2026-06-30T00:00:00Z",
        "review_status": review_status,
        "checklist_items": (
            [
                "problem framing checked",
                "citation registry checked",
                "accepted sources checked",
                "claim-support audit checked",
                "evidence gaps acknowledged",
                "proof artifact absent acknowledged",
                "experiment artifact absent acknowledged",
                "publication_ready remains false acknowledged",
            ]
            if checklist_items is None
            else checklist_items
        ),
        "blocking_concerns": blocking_concerns or [],
        "non_blocking_comments": non_blocking_comments
        or [
            "The draft can proceed to evidence-generation planning with retrieval limits preserved."
        ],
        "requested_changes": requested_changes or [],
        "accepted_limitations": [
            "Retrieval remains bounded background context only.",
            "Proof artifact is absent.",
            "Experiment artifact is absent.",
            "publication_ready remains false.",
        ],
        "recommended_next_action": recommended_next_action,
        "reviewer_attestation": reviewer_attestation,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
    }
    path = tmp_path / filename
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_structured_request_set(
    tmp_path,
    *,
    run_id: str,
    request_set_id: str,
    review_id: str,
    target_artifact_path: str,
    requests: list[dict[str, object]],
) -> Path:
    normalized_requests = [
        {
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
            **request,
        }
        for request in requests
    ]
    payload = {
        "run_id": run_id,
        "request_set_id": request_set_id,
        "review_id": review_id,
        "reviewer_name_optional": "Fixture Reviewer",
        "created_at": "2026-07-01T00:00:00Z",
        "target_artifact_path": target_artifact_path,
        "requests": normalized_requests,
        "reviewer_attestation": (
            "I authored these structured requests as a human reviewer and understand "
            "that they do not create evidence or publication readiness."
        ),
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
    }
    path = tmp_path / f"{request_set_id}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    return path


def _write_planned_experiment_spec(
    reports: Path,
    *,
    run_id: str,
    spec_id: str = "experiment-spec-fixture-001",
    target_claim_id: str = "experiment-claim-1",
    target_section: str = "Demonstration Status",
    hypothesis_or_question: str = "Can the local synthetic template record bounded metrics?",
) -> Path:
    spec = PlannedExperimentSpec(
        run_id=run_id,
        spec_id=spec_id,
        target_claim_id=target_claim_id,
        target_section=target_section,
        hypothesis_or_question=hypothesis_or_question,
        suggested_dataset="deterministic synthetic calibration fixture",
        suggested_metrics=["bounded_improvement", "method_error"],
        suggested_baselines=["deterministic baseline"],
        suggested_seed_policy="fixed seed 1729",
        expected_output_artifacts=["metrics", "log"],
    )
    path = reports / f"{spec_id}.json"
    path.write_text(spec.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _write_planned_proof_spec(
    reports: Path,
    *,
    run_id: str,
    spec_id: str = "proof-obligation-spec-fixture-001",
    target_claim_id: str = "fixture-theorem-claim",
    statement: str = "A bounded fixture statement requires proof evidence.",
    suggested_checker: str = "explicitly configured local formal proof backend",
    required_artifact_type: str = "passed scoped proof artifact",
) -> Path:
    spec = ProofObligationSpec(
        run_id=run_id,
        spec_id=spec_id,
        target_claim_id=target_claim_id,
        statement=statement,
        suggested_checker=suggested_checker,
        required_artifact_type=required_artifact_type,
    )
    path = reports / f"{spec_id}.json"
    path.write_text(spec.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _write_retrieval_expansion_request(
    reports: Path,
    *,
    run_id: str,
    request_id: str = "retrieval-expansion-request-fixture-001",
) -> Path:
    request = RetrievalExpansionRequest(
        run_id=run_id,
        request_id=request_id,
        target_claim_id_optional=None,
        target_section_optional="Introduction and Problem Framing",
        query_terms=["human", "geography", "bounded", "retrieval"],
        reason="Fixture bounded retrieval expansion request.",
        minimum_source_quality="accepted registry source after deterministic checks",
    )
    path = reports / f"{request_id}.json"
    path.write_text(request.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def _write_exhausted_gap_inputs(
    tmp_path: Path,
    *,
    run_id: str,
    gap_type: str,
) -> None:
    reports = tmp_path / "runs" / run_id / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    gap_fingerprint = "a" * 64
    target_claim = None if gap_type == "needs_retrieval_expansion" else "claim-1"
    target_section = (
        "Demonstration Status"
        if gap_type == "needs_python_experiment"
        else "Method and Model"
        if target_claim
        else None
    )
    expected_artifact = {
        "needs_retrieval_expansion": "retrieval_quality_report",
        "needs_formal_proof": "proof_artifact",
        "needs_python_experiment": "experiment_artifact",
        "needs_claim_removal": "revised_manuscript",
        "needs_claim_downgrade": "revised_manuscript",
    }[gap_type]
    record = GapAttemptRecord(
        gap_fingerprint=gap_fingerprint,
        target_claim_id_optional=target_claim,
        target_section_optional=target_section,
        gap_type=gap_type,
        recommended_action=f"Initial exhausted action for {gap_type}.",
        expected_artifact_type=expected_artifact,
        attempt_count=1,
        no_op_attempt_count=1,
        latest_attempt_status="skipped",
        current_gap_status="exhausted_no_progress",
    )
    history = GapAttemptHistory(
        run_id=run_id,
        history_version=9999,
        gap_count=1,
        attempt_count=1,
        exhausted_gap_count=1,
        records=[record],
        created_at="2026-07-01T00:00:00Z",
        updated_at="2026-07-01T00:00:00Z",
    )
    plan_item = AutonomousEvidenceGapPlanItem(
        item_id="plan-item-exhausted",
        target_type="claim" if target_claim else "retrieval",
        target_claim_id_optional=target_claim,
        target_section_optional=target_section,
        current_support_status="unsupported",
        gap_type=gap_type,
        recommended_action=record.recommended_action,
        priority="high",
        blocking=False,
        rationale="The initial deterministic strategy made no progress.",
        required_inputs=[f"target={target_claim or 'bounded-context'}"],
        expected_artifact_type=expected_artifact,
        automation_ready=False,
        gap_fingerprint=gap_fingerprint,
        gap_attempt_history_present=True,
        gap_attempt_count=1,
        gap_already_attempted=True,
        gap_exhausted=True,
        automation_ready_after_history=False,
    )
    plan = AutonomousEvidenceGapPlan(
        run_id=run_id,
        planner_backend="deterministic",
        planner_status="planned",
        claim_evidence_map_path=f"runs/{run_id}/reports/claim-evidence-map.json",
        claim_support_audit_path=f"runs/{run_id}/reports/claim-support-audit.json",
        plan_items=[plan_item],
        gap_attempt_history_present=True,
        gap_attempt_count=1,
        exhausted_gap_count=1,
    )
    (reports / "gap-attempt-history-9999.json").write_text(
        history.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )
    (reports / "autonomous-evidence-gap-plan-9999.json").write_text(
        plan.model_dump_json(indent=2) + "\n",
        encoding="utf-8",
    )


def _write_proof_artifact_fixture(
    tmp_path,
    *,
    run_id: str,
    artifact_run_id: str | None = None,
    proof_id: str = "lean-proof-passed-001",
    proof_type: str = "lean_verified",
    claim_ids_or_statement_ids: list[str] | None = None,
    checker_status: str = "passed",
    statement: str = ("A local checker report is linked for a bounded statement in this fixture."),
    is_verification_evidence: bool = True,
    proof_hash: str = "1" * 64,
) -> Path:
    reviewed_run_id = artifact_run_id or run_id
    payload = {
        "run_id": run_id,
        "proof_id": proof_id,
        "proof_type": proof_type,
        "claim_ids_or_statement_ids": claim_ids_or_statement_ids or ["statement-1"],
        "statement": statement,
        "artifact_path_optional": (f"runs/{reviewed_run_id}/reports/revised-manuscript-draft.md"),
        "checker_name_optional": "fixture-local-checker",
        "checker_version_optional": "0.1.0",
        "checker_status": checker_status,
        "checker_log_hash_optional": "2" * 64,
        "proof_hash": proof_hash,
        "review_status": "artifact_scope_not_human_validated",
        "limitations": [
            "This fixture is local proof-artifact intake only.",
            "It does not imply novelty, broad correctness, or publication readiness.",
        ],
        "created_at": "2026-06-30T00:00:00Z",
        "ingested_at": "2026-06-30T00:00:00Z",
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": is_verification_evidence,
    }
    path = tmp_path / f"{proof_id}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _write_experiment_artifact_fixture(
    tmp_path,
    *,
    run_id: str,
    artifact_run_id: str | None = None,
    experiment_id: str = "completed-experiment-001",
    claim_ids_or_section_ids: list[str] | None = None,
    status: str = "completed",
    result_summary: str = (
        "The local fixture run completed and reports bounded metrics for this run only."
    ),
    config_hash: str = "7" * 64,
    experiment_type: str = "local_synthetic_fixture",
    metrics: dict[str, object] | None = None,
) -> Path:
    reviewed_run_id = artifact_run_id or run_id
    payload = {
        "run_id": run_id,
        "experiment_id": experiment_id,
        "experiment_type": experiment_type,
        "claim_ids_or_section_ids": claim_ids_or_section_ids or ["demonstration-status"],
        "hypothesis_or_question": (
            "Can the local fixture record bounded experiment-output intake?"
        ),
        "status": status,
        "dataset_name_optional": "fixture-synthetic-dataset",
        "dataset_hash_optional": "6" * 64,
        "config_hash": config_hash,
        "code_commit_hash_optional": "abc123fixture",
        "command_optional": "factori fixture-experiment --local",
        "metrics": metrics
        or {
            "fixture_metric": 1.0,
            "sample_count": 3,
        },
        "result_summary": result_summary,
        "artifact_paths": [f"runs/{reviewed_run_id}/reports/revised-manuscript-draft.md"],
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
    path = tmp_path / f"{experiment_id}.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _assert_artifact_boundary_flags(
    tmp_path,
    ref: ArtifactRef,
    *,
    is_verification_evidence: bool = False,
) -> None:
    path = tmp_path / ref.path
    assert path.is_file()
    assert ref.content_hash == sha256_file(path)
    linked = ArtifactRef.model_validate_json(
        (tmp_path / f"{ref.path}.meta.json").read_text(encoding="utf-8")
    )
    assert linked.metadata["is_verification_evidence"] is is_verification_evidence
    assert linked.metadata["creates_scientific_validation"] is False
    assert linked.metadata["implies_publication_readiness"] is False


def _assert_non_evidence_artifact(tmp_path, ref: ArtifactRef) -> None:
    path = tmp_path / ref.path
    assert path.is_file()
    assert ref.content_hash == sha256_file(path)
    linked = ArtifactRef.model_validate_json(
        (tmp_path / f"{ref.path}.meta.json").read_text(encoding="utf-8")
    )
    assert linked.metadata["is_verification_evidence"] is False
    assert linked.metadata["creates_scientific_validation"] is False
    assert linked.metadata["implies_publication_readiness"] is False


class _UnsafeFirstProseGenerator:
    backend_name = "fake"
    is_fake = True
    external_calls_enabled = False

    def __init__(self) -> None:
        self._delegate = FakeProseGenerator()
        self._calls = 0

    def generate_section(self, section_contract, claim_table) -> GeneratedSectionDraft:
        self._calls += 1
        draft = self._delegate.generate_section(section_contract, claim_table)
        if self._calls != 1:
            return draft
        return draft.model_copy(
            update={
                "content": (
                    "Conjecture. The synthetic result is empirically validated. "
                    "This unsupported sentence is intentionally unsafe."
                ),
                "unsupported_sentences": ["This unsupported sentence is intentionally unsafe."],
            }
        )


class _QualityProseGenerator:
    backend_name = "fake"
    is_fake = True
    external_calls_enabled = False

    def generate_section(self, section_contract, claim_table) -> GeneratedSectionDraft:
        del claim_table
        title = section_contract.section_title
        allowed_claim_ids = list(section_contract.allowed_claim_ids)
        evidence_ids = list(section_contract.allowed_evidence_artifact_ids)
        content = _quality_section_text(title)
        return GeneratedSectionDraft(
            section_id=section_contract.section_id,
            title=title,
            content=content,
            claim_ids=allowed_claim_ids,
            used_claim_ids=allowed_claim_ids,
            used_evidence_artifact_ids=evidence_ids,
            used_citation_ids=[],
            used_citation_keys=[],
            unsupported_sentences=[],
            warnings=[],
        )


def _quality_section_text(title: str) -> str:
    lower = title.lower()
    if "introduction" in lower:
        seed = (
            "The problem framing is explicit: this manuscript studies the selected "
            "branch as a bounded internal research object, not as a verified result. "
            "No retrieval-backed citations are available, so the introduction does "
            "not invent citation markers or bibliography entries."
        )
    elif "method" in lower:
        seed = (
            "The method and model summary describes the deterministic scaffold, the "
            "claim table, and the evidence links as audit objects. The approach keeps "
            "presentation artifacts separate from verification evidence."
        )
    elif "claim" in lower:
        seed = (
            "The claim and evidence boundary section lists only admitted claim IDs "
            "and preserves their labels. It does not transform conjectural, fake, or "
            "presentation material into proof or experiment evidence."
        )
    elif "demonstration" in lower:
        seed = (
            "The demonstration status is a non-evidence MVP account. No real proof, "
            "real experiment, real-world empirical validation, or publication-ready "
            "claim is available from this generated paper package."
        )
    elif "limitation" in lower:
        seed = (
            "The limitations section states that fake validators, LLM prose, citation "
            "absence, and LaTeX export are context only. The draft remains suitable "
            "only for internal human review."
        )
    elif "conclusion" in lower:
        seed = (
            "The conclusion summarizes the bounded contribution and repeats that the "
            "generated manuscript cannot create evidence, upgrade labels, invent "
            "citations, or imply publication readiness."
        )
    else:
        seed = (
            "The abstract states the central message and keeps the scientific status "
            "bounded by the claim table, evidence map, and release warnings."
        )
    return " ".join(seed for _ in range(10))


def _claim_table_snapshot(tmp_path, run_id: str) -> bytes:
    path = tmp_path / "runs" / run_id / "reports" / "claim-table.json"
    return path.read_bytes()
