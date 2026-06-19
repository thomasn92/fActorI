from __future__ import annotations

from typer.testing import CliRunner

from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.ledger import ResearchLedger
from factori.schemas import BranchStatus, ConstraintSet, ControllerActionType
from factori.stage_a import run_stage_a
from factori.stage_b import (
    StageBError,
    expand_stage_b_children,
    load_stage_a_survivors,
    run_stage_b,
)


def test_stage_b_errors_clearly_if_stage_a_has_not_run(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    try:
        load_stage_a_survivors("run-1", ledger)
    except StageBError as exc:
        assert "Stage A survivors not found" in str(exc)
    else:
        raise AssertionError("expected StageBError")


def test_stage_b_loads_stage_a_survivors(tmp_path) -> None:
    store, ledger = _run_stage_a(tmp_path)

    result = run_stage_b(run_id="run-1", store=store, ledger=ledger)

    assert len(result.stage_a_survivors) == 4
    assert len(load_stage_a_survivors("run-1", ledger)) == 4


def test_child_variant_generation_is_deterministic_and_has_parent_ids(tmp_path) -> None:
    _, ledger = _run_stage_a(tmp_path)
    survivors = load_stage_a_survivors("run-1", ledger)

    first = expand_stage_b_children(survivors)
    second = expand_stage_b_children(survivors)

    assert first == second
    assert len(first) == 16
    assert all(child.parent_candidate_id for child in first)
    assert all(child.variant_type for child in first)


def test_stage_b_gate_keeps_only_candidates_satisfying_thresholds(tmp_path) -> None:
    store, ledger = _run_stage_a(tmp_path)

    result = run_stage_b(run_id="run-1", store=store, ledger=ledger)

    assert result.survivors
    for candidate in result.survivors:
        score = result.scores[candidate.id]
        assert score.novelty >= 0.45
        assert score.feasibility >= 0.75
        assert score.verifiability >= 0.70
        assert result.bridge_reports[candidate.id].survives
        assert result.baseline_reports[candidate.id].baseline_valid
        assert not result.redteam_reports[candidate.id].redteam_rejection
        assert candidate.status == BranchStatus.ACTIVE


def test_top_two_survivors_are_selected_deterministically(tmp_path) -> None:
    store, ledger = _run_stage_a(tmp_path)

    first = run_stage_b(run_id="run-1", store=store, ledger=ledger)
    survivor_ids = [candidate.id for candidate in first.survivors]

    assert len(first.survivors) == 2
    assert survivor_ids == sorted(
        survivor_ids,
        key=lambda candidate_id: (
            -first.scores[candidate_id].base_score(),
            candidate_id,
        ),
    )


def test_every_stage_b_decision_creates_ledger_commit(tmp_path) -> None:
    store, ledger = _run_stage_a(tmp_path)

    result = run_stage_b(run_id="run-1", store=store, ledger=ledger)
    commits = ledger.list_commits("run-1")
    action_types = [commit.action_type for commit in commits]

    for action_type in [
        ControllerActionType.STAGE_B_STARTED,
        ControllerActionType.STAGE_B_CHILD_GENERATED,
        ControllerActionType.STAGE_B_REVIEWERS_RUN,
        ControllerActionType.STAGE_B_DISAGREEMENT_RESOLVED,
        ControllerActionType.STAGE_B_BRIDGE_CHECKED,
        ControllerActionType.STAGE_B_BASELINE_CHECKED,
        ControllerActionType.STAGE_B_REDTEAM_CHECKED,
        ControllerActionType.STAGE_B_QUESTIONER_ROUTED,
        ControllerActionType.STAGE_B_SCORE_COMPUTED,
        ControllerActionType.STAGE_B_SURVIVORS_SELECTED,
        ControllerActionType.STAGE_B_REPORT_WRITTEN,
    ]:
        assert action_type in action_types
    child_commits = [
        commit
        for commit in commits
        if commit.action_type == ControllerActionType.STAGE_B_CHILD_GENERATED
    ]
    assert len(child_commits) == len(result.children)


def test_every_stage_b_artifact_has_a_hash_and_report_is_created(tmp_path) -> None:
    store, ledger = _run_stage_a(tmp_path)

    result = run_stage_b(run_id="run-1", store=store, ledger=ledger)
    artifacts = [
        artifact for artifact_list in result.artifacts.values() for artifact in artifact_list
    ]

    assert artifacts
    assert all(len(artifact.content_hash) == 64 for artifact in artifacts)
    assert len(result.report_artifact.content_hash) == 64
    assert (tmp_path / result.report_artifact.path).is_file()


def test_cli_run_stage_b_works_after_stage_a(tmp_path) -> None:
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
    stage_b = runner.invoke(
        app,
        ["run-stage-b", "--root", str(tmp_path), "--run-id", "run-1"],
    )

    assert stage_a.exit_code == 0
    assert stage_b.exit_code == 0
    assert "stage_a_survivors=4" in stage_b.output
    assert "stage_b_children=16" in stage_b.output
    assert "stage_b_report=runs/run-1/reports/stage-b-report.md" in stage_b.output


def test_cli_run_stage_b_errors_without_stage_a(tmp_path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["run-stage-b", "--root", str(tmp_path), "--run-id", "run-1"],
    )

    assert result.exit_code == 1
    assert "Stage A survivors not found" in result.stderr


def _run_stage_a(tmp_path) -> tuple[ArtifactStore, ResearchLedger]:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )
    return store, ledger
