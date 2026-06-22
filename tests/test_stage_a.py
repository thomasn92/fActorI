from __future__ import annotations

import json

from typer.testing import CliRunner

from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.ledger import ResearchLedger
from factori.schemas import BranchStatus, ConstraintSet, ControllerActionType
from factori.scoring import passes_stage_a_gate
from factori.stage_a import run_stage_a


def test_stage_a_generates_candidates_and_ranked_report(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    result = run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )

    assert len(result.generated_candidates) == 15
    assert result.report_artifact.path.endswith("reports/stage-a-report.md")
    assert (tmp_path / result.report_artifact.path).is_file()
    assert len(result.survivors) <= 4


def test_public_and_user_data_candidates_are_deferred(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    result = run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography", method="optimal transport"),
        store=store,
        ledger=ledger,
    )

    deferred_statuses = {
        candidate.data_requirement: candidate.status for candidate in result.deferred_candidates
    }
    assert BranchStatus.DEFERRED_REAL_DATA_CANDIDATE in deferred_statuses.values()
    assert BranchStatus.REQUIRES_REAL_DATA in deferred_statuses.values()


def test_every_generated_candidate_has_a_ledger_commit(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    result = run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="machine learning", method="calibration"),
        store=store,
        ledger=ledger,
    )
    generated_commit_ids = {
        commit.candidate_id
        for commit in ledger.list_commits("run-1")
        if commit.action_type == ControllerActionType.STAGE_A_CANDIDATE_GENERATED
    }

    assert generated_commit_ids == {candidate.id for candidate in result.generated_candidates}


def test_every_score_artifact_has_a_hash(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    result = run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="machine learning", method="calibration"),
        store=store,
        ledger=ledger,
    )

    assert result.score_artifacts
    assert all(len(artifact.content_hash) == 64 for artifact in result.score_artifacts.values())


def test_stage_a_gate_keeps_only_valid_survivors(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    result = run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="robust finance"),
        store=store,
        ledger=ledger,
    )

    assert result.survivors
    assert all(passes_stage_a_gate(result.scores[candidate.id]) for candidate in result.survivors)
    assert all(candidate.status == BranchStatus.ACTIVE for candidate in result.survivors)


def test_cli_run_stage_a_blocks_accidental_rerun(tmp_path) -> None:
    runner = CliRunner()
    args = [
        "run-stage-a",
        "--root",
        str(tmp_path),
        "--run-id",
        "run-1",
        "--domain",
        "human geography",
    ]

    first = runner.invoke(app, args)
    first_ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    first_commits = first_ledger.list_commits("run-1")
    first_candidate_ids = _candidate_ids_from_ledger(first_commits)
    first_scores = _scores_from_ledger(first_commits)

    second = runner.invoke(app, args)
    second_ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    second_commits = second_ledger.list_commits("run-1")
    assert first.exit_code == 0
    assert second.exit_code == 1
    assert "generated_candidates=15" in first.output
    assert "BlockedAlreadyComplete" in second.output
    assert first_candidate_ids == _candidate_ids_from_ledger(second_commits)
    assert first_scores == _scores_from_ledger(second_commits)
    assert len(second_commits) == len(first_commits)


def _candidate_ids_from_ledger(commits) -> list[str]:
    return sorted(
        {
            commit.candidate_id
            for commit in commits
            if commit.action_type == ControllerActionType.STAGE_A_CANDIDATE_GENERATED
        }
    )


def _scores_from_ledger(commits) -> dict[str, dict[str, object]]:
    scores: dict[str, dict[str, object]] = {}
    for commit in commits:
        if commit.action_type != ControllerActionType.STAGE_A_SCORE_COMPUTED:
            continue
        payload = json.loads(json.dumps(commit.payload, sort_keys=True))
        scores[str(payload["candidate_id"])] = {
            "score": payload["score"],
            "base_score": payload["base_score"],
            "cost": payload["cost"],
            "cost_aware_score": payload["cost_aware_score"],
        }
    return scores
