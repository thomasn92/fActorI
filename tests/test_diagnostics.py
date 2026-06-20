from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from factori.abstract_synthesis import run_abstract_synthesis
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.diagnostics import (
    DiagnosticError,
    build_diagnostic_report,
    write_diagnostic_report,
)
from factori.draft_skeleton import run_draft_skeleton_generation
from factori.export_plan import prepare_export
from factori.final_audit import run_final_audit
from factori.final_paper import run_paper_assembly
from factori.hashing import canonical_json, sha256_file
from factori.ledger import ResearchLedger
from factori.manifest import build_artifact_manifest
from factori.manuscript_plan import run_manuscript_planning
from factori.release_gate import decide_release_gate
from factori.replay import replay_verify_run, write_replay_report
from factori.research_object import build_research_object
from factori.schemas import (
    ArtifactManifest,
    AuditCategory,
    AuditCheck,
    AuditCheckStatus,
    AuditSeverity,
    ConstraintSet,
    ControllerActionType,
    DiagnosticStatus,
    FinalAuditReport,
    ReplayCheck,
    ReplayStatus,
    ReplayVerificationReport,
)
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_selection import run_stage_c_selection


def test_diagnose_run_errors_without_audit_or_replay(tmp_path) -> None:
    with pytest.raises(DiagnosticError, match="No final audit or replay outputs found"):
        build_diagnostic_report("run-1", root=tmp_path)


def test_diagnostics_loads_final_audit_when_replay_absent(tmp_path) -> None:
    _write_final_audit_sources(tmp_path)

    report = build_diagnostic_report("run-1", root=tmp_path)

    assert "final_audit_report" in report.sources_loaded
    assert "replay_report" not in report.sources_loaded


def test_diagnostics_loads_replay_report_when_present(tmp_path) -> None:
    _write_replay_source(tmp_path)

    report = build_diagnostic_report("run-1", root=tmp_path)

    assert "replay_report" in report.sources_loaded


def test_diagnostics_is_deterministic(tmp_path) -> None:
    _write_final_audit_sources(tmp_path)

    first = build_diagnostic_report("run-1", root=tmp_path)
    second = build_diagnostic_report("run-1", root=tmp_path)

    assert first == second


def test_complete_deterministic_sources_produce_no_issues(tmp_path) -> None:
    _write_final_audit_sources(tmp_path)

    report = build_diagnostic_report("run-1", root=tmp_path)

    assert report.diagnostic_status in {
        DiagnosticStatus.NO_ISSUES,
        DiagnosticStatus.WARNINGS_ONLY,
    }
    assert report.blocking_causes_count == 0


def test_complete_pipeline_through_replay_produces_no_issues_or_warnings(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )
    run_stage_b(run_id="run-1", store=store, ledger=ledger)
    run_stage_c_selection(run_id="run-1", store=store, ledger=ledger)
    run_stage_c(run_id="run-1", store=store, ledger=ledger)
    run_abstract_synthesis(run_id="run-1", store=store, ledger=ledger)
    run_manuscript_planning(run_id="run-1", store=store, ledger=ledger)
    run_draft_skeleton_generation(run_id="run-1", store=store, ledger=ledger)
    build_research_object(run_id="run-1", store=store, ledger=ledger)
    run_paper_assembly(run_id="run-1", store=store, ledger=ledger)
    run_final_audit(run_id="run-1", store=store, ledger=ledger)
    prepare_export(run_id="run-1", store=store, ledger=ledger)
    replay_report = replay_verify_run("run-1", root=tmp_path)
    write_replay_report(run_id="run-1", report=replay_report, root=tmp_path)

    report = build_diagnostic_report("run-1", root=tmp_path)

    assert report.diagnostic_status in {
        DiagnosticStatus.NO_ISSUES,
        DiagnosticStatus.WARNINGS_ONLY,
    }
    assert report.blocking_causes_count == 0


