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
from factori.config import (
    DEFAULT_ADAPTER_BACKEND,
    DEFAULT_ALLOW_EXTERNAL_CALLS,
    DEFAULT_LLM_MODEL,
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
from factori.hygiene_plan import (
    build_hygiene_remediation_plan,
    summarize_hygiene_remediation_plan,
    write_hygiene_remediation_plan,
)
from factori.ledger import LedgerError, ResearchLedger
from factori.manuscript_plan import ManuscriptPlanError, run_manuscript_planning
from factori.output_hygiene import (
    inspect_output_hygiene,
    summarize_output_hygiene,
    write_output_hygiene_report,
)
from factori.protocol_compat import ProtocolCompatibilityStatus, compare_schema_dirs
from factori.protocols import PROTOCOL_VERSION
from factori.questioner import route_questions_to_action, routed_action, select_questions
from factori.regression_diagnostics import summarize_cross_run_comparison
from factori.replay import (
    ReplayVerificationError,
    replay_verify_run,
    summarize_replay_verification,
    write_replay_report,
)
from factori.rerun_policy import decide_stage_rerun, validate_ledger_tip
from factori.research_object import ResearchObjectError, build_research_object
from factori.retrieval import compute_retrieval_adequacy
from factori.run_all import PipelineRunError, run_deterministic_pipeline
from factori.schema_export import (
    DEFAULT_PROTOCOL_OUTPUT_DIR,
    check_protocols,
    export_protocols,
)
from factori.schemas import (
    ArtifactType,
    Candidate,
    ConstraintSet,
    ControllerActionType,
    DataRequirement,
    LiteratureState,
    PipelineDryRunPlan,
    PipelineFailurePolicy,
    PipelineRunConfig,
    PipelineRunStatus,
    PipelineStage,
    PlannedStageStatus,
    RerunPolicy,
    ScoreVector,
    StageRerunStatus,
    StagnationEvent,
    VerificationLabel,
    VerificationState,
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
    for name, class_name in registry.class_names().items():
        typer.echo(f"{name}={class_name}")


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
    store = ArtifactStore(root)
    store.init_run(run_id)
    ledger = _ledger(root, run_id)
    candidate = Candidate(
        id=candidate_id,
        constraints=ConstraintSet(
            domain=domain,
            question=question,
            data_requirement=data_requirement,
        ),
        domain=domain,
        question=question,
        data_requirement=data_requirement,
    )
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=candidate_id,
        artifact_type=ArtifactType.CANDIDATE,
        data=candidate,
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=candidate_id,
        parent_hash=_latest_parent(ledger, run_id),
        action_type=ControllerActionType.ADD_CANDIDATE,
        payload={"candidate": candidate.model_dump(mode="json")},
        artifact_refs=[artifact],
    )
    store.link_artifact_to_commit(artifact, commit.commit_hash)
    typer.echo(f"added {candidate_id} {commit.commit_hash}")


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
    store = ArtifactStore(root)
    store.init_run(run_id)
    ledger = _ledger(root, run_id)

    if format_ == "json":
        payload = {"content": content or "deterministic artifact"}
        artifact = store.write_json(
            run_id=run_id,
            artifact_id=artifact_id,
            artifact_type=kind,
            data=payload,
        )
    elif format_ in {"md", "markdown"}:
        artifact = store.write_markdown(
            run_id=run_id,
            artifact_id=artifact_id,
            artifact_type=kind,
            markdown=content or "# Deterministic Artifact\n",
        )
    else:
        raise typer.BadParameter("format must be json or markdown")

    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=_latest_parent(ledger, run_id),
        action_type=ControllerActionType.WRITE_ARTIFACT,
        payload={"artifact_id": artifact_id, "kind": kind.value, "format": format_},
        artifact_refs=[artifact],
    )
    store.link_artifact_to_commit(artifact, commit.commit_hash)
    typer.echo(f"wrote {artifact.path} {commit.commit_hash}")


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
    store = ArtifactStore(root)
    ledger = _ledger(root, run_id)
    try:
        result = run_stage_c(run_id=run_id, store=store, ledger=ledger)
    except StageCError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc

    labels = [record.label for record in result.verification_records.values()]
    typer.echo(f"stage_c_ready={len(result.stage_c_ready_candidates)}")
    typer.echo(f"fake_proof_runs={len(result.proof_results)}")
    typer.echo(f"fake_synthetic_experiments={len(result.experiment_results)}")
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
    _ensure_run_initialized(root, run_id)
    ledger = _ledger(root, run_id)
    candidate = Candidate(
        id=candidate_id,
        domain="demo-domain",
        method="demo-method",
        question="Should the deterministic control layer continue?",
        data_requirement=DataRequirement.SYNTHETIC_ONLY,
    )
    score = ScoreVector(
        novelty=0.52,
        feasibility=0.62,
        verifiability=0.58,
        reviewer=0.60,
        difficulty=0.52,
        diversity=0.50,
        uncertainty=0.10,
    )
    literature_state = LiteratureState(
        semantic=0.62,
        keyword=0.60,
        citation=0.55,
        diversity=0.58,
        adversarial=0.50,
        novelty_risk=0.30,
    )
    verification_state = VerificationState()
    questions = select_questions(
        "stage_b",
        candidate,
        score,
        literature_state,
        verification_state,
        triggers={"weak_data", "weak_baseline"},
    )
    action = route_questions_to_action(
        questions,
        candidate,
        score,
        literature_state,
        verification_state,
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.QUESTIONER_CHECK,
        payload={
            "controller_action": action.model_dump(mode="json"),
            "routed_action": routed_action(action).value,
        },
    )
    typer.echo(f"questions={len(questions)}")
    typer.echo(f"routed_action={routed_action(action).value}")
    typer.echo(f"commit_hash={commit.commit_hash}")


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
        registry = get_adapter_registry(
            AdapterConfig(
                retrieval_backend=retrieval_backend,
                allow_external_calls=allow_external_calls,
                retrieval_limit=retrieval_limit,
            )
        )
    except (AdapterConfigurationError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc
    if registry.config.retrieval_backend == "fake":
        certificate = compute_retrieval_adequacy(
            LiteratureState(
                semantic=0.70,
                keyword=0.74,
                citation=0.66,
                diversity=0.62,
                adversarial=0.58,
                novelty_risk=0.25,
            )
        )
    else:
        results = registry.retrieval.search(query, retrieval_limit)
        certificate = registry.retrieval.build_adequacy_certificate(query, results)
    typer.echo(json.dumps(certificate.model_dump(mode="json"), sort_keys=True))


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
