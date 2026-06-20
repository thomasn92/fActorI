from __future__ import annotations

import json

from typer.testing import CliRunner

from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.hashing import canonical_json, sha256_file
from factori.hygiene_plan import (
    build_hygiene_remediation_plan,
    summarize_hygiene_remediation_plan,
    write_hygiene_remediation_plan,
)
from factori.ledger import ResearchLedger
from factori.output_hygiene import inspect_output_hygiene
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    ArtifactManifest,
    ControllerActionType,
    HygieneRemediationPlan,
    OutputHygieneCategory,
    OutputHygieneFinding,
    OutputHygieneReport,
    OutputHygieneSeverity,
    OutputHygieneStatus,
    PipelineRunConfig,
    RemediationPlanStatus,
    RunFileIndex,
)


def test_missing_run_produces_inconsistent_non_executing_plan(tmp_path) -> None:
    report = inspect_output_hygiene("missing", root=tmp_path)

    plan = build_hygiene_remediation_plan(report)

    assert plan.plan_status == RemediationPlanStatus.RUN_INCONSISTENT
    assert not plan.execution_performed


def test_remediation_planning_is_deterministic() -> None:
    report = _report(
        [_finding(OutputHygieneCategory.ORPHANED_ARTIFACT, ["reports/orphan.json"])]
    )

    first = build_hygiene_remediation_plan(report)
    second = build_hygiene_remediation_plan(report)

    assert first == second


def test_clean_hygiene_report_requires_no_remediation() -> None:
    plan = build_hygiene_remediation_plan(_report([]))

    assert plan.plan_status == RemediationPlanStatus.NO_REMEDIATION_NEEDED
    assert plan.actions == []


def test_plan_summary_is_deterministic_and_counts_risks() -> None:
    report = _report(
        [
            _finding(OutputHygieneCategory.STALE_OUTPUT, ["reports/stale.tmp"]),
            _finding(OutputHygieneCategory.HASH_MISMATCH, ["reports/custom.json"]),
            _finding(OutputHygieneCategory.EVIDENCE_BOUNDARY_RISK),
        ]
    )
    plan = build_hygiene_remediation_plan(report)

    first = summarize_hygiene_remediation_plan(plan)
    second = summarize_hygiene_remediation_plan(plan)

    assert first == second
    assert first["actions_total"] == 3
    assert first["low_risk_actions"] == 1
    assert first["high_risk_actions"] == 1
    assert first["unsafe_actions"] == 1
    assert first["plan_status"] == RemediationPlanStatus.RUN_INCONSISTENT.value


def test_planner_does_not_execute_delete_or_repair_actions(tmp_path) -> None:
    store, ledger = _minimal_run(tmp_path)
    orphan = store.run_path("run-1") / "reports" / "orphan.json"
    stale = store.run_path("run-1") / "reports" / "stale.tmp"
    orphan.write_text("{}\n", encoding="utf-8")
    stale.write_text("temporary\n", encoding="utf-8")
    before_files = _relative_files(store.run_path("run-1"))
    before_commits = len(ledger.list_commits("run-1"))

    plan = build_hygiene_remediation_plan(
        inspect_output_hygiene("run-1", root=tmp_path)
    )

    assert _relative_files(store.run_path("run-1")) == before_files
    assert orphan.is_file()
    assert stale.is_file()
    assert len(ledger.list_commits("run-1")) == before_commits
    assert not plan.execution_performed
    assert all(not action.execution_performed for action in plan.actions)


def test_planner_does_not_mutate_artifact_manifest(tmp_path) -> None:
    store, ledger = _minimal_run(tmp_path)
    path = store.run_path("run-1") / "research_object" / "artifact-manifest.json"
    manifest = ArtifactManifest(
        run_id="run-1",
        artifacts=[],
        evidence_artifact_count=0,
        presentation_artifact_count=0,
    )
    path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    manifest_hash_before = sha256_file(path)
    commits_before = len(ledger.list_commits("run-1"))

    build_hygiene_remediation_plan(inspect_output_hygiene("run-1", root=tmp_path))

    assert sha256_file(path) == manifest_hash_before
    assert len(ledger.list_commits("run-1")) == commits_before


def test_optional_plan_report_is_marked_outside_provenance(tmp_path) -> None:
    store, ledger = _minimal_run(tmp_path)
    plan = build_hygiene_remediation_plan(
        inspect_output_hygiene("run-1", root=tmp_path)
    )
    commits_before = len(ledger.list_commits("run-1"))

    json_path, markdown_path = write_hygiene_remediation_plan(
        plan=plan,
        root=tmp_path,
    )

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert payload["not_provenance"] is True
    assert payload["not_evidence"] is True
    assert payload["not_ledgered"] is True
    assert payload["plan"]["execution_performed"] is False
    assert "not_provenance: true" in markdown
    assert "not_evidence: true" in markdown
    assert "not_ledgered: true" in markdown
    assert len(ledger.list_commits("run-1")) == commits_before
    assert not (json_path.with_name(f"{json_path.name}.meta.json")).exists()
    assert not (markdown_path.with_name(f"{markdown_path.name}.meta.json")).exists()
    assert json_path.parent == store.run_path("run-1") / "hygiene"


