from __future__ import annotations

import pytest
from typer.testing import CliRunner

from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.ledger import ResearchLedger
from factori.schemas import (
    BranchVerificationType,
    Candidate,
    ConstraintSet,
    ControllerActionType,
    DataRequirement,
    VerificationLabel,
)
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import (
    StageCError,
    classify_stage_c_branch,
    load_stage_c_ready_candidates,
    run_fake_no_data_methodological_validation,
    run_stage_c,
)
from factori.stage_c_selection import run_stage_c_selection


def test_stage_c_errors_clearly_if_selection_has_not_run(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    with pytest.raises(StageCError, match="Stage C-ready candidates not found"):
        load_stage_c_ready_candidates("run-1", ledger)


def test_stage_c_loads_ready_candidates(tmp_path) -> None:
    _, ledger = _run_stage_c_selection_pipeline(tmp_path)

    candidates = load_stage_c_ready_candidates("run-1", ledger)

    assert len(candidates) == 1
    assert candidates[0].status.value == "StageCReady"


def test_branch_type_classification_is_deterministic() -> None:
    mathematical = Candidate(
        id="candidate-math",
        question="Can we prove a theorem?",
        theory="Theorem-style claim",
    )
    synthetic = Candidate(
        id="candidate-synthetic",
        question="Can synthetic data validate this method?",
        data_requirement=DataRequirement.SYNTHETIC_ONLY,
    )
    no_data = Candidate(
        id="candidate-method",
        question="Can a methodological branch remain explicitly labeled?",
        data_requirement=DataRequirement.NO_DATA,
    )
    unsupported = Candidate(
        id="candidate-real-data",
        question="Does this require public data?",
        data_requirement=DataRequirement.PUBLIC_DOWNLOAD,
    )

    assert classify_stage_c_branch(mathematical) == BranchVerificationType.MATHEMATICAL
    assert classify_stage_c_branch(mathematical) == BranchVerificationType.MATHEMATICAL
    assert classify_stage_c_branch(synthetic) == BranchVerificationType.SYNTHETIC_EMPIRICAL
    assert classify_stage_c_branch(no_data) == BranchVerificationType.NO_DATA_METHODOLOGICAL
    assert classify_stage_c_branch(unsupported) == BranchVerificationType.UNSUPPORTED


def test_no_data_methodological_branches_cannot_become_experiment_verified() -> None:
    candidate = Candidate(
        id="candidate-method",
        question="Can a no-data method be labeled without experiment evidence?",
        data_requirement=DataRequirement.NO_DATA,
    )

    record = run_fake_no_data_methodological_validation(candidate)

    assert record.branch_type == BranchVerificationType.NO_DATA_METHODOLOGICAL
    assert record.label in {
        VerificationLabel.CONJECTURE,
        VerificationLabel.LIMITATION,
        VerificationLabel.UNSUPPORTED,
    }
    assert record.label != VerificationLabel.EXPERIMENT_VERIFIED
    assert record.label != VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED


def test_stage_c_verifies_selected_candidates_and_writes_artifacts(tmp_path) -> None:
    store, ledger = _run_stage_c_selection_pipeline(tmp_path)

    result = run_stage_c(run_id="run-1", store=store, ledger=ledger)

    assert len(result.stage_c_ready_candidates) == 1
    assert len(result.proof_results) == 1
    assert not result.experiment_results
    assert len(result.verified_candidates) == 1
    assert result.verified_candidates[0].verification.labels == [VerificationLabel.LEAN_VERIFIED]
    assert result.verified_candidates[0].verification.evidence_artifacts

    commits = ledger.list_commits("run-1")
    action_types = [commit.action_type for commit in commits]
    assert ControllerActionType.STAGE_C_VERIFICATION_STARTED in action_types
    assert ControllerActionType.STAGE_C_PROOF_VALIDATED in action_types
    assert ControllerActionType.STAGE_C_VERIFICATION_DECIDED in action_types
    assert ControllerActionType.STAGE_C_VERIFICATION_REPORT_WRITTEN in action_types

    artifacts = [
        artifact for artifact_list in result.artifacts.values() for artifact in artifact_list
    ]
    artifacts.append(result.report_artifact)
    assert all(len(artifact.content_hash) == 64 for artifact in artifacts)
    assert all(artifact.producing_commit_hash for artifact in artifacts)
    assert (tmp_path / result.report_artifact.path).is_file()


def test_every_stage_c_decision_creates_a_ledger_commit(tmp_path) -> None:
    store, ledger = _run_stage_c_selection_pipeline(tmp_path)

    result = run_stage_c(run_id="run-1", store=store, ledger=ledger)
    decision_commits = [
        commit
        for commit in ledger.list_commits("run-1")
        if commit.action_type == ControllerActionType.STAGE_C_VERIFICATION_DECIDED
    ]

    assert len(decision_commits) == len(result.stage_c_ready_candidates)


def test_cli_run_stage_c_works_after_selection(tmp_path) -> None:
    runner = CliRunner()
    stage_a = runner.invoke(
        app,
        [
            "run-stage-a",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--domain",
            "human geography",
        ],
    )
    stage_b = runner.invoke(app, ["run-stage-b", "--root", str(tmp_path), "--run-id", "run-1"])
    select = runner.invoke(
        app,
        ["select-stage-c", "--root", str(tmp_path), "--run-id", "run-1"],
    )
    stage_c = runner.invoke(app, ["run-stage-c", "--root", str(tmp_path), "--run-id", "run-1"])

    assert stage_a.exit_code == 0
    assert stage_b.exit_code == 0
    assert select.exit_code == 0
    assert stage_c.exit_code == 0
    assert "stage_c_ready=1" in stage_c.output
    assert "fake_proof_runs=1" in stage_c.output
    assert "lean_verified=1" in stage_c.output
    assert "stage_c_report=runs/run-1/reports/stage-c-verification-report.md" in stage_c.output


def test_cli_run_stage_c_errors_without_selection(tmp_path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["run-stage-c", "--root", str(tmp_path), "--run-id", "run-1"])

    assert result.exit_code == 1
    assert "Stage C-ready candidates not found" in result.stderr


def _run_stage_c_selection_pipeline(tmp_path) -> tuple[ArtifactStore, ResearchLedger]:
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
    return store, ledger
