"""Minimal Typer CLI for the deterministic foundation."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Annotated

import typer

from factori.abstract_synthesis import AbstractSynthesisError, run_abstract_synthesis
from factori.adapters.atlas_ranking import OpenAIAtlasPairRanker
from factori.adapters.config import AdapterConfig
from factori.adapters.deep_opportunity import OpenAIDeepOpportunityGenerator
from factori.adapters.errors import AdapterError
from factori.adapters.registry import AdapterConfigurationError, get_adapter_registry
from factori.adapters.retrieval_real import OpenAlexRetrievalClient
from factori.artifacts import ArtifactStore
from factori.autonomous_evidence_plan import (
    AutonomousEvidencePlanError,
    inspect_autonomous_evidence_gap_plan,
    persist_autonomous_evidence_gap_plan,
)
from factori.autonomous_loop import (
    AutonomousLoopError,
    inspect_autonomous_loop,
    run_autonomous_loop,
)
from factori.autonomous_paper_checkpoint import (
    AutonomousPaperCheckpointError,
    inspect_autonomous_paper_checkpoints,
    inspect_autonomous_paper_resume,
)
from factori.autonomous_paper_run import (
    AutonomousPaperRunError,
    inspect_autonomous_paper_run,
    run_autonomous_paper,
)
from factori.autonomous_plan_execution import (
    AutonomousPlanExecutionError,
    execute_autonomous_evidence_plan,
    inspect_autonomous_plan_execution,
)
from factori.branch_routing import (
    BranchRoutingError,
    inspect_branch_routes,
    render_branch_route_text,
    route_branches,
)
from factori.capability_escalation import (
    CapabilityEscalationError,
    escalate_capabilities,
    inspect_capability_escalation,
)
from factori.citations import (
    build_citation_registry_from_ledger,
    validate_citation_usage,
    write_citation_registry_reports,
)
from factori.claim_evidence import (
    ClaimEvidenceMapError,
    inspect_claim_evidence_map,
    persist_claim_evidence_map,
)
from factori.commands.artifacts import write_artifact as write_artifact_entry
from factori.commands.candidates import add_candidate as add_candidate_entry
from factori.commands.questioner import run_questioner_check
from factori.commands.retrieval_demo import run_retrieval_adequacy_demo
from factori.config import (
    DEFAULT_ADAPTER_BACKEND,
    DEFAULT_ALLOW_EXTERNAL_CALLS,
    DEFAULT_ALLOW_EXTERNAL_TOOLS,
    DEFAULT_EXPERIMENT_BACKEND,
    DEFAULT_EXPERIMENT_REPLICATIONS,
    DEFAULT_EXPERIMENT_TIMEOUT_SECONDS,
    DEFAULT_LLM_MODEL,
    DEFAULT_PROOF_BACKEND,
    DEFAULT_PROOF_TIMEOUT_SECONDS,
    DEFAULT_PROSE_BACKEND,
    DEFAULT_RETRIEVAL_BACKEND,
    DEFAULT_RETRIEVAL_LIMIT,
    DEFAULT_REVIEWER_BACKEND,
    DEFAULT_REVIEWER_MAX_OBJECTIONS,
    DEFAULT_ROOT,
    DEFAULT_RUN_ID,
    LEDGER_FILENAME,
    OPENAI_API_KEY_ENV,
    OPENALEX_API_KEY_ENV,
)
from factori.creative_mutations import (
    CreativeMutationError,
    apply_creative_mutations,
    inspect_creative_mutations,
    plan_creative_mutations,
)
from factori.creative_search import (
    CreativeSearchError,
    inspect_creative_search,
    run_creative_search,
)
from factori.cross_run import CrossRunError, compare_runs, write_cross_run_report
from factori.deep_opportunity_discovery import (
    DeepOpportunityDiscoveryError,
    MockedOpportunityRetriever,
    OpenAlexOpportunityRetriever,
    discover_deep_opportunities,
    inspect_deep_opportunities,
    render_deep_opportunity_text,
)
from factori.diagnostics import (
    DiagnosticError,
    build_diagnostic_report,
    write_diagnostic_report,
)
from factori.domain_method_atlas import (
    AtlasScanError,
    build_domain_method_atlas,
    inspect_atlas_scan,
    render_atlas_scan_text,
    scan_domain_method_pairs,
)
from factori.draft_skeleton import DraftSkeletonError, run_draft_skeleton_generation
from factori.dry_run import build_pipeline_dry_run_plan
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
from factori.experiment_template_routing import (
    ExperimentGapRoutingError,
    inspect_experiment_gap_routing,
    route_experiment_gaps,
)
from factori.export_plan import ExportPreparationError, prepare_export
from factori.final_audit import FinalAuditError, run_final_audit
from factori.final_bundle_verification import (
    FinalBundleVerificationError,
    verify_final_release_bundle,
)
from factori.final_manuscript_regeneration import (
    FinalManuscriptRegenerationError,
    inspect_final_manuscript,
    regenerate_final_manuscript,
)
from factori.final_paper import PaperAssemblyError, run_paper_assembly
from factori.final_release_bundle import (
    FinalReleaseBundleError,
    build_final_release_bundle,
    inspect_final_release_bundle,
)
from factori.full_paper_generation import (
    FullPaperGenerationError,
    PaperBundleInspectionError,
    full_paper_generation_result_model,
    generate_full_paper,
    inspect_paper_bundle_summary,
    inspect_reviewer_bundle_summary,
    lint_paper_bundle_summary,
)
from factori.full_paper_release import (
    FullPaperReleaseError,
    run_full_paper_release_gate,
)
from factori.gap_attempts import (
    GapAttemptHistoryError,
    inspect_gap_attempt_history,
    inspect_planned_spec_dedup,
)
from factori.gap_strategy_diversification import (
    GapStrategyDiversificationError,
    inspect_gap_strategy_diversification,
    persist_gap_strategy_diversification,
)
from factori.generation_mutations import (
    GenerationMutationError,
    apply_generation_mutations,
    inspect_generation_mutations,
    plan_generation_mutations,
)
from factori.human_review import (
    HumanReviewIntakeError,
    ingest_human_review,
    inspect_human_review,
)
from factori.human_review_reconciliation import (
    HumanReviewReconciliationError,
    inspect_human_review_reconciliation,
    reconcile_human_review,
)
from factori.hygiene_plan import (
    build_hygiene_remediation_plan,
    summarize_hygiene_remediation_plan,
    write_hygiene_remediation_plan,
)
from factori.idea_space import (
    IdeaSpaceError,
    export_idea_space_report,
    inspect_idea_space,
    render_idea_space_text,
)
from factori.idea_tree import (
    IdeaTreeError,
    export_idea_tree,
    inspect_idea_tree,
    render_idea_tree_text,
)
from factori.latex_export import LatexExportError, export_latex_from_run
from factori.latex_render import LatexRenderError
from factori.ledger import LedgerError, ResearchLedger
from factori.literature_positioning import build_literature_positioning_report
from factori.llm_orchestration import (
    LLMOrchestrationError,
    LLMRunInspectionError,
    build_llm_orchestration_preflight_summary,
    inspect_llm_run_summary,
    llm_orchestration_result_model,
    run_llm_paper_orchestration,
)
from factori.manuscript_drafting import (
    ManuscriptDraftingError,
    draft_manuscript,
    load_manuscript_drafting_inputs,
)
from factori.manuscript_plan import ManuscriptPlanError, run_manuscript_planning
from factori.mutation_tournament import (
    MutationTournamentError,
    inspect_mutation_tournament,
    run_mutation_tournament,
)
from factori.narrative_contract import (
    NarrativeContractError,
    build_narrative_contract,
    load_narrative_inputs,
)
from factori.opportunity_discovery import (
    OpportunityDiscoveryError,
    discover_opportunities,
    inspect_opportunities,
    render_opportunity_discovery_text,
)
from factori.output_hygiene import (
    inspect_output_hygiene,
    summarize_output_hygiene,
    write_output_hygiene_report,
)
from factori.paper_critic import PaperCriticError, critique_paper_from_run
from factori.paper_revision import revise_paper_from_run
from factori.paper_shape import critique_paper_shape, write_paper_shape_reports
from factori.planned_spec_execution import (
    PlannedSpecExecutionError,
    execute_planned_specs,
    inspect_planned_spec_execution,
)
from factori.production_mode import (
    ProductionModeError,
    check_production_mode,
    inspect_backends,
    render_production_mode_text,
)
from factori.prose_contract import SectionDraftGenerationError, generate_section_draft
from factori.protocol_compat import ProtocolCompatibilityStatus, compare_schema_dirs
from factori.protocol_validation import (
    DEFAULT_PROTOCOL_EXAMPLES_DIR,
    validate_protocol_examples,
)
from factori.protocol_versioning import (
    ProtocolVersionCheckStatus,
    check_protocol_version_dirs,
)
from factori.protocols import PROTOCOL_VERSION
from factori.python_experiment_sandbox import (
    PythonExperimentSandboxError,
    inspect_python_experiment_sandbox,
    run_python_experiment_sandbox,
)
from factori.regression_diagnostics import summarize_cross_run_comparison
from factori.replay import (
    ReplayVerificationError,
    replay_verify_run,
    summarize_replay_verification,
    write_replay_report,
)
from factori.rerun_policy import decide_stage_rerun, validate_ledger_tip
from factori.research_object import ResearchObjectError, build_research_object
from factori.reviewer_change_requests import (
    ReviewerChangeRequestError,
    ingest_reviewer_change_requests,
    inspect_reviewer_change_requests,
)
from factori.route_execution import (
    RouteExecutionError,
    build_route_execution_specs,
    inspect_route_execution,
    render_route_execution_text,
    run_route_execution,
)
from factori.run_all import PipelineRunError, run_deterministic_pipeline
from factori.schema_export import (
    DEFAULT_PROTOCOL_OUTPUT_DIR,
    check_protocols,
    export_protocols,
)
from factori.schemas import (
    ArtifactType,
    ControllerActionType,
    DataRequirement,
    DeepOpportunityDiscoveryConfig,
    FullPaperGenerationConfig,
    FullPaperReleaseGateConfig,
    LLMBudgetConfig,
    LLMOrchestrationConfig,
    PipelineDryRunPlan,
    PipelineFailurePolicy,
    PipelineRunConfig,
    PipelineRunStatus,
    PipelineStage,
    PlannedStageStatus,
    RerunPolicy,
    StageRerunStatus,
    StagnationEvent,
    VerificationLabel,
)
from factori.scientific_substrate import (
    ScientificSubstrateError,
    build_scientific_substrate,
    inspect_scientific_substrate,
)
from factori.stage_a import constraint_from_inputs, run_stage_a
from factori.stage_b import StageBError, run_stage_b
from factori.stage_c import StageCError, run_stage_c
from factori.stage_c_selection import StageCSelectionError, run_stage_c_selection
from factori.stagnation import compute_stagnation, forced_stagnation_action
from factori.status import inspect_run_status, stage_status_detail, validate_resume_request
from factori.substrate_experiment_routing import (
    SubstrateExperimentRoutingError,
    inspect_substrate_experiment_routing,
    route_substrate_experiment,
)
from factori.substrate_promotion import (
    SubstratePromotionError,
    inspect_substrate_promotion,
    promote_variance_substrates,
    render_substrate_promotion_text,
)
from factori.substrate_tournament import (
    SubstrateTournamentError,
    inspect_substrate_tournament,
    run_substrate_tournament,
)
from factori.variance_augmentation import (
    VarianceAugmentationError,
    apply_variance_augmentation,
    augment_variance,
    inspect_variance_augmentation,
    render_variance_augmentation_text,
)

app = typer.Typer(no_args_is_help=True)


def _ledger_path(root: Path, run_id: str) -> Path:
    return root / "runs" / run_id / LEDGER_FILENAME


def _ledger(root: Path, run_id: str) -> ResearchLedger:
    return ResearchLedger(_ledger_path(root, run_id))


def _latest_parent(ledger: ResearchLedger, run_id: str) -> str | None:
    return ledger.latest_commit_hash(run_id)


def _ensure_run_initialized(root: Path, run_id: str) -> None:
    store = ArtifactStore(root)
    store.init_run(run_id)
    ledger = _ledger(root, run_id)
    if ledger.latest_commit_hash(run_id) is None:
        ledger.append_commit(
            run_id=run_id,
            action_type=ControllerActionType.INIT_RUN,
            payload={"run_id": run_id},
            timestamp="1970-01-01T00:00:00.000000Z",
        )


def _reject_non_fake_requirement(*, required: bool, stage_name: str) -> None:
    if not required:
        return
    typer.echo(
        f"Strict non-fake production mode rejected {stage_name}: the current backend is "
        "template, heuristic, or fixture based.",
        err=True,
    )
    raise typer.Exit(code=1)


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
        choices = "fail-if-exists, skip-if-complete, allow-if-forced, read-only-only"
        raise typer.BadParameter(f"Expected one of: {choices}") from exc


def _guard_mutating_stage(
    *,
    root: Path,
    run_id: str,
    stage: PipelineStage,
    rerun_policy: str,
    force: bool,
) -> bool:
    policy = _parse_rerun_policy(rerun_policy)
    status_report = inspect_run_status(run_id=run_id, root=root)
    decision = decide_stage_rerun(
        run_id=run_id,
        stage_name=stage,
        policy=policy,
        status_report=status_report,
        force=force,
        root=root,
    )
    if decision.status == StageRerunStatus.SKIPPED_ALREADY_COMPLETE:
        typer.echo(f"stage_rerun_status={decision.status.value}")
        typer.echo(f"stage={stage.value}")
        return False
    if not decision.should_run:
        typer.echo(f"stage_rerun_status={decision.status.value}", err=True)
        typer.echo(decision.reason, err=True)
        raise typer.Exit(code=1)
    return True


@app.command("export-protocols")
def export_protocols_command(
    output_dir: Annotated[
        Path,
        typer.Option("--output-dir"),
    ] = DEFAULT_PROTOCOL_OUTPUT_DIR,
    check: Annotated[bool, typer.Option("--check")] = False,
) -> None:
    """Export or verify language-neutral developer protocol contracts."""
    result = check_protocols(output_dir) if check else export_protocols(output_dir)
    if check and not result.up_to_date:
        typer.echo("Protocol files are stale or missing:", err=True)
        for path in result.stale_files:
            typer.echo(f"- {path}", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"protocol_version={PROTOCOL_VERSION}")
    typer.echo(f"schemas={len(result.schema_files)}")
    typer.echo(f"examples={len(result.example_files)}")
    typer.echo(f"output_dir={result.output_dir}")
    typer.echo(f"check={'ok' if check else 'not_requested'}")


@app.command("check-protocol-compat")
def check_protocol_compat_command(
    old_dir: Annotated[Path, typer.Option("--old-dir")],
    new_dir: Annotated[Path, typer.Option("--new-dir")],
    json_output: Annotated[bool, typer.Option("--json")] = False,
    fail_on_breaking: Annotated[
        bool,
        typer.Option("--fail-on-breaking"),
    ] = False,
) -> None:
    """Compare two protocol schema directories without modifying either."""
    report = compare_schema_dirs(old_dir, new_dir)
    if json_output:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        typer.echo(f"old_protocol_version={report.old_protocol_version}")
        typer.echo(f"new_protocol_version={report.new_protocol_version}")
        typer.echo(f"schemas_added={len(report.schemas_added)}")
        typer.echo(f"schemas_removed={len(report.schemas_removed)}")
        typer.echo(f"schemas_changed={len(report.schemas_changed)}")
        typer.echo(f"breaking_changes={len(report.breaking_changes)}")
        typer.echo(f"nonbreaking_changes={len(report.nonbreaking_changes)}")
        typer.echo(f"documentation_changes={len(report.documentation_changes)}")
        typer.echo(f"unknown_changes={len(report.unknown_changes)}")
        typer.echo(f"compatibility_status={report.compatibility_status.value}")
        for error in report.comparison_errors:
            typer.echo(f"error={error}", err=True)
    if report.compatibility_status == ProtocolCompatibilityStatus.COMPARISON_FAILED:
        raise typer.Exit(code=1)
    if fail_on_breaking and report.breaking_changes:
        raise typer.Exit(code=1)


@app.command("validate-protocol-examples")
def validate_protocol_examples_command(
    schema_dir: Annotated[
        Path,
        typer.Option("--schema-dir"),
    ] = DEFAULT_PROTOCOL_OUTPUT_DIR,
    examples_dir: Annotated[
        Path,
        typer.Option("--examples-dir"),
    ] = DEFAULT_PROTOCOL_EXAMPLES_DIR,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate deterministic protocol examples against exported JSON Schemas."""
    report = validate_protocol_examples(schema_dir=schema_dir, examples_dir=examples_dir)
    if json_output:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        typer.echo(f"schema_dir={report.schema_dir}")
        typer.echo(f"examples_dir={report.examples_dir}")
        typer.echo(f"examples_checked={report.examples_checked}")
        typer.echo(f"examples_valid={report.examples_valid}")
        typer.echo(f"examples_invalid={report.examples_invalid}")
        for result in report.results:
            if not result.valid:
                typer.echo(f"invalid_example={result.example_file}", err=True)
                for error in result.errors:
                    typer.echo(f"error={error}", err=True)
    if report.examples_invalid:
        raise typer.Exit(code=1)


