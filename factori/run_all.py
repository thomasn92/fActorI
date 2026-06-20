"""Canonical direct one-command runner for the deterministic fActorI pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.abstract_synthesis import run_abstract_synthesis
from factori.artifacts import ArtifactStore
from factori.diagnostics import (
    build_diagnostic_report,
)
from factori.diagnostics import (
    write_diagnostic_report as write_diagnostic_report_file,
)
from factori.draft_skeleton import run_draft_skeleton_generation
from factori.export_plan import prepare_export
from factori.final_audit import run_final_audit
from factori.final_paper import run_paper_assembly
from factori.ledger import ResearchLedger, utc_timestamp
from factori.manuscript_plan import run_manuscript_planning
from factori.pipeline import (
    PipelineConfigurationError,
    pipeline_status_for_results,
    selected_pipeline_stages,
    stage_is_read_only,
)
from factori.replay import replay_verify_run, write_replay_report
from factori.reports import render_pipeline_run_report_markdown
from factori.research_object import build_research_object
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    DiagnosticStatus,
    PipelineFailurePolicy,
    PipelineRunConfig,
    PipelineRunReport,
    PipelineRunStatus,
    PipelineStage,
    PipelineStageResult,
    ReleaseGateDecision,
    ReleaseGateStatus,
    ReplayStatus,
    ResumeValidationStatus,
)
from factori.stage_a import constraint_from_inputs, run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_selection import run_stage_c_selection
from factori.status import validate_resume_request


class PipelineRunError(RuntimeError):
    """Raised when a run cannot be started with the requested resume policy."""


@dataclass(frozen=True)
class _StageExecution:
    status: PipelineRunStatus
    artifacts: list[str]
    summary: dict[str, Any]
    warnings: list[str]
    final_outputs: dict[str, str]
    release_status: ReleaseGateStatus | None = None
    replay_status: ReplayStatus | None = None
    diagnostic_status: DiagnosticStatus | None = None


def run_deterministic_pipeline(config: PipelineRunConfig) -> PipelineRunReport:
    """Execute selected deterministic stages directly and write a ledgered pipeline report."""
    try:
        stages = selected_pipeline_stages(config)
    except PipelineConfigurationError as exc:
        raise PipelineRunError(str(exc)) from exc

    store = ArtifactStore(config.root)
    ledger_path = store.run_path(config.run_id) / "ledger.sqlite"
    if config.start_at is None and ledger_path.is_file():
        existing = ResearchLedger(ledger_path).list_commits(config.run_id)
        if existing:
            raise PipelineRunError(
                f"Run already exists: {config.run_id}; use --start-at to resume"
            )
    if config.start_at is not None:
        validation = validate_resume_request(
            run_id=config.run_id,
            start_at_stage=config.start_at,
            root=config.root,
        )
        if validation.resume_status == ResumeValidationStatus.RESUME_BLOCKED:
            details = "; ".join(validation.blocking_issues)
            raise PipelineRunError(f"Cannot start at {config.start_at.value}: {details}")

    store.init_run(config.run_id)
    ledger = ResearchLedger(ledger_path)
    pipeline_started = utc_timestamp()
    stage_results: list[PipelineStageResult] = []
    warnings: list[str] = []
    final_outputs: dict[str, str] = {}
    blocking_stage: PipelineStage | None = None
    release_status: ReleaseGateStatus | None = _load_release_status(config.root, config.run_id)
    replay_status: ReplayStatus | None = None
    diagnostic_status: DiagnosticStatus | None = None
    mutating_failure = False

    for stage in stages:
        if mutating_failure and not stage_is_read_only(stage):
            warnings.append(f"Skipped {stage.value} after an earlier mutating-stage failure")
            continue
        stage_started = utc_timestamp()
        commits_before = len(ledger.list_commits(config.run_id))
        try:
            execution = _execute_stage(stage, config, store, ledger)
            commits_after = len(ledger.list_commits(config.run_id))
            if stage_is_read_only(stage) and commits_after != commits_before:
                raise PipelineRunError(
                    f"Read-only stage mutated ledger: {stage.value}"
                )
            stage_result = PipelineStageResult(
                stage_name=stage,
                started_at=stage_started,
                finished_at=utc_timestamp(),
                status=execution.status,
                created_artifacts=execution.artifacts,
                summary={
                    **execution.summary,
                    "ledger_commits_before": commits_before,
                    "ledger_commits_after": commits_after,
                },
            )
            stage_results.append(stage_result)
            warnings.extend(execution.warnings)
            final_outputs.update(execution.final_outputs)
            release_status = execution.release_status or release_status
            replay_status = execution.replay_status or replay_status
            diagnostic_status = execution.diagnostic_status or diagnostic_status
            if execution.status == PipelineRunStatus.PIPELINE_BLOCKED:
                blocking_stage = blocking_stage or stage
                if config.failure_policy == PipelineFailurePolicy.FAIL_FAST:
                    break
        except (RuntimeError, ValueError) as exc:
            stage_results.append(
                PipelineStageResult(
                    stage_name=stage,
                    started_at=stage_started,
                    finished_at=utc_timestamp(),
                    status=PipelineRunStatus.PIPELINE_FAILED,
                    summary={
                        "ledger_commits_before": commits_before,
                        "ledger_commits_after": len(ledger.list_commits(config.run_id)),
                    },
                    error_message=str(exc),
                )
            )
            blocking_stage = blocking_stage or stage
            warnings.append(f"{stage.value} failed: {exc}")
            mutating_failure = not stage_is_read_only(stage)
            if config.failure_policy == PipelineFailurePolicy.FAIL_FAST:
                break

    _add_existing_final_outputs(config.root, config.run_id, final_outputs)
    pipeline_status = pipeline_status_for_results(stage_results, warnings)
    pipeline_report_path = (
        Path("runs") / config.run_id / "reports" / "pipeline-run-report.md"
    ).as_posix()
    report = PipelineRunReport(
        run_id=config.run_id,
        domain=config.domain,
        method=config.method,
        stage_results=stage_results,
        started_at=pipeline_started,
        finished_at=utc_timestamp(),
        pipeline_status=pipeline_status,
        failure_policy=config.failure_policy,
        blocking_stage=blocking_stage,
        warnings=sorted(set(warnings)),
        final_outputs=final_outputs,
        release_status=release_status,
        replay_status=replay_status,
        diagnostic_status=diagnostic_status,
        pipeline_report_path=pipeline_report_path,
    )
    _write_pipeline_report(report, store, ledger)
    return report


def _execute_stage(
    stage: PipelineStage,
    config: PipelineRunConfig,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> _StageExecution:
    run_id = config.run_id
    if stage == PipelineStage.RUN_STAGE_A:
        result = run_stage_a(
            run_id=run_id,
            constraints=constraint_from_inputs(config.domain, config.method),
            store=store,
            ledger=ledger,
        )
        artifacts = [*result.candidate_artifacts.values(), *result.score_artifacts.values()]
        if result.stage0.report_artifact is not None:
            artifacts.append(result.stage0.report_artifact)
        artifacts.append(result.report_artifact)
        return _success(
            artifacts,
            generated_candidates=len(result.generated_candidates),
            stage_a_survivors=len(result.survivors),
        )
    if stage == PipelineStage.RUN_STAGE_B:
        result = run_stage_b(run_id=run_id, store=store, ledger=ledger)
        artifacts = [item for values in result.artifacts.values() for item in values]
        artifacts.append(result.report_artifact)
        return _success(
            artifacts,
            stage_b_children=len(result.children),
            stage_b_survivors=len(result.survivors),
        )
    if stage == PipelineStage.SELECT_STAGE_C:
        result = run_stage_c_selection(run_id=run_id, store=store, ledger=ledger)
        artifacts = [item for values in result.artifacts.values() for item in values]
        artifacts.extend([result.budget_artifact, result.report_artifact])
        return _success(
            artifacts,
            stage_c_ready=len(result.selected_candidates),
            budget_deferred=len(result.budget_deferred),
        )
    if stage == PipelineStage.RUN_STAGE_C:
        result = run_stage_c(run_id=run_id, store=store, ledger=ledger)
        artifacts = [item for values in result.artifacts.values() for item in values]
        artifacts.append(result.report_artifact)
        return _success(
            artifacts,
            verified_candidates=len(result.verified_candidates),
            fake_proof_runs=len(result.proof_results),
            fake_synthetic_experiments=len(result.experiment_results),
            fake=True,
        )
    if stage == PipelineStage.SYNTHESIZE_ABSTRACT:
        result = run_abstract_synthesis(run_id=run_id, store=store, ledger=ledger)
        artifacts = [*result.artifacts, result.final_nucleus_artifact, result.report_artifact]
        return _success(
            artifacts,
            final_nucleus_id=result.final_nucleus.id,
            final_nucleus_type=result.final_nucleus.nucleus_type.value,
        )
    if stage == PipelineStage.PLAN_MANUSCRIPT:
        result = run_manuscript_planning(run_id=run_id, store=store, ledger=ledger)
        artifacts = [
            result.claim_table_artifact,
            result.blocked_claims_artifact,
            result.manuscript_plan_artifact,
            result.markdown_artifact,
        ]
        return _success(
            artifacts,
            claims=len(result.claim_table.claims),
            blocked_claims=len(result.blocked_claims),
        )
    if stage == PipelineStage.BUILD_DRAFT_SKELETON:
        result = run_draft_skeleton_generation(run_id=run_id, store=store, ledger=ledger)
        artifacts = [
            result.draft_json_artifact,
            result.draft_markdown_artifact,
            result.checklist_json_artifact,
            result.checklist_markdown_artifact,
        ]
        return _success(
            artifacts,
            sections=len(result.draft_skeleton.section_stubs),
            checklist_failures=result.checklist.failures_count,
        )
    if stage == PipelineStage.PACKAGE_RESEARCH_OBJECT:
        result = build_research_object(run_id=run_id, store=store, ledger=ledger)
        refs = [
            result.manifest.research_object_json,
            result.manifest.research_object_markdown,
            result.manifest.artifact_manifest,
            result.manifest.ledger_summary,
            result.manifest.branch_outcomes,
            result.manifest.reproducibility_manifest,
        ]
        return _success(
            refs,
            final_outputs={
                "research_object": result.manifest.research_object_markdown.path,
            },
            artifact_count=len(result.artifact_manifest.artifacts),
            reproducible=result.reproducibility_manifest.reproducible,
        )
    if stage == PipelineStage.ASSEMBLE_PAPER_SKELETON:
        result = run_paper_assembly(run_id=run_id, store=store, ledger=ledger)
        artifacts = [
            result.paper_json_artifact,
            result.paper_markdown_artifact,
            result.assembly_report_artifact,
        ]
        return _success(
            artifacts,
            final_outputs={"paper_skeleton": result.paper_markdown_artifact.path},
            sections=result.assembly_report.sections_count,
            ready_for_polished_prose=result.assembly_report.ready_for_polished_prose,
        )
    if stage == PipelineStage.FINAL_AUDIT:
        result = run_final_audit(run_id=run_id, store=store, ledger=ledger)
        decision = result.release_gate_decision
        status = (
            PipelineRunStatus.PIPELINE_BLOCKED
            if decision.status == ReleaseGateStatus.RELEASE_BLOCKED
            else PipelineRunStatus.PIPELINE_SUCCEEDED_WITH_WARNINGS
            if decision.status == ReleaseGateStatus.RELEASE_READY_WITH_WARNINGS
            else PipelineRunStatus.PIPELINE_SUCCEEDED
        )
        warnings = [*decision.warnings, *decision.blocking_reasons]
        return _StageExecution(
            status=status,
            artifacts=_paths(
                [
                    result.audit_json_artifact,
                    result.audit_markdown_artifact,
                    result.release_json_artifact,
                    result.release_markdown_artifact,
                ]
            ),
            summary={
                "audit_checks": len(result.audit_report.checks),
                "blocking_failures": result.audit_report.blocking_failures_count,
                "release_status": decision.status.value,
            },
            warnings=warnings,
            final_outputs={"final_audit_report": result.audit_markdown_artifact.path},
            release_status=decision.status,
        )
    if stage == PipelineStage.PREPARE_EXPORT:
        result = prepare_export(run_id=run_id, store=store, ledger=ledger)
        artifacts = [
            result.prose_contract_artifact,
            result.latex_plan_artifact,
            result.section_map_artifact,
            result.claim_map_artifact,
            result.readiness_json_artifact,
            result.readiness_markdown_artifact,
            result.bundle_manifest_artifact,
        ]
        status = (
            PipelineRunStatus.PIPELINE_BLOCKED
            if result.readiness_report.export_blocked
            else PipelineRunStatus.PIPELINE_SUCCEEDED_WITH_WARNINGS
            if result.readiness_report.warnings
            else PipelineRunStatus.PIPELINE_SUCCEEDED
        )
        return _StageExecution(
            status=status,
            artifacts=_paths(artifacts),
            summary={
                "ready_for_polished_prose": (
                    result.readiness_report.ready_for_polished_prose
                ),
                "ready_for_latex_export": result.readiness_report.ready_for_latex_export,
                "export_blocked": result.readiness_report.export_blocked,
            },
            warnings=[
                *result.readiness_report.warnings,
                *result.readiness_report.blocking_reasons,
            ],
            final_outputs={
                "export_readiness_report": result.readiness_markdown_artifact.path,
            },
        )
    if stage == PipelineStage.REPLAY_VERIFY:
        report = replay_verify_run(run_id=run_id, root=config.root)
        created: list[str] = []
        outputs: dict[str, str] = {}
        if config.write_replay_report:
            json_path, markdown_path = write_replay_report(
                run_id=run_id,
                report=report,
                root=config.root,
            )
            created = [_relative(config.root, json_path), _relative(config.root, markdown_path)]
            outputs["replay_report"] = _relative(config.root, markdown_path)
        status = (
            PipelineRunStatus.PIPELINE_BLOCKED
            if report.replay_status == ReplayStatus.REPLAY_FAILED
            else PipelineRunStatus.PIPELINE_SUCCEEDED_WITH_WARNINGS
            if report.replay_status == ReplayStatus.REPLAY_VERIFIED_WITH_WARNINGS
            else PipelineRunStatus.PIPELINE_SUCCEEDED
        )
        return _StageExecution(
            status=status,
            artifacts=created,
            summary={
                "replay_status": report.replay_status.value,
                "blocking_failures": report.blocking_failures_count,
                "read_only": True,
            },
            warnings=[finding.message for finding in report.findings],
            final_outputs=outputs,
            replay_status=report.replay_status,
        )
    if stage == PipelineStage.DIAGNOSE_RUN:
        report = build_diagnostic_report(run_id=run_id, root=config.root)
        created = []
        outputs = {}
        if config.write_diagnostic_report:
            json_path, markdown_path = write_diagnostic_report_file(
                run_id=run_id,
                report=report,
                root=config.root,
            )
            created = [_relative(config.root, json_path), _relative(config.root, markdown_path)]
            outputs["diagnostic_report"] = _relative(config.root, markdown_path)
        status = (
            PipelineRunStatus.PIPELINE_BLOCKED
            if report.diagnostic_status == DiagnosticStatus.BLOCKED
            else PipelineRunStatus.PIPELINE_SUCCEEDED_WITH_WARNINGS
            if report.diagnostic_status
            in {DiagnosticStatus.WARNINGS_ONLY, DiagnosticStatus.ACTION_RECOMMENDED}
            else PipelineRunStatus.PIPELINE_SUCCEEDED
        )
        return _StageExecution(
            status=status,
            artifacts=created,
            summary={
                "diagnostic_status": report.diagnostic_status.value,
                "root_causes": len(report.root_causes),
                "read_only": True,
            },
            warnings=[cause.summary for cause in report.root_causes],
            final_outputs=outputs,
            diagnostic_status=report.diagnostic_status,
        )
    raise PipelineRunError(f"Unsupported pipeline stage: {stage.value}")


def _success(
    artifacts: list[ArtifactRef],
    *,
    final_outputs: dict[str, str] | None = None,
    **summary: Any,
) -> _StageExecution:
    return _StageExecution(
        status=PipelineRunStatus.PIPELINE_SUCCEEDED,
        artifacts=_paths(artifacts),
        summary=summary,
        warnings=[],
        final_outputs=final_outputs or {},
    )


def _paths(artifacts: list[ArtifactRef]) -> list[str]:
    return sorted({artifact.path for artifact in artifacts})


def _write_pipeline_report(
    report: PipelineRunReport,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> tuple[ArtifactRef, ArtifactRef]:
    json_artifact = store.write_json(
        run_id=report.run_id,
        artifact_id="pipeline-run-report",
        artifact_type=ArtifactType.REPORT,
        data=report,
        metadata={"stage": "pipeline", "fake": True, "is_provenance_summary": False},
    )
    markdown_artifact = store.write_markdown(
        run_id=report.run_id,
        artifact_id="pipeline-run-report",
        artifact_type=ArtifactType.REPORT,
        markdown=render_pipeline_run_report_markdown(pipeline_report=report),
        metadata={"stage": "pipeline", "fake": True, "is_provenance_summary": False},
    )
    commit = ledger.append_commit(
        run_id=report.run_id,
        parent_hash=ledger.latest_commit_hash(report.run_id),
        action_type=ControllerActionType.PIPELINE_RUN_REPORT_WRITTEN,
        payload=report.model_dump(mode="json"),
        artifact_refs=[json_artifact, markdown_artifact],
    )
    return (
        store.link_artifact_to_commit(json_artifact, commit.commit_hash),
        store.link_artifact_to_commit(markdown_artifact, commit.commit_hash),
    )


def _load_release_status(root: Path, run_id: str) -> ReleaseGateStatus | None:
    path = root / "runs" / run_id / "reports" / "release-gate-decision.json"
    if not path.is_file():
        return None
    return ReleaseGateDecision.model_validate_json(
        path.read_text(encoding="utf-8")
    ).status


def _add_existing_final_outputs(
    root: Path,
    run_id: str,
    outputs: dict[str, str],
) -> None:
    expected = {
        "research_object": root / "runs" / run_id / "research_object" / "research-object.md",
        "paper_skeleton": root / "runs" / run_id / "research_object" / "paper-skeleton.md",
        "export_readiness_report": (
            root / "runs" / run_id / "reports" / "export-readiness-report.md"
        ),
    }
    for key, path in expected.items():
        if path.is_file():
            outputs.setdefault(key, _relative(root, path))


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()
