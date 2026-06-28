"""Minimal Typer CLI for the deterministic foundation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from factori.abstract_synthesis import AbstractSynthesisError, run_abstract_synthesis
from factori.adapters.config import AdapterConfig
from factori.adapters.registry import AdapterConfigurationError, get_adapter_registry
from factori.artifacts import ArtifactStore
from factori.citations import (
    build_citation_registry_from_ledger,
    validate_citation_usage,
    write_citation_registry_reports,
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
)
from factori.cross_run import CrossRunError, compare_runs, write_cross_run_report
from factori.diagnostics import (
    DiagnosticError,
    build_diagnostic_report,
    write_diagnostic_report,
)
from factori.draft_skeleton import DraftSkeletonError, run_draft_skeleton_generation
from factori.dry_run import build_pipeline_dry_run_plan
from factori.export_plan import ExportPreparationError, prepare_export
from factori.final_audit import FinalAuditError, run_final_audit
from factori.final_paper import PaperAssemblyError, run_paper_assembly
from factori.full_paper_generation import (
    FullPaperGenerationError,
    full_paper_generation_result_model,
    generate_full_paper,
)
from factori.full_paper_release import (
    FullPaperReleaseError,
    run_full_paper_release_gate,
)
from factori.hygiene_plan import (
    build_hygiene_remediation_plan,
    summarize_hygiene_remediation_plan,
    write_hygiene_remediation_plan,
)
from factori.latex_export import LatexExportError, export_latex_from_run
from factori.latex_render import LatexRenderError
from factori.ledger import LedgerError, ResearchLedger
from factori.literature_positioning import build_literature_positioning_report
from factori.llm_orchestration import (
    LLMOrchestrationError,
    build_llm_orchestration_preflight_summary,
    llm_orchestration_result_model,
    run_llm_paper_orchestration,
)
from factori.manuscript_drafting import (
    ManuscriptDraftingError,
    draft_manuscript,
    load_manuscript_drafting_inputs,
)
from factori.manuscript_plan import ManuscriptPlanError, run_manuscript_planning
from factori.narrative_contract import (
    NarrativeContractError,
    build_narrative_contract,
    load_narrative_inputs,
)
from factori.output_hygiene import (
    inspect_output_hygiene,
    summarize_output_hygiene,
    write_output_hygiene_report,
)
from factori.paper_critic import PaperCriticError, critique_paper_from_run
from factori.paper_revision import revise_paper_from_run
from factori.paper_shape import critique_paper_shape, write_paper_shape_reports
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
from factori.regression_diagnostics import summarize_cross_run_comparison
from factori.replay import (
    ReplayVerificationError,
    replay_verify_run,
    summarize_replay_verification,
    write_replay_report,
)
from factori.rerun_policy import decide_stage_rerun, validate_ledger_tip
from factori.research_object import ResearchObjectError, build_research_object
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
from factori.stage_a import constraint_from_inputs, run_stage_a
from factori.stage_b import StageBError, run_stage_b
from factori.stage_c import StageCError, run_stage_c
from factori.stage_c_selection import StageCSelectionError, run_stage_c_selection
from factori.stagnation import compute_stagnation, forced_stagnation_action
from factori.status import inspect_run_status, stage_status_detail, validate_resume_request

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
                registry.proof_verifier
                if registry.config.proof_backend != "fake"
                else None
            ),
            experiment_runner=(
                registry.experiment_runner
                if registry.config.experiment_backend != "fake"
                else None
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
        "real_synthetic_experiments="
        f"{len(result.experiment_results) - fake_experiment_runs}"
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
    typer.echo(
        "is_verification_evidence="
        f"{str(critique.is_verification_evidence).lower()}"
    )
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
            "citation_safety="
            f"{str(result.citation_safety_report.safe).lower()}"
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
                                artifacts.literature_positioning_artifact.model_dump(
                                    mode="json"
                                )
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
        "patches="
        + (str(len(revision_result.patches)) if revision_result is not None else "0")
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
    typer.echo(f"render_check={str(render_check).lower()}")
    typer.echo("publication_ready=false")
    typer.echo("is_verification_evidence=false")
    typer.echo("creates_scientific_validation=false")
    typer.echo(f"citation_registry={bundle.citation_registry_artifact_id or 'missing'}")
    typer.echo(
        "complete_manuscript_draft="
        f"{bundle.complete_manuscript_draft_artifact_id or 'missing'}"
    )
    typer.echo(f"latex_artifact={bundle.latex_artifact_id or 'missing'}")
    typer.echo(f"paper_critic_report={bundle.paper_critic_report_artifact_id or 'missing'}")
    if bundle.revised_manuscript_draft_artifact_id is not None:
        typer.echo(f"revised_manuscript_draft={bundle.revised_manuscript_draft_artifact_id}")
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
        reviewer_max_objections=reviewer_max_objections,
        generate_paper=generate_paper,
        evaluate_release=evaluate_release,
        include_citations=include_citations,
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
    typer.echo(f"allow_external_calls={str(allow_external_calls).lower()}")
    typer.echo(f"preflight_only={str(preflight_only).lower()}")
    typer.echo(f"estimated_max_calls={preflight_summary['estimated_max_calls']}")
    typer.echo(
        "generate_paper_effective="
        f"{str(preflight_summary['generate_paper_effective']).lower()}"
    )
    typer.echo(
        "evaluate_release_effective="
        f"{str(preflight_summary['evaluate_release_effective']).lower()}"
    )
    typer.echo(
        "export_latex_effective="
        f"{str(preflight_summary['export_latex_effective']).lower()}"
    )
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
