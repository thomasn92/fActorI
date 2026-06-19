from __future__ import annotations

import json

from factori.abstract_synthesis import run_abstract_synthesis
from factori.artifacts import ArtifactStore
from factori.draft_skeleton import run_draft_skeleton_generation
from factori.export_plan import prepare_export
from factori.final_audit import run_final_audit
from factori.final_paper import run_paper_assembly
from factori.hashing import sha256_file
from factori.ledger import ResearchLedger
from factori.manuscript_plan import run_manuscript_planning
from factori.replay import replay_verify_run
from factori.research_object import build_research_object
from factori.run_verifier import summarize_replay_verification
from factori.schemas import (
    AuditCheckStatus,
    ConstraintSet,
    ReplayStatus,
)
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_selection import run_stage_c_selection


def test_replay_complete_run_verifies_or_warns(tmp_path) -> None:
    _, ledger = _run_pipeline_to_export(tmp_path)

    report = replay_verify_run("run-1", root=tmp_path)

    assert report.replay_status in {
        ReplayStatus.REPLAY_VERIFIED,
        ReplayStatus.REPLAY_VERIFIED_WITH_WARNINGS,
    }
    assert report.ledger_commits_checked == len(ledger.list_commits("run-1"))
    assert report.artifacts_checked > 0
    assert report.hashes_verified > 0


def test_replay_loads_ledger_manifest_and_recomputes_hashes(tmp_path) -> None:
    _run_pipeline_to_export(tmp_path)

    report = replay_verify_run("run-1", root=tmp_path)

    assert _status(report, "ledger_hash_chain_valid") == AuditCheckStatus.PASS
    assert _status(report, "artifact_manifest_loaded") == AuditCheckStatus.PASS
    assert any(check.check_id.startswith("manifest_hash:") for check in report.checks)


def test_replay_detects_tampered_artifact(tmp_path) -> None:
    _run_pipeline_to_export(tmp_path)
    path = tmp_path / "runs" / "run-1" / "reports" / "claim-table.json"
    path.write_text(path.read_text(encoding="utf-8") + "\nTAMPERED\n", encoding="utf-8")

    report = replay_verify_run("run-1", root=tmp_path)

    assert report.replay_status == ReplayStatus.REPLAY_FAILED
    assert any("hash mismatch" in check.message for check in report.checks)


def test_replay_detects_missing_artifact(tmp_path) -> None:
    _run_pipeline_to_export(tmp_path)
    (tmp_path / "runs" / "run-1" / "research_object" / "paper-skeleton.json").unlink()

    report = replay_verify_run("run-1", root=tmp_path)

    assert report.replay_status == ReplayStatus.REPLAY_FAILED
    assert any("artifact file not found" in check.message for check in report.checks)


def test_replay_detects_missing_producing_commit_for_evidence(tmp_path) -> None:
    _run_pipeline_to_export(tmp_path)
    manifest_path = tmp_path / "runs" / "run-1" / "research_object" / "artifact-manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    for entry in data["artifacts"]:
        if entry["is_evidence"]:
            entry["producing_commit_hash"] = None
            break
    manifest_path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")

    report = replay_verify_run("run-1", root=tmp_path)

    assert report.replay_status == ReplayStatus.REPLAY_FAILED
    assert _status(report, "evidence_artifacts_have_producing_commits") == AuditCheckStatus.FAIL


def test_replay_detects_markdown_or_latex_as_verification_evidence(tmp_path) -> None:
    _run_pipeline_to_export(tmp_path)
    manifest_path = tmp_path / "runs" / "run-1" / "research_object" / "artifact-manifest.json"
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    markdown_entry = next(entry for entry in data["artifacts"] if entry["path"].endswith(".md"))
    markdown_entry["is_evidence"] = True
    markdown_entry["is_presentation"] = True
    manifest_path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")

    report = replay_verify_run("run-1", root=tmp_path)

    assert report.replay_status == ReplayStatus.REPLAY_FAILED
    assert _status(report, "presentation_artifacts_not_verification_evidence") == (
        AuditCheckStatus.FAIL
    )


def test_replay_detects_real_data_experiment_verified_in_mvp(tmp_path) -> None:
    _run_pipeline_to_export(tmp_path)
    claim_table_path = tmp_path / "runs" / "run-1" / "reports" / "claim-table.json"
    data = json.loads(claim_table_path.read_text(encoding="utf-8"))
    data["claims"][0]["claim_label"] = "RealDataExperimentVerified"
    claim_table_path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")

    report = replay_verify_run("run-1", root=tmp_path)

    assert report.replay_status == ReplayStatus.REPLAY_FAILED
    assert _status(report, "no_real_data_experiment_verified_in_mvp") == AuditCheckStatus.FAIL


def test_replay_detects_missing_final_nucleus_claim_table_and_paper_skeleton(tmp_path) -> None:
    _run_pipeline_to_export(tmp_path)
    for path in [
        tmp_path / "runs" / "run-1" / "reports" / "final-nucleus.json",
        tmp_path / "runs" / "run-1" / "reports" / "claim-table.json",
        tmp_path / "runs" / "run-1" / "research_object" / "paper-skeleton.json",
    ]:
        path.unlink()

    report = replay_verify_run("run-1", root=tmp_path)

    assert _status(report, "final_nucleus_loaded") == AuditCheckStatus.FAIL
    assert _status(report, "claim_table_loaded") == AuditCheckStatus.FAIL
    assert _status(report, "paper_skeleton_loaded") == AuditCheckStatus.FAIL


