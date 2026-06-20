from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from factori.abstract_synthesis import run_abstract_synthesis
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.cross_run import CrossRunError, compare_runs, write_cross_run_report
from factori.draft_skeleton import run_draft_skeleton_generation
from factori.export_plan import prepare_export
from factori.final_audit import run_final_audit
from factori.final_paper import run_paper_assembly
from factori.hashing import canonical_json, sha256_file
from factori.ledger import ResearchLedger
from factori.manifest import build_artifact_manifest
from factori.manuscript_plan import run_manuscript_planning
from factori.regression_diagnostics import summarize_cross_run_comparison
from factori.research_object import build_research_object
from factori.schemas import (
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactRef,
    ArtifactType,
    AuditCategory,
    AuditCheck,
    AuditCheckStatus,
    AuditSeverity,
    BranchOutcomeSummary,
    BranchStatus,
    Claim,
    ClaimTable,
    ConstraintSet,
    ControllerActionType,
    DiagnosticReport,
    DiagnosticStatus,
    ExportReadinessReport,
    FinalAuditReport,
    FinalNucleus,
    FinalNucleusType,
    LedgerSummary,
    PaperSkeleton,
    RegressionCategory,
    RegressionSeverity,
    RegressionStatus,
    ReleaseGateDecision,
    ReleaseGateStatus,
    ReplayCheck,
    ReplayStatus,
    ReplayVerificationReport,
    ResearchObject,
    VerificationLabel,
)
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_selection import run_stage_c_selection


def test_compare_errors_when_baseline_run_missing(tmp_path) -> None:
    _write_run(tmp_path, "candidate")

    with pytest.raises(CrossRunError, match="Baseline run does not exist"):
        compare_runs("baseline", "candidate", root=tmp_path)


def test_compare_errors_when_candidate_run_missing(tmp_path) -> None:
    _write_run(tmp_path, "baseline")

    with pytest.raises(CrossRunError, match="Candidate run does not exist"):
        compare_runs("baseline", "candidate", root=tmp_path)


def test_comparison_loads_required_and_optional_sources(tmp_path) -> None:
    _write_run(tmp_path, "baseline")
    _write_run(tmp_path, "candidate")

    report = compare_runs("baseline", "candidate", root=tmp_path)

    expected = {
        "ledger_summary",
        "artifact_manifest",
        "branch_outcomes",
        "research_object",
        "final_audit",
        "release_gate",
        "export_readiness",
        "replay_report",
        "diagnostic_report",
        "paper_skeleton",
        "claim_table",
    }
    assert expected <= set(report.sources_loaded["baseline"])
    assert expected <= set(report.sources_loaded["candidate"])


def test_comparison_is_deterministic(tmp_path) -> None:
    _write_run(tmp_path, "baseline")
    _write_run(tmp_path, "candidate")

    first = compare_runs("baseline", "candidate", root=tmp_path)
    second = compare_runs("baseline", "candidate", root=tmp_path)

    assert first == second


def test_identical_completed_runs_have_no_regression(tmp_path) -> None:
    _write_run(tmp_path, "baseline")
    _write_run(tmp_path, "candidate")

    report = compare_runs("baseline", "candidate", root=tmp_path)

    assert report.regression_status == RegressionStatus.NO_REGRESSION
    assert all(
        difference.severity == RegressionSeverity.INFO
        for difference in report.differences
    )


def test_two_full_deterministic_runs_have_no_blocking_regression(tmp_path) -> None:
    _run_full_pipeline(tmp_path, "baseline")
    _run_full_pipeline(tmp_path, "candidate")

    report = compare_runs("baseline", "candidate", root=tmp_path)

    assert report.regression_status in {
        RegressionStatus.NO_REGRESSION,
        RegressionStatus.REGRESSION_WARNINGS,
    }
    assert not any(
        finding.severity == RegressionSeverity.BLOCKING
        for finding in report.regression_findings
    )


def test_missing_candidate_output_is_blocking(tmp_path) -> None:
    _write_run(tmp_path, "baseline")
    paths = _write_run(tmp_path, "candidate")
    paths["paper_skeleton"].unlink()

    report = compare_runs("baseline", "candidate", root=tmp_path)

    assert report.regression_status == RegressionStatus.REGRESSION_DETECTED
    assert any(
        finding.category == RegressionCategory.MISSING_OUTPUT
        and finding.severity == RegressionSeverity.BLOCKING
        for finding in report.regression_findings
    )