@app.command("check-protocol-version")
def check_protocol_version_command(
    old_dir: Annotated[Path, typer.Option("--old-dir")],
    new_dir: Annotated[Path, typer.Option("--new-dir")],
    old_version: Annotated[str | None, typer.Option("--old-version")] = None,
    new_version: Annotated[str | None, typer.Option("--new-version")] = None,
    allow_unknown: Annotated[bool, typer.Option("--allow-unknown")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Check semantic protocol version movement against schema compatibility."""
    report = check_protocol_version_dirs(
        old_dir,
        new_dir,
        old_version=old_version,
        new_version=new_version,
        allow_unknown=allow_unknown,
    )
    if json_output:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
    else:
        typer.echo(f"old_version={report.old_version}")
        typer.echo(f"new_version={report.new_version}")
        typer.echo(f"required_bump={report.required_bump.value}")
        typer.echo(f"observed_bump={report.observed_bump.value}")
        typer.echo(f"version_check_status={report.status.value}")
        typer.echo(f"compatibility_status={report.compatibility_status.value}")
        typer.echo(f"breaking_changes={report.breaking_changes}")
        typer.echo(f"nonbreaking_changes={report.nonbreaking_changes}")
        typer.echo(f"documentation_changes={report.documentation_changes}")
        typer.echo(f"unknown_changes={report.unknown_changes}")
        for reason in report.reasons:
            typer.echo(f"reason={reason}", err=True)
    if report.status != ProtocolVersionCheckStatus.PASSED:
        raise typer.Exit(code=1)


@app.command("adapters")
@app.command("show-adapters")
def show_adapters_command(
    backend: Annotated[str, typer.Option("--backend")] = DEFAULT_ADAPTER_BACKEND,
    allow_external_calls: Annotated[
        bool,
        typer.Option("--allow-external-calls"),
    ] = DEFAULT_ALLOW_EXTERNAL_CALLS,
    llm_model: Annotated[str, typer.Option("--llm-model")] = DEFAULT_LLM_MODEL,
    reviewer_backend: Annotated[
        str,
        typer.Option("--reviewer-backend"),
    ] = DEFAULT_REVIEWER_BACKEND,
    use_llm_reviewers: Annotated[
        bool,
        typer.Option("--use-llm-reviewers"),
    ] = False,
    reviewer_model: Annotated[
        str,
        typer.Option("--reviewer-model"),
    ] = DEFAULT_LLM_MODEL,
    retrieval_backend: Annotated[
        str,
        typer.Option("--retrieval-backend"),
    ] = DEFAULT_RETRIEVAL_BACKEND,
    retrieval_limit: Annotated[
        int,
        typer.Option("--retrieval-limit"),
    ] = DEFAULT_RETRIEVAL_LIMIT,
    proof_backend: Annotated[
        str,
        typer.Option("--proof-backend"),
    ] = DEFAULT_PROOF_BACKEND,
    allow_external_tools: Annotated[
        bool,
        typer.Option("--allow-external-tools"),
    ] = DEFAULT_ALLOW_EXTERNAL_TOOLS,
    proof_executable: Annotated[
        str | None,
        typer.Option("--proof-executable"),
    ] = None,
    proof_timeout_seconds: Annotated[
        int,
        typer.Option("--proof-timeout-seconds"),
    ] = DEFAULT_PROOF_TIMEOUT_SECONDS,
    experiment_backend: Annotated[
        str,
        typer.Option("--experiment-backend"),
    ] = DEFAULT_EXPERIMENT_BACKEND,
    experiment_runner: Annotated[
        str | None,
        typer.Option("--experiment-runner"),
    ] = None,
    experiment_timeout_seconds: Annotated[
        int,
        typer.Option("--experiment-timeout-seconds"),
    ] = DEFAULT_EXPERIMENT_TIMEOUT_SECONDS,
    experiment_replications: Annotated[
        int,
        typer.Option("--experiment-replications"),
    ] = DEFAULT_EXPERIMENT_REPLICATIONS,
    prose_backend: Annotated[
        str,
        typer.Option("--prose-backend"),
    ] = DEFAULT_PROSE_BACKEND,
    prose_model: Annotated[str, typer.Option("--prose-model")] = DEFAULT_LLM_MODEL,
) -> None:
    """Show the active adapter registry without calling any backend."""
    try:
        registry = get_adapter_registry(
            AdapterConfig(
                adapter_backend=backend,
                allow_external_calls=allow_external_calls,
                llm_model=llm_model,
                reviewer_backend=reviewer_backend,
                use_llm_reviewers=use_llm_reviewers,
                reviewer_model=reviewer_model,
                retrieval_backend=retrieval_backend,
                retrieval_limit=retrieval_limit,
                proof_backend=proof_backend,
                allow_external_tools=allow_external_tools,
                proof_executable=proof_executable,
                proof_timeout_seconds=proof_timeout_seconds,
                experiment_backend=experiment_backend,
                experiment_runner=experiment_runner,
                experiment_timeout_seconds=experiment_timeout_seconds,
                experiment_replications=experiment_replications,
                prose_backend=prose_backend,
                prose_model=prose_model,
            )
        )
    except (AdapterConfigurationError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"adapter_backend={registry.config.adapter_backend}")
    typer.echo(f"allow_external_calls={str(registry.config.allow_external_calls).lower()}")
    typer.echo(f"llm_model={registry.config.llm_model}")
    typer.echo(f"reviewer_backend={registry.config.reviewer_backend}")
    typer.echo(f"use_llm_reviewers={str(registry.config.use_llm_reviewers).lower()}")
    typer.echo(f"reviewer_model={registry.config.reviewer_model}")
    typer.echo(f"retrieval_backend={registry.config.retrieval_backend}")
    typer.echo(f"retrieval_limit={registry.config.retrieval_limit}")
    typer.echo(f"proof_backend={registry.config.proof_backend}")
    typer.echo(f"allow_external_tools={str(registry.config.allow_external_tools).lower()}")
    typer.echo(f"proof_executable={registry.config.proof_executable or 'not_configured'}")
    typer.echo(f"proof_timeout_seconds={registry.config.proof_timeout_seconds}")
    typer.echo(f"experiment_backend={registry.config.experiment_backend}")
    typer.echo(f"experiment_runner={registry.config.experiment_runner or 'not_configured'}")
    typer.echo(f"experiment_timeout_seconds={registry.config.experiment_timeout_seconds}")
    typer.echo(f"experiment_replications={registry.config.experiment_replications}")
    typer.echo(f"prose_backend={registry.config.prose_backend}")
    typer.echo(f"prose_model={registry.config.prose_model}")
    for name, class_name in registry.class_names().items():
        typer.echo(f"{name}={class_name}")
    for descriptor in registry.provider_descriptors():
        typer.echo(
            "provider_descriptor="
            f"backend={descriptor.backend_name},"
            f"provider={descriptor.provider_name},"
            f"kind={descriptor.adapter_kind},"
            f"is_default={str(descriptor.is_default).lower()},"
            f"is_fake={str(descriptor.is_fake).lower()},"
            f"requires_external_calls={str(descriptor.requires_external_calls).lower()},"
            f"requires_external_tools={str(descriptor.requires_external_tools).lower()},"
            f"requires_api_key={str(descriptor.requires_api_key).lower()},"
            "supports_candidate_generation="
            f"{str(descriptor.supports_candidate_generation).lower()},"
            f"supports_review={str(descriptor.supports_review).lower()},"
            f"supports_retrieval={str(descriptor.supports_retrieval).lower()},"
            f"supports_proof={str(descriptor.supports_proof).lower()},"
            f"supports_experiments={str(descriptor.supports_experiments).lower()},"
            "supports_prose_generation="
            f"{str(descriptor.supports_prose_generation).lower()}"
        )


@app.command("init-run")
def init_run(
    run_id: Annotated[str, typer.Option("--run-id")] = DEFAULT_RUN_ID,
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
) -> None:
    """Initialize a local run directory and root ledger commit."""
    previous_head = _ledger(root, run_id).latest_commit_hash(run_id)
    _ensure_run_initialized(root, run_id)
    if previous_head is None:
        commit = _ledger(root, run_id).list_commits(run_id)[0]
        typer.echo(f"initialized {run_id} {commit.commit_hash}")
    else:
        typer.echo(f"initialized {run_id}")


@app.command("add-candidate")
def add_candidate(
    run_id: Annotated[str, typer.Option("--run-id")] = DEFAULT_RUN_ID,
    candidate_id: Annotated[str, typer.Option("--candidate-id")] = "candidate-001",
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    domain: Annotated[str, typer.Option("--domain")] = "example-domain",
    question: Annotated[str, typer.Option("--question")] = (
        "What deterministic MVP invariant is tested?"
    ),
    data_requirement: Annotated[
        DataRequirement,
        typer.Option("--data-requirement", case_sensitive=True),
    ] = DataRequirement.NO_DATA,
) -> None:
    """Add a deterministic example candidate and ledger it."""
    result = add_candidate_entry(
        run_id=run_id,
        candidate_id=candidate_id,
        root=root,
        domain=domain,
        question=question,
        data_requirement=data_requirement,
    )
    typer.echo(f"added {candidate_id} {result.commit.commit_hash}")


@app.command("show-ledger")
def show_ledger(
    run_id: Annotated[str, typer.Option("--run-id")] = DEFAULT_RUN_ID,
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
) -> None:
    """Print ledger commits as JSON lines."""
    ledger = _ledger(root, run_id)
    for commit in ledger.list_commits(run_id):
        typer.echo(json.dumps(commit.model_dump(mode="json"), sort_keys=True))


@app.command("write-artifact")
def write_artifact(
    run_id: Annotated[str, typer.Option("--run-id")] = DEFAULT_RUN_ID,
    artifact_id: Annotated[str, typer.Option("--artifact-id")] = "artifact-001",
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    kind: Annotated[ArtifactType, typer.Option("--kind", case_sensitive=True)] = (
        ArtifactType.REPORT
    ),
    format_: Annotated[str, typer.Option("--format")] = "json",
    content: Annotated[str | None, typer.Option("--content")] = None,
) -> None:
    """Write a JSON or Markdown artifact and ledger it."""
    try:
        result = write_artifact_entry(
            run_id=run_id,
            artifact_id=artifact_id,
            root=root,
            kind=kind,
            format_=format_,
            content=content,
        )
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    typer.echo(f"wrote {result.artifact.path} {result.commit.commit_hash}")


@app.command("validate-run")
def validate_run(
    run_id: Annotated[str, typer.Option("--run-id")] = DEFAULT_RUN_ID,
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
) -> None:
    """Validate the local run directory and ledger invariants."""
    store = ArtifactStore(root)
    store.validate_run_structure(run_id)
    ledger = _ledger(root, run_id)
    try:
        ledger.validate()
    except LedgerError as exc:
        raise typer.Exit(code=1) from exc
    typer.echo(f"valid {run_id}")


@app.command("validate-ledger-tip")
def validate_ledger_tip_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
) -> None:
    """Inspect ledger tips, forks, parent links, and duplicate stage markers."""
    report = validate_ledger_tip(run_id, root=root)
    typer.echo(f"run_id={report.run_id}")
    typer.echo(f"ledger_tip_status={report.status.value}")
    typer.echo(f"commits={report.commit_count}")
    typer.echo(f"tips={len(report.tip_hashes)}")
    typer.echo(f"branch_findings={len(report.branch_findings)}")
    typer.echo(f"duplicate_stage_findings={len(report.duplicate_stage_findings)}")
    typer.echo(f"blocking_findings={len(report.blocking_findings)}")
    if report.status.value in {"Invalid", "Missing"}:
        raise typer.Exit(code=1)


@app.command("status")
def status_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    stage: Annotated[PipelineStage | None, typer.Option("--stage")] = None,
) -> None:
    """Inspect deterministic run checkpoints without mutating provenance."""
    if stage is not None:
        detail = stage_status_detail(run_id=run_id, stage_name=stage, root=root)
        if json_output:
            typer.echo(json.dumps(detail, sort_keys=True))
            return
        typer.echo(f"run_id={detail['run_id']}")
        typer.echo(f"stage={detail['stage']}")
        typer.echo(f"completed={str(detail['completed']).lower()}")
        typer.echo(f"required_artifacts_present={len(detail['required_artifacts_present'])}")
        typer.echo(f"required_artifacts_missing={len(detail['required_artifacts_missing'])}")
        typer.echo(f"prerequisites={len(detail['prerequisites'])}")
        return

    report = inspect_run_status(run_id=run_id, root=root)
    if json_output:
        typer.echo(json.dumps(report.model_dump(mode="json"), sort_keys=True))
        return
    typer.echo(f"run_id={report.run_id}")
    typer.echo(f"completeness_status={report.completeness_status.value}")
    typer.echo(f"completed_stages={len(report.completed_stages)}")
    typer.echo(f"missing_stages={len(report.missing_stages)}")
    typer.echo(
        "last_completed_stage="
        + (report.last_completed_stage.value if report.last_completed_stage is not None else "none")
    )
    typer.echo(
        "next_recommended_stage="
        + (
            report.next_recommended_stage.stage_name.value
            if report.next_recommended_stage.stage_name is not None
            else "none"
        )
    )
    typer.echo(f"ledger_commits={report.ledger_commit_count}")
    typer.echo(f"blocking_issues={len(report.blocking_issues)}")
    typer.echo(f"warnings={len(report.warnings)}")


@app.command("validate-resume")
def validate_resume_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    start_at: Annotated[PipelineStage, typer.Option("--start-at")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
) -> None:
    """Validate a run-all resume point without executing it."""
    report = validate_resume_request(run_id=run_id, start_at_stage=start_at, root=root)
    typer.echo(f"run_id={report.run_id}")
    typer.echo(f"start_at={report.start_at_stage.value}")
    typer.echo(f"resume_status={report.resume_status.value}")
    typer.echo(f"missing_prerequisites={len(report.missing_prerequisites)}")
    typer.echo(f"warnings={len(report.warnings)}")
    if report.resume_status.value == "ResumeBlocked":
        raise typer.Exit(code=1)


def _print_dry_run_plan(plan: PipelineDryRunPlan, *, json_output: bool) -> None:
    if json_output:
        typer.echo(json.dumps(plan.model_dump(mode="json"), sort_keys=True))
        return
    would_run = _planned_status_count(plan, PlannedStageStatus.WOULD_RUN)
    read_only = _planned_status_count(plan, PlannedStageStatus.READ_ONLY_CHECK)
    would_skip = _planned_status_count(plan, PlannedStageStatus.WOULD_SKIP)
    already_complete = _planned_status_count(plan, PlannedStageStatus.ALREADY_COMPLETE)
    blocked = sum(
        1
        for stage in plan.planned_stages
        if stage.status
        in {
            PlannedStageStatus.BLOCKED_BY_PREREQUISITE,
            PlannedStageStatus.BLOCKED_BY_STOP_AFTER,
        }
    )
    typer.echo(f"run_id={plan.run_id}")
    typer.echo(f"dry_run_status={plan.dry_run_status.value}")
    typer.echo(f"planned_stages={len(plan.planned_stages)}")
    typer.echo(f"would_run={would_run + read_only}")
    typer.echo(f"would_skip={would_skip}")
    typer.echo(f"already_complete={already_complete}")
    typer.echo(f"blocked={blocked}")
    typer.echo(f"warnings={plan.warnings_count}")
    typer.echo(f"blocking_findings={plan.blocking_findings_count}")
    typer.echo("next_stage=" + (plan.next_stage.value if plan.next_stage is not None else "none"))


def _planned_status_count(
    plan: PipelineDryRunPlan,
    status: PlannedStageStatus,
) -> int:
    return sum(1 for stage in plan.planned_stages if stage.status == status)


@app.command("run-all")
def run_all_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    domain: Annotated[str | None, typer.Option("--domain")] = None,
    method: Annotated[str | None, typer.Option("--method")] = None,
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    adapter_backend: Annotated[
        str,
        typer.Option("--adapter-backend"),
    ] = DEFAULT_ADAPTER_BACKEND,
    allow_external_calls: Annotated[
        bool,
        typer.Option("--allow-external-calls"),
    ] = DEFAULT_ALLOW_EXTERNAL_CALLS,
    llm_model: Annotated[str, typer.Option("--llm-model")] = DEFAULT_LLM_MODEL,
    reviewer_backend: Annotated[
        str,
        typer.Option("--reviewer-backend"),
    ] = DEFAULT_REVIEWER_BACKEND,
    use_llm_reviewers: Annotated[
        bool,
        typer.Option("--use-llm-reviewers"),
    ] = False,
    reviewer_model: Annotated[
        str,
        typer.Option("--reviewer-model"),
    ] = DEFAULT_LLM_MODEL,
    reviewer_max_objections: Annotated[
        int,
        typer.Option("--reviewer-max-objections"),
    ] = DEFAULT_REVIEWER_MAX_OBJECTIONS,
    proof_backend: Annotated[
        str,
        typer.Option("--proof-backend"),
    ] = DEFAULT_PROOF_BACKEND,
    allow_external_tools: Annotated[
        bool,
        typer.Option("--allow-external-tools"),
    ] = DEFAULT_ALLOW_EXTERNAL_TOOLS,
    proof_executable: Annotated[
        str | None,
        typer.Option("--proof-executable"),
    ] = None,
    proof_timeout_seconds: Annotated[
        int,
        typer.Option("--proof-timeout-seconds"),
    ] = DEFAULT_PROOF_TIMEOUT_SECONDS,
    experiment_backend: Annotated[
        str,
        typer.Option("--experiment-backend"),
    ] = DEFAULT_EXPERIMENT_BACKEND,
    experiment_runner: Annotated[
        str | None,
        typer.Option("--experiment-runner"),
    ] = None,
    experiment_timeout_seconds: Annotated[
        int,
        typer.Option("--experiment-timeout-seconds"),
    ] = DEFAULT_EXPERIMENT_TIMEOUT_SECONDS,
    experiment_replications: Annotated[
        int,
        typer.Option("--experiment-replications"),
    ] = DEFAULT_EXPERIMENT_REPLICATIONS,
    stop_after: Annotated[PipelineStage | None, typer.Option("--stop-after")] = None,
    start_at: Annotated[PipelineStage | None, typer.Option("--start-at")] = None,
    skip_replay: Annotated[bool, typer.Option("--skip-replay")] = False,
    run_diagnostics: Annotated[bool, typer.Option("--run-diagnostics")] = False,
    write_replay_report: Annotated[
        bool,
        typer.Option("--write-replay-report"),
    ] = False,
    write_diagnostic_report: Annotated[
        bool,
        typer.Option("--write-diagnostic-report"),
    ] = False,
    fail_fast: Annotated[bool, typer.Option("--fail-fast")] = False,
    rerun_policy: Annotated[
        str,
        typer.Option("--rerun-policy"),
    ] = "fail-if-exists",
    force: Annotated[bool, typer.Option("--force")] = False,
    dry_run: Annotated[bool, typer.Option("--dry-run")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run the deterministic MVP pipeline directly in one process."""
    config = PipelineRunConfig(
        run_id=run_id,
        domain=domain or "",
        method=method,
        root=root,
        adapter_backend=adapter_backend,
        allow_external_calls=allow_external_calls,
        llm_model=llm_model,
        reviewer_backend=reviewer_backend,
        use_llm_reviewers=use_llm_reviewers,
        reviewer_model=reviewer_model,
        reviewer_max_objections=reviewer_max_objections,
        proof_backend=proof_backend,
        allow_external_tools=allow_external_tools,
        proof_executable=proof_executable,
        proof_timeout_seconds=proof_timeout_seconds,
        experiment_backend=experiment_backend,
        experiment_runner=experiment_runner,
        experiment_timeout_seconds=experiment_timeout_seconds,
        experiment_replications=experiment_replications,
        stop_after=stop_after,
        start_at=start_at,
        skip_replay=skip_replay,
        run_diagnostics=run_diagnostics,
        write_replay_report=write_replay_report,
        write_diagnostic_report=write_diagnostic_report,
        failure_policy=(
            PipelineFailurePolicy.FAIL_FAST if fail_fast else PipelineFailurePolicy.CONTINUE_SAFE
        ),
        rerun_policy=_parse_rerun_policy(rerun_policy),
        force=force,
    )
    if dry_run:
        plan = build_pipeline_dry_run_plan(config)
        _print_dry_run_plan(plan, json_output=json_output)
        return
    try:
        report = run_deterministic_pipeline(config)
    except PipelineRunError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    typer.echo(f"run_id={report.run_id}")
    typer.echo(f"domain={report.domain}")
    typer.echo(f"stages_run={len(report.stage_results)}")
    typer.echo(f"pipeline_status={report.pipeline_status.value}")
    typer.echo(
        "release_status="
        + (report.release_status.value if report.release_status is not None else "not_run")
    )
    typer.echo(
        "replay_status="
        + (report.replay_status.value if report.replay_status is not None else "skipped")
    )
    typer.echo(
        "diagnostic_status="
        + (report.diagnostic_status.value if report.diagnostic_status is not None else "skipped")
    )
    typer.echo(f"research_object={report.final_outputs.get('research_object', 'missing')}")
    typer.echo(f"paper_skeleton={report.final_outputs.get('paper_skeleton', 'missing')}")
    typer.echo(
        f"export_readiness_report={report.final_outputs.get('export_readiness_report', 'missing')}"
    )
    typer.echo(f"pipeline_report={report.pipeline_report_path}")
    if report.pipeline_status in {
        PipelineRunStatus.PIPELINE_BLOCKED,
        PipelineRunStatus.PIPELINE_FAILED,
    }:
        raise typer.Exit(code=1)


@app.command("plan-run")
def plan_run_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    domain: Annotated[str | None, typer.Option("--domain")] = None,
    method: Annotated[str | None, typer.Option("--method")] = None,
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    adapter_backend: Annotated[
        str,
        typer.Option("--adapter-backend"),
    ] = DEFAULT_ADAPTER_BACKEND,
    allow_external_calls: Annotated[
        bool,
        typer.Option("--allow-external-calls"),
    ] = DEFAULT_ALLOW_EXTERNAL_CALLS,
    llm_model: Annotated[str, typer.Option("--llm-model")] = DEFAULT_LLM_MODEL,
    reviewer_backend: Annotated[
        str,
        typer.Option("--reviewer-backend"),
    ] = DEFAULT_REVIEWER_BACKEND,
    use_llm_reviewers: Annotated[
        bool,
        typer.Option("--use-llm-reviewers"),
    ] = False,
    reviewer_model: Annotated[
        str,
        typer.Option("--reviewer-model"),
    ] = DEFAULT_LLM_MODEL,
    reviewer_max_objections: Annotated[
        int,
        typer.Option("--reviewer-max-objections"),
    ] = DEFAULT_REVIEWER_MAX_OBJECTIONS,
    proof_backend: Annotated[
        str,
        typer.Option("--proof-backend"),
    ] = DEFAULT_PROOF_BACKEND,
    allow_external_tools: Annotated[
        bool,
        typer.Option("--allow-external-tools"),
    ] = DEFAULT_ALLOW_EXTERNAL_TOOLS,
    proof_executable: Annotated[
        str | None,
        typer.Option("--proof-executable"),
    ] = None,
    proof_timeout_seconds: Annotated[
        int,
        typer.Option("--proof-timeout-seconds"),
    ] = DEFAULT_PROOF_TIMEOUT_SECONDS,
    experiment_backend: Annotated[
        str,
        typer.Option("--experiment-backend"),
    ] = DEFAULT_EXPERIMENT_BACKEND,
    experiment_runner: Annotated[
        str | None,
        typer.Option("--experiment-runner"),
    ] = None,
    experiment_timeout_seconds: Annotated[
        int,
        typer.Option("--experiment-timeout-seconds"),
    ] = DEFAULT_EXPERIMENT_TIMEOUT_SECONDS,
    experiment_replications: Annotated[
        int,
        typer.Option("--experiment-replications"),
    ] = DEFAULT_EXPERIMENT_REPLICATIONS,
    stop_after: Annotated[PipelineStage | None, typer.Option("--stop-after")] = None,
    start_at: Annotated[PipelineStage | None, typer.Option("--start-at")] = None,
    skip_replay: Annotated[bool, typer.Option("--skip-replay")] = False,
    run_diagnostics: Annotated[bool, typer.Option("--run-diagnostics")] = False,
    write_replay_report: Annotated[
        bool,
        typer.Option("--write-replay-report"),
    ] = False,
    write_diagnostic_report: Annotated[
        bool,
        typer.Option("--write-diagnostic-report"),
    ] = False,
    fail_fast: Annotated[bool, typer.Option("--fail-fast")] = False,
    rerun_policy: Annotated[
        str,
        typer.Option("--rerun-policy"),
    ] = "fail-if-exists",
    force: Annotated[bool, typer.Option("--force")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Plan run-all execution without mutating provenance."""
    config = PipelineRunConfig(
        run_id=run_id,
        domain=domain or "",
        method=method,
        root=root,
        adapter_backend=adapter_backend,
        allow_external_calls=allow_external_calls,
        llm_model=llm_model,
        reviewer_backend=reviewer_backend,
        use_llm_reviewers=use_llm_reviewers,
        reviewer_model=reviewer_model,
        reviewer_max_objections=reviewer_max_objections,
        proof_backend=proof_backend,
        allow_external_tools=allow_external_tools,
        proof_executable=proof_executable,
        proof_timeout_seconds=proof_timeout_seconds,
        experiment_backend=experiment_backend,
        experiment_runner=experiment_runner,
        experiment_timeout_seconds=experiment_timeout_seconds,
        experiment_replications=experiment_replications,
        stop_after=stop_after,
        start_at=start_at,
        skip_replay=skip_replay,
        run_diagnostics=run_diagnostics,
        write_replay_report=write_replay_report,
        write_diagnostic_report=write_diagnostic_report,
        failure_policy=(
            PipelineFailurePolicy.FAIL_FAST if fail_fast else PipelineFailurePolicy.CONTINUE_SAFE
        ),
        rerun_policy=_parse_rerun_policy(rerun_policy),
        force=force,
    )
    _print_dry_run_plan(build_pipeline_dry_run_plan(config), json_output=json_output)


@app.command("run-stage-a")
def run_stage_a_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    domain: Annotated[str, typer.Option("--domain")],
    method: Annotated[str | None, typer.Option("--method")] = None,
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    adapter_backend: Annotated[
        str,
        typer.Option("--adapter-backend"),
    ] = DEFAULT_ADAPTER_BACKEND,
    allow_external_calls: Annotated[
        bool,
        typer.Option("--allow-external-calls"),
    ] = DEFAULT_ALLOW_EXTERNAL_CALLS,
    llm_model: Annotated[str, typer.Option("--llm-model")] = DEFAULT_LLM_MODEL,
    rerun_policy: Annotated[
        str,
        typer.Option("--rerun-policy"),
    ] = "fail-if-exists",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Run Stage 0/A with fake defaults or an explicitly gated real LLM."""
    if not _guard_mutating_stage(
        root=root,
        run_id=run_id,
        stage=PipelineStage.RUN_STAGE_A,
        rerun_policy=rerun_policy,
        force=force,
    ):
        return
    try:
        registry = get_adapter_registry(
            AdapterConfig(
                adapter_backend=adapter_backend,
                allow_external_calls=allow_external_calls,
                llm_model=llm_model,
            )
        )
    except (AdapterConfigurationError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    _ensure_run_initialized(root, run_id)
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    result = run_stage_a(
        run_id=run_id,
        constraints=constraint_from_inputs(domain=domain, method=method),
        store=store,
        ledger=ledger,
        llm_client=registry.llm if registry.config.adapter_backend != "fake" else None,
    )
    typer.echo(f"generated_candidates={len(result.generated_candidates)}")
    typer.echo(f"deferred_by_data_gate={len(result.deferred_candidates)}")
    typer.echo(f"pruned_duplicates={len(result.duplicate_decisions)}")
    typer.echo(f"passing_stage_a={len(result.survivors)}")
    typer.echo(f"stage_a_report={result.report_artifact.path}")
    typer.echo(f"adapter_backend={result.adapter_metadata['backend']}")


@app.command("run-stage-b")
def run_stage_b_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    retrieval_backend: Annotated[
        str,
        typer.Option("--retrieval-backend"),
    ] = DEFAULT_RETRIEVAL_BACKEND,
    allow_external_calls: Annotated[
        bool,
        typer.Option("--allow-external-calls"),
    ] = DEFAULT_ALLOW_EXTERNAL_CALLS,
    retrieval_limit: Annotated[
        int,
        typer.Option("--retrieval-limit"),
    ] = DEFAULT_RETRIEVAL_LIMIT,
    reviewer_backend: Annotated[
        str,
        typer.Option("--reviewer-backend"),
    ] = DEFAULT_REVIEWER_BACKEND,
    use_llm_reviewers: Annotated[
        bool,
        typer.Option("--use-llm-reviewers"),
    ] = False,
    reviewer_model: Annotated[
        str,
        typer.Option("--reviewer-model"),
    ] = DEFAULT_LLM_MODEL,
    reviewer_max_objections: Annotated[
        int,
        typer.Option("--reviewer-max-objections"),
    ] = DEFAULT_REVIEWER_MAX_OBJECTIONS,
    rerun_policy: Annotated[
        str,
        typer.Option("--rerun-policy"),
    ] = "fail-if-exists",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Run Stage B with fake defaults and explicitly gated external adapters."""
    if not _guard_mutating_stage(
        root=root,
        run_id=run_id,
        stage=PipelineStage.RUN_STAGE_B,
        rerun_policy=rerun_policy,
        force=force,
    ):
        return
    try:
        registry = get_adapter_registry(
            AdapterConfig(
                retrieval_backend=retrieval_backend,
                allow_external_calls=allow_external_calls,
                retrieval_limit=retrieval_limit,
                reviewer_backend=reviewer_backend,
                use_llm_reviewers=use_llm_reviewers,
                reviewer_model=reviewer_model,
                reviewer_max_objections=reviewer_max_objections,
            )
        )
    except (AdapterConfigurationError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = run_stage_b(
            run_id=run_id,
            store=store,
            ledger=ledger,
            retrieval_client=(
                registry.retrieval if registry.config.retrieval_backend != "fake" else None
            ),
            reviewer_client=(registry.reviewer if use_llm_reviewers else None),
        )
    except StageBError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"stage_a_survivors={len(result.stage_a_survivors)}")
    typer.echo(f"stage_b_children={len(result.children)}")
    typer.echo(f"rejected_bridge={len(result.rejected_bridge)}")
    typer.echo(f"rejected_review={len(result.rejected_review)}")
    typer.echo(f"rejected_baseline={len(result.rejected_baseline)}")
    typer.echo(f"insufficient_retrieval={len(result.insufficient_retrieval)}")
    typer.echo(f"passing_stage_b={len(result.survivors)}")
    typer.echo(f"stage_b_report={result.report_artifact.path}")
    typer.echo(f"reviewer_backend={result.reviewer_adapter_metadata['backend']}")
    typer.echo(f"retrieval_backend={registry.config.retrieval_backend}")


@app.command("select-stage-c")
def select_stage_c_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    rerun_policy: Annotated[
        str,
        typer.Option("--rerun-policy"),
    ] = "fail-if-exists",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Run deterministic Stage B-to-C filtering and Stage C candidate selection."""
    if not _guard_mutating_stage(
        root=root,
        run_id=run_id,
        stage=PipelineStage.SELECT_STAGE_C,
        rerun_policy=rerun_policy,
        force=force,
    ):
        return
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = run_stage_c_selection(run_id=run_id, store=store, ledger=ledger)
    except StageCSelectionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"stage_b_survivors={len(result.stage_b_survivors)}")
    typer.echo(f"rejected_redteam={len(result.rejected_redteam)}")
    typer.echo(f"pruned_uncertain={len(result.pruned_uncertain)}")
    typer.echo(f"insufficient_retrieval={len(result.insufficient_retrieval)}")
    typer.echo(f"deferred_data={len(result.deferred_data)}")
    typer.echo(f"budget_deferred={len(result.budget_deferred)}")
    typer.echo(f"stage_c_ready={len(result.selected_candidates)}")
    typer.echo(f"stage_c_selection_report={result.report_artifact.path}")


@app.command("run-stage-c")
def run_stage_c_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    proof_backend: Annotated[
        str,
        typer.Option("--proof-backend"),
    ] = DEFAULT_PROOF_BACKEND,
    allow_external_tools: Annotated[
        bool,
        typer.Option("--allow-external-tools"),
    ] = DEFAULT_ALLOW_EXTERNAL_TOOLS,
    proof_executable: Annotated[
        str | None,
        typer.Option("--proof-executable"),
    ] = None,
    proof_timeout_seconds: Annotated[
        int,
        typer.Option("--proof-timeout-seconds"),
    ] = DEFAULT_PROOF_TIMEOUT_SECONDS,
    experiment_backend: Annotated[
        str,
        typer.Option("--experiment-backend"),
    ] = DEFAULT_EXPERIMENT_BACKEND,
    experiment_runner: Annotated[
        str | None,
        typer.Option("--experiment-runner"),
    ] = None,
    experiment_timeout_seconds: Annotated[
        int,
        typer.Option("--experiment-timeout-seconds"),
    ] = DEFAULT_EXPERIMENT_TIMEOUT_SECONDS,
    experiment_replications: Annotated[
        int,
        typer.Option("--experiment-replications"),
    ] = DEFAULT_EXPERIMENT_REPLICATIONS,
    rerun_policy: Annotated[
        str,
        typer.Option("--rerun-policy"),
    ] = "fail-if-exists",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Run deterministic fake Stage C verification."""
    if not _guard_mutating_stage(
        root=root,
        run_id=run_id,
        stage=PipelineStage.RUN_STAGE_C,
        rerun_policy=rerun_policy,
        force=force,
    ):
        return
    try:
        registry = get_adapter_registry(
            AdapterConfig(
                proof_backend=proof_backend,
                allow_external_tools=allow_external_tools,
                proof_executable=proof_executable,
                proof_timeout_seconds=proof_timeout_seconds,
                experiment_backend=experiment_backend,
                experiment_runner=experiment_runner,
                experiment_timeout_seconds=experiment_timeout_seconds,
                experiment_replications=experiment_replications,
            )
        )
    except (AdapterConfigurationError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = run_stage_c(
            run_id=run_id,
            store=store,
            ledger=ledger,
            proof_verifier=(
                registry.proof_verifier if registry.config.proof_backend != "fake" else None
            ),
            experiment_runner=(
                registry.experiment_runner if registry.config.experiment_backend != "fake" else None
            ),
        )
    except StageCError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    labels = [record.label for record in result.verification_records.values()]
    fake_proof_runs = sum(
        1 for proof_result in result.proof_results.values() if getattr(proof_result, "fake", False)
    )
    typer.echo(f"stage_c_ready={len(result.stage_c_ready_candidates)}")
    typer.echo(f"fake_proof_runs={fake_proof_runs}")
    typer.echo(f"real_proof_runs={len(result.proof_results) - fake_proof_runs}")
    fake_experiment_runs = sum(
        1
        for experiment_result in result.experiment_results.values()
        if getattr(experiment_result, "fake", False)
    )
    typer.echo(f"fake_synthetic_experiments={fake_experiment_runs}")
    typer.echo(
        f"real_synthetic_experiments={len(result.experiment_results) - fake_experiment_runs}"
    )
    typer.echo(f"lean_verified={labels.count(VerificationLabel.LEAN_VERIFIED)}")
    typer.echo(
        "synthetic_experiment_verified="
        f"{labels.count(VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED)}"
    )
    typer.echo(f"negative_results={labels.count(VerificationLabel.NEGATIVE_RESULT)}")
    typer.echo(f"conjectures={labels.count(VerificationLabel.CONJECTURE)}")
    typer.echo(f"limitations={labels.count(VerificationLabel.LIMITATION)}")
    typer.echo(f"unsupported={labels.count(VerificationLabel.UNSUPPORTED)}")
    typer.echo(f"stage_c_report={result.report_artifact.path}")
    typer.echo(f"proof_backend={result.proof_backend_metadata['backend']}")
    typer.echo(f"experiment_backend={result.experiment_backend_metadata['backend']}")


@app.command("synthesize-abstract")
def synthesize_abstract_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    rerun_policy: Annotated[
        str,
        typer.Option("--rerun-policy"),
    ] = "fail-if-exists",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Run deterministic abstract synthesis and final nucleus selection."""
    if not _guard_mutating_stage(
        root=root,
        run_id=run_id,
        stage=PipelineStage.SYNTHESIZE_ABSTRACT,
        rerun_policy=rerun_policy,
        force=force,
    ):
        return
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = run_abstract_synthesis(run_id=run_id, store=store, ledger=ledger)
    except AbstractSynthesisError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"stage_c_results={len(result.stage_c_results)}")
    typer.echo(f"abstract_models_proposed={len(result.abstract_models)}")
    typer.echo(f"abstract_models_passed={len(result.passing_abstractions)}")
    typer.echo(f"final_nucleus_type={result.final_nucleus.nucleus_type.value}")
    typer.echo(f"final_nucleus_id={result.final_nucleus.id}")
    typer.echo(f"abstract_synthesis_report={result.report_artifact.path}")


@app.command("plan-manuscript")
def plan_manuscript_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    rerun_policy: Annotated[
        str,
        typer.Option("--rerun-policy"),
    ] = "fail-if-exists",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Build deterministic manuscript planning artifacts."""
    if not _guard_mutating_stage(
        root=root,
        run_id=run_id,
        stage=PipelineStage.PLAN_MANUSCRIPT,
        rerun_policy=rerun_policy,
        force=force,
    ):
        return
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = run_manuscript_planning(run_id=run_id, store=store, ledger=ledger)
    except ManuscriptPlanError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    claims_allowed = len(result.manuscript_plan.allowed_claim_ids)
    claims_blocked = len(result.blocked_claims)
    typer.echo(f"final_nucleus_type={result.final_nucleus.nucleus_type.value}")
    typer.echo(f"claims_total={len(result.claim_table.claims)}")
    typer.echo(f"claims_allowed={claims_allowed}")
    typer.echo(f"claims_blocked={claims_blocked}")
    typer.echo(f"manuscript_plan={result.markdown_artifact.path}")
    typer.echo(f"claim_table={result.claim_table_artifact.path}")


@app.command("critique-paper-shape")
def critique_paper_shape_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    write_report: Annotated[bool, typer.Option("--write-report")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Critique manuscript narrative shape without changing scientific labels."""
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        inputs = load_narrative_inputs(run_id, ledger)
    except NarrativeContractError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    contract = build_narrative_contract(
        inputs.manuscript_plan,
        inputs.final_nucleus,
        inputs.claim_table,
        run_id=run_id,
    )
    critique = critique_paper_shape(contract, inputs.manuscript_plan)
    artifacts = None
    if write_report:
        artifacts = write_paper_shape_reports(
            run_id=run_id,
            contract=contract,
            critique=critique,
            store=store,
            ledger=ledger,
        )
    if json_output:
        payload = {
            "contract": contract.model_dump(mode="json"),
            "critique": critique.model_dump(mode="json"),
            "artifacts": (
                {
                    "narrative_contract": artifacts.narrative_contract_artifact.model_dump(
                        mode="json"
                    ),
                    "paper_shape_critique": artifacts.critique_json_artifact.model_dump(
                        mode="json"
                    ),
                    "paper_shape_critique_markdown": (
                        artifacts.critique_markdown_artifact.model_dump(mode="json")
                    ),
                }
                if artifacts is not None
                else None
            ),
        }
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"paper_shape_status={critique.status.value}")
    typer.echo(f"paper_shape_score={critique.score.total:.3f}")
    typer.echo(f"missing_items={len(critique.missing_items)}")
    typer.echo(f"warnings={len(critique.warnings)}")
    typer.echo(f"is_verification_evidence={str(critique.is_verification_evidence).lower()}")
    if artifacts is not None:
        typer.echo(f"narrative_contract={artifacts.narrative_contract_artifact.path}")
        typer.echo(f"paper_shape_critique={artifacts.critique_markdown_artifact.path}")


@app.command("generate-section-draft")
def generate_section_draft_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    section_id: Annotated[str, typer.Option("--section-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    prose_backend: Annotated[
        str,
        typer.Option("--prose-backend"),
    ] = DEFAULT_PROSE_BACKEND,
    allow_external_calls: Annotated[
        bool,
        typer.Option("--allow-external-calls"),
    ] = DEFAULT_ALLOW_EXTERNAL_CALLS,
    prose_model: Annotated[str, typer.Option("--prose-model")] = DEFAULT_LLM_MODEL,
    max_words: Annotated[int, typer.Option("--max-words")] = 160,
    write_report: Annotated[bool, typer.Option("--write-report")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Generate and validate one manuscript section draft."""
    try:
        registry = get_adapter_registry(
            AdapterConfig(
                prose_backend=prose_backend,
                allow_external_calls=allow_external_calls,
                prose_model=prose_model,
            )
        )
    except (AdapterConfigurationError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = generate_section_draft(
            run_id=run_id,
            section_id=section_id,
            store=store,
            ledger=ledger,
            prose_generator=registry.prose_generator,
            write_report=write_report,
            max_words=max_words,
        )
    except SectionDraftGenerationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "section_contract": result.section_contract.model_dump(mode="json"),
                    "draft": result.draft.model_dump(mode="json"),
                    "safety_report": result.safety_report.model_dump(mode="json"),
                    "artifacts": {
                        "request": result.request_artifact.model_dump(mode="json")
                        if result.request_artifact
                        else None,
                        "response": result.response_artifact.model_dump(mode="json")
                        if result.response_artifact
                        else None,
                        "draft": result.draft_artifact.model_dump(mode="json")
                        if result.draft_artifact
                        else None,
                        "safety": result.safety_artifact.model_dump(mode="json")
                        if result.safety_artifact
                        else None,
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"section_id={result.section_contract.section_id}")
    typer.echo(f"prose_backend={registry.config.prose_backend}")
    typer.echo(f"used_claims={len(result.safety_report.used_claim_ids)}")
    typer.echo(f"used_evidence={len(result.safety_report.used_evidence_artifact_ids)}")
    typer.echo(f"safe={str(result.safety_report.safe).lower()}")
    typer.echo(f"rejected={str(result.safety_report.rejected).lower()}")
    typer.echo(f"warnings={len(result.safety_report.warnings)}")
    if result.draft_artifact is not None:
        typer.echo(f"section_draft={result.draft_artifact.path}")
    if result.safety_artifact is not None:
        typer.echo(f"section_safety={result.safety_artifact.path}")


@app.command("draft-manuscript")
def draft_manuscript_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    prose_backend: Annotated[
        str,
        typer.Option("--prose-backend"),
    ] = DEFAULT_PROSE_BACKEND,
    allow_external_calls: Annotated[
        bool,
        typer.Option("--allow-external-calls"),
    ] = DEFAULT_ALLOW_EXTERNAL_CALLS,
    prose_model: Annotated[str, typer.Option("--prose-model")] = DEFAULT_LLM_MODEL,
    max_words: Annotated[int, typer.Option("--max-words")] = 160,
    include_citations: Annotated[bool, typer.Option("--include-citations")] = False,
    write_report: Annotated[bool, typer.Option("--write-report")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Draft all manuscript sections and assemble a Markdown manuscript."""
    try:
        registry = get_adapter_registry(
            AdapterConfig(
                prose_backend=prose_backend,
                allow_external_calls=allow_external_calls,
                prose_model=prose_model,
            )
        )
    except (AdapterConfigurationError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = draft_manuscript(
            run_id=run_id,
            store=store,
            ledger=ledger,
            prose_generator=registry.prose_generator,
            write_report=write_report,
            include_citations=include_citations,
            max_words=max_words,
        )
    except ManuscriptDraftingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "drafting_plan": result.drafting_plan.model_dump(mode="json"),
                    "drafting_report": result.drafting_report.model_dump(mode="json"),
                    "complete_draft": result.complete_draft.model_dump(mode="json"),
                    "assembly_report": result.assembly_report.model_dump(mode="json"),
                    "citation_registry": (
                        result.citation_registry.model_dump(mode="json")
                        if result.citation_registry
                        else None
                    ),
                    "literature_positioning_report": (
                        result.literature_positioning_report.model_dump(mode="json")
                        if result.literature_positioning_report
                        else None
                    ),
                    "citation_safety_report": (
                        result.citation_safety_report.model_dump(mode="json")
                        if result.citation_safety_report
                        else None
                    ),
                    "artifacts": {
                        "plan": result.plan_artifact.model_dump(mode="json")
                        if result.plan_artifact
                        else None,
                        "drafting_report": (
                            result.drafting_report_artifact.model_dump(mode="json")
                            if result.drafting_report_artifact
                            else None
                        ),
                        "complete_draft": (
                            result.markdown_artifact.model_dump(mode="json")
                            if result.markdown_artifact
                            else None
                        ),
                        "assembly_report": (
                            result.assembly_report_artifact.model_dump(mode="json")
                            if result.assembly_report_artifact
                            else None
                        ),
                        "citation_registry": (
                            result.citation_registry_artifact.model_dump(mode="json")
                            if result.citation_registry_artifact
                            else None
                        ),
                        "literature_positioning": (
                            result.literature_positioning_artifact.model_dump(mode="json")
                            if result.literature_positioning_artifact
                            else None
                        ),
                        "citation_safety": (
                            result.citation_safety_artifact.model_dump(mode="json")
                            if result.citation_safety_artifact
                            else None
                        ),
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"prose_backend={registry.config.prose_backend}")
    typer.echo(f"sections={result.drafting_report.sections_total}")
    typer.echo(f"safe_sections={result.drafting_report.sections_safe}")
    typer.echo(f"unsafe_sections={result.drafting_report.sections_unsafe}")
    typer.echo(f"draft_status={result.drafting_report.draft_status.value}")
    typer.echo(f"include_citations={str(include_citations).lower()}")
    if result.citation_registry is not None:
        typer.echo(f"citations={len(result.citation_registry.citations)}")
        typer.echo(
            f"citation_safety={str(result.citation_safety_report.safe).lower()}"
            if result.citation_safety_report is not None
            else "citation_safety=missing"
        )
    typer.echo(f"warnings={len(result.drafting_report.warnings)}")
    if result.markdown_artifact is not None:
        typer.echo(f"complete_manuscript_draft={result.markdown_artifact.path}")
    if result.drafting_report_artifact is not None:
        typer.echo(f"manuscript_drafting_report={result.drafting_report_artifact.path}")


@app.command("build-citation-registry")
def build_citation_registry_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    write_report: Annotated[bool, typer.Option("--write-report")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Build citation and literature-positioning reports from retrieval metadata."""
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        inputs = load_manuscript_drafting_inputs(run_id, ledger)
    except ManuscriptDraftingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    registry = build_citation_registry_from_ledger(run_id, ledger)
    positioning = build_literature_positioning_report(
        run_id=run_id,
        citation_registry=registry,
        narrative_contract=inputs.narrative_contract,
    )
    safety = validate_citation_usage(positioning.markdown_intro_paragraph, registry)
    artifacts = None
    if write_report:
        artifacts = write_citation_registry_reports(
            run_id=run_id,
            store=store,
            ledger=ledger,
            citation_registry=registry,
            literature_positioning_report=positioning,
            citation_safety_report=safety,
        )
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "citation_registry": registry.model_dump(mode="json"),
                    "literature_positioning_report": positioning.model_dump(mode="json"),
                    "citation_safety_report": safety.model_dump(mode="json"),
                    "artifacts": (
                        {
                            "citation_registry": (
                                artifacts.citation_registry_artifact.model_dump(mode="json")
                            ),
                            "literature_positioning": (
                                artifacts.literature_positioning_artifact.model_dump(mode="json")
                            ),
                            "citation_safety": (
                                artifacts.citation_safety_artifact.model_dump(mode="json")
                            ),
                        }
                        if artifacts is not None
                        else None
                    ),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"citations={len(registry.citations)}")
    typer.echo(f"warnings={len(registry.warnings) + len(safety.warnings)}")
    typer.echo(f"citation_safety={str(safety.safe).lower()}")
    typer.echo("is_verification_evidence=false")
    typer.echo("proves_novelty=false")
    if artifacts is not None:
        typer.echo(f"citation_registry={artifacts.citation_registry_artifact.path}")
        typer.echo(f"literature_positioning={artifacts.literature_positioning_artifact.path}")
        typer.echo(f"citation_safety={artifacts.citation_safety_artifact.path}")


@app.command("export-latex")
def export_latex_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    write_report: Annotated[bool, typer.Option("--write-report")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    render_check: Annotated[bool, typer.Option("--render-check")] = False,
    allow_external_tools: Annotated[
        bool,
        typer.Option("--allow-external-tools"),
    ] = DEFAULT_ALLOW_EXTERNAL_TOOLS,
    latex_executable: Annotated[
        str | None,
        typer.Option("--latex-executable"),
    ] = None,
) -> None:
    """Export a complete Markdown manuscript draft to LaTeX presentation artifacts."""
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = export_latex_from_run(
            run_id=run_id,
            store=store,
            ledger=ledger,
            root=root,
            write_report=write_report,
            render_check=render_check,
            allow_external_tools=allow_external_tools,
            latex_executable=latex_executable,
        )
    except (LatexExportError, LatexRenderError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    export_result = result.export_result
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "latex_export_result": export_result.model_dump(mode="json"),
                    "artifacts": {
                        "paper": result.paper_artifact.model_dump(mode="json")
                        if result.paper_artifact
                        else None,
                        "references": (
                            result.bibliography_artifact.model_dump(mode="json")
                            if result.bibliography_artifact
                            else None
                        ),
                        "source_map": (
                            result.source_map_artifact.model_dump(mode="json")
                            if result.source_map_artifact
                            else None
                        ),
                        "export_report": (
                            result.export_report_artifact.model_dump(mode="json")
                            if result.export_report_artifact
                            else None
                        ),
                        "safety_report": (
                            result.safety_report_artifact.model_dump(mode="json")
                            if result.safety_report_artifact
                            else None
                        ),
                        "compile_check": (
                            result.compile_check_artifact.model_dump(mode="json")
                            if result.compile_check_artifact
                            else None
                        ),
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    typer.echo(f"run_id={run_id}")
    typer.echo(f"latex_safe={str(export_result.safety_report.safe).lower()}")
    typer.echo(f"latex_rejected={str(export_result.safety_report.rejected).lower()}")
    typer.echo(f"source_map_entries={len(export_result.source_map.entries)}")
    typer.echo(f"citations_used={len(export_result.safety_report.used_citation_keys)}")
    typer.echo(f"warnings={len(export_result.warnings)}")
    typer.echo(f"render_check={str(render_check).lower()}")
    typer.echo(
        "render_passed="
        + (
            str(export_result.render_result.passed).lower()
            if export_result.render_result is not None
            else "not_run"
        )
    )
    typer.echo("is_verification_evidence=false")
    typer.echo("creates_scientific_validation=false")
    if result.paper_artifact is not None:
        typer.echo(f"paper_tex={result.paper_artifact.path}")
    if result.bibliography_artifact is not None:
        typer.echo(f"references_bib={result.bibliography_artifact.path}")
    if result.source_map_artifact is not None:
        typer.echo(f"latex_source_map={result.source_map_artifact.path}")


@app.command("critique-paper")
def critique_paper_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    write_report: Annotated[bool, typer.Option("--write-report")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Critique generated Markdown/LaTeX paper artifacts without scientific authority."""
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = critique_paper_from_run(
            run_id=run_id,
            root=root,
            store=store,
            ledger=ledger,
            write_report=write_report,
        )
    except PaperCriticError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    report = result.critic_report
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "paper_critic_report": report.model_dump(mode="json"),
                    "artifacts": {
                        "paper_critic_report": (
                            result.critic_report_artifact.model_dump(mode="json")
                            if result.critic_report_artifact
                            else None
                        )
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"findings={report.findings_count}")
    typer.echo(f"blocking_findings={report.blocking_findings}")
    typer.echo(f"major_findings={report.major_findings}")
    typer.echo(f"warning_findings={report.warning_findings}")
    typer.echo("publication_ready=false")
    typer.echo("is_verification_evidence=false")
    typer.echo("creates_scientific_validation=false")
    if result.critic_report_artifact is not None:
        typer.echo(f"paper_critic_report={result.critic_report_artifact.path}")


@app.command("revise-paper")
def revise_paper_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    write_report: Annotated[bool, typer.Option("--write-report")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    apply_safe_fake_revision: Annotated[
        bool,
        typer.Option("--apply-safe-fake-revision"),
    ] = False,
) -> None:
    """Plan or apply one deterministic safe fake revision pass."""
    if write_report and not apply_safe_fake_revision:
        typer.echo(
            "revise-paper --write-report requires --apply-safe-fake-revision",
            err=True,
        )
        raise typer.Exit(code=1)
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = revise_paper_from_run(
            run_id=run_id,
            root=root,
            store=store,
            ledger=ledger,
            apply_safe_fake_revision_flag=apply_safe_fake_revision,
            write_report=write_report,
        )
    except PaperCriticError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    revision_result = result.revision_result
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "paper_critic_report": result.critic_report.model_dump(mode="json"),
                    "paper_revision_plan": result.revision_plan.model_dump(mode="json"),
                    "paper_revision_result": (
                        revision_result.model_dump(mode="json")
                        if revision_result is not None
                        else None
                    ),
                    "artifacts": {
                        "paper_critic_report": (
                            result.critic_report_artifact.model_dump(mode="json")
                            if result.critic_report_artifact
                            else None
                        ),
                        "paper_revision_plan": (
                            result.revision_plan_artifact.model_dump(mode="json")
                            if result.revision_plan_artifact
                            else None
                        ),
                        "revision_safety_report": (
                            result.revision_safety_artifact.model_dump(mode="json")
                            if result.revision_safety_artifact
                            else None
                        ),
                        "revised_manuscript_draft": (
                            result.revised_markdown_artifact.model_dump(mode="json")
                            if result.revised_markdown_artifact
                            else None
                        ),
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"actions={len(result.revision_plan.actions)}")
    typer.echo(f"safe_to_apply={str(result.revision_plan.safe_to_apply).lower()}")
    typer.echo(
        "revision_status="
        + (
            revision_result.revision_status.value
            if revision_result is not None
            else "planning_only"
        )
    )
    typer.echo(
        "patches=" + (str(len(revision_result.patches)) if revision_result is not None else "0")
    )
    typer.echo("publication_ready=false")
    typer.echo("is_verification_evidence=false")
    typer.echo("creates_scientific_validation=false")
    if result.revision_plan_artifact is not None:
        typer.echo(f"paper_revision_plan={result.revision_plan_artifact.path}")
    if result.revision_safety_artifact is not None:
        typer.echo(f"revision_safety_report={result.revision_safety_artifact.path}")
    if result.revised_markdown_artifact is not None:
        typer.echo(f"revised_manuscript_draft={result.revised_markdown_artifact.path}")


@app.command("generate-paper")
def generate_paper_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    write_report: Annotated[bool, typer.Option("--write-report")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    include_citations: Annotated[bool, typer.Option("--include-citations")] = True,
    export_latex: Annotated[bool, typer.Option("--export-latex")] = True,
    critique: Annotated[bool, typer.Option("--critique")] = True,
    revise: Annotated[bool, typer.Option("--revise")] = False,
    apply_safe_fake_revision: Annotated[
        bool,
        typer.Option("--apply-safe-fake-revision"),
    ] = False,
    reexport_latex_after_revision: Annotated[
        bool,
        typer.Option("--reexport-latex-after-revision"),
    ] = False,
    render_check: Annotated[bool, typer.Option("--render-check")] = False,
    allow_external_tools: Annotated[
        bool,
        typer.Option("--allow-external-tools"),
    ] = DEFAULT_ALLOW_EXTERNAL_TOOLS,
    latex_executable: Annotated[
        str | None,
        typer.Option("--latex-executable"),
    ] = None,
    prose_backend: Annotated[
        str,
        typer.Option("--prose-backend"),
    ] = DEFAULT_PROSE_BACKEND,
    allow_external_calls: Annotated[
        bool,
        typer.Option("--allow-external-calls"),
    ] = DEFAULT_ALLOW_EXTERNAL_CALLS,
    prose_model: Annotated[str, typer.Option("--prose-model")] = DEFAULT_LLM_MODEL,
    quality_repair_backend: Annotated[
        str,
        typer.Option("--quality-repair-backend"),
    ] = "off",
    quality_repair_model: Annotated[
        str,
        typer.Option("--quality-repair-model"),
    ] = DEFAULT_LLM_MODEL,
    rerun_policy: Annotated[
        str,
        typer.Option("--rerun-policy"),
    ] = "fail-if-exists",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Generate a complete non-evidence paper package from an existing run."""
    try:
        policy = _parse_rerun_policy(rerun_policy)
        registry = get_adapter_registry(
            AdapterConfig(
                prose_backend=prose_backend,
                allow_external_calls=allow_external_calls,
                prose_model=prose_model,
            )
        )
    except (AdapterConfigurationError, ValueError, typer.BadParameter) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    config = FullPaperGenerationConfig(
        run_id=run_id,
        include_citations=include_citations,
        export_latex=export_latex,
        critique=critique,
        revise=revise,
        apply_safe_fake_revision=apply_safe_fake_revision,
        reexport_latex_after_revision=reexport_latex_after_revision,
        render_check=render_check,
        allow_external_tools=allow_external_tools,
        latex_executable=latex_executable,
        prose_backend=prose_backend,
        allow_external_calls=allow_external_calls,
        prose_model=prose_model,
        quality_repair_backend=quality_repair_backend,
        quality_repair_model=quality_repair_model,
        write_report=write_report,
        rerun_policy=policy,
        force=force,
    )
    try:
        result = generate_full_paper(
            run_id=run_id,
            root=root,
            store=store,
            ledger=ledger,
            prose_generator=registry.prose_generator,
            config=config,
        )
    except FullPaperGenerationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    result_model = full_paper_generation_result_model(result)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "full_paper_generation_result": result_model.model_dump(mode="json"),
                    "artifacts": {
                        "full_paper_generation_report": (
                            result.report_artifact.model_dump(mode="json")
                            if result.report_artifact
                            else None
                        ),
                        "full_paper_artifact_bundle": (
                            result.bundle_artifact.model_dump(mode="json")
                            if result.bundle_artifact
                            else None
                        ),
                        "revised_paper": (
                            result.revised_latex_artifact.model_dump(mode="json")
                            if result.revised_latex_artifact
                            else None
                        ),
                        "revised_references": (
                            result.revised_references_artifact.model_dump(mode="json")
                            if result.revised_references_artifact
                            else None
                        ),
                        "revised_source_map": (
                            result.revised_source_map_artifact.model_dump(mode="json")
                            if result.revised_source_map_artifact
                            else None
                        ),
                        "quality_repair_report": (
                            result.quality_repair_report_artifact.model_dump(mode="json")
                            if result.quality_repair_report_artifact
                            else None
                        ),
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    report = result.report
    bundle = result.artifact_bundle
    typer.echo(f"run_id={run_id}")
    typer.echo(f"paper_generation_status={report.generation_status.value}")
    typer.echo(f"steps={len(report.steps)}")
    typer.echo(f"warnings={len(report.warnings)}")
    typer.echo(f"blocking_issues={len(report.blocking_issues)}")
    typer.echo(f"include_citations={str(include_citations).lower()}")
    typer.echo(f"export_latex={str(export_latex).lower()}")
    typer.echo(f"revision_applied={str(report.revision_applied).lower()}")
    typer.echo(f"quality_repair_backend={quality_repair_backend}")
    typer.echo(f"render_check={str(render_check).lower()}")
    typer.echo("publication_ready=false")
    typer.echo("is_verification_evidence=false")
    typer.echo("creates_scientific_validation=false")
    typer.echo(f"citation_registry={bundle.citation_registry_artifact_id or 'missing'}")
    typer.echo(
        f"complete_manuscript_draft={bundle.complete_manuscript_draft_artifact_id or 'missing'}"
    )
    typer.echo(f"latex_artifact={bundle.latex_artifact_id or 'missing'}")
    typer.echo(f"paper_critic_report={bundle.paper_critic_report_artifact_id or 'missing'}")
    if bundle.revised_manuscript_draft_artifact_id is not None:
        typer.echo(f"revised_manuscript_draft={bundle.revised_manuscript_draft_artifact_id}")
    if bundle.quality_repair_report_artifact_id is not None:
        typer.echo(f"quality_repair_report={bundle.quality_repair_report_artifact_id}")
    if result.report_artifact is not None:
        typer.echo(f"full_paper_generation_report={result.report_artifact.path}")
    if result.bundle_artifact is not None:
        typer.echo(f"full_paper_artifact_bundle={result.bundle_artifact.path}")


@app.command("evaluate-paper-release")
def evaluate_paper_release_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    write_report: Annotated[bool, typer.Option("--write-report")] = False,
    max_major_findings: Annotated[int, typer.Option("--max-major-findings")] = 0,
    allow_warnings: Annotated[bool, typer.Option("--allow-warnings")] = True,
    require_latex_export: Annotated[
        bool,
        typer.Option("--require-latex-export"),
    ] = True,
    require_citations: Annotated[bool, typer.Option("--require-citations")] = False,
    require_revision_status: Annotated[
        bool,
        typer.Option("--require-revision-status"),
    ] = False,
) -> None:
    """Evaluate generated-paper readiness for human review only."""
    config = FullPaperReleaseGateConfig(
        run_id=run_id,
        max_major_findings=max_major_findings,
        allow_warnings=allow_warnings,
        require_latex_export=require_latex_export,
        require_citations=require_citations,
        require_revision_status=require_revision_status,
        write_report=write_report,
    )
    try:
        result = run_full_paper_release_gate(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            config=config,
        )
    except FullPaperReleaseError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "full_paper_release_report": result.report.model_dump(mode="json"),
                    "artifacts": {
                        "release_report": (
                            result.report_artifact.model_dump(mode="json")
                            if result.report_artifact
                            else None
                        ),
                        "completeness_report": (
                            result.completeness_artifact.model_dump(mode="json")
                            if result.completeness_artifact
                            else None
                        ),
                        "evidence_boundary_report": (
                            result.evidence_boundary_artifact.model_dump(mode="json")
                            if result.evidence_boundary_artifact
                            else None
                        ),
                        "summary": (
                            result.summary_artifact.model_dump(mode="json")
                            if result.summary_artifact
                            else None
                        ),
                        "reviewer_summary": (
                            result.reviewer_summary_artifact.model_dump(mode="json")
                            if result.reviewer_summary_artifact
                            else None
                        ),
                        "reviewer_summary_markdown": (
                            result.reviewer_summary_markdown_artifact.model_dump(mode="json")
                            if result.reviewer_summary_markdown_artifact
                            else None
                        ),
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    report = result.report
    typer.echo(f"run_id={run_id}")
    typer.echo(f"paper_release_status={report.decision.status.value}")
    typer.echo(f"ready_for_human_review={str(report.decision.ready_for_human_review).lower()}")
    typer.echo(f"blocking_findings={len(report.decision.blocking_reasons)}")
    typer.echo(f"warnings={len(report.decision.warnings)}")
    typer.echo(f"revision_status={report.revision_status or 'unknown'}")
    typer.echo("publication_ready=false")
    typer.echo("is_verification_evidence=false")
    if result.report_artifact is not None:
        typer.echo(f"full_paper_release_report={result.report_artifact.path}")
    if result.reviewer_summary_artifact is not None:
        typer.echo(f"reviewer_bundle_summary={result.reviewer_summary_artifact.path}")


@app.command("ingest-human-review")
def ingest_human_review_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    review_file: Annotated[Path, typer.Option("--review-file")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate and persist a local human-review artifact without evidence upgrades."""
    try:
        result = ingest_human_review(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            review_file=review_file,
        )
    except HumanReviewIntakeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "human_review_artifact": result.review.model_dump(mode="json"),
                    "publication_ready": False,
                    "creates_scientific_validation": False,
                    "implies_publication_readiness": False,
                    "is_verification_evidence": False,
                    "artifacts": {
                        "human_review_artifact": result.review_artifact.model_dump(mode="json"),
                        "human_review_summary": (
                            result.review_summary_artifact.model_dump(mode="json")
                        ),
                        "reviewer_summary": (
                            result.reviewer_summary_artifact.model_dump(mode="json")
                        ),
                        "reviewer_summary_markdown": (
                            result.reviewer_summary_markdown_artifact.model_dump(mode="json")
                        ),
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"human_review_status={result.review.review_status}")
    typer.echo(f"blocking_concerns={len(result.review.blocking_concerns)}")
    typer.echo(f"requested_changes={len(result.review.requested_changes)}")
    typer.echo(f"recommended_next_action={result.review.recommended_next_action}")
    typer.echo("publication_ready=false")
    typer.echo("creates_scientific_validation=false")
    typer.echo("is_verification_evidence=false")
    typer.echo(f"human_review_artifact={result.review_artifact.path}")
    typer.echo(f"human_review_summary={result.review_summary_artifact.path}")


@app.command("inspect-human-review")
def inspect_human_review_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect a persisted human-review artifact without mutation."""
    try:
        summary = inspect_human_review(run_id=run_id, root=root)
    except HumanReviewIntakeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Human review: {summary['run_id']}")
    typer.echo(f"Status: {summary.get('review_status') or 'unknown'}")
    typer.echo(f"Blocking concerns: {int(summary.get('human_review_blocking_concern_count') or 0)}")
    typer.echo(f"Requested changes: {int(summary.get('human_review_requested_change_count') or 0)}")
    typer.echo(f"Recommended next action: {summary.get('recommended_next_action') or 'none'}")
    typer.echo(f"Publication ready: {str(summary.get('publication_ready', False)).lower()}")
    typer.echo(f"Artifact: {summary.get('human_review_artifact_path')}")


@app.command("ingest-proof-artifact")
def ingest_proof_artifact_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    proof_file: Annotated[Path, typer.Option("--proof-file")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate and persist a local proof artifact without readiness upgrades."""
    try:
        result = ingest_proof_artifact(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            proof_file=proof_file,
        )
    except EvidenceArtifactIntakeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "proof_artifact": result.proof.model_dump(mode="json"),
        "publication_ready": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": result.proof.is_verification_evidence,
        "artifacts": {
            "proof_artifact": result.proof_artifact.model_dump(mode="json"),
            "proof_index": result.proof_index_artifact.model_dump(mode="json"),
            "reviewer_summary": result.reviewer_summary_artifact.model_dump(mode="json"),
            "reviewer_summary_markdown": (
                result.reviewer_summary_markdown_artifact.model_dump(mode="json")
            ),
        },
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"proof_id={result.proof.proof_id}")
    typer.echo(f"proof_type={result.proof.proof_type}")
    typer.echo(f"checker_status={result.proof.checker_status}")
    typer.echo(f"formal_verification_passed={str(result.proof.is_verification_evidence).lower()}")
    typer.echo("publication_ready=false")
    typer.echo("creates_scientific_validation=false")
    typer.echo(f"proof_artifact={result.proof_artifact.path}")


@app.command("inspect-proof-artifacts")
def inspect_proof_artifacts_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect persisted proof artifacts without mutation."""
    try:
        summary = inspect_proof_artifacts(run_id=run_id, root=root)
    except EvidenceArtifactIntakeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Proof artifacts: {summary['run_id']}")
    typer.echo(f"Proof artifact count: {summary['proof_artifact_count']}")
    typer.echo(
        f"Formal verification artifacts passed: {summary['formal_verification_passed_count']}"
    )
    typer.echo(f"Informal proof artifacts: {summary['informal_proof_artifact_count']}")
    typer.echo(f"Proof evidence gap present: {str(summary['proof_evidence_gap_present']).lower()}")
    typer.echo("Publication ready: false")
    for path in summary["proof_artifact_paths"]:
        typer.echo(f"- {path}")


@app.command("ingest-experiment-artifact")
def ingest_experiment_artifact_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    experiment_file: Annotated[Path, typer.Option("--experiment-file")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate and persist a local experiment artifact without readiness upgrades."""
    try:
        result = ingest_experiment_artifact(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            experiment_file=experiment_file,
        )
    except EvidenceArtifactIntakeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "experiment_artifact": result.experiment.model_dump(mode="json"),
        "publication_ready": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
        "artifacts": {
            "experiment_artifact": result.experiment_artifact.model_dump(mode="json"),
            "experiment_index": result.experiment_index_artifact.model_dump(mode="json"),
            "reviewer_summary": result.reviewer_summary_artifact.model_dump(mode="json"),
            "reviewer_summary_markdown": (
                result.reviewer_summary_markdown_artifact.model_dump(mode="json")
            ),
        },
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"experiment_id={result.experiment.experiment_id}")
    typer.echo(f"experiment_type={result.experiment.experiment_type}")
    typer.echo(f"experiment_status={result.experiment.status}")
    typer.echo(f"completed={str(result.experiment.status == 'completed').lower()}")
    typer.echo("publication_ready=false")
    typer.echo("creates_scientific_validation=false")
    typer.echo(f"experiment_artifact={result.experiment_artifact.path}")


@app.command("inspect-experiment-artifacts")
def inspect_experiment_artifacts_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect persisted experiment artifacts without mutation."""
    try:
        summary = inspect_experiment_artifacts(run_id=run_id, root=root)
    except EvidenceArtifactIntakeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Experiment artifacts: {summary['run_id']}")
    typer.echo(f"Experiment artifact count: {summary['experiment_artifact_count']}")
    typer.echo(f"Completed experiments: {summary['completed_experiment_count']}")
    typer.echo(f"Inconclusive experiments: {summary['inconclusive_experiment_count']}")
    typer.echo(f"Failed experiments: {summary['failed_experiment_count']}")
    typer.echo(
        "Experiment evidence gap present: "
        f"{str(summary['experiment_evidence_gap_present']).lower()}"
    )
    typer.echo("Publication ready: false")
    for path in summary["experiment_artifact_paths"]:
        typer.echo(f"- {path}")


@app.command("build-claim-evidence-map")
def build_claim_evidence_map_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Build and persist deterministic scoped evidence-to-claim links."""
    try:
        result = persist_claim_evidence_map(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
        )
    except ClaimEvidenceMapError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "claim_evidence_map": result.claim_evidence_map.model_dump(mode="json"),
        "claim_evidence_map_present": True,
        "publication_ready": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
        "artifacts": {
            "claim_evidence_map": result.map_artifact.model_dump(mode="json"),
            "claim_evidence_map_markdown": result.markdown_artifact.model_dump(mode="json"),
            "reviewer_summary": result.reviewer_summary_artifact.model_dump(mode="json"),
            "reviewer_summary_markdown": (
                result.reviewer_summary_markdown_artifact.model_dump(mode="json")
            ),
        },
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    counts = result.claim_evidence_map.summary_counts
    typer.echo(f"run_id={run_id}")
    typer.echo("claim_evidence_map_present=true")
    typer.echo(f"supported_claims={int(counts.get('supported_within_scope') or 0)}")
    typer.echo(f"partial_claims={int(counts.get('partially_supported') or 0)}")
    typer.echo(f"unsupported_claims={int(counts.get('unsupported') or 0)}")
    typer.echo(f"proof_supported_claims={int(counts.get('proof_supported_claim') or 0)}")
    typer.echo(f"experiment_supported_claims={int(counts.get('experiment_supported_claim') or 0)}")
    typer.echo(
        f"citation_supported_claims={int(counts.get('citation_supported_background_claim') or 0)}"
    )
    typer.echo("publication_ready=false")
    typer.echo(f"claim_evidence_map={result.map_artifact.path}")


@app.command("inspect-claim-evidence-map")
def inspect_claim_evidence_map_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect a persisted claim-evidence map without mutation."""
    try:
        summary = inspect_claim_evidence_map(run_id=run_id, root=root)
    except ClaimEvidenceMapError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Claim-evidence map: {summary['run_id']}")
    typer.echo(f"Supported claims: {summary['claim_evidence_supported_count']}")
    typer.echo(f"Partially supported claims: {summary['claim_evidence_partial_count']}")
    typer.echo(f"Unsupported claims: {summary['claim_evidence_unsupported_count']}")
    typer.echo(f"Proof-supported claims: {summary['proof_supported_claim_count']}")
    typer.echo(f"Experiment-supported claims: {summary['experiment_supported_claim_count']}")
    typer.echo(f"Citation-supported claims: {summary['citation_supported_claim_count']}")
    typer.echo(f"Human-review-linked claims: {summary['human_review_linked_claim_count']}")
    typer.echo("Publication ready: false")
    typer.echo(f"Artifact: {summary.get('claim_evidence_map_path')}")


@app.command("build-autonomous-evidence-plan")
def build_autonomous_evidence_plan_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    planner_backend: Annotated[
        str,
        typer.Option("--planner-backend"),
    ] = "off",
    planner_model: Annotated[
        str,
        typer.Option("--planner-model"),
    ] = DEFAULT_LLM_MODEL,
    max_calls: Annotated[
        int,
        typer.Option("--max-autonomous-evidence-planner-calls"),
    ] = 0,
    allow_external_calls: Annotated[
        bool,
        typer.Option("--allow-external-calls"),
    ] = False,
    enable_empirical_demonstration_gaps: Annotated[
        bool,
        typer.Option("--enable-empirical-demonstration-gaps"),
    ] = False,
    enable_capability_escalation: Annotated[
        bool,
        typer.Option("--enable-capability-escalation"),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Build and persist a deterministic autonomous evidence-gap plan."""
    try:
        if enable_empirical_demonstration_gaps:
            persist_claim_evidence_map(
                run_id=run_id,
                root=root,
                store=ArtifactStore(root),
                ledger=_ledger(root, run_id),
                enable_empirical_demonstration_gaps=True,
            )
        result = persist_autonomous_evidence_gap_plan(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            backend=planner_backend,
            model=planner_model,
            max_calls=max_calls,
            allow_external_calls=allow_external_calls,
        )
    except AutonomousEvidencePlanError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "autonomous_evidence_gap_plan": result.plan.model_dump(mode="json"),
        "autonomous_evidence_plan_present": True,
        "publication_ready": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
        "artifacts": {
            "autonomous_evidence_gap_plan": result.plan_artifact.model_dump(mode="json"),
            "autonomous_evidence_gap_plan_markdown": (
                result.markdown_artifact.model_dump(mode="json")
            ),
            "reviewer_summary": result.reviewer_summary_artifact.model_dump(mode="json"),
            "reviewer_summary_markdown": (
                result.reviewer_summary_markdown_artifact.model_dump(mode="json")
            ),
        },
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"planner_backend={result.plan.planner_backend}")
    typer.echo(f"planner_status={result.plan.planner_status}")
    typer.echo(f"plan_items={len(result.plan.plan_items)}")
    typer.echo(
        "automation_ready_items="
        f"{sum(1 for item in result.plan.plan_items if item.automation_ready)}"
    )
    typer.echo(
        f"human_intervention_required={str(result.plan.requires_human_intervention).lower()}"
    )
    typer.echo("publication_ready=false")
    typer.echo(f"autonomous_evidence_gap_plan={result.plan_artifact.path}")


@app.command("inspect-autonomous-evidence-plan")
def inspect_autonomous_evidence_plan_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect a persisted autonomous evidence-gap plan without mutation."""
    try:
        summary = inspect_autonomous_evidence_gap_plan(run_id=run_id, root=root)
    except AutonomousEvidencePlanError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Autonomous evidence-gap plan: {summary['run_id']}")
    typer.echo(f"Planner backend: {summary['planner_backend']}")
    typer.echo(f"Planner status: {summary['planner_status']}")
    typer.echo(f"Plan items: {summary['autonomous_plan_item_count']}")
    typer.echo(f"Python experiment items: {summary['autonomous_python_experiment_item_count']}")
    typer.echo(f"Formal proof items: {summary['autonomous_formal_proof_item_count']}")
    typer.echo(f"Retrieval expansion items: {summary['autonomous_retrieval_expansion_item_count']}")
    typer.echo(f"Claim downgrade items: {summary['autonomous_claim_downgrade_item_count']}")
    typer.echo(f"Claim removal items: {summary['autonomous_claim_removal_item_count']}")
    typer.echo(f"Automation-ready items: {summary['automation_ready_item_count']}")
    typer.echo(
        "Human intervention required: "
        f"{str(summary['autonomous_human_intervention_required']).lower()}"
    )
    typer.echo("Publication ready: false")
    typer.echo(f"Artifact: {summary['autonomous_evidence_plan_path']}")


@app.command("execute-autonomous-evidence-plan")
def execute_autonomous_evidence_plan_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    execution_mode: Annotated[
        str,
        typer.Option("--execution-mode"),
    ] = "dry-run",
    executor_backend: Annotated[
        str,
        typer.Option("--executor-backend"),
    ] = "deterministic",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Execute the current autonomous plan in deterministic dry-run or apply mode."""
    try:
        result = execute_autonomous_evidence_plan(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            execution_mode=execution_mode,
            executor_backend=executor_backend,
        )
    except AutonomousPlanExecutionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "autonomous_plan_execution": result.report.model_dump(mode="json"),
        "autonomous_plan_execution_index": result.index.model_dump(mode="json"),
        "autonomous_execution_present": True,
        "publication_ready": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
        "artifacts": {
            "execution_report": result.report_artifact.model_dump(mode="json"),
            "execution_report_markdown": result.report_markdown_artifact.model_dump(mode="json"),
            "execution_index": result.index_artifact.model_dump(mode="json"),
        },
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"execution_id={result.report.execution_id}")
    typer.echo(f"execution_mode={result.report.execution_mode}")
    typer.echo(f"execution_status={result.report.execution_status}")
    typer.echo(f"actions_applied={result.report.actions_applied}")
    typer.echo(f"actions_deferred={result.report.actions_deferred}")
    typer.echo(f"actions_rejected={result.report.actions_rejected}")
    typer.echo(f"actions_failed={result.report.actions_failed}")
    typer.echo(
        f"human_intervention_required={str(result.report.requires_human_intervention).lower()}"
    )
    typer.echo("publication_ready=false")
    typer.echo(f"autonomous_plan_execution={result.report_artifact.path}")


@app.command("inspect-autonomous-plan-execution")
def inspect_autonomous_plan_execution_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest autonomous plan execution without mutation."""
    try:
        summary = inspect_autonomous_plan_execution(run_id=run_id, root=root)
    except AutonomousPlanExecutionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Autonomous plan execution: {summary['run_id']}")
    typer.echo(f"Execution count: {summary['autonomous_execution_count']}")
    typer.echo(f"Latest mode: {summary['latest_autonomous_execution_mode']}")
    typer.echo(f"Latest status: {summary['latest_autonomous_execution_status']}")
    typer.echo(f"Actions applied: {summary['autonomous_actions_applied']}")
    typer.echo(f"Actions deferred: {summary['autonomous_actions_deferred']}")
    typer.echo(f"Actions rejected: {summary['autonomous_actions_rejected']}")
    typer.echo(f"Actions failed: {summary['autonomous_actions_failed']}")
    typer.echo(f"Created specs: {summary['autonomous_created_spec_count']}")
    typer.echo(f"Duplicate specs skipped: {int(summary.get('duplicate_specs_skipped') or 0)}")
    typer.echo(
        "Human intervention required: "
        f"{str(summary['autonomous_execution_requires_human_intervention']).lower()}"
    )
    typer.echo("Publication ready: false")
    typer.echo(f"Artifact: {summary['autonomous_execution_report_path']}")


@app.command("execute-planned-specs")
def execute_planned_specs_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    execution_mode: Annotated[
        str,
        typer.Option("--execution-mode"),
    ] = "dry-run",
    spec_executor_backend: Annotated[
        str,
        typer.Option("--spec-executor-backend"),
    ] = "deterministic_local",
    python_sandbox_backend: Annotated[
        str,
        typer.Option("--python-sandbox-backend"),
    ] = "off",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Execute planned local proof, experiment, and retrieval specs."""
    try:
        result = execute_planned_specs(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            execution_mode=execution_mode,
            spec_executor_backend=spec_executor_backend,
            python_sandbox_backend=python_sandbox_backend,
        )
    except PlannedSpecExecutionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "planned_spec_execution": result.report.model_dump(mode="json"),
        "planned_spec_execution_index": result.index.model_dump(mode="json"),
        "planned_spec_execution_present": True,
        "publication_ready": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
        "artifacts": {
            "execution_report": result.report_artifact.model_dump(mode="json"),
            "execution_report_markdown": result.report_markdown_artifact.model_dump(mode="json"),
            "execution_index": result.index_artifact.model_dump(mode="json"),
        },
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"execution_id={result.report.execution_id}")
    typer.echo(f"execution_mode={result.report.execution_mode}")
    typer.echo(f"execution_status={result.report.execution_status}")
    typer.echo(f"specs_executed={result.report.specs_executed}")
    typer.echo(f"specs_deferred={result.report.specs_deferred}")
    typer.echo(f"specs_rejected={result.report.specs_rejected}")
    typer.echo(f"specs_failed={result.report.specs_failed}")
    typer.echo(f"experiment_artifacts_created={result.report.experiment_artifacts_created}")
    typer.echo(f"proof_artifacts_created={result.report.proof_artifacts_created}")
    typer.echo(f"retrieval_artifacts_created={result.report.retrieval_artifacts_created}")
    typer.echo(
        f"human_intervention_required={str(result.report.requires_human_intervention).lower()}"
    )
    typer.echo("publication_ready=false")
    typer.echo(f"planned_spec_execution={result.report_artifact.path}")


@app.command("inspect-planned-spec-execution")
def inspect_planned_spec_execution_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest planned-spec execution without mutation."""
    try:
        summary = inspect_planned_spec_execution(run_id=run_id, root=root)
    except PlannedSpecExecutionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Planned spec execution: {summary['run_id']}")
    typer.echo(f"Execution count: {summary['planned_spec_execution_count']}")
    typer.echo(f"Latest mode: {summary['latest_planned_spec_execution_mode']}")
    typer.echo(f"Latest status: {summary['latest_planned_spec_execution_status']}")
    typer.echo(f"Experiment specs executed: {summary['experiment_specs_executed']}")
    typer.echo(f"Proof specs executed: {summary['proof_specs_executed']}")
    typer.echo(f"Retrieval specs executed: {summary['retrieval_specs_executed']}")
    typer.echo(f"Experiment artifacts created: {summary['experiment_artifacts_created']}")
    typer.echo(f"Proof artifacts created: {summary['proof_artifacts_created']}")
    typer.echo(f"Retrieval artifacts created: {summary['retrieval_artifacts_created']}")
    typer.echo(
        f"Duplicate specs skipped: {int(summary.get('planned_spec_duplicate_specs_skipped') or 0)}"
    )
    typer.echo(
        "Human intervention required: "
        f"{str(summary['planned_spec_execution_requires_human_intervention']).lower()}"
    )
    typer.echo("Publication ready: false")
    typer.echo(f"Artifact: {summary['planned_spec_execution_report_path']}")


@app.command("run-python-experiment-sandbox")
def run_python_experiment_sandbox_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    experiment_spec: Annotated[Path, typer.Option("--experiment-spec")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    sandbox_backend: Annotated[str, typer.Option("--sandbox-backend")] = "uv_local",
    execution_mode: Annotated[str, typer.Option("--execution-mode")] = "dry-run",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate or execute one approved local Python experiment bundle."""
    try:
        result = run_python_experiment_sandbox(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            experiment_spec=experiment_spec,
            sandbox_backend=sandbox_backend,
            execution_mode=execution_mode,
        )
    except PythonExperimentSandboxError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        **result.report.model_dump(mode="json"),
        "python_experiment_sandbox_present": True,
        "python_experiment_sandbox_index": result.index.model_dump(mode="json"),
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"sandbox_run_id={result.report.sandbox_run_id}")
    typer.echo(f"execution_mode={result.report.execution_mode}")
    typer.echo(f"sandbox_status={result.report.sandbox_status}")
    typer.echo(f"network_disabled={str(result.report.network_disabled).lower()}")
    typer.echo(
        "experiment_artifact_created="
        f"{str(bool(result.report.ingested_experiment_artifact_path_optional)).lower()}"
    )
    typer.echo("publication_ready=false")
    typer.echo(f"sandbox_report={result.report_artifact.path}")