def test_blocking_root_cause_produces_blocked(tmp_path) -> None:
    _write_final_audit_sources(tmp_path, blocking=True)

    report = build_diagnostic_report("run-1", root=tmp_path)

    assert report.diagnostic_status == DiagnosticStatus.BLOCKED
    assert report.blocking_causes_count > 0


def test_nonblocking_warning_produces_warning_or_action_status(tmp_path) -> None:
    _write_final_audit_sources(tmp_path, warning=True)

    report = build_diagnostic_report("run-1", root=tmp_path)

    assert report.diagnostic_status in {
        DiagnosticStatus.WARNINGS_ONLY,
        DiagnosticStatus.ACTION_RECOMMENDED,
    }
    assert report.warning_causes_count == 1


def test_diagnostics_does_not_mutate_ledger_or_manifest(tmp_path) -> None:
    ledger, manifest_path = _write_final_audit_sources(tmp_path)
    before_commits = len(ledger.list_commits("run-1"))
    before_manifest_hash = sha256_file(manifest_path)

    report = build_diagnostic_report("run-1", root=tmp_path)

    assert len(ledger.list_commits("run-1")) == before_commits
    assert sha256_file(manifest_path) == before_manifest_hash
    assert not report.ledger_mutated
    assert not report.artifact_manifest_mutated


def test_write_report_is_marked_outside_provenance(tmp_path) -> None:
    ledger, manifest_path = _write_final_audit_sources(tmp_path)
    report = build_diagnostic_report("run-1", root=tmp_path)
    before_commits = len(ledger.list_commits("run-1"))
    before_manifest_hash = sha256_file(manifest_path)

    json_path, markdown_path = write_diagnostic_report(
        run_id="run-1",
        report=report,
        root=tmp_path,
    )

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert payload["not_provenance"] is True
    assert payload["not_evidence"] is True
    assert payload["not_ledgered"] is True
    assert "not_provenance: true" in markdown
    assert "not_evidence: true" in markdown
    assert "not_ledgered: true" in markdown
    assert len(ledger.list_commits("run-1")) == before_commits
    assert sha256_file(manifest_path) == before_manifest_hash


def test_diagnostic_reports_are_excluded_from_normal_manifest(tmp_path) -> None:
    _write_final_audit_sources(tmp_path)
    report = build_diagnostic_report("run-1", root=tmp_path)
    write_diagnostic_report(run_id="run-1", report=report, root=tmp_path)

    manifest = build_artifact_manifest("run-1", ArtifactStore(tmp_path))

    assert all("/diagnostics/" not in entry.path for entry in manifest.artifacts)


def test_cli_diagnose_run_works_after_final_audit(tmp_path) -> None:
    _write_final_audit_sources(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["diagnose-run", "--root", str(tmp_path), "--run-id", "run-1"],
    )

    assert result.exit_code == 0
    assert "run_id=run-1" in result.output
    assert "diagnostic_status=" in result.output
    assert "ledger_mutated=false" in result.output


def test_cli_diagnose_run_works_after_replay_report(tmp_path) -> None:
    _write_replay_source(tmp_path)
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["diagnose-run", "--root", str(tmp_path), "--run-id", "run-1"],
    )

    assert result.exit_code == 0
    assert "root_causes=0" in result.output


def test_cli_diagnose_run_write_report_works(tmp_path) -> None:
    ledger, _ = _write_final_audit_sources(tmp_path)
    before_commits = len(ledger.list_commits("run-1"))
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "diagnose-run",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--write-report",
        ],
    )

    assert result.exit_code == 0
    assert (tmp_path / "runs" / "run-1" / "diagnostics" / "diagnostic-report.json").is_file()
    assert (tmp_path / "runs" / "run-1" / "diagnostics" / "diagnostic-report.md").is_file()
    assert len(ledger.list_commits("run-1")) == before_commits