def test_cli_json_output_is_valid_and_read_only(tmp_path) -> None:
    _, ledger = _minimal_run(tmp_path)
    commits_before = len(ledger.list_commits("run-1"))

    result = CliRunner().invoke(
        app,
        [
            "plan-hygiene-remediation",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["run_id"] == "run-1"
    assert payload["execution_performed"] is False
    assert len(ledger.list_commits("run-1")) == commits_before


def test_cli_write_report_creates_only_non_provenance_files(tmp_path) -> None:
    store, ledger = _minimal_run(tmp_path)
    commits_before = len(ledger.list_commits("run-1"))

    result = CliRunner().invoke(
        app,
        [
            "plan-hygiene-remediation",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--write-report",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "plan_status=NoRemediationNeeded" in result.output
    assert (store.run_path("run-1") / "hygiene" / "remediation-plan.json").is_file()
    assert (store.run_path("run-1") / "hygiene" / "remediation-plan.md").is_file()
    assert len(ledger.list_commits("run-1")) == commits_before


def test_cli_works_after_full_deterministic_run(tmp_path) -> None:
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id="complete",
            domain="human geography",
            root=tmp_path,
        )
    )
    ledger = ResearchLedger(tmp_path / "runs" / "complete" / "ledger.sqlite")
    commits_before = len(ledger.list_commits("complete"))

    result = CliRunner().invoke(
        app,
        [
            "plan-hygiene-remediation",
            "--root",
            str(tmp_path),
            "--run-id",
            "complete",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "plan_status=NoRemediationNeeded" in result.output
    assert "actions_total=0" in result.output
    assert "ledger_mutated=false" in result.output
    assert len(ledger.list_commits("complete")) == commits_before


def test_plan_round_trips_through_strict_schema() -> None:
    plan = build_hygiene_remediation_plan(
        _report([_finding(OutputHygieneCategory.UNEXPECTED_FILE, ["notes.txt"])])
    )

    restored = HygieneRemediationPlan.model_validate_json(plan.model_dump_json())

    assert restored == plan


def _minimal_run(tmp_path) -> tuple[ArtifactStore, ResearchLedger]:
    store = ArtifactStore(tmp_path)
    store.init_run("run-1")
    ledger = ResearchLedger(store.run_path("run-1") / "ledger.sqlite")
    ledger.append_commit(
        run_id="run-1",
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": "run-1"},
        timestamp="1970-01-01T00:00:00.000000Z",
    )
    return store, ledger


def _report(findings: list[OutputHygieneFinding]) -> OutputHygieneReport:
    blocking = sum(
        finding.severity == OutputHygieneSeverity.BLOCKING for finding in findings
    )
    warnings = sum(
        finding.severity == OutputHygieneSeverity.WARNING for finding in findings
    )
    status = (
        OutputHygieneStatus.HYGIENE_ISSUES_FOUND
        if blocking
        else OutputHygieneStatus.CLEAN_WITH_WARNINGS
        if warnings
        else OutputHygieneStatus.CLEAN
    )
    return OutputHygieneReport(
        run_id="run-1",
        hygiene_status=status,
        file_index=RunFileIndex(
            run_id="run-1",
            run_exists=True,
            run_path="runs/run-1",
            files=[],
            files_scanned=0,
            manifest_entries=0,
            ledger_exists=True,
            ledger_commit_count=1,
            artifact_manifest_exists=False,
        ),
        findings=findings,
        files_scanned=0,
        manifest_entries=0,
        orphaned_files=0,
        missing_manifest_files=0,
        hash_mismatches=0,
        duplicate_outputs=0,
        non_provenance_files=0,
        unexpected_files=0,
        warnings_count=warnings,
        blocking_findings_count=blocking,
    )


def _finding(
    category: OutputHygieneCategory,
    paths: list[str] | None = None,
) -> OutputHygieneFinding:
    severity = (
        OutputHygieneSeverity.WARNING
        if category
        in {
            OutputHygieneCategory.ORPHANED_ARTIFACT,
            OutputHygieneCategory.STALE_OUTPUT,
            OutputHygieneCategory.UNEXPECTED_FILE,
        }
        else OutputHygieneSeverity.BLOCKING
    )
    return OutputHygieneFinding(
        finding_id=f"finding-{category.value}",
        category=category,
        severity=severity,
        message=f"deterministic {category.value} finding",
        paths=paths or [],
    )


def _relative_files(run_path) -> set[str]:
    return {
        path.relative_to(run_path).as_posix()
        for path in run_path.rglob("*")
        if path.is_file()
    }
