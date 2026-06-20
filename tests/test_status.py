from __future__ import annotations

import json

from typer.testing import CliRunner

from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.ledger import ResearchLedger
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    ControllerActionType,
    PipelineRunConfig,
    PipelineStage,
    ResumeValidationStatus,
    RunCompletenessStatus,
)
from factori.status import inspect_run_status, validate_resume_request


def test_status_reports_no_run_found_for_missing_run(tmp_path) -> None:
    report = inspect_run_status("missing-run", tmp_path)

    assert report.completeness_status == RunCompletenessStatus.NO_RUN_FOUND
    assert report.run_exists is False
    assert report.ledger_commit_count == 0
    assert report.next_recommended_stage.stage_name is None


def test_status_reports_partial_run_after_only_stage_a(tmp_path) -> None:
    run_deterministic_pipeline(_config(tmp_path, stop_after=PipelineStage.RUN_STAGE_A))
    report = inspect_run_status("run-1", tmp_path)

    assert report.completeness_status == RunCompletenessStatus.PARTIAL_RUN
    assert report.completed_stages == [PipelineStage.RUN_STAGE_A]
    assert report.last_completed_stage == PipelineStage.RUN_STAGE_A
    assert report.next_recommended_stage.stage_name == PipelineStage.RUN_STAGE_B


def test_status_reports_complete_run_after_full_run_all(tmp_path) -> None:
    run_deterministic_pipeline(_config(tmp_path))
    report = inspect_run_status("run-1", tmp_path)

    assert report.completeness_status in {
        RunCompletenessStatus.COMPLETE_RUN,
        RunCompletenessStatus.COMPLETE_WITH_WARNINGS,
    }
    assert PipelineStage.REPLAY_VERIFY in report.completed_stages
    assert report.research_object_exists is True
    assert report.paper_skeleton_exists is True
    assert report.final_audit_exists is True
    assert report.export_preparation_exists is True


def test_completed_stages_are_inferred_from_artifacts(tmp_path) -> None:
    report_path = tmp_path / "runs" / "run-1" / "reports" / "stage-a-report.md"
    report_path.parent.mkdir(parents=True)
    report_path.write_text("# Stage A\n", encoding="utf-8")

    report = inspect_run_status("run-1", tmp_path)

    assert report.completed_stages == [PipelineStage.RUN_STAGE_A]
    assert report.ledger_exists is False
    assert report.completeness_status == RunCompletenessStatus.INCONSISTENT_RUN


def test_missing_artifacts_are_detected(tmp_path) -> None:
    run_deterministic_pipeline(_config(tmp_path, stop_after=PipelineStage.RUN_STAGE_A))
    stage_a_report = tmp_path / "runs" / "run-1" / "reports" / "stage-a-report.md"
    stage_a_report.unlink()

    report = inspect_run_status("run-1", tmp_path)

    assert "runs/run-1/reports/stage-a-report.md" in report.required_artifacts_missing
    assert PipelineStage.RUN_STAGE_A in report.missing_stages


def test_ledger_commit_count_is_reported(tmp_path) -> None:
    run_deterministic_pipeline(_config(tmp_path, stop_after=PipelineStage.RUN_STAGE_A))
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    report = inspect_run_status("run-1", tmp_path)

    assert report.ledger_commit_count == len(ledger.list_commits("run-1"))


def test_status_command_is_read_only_for_ledger_and_manifest(tmp_path) -> None:
    run_deterministic_pipeline(_config(tmp_path))
    run_path = tmp_path / "runs" / "run-1"
    ledger = ResearchLedger(run_path / "ledger.sqlite")
    before_commits = len(ledger.list_commits("run-1"))
    manifest_path = run_path / "research_object" / "artifact-manifest.json"
    before_manifest = manifest_path.read_text(encoding="utf-8")

    result = CliRunner().invoke(
        app,
        ["status", "--run-id", "run-1", "--root", str(tmp_path)],
    )

    assert result.exit_code == 0, result.output
    assert "completeness_status=" in result.output
    assert len(ledger.list_commits("run-1")) == before_commits
    assert manifest_path.read_text(encoding="utf-8") == before_manifest