def test_evidence_artifact_hash_drift_is_blocking(tmp_path) -> None:
    _write_run(tmp_path, "baseline", artifact_hash="a" * 64)
    _write_run(tmp_path, "candidate", artifact_hash="b" * 64)

    report = compare_runs("baseline", "candidate", root=tmp_path)

    assert any(
        finding.category == RegressionCategory.HASH_DRIFT
        and finding.severity == RegressionSeverity.BLOCKING
        for finding in report.regression_findings
    )


def test_evidence_boundary_classification_regression_is_blocking(tmp_path) -> None:
    _write_run(tmp_path, "baseline", presentation_evidence=False)
    _write_run(tmp_path, "candidate", presentation_evidence=True)

    report = compare_runs("baseline", "candidate", root=tmp_path)

    assert any(
        finding.category == RegressionCategory.EVIDENCE_BOUNDARY_REGRESSION
        for finding in report.regression_findings
    )


def test_claim_label_inflation_is_detected(tmp_path) -> None:
    _write_run(tmp_path, "baseline", claim_label=VerificationLabel.CONJECTURE)
    _write_run(tmp_path, "candidate", claim_label=VerificationLabel.LEAN_VERIFIED)

    report = compare_runs("baseline", "candidate", root=tmp_path)

    assert any(
        finding.category == RegressionCategory.CLAIM_LABEL_CHANGE
        and finding.severity == RegressionSeverity.BLOCKING
        for finding in report.regression_findings
    )


def test_synthetic_real_world_regression_is_detected(tmp_path) -> None:
    _write_run(
        tmp_path,
        "baseline",
        claim_label=VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
        claim_text="Controlled synthetic result.",
    )
    _write_run(
        tmp_path,
        "candidate",
        claim_label=VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
        claim_text="Controlled synthetic result proves real-world performance.",
    )

    report = compare_runs("baseline", "candidate", root=tmp_path)

    assert any(
        finding.category == RegressionCategory.EVIDENCE_BOUNDARY_REGRESSION
        for finding in report.regression_findings
    )


def test_new_blocked_claim_and_nucleus_change_are_warnings(tmp_path) -> None:
    _write_run(tmp_path, "baseline", nucleus_id="nucleus-a", blocked_claim=False)
    _write_run(tmp_path, "candidate", nucleus_id="nucleus-b", blocked_claim=True)

    report = compare_runs("baseline", "candidate", root=tmp_path)

    categories = {finding.category for finding in report.regression_findings}
    assert RegressionCategory.BLOCKED_CLAIM_CHANGE in categories
    assert any(
        finding.summary == "Different final nucleus selected"
        and finding.severity == RegressionSeverity.WARNING
        for finding in report.regression_findings
    )


def test_branch_outcome_count_change_is_warning(tmp_path) -> None:
    _write_run(tmp_path, "baseline", branch_count=1)
    _write_run(tmp_path, "candidate", branch_count=2)

    report = compare_runs("baseline", "candidate", root=tmp_path)

    assert any(
        finding.category == RegressionCategory.BRANCH_OUTCOME_CHANGE
        and finding.severity == RegressionSeverity.WARNING
        for finding in report.regression_findings
    )


def test_optional_replay_presence_is_information_only(tmp_path) -> None:
    _write_run(tmp_path, "baseline", replay_status=None)
    _write_run(tmp_path, "candidate", replay_status=ReplayStatus.REPLAY_VERIFIED)

    report = compare_runs("baseline", "candidate", root=tmp_path)

    replay_presence = next(
        difference
        for difference in report.differences
        if difference.field == "outputs.replay_report.present"
    )
    assert replay_presence.severity == RegressionSeverity.INFO
    assert report.regression_status == RegressionStatus.NO_REGRESSION


def test_summary_is_deterministic(tmp_path) -> None:
    _write_run(tmp_path, "baseline")
    _write_run(tmp_path, "candidate", branch_count=2)
    report = compare_runs("baseline", "candidate", root=tmp_path)

    first = summarize_cross_run_comparison(report)
    second = summarize_cross_run_comparison(report)

    assert first == second
    assert first.warning_regressions > 0