@app.command("inspect-python-experiment-sandbox")
def inspect_python_experiment_sandbox_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest local Python experiment sandbox run."""
    try:
        summary = inspect_python_experiment_sandbox(run_id=run_id, root=root)
    except PythonExperimentSandboxError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Python experiment sandbox: {summary['run_id']}")
    typer.echo(f"Sandbox runs: {summary['python_experiment_sandbox_run_count']}")
    typer.echo(f"Latest status: {summary['latest_python_sandbox_status']}")
    typer.echo(f"Completed runs: {summary['python_experiment_sandbox_completed_count']}")
    typer.echo(f"Failed runs: {summary['python_experiment_sandbox_failed_count']}")
    typer.echo(
        f"Experiment artifacts created: {summary['python_experiment_artifacts_created_count']}"
    )
    typer.echo(
        f"Network disabled: {str(summary['python_experiment_sandbox_network_disabled']).lower()}"
    )
    typer.echo("Publication ready: false")


@app.command("route-experiment-gaps")
def route_experiment_gaps_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    routing_backend: Annotated[str, typer.Option("--routing-backend")] = "deterministic",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Route experiment gaps to approved local experiment templates."""
    try:
        result = route_experiment_gaps(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            routing_backend=routing_backend,
        )
    except ExperimentGapRoutingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "experiment_gap_routing": result.report.model_dump(mode="json"),
        "experiment_gap_routing_index": result.index.model_dump(mode="json"),
        "experiment_template_registry": result.registry.model_dump(mode="json"),
        "experiment_gap_routing_present": True,
        "routed_experiment_gap_count": result.report.routed_gap_count,
        "unrouted_experiment_gap_count": result.report.unrouted_gap_count,
        "created_experiment_spec_count": result.report.created_experiment_spec_count,
        "publication_ready": False,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"routing_id={result.report.routing_id}")
    typer.echo(f"routing_status={result.report.routing_status}")
    typer.echo(f"routed_experiment_gap_count={result.report.routed_gap_count}")
    typer.echo(f"created_experiment_spec_count={result.report.created_experiment_spec_count}")
    typer.echo("publication_ready=false")
    typer.echo(f"experiment_gap_routing={result.report_artifact.path}")