def test_replay_detects_missing_export_readiness_report(tmp_path) -> None:
    _run_pipeline_to_export(tmp_path)
    (tmp_path / "runs" / "run-1" / "reports" / "export-readiness-report.json").unlink()

    report = replay_verify_run("run-1", root=tmp_path)

    assert _status(report, "export_readiness_loaded") == AuditCheckStatus.FAIL


def test_replay_detects_release_gate_inconsistency(tmp_path) -> None:
    _run_pipeline_to_export(tmp_path)
    release_path = tmp_path / "runs" / "run-1" / "reports" / "release-gate-decision.json"
    data = json.loads(release_path.read_text(encoding="utf-8"))
    data["status"] = "ReleaseBlocked"
    data["ready_for_polished_prose"] = False
    data["ready_for_latex_export"] = False
    release_path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")

    report = replay_verify_run("run-1", root=tmp_path)

    assert _status(report, "release_gate_consistent_with_final_audit") == (
        AuditCheckStatus.FAIL
    )


def test_replay_detects_export_readiness_inconsistency(tmp_path) -> None:
    _run_pipeline_to_export(tmp_path)
    readiness_path = tmp_path / "runs" / "run-1" / "reports" / "export-readiness-report.json"
    data = json.loads(readiness_path.read_text(encoding="utf-8"))
    data["ready_for_polished_prose"] = False
    data["ready_for_latex_export"] = False
    data["export_blocked"] = True
    data["blocking_reasons"] = ["tampered export readiness"]
    readiness_path.write_text(json.dumps(data, sort_keys=True) + "\n", encoding="utf-8")

    report = replay_verify_run("run-1", root=tmp_path)

    assert _status(report, "export_readiness_consistent_with_release_gate") == (
        AuditCheckStatus.FAIL
    )


def test_replay_confirms_blocked_and_failed_branches_are_represented(tmp_path) -> None:
    _run_pipeline_to_export(tmp_path)

    report = replay_verify_run("run-1", root=tmp_path)

    assert _status(report, "blocked_claims_represented") in {
        AuditCheckStatus.PASS,
        AuditCheckStatus.NOT_APPLICABLE,
    }
    assert _status(report, "failed_deferred_pruned_branches_represented") in {
        AuditCheckStatus.PASS,
        AuditCheckStatus.NOT_APPLICABLE,
    }


def test_replay_confirms_runtime_summaries_are_not_provenance(tmp_path) -> None:
    _run_pipeline_to_export(tmp_path)

    report = replay_verify_run("run-1", root=tmp_path)

    assert _status(report, "runtime_summary_not_provenance") == AuditCheckStatus.PASS


def test_blocking_failures_produce_replay_failed(tmp_path) -> None:
    _run_pipeline_to_export(tmp_path)
    (tmp_path / "runs" / "run-1" / "reports" / "claim-table.json").unlink()

    report = replay_verify_run("run-1", root=tmp_path)

    assert report.replay_status == ReplayStatus.REPLAY_FAILED


def test_replay_summary_is_deterministic(tmp_path) -> None:
    _run_pipeline_to_export(tmp_path)

    first = summarize_replay_verification(replay_verify_run("run-1", root=tmp_path))
    second = summarize_replay_verification(replay_verify_run("run-1", root=tmp_path))

    assert first == second


def test_replay_does_not_mutate_ledger_or_artifact_manifest(tmp_path) -> None:
    _, ledger = _run_pipeline_to_export(tmp_path)
    manifest_path = tmp_path / "runs" / "run-1" / "research_object" / "artifact-manifest.json"
    before_commits = len(ledger.list_commits("run-1"))
    before_manifest_hash = sha256_file(manifest_path)

    report = replay_verify_run("run-1", root=tmp_path)

    assert len(ledger.list_commits("run-1")) == before_commits
    assert sha256_file(manifest_path) == before_manifest_hash
    assert not report.ledger_mutated
    assert not report.artifact_manifest_mutated


def test_replay_report_files_are_excluded_from_artifact_manifest(tmp_path) -> None:
    store, _ = _run_pipeline_to_export(tmp_path)
    from factori.manifest import build_artifact_manifest
    from factori.replay import write_replay_report

    write_replay_report(
        run_id="run-1",
        report=replay_verify_run("run-1", root=tmp_path),
        root=tmp_path,
    )

    manifest = build_artifact_manifest("run-1", store)
    assert all("/replay/" not in entry.path for entry in manifest.artifacts)


def _run_pipeline_to_export(tmp_path) -> tuple[ArtifactStore, ResearchLedger]:
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
    return store, ledger


def _status(report, check_id: str) -> AuditCheckStatus:
    matches = [check.status for check in report.checks if check.check_id == check_id]
    assert matches, check_id
    return matches[0]