def test_comparison_does_not_mutate_ledgers_or_manifests(tmp_path) -> None:
    baseline_paths = _write_run(tmp_path, "baseline")
    candidate_paths = _write_run(tmp_path, "candidate")
    baseline_ledger = ResearchLedger(baseline_paths["ledger"])
    candidate_ledger = ResearchLedger(candidate_paths["ledger"])
    baseline_count = len(baseline_ledger.list_commits("baseline"))
    candidate_count = len(candidate_ledger.list_commits("candidate"))
    baseline_hash = sha256_file(baseline_paths["artifact_manifest"])
    candidate_hash = sha256_file(candidate_paths["artifact_manifest"])

    report = compare_runs("baseline", "candidate", root=tmp_path)

    assert len(baseline_ledger.list_commits("baseline")) == baseline_count
    assert len(candidate_ledger.list_commits("candidate")) == candidate_count
    assert sha256_file(baseline_paths["artifact_manifest"]) == baseline_hash
    assert sha256_file(candidate_paths["artifact_manifest"]) == candidate_hash
    assert not report.ledger_mutated
    assert not report.artifact_manifest_mutated


def test_write_report_is_outside_provenance_and_manifest(tmp_path) -> None:
    _write_run(tmp_path, "baseline")
    candidate_paths = _write_run(tmp_path, "candidate")
    candidate_ledger = ResearchLedger(candidate_paths["ledger"])
    before_count = len(candidate_ledger.list_commits("candidate"))
    report = compare_runs("baseline", "candidate", root=tmp_path)

    json_path, markdown_path = write_cross_run_report(report=report, root=tmp_path)

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert payload["not_provenance"] is True
    assert payload["not_evidence"] is True
    assert payload["not_ledgered"] is True
    assert "not_provenance: true" in markdown
    assert "not_evidence: true" in markdown
    assert "not_ledgered: true" in markdown
    assert len(candidate_ledger.list_commits("candidate")) == before_count
    manifest = build_artifact_manifest("candidate", ArtifactStore(tmp_path))
    assert all("/comparisons/" not in entry.path for entry in manifest.artifacts)


def test_cli_compare_runs_works(tmp_path) -> None:
    _write_run(tmp_path, "baseline")
    _write_run(tmp_path, "candidate")
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "compare-runs",
            "--root",
            str(tmp_path),
            "--baseline-run-id",
            "baseline",
            "--candidate-run-id",
            "candidate",
        ],
    )

    assert result.exit_code == 0
    assert "baseline_run_id=baseline" in result.output
    assert "candidate_run_id=candidate" in result.output
    assert "regression_status=NoRegression" in result.output
    assert "ledger_mutated=false" in result.output


def test_cli_compare_runs_write_report_works(tmp_path) -> None:
    _write_run(tmp_path, "baseline")
    _write_run(tmp_path, "candidate")
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "compare-runs",
            "--root",
            str(tmp_path),
            "--baseline-run-id",
            "baseline",
            "--candidate-run-id",
            "candidate",
            "--write-report",
        ],
    )

    report_path = (
        tmp_path
        / "runs"
        / "candidate"
        / "comparisons"
        / "comparison-baseline-vs-candidate.json"
    )
    assert result.exit_code == 0
    assert report_path.is_file()


def test_cli_compare_runs_errors_for_missing_run(tmp_path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        [
            "compare-runs",
            "--root",
            str(tmp_path),
            "--baseline-run-id",
            "missing",
            "--candidate-run-id",
            "candidate",
        ],
    )

    assert result.exit_code == 1
    assert "Baseline run does not exist" in result.stderr