@app.command("inspect-experiment-gap-routing")
def inspect_experiment_gap_routing_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest experiment-gap routing report."""
    try:
        summary = inspect_experiment_gap_routing(run_id=run_id, root=root)
    except ExperimentGapRoutingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Experiment gap routing: {summary['run_id']}")
    typer.echo(f"Routing status: {summary['routing_status']}")
    typer.echo(f"Routed experiment gaps: {summary['routed_experiment_gap_count']}")
    typer.echo(f"Unrouted experiment gaps: {summary['unrouted_experiment_gap_count']}")
    typer.echo(f"Created experiment specs: {summary['created_experiment_spec_count']}")
    typer.echo(f"Routed empirical gaps: {summary['routed_empirical_gap_count']}")
    typer.echo(f"Synthetic template specs created: {summary['synthetic_template_specs_created']}")
    typer.echo("Publication ready: false")
    typer.echo(f"Artifact: {summary['experiment_gap_routing_report_path']}")


@app.command("run-autonomous-loop")
def run_autonomous_loop_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    loop_backend: Annotated[str, typer.Option("--loop-backend")] = "deterministic",
    max_iterations: Annotated[int, typer.Option("--max-iterations")] = 3,
    max_attempts_per_gap: Annotated[int, typer.Option("--max-attempts-per-gap")] = 2,
    enable_strategy_diversification: Annotated[
        bool,
        typer.Option("--enable-strategy-diversification"),
    ] = False,
    enable_experiment_routing: Annotated[
        bool,
        typer.Option("--enable-experiment-routing"),
    ] = False,
    enable_empirical_demonstration_gaps: Annotated[
        bool,
        typer.Option("--enable-empirical-demonstration-gaps"),
    ] = False,
    enable_capability_escalation: Annotated[
        bool,
        typer.Option("--enable-capability-escalation"),
    ] = False,
    python_sandbox_backend: Annotated[
        str,
        typer.Option("--python-sandbox-backend"),
    ] = "off",
    max_sandbox_runs_per_loop: Annotated[int, typer.Option("--max-sandbox-runs-per-loop")] = 3,
    max_sandbox_runs_per_iteration: Annotated[
        int,
        typer.Option("--max-sandbox-runs-per-iteration"),
    ] = 1,
    max_sandbox_seconds_per_loop: Annotated[
        int,
        typer.Option("--max-sandbox-seconds-per-loop"),
    ] = 120,
    max_sandbox_failures_per_loop: Annotated[
        int,
        typer.Option("--max-sandbox-failures-per-loop"),
    ] = 1,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run the deterministic autonomous evidence-gap loop controller."""
    try:
        result = run_autonomous_loop(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            loop_backend=loop_backend,
            max_iterations=max_iterations,
            max_attempts_per_gap=max_attempts_per_gap,
            enable_strategy_diversification=enable_strategy_diversification,
            enable_experiment_routing=enable_experiment_routing,
            enable_empirical_demonstration_gaps=enable_empirical_demonstration_gaps,
            enable_capability_escalation=enable_capability_escalation,
            python_sandbox_backend=python_sandbox_backend,
            max_sandbox_runs_per_loop=max_sandbox_runs_per_loop,
            max_sandbox_runs_per_iteration=max_sandbox_runs_per_iteration,
            max_sandbox_seconds_per_loop=max_sandbox_seconds_per_loop,
            max_sandbox_failures_per_loop=max_sandbox_failures_per_loop,
        )
    except AutonomousLoopError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "autonomous_loop": result.report.model_dump(mode="json"),
        "autonomous_loop_index": result.index.model_dump(mode="json"),
        "autonomous_loop_present": True,
        "publication_ready": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
        "artifacts": {
            "autonomous_loop_report": result.report_artifact.model_dump(mode="json"),
            "autonomous_loop_markdown": result.report_markdown_artifact.model_dump(mode="json"),
            "autonomous_loop_index": result.index_artifact.model_dump(mode="json"),
        },
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"loop_id={result.report.loop_id}")
    typer.echo(f"loop_status={result.report.loop_status}")
    typer.echo(f"iterations_completed={result.report.iterations_completed}")
    typer.echo(f"stop_reason={result.report.stop_reason}")
    typer.echo(
        f"human_intervention_required={str(result.report.requires_human_intervention).lower()}"
    )
    typer.echo("publication_ready=false")
    typer.echo(f"autonomous_loop={result.report_artifact.path}")


@app.command("diversify-gap-strategies")
def diversify_gap_strategies_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    strategy_backend: Annotated[
        str,
        typer.Option("--strategy-backend"),
    ] = "deterministic",
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Persist deterministic alternatives for exhausted autonomous gaps."""
    try:
        result = persist_gap_strategy_diversification(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            backend=strategy_backend,
        )
    except GapStrategyDiversificationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "gap_strategy_diversification": result.report.model_dump(mode="json"),
        "gap_strategy_diversification_index": result.index.model_dump(mode="json"),
        "strategy_diversification_present": True,
        "publication_ready": False,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"diversification_id={result.report.diversification_id}")
    typer.echo(f"strategy_status={result.report.strategy_status}")
    typer.echo(f"strategy_options={result.report.strategy_option_count}")
    typer.echo(f"selected_strategies={result.report.selected_strategy_count}")
    typer.echo(f"duplicate_strategies={result.report.duplicate_strategy_count}")
    typer.echo("publication_ready=false")
    typer.echo(f"strategy_diversification={result.report_artifact.path}")


@app.command("inspect-gap-strategy-diversification")
def inspect_gap_strategy_diversification_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest gap strategy diversification without mutation."""
    try:
        summary = inspect_gap_strategy_diversification(run_id=run_id, root=root)
    except GapStrategyDiversificationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Gap strategy diversification: {summary['run_id']}")
    typer.echo(f"Status: {summary['strategy_status']}")
    typer.echo(f"Strategy options: {summary['strategy_option_count']}")
    typer.echo(f"Selected strategies: {summary['selected_strategy_count']}")
    typer.echo(f"Duplicate strategies: {summary['duplicate_strategy_count']}")
    typer.echo("Publication ready: false")
    typer.echo(f"Artifact: {summary['gap_strategy_diversification_report_path']}")


@app.command("inspect-autonomous-loop")
def inspect_autonomous_loop_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest autonomous loop controller report."""
    try:
        summary = inspect_autonomous_loop(run_id=run_id, root=root)
    except AutonomousLoopError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Autonomous loop: {summary['run_id']}")
    typer.echo(f"Loop count: {summary['autonomous_loop_count']}")
    typer.echo(f"Latest status: {summary['latest_autonomous_loop_status']}")
    typer.echo(f"Iterations completed: {summary['latest_autonomous_loop_iterations_completed']}")
    typer.echo(f"Stop reason: {summary['latest_autonomous_loop_stop_reason']}")
    typer.echo(
        f"Terminal state: {summary.get('autonomous_loop_terminal_state') or 'not_available'}"
    )
    typer.echo(
        "Terminal reason: "
        f"{summary.get('autonomous_loop_terminal_state_reason') or 'not_available'}"
    )
    typer.echo(f"Resolved gaps: {int(summary.get('autonomous_loop_resolved_gap_count') or 0)}")
    typer.echo(f"Deferred gaps: {int(summary.get('autonomous_loop_deferred_gap_count') or 0)}")
    typer.echo(f"Exhausted gaps: {int(summary.get('autonomous_loop_exhausted_gap_count') or 0)}")
    typer.echo(
        f"Duplicate-only gaps: {int(summary.get('autonomous_loop_duplicate_only_gap_count') or 0)}"
    )
    typer.echo(
        "Automation-ready after history: "
        f"{int(summary.get('autonomous_loop_automation_ready_after_history_count') or 0)}"
    )
    typer.echo(
        "Stopped before max iterations: "
        f"{str(bool(summary.get('autonomous_loop_stopped_before_max_iterations'))).lower()}"
    )
    typer.echo(
        "Empirical paths resolved / proof paths deferred / retrieval paths deferred: "
        f"{int(summary.get('autonomous_loop_empirical_paths_resolved') or 0)}/"
        f"{int(summary.get('autonomous_loop_proof_paths_deferred') or 0)}/"
        f"{int(summary.get('autonomous_loop_retrieval_paths_deferred') or 0)}"
    )
    typer.echo(
        "Empirical paths resolved: "
        f"{int(summary.get('autonomous_loop_empirical_paths_resolved') or 0)}"
    )
    typer.echo(
        f"Proof paths deferred: {int(summary.get('autonomous_loop_proof_paths_deferred') or 0)}"
    )
    typer.echo(
        "Retrieval paths deferred: "
        f"{int(summary.get('autonomous_loop_retrieval_paths_deferred') or 0)}"
    )
    typer.echo(
        f"Final unsupported claims: {summary['autonomous_loop_final_unsupported_claim_count']}"
    )
    typer.echo(
        "Final automation-ready items: "
        f"{summary['autonomous_loop_final_automation_ready_item_count']}"
    )
    typer.echo(
        "Iterations without progress: "
        f"{summary.get('autonomous_loop_iterations_without_progress', 0)}"
    )
    typer.echo(
        "Stopped due to exhausted gaps: "
        f"{str(bool(summary.get('autonomous_loop_stopped_due_to_exhausted_gaps'))).lower()}"
    )
    typer.echo(f"Duplicate specs skipped: {int(summary.get('duplicate_specs_skipped') or 0)}")
    typer.echo(
        f"Gaps exhausted/no-progress: {int(summary.get('gap_exhausted_no_progress_count') or 0)}"
    )
    typer.echo(
        "Strategy diversification: "
        f"{'present' if summary.get('strategy_diversification_present') else 'absent'}"
    )
    typer.echo(f"Strategy options: {int(summary.get('strategy_option_count') or 0)}")
    typer.echo(f"Selected strategies: {int(summary.get('selected_strategy_count') or 0)}")
    typer.echo(f"Duplicate strategies: {int(summary.get('duplicate_strategy_count') or 0)}")
    typer.echo(
        "Deferred after all strategies exhausted: "
        f"{int(summary.get('gaps_deferred_after_strategy_exhaustion') or 0)}"
    )
    typer.echo(
        "Experiment routing enabled: "
        f"{str(bool(summary.get('experiment_routing_enabled'))).lower()}"
    )
    typer.echo(f"Routed experiment gaps: {int(summary.get('routed_experiment_gap_count') or 0)}")
    typer.echo(f"Routed experiment specs: {int(summary.get('routed_experiment_spec_count') or 0)}")
    typer.echo(f"Bounded empirical gaps created: {int(summary.get('empirical_gaps_created') or 0)}")
    typer.echo(f"Bounded empirical gaps routed: {int(summary.get('empirical_gaps_routed') or 0)}")
    typer.echo(
        f"Sandbox experiments completed: {int(summary.get('sandbox_experiments_completed') or 0)}"
    )
    typer.echo(
        f"Experiment artifacts ingested: {int(summary.get('experiment_artifacts_ingested') or 0)}"
    )
    typer.echo(
        f"Sandbox budget exhausted: {str(bool(summary.get('sandbox_budget_exhausted'))).lower()}"
    )
    typer.echo(
        "Sandbox runs used/remaining: "
        f"{int(summary.get('sandbox_budget_runs_used') or 0)}/"
        f"{int(summary.get('sandbox_budget_runs_remaining') or 0)}"
    )
    typer.echo(
        "Capability escalation enabled/status: "
        f"{str(bool(summary.get('capability_escalation_enabled'))).lower()}/"
        f"{summary.get('capability_escalation_status') or 'none'}"
    )
    typer.echo(
        f"Proof escalations attempted: {int(summary.get('proof_escalation_attempt_count') or 0)}"
    )
    typer.echo(
        "Retrieval escalations attempted: "
        f"{int(summary.get('retrieval_escalation_attempt_count') or 0)}"
    )
    typer.echo(f"Successful escalations: {int(summary.get('successful_escalation_count') or 0)}")
    typer.echo(
        f"Deferred after escalation: {int(summary.get('deferred_after_escalation_count') or 0)}"
    )
    typer.echo(
        "Human intervention required: "
        f"{str(summary['autonomous_loop_requires_human_intervention']).lower()}"
    )
    typer.echo("Publication ready: false")


@app.command("run-autonomous-paper")
def run_autonomous_paper_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    domain: Annotated[str, typer.Option("--domain")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    topic_or_question: Annotated[
        str | None,
        typer.Option("--topic-or-question", "--method"),
    ] = None,
    controller_backend: Annotated[
        str,
        typer.Option("--controller-backend"),
    ] = "deterministic",
    llm_scope: Annotated[str, typer.Option("--llm-scope")] = "full-paper",
    candidate_backend: Annotated[
        str,
        typer.Option("--candidate-backend"),
    ] = DEFAULT_ADAPTER_BACKEND,
    reviewer_backend: Annotated[
        str,
        typer.Option("--reviewer-backend"),
    ] = DEFAULT_REVIEWER_BACKEND,
    prose_backend: Annotated[str, typer.Option("--prose-backend")] = DEFAULT_PROSE_BACKEND,
    claim_adjudicator_backend: Annotated[
        str,
        typer.Option("--claim-adjudicator-backend"),
    ] = "fake",
    source_relevance_adjudicator_backend: Annotated[
        str,
        typer.Option("--source-relevance-adjudicator-backend"),
    ] = "fake",
    quality_repair_backend: Annotated[
        str,
        typer.Option("--quality-repair-backend"),
    ] = "deterministic",
    candidate_model: Annotated[
        str,
        typer.Option("--candidate-model", "--llm-model"),
    ] = DEFAULT_LLM_MODEL,
    reviewer_model: Annotated[
        str,
        typer.Option("--reviewer-model"),
    ] = DEFAULT_LLM_MODEL,
    prose_model: Annotated[str, typer.Option("--prose-model")] = DEFAULT_LLM_MODEL,
    allow_external_calls: Annotated[
        bool,
        typer.Option("--allow-external-calls"),
    ] = False,
    enable_retrieval: Annotated[
        bool,
        typer.Option("--enable-retrieval/--disable-retrieval"),
    ] = False,
    retrieval_backend: Annotated[str, typer.Option("--retrieval-backend")] = "fake",
    retrieval_local_path: Annotated[
        str | None,
        typer.Option("--retrieval-local-path"),
    ] = None,
    max_retrieval_sources: Annotated[
        int,
        typer.Option("--max-retrieval-sources"),
    ] = 8,
    citation_policy: Annotated[str, typer.Option("--citation-policy")] = "none",
    enable_safe_repair: Annotated[
        bool,
        typer.Option("--enable-safe-repair/--disable-safe-repair"),
    ] = True,
    loop_backend: Annotated[str, typer.Option("--loop-backend")] = "deterministic",
    max_loop_iterations: Annotated[
        int,
        typer.Option("--max-loop-iterations"),
    ] = 6,
    max_attempts_per_gap: Annotated[
        int,
        typer.Option("--max-attempts-per-gap"),
    ] = 1,
    enable_strategy_diversification: Annotated[
        bool,
        typer.Option("--enable-strategy-diversification"),
    ] = False,
    enable_experiment_routing: Annotated[
        bool,
        typer.Option("--enable-experiment-routing"),
    ] = False,
    enable_empirical_demonstration_gaps: Annotated[
        bool,
        typer.Option("--enable-empirical-demonstration-gaps"),
    ] = False,
    enable_capability_escalation: Annotated[
        bool,
        typer.Option("--enable-capability-escalation"),
    ] = False,
    python_sandbox_backend: Annotated[
        str,
        typer.Option("--python-sandbox-backend"),
    ] = "off",
    max_sandbox_runs_per_loop: Annotated[
        int,
        typer.Option("--max-sandbox-runs-per-loop"),
    ] = 2,
    max_sandbox_runs_per_iteration: Annotated[
        int,
        typer.Option("--max-sandbox-runs-per-iteration"),
    ] = 1,
    regeneration_backend: Annotated[
        str,
        typer.Option("--regeneration-backend"),
    ] = "deterministic",
    build_final_bundle: Annotated[
        bool,
        typer.Option("--build-final-bundle/--skip-final-bundle"),
    ] = True,
    verify_final_bundle: Annotated[
        bool,
        typer.Option("--verify-final-bundle/--skip-final-bundle-verification"),
    ] = True,
    compile_pdf: Annotated[bool, typer.Option("--compile-pdf")] = False,
    strict_export: Annotated[bool, typer.Option("--strict-export")] = False,
    resume_existing: Annotated[bool, typer.Option("--resume-existing")] = False,
    max_total_calls: Annotated[int | None, typer.Option("--max-total-calls")] = None,
    max_candidate_generation_calls: Annotated[
        int | None,
        typer.Option("--max-candidate-generation-calls"),
    ] = None,
    max_review_calls: Annotated[int | None, typer.Option("--max-review-calls")] = None,
    max_prose_calls: Annotated[int | None, typer.Option("--max-prose-calls")] = None,
    max_claim_adjudication_calls: Annotated[
        int | None,
        typer.Option("--max-claim-adjudication-calls"),
    ] = None,
    max_source_relevance_adjudication_calls: Annotated[
        int | None,
        typer.Option("--max-source-relevance-adjudication-calls"),
    ] = None,
    max_estimated_cost_usd: Annotated[
        float | None,
        typer.Option("--max-estimated-cost-usd"),
    ] = None,
    require_non_fake_backends: Annotated[
        bool,
        typer.Option("--require-non-fake-backends"),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run the complete fail-closed autonomous paper MVP in one command."""
    _reject_non_fake_requirement(
        required=require_non_fake_backends,
        stage_name="run-autonomous-paper",
    )
    config = LLMOrchestrationConfig(
        run_id=run_id,
        domain=domain,
        method=topic_or_question,
        candidate_backend=candidate_backend,
        reviewer_backend=reviewer_backend,
        prose_backend=prose_backend,
        allow_external_calls=allow_external_calls,
        llm_model=candidate_model,
        reviewer_model=reviewer_model,
        prose_model=prose_model,
        claim_adjudicator_backend=claim_adjudicator_backend,
        claim_adjudicator_model=candidate_model,
        source_relevance_adjudicator_backend=source_relevance_adjudicator_backend,
        source_relevance_adjudicator_model=candidate_model,
        quality_repair_backend=quality_repair_backend,
        quality_repair_model=candidate_model,
        enable_retrieval=enable_retrieval,
        retrieval_backend=retrieval_backend,
        retrieval_local_path=retrieval_local_path,
        max_retrieval_sources=max_retrieval_sources,
        citation_policy=citation_policy,
        generate_paper=True,
        evaluate_release=True,
        include_citations=enable_retrieval,
        export_latex=True,
        critique=True,
        write_report=True,
        budget=LLMBudgetConfig(
            max_total_calls=max_total_calls,
            max_candidate_generation_calls=max_candidate_generation_calls,
            max_review_calls=max_review_calls,
            max_prose_calls=max_prose_calls,
            max_claim_adjudication_calls=max_claim_adjudication_calls,
            max_source_relevance_adjudication_calls=(max_source_relevance_adjudication_calls),
            max_estimated_cost_usd=max_estimated_cost_usd,
        ),
    )
    try:
        result = run_autonomous_paper(
            config=config,
            root=root,
            controller_backend=controller_backend,
            llm_scope=llm_scope,
            enable_safe_repair=enable_safe_repair,
            loop_backend=loop_backend,
            max_loop_iterations=max_loop_iterations,
            max_attempts_per_gap=max_attempts_per_gap,
            enable_strategy_diversification=enable_strategy_diversification,
            enable_experiment_routing=enable_experiment_routing,
            enable_empirical_demonstration_gaps=enable_empirical_demonstration_gaps,
            enable_capability_escalation=enable_capability_escalation,
            python_sandbox_backend=python_sandbox_backend,
            max_sandbox_runs_per_loop=max_sandbox_runs_per_loop,
            max_sandbox_runs_per_iteration=max_sandbox_runs_per_iteration,
            regeneration_backend=regeneration_backend,
            build_final_bundle=build_final_bundle,
            verify_final_bundle=verify_final_bundle,
            compile_pdf=compile_pdf,
            strict_export=strict_export,
            resume_existing=resume_existing,
        )
    except AutonomousPaperRunError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(result.report.model_dump(mode="json"), indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"controller_status={result.report.controller_status}")
    typer.echo(f"handoff_status={result.report.handoff_status}")
    typer.echo(f"final_bundle_status={result.report.final_bundle_status or 'not_available'}")
    typer.echo(
        "final_bundle_verification_status="
        f"{result.report.final_bundle_verification_status or 'not_available'}"
    )
    typer.echo(f"deferred_gap_count={result.report.deferred_gap_count}")
    typer.echo(f"unsupported_claim_count={result.report.unsupported_claim_count}")
    typer.echo(
        f"human_intervention_required={str(result.report.human_intervention_required).lower()}"
    )
    typer.echo("publication_ready=false")
    typer.echo(f"report={result.report_artifact.path}")