def test_cli_diagnose_run_errors_without_sources(tmp_path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["diagnose-run", "--root", str(tmp_path), "--run-id", "run-1"],
    )

    assert result.exit_code == 1
    assert "No final audit or replay outputs found" in result.stderr


def _write_final_audit_sources(
    root,
    *,
    warning: bool = False,
    blocking: bool = False,
) -> tuple[ResearchLedger, object]:
    run_path = root / "runs" / "run-1"
    reports_path = run_path / "reports"
    research_path = run_path / "research_object"
    reports_path.mkdir(parents=True, exist_ok=True)
    research_path.mkdir(parents=True, exist_ok=True)
    ledger = ResearchLedger(run_path / "ledger.sqlite")
    ledger.append_commit(
        run_id="run-1",
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": "run-1"},
        timestamp="1970-01-01T00:00:00.000000Z",
    )
    if blocking:
        check = AuditCheck(
            check_id="final_nucleus_exists",
            category=AuditCategory.RESEARCH_OBJECT_COMPLETENESS,
            status=AuditCheckStatus.FAIL,
            severity=AuditSeverity.BLOCKING,
            message="final nucleus is missing",
        )
    elif warning:
        check = AuditCheck(
            check_id="human_required_only_for_tail_cases",
            category=AuditCategory.HUMAN_ESCALATION_POLICY,
            status=AuditCheckStatus.WARNING,
            severity=AuditSeverity.WARNING,
            message="deterministic warning",
        )
    else:
        check = AuditCheck(
            check_id="ledger_exists",
            category=AuditCategory.LEDGER_INTEGRITY,
            status=AuditCheckStatus.PASS,
            severity=AuditSeverity.INFO,
            message="ledger exists",
        )
    audit = FinalAuditReport(
        run_id="run-1",
        checks=[check],
        passes_count=int(check.status == AuditCheckStatus.PASS),
        warnings_count=int(check.status == AuditCheckStatus.WARNING),
        failures_count=int(check.status == AuditCheckStatus.FAIL),
        blocking_failures_count=int(
            check.status == AuditCheckStatus.FAIL
            and check.severity == AuditSeverity.BLOCKING
        ),
    )
    release = decide_release_gate(audit)
    (reports_path / "final-audit-report.json").write_text(
        canonical_json(audit) + "\n",
        encoding="utf-8",
    )
    (reports_path / "release-gate-decision.json").write_text(
        canonical_json(release) + "\n",
        encoding="utf-8",
    )
    manifest = ArtifactManifest(
        run_id="run-1",
        artifacts=[],
        evidence_artifact_count=0,
        presentation_artifact_count=0,
    )
    manifest_path = research_path / "artifact-manifest.json"
    manifest_path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    return ledger, manifest_path


def _write_replay_source(root) -> None:
    replay_path = root / "runs" / "run-1" / "replay"
    replay_path.mkdir(parents=True, exist_ok=True)
    check = ReplayCheck(
        check_id="ledger_hash_chain_valid",
        category=AuditCategory.LEDGER_INTEGRITY,
        status=AuditCheckStatus.PASS,
        severity=AuditSeverity.INFO,
        message="ledger hash chain is valid",
    )
    report = ReplayVerificationReport(
        run_id="run-1",
        checks=[check],
        replay_status=ReplayStatus.REPLAY_VERIFIED,
        ledger_commits_checked=1,
        artifacts_checked=0,
        hashes_verified=0,
        evidence_artifacts_checked=0,
        presentation_artifacts_checked=0,
        stage_outputs_checked=0,
        warnings_count=0,
        blocking_failures_count=0,
        ledger_mutated=False,
        artifact_manifest_mutated=False,
    )
    payload = {
        "not_provenance": True,
        "not_evidence": True,
        "not_ledgered": True,
        "report": report,
    }
    (replay_path / "replay-verification-report.json").write_text(
        canonical_json(payload) + "\n",
        encoding="utf-8",
    )