def _write_run(
    root,
    run_id: str,
    *,
    artifact_hash: str = "a" * 64,
    presentation_evidence: bool = False,
    nucleus_id: str = "nucleus-a",
    claim_label: VerificationLabel = VerificationLabel.CONJECTURE,
    claim_text: str = "Deterministic claim.",
    blocked_claim: bool = False,
    branch_count: int = 1,
    release_status: ReleaseGateStatus = ReleaseGateStatus.RELEASE_READY,
    replay_status: ReplayStatus | None = ReplayStatus.REPLAY_VERIFIED,
    diagnostic_status: DiagnosticStatus | None = DiagnosticStatus.NO_ISSUES,
) -> dict[str, object]:
    run_path = root / "runs" / run_id
    reports_path = run_path / "reports"
    research_path = run_path / "research_object"
    reports_path.mkdir(parents=True, exist_ok=True)
    research_path.mkdir(parents=True, exist_ok=True)
    ledger_path = run_path / "ledger.sqlite"
    ledger = ResearchLedger(ledger_path)
    commit = ledger.append_commit(
        run_id=run_id,
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": run_id},
        timestamp="1970-01-01T00:00:00.000000Z",
    )
    ledger_summary = LedgerSummary(
        run_id=run_id,
        commit_count=1,
        root_commit_hash=commit.commit_hash,
        latest_commit_hash=commit.commit_hash,
        action_type_counts={ControllerActionType.INIT_RUN.value: 1},
        candidate_count=1,
        artifact_count=1,
        verification_decision_count=1,
        human_tail_escalation_count=0,
    )
    entry_path = (
        f"runs/{run_id}/reports/presentation.md"
        if presentation_evidence
        else f"runs/{run_id}/experiments/evidence.json"
    )
    manifest = ArtifactManifest(
        run_id=run_id,
        artifacts=[
            ArtifactManifestEntry(
                artifact_id="evidence-1",
                artifact_type=(
                    ArtifactType.REPORT
                    if presentation_evidence
                    else ArtifactType.EXPERIMENT
                ),
                path=entry_path,
                content_hash=artifact_hash,
                producing_commit_hash=commit.commit_hash,
                is_evidence=True,
                is_presentation=presentation_evidence,
            )
        ],
        evidence_artifact_count=1,
        presentation_artifact_count=int(presentation_evidence),
    )
    branch_outcomes = [
        BranchOutcomeSummary(
            candidate_id=f"candidate-{index}",
            outcome="StageCReady",
            status=BranchStatus.STAGE_C_READY,
            verification_label=claim_label,
            action_type=ControllerActionType.STAGE_C_VERIFICATION_DECIDED,
            reason="deterministic outcome",
        )
        for index in range(branch_count)
    ]
    nucleus = FinalNucleus(
        id=nucleus_id,
        nucleus_type=FinalNucleusType.BRANCH_NUCLEUS,
        candidate_id="candidate-0",
        supporting_candidate_ids=["candidate-0"],
        labels_by_candidate={"candidate-0": claim_label},
        reason="deterministic nucleus",
    )
    ref = _ref(run_id, "source", commit.commit_hash)
    research_object = ResearchObject(
        run_id=run_id,
        final_nucleus=nucleus,
        manuscript_plan_ref=ref,
        draft_skeleton_ref=ref,
        claim_table_ref=ref,
        blocked_claims_ref=ref,
        checklist_ref=ref,
        stage_reports={},
        artifact_manifest_ref=ref,
        ledger_summary_ref=ref,
        branch_outcomes_ref=ref,
        reproducibility_manifest_ref=ref,
        created_at="1970-01-01T00:00:00.000000Z",
    )
    audit_check = AuditCheck(
        check_id="evidence_boundary",
        category=AuditCategory.EVIDENCE_BOUNDARY,
        status=AuditCheckStatus.PASS,
        severity=AuditSeverity.INFO,
        message="evidence boundary passes",
    )
    audit = FinalAuditReport(
        run_id=run_id,
        checks=[audit_check],
        passes_count=1,
        warnings_count=0,
        failures_count=0,
        blocking_failures_count=0,
    )
    ready = release_status != ReleaseGateStatus.RELEASE_BLOCKED
    release = ReleaseGateDecision(
        run_id=run_id,
        status=release_status,
        ready_for_polished_prose=ready,
        ready_for_latex_export=ready,
        ready_for_external_review=False,
        blocking_reasons=[] if ready else ["blocked"],
        warnings=(
            ["warning"]
            if release_status == ReleaseGateStatus.RELEASE_READY_WITH_WARNINGS
            else []
        ),
        audit_checks=1,
    )
    export = ExportReadinessReport(
        run_id=run_id,
        ready_for_polished_prose=ready,
        ready_for_latex_export=ready,
        ready_for_external_review=False,
        export_blocked=not ready,
        export_allowed_claims=int(ready),
        export_blocked_claims=int(not ready),
        blocking_reasons=[] if ready else ["blocked"],
    )
    claim_table = ClaimTable(
        final_nucleus_id=nucleus_id,
        claims=[
            Claim(
                claim_id="claim-1",
                claim_text=claim_text,
                claim_label=claim_label,
                candidate_id="candidate-0",
                evidence_artifact_ids=["evidence-1"],
                evidence_types=["experiment"],
                allowed_in_main_text=not blocked_claim,
                allowed_section="Theory",
                reason="deterministic claim",
            )
        ],
    )
    paper = PaperSkeleton(
        paper_id="paper-1",
        run_id=run_id,
        title="Paper",
        abstract_scaffold="Abstract",
        sections=[],
        appendices=[],
        claim_placeholders=[],
        provenance_refs={},
    )
    paths = {
        "ledger": ledger_path,
        "ledger_summary": research_path / "ledger-summary.json",
        "artifact_manifest": research_path / "artifact-manifest.json",
        "branch_outcomes": research_path / "branch-outcomes.json",
        "research_object": research_path / "research-object.json",
        "final_audit": reports_path / "final-audit-report.json",
        "release_gate": reports_path / "release-gate-decision.json",
        "export_readiness": reports_path / "export-readiness-report.json",
        "paper_skeleton": research_path / "paper-skeleton.json",
        "claim_table": reports_path / "claim-table.json",
    }
    _write(paths["ledger_summary"], ledger_summary)
    _write(paths["artifact_manifest"], manifest)
    _write(paths["branch_outcomes"], {"branch_outcomes": branch_outcomes})
    _write(paths["research_object"], research_object)
    _write(paths["final_audit"], audit)
    _write(paths["release_gate"], release)
    _write(paths["export_readiness"], export)
    _write(paths["paper_skeleton"], paper)
    _write(paths["claim_table"], claim_table)
    if replay_status is not None:
        replay = ReplayVerificationReport(
            run_id=run_id,
            checks=[
                ReplayCheck(
                    check_id="ledger_hash_chain_valid",
                    category=AuditCategory.LEDGER_INTEGRITY,
                    status=(
                        AuditCheckStatus.FAIL
                        if replay_status == ReplayStatus.REPLAY_FAILED
                        else AuditCheckStatus.PASS
                    ),
                    severity=(
                        AuditSeverity.BLOCKING
                        if replay_status == ReplayStatus.REPLAY_FAILED
                        else AuditSeverity.INFO
                    ),
                    message="deterministic replay",
                )
            ],
            replay_status=replay_status,
            ledger_commits_checked=1,
            artifacts_checked=1,
            hashes_verified=1,
            evidence_artifacts_checked=1,
            presentation_artifacts_checked=0,
            stage_outputs_checked=9,
            warnings_count=0,
            blocking_failures_count=int(replay_status == ReplayStatus.REPLAY_FAILED),
            ledger_mutated=False,
            artifact_manifest_mutated=False,
        )
        replay_path = run_path / "replay" / "replay-verification-report.json"
        replay_path.parent.mkdir(parents=True, exist_ok=True)
        _write(replay_path, {"report": replay})
        paths["replay_report"] = replay_path
    if diagnostic_status is not None:
        diagnostic = DiagnosticReport(
            run_id=run_id,
            diagnostic_status=diagnostic_status,
            root_causes=[],
            finding_groups=[],
            recommended_steps=[],
            sources_loaded=[],
            blocking_causes_count=int(diagnostic_status == DiagnosticStatus.BLOCKED),
            warning_causes_count=0,
            ledger_mutated=False,
            artifact_manifest_mutated=False,
        )
        diagnostic_path = run_path / "diagnostics" / "diagnostic-report.json"
        diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
        _write(diagnostic_path, {"report": diagnostic})
        paths["diagnostic_report"] = diagnostic_path
    return paths