@app.command("inspect-autonomous-paper-run")
def inspect_autonomous_paper_run_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest one-command autonomous paper report."""
    try:
        summary = inspect_autonomous_paper_run(run_id=run_id, root=root)
    except AutonomousPaperRunError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Autonomous paper run: {run_id}")
    typer.echo(f"Controller status: {summary['controller_status']}")
    typer.echo(f"Handoff status: {summary['handoff_status']}")
    for stage in summary["stages"]:
        typer.echo(f"- {stage['stage_name']}: {stage['stage_status']}")
    typer.echo(f"Final bundle: {summary.get('final_bundle_path_optional') or 'not_available'}")
    typer.echo(
        f"Final verification: {summary.get('final_bundle_verification_status') or 'not_available'}"
    )
    typer.echo(f"Deferred gaps: {summary['deferred_gap_count']}")
    typer.echo(f"Unsupported claims: {summary['unsupported_claim_count']}")
    typer.echo(
        "Base generation root failure: "
        f"{summary.get('root_base_generation_failure_stage') or 'none'} / "
        f"{summary.get('root_base_generation_failure_reason') or 'none'}"
    )
    typer.echo(
        "Base generation counts: "
        f"candidates={int(summary.get('candidate_count') or 0)}, "
        f"stage_a_survivors={int(summary.get('stage_a_survivor_count') or 0)}, "
        f"stage_b_survivors={int(summary.get('stage_b_survivor_count') or 0)}, "
        f"stage_c_ready={int(summary.get('stage_c_ready_count') or 0)}, "
        f"manuscript_plan_present="
        f"{str(bool(summary.get('manuscript_plan_present'))).lower()}"
    )
    typer.echo(
        f"Human intervention required: {str(summary['human_intervention_required']).lower()}"
    )
    typer.echo("Publication ready: false")
    typer.echo(f"Artifact: {summary['autonomous_paper_run_index']['latest_report_path']}")


@app.command("inspect-autonomous-paper-checkpoints")
def inspect_autonomous_paper_checkpoints_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Verify and inspect autonomous paper controller checkpoints read-only."""
    try:
        summary = inspect_autonomous_paper_checkpoints(run_id=run_id, root=root)
    except AutonomousPaperCheckpointError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Autonomous paper checkpoints: {run_id}")
    typer.echo(f"Checkpoint count: {summary['checkpoint_count']}")
    typer.echo(f"Latest completed stage: {summary['latest_completed_stage']}")
    typer.echo(f"Resume allowed: {str(summary['resume_allowed']).lower()}")
    typer.echo(f"Resume blockers: {len(summary['resume_blockers'])}")
    typer.echo("Publication ready: false")


@app.command("inspect-autonomous-paper-resume")
def inspect_autonomous_paper_resume_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest append-only autonomous paper resume report."""
    try:
        summary = inspect_autonomous_paper_resume(run_id=run_id, root=root)
    except AutonomousPaperCheckpointError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Autonomous paper resume: {run_id}")
    typer.echo(f"Resume status: {summary['resume_status']}")
    typer.echo(f"Actual resume stage: {summary['actual_resume_stage']}")
    typer.echo(f"Stages reused: {len(summary['stages_reused'])}")
    typer.echo(f"Stages rerun: {len(summary['stages_rerun'])}")
    typer.echo(f"Resume blockers: {len(summary['resume_blockers'])}")
    typer.echo("Publication ready: false")


def _parse_explicit_bool(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"true", "1", "yes", "y"}:
        return True
    if normalized in {"false", "0", "no", "n"}:
        return False
    raise typer.BadParameter("expected true or false")


@app.command("escalate-capabilities")
def escalate_capabilities_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    allow_network: Annotated[str, typer.Option("--allow-network")] = "false",
    allow_external_proof_tools: Annotated[
        str,
        typer.Option("--allow-external-proof-tools"),
    ] = "false",
    allow_external_retrieval_tools: Annotated[
        str,
        typer.Option("--allow-external-retrieval-tools"),
    ] = "false",
    max_escalation_attempts_per_gap: Annotated[
        int,
        typer.Option("--max-escalation-attempts-per-gap"),
    ] = 1,
    max_escalation_attempts_per_loop: Annotated[
        int,
        typer.Option("--max-escalation-attempts-per-loop"),
    ] = 4,
    max_retrieval_sources_per_escalation: Annotated[
        int,
        typer.Option("--max-retrieval-sources-per-escalation"),
    ] = 8,
    max_tool_runtime_seconds: Annotated[
        int,
        typer.Option("--max-tool-runtime-seconds"),
    ] = 30,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Attempt fail-closed local/offline escalation for deferred proof/retrieval gaps."""
    try:
        result = escalate_capabilities(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            allow_network=_parse_explicit_bool(allow_network),
            allow_external_proof_tools=_parse_explicit_bool(allow_external_proof_tools),
            allow_external_retrieval_tools=_parse_explicit_bool(allow_external_retrieval_tools),
            max_escalation_attempts_per_gap=max_escalation_attempts_per_gap,
            max_escalation_attempts_per_loop=max_escalation_attempts_per_loop,
            max_retrieval_sources_per_escalation=max_retrieval_sources_per_escalation,
            max_tool_runtime_seconds=max_tool_runtime_seconds,
        )
    except CapabilityEscalationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "capability_escalation": result.report.model_dump(mode="json"),
        "capability_escalation_index": result.index.model_dump(mode="json"),
        "capability_escalation_present": True,
        "publication_ready": False,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"escalation_id={result.report.escalation_id}")
    typer.echo(f"escalation_status={result.report.escalation_status}")
    typer.echo(f"proof_escalation_attempt_count={result.report.proof_escalation_attempt_count}")
    typer.echo(
        f"retrieval_escalation_attempt_count={result.report.retrieval_escalation_attempt_count}"
    )
    typer.echo(f"successful_escalation_count={result.report.successful_escalation_count}")
    typer.echo(f"deferred_after_escalation_count={result.report.deferred_after_escalation_count}")
    typer.echo(f"network_allowed={str(result.report.network_allowed).lower()}")
    typer.echo(f"external_tools_allowed={str(result.report.external_tools_allowed).lower()}")
    typer.echo("publication_ready=false")
    typer.echo(f"capability_escalation={result.report_artifact.path}")


@app.command("inspect-capability-escalation")
def inspect_capability_escalation_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect latest capability escalation without mutation."""
    try:
        summary = inspect_capability_escalation(run_id=run_id, root=root)
    except CapabilityEscalationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Capability escalation: {summary['run_id']}")
    typer.echo(f"Status: {summary['capability_escalation_status']}")
    typer.echo(f"Proof escalations attempted: {summary['proof_escalation_attempt_count']}")
    typer.echo(f"Retrieval escalations attempted: {summary['retrieval_escalation_attempt_count']}")
    typer.echo(f"Successful escalations: {summary['successful_escalation_count']}")
    typer.echo(f"Deferred after escalation: {summary['deferred_after_escalation_count']}")
    typer.echo(f"Network allowed: {str(summary['capability_escalation_network_allowed']).lower()}")
    typer.echo(
        "External tools allowed: "
        f"{str(summary['capability_escalation_external_tools_allowed']).lower()}"
    )
    typer.echo("Publication ready: false")
    typer.echo(f"Artifact: {summary['capability_escalation_report_path']}")


@app.command("regenerate-final-manuscript")
def regenerate_final_manuscript_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    regeneration_backend: Annotated[
        str,
        typer.Option("--regeneration-backend"),
    ] = "deterministic",
    allow_external_calls: Annotated[
        bool,
        typer.Option("--allow-external-calls"),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Regenerate a coherent final manuscript from the final scoped evidence state."""
    try:
        result = regenerate_final_manuscript(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            backend=regeneration_backend,
            allow_external_calls=allow_external_calls,
        )
    except FinalManuscriptRegenerationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "final_manuscript": result.report.model_dump(mode="json"),
        "final_manuscript_index": result.index.model_dump(mode="json"),
        "final_manuscript_present": True,
        "publication_ready": False,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"regeneration_id={result.report.regeneration_id}")
    typer.echo(f"regeneration_status={result.report.regeneration_status}")
    typer.echo(f"sections_generated={result.report.sections_generated}")
    typer.echo(f"supported_claim_count={result.report.supported_claim_count}")
    typer.echo(f"unsupported_claim_count={result.report.unsupported_claim_count}")
    typer.echo(f"deferred_gap_count={result.report.deferred_gap_count}")
    typer.echo("publication_ready=false")
    typer.echo(f"final_manuscript={result.manuscript_artifact.path}")


@app.command("build-domain-method-atlas")
def build_domain_method_atlas_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Persist the curated production-eligible domain and method atlas."""
    try:
        result = build_domain_method_atlas(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
        )
    except AtlasScanError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(result.report.model_dump_json(indent=2))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"scan_id={result.report.scan_id}")
    typer.echo(f"domain_count={result.report.domain_count}")
    typer.echo(f"method_count={result.report.method_count}")
    typer.echo("production_ready=true")
    typer.echo("publication_ready=false")
    typer.echo(f"artifact={result.report_artifact.path}")


@app.command("scan-domain-method-pairs")
def scan_domain_method_pairs_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    backend: Annotated[str, typer.Option("--backend")] = "llm-openai",
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    top_pairs: Annotated[int, typer.Option("--top-pairs")] = 30,
    model: Annotated[str, typer.Option("--model")] = DEFAULT_LLM_MODEL,
    allow_external_calls: Annotated[
        bool,
        typer.Option("--allow-external-calls"),
    ] = False,
    require_non_fake_backends: Annotated[
        bool,
        typer.Option("--require-non-fake-backends"),
    ] = False,
    batch_size: Annotated[int, typer.Option("--batch-size")] = 20,
    max_ranking_calls: Annotated[int, typer.Option("--max-ranking-calls")] = 50,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Rank compatible atlas pairs with an explicitly gated non-fake LLM."""
    normalized_backend = backend.strip().lower().replace("_", "-")
    if normalized_backend not in {"llm-openai", "openai"}:
        typer.echo(
            "Only the llm-openai atlas ranking backend is implemented; no deterministic "
            "ranking fallback is available.",
            err=True,
        )
        raise typer.Exit(code=1)
    api_key = os.environ.get(OPENAI_API_KEY_ENV, "")
    try:
        ranker = OpenAIAtlasPairRanker(
            api_key=api_key,
            model=model,
            allow_external_calls=allow_external_calls,
        )
        result = scan_domain_method_pairs(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            ranker=ranker,
            top_pairs=top_pairs,
            require_non_fake_backends=require_non_fake_backends,
            batch_size=batch_size,
            max_ranking_calls=max_ranking_calls,
        )
    except (AdapterError, AtlasScanError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(result.report.model_dump_json(indent=2))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"scan_id={result.report.scan_id}")
    typer.echo(f"raw_pair_count={result.report.raw_pair_count}")
    typer.echo(f"excluded_pair_count={result.report.excluded_pair_count}")
    typer.echo(f"surviving_pair_count={result.report.surviving_pair_count}")
    typer.echo(f"llm_ranked_pair_count={result.report.llm_ranked_pair_count}")
    typer.echo(f"selected_pair_count={result.report.selected_pair_count}")
    typer.echo(f"domain_family_coverage={result.report.domain_family_coverage}")
    typer.echo(f"method_family_coverage={result.report.method_family_coverage}")
    typer.echo(f"production_ready={str(result.report.production_ready).lower()}")
    typer.echo("publication_ready=false")
    typer.echo(f"artifact={result.report_artifact.path}")


@app.command("inspect-atlas-scan")
def inspect_atlas_scan_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest atlas build or LLM pair scan without mutation."""
    try:
        report = inspect_atlas_scan(run_id=run_id, root=root)
    except AtlasScanError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    typer.echo(render_atlas_scan_text(report))


@app.command("discover-deep-opportunities")
def discover_deep_opportunities_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    backend: Annotated[str, typer.Option("--backend")] = "llm-openai",
    retrieval_mode: Annotated[str, typer.Option("--retrieval-mode")] = "mocked",
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    model: Annotated[str, typer.Option("--model")] = DEFAULT_LLM_MODEL,
    allow_external_calls: Annotated[
        bool,
        typer.Option("--allow-external-calls"),
    ] = False,
    require_non_fake_backends: Annotated[
        bool,
        typer.Option("--require-non-fake-backends"),
    ] = False,
    max_pairs: Annotated[int, typer.Option("--max-pairs")] = 30,
    opportunities_per_pair: Annotated[
        int,
        typer.Option("--opportunities-per-pair"),
    ] = 3,
    max_selected_opportunities: Annotated[
        int,
        typer.Option("--max-selected-opportunities"),
    ] = 40,
    max_generation_calls: Annotated[
        int,
        typer.Option("--max-generation-calls"),
    ] = 30,
    max_retrieval_sources_per_pair: Annotated[
        int,
        typer.Option("--max-retrieval-sources-per-pair"),
    ] = 5,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Generate concrete retrieval-contextualized opportunities with a non-fake LLM."""
    normalized_backend = backend.strip().lower().replace("_", "-")
    if normalized_backend not in {"llm-openai", "openai"}:
        typer.echo(
            "Only the llm-openai deep opportunity backend is implemented; no deterministic "
            "generation fallback is available.",
            err=True,
        )
        raise typer.Exit(code=1)
    normalized_retrieval = retrieval_mode.strip().lower().replace("-", "_")
    if normalized_retrieval in {"mocked", "mock"}:
        normalized_retrieval = "mocked_retrieval"
    elif normalized_retrieval in {"real", "openalex"}:
        normalized_retrieval = "real_retrieval"
    if normalized_retrieval not in {"mocked_retrieval", "real_retrieval"}:
        typer.echo("retrieval-mode must be mocked or real", err=True)
        raise typer.Exit(code=1)

    try:
        generator = OpenAIDeepOpportunityGenerator(
            api_key=os.environ.get(OPENAI_API_KEY_ENV, ""),
            model=model,
            allow_external_calls=allow_external_calls,
        )
        if normalized_retrieval == "real_retrieval":
            retriever = OpenAlexOpportunityRetriever(
                OpenAlexRetrievalClient(
                    api_key=os.environ.get(OPENALEX_API_KEY_ENV, ""),
                    default_limit=max_retrieval_sources_per_pair,
                    allow_external_calls=allow_external_calls,
                )
            )
        else:
            retriever = MockedOpportunityRetriever()
        config = DeepOpportunityDiscoveryConfig(
            run_id=run_id,
            backend="llm-openai",
            retrieval_mode=normalized_retrieval,
            max_pairs=max_pairs,
            max_generation_calls=max_generation_calls,
            opportunities_per_pair=opportunities_per_pair,
            max_selected_opportunities=max_selected_opportunities,
            max_retrieval_sources_per_pair=max_retrieval_sources_per_pair,
            require_non_fake_backends=require_non_fake_backends,
        )
        result = discover_deep_opportunities(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            generator=generator,
            retriever=retriever,
            config=config,
        )
    except (AdapterError, DeepOpportunityDiscoveryError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(result.report.model_dump_json(indent=2))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"discovery_id={result.report.discovery_id}")
    typer.echo(f"selected_pair_count={result.report.selected_pair_count}")
    typer.echo(
        f"generated_opportunity_count={result.report.generated_opportunity_count}"
    )
    typer.echo(f"selected_opportunity_count={result.report.selected_opportunity_count}")
    typer.echo(f"retrieval_mode={result.report.config.retrieval_mode}")
    typer.echo(f"production_ready={str(result.report.production_ready).lower()}")
    typer.echo("publication_ready=false")
    typer.echo(f"artifact={result.report_artifact.path}")


@app.command("inspect-deep-opportunities")
def inspect_deep_opportunities_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest deep opportunity discovery report without mutation."""
    try:
        report = inspect_deep_opportunities(run_id=run_id, root=root)
    except DeepOpportunityDiscoveryError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    typer.echo(render_deep_opportunity_text(report))


@app.command("discover-opportunities")
def discover_opportunities_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    domain: Annotated[str, typer.Option("--domain")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    max_methods: Annotated[int, typer.Option("--max-methods")] = 20,
    require_non_fake_backends: Annotated[
        bool,
        typer.Option("--require-non-fake-backends"),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run deterministic Stage 0 domain-method opportunity discovery."""
    _reject_non_fake_requirement(
        required=require_non_fake_backends,
        stage_name="opportunity_discovery",
    )
    try:
        result = discover_opportunities(
            run_id=run_id,
            domain=domain,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            max_methods=max_methods,
        )
    except OpportunityDiscoveryError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(result.report.model_dump_json(indent=2))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"discovery_id={result.report.discovery_id}")
    typer.echo(f"domain={result.report.domain}")
    typer.echo(f"primitive_count={result.report.primitive_count}")
    typer.echo(f"method_lens_count={result.report.method_lens_count}")
    typer.echo(f"opportunity_count={result.report.opportunity_count}")
    typer.echo(f"promoted_count={result.report.promoted_count}")
    typer.echo(f"seed_constraint_count={result.report.seed_constraint_count}")
    typer.echo("publication_ready=false")
    typer.echo(f"artifact={result.report_artifact.path}")


@app.command("inspect-opportunities")
def inspect_opportunities_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect latest deterministic Stage 0 opportunity discovery."""
    try:
        report = inspect_opportunities(run_id=run_id, root=root)
    except OpportunityDiscoveryError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    typer.echo(render_opportunity_discovery_text(report))


@app.command("augment-variance")
def augment_variance_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    candidates_per_seed: Annotated[int, typer.Option("--candidates-per-seed")] = 4,
    max_total_candidates: Annotated[int, typer.Option("--max-total-candidates")] = 40,
    require_non_fake_backends: Annotated[
        bool,
        typer.Option("--require-non-fake-backends"),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Generate deterministic high-variance branches from promoted Stage 0 seeds."""
    _reject_non_fake_requirement(
        required=require_non_fake_backends,
        stage_name="variance_generation",
    )
    try:
        result = augment_variance(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            candidates_per_seed=candidates_per_seed,
            max_total_candidates=max_total_candidates,
        )
    except (VarianceAugmentationError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(result.report.model_dump_json(indent=2))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"augmentation_id={result.report.augmentation_id}")
    typer.echo(f"seed_count={result.report.seed_count}")
    typer.echo(f"candidate_count={result.report.candidate_count}")
    typer.echo(f"selected_candidate_count={result.report.selected_candidate_count}")
    typer.echo(f"method_lens_coverage={result.report.diversity_diagnostic.method_lens_coverage}")
    typer.echo(f"diversity_score={result.report.diversity_diagnostic.diversity_score}")
    typer.echo("publication_ready=false")
    typer.echo(f"artifact={result.report_artifact.path}")


@app.command("inspect-variance-augmentation")
def inspect_variance_augmentation_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect latest opportunity-seeded variance augmentation."""
    try:
        report = inspect_variance_augmentation(run_id=run_id, root=root)
    except VarianceAugmentationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    typer.echo(render_variance_augmentation_text(report))


@app.command("apply-variance-augmentation")
def apply_variance_augmentation_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Append selected variance branches for IdeaTree reconstruction."""
    try:
        result = apply_variance_augmentation(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
        )
    except VarianceAugmentationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(result.report.model_dump_json(indent=2))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"augmentation_id={result.report.augmentation_id}")
    typer.echo(f"idea_tree_nodes_added={result.report.idea_tree_nodes_added}")
    typer.echo("publication_ready=false")
    typer.echo(f"artifact={result.report_artifact.path}")


@app.command("inspect-idea-tree")
def inspect_idea_tree_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Reconstruct and inspect the creative search tree without mutation."""
    try:
        report = inspect_idea_tree(run_id=run_id, root=root)
    except IdeaTreeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    typer.echo(render_idea_tree_text(report))
    typer.echo("")
    typer.echo(f"Nodes: {report.node_count}")
    typer.echo(f"Edges: {report.edge_count}")
    typer.echo(f"Stage A candidates: {report.stage_a_node_count}")
    typer.echo(f"Stage B variants: {report.stage_b_node_count}")
    typer.echo(f"Stage C selected: {report.stage_c_selected_count}")
    typer.echo(f"Pruned nodes: {report.pruned_node_count}")
    typer.echo(f"Warnings: {len(report.warnings)}")
    typer.echo("Publication ready: false")


@app.command("export-idea-tree")
def export_idea_tree_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    export_format: Annotated[str, typer.Option("--format")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
) -> None:
    """Write an append-only context-only Markdown or JSON idea-tree export."""
    try:
        report = export_idea_tree(
            run_id=run_id,
            root=root,
            export_format=export_format,
        )
    except IdeaTreeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"run_id={report.run_id}")
    typer.echo(f"export_id={report.export_id}")
    typer.echo(f"format={report.export_format}")
    typer.echo(f"node_count={report.node_count}")
    typer.echo(f"edge_count={report.edge_count}")
    typer.echo("publication_ready=false")
    typer.echo(f"artifact={report.export_path}")


@app.command("inspect-idea-space")
def inspect_idea_space_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect deterministic idea-space diversity diagnostics without mutation."""
    try:
        report = inspect_idea_space(run_id=run_id, root=root)
    except IdeaSpaceError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    typer.echo(render_idea_space_text(report))


@app.command("export-idea-space-report")
def export_idea_space_report_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    export_format: Annotated[str, typer.Option("--format")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
) -> None:
    """Write an append-only context-only idea-space report."""
    try:
        artifact_path = export_idea_space_report(
            run_id=run_id,
            root=root,
            export_format=export_format,
        )
        report = inspect_idea_space(run_id=run_id, root=root)
    except IdeaSpaceError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"run_id={run_id}")
    typer.echo(f"format={export_format}")
    typer.echo(f"diversity_score={report.diversity_score}")
    typer.echo(f"effective_rank={report.effective_rank}")
    typer.echo(f"recommended_mutation_axes={len(report.recommended_mutation_axes)}")
    typer.echo("publication_ready=false")
    typer.echo(f"artifact={artifact_path}")


@app.command("promote-variance-substrates")
def promote_variance_substrates_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    max_substrates: Annotated[int, typer.Option("--max-substrates")] = 8,
    require_non_fake_backends: Annotated[
        bool,
        typer.Option("--require-non-fake-backends"),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Promote diverse variance candidates into concrete ScientificSubstrates."""
    _reject_non_fake_requirement(
        required=require_non_fake_backends,
        stage_name="substrate_construction",
    )
    try:
        result = promote_variance_substrates(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            max_substrates=max_substrates,
        )
    except (SubstratePromotionError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(result.report.model_dump_json(indent=2))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"promotion_id={result.report.promotion_id}")
    typer.echo(f"promoted_substrate_count={result.report.promoted_substrate_count}")
    typer.echo(f"method_lens_coverage={result.report.method_lens_coverage}")
    typer.echo(f"branch_family_coverage={result.report.branch_family_coverage}")
    typer.echo("publication_ready=false")
    typer.echo(f"artifact={result.report_artifact.path}")


@app.command("inspect-substrate-promotion")
def inspect_substrate_promotion_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect latest diversity-constrained substrate promotion."""
    try:
        report = inspect_substrate_promotion(run_id=run_id, root=root)
    except SubstratePromotionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    typer.echo(render_substrate_promotion_text(report))


@app.command("route-branches")
def route_branches_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    require_non_fake_backends: Annotated[
        bool,
        typer.Option("--require-non-fake-backends"),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Assign each latest ScientificSubstrate its deterministic next action."""
    _reject_non_fake_requirement(
        required=require_non_fake_backends,
        stage_name="branch_routing",
    )
    try:
        result = route_branches(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
        )
    except BranchRoutingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(result.plan.model_dump_json(indent=2))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"routing_id={result.plan.routing_id}")
    typer.echo(f"substrate_count={result.plan.substrate_count}")
    typer.echo(f"route_count={result.plan.route_count}")
    typer.echo(f"routed_count={result.plan.routed_count}")
    typer.echo(f"deferred_count={result.plan.deferred_count}")
    typer.echo(f"rejected_count={result.plan.rejected_count}")
    typer.echo("publication_ready=false")
    typer.echo(f"artifact={result.plan_artifact.path}")


@app.command("inspect-branch-routes")
def inspect_branch_routes_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest deterministic ScientificSubstrate route plan."""
    try:
        report = inspect_branch_routes(run_id=run_id, root=root)
    except BranchRoutingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    typer.echo(render_branch_route_text(report))


@app.command("build-route-execution-specs")
def build_route_execution_specs_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    require_non_fake_backends: Annotated[
        bool,
        typer.Option("--require-non-fake-backends"),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Build immutable deterministic execution specs for latest branch routes."""
    _reject_non_fake_requirement(
        required=require_non_fake_backends,
        stage_name="experiment_design",
    )
    try:
        result = build_route_execution_specs(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
        )
    except RouteExecutionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(result.report.model_dump_json(indent=2))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"report_id={result.report.report_id}")
    typer.echo(f"spec_count={result.report.spec_count}")
    typer.echo(f"unsupported_route_count={sum(result.report.unsupported_route_counts.values())}")
    typer.echo("publication_ready=false")
    typer.echo(f"artifact={result.report_artifact.path}")


@app.command("run-route-execution")
def run_route_execution_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    require_non_fake_backends: Annotated[
        bool,
        typer.Option("--require-non-fake-backends"),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run supported route specs through deterministic offline evaluators."""
    _reject_non_fake_requirement(
        required=require_non_fake_backends,
        stage_name="route_execution_fixture_metrics",
    )
    try:
        result = run_route_execution(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
        )
    except RouteExecutionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(result.report.model_dump_json(indent=2))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"report_id={result.report.report_id}")
    typer.echo(f"result_count={result.report.result_count}")
    typer.echo(f"executed_count={result.report.executed_count}")
    typer.echo(f"deferred_count={result.report.deferred_count}")
    typer.echo(f"failed_count={result.report.failed_count}")
    typer.echo("publication_ready=false")
    typer.echo(f"artifact={result.report_artifact.path}")


@app.command("inspect-route-execution")
def inspect_route_execution_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect latest route execution specs and bounded results."""
    try:
        report = inspect_route_execution(run_id=run_id, root=root)
    except RouteExecutionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    typer.echo(render_route_execution_text(report))


@app.command("inspect-backends")
def inspect_backends_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect scientific-stage backend authority without mutating the run."""
    try:
        report = inspect_backends(run_id=run_id, root=root)
    except ProductionModeError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    typer.echo(render_production_mode_text(report))


@app.command("check-production-mode")
def check_production_mode_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    require_non_fake_backends: Annotated[
        bool,
        typer.Option("--require-non-fake-backends"),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Persist a backend-authority check and fail on strict blocking violations."""
    try:
        result = check_production_mode(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            require_non_fake_backends=require_non_fake_backends,
        )
    except (ProductionModeError, LedgerError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(result.report.model_dump_json(indent=2))
    else:
        typer.echo(render_production_mode_text(result.report))
        typer.echo(f"artifact={result.report_artifact.path}")
    if result.report.blocking_violation_count:
        raise typer.Exit(code=1)


@app.command("build-scientific-substrate")
def build_scientific_substrate_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    max_substrates: Annotated[int, typer.Option("--max-substrates")] = 2,
    mutation_axis: Annotated[str | None, typer.Option("--mutation-axis")] = None,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Build concrete scientific substrates from idea-space mutation axes."""
    try:
        result = build_scientific_substrate(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            max_substrates=max_substrates,
            mutation_axis=mutation_axis,
        )
    except ScientificSubstrateError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "scientific_substrate_present": True,
        "build_report": result.report.model_dump(mode="json"),
        "substrates": [substrate.model_dump(mode="json") for substrate in result.substrates],
        "publication_ready": False,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"build_id={result.report.build_id}")
    typer.echo(f"build_status={result.report.build_status}")
    typer.echo(f"substrate_count={result.report.substrate_count}")
    typer.echo(f"selected_substrate={result.report.selected_substrate_title_optional}")
    typer.echo("publication_ready=false")
    typer.echo(f"artifact={result.report_artifact.path}")


@app.command("inspect-scientific-substrate")
def inspect_scientific_substrate_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect latest generated scientific substrates without mutation."""
    try:
        report = inspect_scientific_substrate(run_id=run_id, root=root)
    except ScientificSubstrateError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    status = "present" if report.scientific_substrate_present else "absent"
    typer.echo(f"Scientific substrate: {status}")
    typer.echo(f"Substrate count: {report.substrate_count}")
    typer.echo(f"Selected substrate: {report.selected_substrate_title_optional or 'none'}")
    typer.echo(f"PCA/low-rank alternative: {str(report.pca_low_rank_substrate_present).lower()}")
    typer.echo(f"Equation present: {str(report.equation_present).lower()}")
    typer.echo(f"Baseline present: {str(report.baseline_present).lower()}")
    typer.echo(f"Experiment design present: {str(report.experiment_design_present).lower()}")
    typer.echo(f"Result schema present: {str(report.result_schema_present).lower()}")
    typer.echo("Publication ready: false")


@app.command("route-substrate-experiment")
def route_substrate_experiment_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Route the selected scientific substrate to an approved local experiment."""
    try:
        result = route_substrate_experiment(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
        )
    except SubstrateExperimentRoutingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        **result.report.model_dump(mode="json"),
        "generated_experiment_spec_path": (
            result.spec_artifact.path if result.spec_artifact else None
        ),
        "publication_ready": False,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"routing_status={result.report.routing_status}")
    typer.echo(
        f"substrate_experiment_routed={str(result.report.substrate_experiment_routed).lower()}"
    )
    typer.echo(f"selected_substrate={result.report.selected_substrate_title_optional or 'none'}")
    spec_path = (
        result.report.generated_experiment_spec_path_optional
        or result.report.existing_experiment_spec_path_optional
        or "none"
    )
    typer.echo(f"experiment_spec={spec_path}")
    typer.echo("publication_ready=false")


@app.command("inspect-substrate-experiment-routing")
def inspect_substrate_experiment_routing_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest substrate-specific experiment route and outcome."""
    try:
        payload = inspect_substrate_experiment_routing(run_id=run_id, root=root)
    except SubstrateExperimentRoutingError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"Substrate experiment routing: {payload['routing_status']}")
    typer.echo(f"Selected substrate: {payload['selected_substrate_title_optional'] or 'none'}")
    typer.echo(f"Experiment bundle: {payload['experiment_bundle_optional'] or 'none'}")
    typer.echo(f"Sandbox status: {payload['sandbox_status'] or 'not run'}")
    typer.echo(f"Comparison table present: {str(payload['comparison_table_present']).lower()}")
    typer.echo(f"Claim-evidence linked: {str(payload['claim_evidence_linked']).lower()}")
    typer.echo("Publication ready: false")


@app.command("run-substrate-tournament")
def run_substrate_tournament_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run a bounded uv-local tournament across serious scientific substrates."""
    try:
        result = run_substrate_tournament(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
        )
    except SubstrateTournamentError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        **result.result.model_dump(mode="json"),
        "tournament_present": True,
        "publication_ready": False,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"tournament_status={result.result.tournament_status}")
    typer.echo(f"substrate_count={result.result.substrate_count}")
    typer.echo(f"winner_selected={str(result.result.winner_selected).lower()}")
    typer.echo(f"winner={result.result.winner_substrate_title_optional or 'none'}")
    typer.echo(
        f"comparison_table_present={str(result.result.comparison.comparison_table_present).lower()}"
    )
    typer.echo("publication_ready=false")


@app.command("inspect-substrate-tournament")
def inspect_substrate_tournament_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest substrate tournament and selected bounded branch."""
    try:
        report = inspect_substrate_tournament(run_id=run_id, root=root)
    except SubstrateTournamentError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    status = "present" if report.tournament_present else "absent"
    typer.echo(f"Substrate tournament: {status}")
    typer.echo(f"Tournament status: {report.tournament_status_optional or 'none'}")
    typer.echo(f"Substrate count: {report.substrate_count}")
    typer.echo(
        f"Distance-decay branch completed: {str(report.distance_decay_branch_completed).lower()}"
    )
    typer.echo(
        f"PCA/low-rank branch completed: {str(report.pca_low_rank_branch_completed).lower()}"
    )
    typer.echo(f"Winner selected: {str(report.winner_selected).lower()}")
    typer.echo(f"Winner: {report.winner_substrate_title_optional or 'none'}")
    typer.echo(f"Comparison table: {str(report.comparison_table_present).lower()}")
    typer.echo("Publication ready: false")


@app.command("run-mutation-tournament")
def run_mutation_tournament_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run a second-generation uv-local tournament over mutation substrates."""
    try:
        result = run_mutation_tournament(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
        )
    except MutationTournamentError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        **result.result.model_dump(mode="json"),
        "mutation_tournament_present": True,
        "publication_ready": False,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"tournament_status={result.result.tournament_status}")
    typer.echo(f"original_winner_included={str(result.result.original_winner_included).lower()}")
    typer.echo(f"mutation_substrate_count={result.result.mutation_substrate_count}")
    typer.echo(
        "second_generation_winner_selected="
        f"{str(result.result.second_generation_winner_selected).lower()}"
    )
    typer.echo(
        f"second_generation_winner="
        f"{result.result.second_generation_winner_title_optional or 'none'}"
    )
    typer.echo(f"tournament_outcome={result.result.tournament_outcome}")
    typer.echo(
        f"comparison_table_present={str(result.result.comparison.comparison_table_present).lower()}"
    )
    typer.echo("publication_ready=false")