def test_status_json_output_is_valid_json(tmp_path) -> None:
    run_deterministic_pipeline(_config(tmp_path, stop_after=PipelineStage.RUN_STAGE_A))
    result = CliRunner().invoke(
        app,
        ["status", "--run-id", "run-1", "--root", str(tmp_path), "--json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["run_id"] == "run-1"
    assert payload["completeness_status"] == "PartialRun"


def test_status_stage_output_reports_stage_requirements(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "status",
            "--run-id",
            "run-1",
            "--root",
            str(tmp_path),
            "--stage",
            "run-stage-b",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "stage=run-stage-b" in result.output
    assert "completed=false" in result.output
    assert "prerequisites=1" in result.output


def test_validate_resume_allows_run_stage_a_without_prerequisites(tmp_path) -> None:
    report = validate_resume_request("run-1", PipelineStage.RUN_STAGE_A, tmp_path)

    assert report.resume_status == ResumeValidationStatus.RESUME_ALLOWED
    assert report.missing_prerequisites == []


def test_validate_resume_blocks_without_required_prior_stage_artifacts(tmp_path) -> None:
    _init_empty_run(tmp_path, "run-1")
    checks = [
        PipelineStage.RUN_STAGE_B,
        PipelineStage.SELECT_STAGE_C,
        PipelineStage.RUN_STAGE_C,
        PipelineStage.SYNTHESIZE_ABSTRACT,
        PipelineStage.PLAN_MANUSCRIPT,
        PipelineStage.BUILD_DRAFT_SKELETON,
        PipelineStage.PACKAGE_RESEARCH_OBJECT,
        PipelineStage.ASSEMBLE_PAPER_SKELETON,
        PipelineStage.FINAL_AUDIT,
        PipelineStage.PREPARE_EXPORT,
        PipelineStage.REPLAY_VERIFY,
    ]

    for stage in checks:
        report = validate_resume_request("run-1", stage, tmp_path)
        assert report.resume_status == ResumeValidationStatus.RESUME_BLOCKED
        assert report.missing_prerequisites


def test_validate_resume_allows_valid_resume_after_prerequisites_exist(tmp_path) -> None:
    run_deterministic_pipeline(_config(tmp_path, stop_after=PipelineStage.RUN_STAGE_A))

    report = validate_resume_request("run-1", PipelineStage.RUN_STAGE_B, tmp_path)

    assert report.resume_status == ResumeValidationStatus.RESUME_ALLOWED
    assert report.missing_prerequisites == []


def test_validate_resume_blocks_diagnose_without_audit_or_replay(tmp_path) -> None:
    _init_empty_run(tmp_path, "run-1")

    report = validate_resume_request("run-1", PipelineStage.DIAGNOSE_RUN, tmp_path)

    assert report.resume_status == ResumeValidationStatus.RESUME_BLOCKED
    assert "diagnose-run requires at least final audit or replay outputs" in (
        report.blocking_issues
    )


def test_validate_resume_cli_works(tmp_path) -> None:
    run_deterministic_pipeline(_config(tmp_path, stop_after=PipelineStage.RUN_STAGE_A))

    result = CliRunner().invoke(
        app,
        [
            "validate-resume",
            "--run-id",
            "run-1",
            "--root",
            str(tmp_path),
            "--start-at",
            "run-stage-b",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "resume_status=ResumeAllowed" in result.output


def test_run_all_start_at_uses_resume_validation_for_missing_prerequisites(tmp_path) -> None:
    _init_empty_run(tmp_path, "run-1")

    result = CliRunner().invoke(
        app,
        [
            "run-all",
            "--run-id",
            "run-1",
            "--domain",
            "human geography",
            "--root",
            str(tmp_path),
            "--start-at",
            "run-stage-b",
        ],
    )

    assert result.exit_code == 1
    assert "Cannot start at run-stage-b" in result.output
    assert "missing prerequisite" in result.output


def test_run_all_start_at_still_works_when_prerequisites_exist(tmp_path) -> None:
    run_deterministic_pipeline(_config(tmp_path, stop_after=PipelineStage.SYNTHESIZE_ABSTRACT))

    result = CliRunner().invoke(
        app,
        [
            "run-all",
            "--run-id",
            "run-1",
            "--domain",
            "human geography",
            "--root",
            str(tmp_path),
            "--start-at",
            "plan-manuscript",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "pipeline_status=PipelineSucceeded" in result.output


def test_no_real_external_actions_are_introduced() -> None:
    commands = [
        validate_resume_request("run-1", PipelineStage.RUN_STAGE_A).next_recommended_stage.command
    ]

    assert all("openai" not in (command or "").lower() for command in commands)
    assert all("lean" not in (command or "").lower() for command in commands)
    assert all("docker" not in (command or "").lower() for command in commands)


def _config(tmp_path, **updates: object) -> PipelineRunConfig:
    values: dict[str, object] = {
        "run_id": "run-1",
        "domain": "human geography",
        "root": tmp_path,
    }
    values.update(updates)
    return PipelineRunConfig(**values)


def _init_empty_run(tmp_path, run_id: str) -> None:
    store = ArtifactStore(tmp_path)
    store.init_run(run_id)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    ledger.append_commit(
        run_id=run_id,
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": run_id},
        timestamp="1970-01-01T00:00:00.000000Z",
    )