def _ref(run_id: str, artifact_id: str, commit_hash: str) -> ArtifactRef:
    return ArtifactRef(
        id=artifact_id,
        type=ArtifactType.REPORT,
        path=f"runs/{run_id}/reports/{artifact_id}.json",
        content_hash="c" * 64,
        producing_commit_hash=commit_hash,
    )


def _write(path, payload) -> None:
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")


def _run_full_pipeline(root, run_id: str) -> None:
    store = ArtifactStore(root)
    ledger = ResearchLedger(root / "runs" / run_id / "ledger.sqlite")
    run_stage_a(
        run_id=run_id,
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )
    run_stage_b(run_id=run_id, store=store, ledger=ledger)
    run_stage_c_selection(run_id=run_id, store=store, ledger=ledger)
    run_stage_c(run_id=run_id, store=store, ledger=ledger)
    run_abstract_synthesis(run_id=run_id, store=store, ledger=ledger)
    run_manuscript_planning(run_id=run_id, store=store, ledger=ledger)
    run_draft_skeleton_generation(run_id=run_id, store=store, ledger=ledger)
    build_research_object(run_id=run_id, store=store, ledger=ledger)
    run_paper_assembly(run_id=run_id, store=store, ledger=ledger)
    run_final_audit(run_id=run_id, store=store, ledger=ledger)
    prepare_export(run_id=run_id, store=store, ledger=ledger)