@app.command("inspect-mutation-tournament")
def inspect_mutation_tournament_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest mutation tournament and second-generation winner."""
    try:
        report = inspect_mutation_tournament(run_id=run_id, root=root)
    except MutationTournamentError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    status = "present" if report.mutation_tournament_present else "absent"
    typer.echo(f"Mutation tournament: {status}")
    typer.echo(f"Tournament status: {report.tournament_status_optional or 'none'}")
    typer.echo(f"Original winner included: {str(report.original_winner_included).lower()}")
    typer.echo(
        "Hierarchical alpha branch completed: "
        f"{str(report.hierarchical_alpha_branch_completed).lower()}"
    )
    typer.echo(
        f"Hybrid low-rank branch completed: {str(report.hybrid_low_rank_branch_completed).lower()}"
    )
    typer.echo(
        "Boundary robustness branch completed: "
        f"{str(report.boundary_robustness_branch_completed).lower()}"
    )
    typer.echo(
        "Second-generation winner selected: "
        f"{str(report.second_generation_winner_selected).lower()}"
    )
    typer.echo(f"Winner: {report.second_generation_winner_title_optional or 'none'}")
    typer.echo(
        f"Mutation improved over original: {str(report.mutation_improved_over_original).lower()}"
    )
    typer.echo(f"Comparison table: {str(report.comparison_table_present).lower()}")
    typer.echo("Publication ready: false")


@app.command("run-creative-search")
def run_creative_search_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    max_cycles: Annotated[int, typer.Option("--max-cycles")] = 3,
    min_improvement: Annotated[float, typer.Option("--min-improvement")] = 0.01,
    max_mutations_per_cycle: Annotated[int, typer.Option("--max-mutations-per-cycle")] = 3,
    max_substrates_per_cycle: Annotated[int, typer.Option("--max-substrates-per-cycle")] = 3,
    stop_if_no_improvement: Annotated[
        bool, typer.Option("--stop-if-no-improvement/--no-stop-if-no-improvement")
    ] = True,
    stop_if_diversity_collapses: Annotated[
        bool,
        typer.Option("--stop-if-diversity-collapses/--no-stop-if-diversity-collapses"),
    ] = True,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Run bounded recursive creative search with local deterministic stages."""
    try:
        result = run_creative_search(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            max_cycles=max_cycles,
            min_improvement=min_improvement,
            max_mutations_per_cycle=max_mutations_per_cycle,
            max_substrates_per_cycle=max_substrates_per_cycle,
            stop_if_no_improvement=stop_if_no_improvement,
            stop_if_diversity_collapses=stop_if_diversity_collapses,
        )
    except CreativeSearchError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(result.report.model_dump_json(indent=2))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo("creative_search_present=true")
    typer.echo(f"search_id={result.report.search_id}")
    typer.echo(f"controller_status={result.report.controller_status}")
    typer.echo(f"cycle_count={result.report.cycle_count}")
    typer.echo(f"stop_reason={result.report.stop_reason.value}")
    typer.echo(f"best_current_winner={result.report.best_current_winner}")
    typer.echo(f"best_current_score={result.report.best_current_score}")
    typer.echo(
        "final_bundle_verification_status="
        f"{result.report.final_bundle_verification_status_optional or 'none'}"
    )
    typer.echo("publication_ready=false")


@app.command("inspect-creative-search")
def inspect_creative_search_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest recursive creative-search report without mutation."""
    report = inspect_creative_search(run_id=run_id, root=root)
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    status = "present" if report.creative_search_present else "absent"
    typer.echo(f"Creative search: {status}")
    typer.echo(f"Controller status: {report.controller_status_optional or 'none'}")
    typer.echo(f"Cycles: {report.cycle_count}")
    stop_reason = report.stop_reason_optional.value if report.stop_reason_optional else "none"
    typer.echo(f"Stop reason: {stop_reason}")
    typer.echo(f"Lineage present: {str(report.lineage_present).lower()}")
    typer.echo(f"Starting winner: {report.starting_winner_optional or 'none'}")
    typer.echo(f"Ending winner: {report.ending_winner_optional or 'none'}")
    typer.echo(f"Best current score: {report.best_current_score_optional or 0.0}")
    typer.echo("Publication ready: false")


@app.command("plan-creative-mutations")
def plan_creative_mutations_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    max_mutations: Annotated[int, typer.Option("--max-mutations")] = 5,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Plan tournament-driven creative scientific mutations."""
    try:
        plan = plan_creative_mutations(
            run_id=run_id,
            root=root,
            max_mutations=max_mutations,
            write_report=True,
        )
    except CreativeMutationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(plan.model_dump_json(indent=2))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo("creative_mutation_plan_present=true")
    typer.echo(f"plan_id={plan.plan_id}")
    typer.echo(f"mutation_count={plan.mutation_count}")
    typer.echo(f"selected_for_substrate_build={plan.selected_for_substrate_build_count}")
    for candidate in plan.candidates:
        typer.echo(
            f"- {candidate.title} [{candidate.operator.value}] "
            f"selected={str(candidate.selected_for_substrate_build).lower()}"
        )
    typer.echo("publication_ready=false")


@app.command("inspect-creative-mutations")
def inspect_creative_mutations_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect latest creative mutation plan and application reports."""
    try:
        report = inspect_creative_mutations(run_id=run_id, root=root)
    except CreativeMutationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    status = "present" if report.creative_mutation_plan_present else "absent"
    typer.echo(f"Creative mutation plan: {status}")
    typer.echo(f"Latest plan: {report.latest_plan_id_optional or 'none'}")
    typer.echo(f"Latest apply report: {report.latest_apply_report_id_optional or 'none'}")
    typer.echo(f"Mutation count: {report.mutation_count}")
    typer.echo(f"Selected for substrate build: {report.selected_for_substrate_build_count}")
    typer.echo(f"Applied mutations: {report.applied_mutation_count}")
    typer.echo(f"New IdeaTree nodes added: {str(report.new_idea_tree_nodes_added).lower()}")
    typer.echo(
        f"New ScientificSubstrates created: {str(report.new_scientific_substrates_created).lower()}"
    )
    typer.echo("Publication ready: false")


@app.command("apply-creative-mutations")
def apply_creative_mutations_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    max_mutations: Annotated[int, typer.Option("--max-mutations")] = 3,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Apply selected creative mutations as IdeaTree nodes and ScientificSubstrates."""
    try:
        result = apply_creative_mutations(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            max_mutations=max_mutations,
        )
    except CreativeMutationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        **result.report.model_dump(mode="json"),
        "creative_mutation_plan_present": True,
        "new_idea_tree_nodes_added": result.report.new_idea_tree_node_count > 0,
        "new_scientific_substrates_created": (result.report.new_scientific_substrate_count > 0),
        "publication_ready": False,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"apply_status={result.report.apply_status}")
    typer.echo(f"applied_mutation_count={result.report.applied_mutation_count}")
    typer.echo(f"new_idea_tree_node_count={result.report.new_idea_tree_node_count}")
    typer.echo(f"new_scientific_substrate_count={result.report.new_scientific_substrate_count}")
    typer.echo("publication_ready=false")


@app.command("plan-generation-mutations")
def plan_generation_mutations_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    cycle_index: Annotated[int | None, typer.Option("--cycle-index")] = None,
    max_mutations: Annotated[int, typer.Option("--max-mutations")] = 5,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Plan fresh current-winner-conditioned generation mutations."""
    try:
        plan = plan_generation_mutations(
            run_id=run_id,
            root=root,
            cycle_index=cycle_index,
            max_mutations=max_mutations,
            write_report=True,
        )
    except GenerationMutationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(plan.model_dump_json(indent=2))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo("generation_mutation_plan_present=true")
    typer.echo(f"plan_id={plan.plan_id}")
    typer.echo(f"cycle_index={plan.context.cycle_index}")
    typer.echo(f"current_winner={plan.context.current_winner_title}")
    typer.echo(f"planning_status={plan.planning_status}")
    typer.echo(f"mutation_count={plan.mutation_count}")
    typer.echo(f"selected_for_substrate_build={plan.selected_for_substrate_build_count}")
    typer.echo("publication_ready=false")


@app.command("inspect-generation-mutations")
def inspect_generation_mutations_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect latest generation mutation planning and application."""
    try:
        report = inspect_generation_mutations(run_id=run_id, root=root)
    except GenerationMutationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(report.model_dump_json(indent=2))
        return
    status = "present" if report.generation_mutation_plan_present else "absent"
    typer.echo(f"Generation mutation plan: {status}")
    typer.echo(f"Current winner: {report.current_winner_optional or 'none'}")
    typer.echo(f"Mutations: {report.mutation_count}")
    typer.echo(f"Selected: {report.selected_for_substrate_build_count}")
    typer.echo(f"Applied: {report.applied_mutation_count}")
    typer.echo(f"New IdeaTree nodes: {report.new_idea_tree_node_count}")
    typer.echo(f"New ScientificSubstrates: {report.new_scientific_substrate_count}")
    typer.echo("Publication ready: false")


@app.command("apply-generation-mutations")
def apply_generation_mutations_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    max_mutations: Annotated[int, typer.Option("--max-mutations")] = 3,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Append selected generation mutations as IdeaTree/substrate context."""
    try:
        result = apply_generation_mutations(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            max_mutations=max_mutations,
        )
    except GenerationMutationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(result.report.model_dump_json(indent=2))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"plan_id={result.plan.plan_id}")
    typer.echo(f"applied_mutation_count={result.report.applied_mutation_count}")
    typer.echo(f"new_idea_tree_node_count={result.report.new_idea_tree_node_count}")
    typer.echo(f"new_scientific_substrate_count={result.report.new_scientific_substrate_count}")
    typer.echo("publication_ready=false")


@app.command("inspect-final-manuscript")
def inspect_final_manuscript_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest final manuscript regeneration without mutation."""
    try:
        summary = inspect_final_manuscript(run_id=run_id, root=root)
    except FinalManuscriptRegenerationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Final manuscript: {summary['run_id']}")
    typer.echo(f"Status: {summary['final_manuscript_regeneration_status']}")
    typer.echo(f"Sections generated: {summary['final_manuscript_sections_generated']}")
    typer.echo(f"Supported claims: {summary['final_manuscript_supported_claim_count']}")
    typer.echo(f"Unsupported claims: {summary['final_manuscript_unsupported_claim_count']}")
    typer.echo(f"Deferred gaps: {summary['final_manuscript_deferred_gap_count']}")
    typer.echo("Publication ready: false")
    typer.echo(f"Artifact: {summary['final_manuscript_path']}")


@app.command("build-final-release-bundle")
def build_final_release_bundle_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    compile_pdf: Annotated[bool, typer.Option("--compile-pdf")] = False,
    strict_export: Annotated[bool, typer.Option("--strict-export")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Assemble the deterministic final release bundle and reproducibility manifests."""
    try:
        result = build_final_release_bundle(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            compile_pdf=compile_pdf,
            strict_export=strict_export,
        )
    except FinalReleaseBundleError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "final_release_bundle": result.report.model_dump(mode="json"),
        "final_release_bundle_index": result.index.model_dump(mode="json"),
        "publication_ready": False,
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"bundle_id={result.report.bundle_id}")
    typer.echo(f"bundle_status={result.report.bundle_status}")
    typer.echo(f"bundle_path={result.report.bundle_path}")
    typer.echo(f"artifact_count={result.report.artifact_count}")
    typer.echo(f"hash_count={result.report.hash_count}")
    typer.echo(f"missing_required_artifacts={len(result.report.missing_required_artifacts)}")
    typer.echo(
        f"paper_tex_present={str(result.report.paper_tex_path_optional is not None).lower()}"
    )
    typer.echo(
        "references_bib_present="
        f"{str(result.report.references_bib_path_optional is not None).lower()}"
    )
    typer.echo(f"paper_pdf_present={str(result.report.pdf_path_optional is not None).lower()}")
    typer.echo("publication_ready=false")
    typer.echo(f"report={result.report_artifact.path}")


@app.command("inspect-final-release-bundle")
def inspect_final_release_bundle_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest final release bundle without mutation."""
    try:
        summary = inspect_final_release_bundle(run_id=run_id, root=root)
    except FinalReleaseBundleError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Final release bundle: {summary['run_id']}")
    typer.echo(f"Status: {summary['final_release_bundle_status']}")
    typer.echo(f"Bundle path: {summary['final_release_bundle_path']}")
    typer.echo(f"Artifacts included: {summary['final_release_bundle_artifact_count']}")
    typer.echo(f"Hashes written: {summary['final_release_bundle_hash_count']}")
    typer.echo(f"paper.tex present: {str(summary['paper_tex_present']).lower()}")
    typer.echo(f"references.bib present: {str(summary['references_bib_present']).lower()}")
    typer.echo(f"paper.pdf present: {str(summary['paper_pdf_present']).lower()}")
    typer.echo(
        "Missing required artifacts: "
        f"{summary['final_release_bundle_missing_required_artifact_count']}"
    )
    typer.echo(
        "Bundle verification: "
        f"{'present' if summary.get('final_bundle_verification_present') else 'absent'}"
    )
    typer.echo(
        f"Verification status: {summary.get('final_bundle_verification_status') or 'not_available'}"
    )
    typer.echo(
        "Verification checks passed/failed/warned: "
        f"{int(summary.get('final_bundle_checks_passed') or 0)}/"
        f"{int(summary.get('final_bundle_checks_failed') or 0)}/"
        f"{int(summary.get('final_bundle_checks_warned') or 0)}"
    )
    typer.echo(
        f"Verification hash mismatches: {int(summary.get('final_bundle_hash_mismatch_count') or 0)}"
    )
    typer.echo("Publication ready: false")


@app.command("verify-final-release-bundle")
def verify_final_release_bundle_command(
    bundle_path: Annotated[Path | None, typer.Option("--bundle-path")] = None,
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    write_report: Annotated[bool, typer.Option("--write-report")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Verify a final release bundle by inspection without modifying it."""
    try:
        report = verify_final_release_bundle(
            bundle_path=bundle_path,
            run_id=run_id,
            root=root,
            write_report=write_report,
        )
    except FinalBundleVerificationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True))
        return
    typer.echo(f"Final bundle verification: {report.bundle_path}")
    typer.echo(f"Status: {report.verification_status}")
    typer.echo(
        "Checks passed/failed/warned: "
        f"{report.checks_passed}/{report.checks_failed}/{report.checks_warned}"
    )
    typer.echo(f"Hashes verified: {report.hashes_verified}")
    typer.echo(f"Hash mismatches: {report.hash_mismatch_count}")
    typer.echo(f"Missing required artifacts: {report.missing_required_artifact_count}")
    typer.echo(f"Rejected reference leaks: {report.rejected_reference_leak_count}")
    typer.echo(f"Accepted references check: {str(report.accepted_reference_check_passed).lower()}")
    typer.echo(f"Claim-evidence check: {str(report.claim_evidence_check_passed).lower()}")
    typer.echo(f"Publication ready: {str(report.publication_ready).lower()}")


@app.command("inspect-gap-attempt-history")
def inspect_gap_attempt_history_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest gap-attempt history without mutation."""
    try:
        summary = inspect_gap_attempt_history(run_id=run_id, root=root)
    except GapAttemptHistoryError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Gap attempt history: {summary['run_id']}")
    typer.echo(f"Gap count: {summary['gap_count']}")
    typer.echo(f"Attempt count: {summary['gap_attempt_count']}")
    typer.echo(f"Open gaps: {summary['open_gap_count']}")
    typer.echo(f"Exhausted/no-progress gaps: {summary['gap_exhausted_no_progress_count']}")
    typer.echo(f"Deferred gaps: {summary['remaining_deferred_gap_count']}")
    typer.echo(f"Strategy attempts: {summary.get('strategy_attempt_count', 0)}")
    typer.echo(f"Diversified strategies: {summary.get('diversified_strategy_count', 0)}")
    typer.echo(
        "Deferred after strategy exhaustion: "
        f"{summary.get('gaps_deferred_after_strategy_exhaustion', 0)}"
    )
    typer.echo(f"Resolved gaps: {summary['resolved_gap_count']}")
    typer.echo("Publication ready: false")
    typer.echo(f"Artifact: {summary['gap_attempt_history_path']}")


@app.command("inspect-planned-spec-dedup")
def inspect_planned_spec_dedup_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect the latest planned-spec de-duplication index without mutation."""
    try:
        summary = inspect_planned_spec_dedup(run_id=run_id, root=root)
    except GapAttemptHistoryError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Planned spec de-dup index: {summary['run_id']}")
    typer.echo(f"Spec count: {summary['spec_count']}")
    typer.echo(f"Unique specs: {summary['unique_spec_count']}")
    typer.echo(f"Duplicate specs: {summary['duplicate_planned_spec_count']}")
    typer.echo("Publication ready: false")
    typer.echo(f"Artifact: {summary['planned_spec_dedup_index_path']}")


@app.command("refresh-evidence-aware-manuscript")
def refresh_evidence_aware_manuscript_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    backend: Annotated[
        str,
        typer.Option("--evidence-aware-refresh-backend"),
    ] = "off",
    model: Annotated[
        str,
        typer.Option("--evidence-aware-refresh-model"),
    ] = DEFAULT_LLM_MODEL,
    max_calls: Annotated[
        int,
        typer.Option("--max-evidence-aware-refresh-calls"),
    ] = 0,
    allow_external_calls: Annotated[
        bool,
        typer.Option("--allow-external-calls"),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Refresh final manuscript wording from scoped claim-evidence links."""
    try:
        result = refresh_evidence_aware_manuscript(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            backend=backend,
            model=model,
            max_calls=max_calls,
            allow_external_calls=allow_external_calls,
        )
    except EvidenceAwareRefreshError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "evidence_aware_refresh_report": result.report.model_dump(mode="json"),
        "release": result.release_status,
        "publication_ready": False,
        "artifacts": {
            "refresh_report": result.report_artifact.model_dump(mode="json"),
            "manuscript": result.manuscript_artifact.model_dump(mode="json"),
            "claim_evidence_map": result.claim_evidence_map_artifact.model_dump(mode="json"),
            "reviewer_summary": result.reviewer_summary_artifact.model_dump(mode="json"),
        },
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"refresh_backend={result.report.refresh_backend}")
    typer.echo(f"refresh_status={result.report.refresh_status}")
    typer.echo(f"proof_language_inserted={str(result.report.proof_language_inserted).lower()}")
    typer.echo(
        f"experiment_language_inserted={str(result.report.experiment_language_inserted).lower()}"
    )
    typer.echo(
        "claim_evidence_map_rechecked="
        f"{str(result.report.claim_evidence_map_rechecked_after_refresh).lower()}"
    )
    typer.echo(f"release={result.release_status}")
    typer.echo("publication_ready=false")
    typer.echo(f"refresh_report={result.report_artifact.path}")


@app.command("ingest-reviewer-change-requests")
def ingest_reviewer_change_requests_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    request_file: Annotated[Path, typer.Option("--request-file")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Validate and persist immutable structured reviewer requests."""
    try:
        result = ingest_reviewer_change_requests(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
            request_file=request_file,
        )
    except ReviewerChangeRequestError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "reviewer_change_request_set": result.request_set.model_dump(mode="json"),
        "request_set_number": result.request_set_number,
        "reviewer_change_requests_present": True,
        "publication_ready": False,
        "artifact": result.request_set_artifact.model_dump(mode="json"),
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"request_set_id={result.request_set.request_set_id}")
    typer.echo(f"request_set_number={result.request_set_number}")
    typer.echo(f"request_count={len(result.request_set.requests)}")
    typer.echo("publication_ready=false")
    typer.echo(f"artifact={result.request_set_artifact.path}")


@app.command("inspect-reviewer-change-requests")
def inspect_reviewer_change_requests_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect immutable structured reviewer request sets."""
    try:
        summary = inspect_reviewer_change_requests(run_id=run_id, root=root)
    except ReviewerChangeRequestError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Reviewer change requests: {summary['run_id']}")
    typer.echo(f"Request sets: {summary['reviewer_request_set_count']}")
    typer.echo(f"Latest request set: {summary['latest_request_set_id']}")
    typer.echo(f"Requests: {summary['request_count']}")
    typer.echo("Publication ready: false")


@app.command("reconcile-human-review")
def reconcile_human_review_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Reconcile human-review requests using bounded deterministic revisions."""
    try:
        result = reconcile_human_review(
            run_id=run_id,
            root=root,
            store=ArtifactStore(root),
            ledger=_ledger(root, run_id),
        )
    except HumanReviewReconciliationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    payload = {
        "human_review_reconciliation": result.report.model_dump(mode="json"),
        "release": result.release_status,
        "publication_ready": False,
        "artifacts": {
            "reconciliation_report": result.report_artifact.model_dump(mode="json"),
            "reconciliation_markdown": result.report_markdown_artifact.model_dump(mode="json"),
            "manuscript": result.manuscript_artifact.model_dump(mode="json"),
            "claim_evidence_map": result.claim_evidence_map_artifact.model_dump(mode="json"),
            "reviewer_summary": result.reviewer_summary_artifact.model_dump(mode="json"),
            "reconciliation_index": result.reconciliation_index_artifact.model_dump(mode="json"),
        },
    }
    if json_output:
        typer.echo(json.dumps(payload, indent=2, sort_keys=True))
        return
    typer.echo(f"run_id={run_id}")
    typer.echo(f"cycle={result.report.cycle_number}")
    typer.echo(f"reconciliation_status={result.report.reconciliation_status}")
    typer.echo(f"applied_changes={result.report.applied_change_count}")
    typer.echo(f"rejected_changes={result.report.rejected_change_count}")
    typer.echo(f"deferred_changes={result.report.deferred_change_count}")
    typer.echo(f"requires_new_evidence={result.report.requires_new_evidence_count}")
    typer.echo(f"release={result.release_status}")
    typer.echo("publication_ready=false")
    typer.echo(f"reconciliation_report={result.report_artifact.path}")


@app.command("inspect-human-review-reconciliation")
def inspect_human_review_reconciliation_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect a persisted human-review reconciliation without mutation."""
    try:
        summary = inspect_human_review_reconciliation(run_id=run_id, root=root)
    except HumanReviewReconciliationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    typer.echo(f"Human-review reconciliation: {summary['run_id']}")
    typer.echo(f"Status: {summary['reconciliation_status']}")
    typer.echo(f"Applied changes: {summary['applied_change_count']}")
    typer.echo(f"Rejected changes: {summary['rejected_change_count']}")
    typer.echo(f"Deferred changes: {summary['deferred_change_count']}")
    typer.echo(f"Requires new evidence: {summary['requires_new_evidence_count']}")
    typer.echo("Publication ready: false")
    typer.echo(f"Artifact: {summary['human_review_reconciliation_report_path']}")


@app.command("run-llm-paper")
def run_llm_paper_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    domain: Annotated[str, typer.Option("--domain")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    method: Annotated[str | None, typer.Option("--method")] = None,
    candidate_backend: Annotated[
        str,
        typer.Option("--candidate-backend"),
    ] = DEFAULT_ADAPTER_BACKEND,
    reviewer_backend: Annotated[
        str,
        typer.Option("--reviewer-backend"),
    ] = DEFAULT_REVIEWER_BACKEND,
    prose_backend: Annotated[
        str,
        typer.Option("--prose-backend"),
    ] = DEFAULT_PROSE_BACKEND,
    llm_scope: Annotated[
        str,
        typer.Option("--llm-scope"),
    ] = "full-paper",
    allow_external_calls: Annotated[
        bool,
        typer.Option("--allow-external-calls"),
    ] = DEFAULT_ALLOW_EXTERNAL_CALLS,
    candidate_model: Annotated[
        str,
        typer.Option("--candidate-model", "--llm-model"),
    ] = DEFAULT_LLM_MODEL,
    reviewer_model: Annotated[
        str,
        typer.Option("--reviewer-model"),
    ] = DEFAULT_LLM_MODEL,
    prose_model: Annotated[str, typer.Option("--prose-model")] = DEFAULT_LLM_MODEL,
    claim_adjudicator_backend: Annotated[
        str,
        typer.Option("--claim-adjudicator-backend"),
    ] = "off",
    claim_adjudicator_model: Annotated[
        str,
        typer.Option("--claim-adjudicator-model"),
    ] = DEFAULT_LLM_MODEL,
    source_relevance_adjudicator_backend: Annotated[
        str,
        typer.Option("--source-relevance-adjudicator-backend"),
    ] = "off",
    source_relevance_adjudicator_model: Annotated[
        str,
        typer.Option("--source-relevance-adjudicator-model"),
    ] = DEFAULT_LLM_MODEL,
    quality_repair_backend: Annotated[
        str,
        typer.Option("--quality-repair-backend"),
    ] = "off",
    quality_repair_model: Annotated[
        str,
        typer.Option("--quality-repair-model"),
    ] = DEFAULT_LLM_MODEL,
    reviewer_max_objections: Annotated[
        int,
        typer.Option("--reviewer-max-objections"),
    ] = DEFAULT_REVIEWER_MAX_OBJECTIONS,
    max_total_calls: Annotated[
        int | None,
        typer.Option("--max-total-calls"),
    ] = None,
    max_candidate_generation_calls: Annotated[
        int | None,
        typer.Option("--max-candidate-generation-calls"),
    ] = None,
    max_review_calls: Annotated[
        int | None,
        typer.Option("--max-review-calls"),
    ] = None,
    max_prose_calls: Annotated[
        int | None,
        typer.Option("--max-prose-calls"),
    ] = None,
    max_claim_adjudication_calls: Annotated[
        int | None,
        typer.Option("--max-claim-adjudication-calls"),
    ] = None,
    max_source_relevance_adjudication_calls: Annotated[
        int | None,
        typer.Option("--max-source-relevance-adjudication-calls"),
    ] = None,
    max_quality_repair_calls: Annotated[
        int | None,
        typer.Option("--max-quality-repair-calls"),
    ] = None,
    max_total_input_tokens: Annotated[
        int | None,
        typer.Option("--max-total-input-tokens"),
    ] = None,
    max_total_output_tokens: Annotated[
        int | None,
        typer.Option("--max-total-output-tokens"),
    ] = None,
    max_estimated_cost_usd: Annotated[
        float | None,
        typer.Option("--max-estimated-cost-usd"),
    ] = None,
    max_wallclock_seconds: Annotated[
        int | None,
        typer.Option("--max-wallclock-seconds"),
    ] = None,
    max_retries_per_call: Annotated[
        int,
        typer.Option("--max-retries-per-call"),
    ] = 0,
    rate_limit_per_minute: Annotated[
        int | None,
        typer.Option("--rate-limit-per-minute"),
    ] = None,
    fail_on_budget_unknown: Annotated[
        bool,
        typer.Option("--fail-on-budget-unknown/--allow-unknown-budget"),
    ] = True,
    generate_paper: Annotated[
        bool,
        typer.Option("--generate-paper/--skip-generate-paper"),
    ] = True,
    evaluate_release: Annotated[
        bool,
        typer.Option("--evaluate-release/--skip-evaluate-release"),
    ] = True,
    include_citations: Annotated[
        bool,
        typer.Option("--include-citations/--no-citations"),
    ] = True,
    enable_retrieval: Annotated[
        bool,
        typer.Option("--enable-retrieval/--disable-retrieval"),
    ] = False,
    retrieval_backend: Annotated[
        str,
        typer.Option("--retrieval-backend"),
    ] = "fake",
    retrieval_local_path: Annotated[
        str | None,
        typer.Option("--retrieval-local-path"),
    ] = None,
    max_retrieval_sources: Annotated[
        int,
        typer.Option("--max-retrieval-sources"),
    ] = 5,
    citation_policy: Annotated[
        str,
        typer.Option("--citation-policy"),
    ] = "none",
    export_latex: Annotated[
        bool,
        typer.Option("--export-latex/--skip-export-latex"),
    ] = True,
    critique: Annotated[
        bool,
        typer.Option("--critique/--skip-critique"),
    ] = True,
    revise: Annotated[bool, typer.Option("--revise")] = False,
    apply_safe_fake_revision: Annotated[
        bool,
        typer.Option("--apply-safe-fake-revision"),
    ] = False,
    reexport_latex_after_revision: Annotated[
        bool,
        typer.Option("--reexport-latex-after-revision"),
    ] = False,
    render_check: Annotated[bool, typer.Option("--render-check")] = False,
    allow_external_tools: Annotated[
        bool,
        typer.Option("--allow-external-tools"),
    ] = DEFAULT_ALLOW_EXTERNAL_TOOLS,
    latex_executable: Annotated[
        str | None,
        typer.Option("--latex-executable"),
    ] = None,
    write_report: Annotated[bool, typer.Option("--write-report")] = False,
    preflight_only: Annotated[bool, typer.Option("--preflight-only")] = False,
    enable_safe_repair: Annotated[
        bool,
        typer.Option("--enable-safe-repair"),
    ] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
    rerun_policy: Annotated[
        str,
        typer.Option("--rerun-policy"),
    ] = "fail-if-exists",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Run explicit gated LLM-assisted paper generation orchestration."""
    config = LLMOrchestrationConfig(
        run_id=run_id,
        domain=domain,
        method=method,
        candidate_backend=candidate_backend,
        reviewer_backend=reviewer_backend,
        prose_backend=prose_backend,
        allow_external_calls=allow_external_calls,
        llm_model=candidate_model,
        reviewer_model=reviewer_model,
        prose_model=prose_model,
        claim_adjudicator_backend=claim_adjudicator_backend,
        claim_adjudicator_model=claim_adjudicator_model,
        source_relevance_adjudicator_backend=source_relevance_adjudicator_backend,
        source_relevance_adjudicator_model=source_relevance_adjudicator_model,
        quality_repair_backend=quality_repair_backend,
        quality_repair_model=quality_repair_model,
        reviewer_max_objections=reviewer_max_objections,
        generate_paper=generate_paper,
        evaluate_release=evaluate_release,
        include_citations=include_citations,
        enable_retrieval=enable_retrieval,
        retrieval_backend=retrieval_backend,
        retrieval_local_path=retrieval_local_path,
        max_retrieval_sources=max_retrieval_sources,
        citation_policy=citation_policy,
        export_latex=export_latex,
        critique=critique,
        revise=revise,
        apply_safe_fake_revision=apply_safe_fake_revision,
        reexport_latex_after_revision=reexport_latex_after_revision,
        render_check=render_check,
        allow_external_tools=allow_external_tools,
        latex_executable=latex_executable,
        write_report=write_report,
        rerun_policy=rerun_policy,
        force=force,
        budget=LLMBudgetConfig(
            max_total_calls=max_total_calls,
            max_candidate_generation_calls=max_candidate_generation_calls,
            max_review_calls=max_review_calls,
            max_prose_calls=max_prose_calls,
            max_claim_adjudication_calls=max_claim_adjudication_calls,
            max_source_relevance_adjudication_calls=(max_source_relevance_adjudication_calls),
            max_quality_repair_calls=max_quality_repair_calls,
            max_total_input_tokens=max_total_input_tokens,
            max_total_output_tokens=max_total_output_tokens,
            max_estimated_cost_usd=max_estimated_cost_usd,
            max_wallclock_seconds=max_wallclock_seconds,
            max_retries_per_call=max_retries_per_call,
            rate_limit_per_minute=rate_limit_per_minute,
            fail_on_budget_unknown=fail_on_budget_unknown,
        ),
    )
    try:
        preflight_summary = build_llm_orchestration_preflight_summary(
            config,
            llm_scope=llm_scope,
            enable_safe_repair=enable_safe_repair,
        )
    except LLMOrchestrationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    try:
        result = run_llm_paper_orchestration(
            config=config,
            root=root,
            preflight_only=preflight_only,
            llm_scope=llm_scope,
            enable_safe_repair=enable_safe_repair,
        )
    except LLMOrchestrationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    result_model = llm_orchestration_result_model(result)
    if json_output:
        typer.echo(
            json.dumps(
                {
                    "preflight_summary": preflight_summary,
                    "llm_orchestration_result": result_model.model_dump(mode="json"),
                    "artifacts": {
                        "llm_orchestration_config": (
                            result.config_artifact.model_dump(mode="json")
                            if result.config_artifact
                            else None
                        ),
                        "llm_budget_report": (
                            result.budget_artifact.model_dump(mode="json")
                            if result.budget_artifact
                            else None
                        ),
                        "llm_call_accounting": (
                            result.accounting_artifact.model_dump(mode="json")
                            if result.accounting_artifact
                            else None
                        ),
                        "llm_orchestration_report": (
                            result.report_artifact.model_dump(mode="json")
                            if result.report_artifact
                            else None
                        ),
                        "llm_run_safety_report": (
                            result.safety_artifact.model_dump(mode="json")
                            if result.safety_artifact
                            else None
                        ),
                        "safe_repair_report": (
                            result.generation_result.revision_result.safe_repair_report_artifact.model_dump(
                                mode="json"
                            )
                            if result.generation_result is not None
                            and result.generation_result.revision_result is not None
                            and result.generation_result.revision_result.safe_repair_report_artifact
                            is not None
                            else None
                        ),
                        "quality_repair_report": (
                            result.generation_result.quality_repair_report_artifact.model_dump(
                                mode="json"
                            )
                            if result.generation_result is not None
                            and result.generation_result.quality_repair_report_artifact is not None
                            else None
                        ),
                    },
                },
                indent=2,
                sort_keys=True,
            )
        )
        return
    report = result.report
    typer.echo(f"run_id={run_id}")
    typer.echo(f"llm_orchestration_status={report.orchestration_status.value}")
    typer.echo(f"llm_scope={preflight_summary['llm_scope']}")
    typer.echo(f"candidate_backend={candidate_backend}")
    typer.echo(f"candidate_model={candidate_model}")
    typer.echo(f"reviewer_backend={reviewer_backend}")
    typer.echo(f"reviewer_model={reviewer_model}")
    typer.echo(f"prose_backend={prose_backend}")
    typer.echo(f"prose_model={prose_model}")
    typer.echo(f"source_relevance_adjudicator_backend={source_relevance_adjudicator_backend}")
    typer.echo(f"source_relevance_adjudicator_model={source_relevance_adjudicator_model}")
    typer.echo(f"quality_repair_backend={quality_repair_backend}")
    typer.echo(f"quality_repair_model={quality_repair_model}")
    typer.echo(f"allow_external_calls={str(allow_external_calls).lower()}")
    typer.echo(f"preflight_only={str(preflight_only).lower()}")
    typer.echo(f"estimated_max_calls={preflight_summary['estimated_max_calls']}")
    typer.echo(
        "source_relevance_adjudication_calls="
        f"{preflight_summary['source_relevance_adjudication_calls']}"
    )
    typer.echo(f"quality_repair_calls={preflight_summary['quality_repair_calls']}")
    typer.echo(
        f"generate_paper_effective={str(preflight_summary['generate_paper_effective']).lower()}"
    )
    typer.echo(
        f"evaluate_release_effective={str(preflight_summary['evaluate_release_effective']).lower()}"
    )
    typer.echo(f"export_latex_effective={str(preflight_summary['export_latex_effective']).lower()}")
    typer.echo(f"budget_status={report.budget_decision.decision_status.value}")
    typer.echo(f"total_llm_calls={report.budget_usage.total_calls}")
    typer.echo(f"rate_limit_per_minute={rate_limit_per_minute or 'none'}")
    typer.echo(f"generate_paper_status={report.generate_paper_status or 'skipped'}")
    typer.echo(f"release_status={report.release_status or 'skipped'}")
    typer.echo(f"warnings={len(report.warnings)}")
    typer.echo(f"blocking_issues={len(report.blocking_issues)}")
    typer.echo("publication_ready=false")
    typer.echo("is_verification_evidence=false")
    typer.echo("creates_scientific_validation=false")
    if result.report_artifact is not None:
        typer.echo(f"llm_orchestration_report={result.report_artifact.path}")
    if result.budget_artifact is not None:
        typer.echo(f"llm_budget_report={result.budget_artifact.path}")
    if result.accounting_artifact is not None:
        typer.echo(f"llm_call_accounting={result.accounting_artifact.path}")


@app.command("inspect-llm-run")
def inspect_llm_run_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect an existing persisted LLM orchestration run without mutation."""
    try:
        summary = inspect_llm_run_summary(run_id=run_id, root=root)
    except LLMRunInspectionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    _print_llm_run_summary(summary)


def _print_llm_run_summary(summary: dict[str, object]) -> None:
    estimated_cost = summary.get("estimated_cost_usd")
    budget_line = (
        f"${float(estimated_cost):.2f} estimated"
        if estimated_cost is not None
        else "unknown estimated cost"
    )
    blocking = list(summary.get("blocking_issues") or [])
    warnings = list(summary.get("top_level_warnings") or [])
    artifact_paths = dict(summary.get("artifact_paths") or {})
    typer.echo(f"Run: {summary['run_id']}")
    typer.echo(f"Status: {summary['orchestration_status']}")
    typer.echo(f"Release: {summary.get('paper_release_status') or 'unknown'}")
    typer.echo(f"Publication ready: {str(summary.get('publication_ready', False)).lower()}")
    typer.echo("Safety: safe" if summary.get("safety_report_safe") else "Safety: unsafe")
    typer.echo(
        "Calls: "
        f"{summary['total_calls']} total = "
        f"{summary['candidate_generation_calls']} candidate + "
        f"{summary['review_calls']} review + "
        f"{summary['prose_calls']} prose + "
        f"{summary.get('claim_adjudication_calls', 0)} claim adjudication + "
        f"{summary.get('source_relevance_adjudication_calls', 0)} source relevance + "
        f"{summary.get('quality_repair_calls', 0)} quality repair"
    )
    typer.echo(f"Budget: {budget_line}")
    typer.echo(
        f"Runtime budget blocked: {str(summary.get('runtime_budget_blocked', False)).lower()}"
    )
    typer.echo(
        "Call records: "
        f"{summary['external_call_count']} external, "
        f"{summary['failed_call_count']} failed, "
        f"{summary['blocked_call_count']} blocked, "
        f"{summary['skipped_call_count']} skipped"
    )
    typer.echo(
        f"Safe repair: {'present' if summary.get('safe_repair_report_present') else 'absent'}"
    )
    typer.echo("Blocking issues:")
    if blocking:
        for issue in blocking:
            typer.echo(f"- {issue}")
    else:
        typer.echo("- none")
    typer.echo("Warnings:")
    if warnings:
        for warning in warnings:
            typer.echo(f"- {warning}")
    else:
        typer.echo("- none")
    if artifact_paths:
        typer.echo("Artifacts:")
        for key, path in sorted(artifact_paths.items()):
            typer.echo(f"- {key}: {path}")


@app.command("inspect-paper-bundle")
def inspect_paper_bundle_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect generated paper bundle artifacts without mutation."""
    try:
        summary = inspect_paper_bundle_summary(run_id=run_id, root=root)
    except PaperBundleInspectionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    _print_paper_bundle_summary(summary)


def _print_paper_bundle_summary(summary: dict[str, object]) -> None:
    artifacts = dict(summary.get("artifacts") or {})
    primary = str(summary.get("primary_artifact_to_read") or "none")
    primary_name = Path(primary).name if primary != "none" else "none"
    release = summary.get("release_status") or "unknown"
    safe_repair = "present" if summary.get("safe_repair_report_exists") else "absent"
    quality_repair = "present" if summary.get("quality_repair_report_present") else "absent"
    reviewer_summary = "present" if summary.get("reviewer_bundle_summary_present") else "absent"
    citations = "present" if summary.get("citations_present") else "absent"
    blocking_count = int(summary.get("blocking_issue_count") or 0)
    warning_count = int(summary.get("warning_count") or 0)
    typer.echo(f"Paper bundle: {summary['run_id']}")
    typer.echo(f"Primary draft: {primary_name}")
    typer.echo(f"Release: {release}")
    typer.echo(f"Safe repair: {safe_repair}")
    typer.echo(f"Quality repair: {quality_repair}")
    typer.echo(
        f"Final manuscript: {'present' if summary.get('final_manuscript_present') else 'absent'}"
    )
    typer.echo(
        "Final regeneration status: "
        f"{summary.get('final_manuscript_regeneration_status') or 'not_available'}"
    )
    typer.echo(
        "Final sections / supported claims / deferred gaps / unsupported claims: "
        f"{int(summary.get('final_manuscript_sections_generated') or 0)} / "
        f"{int(summary.get('final_manuscript_supported_claim_count') or 0)} / "
        f"{int(summary.get('final_manuscript_deferred_gap_count') or 0)} / "
        f"{int(summary.get('final_manuscript_unsupported_claim_count') or 0)}"
    )
    typer.echo(
        "Final release bundle: "
        f"{'present' if summary.get('final_release_bundle_present') else 'absent'}"
    )
    typer.echo(f"Bundle status: {summary.get('final_release_bundle_status') or 'not_available'}")
    typer.echo(f"Bundle path: {summary.get('final_release_bundle_path') or 'not_available'}")
    typer.echo(
        "Artifacts included / hashes written: "
        f"{int(summary.get('final_release_bundle_artifact_count') or 0)} / "
        f"{int(summary.get('final_release_bundle_hash_count') or 0)}"
    )
    typer.echo(
        "paper.tex / references.bib / paper.pdf: "
        f"{str(bool(summary.get('paper_tex_present'))).lower()}/"
        f"{str(bool(summary.get('references_bib_present'))).lower()}/"
        f"{str(bool(summary.get('paper_pdf_present'))).lower()}"
    )
    typer.echo(
        "Missing required bundle artifacts: "
        f"{int(summary.get('final_release_bundle_missing_required_artifact_count') or 0)}"
    )
    typer.echo(f"Final bundle verified: {summary.get('final_bundle_verified', 'unknown')}")
    typer.echo(
        "Final bundle verification status: "
        f"{summary.get('final_bundle_verification_status') or 'not_available'}"
    )
    typer.echo(
        "Final bundle verification hash mismatches / missing required: "
        f"{int(summary.get('final_bundle_hash_mismatch_count') or 0)} / "
        f"{int(summary.get('final_bundle_missing_required_artifact_count') or 0)}"
    )
    typer.echo(
        "Autonomous paper run: "
        f"{'present' if summary.get('autonomous_paper_run_present') else 'absent'}"
    )
    typer.echo(
        "Controller / handoff status: "
        f"{summary.get('autonomous_paper_controller_status') or 'not_available'} / "
        f"{summary.get('autonomous_paper_handoff_status') or 'not_available'}"
    )
    typer.echo(
        "Autonomous final bundle / verification: "
        f"{summary.get('autonomous_paper_final_bundle_path') or 'not_available'} / "
        f"{summary.get('autonomous_paper_final_verification_status') or 'not_available'}"
    )
    typer.echo(
        "Autonomous deferred gaps / human intervention: "
        f"{int(summary.get('autonomous_paper_deferred_gap_count') or 0)} / "
        f"{str(bool(summary.get('autonomous_paper_human_intervention_required'))).lower()}"
    )
    typer.echo(
        "Base generation root failure: "
        f"{summary.get('root_base_generation_failure_stage') or 'none'} / "
        f"{summary.get('root_base_generation_failure_reason') or 'none'}"
    )
    typer.echo(
        "Base generation counts: "
        f"candidates={int(summary.get('candidate_count') or 0)}, "
        f"stage_a_survivors={int(summary.get('stage_a_survivor_count') or 0)}, "
        f"stage_b_survivors={int(summary.get('stage_b_survivor_count') or 0)}, "
        f"stage_c_ready={int(summary.get('stage_c_ready_count') or 0)}, "
        f"manuscript_plan_present="
        f"{str(bool(summary.get('manuscript_plan_present'))).lower()}"
    )
    typer.echo(
        "Controller checkpoints: "
        f"{'present' if summary.get('autonomous_paper_checkpoint_present') else 'absent'}"
    )
    typer.echo(
        "Latest completed checkpoint / resume allowed: "
        f"{summary.get('autonomous_paper_latest_completed_checkpoint') or 'not_available'} / "
        f"{str(bool(summary.get('autonomous_paper_resume_allowed'))).lower()}"
    )
    typer.echo(
        "Latest resume status / stages reused / rerun: "
        f"{summary.get('autonomous_paper_latest_resume_status') or 'not_available'} / "
        f"{int(summary.get('autonomous_paper_stages_reused_count') or 0)} / "
        f"{int(summary.get('autonomous_paper_stages_rerun_count') or 0)}"
    )
    typer.echo(f"Resume blockers: {int(summary.get('autonomous_paper_resume_blocker_count') or 0)}")
    typer.echo(
        "Evidence-aware refresh: "
        f"{'present' if summary.get('evidence_aware_refresh_report_present') else 'absent'}"
    )
    typer.echo(f"Refresh backend: {summary.get('evidence_aware_refresh_backend') or 'off'}")
    typer.echo(
        f"Proof language inserted: {str(bool(summary.get('proof_language_inserted'))).lower()}"
    )
    typer.echo(
        "Experiment language inserted: "
        f"{str(bool(summary.get('experiment_language_inserted'))).lower()}"
    )
    typer.echo(
        "Claim-evidence map rechecked: "
        f"{str(bool(summary.get('claim_evidence_map_rechecked_after_refresh'))).lower()}"
    )
    typer.echo(
        "Human-review reconciliation: "
        f"{'present' if summary.get('human_review_reconciliation_present') else 'absent'}"
    )
    typer.echo(
        "Reconciliation status: "
        f"{summary.get('human_review_reconciliation_status') or 'not_available'}"
    )
    typer.echo(f"Applied changes: {int(summary.get('human_review_applied_change_count') or 0)}")
    typer.echo(f"Rejected changes: {int(summary.get('human_review_rejected_change_count') or 0)}")
    typer.echo(f"Deferred changes: {int(summary.get('human_review_deferred_change_count') or 0)}")
    typer.echo(
        "Requires new evidence: "
        f"{int(summary.get('human_review_requires_new_evidence_count') or 0)}"
    )
    typer.echo(
        "Reviewer change requests: "
        f"{'present' if summary.get('reviewer_change_requests_present') else 'absent'}"
    )
    typer.echo(f"Reviewer request sets: {int(summary.get('reviewer_request_set_count') or 0)}")
    typer.echo(
        f"Reconciliation cycles: {int(summary.get('human_review_reconciliation_cycle_count') or 0)}"
    )
    typer.echo(
        f"Latest reconciliation cycle: {int(summary.get('latest_reconciliation_cycle') or 0)}"
    )
    typer.echo(f"Unresolved requests: {int(summary.get('unresolved_reviewer_request_count') or 0)}")
    typer.echo(
        "Autonomous evidence plan: "
        f"{'present' if summary.get('autonomous_evidence_plan_present') else 'absent'}"
    )
    typer.echo(f"Autonomous plan items: {int(summary.get('autonomous_plan_item_count') or 0)}")
    typer.echo(f"Automation-ready items: {int(summary.get('automation_ready_item_count') or 0)}")
    typer.echo(
        "Human intervention required: "
        f"{str(bool(summary.get('autonomous_human_intervention_required'))).lower()}"
    )
    typer.echo(
        "Autonomous execution: "
        f"{'present' if summary.get('autonomous_execution_present') else 'absent'}"
    )
    typer.echo(
        "Latest execution mode: "
        f"{summary.get('latest_autonomous_execution_mode') or 'not_available'}"
    )
    typer.echo(
        "Latest execution status: "
        f"{summary.get('latest_autonomous_execution_status') or 'not_available'}"
    )
    typer.echo(
        "Autonomous actions applied/deferred/rejected/failed: "
        f"{int(summary.get('autonomous_actions_applied') or 0)}/"
        f"{int(summary.get('autonomous_actions_deferred') or 0)}/"
        f"{int(summary.get('autonomous_actions_rejected') or 0)}/"
        f"{int(summary.get('autonomous_actions_failed') or 0)}"
    )
    typer.echo(f"Created specs: {int(summary.get('autonomous_created_spec_count') or 0)}")
    typer.echo(
        "Planned spec execution: "
        f"{'present' if summary.get('planned_spec_execution_present') else 'absent'}"
    )
    typer.echo(
        "Latest planned spec execution: "
        f"{summary.get('latest_planned_spec_execution_mode') or 'not_available'} / "
        f"{summary.get('latest_planned_spec_execution_status') or 'not_available'}"
    )
    typer.echo(f"Experiment specs executed: {int(summary.get('experiment_specs_executed') or 0)}")
    typer.echo(f"Proof specs executed: {int(summary.get('proof_specs_executed') or 0)}")
    typer.echo(f"Retrieval specs executed: {int(summary.get('retrieval_specs_executed') or 0)}")
    typer.echo(
        f"Experiment artifacts created: {int(summary.get('experiment_artifacts_created') or 0)}"
    )
    typer.echo(f"Proof artifacts created: {int(summary.get('proof_artifacts_created') or 0)}")
    typer.echo(
        f"Retrieval artifacts created: {int(summary.get('retrieval_artifacts_created') or 0)}"
    )
    typer.echo(
        "Python sandbox: "
        f"{'present' if summary.get('python_experiment_sandbox_present') else 'absent'}"
    )
    typer.echo(
        f"Latest sandbox status: {summary.get('latest_python_sandbox_status') or 'not_available'}"
    )
    typer.echo(
        "Completed sandbox runs: "
        f"{int(summary.get('python_experiment_sandbox_completed_count') or 0)}"
    )
    typer.echo(
        "Python experiment artifacts created: "
        f"{int(summary.get('python_experiment_artifacts_created_count') or 0)}"
    )
    typer.echo(
        "Python sandbox network disabled: "
        f"{str(summary.get('python_experiment_sandbox_network_disabled', True)).lower()}"
    )
    typer.echo(
        "Experiment gap routing: "
        f"{'present' if summary.get('experiment_gap_routing_present') else 'absent'}"
    )
    typer.echo(f"Routed experiment gaps: {int(summary.get('routed_experiment_gap_count') or 0)}")
    typer.echo(f"Bounded empirical gaps: {int(summary.get('bounded_empirical_gap_count') or 0)}")
    typer.echo(
        f"Needs-python-experiment items: {int(summary.get('needs_python_experiment_count') or 0)}"
    )
    typer.echo(f"Routed empirical gaps: {int(summary.get('routed_empirical_gap_count') or 0)}")
    typer.echo(
        f"Created experiment specs: {int(summary.get('created_experiment_spec_count') or 0)}"
    )
    typer.echo(
        "Sandbox experiments completed: "
        f"{int(summary.get('sandbox_experiment_completed_count') or 0)}"
    )
    typer.echo(
        "Experiment artifacts ingested: "
        f"{int(summary.get('experiment_artifacts_ingested_count') or 0)}"
    )
    typer.echo(
        "Sandbox budget used/remaining: "
        f"{int(summary.get('sandbox_budget_runs_used') or 0)}/"
        f"{int(summary.get('sandbox_budget_runs_remaining') or 0)}"
    )
    typer.echo(f"Budget exhausted: {str(bool(summary.get('sandbox_budget_exhausted'))).lower()}")
    typer.echo(
        "Capability escalation: "
        f"{'present' if summary.get('capability_escalation_present') else 'absent'}"
    )
    typer.echo(
        f"Escalation status: {summary.get('capability_escalation_status') or 'not_available'}"
    )
    typer.echo(
        f"Proof escalations attempted: {int(summary.get('proof_escalation_attempt_count') or 0)}"
    )
    typer.echo(
        "Retrieval escalations attempted: "
        f"{int(summary.get('retrieval_escalation_attempt_count') or 0)}"
    )
    typer.echo(f"Successful escalations: {int(summary.get('successful_escalation_count') or 0)}")
    typer.echo(
        f"Deferred after escalation: {int(summary.get('deferred_after_escalation_count') or 0)}"
    )
    typer.echo(
        "Network allowed: "
        f"{str(bool(summary.get('capability_escalation_network_allowed'))).lower()}"
    )
    typer.echo(
        "External tools allowed: "
        f"{str(bool(summary.get('capability_escalation_external_tools_allowed'))).lower()}"
    )
    typer.echo(
        f"Autonomous loop: {'present' if summary.get('autonomous_loop_present') else 'absent'}"
    )
    typer.echo(
        f"Latest loop status: {summary.get('latest_autonomous_loop_status') or 'not_available'}"
    )
    typer.echo(
        "Iterations completed: "
        f"{int(summary.get('latest_autonomous_loop_iterations_completed') or 0)}"
    )
    typer.echo(
        f"Stop reason: {summary.get('latest_autonomous_loop_stop_reason') or 'not_available'}"
    )
    typer.echo(
        f"Terminal state: {summary.get('autonomous_loop_terminal_state') or 'not_available'}"
    )
    typer.echo(
        "Terminal reason: "
        f"{summary.get('autonomous_loop_terminal_state_reason') or 'not_available'}"
    )
    typer.echo(
        "Resolved/deferred/exhausted/duplicate-only gaps: "
        f"{int(summary.get('autonomous_loop_resolved_gap_count') or 0)}/"
        f"{int(summary.get('autonomous_loop_deferred_gap_count') or 0)}/"
        f"{int(summary.get('autonomous_loop_exhausted_gap_count') or 0)}/"
        f"{int(summary.get('autonomous_loop_duplicate_only_gap_count') or 0)}"
    )
    typer.echo(
        "Automation-ready after history: "
        f"{int(summary.get('autonomous_loop_automation_ready_after_history_count') or 0)}"
    )
    typer.echo(
        "Stopped before max iterations: "
        f"{str(bool(summary.get('autonomous_loop_stopped_before_max_iterations'))).lower()}"
    )
    typer.echo(
        "Final unsupported claims: "
        f"{int(summary.get('autonomous_loop_final_unsupported_claim_count') or 0)}"
    )
    typer.echo(
        "Final automation-ready items: "
        f"{int(summary.get('autonomous_loop_final_automation_ready_item_count') or 0)}"
    )
    typer.echo(
        "Human intervention required by loop: "
        f"{str(bool(summary.get('autonomous_loop_requires_human_intervention'))).lower()}"
    )
    typer.echo(
        "Gap attempt history: "
        f"{'present' if summary.get('gap_attempt_history_present') else 'absent'}"
    )
    typer.echo(f"Duplicate specs skipped: {int(summary.get('duplicate_specs_skipped') or 0)}")
    typer.echo(
        f"Gaps exhausted/no-progress: {int(summary.get('gap_exhausted_no_progress_count') or 0)}"
    )
    typer.echo(
        "Strategy diversification: "
        f"{'present' if summary.get('strategy_diversification_present') else 'absent'}"
    )
    typer.echo(f"Strategy options: {int(summary.get('strategy_option_count') or 0)}")
    typer.echo(f"Selected strategies: {int(summary.get('selected_strategy_count') or 0)}")
    typer.echo(f"Duplicate strategies: {int(summary.get('duplicate_strategy_count') or 0)}")
    typer.echo(
        "Deferred after all strategies exhausted: "
        f"{int(summary.get('gaps_deferred_after_strategy_exhaustion') or 0)}"
    )
    typer.echo(
        "Remaining automation-ready items after history: "
        f"{int(summary.get('automation_ready_item_count') or 0)}"
    )
    typer.echo(f"Reviewer summary: {reviewer_summary}")
    typer.echo(f"Reviewer summary status: {summary.get('reviewer_summary_status') or 'absent'}")
    typer.echo(f"Evidence gaps: {int(summary.get('reviewer_summary_evidence_gap_count') or 0)}")
    typer.echo(
        "Human-review checklist items: "
        f"{int(summary.get('reviewer_summary_human_checklist_count') or 0)}"
    )
    typer.echo(
        f"Human review: {'present' if summary.get('human_review_artifact_present') else 'absent'}"
    )
    typer.echo(f"Human review status: {summary.get('human_review_status') or 'not_available'}")
    typer.echo(
        "Blocking human-review concerns: "
        f"{int(summary.get('human_review_blocking_concern_count') or 0)}"
    )
    typer.echo(f"Requested changes: {int(summary.get('human_review_requested_change_count') or 0)}")
    typer.echo(
        f"Recommended next action: {summary.get('human_review_recommended_next_action') or 'none'}"
    )
    typer.echo(f"Proof artifacts: {int(summary.get('proof_artifact_count') or 0)}")
    typer.echo(
        "Formal verification artifacts passed: "
        f"{int(summary.get('formal_verification_passed_count') or 0)}"
    )
    typer.echo(f"Experiment artifacts: {int(summary.get('experiment_artifact_count') or 0)}")
    typer.echo(f"Completed experiments: {int(summary.get('completed_experiment_count') or 0)}")
    typer.echo(f"Remaining evidence gaps: {int(summary.get('remaining_evidence_gap_count') or 0)}")
    typer.echo(
        "Claim-evidence map: "
        f"{'present' if summary.get('claim_evidence_map_present') else 'absent'}"
    )
    typer.echo(f"Supported claims: {int(summary.get('claim_evidence_supported_count') or 0)}")
    typer.echo(
        f"Partially supported claims: {int(summary.get('claim_evidence_partial_count') or 0)}"
    )
    typer.echo(f"Unsupported claims: {int(summary.get('claim_evidence_unsupported_count') or 0)}")
    typer.echo(f"Proof-supported claims: {int(summary.get('proof_supported_claim_count') or 0)}")
    typer.echo(
        f"Experiment-supported claims: {int(summary.get('experiment_supported_claim_count') or 0)}"
    )
    typer.echo(
        f"Citation-supported claims: {int(summary.get('citation_supported_claim_count') or 0)}"
    )
    typer.echo(f"Quality repair backend: {summary.get('quality_repair_backend') or 'off'}")
    typer.echo(
        f"Quality repaired sections: {int(summary.get('quality_repaired_section_count') or 0)}"
    )
    typer.echo(f"Sections repaired: {int(summary.get('quality_repaired_section_count') or 0)}")
    typer.echo(
        "Depth targets met: "
        f"{int(summary.get('section_depth_target_met_count') or 0)}/"
        f"{int(summary.get('section_depth_target_total') or 0)}"
    )
    typer.echo(f"Warnings reduced: {int(summary.get('warnings_reduced_count') or 0)}")
    typer.echo(
        "Quality status before/after: "
        f"{summary.get('quality_status_before_repair') or 'unknown'} / "
        f"{summary.get('quality_status_after_repair') or 'unknown'}"
    )
    typer.echo(f"Main-body sections: {summary.get('main_body_section_count', 0)}")
    typer.echo(f"Appendix sections: {summary.get('appendix_section_count', 0)}")
    typer.echo(f"Total headings: {summary.get('total_heading_count', 0)}")
    typer.echo(f"Words: {int(summary.get('word_count') or 0):,}")
    typer.echo(f"Citations: {citations}")
    typer.echo(
        f"Citation registry: {'present' if summary.get('citation_registry_present') else 'absent'}"
    )
    typer.echo(f"Registry sources: {int(summary.get('citation_registry_source_count') or 0)}")
    typer.echo(
        f"Unregistered citations: {len(list(summary.get('unregistered_citation_keys') or []))}"
    )
    typer.echo(f"Bibliography: {summary.get('bibliography_status') or 'absent'}")
    typer.echo(
        "Retrieval quality: "
        f"{'present' if summary.get('retrieval_quality_report_present') else 'absent'}"
    )
    typer.echo(f"Retrieved sources: {int(summary.get('retrieved_source_count') or 0)}")
    typer.echo(f"Accepted sources: {int(summary.get('accepted_source_count') or 0)}")
    typer.echo(f"Rejected sources: {int(summary.get('rejected_source_count') or 0)}")
    typer.echo(f"Adequacy: {summary.get('retrieval_adequacy_status') or 'not_evaluated'}")
    typer.echo(
        "Source relevance adjudication: "
        f"{summary.get('source_relevance_adjudicator_backend') or 'off'}"
    )
    typer.echo(
        f"Adjudicated sources: {int(summary.get('source_relevance_adjudicated_count') or 0)}"
    )
    typer.echo(
        f"LLM accepted sources: {int(summary.get('source_relevance_llm_accepted_count') or 0)}"
    )
    typer.echo(
        f"LLM rejected sources: {int(summary.get('source_relevance_llm_rejected_count') or 0)}"
    )
    typer.echo(
        f"Hard rejected sources: {int(summary.get('source_relevance_hard_reject_count') or 0)}"
    )
    typer.echo(
        f"Claim support: {'present' if summary.get('claim_support_audit_present') else 'absent'}"
    )
    typer.echo(
        "Registry-supported claims: "
        f"{int(summary.get('claim_support_registry_supported_count') or 0)}"
    )
    typer.echo(
        "Missing citation claims: "
        f"{int(summary.get('claim_support_missing_required_citation_count') or 0)}"
    )
    typer.echo(f"Scope mismatches: {int(summary.get('claim_support_scope_mismatch_count') or 0)}")
    typer.echo(
        "Citation validation misuse: "
        f"{int(summary.get('citation_as_validation_misuse_count') or 0)}"
    )
    typer.echo(
        "Claim adjudication: "
        f"{summary.get('claim_adjudicator_backend') or 'off'} "
        f"({int(summary.get('adjudicated_sentence_count') or 0)} sentences)"
    )
    typer.echo(f"Blocking issues: {blocking_count if blocking_count else 'none'}")
    typer.echo(f"Warnings: {warning_count}")
    typer.echo(f"Title: {summary.get('title_detected') or 'unknown'}")
    typer.echo(f"Abstract: {'present' if summary.get('abstract_detected') else 'absent'}")
    if artifacts:
        typer.echo("Artifacts:")
        _echo_named_artifact(artifacts, "revised manuscript", "revised_manuscript_draft")
        _echo_named_artifact(artifacts, "complete manuscript", "complete_manuscript_draft")
        _echo_named_artifact(artifacts, "revised latex", "revised_paper")
        _echo_named_artifact(artifacts, "latex", "paper")
        _echo_named_artifact(artifacts, "source map", "revised_latex_source_map")
        _echo_named_artifact(artifacts, "source map", "latex_source_map")
        _echo_named_artifact(artifacts, "generation report", "generation_report")
        _echo_named_artifact(artifacts, "release report", "release_report")
        _echo_named_artifact(artifacts, "safe repair report", "safe_repair_report")
        _echo_named_artifact(
            artifacts,
            "quality repair report",
            "quality_repair_report",
        )
        _echo_named_artifact(
            artifacts,
            "final release bundle report",
            "final_release_bundle_report",
        )
        _echo_named_artifact(
            artifacts,
            "final release bundle index",
            "final_release_bundle_index",
        )
        _echo_named_artifact(
            artifacts,
            "evidence-aware refresh report",
            "evidence_aware_refresh_report",
        )
        _echo_named_artifact(artifacts, "human review", "human_review_artifact")
        _echo_named_artifact(artifacts, "human review summary", "human_review_summary")
        _echo_named_artifact(
            artifacts,
            "reviewer summary",
            "reviewer_bundle_summary_json",
        )
        _echo_named_artifact(
            artifacts,
            "reviewer summary markdown",
            "reviewer_bundle_summary_markdown",
        )
        _echo_named_artifact(
            artifacts,
            "reviewer summary after human review",
            "reviewer_bundle_summary_after_human_review_json",
        )
        _echo_named_artifact(
            artifacts,
            "reviewer summary after evidence artifacts",
            "reviewer_bundle_summary_after_evidence_artifacts_json",
        )
        _echo_named_artifact(
            artifacts,
            "reviewer summary after claim evidence map",
            "reviewer_bundle_summary_after_claim_evidence_map_json",
        )
        _echo_named_artifact(artifacts, "claim-evidence map", "claim_evidence_map")
        _echo_named_artifact(
            artifacts,
            "claim-evidence map markdown",
            "claim_evidence_map_markdown",
        )
        _echo_named_artifact(artifacts, "retrieval report", "retrieval_report")
        _echo_named_artifact(
            artifacts,
            "retrieval quality report",
            "retrieval_quality_report",
        )
        _echo_named_artifact(artifacts, "citation registry", "citation_registry")
        _echo_named_artifact(artifacts, "claim support audit", "claim_support_audit")


def _echo_named_artifact(
    artifacts: dict[object, object],
    label: str,
    key: str,
) -> None:
    path = artifacts.get(key)
    if path is not None:
        typer.echo(f"- {label}: {path}")


@app.command("inspect-reviewer-summary")
def inspect_reviewer_summary_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect a persisted reviewer-facing paper-bundle summary without mutation."""
    try:
        summary = inspect_reviewer_bundle_summary(run_id=run_id, root=root)
    except PaperBundleInspectionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    _print_reviewer_bundle_summary(summary)


def _print_reviewer_bundle_summary(summary: dict[str, object]) -> None:
    evidence_gaps = list(summary.get("evidence_gaps") or [])
    checklist = list(summary.get("human_review_checklist") or [])
    actions = list(summary.get("recommended_next_actions") or [])
    typer.echo(f"Reviewer summary: {summary['run_id']}")
    typer.echo(f"Release: {summary.get('release_status') or 'unknown'}")
    typer.echo(f"Publication ready: {str(summary.get('publication_ready', False)).lower()}")
    typer.echo(f"Safety: {summary.get('safety_status') or 'unknown'}")
    typer.echo(f"Quality: {summary.get('quality_status') or 'unknown'}")
    typer.echo(
        "Claim/citation: "
        f"{summary.get('claim_support_status') or 'unknown'} / "
        f"{summary.get('citation_status') or 'unknown'}"
    )
    typer.echo(
        "Retrieval/source: "
        f"{summary.get('retrieval_quality_status') or 'unknown'} / "
        f"{summary.get('source_relevance_status') or 'unknown'}"
    )
    typer.echo(
        f"Human review: {'present' if summary.get('human_review_artifact_present') else 'absent'}"
    )
    typer.echo(f"Human review status: {summary.get('human_review_status') or 'not_available'}")
    typer.echo(
        "Blocking human-review concerns: "
        f"{int(summary.get('human_review_blocking_concern_count') or 0)}"
    )
    typer.echo(f"Requested changes: {int(summary.get('human_review_requested_change_count') or 0)}")
    typer.echo(
        f"Recommended next action: {summary.get('human_review_recommended_next_action') or 'none'}"
    )
    typer.echo(f"Proof artifacts: {int(summary.get('proof_artifact_count') or 0)}")
    typer.echo(
        "Formal verification artifacts passed: "
        f"{int(summary.get('formal_verification_artifact_count') or 0)}"
    )
    typer.echo(f"Experiment artifacts: {int(summary.get('experiment_artifact_count') or 0)}")
    typer.echo(f"Completed experiments: {int(summary.get('completed_experiment_count') or 0)}")
    typer.echo(
        "Claim-evidence map: "
        f"{'present' if summary.get('claim_evidence_map_present') else 'absent'}"
    )
    typer.echo(f"Supported claims: {int(summary.get('claim_evidence_supported_count') or 0)}")
    typer.echo(
        f"Partially supported claims: {int(summary.get('claim_evidence_partial_count') or 0)}"
    )
    typer.echo(f"Unsupported claims: {int(summary.get('claim_evidence_unsupported_count') or 0)}")
    typer.echo(f"Proof-supported claims: {int(summary.get('proof_supported_claim_count') or 0)}")
    typer.echo(
        f"Experiment-supported claims: {int(summary.get('experiment_supported_claim_count') or 0)}"
    )
    typer.echo(
        f"Citation-supported claims: {int(summary.get('citation_supported_claim_count') or 0)}"
    )
    typer.echo(f"Evidence gaps: {len(evidence_gaps)}")
    for gap in evidence_gaps:
        typer.echo(f"- {gap}")
    typer.echo(f"Human-review checklist: {len(checklist)}")
    for item in checklist:
        typer.echo(f"- {item}")
    typer.echo(f"Recommended next actions: {len(actions)}")
    for item in actions:
        typer.echo(f"- {item}")


@app.command("lint-paper-bundle")
def lint_paper_bundle_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    min_words: Annotated[int, typer.Option("--min-words")] = 1500,
    min_avg_words_per_section: Annotated[
        float,
        typer.Option("--min-avg-words-per-section"),
    ] = 120.0,
    min_citation_markers: Annotated[int, typer.Option("--min-citation-markers")] = 1,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Lint generated paper bundle quality without mutation or evidence authority."""
    try:
        summary = lint_paper_bundle_summary(
            run_id=run_id,
            root=root,
            min_words=min_words,
            min_avg_words_per_section=min_avg_words_per_section,
            min_citation_markers=min_citation_markers,
        )
    except PaperBundleInspectionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if json_output:
        typer.echo(json.dumps(summary, indent=2, sort_keys=True))
        return
    _print_paper_bundle_lint_summary(summary)


def _print_paper_bundle_lint_summary(summary: dict[str, object]) -> None:
    failures = list(summary.get("quality_failure_reasons") or [])
    development_warnings = list(
        summary.get("development_warnings")
        or summary.get("quality_warning_reasons")
        or summary.get("warnings")
        or []
    )
    semantic_checks = dict(summary.get("semantic_checks") or {})
    title_state = (
        "placeholder"
        if summary.get("title_is_placeholder")
        else "present"
        if summary.get("title_detected")
        else "missing"
    )
    citations = "present" if summary.get("citations_present") else "absent"
    typer.echo(f"Paper quality: {summary['run_id']}")
    typer.echo(f"Status: {summary['quality_status']}")
    typer.echo(f"Words: {int(summary.get('word_count') or 0):,}")
    typer.echo(f"Main-body sections: {int(summary.get('main_body_section_count') or 0)}")
    typer.echo(f"Appendix sections: {int(summary.get('appendix_section_count') or 0)}")
    typer.echo(f"Total headings: {int(summary.get('total_heading_count') or 0)}")
    typer.echo(
        "Average main-body words/section: "
        f"{float(summary.get('main_body_avg_words_per_section') or 0.0):.1f}"
    )
    typer.echo(f"Title: {title_state}")
    typer.echo(f"Citations: {citations}")
    typer.echo(
        f"Quality repair: {'present' if summary.get('quality_repair_report_present') else 'absent'}"
    )
    typer.echo(
        "Evidence-aware refresh: "
        f"{'present' if summary.get('evidence_aware_refresh_report_present') else 'absent'}"
    )
    typer.echo(
        f"Evidence-aware refresh backend: {summary.get('evidence_aware_refresh_backend') or 'off'}"
    )
    typer.echo(
        "Human-review reconciliation: "
        f"{'present' if summary.get('human_review_reconciliation_present') else 'absent'}"
    )
    typer.echo(
        "Reconciliation status: "
        f"{summary.get('human_review_reconciliation_status') or 'not_available'}"
    )
    typer.echo(
        "Autonomous evidence plan: "
        f"{'present' if summary.get('autonomous_evidence_plan_present') else 'absent'}"
    )
    typer.echo(f"Autonomous plan items: {int(summary.get('autonomous_plan_item_count') or 0)}")
    typer.echo(f"Automation-ready items: {int(summary.get('automation_ready_item_count') or 0)}")
    typer.echo(
        "Human intervention required: "
        f"{str(bool(summary.get('autonomous_human_intervention_required'))).lower()}"
    )
    typer.echo(
        "Autonomous execution: "
        f"{'present' if summary.get('autonomous_execution_present') else 'absent'}"
    )
    typer.echo(
        "Latest autonomous execution: "
        f"{summary.get('latest_autonomous_execution_mode') or 'not_available'} / "
        f"{summary.get('latest_autonomous_execution_status') or 'not_available'}"
    )
    typer.echo(
        "Autonomous actions applied/deferred/rejected/failed: "
        f"{int(summary.get('autonomous_actions_applied') or 0)}/"
        f"{int(summary.get('autonomous_actions_deferred') or 0)}/"
        f"{int(summary.get('autonomous_actions_rejected') or 0)}/"
        f"{int(summary.get('autonomous_actions_failed') or 0)}"
    )
    typer.echo(
        "Planned spec execution: "
        f"{'present' if summary.get('planned_spec_execution_present') else 'absent'}"
    )
    typer.echo(
        "Latest planned spec execution: "
        f"{summary.get('latest_planned_spec_execution_mode') or 'not_available'} / "
        f"{summary.get('latest_planned_spec_execution_status') or 'not_available'}"
    )
    typer.echo(
        "Planned spec artifacts created experiment/proof/retrieval: "
        f"{int(summary.get('experiment_artifacts_created') or 0)}/"
        f"{int(summary.get('proof_artifacts_created') or 0)}/"
        f"{int(summary.get('retrieval_artifacts_created') or 0)}"
    )
    typer.echo(
        "Python sandbox: "
        f"{'present' if summary.get('python_experiment_sandbox_present') else 'absent'}"
    )
    typer.echo(
        "Latest Python sandbox status/completed/failed/artifacts: "
        f"{summary.get('latest_python_sandbox_status') or 'not_available'} / "
        f"{int(summary.get('python_experiment_sandbox_completed_count') or 0)}/"
        f"{int(summary.get('python_experiment_sandbox_failed_count') or 0)}/"
        f"{int(summary.get('python_experiment_artifacts_created_count') or 0)}"
    )
    typer.echo(
        f"Autonomous loop: {'present' if summary.get('autonomous_loop_present') else 'absent'}"
    )
    typer.echo(
        "Latest autonomous loop: "
        f"{summary.get('latest_autonomous_loop_status') or 'not_available'} / "
        f"{summary.get('latest_autonomous_loop_stop_reason') or 'not_available'}"
    )
    typer.echo(
        "Autonomous loop iterations completed: "
        f"{int(summary.get('latest_autonomous_loop_iterations_completed') or 0)}"
    )
    typer.echo(
        "Autonomous loop final unsupported claims: "
        f"{int(summary.get('autonomous_loop_final_unsupported_claim_count') or 0)}"
    )
    typer.echo(
        "Strategy diversification: "
        f"{'present' if summary.get('strategy_diversification_present') else 'absent'}"
    )
    typer.echo(f"Strategy options: {int(summary.get('strategy_option_count') or 0)}")
    typer.echo(f"Selected strategies: {int(summary.get('selected_strategy_count') or 0)}")
    typer.echo(f"Duplicate strategies: {int(summary.get('duplicate_strategy_count') or 0)}")
    typer.echo(
        "Deferred after all strategies exhausted: "
        f"{int(summary.get('gaps_deferred_after_strategy_exhaustion') or 0)}"
    )
    typer.echo(f"Quality repair backend: {summary.get('quality_repair_backend') or 'off'}")
    typer.echo(
        f"Quality repaired sections: {int(summary.get('quality_repaired_section_count') or 0)}"
    )
    typer.echo(
        "Depth targets met: "
        f"{int(summary.get('section_depth_target_met_count') or 0)}/"
        f"{int(summary.get('section_depth_target_total') or 0)}"
    )
    typer.echo(f"Warnings reduced: {int(summary.get('warnings_reduced_count') or 0)}")
    typer.echo(
        "Quality status before/after: "
        f"{summary.get('quality_status_before_repair') or 'unknown'} / "
        f"{summary.get('quality_status_after_repair') or 'unknown'}"
    )
    typer.echo(
        "Citation registry: "
        f"{'present' if summary.get('citation_registry_present') else 'absent'} "
        f"({int(summary.get('citation_registry_source_count') or 0)} sources)"
    )
    typer.echo(f"Bibliography: {summary.get('bibliography_status') or 'absent'}")
    typer.echo(
        f"Claim support: {'present' if summary.get('claim_support_audit_present') else 'absent'}"
    )
    typer.echo(
        "Retrieval quality: "
        f"{'present' if summary.get('retrieval_quality_report_present') else 'absent'} "
        f"({int(summary.get('accepted_source_count') or 0)} accepted / "
        f"{int(summary.get('rejected_source_count') or 0)} rejected)"
    )
    typer.echo(
        "Source relevance adjudication: "
        f"{summary.get('source_relevance_adjudicator_backend') or 'off'}"
    )
    typer.echo(
        f"Adjudicated sources: {int(summary.get('source_relevance_adjudicated_count') or 0)}"
    )
    typer.echo(
        f"LLM accepted sources: {int(summary.get('source_relevance_llm_accepted_count') or 0)}"
    )
    typer.echo(
        f"LLM rejected sources: {int(summary.get('source_relevance_llm_rejected_count') or 0)}"
    )
    typer.echo(
        f"Hard rejected sources: {int(summary.get('source_relevance_hard_reject_count') or 0)}"
    )
    typer.echo(
        "Missing citation claims: "
        f"{int(summary.get('claim_support_missing_required_citation_count') or 0)}"
    )
    typer.echo(f"Scope mismatches: {int(summary.get('claim_support_scope_mismatch_count') or 0)}")
    typer.echo(
        "Citation validation misuse: "
        f"{int(summary.get('citation_as_validation_misuse_count') or 0)}"
    )
    typer.echo(
        "Claim adjudication: "
        f"{summary.get('claim_adjudicator_backend') or 'off'} "
        f"({int(summary.get('adjudicated_sentence_count') or 0)} sentences)"
    )
    typer.echo(f"Release: {summary.get('paper_release_status') or 'unknown'}")
    typer.echo("Publication ready: false")
    typer.echo("Semantic essentials:")
    for key, label in (
        ("problem_statement_present", "problem statement"),
        ("central_contribution_present", "central contribution"),
        ("method_summary_present", "method summary"),
        ("evidence_boundary_statement_present", "evidence boundaries"),
        ("limitations_present", "limitations"),
        ("provenance_present", "provenance"),
    ):
        state = "present" if semantic_checks.get(key) else "missing"
        typer.echo(f"- {label}: {state}")
    typer.echo("Quality failures:")
    if failures:
        for issue in failures:
            typer.echo(f"- {issue}")
    else:
        typer.echo("- none")
    if development_warnings:
        typer.echo("Development warnings:")
        for warning in development_warnings:
            typer.echo(f"- {warning}")


@app.command("build-draft-skeleton")
def build_draft_skeleton_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    rerun_policy: Annotated[
        str,
        typer.Option("--rerun-policy"),
    ] = "fail-if-exists",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Build deterministic draft skeleton and checklist artifacts."""
    if not _guard_mutating_stage(
        root=root,
        run_id=run_id,
        stage=PipelineStage.BUILD_DRAFT_SKELETON,
        rerun_policy=rerun_policy,
        force=force,
    ):
        return
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = run_draft_skeleton_generation(run_id=run_id, store=store, ledger=ledger)
    except DraftSkeletonError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"sections={len(result.draft_skeleton.section_stubs)}")
    typer.echo(f"claim_placeholders={len(result.draft_skeleton.claim_placeholders)}")
    typer.echo(f"checklist_items={len(result.checklist.items)}")
    typer.echo(f"checklist_failures={result.checklist.failures_count}")
    typer.echo(f"draft_skeleton={result.draft_markdown_artifact.path}")
    typer.echo(f"manuscript_checklist={result.checklist_markdown_artifact.path}")


@app.command("package-research-object")
def package_research_object_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    rerun_policy: Annotated[
        str,
        typer.Option("--rerun-policy"),
    ] = "fail-if-exists",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Package deterministic pipeline outputs into a local research object."""
    if not _guard_mutating_stage(
        root=root,
        run_id=run_id,
        stage=PipelineStage.PACKAGE_RESEARCH_OBJECT,
        rerun_policy=rerun_policy,
        force=force,
    ):
        return
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = build_research_object(run_id=run_id, store=store, ledger=ledger)
    except ResearchObjectError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"run_id={run_id}")
    typer.echo(f"commits={result.ledger_summary.commit_count}")
    typer.echo(f"artifacts={len(result.artifact_manifest.artifacts)}")
    typer.echo(f"evidence_artifacts={result.artifact_manifest.evidence_artifact_count}")
    typer.echo(f"presentation_artifacts={result.artifact_manifest.presentation_artifact_count}")
    typer.echo(f"branch_outcomes={len(result.branch_outcomes)}")
    typer.echo(f"reproducible={str(result.reproducibility_manifest.reproducible).lower()}")
    typer.echo(f"research_object={result.manifest.research_object_markdown.path}")


@app.command("assemble-paper-skeleton")
def assemble_paper_skeleton_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    rerun_policy: Annotated[
        str,
        typer.Option("--rerun-policy"),
    ] = "fail-if-exists",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Assemble deterministic paper-shaped Markdown and JSON skeleton artifacts."""
    if not _guard_mutating_stage(
        root=root,
        run_id=run_id,
        stage=PipelineStage.ASSEMBLE_PAPER_SKELETON,
        rerun_policy=rerun_policy,
        force=force,
    ):
        return
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = run_paper_assembly(run_id=run_id, store=store, ledger=ledger)
    except PaperAssemblyError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(f"sections={result.assembly_report.sections_count}")
    typer.echo(f"claims_included={result.assembly_report.claims_included}")
    typer.echo(f"claims_blocked={result.assembly_report.claims_blocked}")
    typer.echo(f"evidence_links={result.assembly_report.evidence_links_count}")
    typer.echo(
        f"ready_for_polished_prose={str(result.assembly_report.ready_for_polished_prose).lower()}"
    )
    typer.echo(f"paper_skeleton={result.paper_markdown_artifact.path}")


@app.command("final-audit")
def final_audit_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    rerun_policy: Annotated[
        str,
        typer.Option("--rerun-policy"),
    ] = "fail-if-exists",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Run deterministic final audit and release gate."""
    if not _guard_mutating_stage(
        root=root,
        run_id=run_id,
        stage=PipelineStage.FINAL_AUDIT,
        rerun_policy=rerun_policy,
        force=force,
    ):
        return
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = run_final_audit(run_id=run_id, store=store, ledger=ledger)
    except FinalAuditError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    report = result.audit_report
    decision = result.release_gate_decision
    typer.echo(f"audit_checks={len(report.checks)}")
    typer.echo(f"passes={report.passes_count}")
    typer.echo(f"warnings={report.warnings_count}")
    typer.echo(f"failures={report.failures_count}")
    typer.echo(f"blocking_failures={report.blocking_failures_count}")
    typer.echo(f"release_status={decision.status.value}")
    typer.echo(f"ready_for_polished_prose={str(decision.ready_for_polished_prose).lower()}")
    typer.echo(f"ready_for_latex_export={str(decision.ready_for_latex_export).lower()}")
    typer.echo(f"ready_for_external_review={str(decision.ready_for_external_review).lower()}")
    typer.echo(f"final_audit_report={result.audit_markdown_artifact.path}")
    typer.echo(f"release_gate_decision={result.release_markdown_artifact.path}")


@app.command("prepare-export")
def prepare_export_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    rerun_policy: Annotated[
        str,
        typer.Option("--rerun-policy"),
    ] = "fail-if-exists",
    force: Annotated[bool, typer.Option("--force")] = False,
) -> None:
    """Prepare deterministic export contracts and maps."""
    if not _guard_mutating_stage(
        root=root,
        run_id=run_id,
        stage=PipelineStage.PREPARE_EXPORT,
        rerun_policy=rerun_policy,
        force=force,
    ):
        return
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = prepare_export(run_id=run_id, store=store, ledger=ledger)
    except ExportPreparationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    allowed_claims = [claim for claim in result.claim_map if claim.export_allowed]
    blocked_claims = [claim for claim in result.claim_map if not claim.export_allowed]
    typer.echo(f"sections={len(result.section_map)}")
    typer.echo(f"claims={len(result.claim_map)}")
    typer.echo(f"export_allowed_claims={len(allowed_claims)}")
    typer.echo(f"export_blocked_claims={len(blocked_claims)}")
    typer.echo(
        f"ready_for_polished_prose={str(result.readiness_report.ready_for_polished_prose).lower()}"
    )
    typer.echo(
        f"ready_for_latex_export={str(result.readiness_report.ready_for_latex_export).lower()}"
    )
    typer.echo(
        "ready_for_external_review="
        f"{str(result.readiness_report.ready_for_external_review).lower()}"
    )
    typer.echo(f"export_readiness_report={result.readiness_markdown_artifact.path}")


@app.command("replay-verify")
def replay_verify_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    write_report: Annotated[bool, typer.Option("--write-report")] = False,
) -> None:
    """Replay-verify a completed deterministic run without mutating provenance."""
    try:
        report = replay_verify_run(run_id=run_id, root=root)
    except ReplayVerificationError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if write_report:
        write_replay_report(run_id=run_id, report=report, root=root)
    summary = summarize_replay_verification(report)
    typer.echo(f"run_id={summary.run_id}")
    typer.echo(f"ledger_commits_checked={summary.ledger_commits_checked}")
    typer.echo(f"artifacts_checked={summary.artifacts_checked}")
    typer.echo(f"hashes_verified={summary.hashes_verified}")
    typer.echo(f"evidence_artifacts_checked={summary.evidence_artifacts_checked}")
    typer.echo(f"presentation_artifacts_checked={summary.presentation_artifacts_checked}")
    typer.echo(f"warnings={summary.warnings}")
    typer.echo(f"blocking_failures={summary.blocking_failures}")
    typer.echo(f"ledger_mutated={str(summary.ledger_mutated).lower()}")
    typer.echo(f"artifact_manifest_mutated={str(summary.artifact_manifest_mutated).lower()}")
    typer.echo(f"replay_status={summary.replay_status.value}")


@app.command("diagnose-run")
def diagnose_run_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    write_report: Annotated[bool, typer.Option("--write-report")] = False,
) -> None:
    """Explain deterministic audit, replay, and export failures without repairing them."""
    try:
        report = build_diagnostic_report(run_id=run_id, root=root)
    except DiagnosticError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if write_report:
        write_diagnostic_report(run_id=run_id, report=report, root=root)
    typer.echo(f"run_id={report.run_id}")
    typer.echo(f"diagnostic_status={report.diagnostic_status.value}")
    typer.echo(f"root_causes={len(report.root_causes)}")
    typer.echo(f"recommended_steps={len(report.recommended_steps)}")
    typer.echo(f"blocking_causes={report.blocking_causes_count}")
    typer.echo(f"warnings={report.warning_causes_count + len(report.warnings)}")
    typer.echo(f"ledger_mutated={str(report.ledger_mutated).lower()}")
    typer.echo(f"artifact_manifest_mutated={str(report.artifact_manifest_mutated).lower()}")


@app.command("inspect-hygiene")
def inspect_hygiene_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    write_report: Annotated[bool, typer.Option("--write-report")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Inspect run output hygiene without mutating, repairing, or deleting files."""
    report = inspect_output_hygiene(run_id=run_id, root=root)
    if write_report:
        write_output_hygiene_report(run_id=run_id, report=report, root=root)
    if json_output:
        typer.echo(json.dumps(report.model_dump(mode="json"), sort_keys=True))
        return
    summary = summarize_output_hygiene(report)
    for key in (
        "run_id",
        "hygiene_status",
        "files_scanned",
        "manifest_entries",
        "orphaned_files",
        "missing_manifest_files",
        "hash_mismatches",
        "duplicate_outputs",
        "non_provenance_files",
        "unexpected_files",
        "warnings",
        "blocking_findings",
    ):
        typer.echo(f"{key}={summary[key]}")
    typer.echo(f"ledger_mutated={str(report.ledger_mutated).lower()}")
    typer.echo(f"artifact_manifest_mutated={str(report.artifact_manifest_mutated).lower()}")
    for finding in report.findings:
        paths = ",".join(finding.paths) or "none"
        typer.echo(
            f"finding={finding.severity.value}:{finding.category.value}:{paths}:{finding.message}"
        )


@app.command("plan-hygiene-remediation")
def plan_hygiene_remediation_command(
    run_id: Annotated[str, typer.Option("--run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    write_report: Annotated[bool, typer.Option("--write-report")] = False,
    json_output: Annotated[bool, typer.Option("--json")] = False,
) -> None:
    """Plan hygiene remediation without executing cleanup, repair, or reruns."""
    hygiene_report = inspect_output_hygiene(run_id=run_id, root=root)
    plan = build_hygiene_remediation_plan(hygiene_report)
    if write_report:
        write_hygiene_remediation_plan(plan=plan, root=root)
    if json_output:
        typer.echo(json.dumps(plan.model_dump(mode="json"), sort_keys=True))
        return
    summary = summarize_hygiene_remediation_plan(plan)
    for key in (
        "run_id",
        "plan_status",
        "actions_total",
        "low_risk_actions",
        "medium_risk_actions",
        "high_risk_actions",
        "unsafe_actions",
        "manual_inspection_actions",
        "rerun_stage_actions",
    ):
        typer.echo(f"{key}={summary[key]}")
    typer.echo(f"ledger_mutated={str(plan.ledger_mutated).lower()}")
    typer.echo(f"artifact_manifest_mutated={str(plan.artifact_manifest_mutated).lower()}")
    for action in plan.actions:
        paths = ",".join(action.paths) or "none"
        stage = action.recommended_stage or "none"
        typer.echo(
            f"action={action.kind.value}:{action.risk.value}:{stage}:{paths}:{action.reason}"
        )


@app.command("compare-runs")
def compare_runs_command(
    baseline_run_id: Annotated[str, typer.Option("--baseline-run-id")],
    candidate_run_id: Annotated[str, typer.Option("--candidate-run-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
    write_report: Annotated[bool, typer.Option("--write-report")] = False,
) -> None:
    """Compare two completed deterministic runs without mutating either run."""
    try:
        report = compare_runs(
            baseline_run_id=baseline_run_id,
            candidate_run_id=candidate_run_id,
            root=root,
        )
    except CrossRunError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if write_report:
        write_cross_run_report(report=report, root=root)
    summary = summarize_cross_run_comparison(report)
    typer.echo(f"baseline_run_id={summary.baseline_run_id}")
    typer.echo(f"candidate_run_id={summary.candidate_run_id}")
    typer.echo(f"differences={summary.differences_count}")
    typer.echo(f"blocking_regressions={summary.blocking_regressions}")
    typer.echo(f"warning_regressions={summary.warning_regressions}")
    typer.echo(f"info_differences={summary.info_differences}")
    typer.echo(f"regression_status={summary.regression_status.value}")
    typer.echo(
        "baseline_release_status="
        + (
            summary.baseline_release_status.value
            if summary.baseline_release_status is not None
            else "missing"
        )
    )
    typer.echo(
        "candidate_release_status="
        + (
            summary.candidate_release_status.value
            if summary.candidate_release_status is not None
            else "missing"
        )
    )
    typer.echo(
        "baseline_replay_status="
        + (
            summary.baseline_replay_status.value
            if summary.baseline_replay_status is not None
            else "missing"
        )
    )
    typer.echo(
        "candidate_replay_status="
        + (
            summary.candidate_replay_status.value
            if summary.candidate_replay_status is not None
            else "missing"
        )
    )
    typer.echo(f"ledger_mutated={str(summary.ledger_mutated).lower()}")
    typer.echo(f"artifact_manifest_mutated={str(summary.artifact_manifest_mutated).lower()}")


@app.command("questioner-check")
def questioner_check(
    run_id: Annotated[str, typer.Option("--run-id")],
    candidate_id: Annotated[str, typer.Option("--candidate-id")],
    root: Annotated[Path, typer.Option("--root")] = DEFAULT_ROOT,
) -> None:
    """Run a deterministic Strategic Questioner check and ledger it."""
    result = run_questioner_check(
        run_id=run_id,
        candidate_id=candidate_id,
        root=root,
    )
    typer.echo(f"questions={len(result.questions)}")
    typer.echo(f"routed_action={result.routed_action.value}")
    typer.echo(f"commit_hash={result.commit.commit_hash}")


@app.command("retrieval-adequacy-demo")
def retrieval_adequacy_demo(
    query: Annotated[
        str,
        typer.Option("--query"),
    ] = "distribution shift uncertainty quantification",
    retrieval_backend: Annotated[
        str,
        typer.Option("--retrieval-backend"),
    ] = DEFAULT_RETRIEVAL_BACKEND,
    allow_external_calls: Annotated[
        bool,
        typer.Option("--allow-external-calls"),
    ] = DEFAULT_ALLOW_EXTERNAL_CALLS,
    retrieval_limit: Annotated[
        int,
        typer.Option("--retrieval-limit"),
    ] = DEFAULT_RETRIEVAL_LIMIT,
) -> None:
    """Print fake-default or explicitly gated bounded retrieval adequacy."""
    try:
        result = run_retrieval_adequacy_demo(
            query=query,
            retrieval_backend=retrieval_backend,
            allow_external_calls=allow_external_calls,
            retrieval_limit=retrieval_limit,
        )
    except (AdapterConfigurationError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result.certificate.model_dump(mode="json"), sort_keys=True))


@app.command("stagnation-demo")
def stagnation_demo() -> None:
    """Print a deterministic stagnation decision."""
    state = compute_stagnation(
        [
            StagnationEvent(action="Refine", score=0.50),
            StagnationEvent(action="Repair", score=0.505),
            StagnationEvent(action="Repair", score=0.507),
            StagnationEvent(action="Repair", score=0.508),
        ],
        epsilon_score=0.01,
        window=4,
    )
    typer.echo(json.dumps(state.model_dump(mode="json"), sort_keys=True))
    typer.echo(f"forced_action={forced_stagnation_action(state).value}")


def main() -> None:
    """Console-script entrypoint."""
    app()
