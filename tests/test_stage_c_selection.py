from __future__ import annotations

import pytest
from typer.testing import CliRunner

from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.ledger import ResearchLedger
from factori.schemas import (
    BranchStatus,
    ConstraintSet,
    ControllerActionType,
    DataRequirement,
    LiteratureState,
    UncertaintyEstimate,
)
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c_selection import (
    RT_THRESHOLD,
    StageCSelectionError,
    _decide_candidate_status,
    aggregate_redteam_selection,
    load_stage_b_selection_contexts,
    novelty_attack,
    run_stage_c_selection,
)


def test_stage_c_selection_errors_clearly_if_stage_b_has_not_run(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    with pytest.raises(StageCSelectionError, match="Stage B survivors not found"):
        load_stage_b_selection_contexts("run-1", ledger)


def test_stage_c_selection_errors_clearly_if_stage_b_artifacts_are_missing(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    ledger.append_commit(
        run_id="run-1",
        action_type=ControllerActionType.STAGE_B_SURVIVORS_SELECTED,
        payload={"survivor_ids": ["missing-candidate"]},
    )

    with pytest.raises(StageCSelectionError, match="Stage B validation artifacts missing"):
        load_stage_b_selection_contexts("run-1", ledger)


def test_stage_c_selection_loads_stage_b_survivors(tmp_path) -> None:
    _, ledger, _ = _run_stage_b_pipeline(tmp_path)

    contexts = load_stage_b_selection_contexts("run-1", ledger)

    assert len(contexts) == 2
    assert all(context.candidate.parent_candidate_id for context in contexts)


def test_redteam_aggregation_and_novelty_attack_are_deterministic(tmp_path) -> None:
    _, ledger, _ = _run_stage_b_pipeline(tmp_path)
    contexts = load_stage_b_selection_contexts("run-1", ledger)
    context = contexts[0]
    siblings = [loaded.candidate for loaded in contexts]

    first_novelty = novelty_attack(context.candidate, siblings)
    second_novelty = novelty_attack(context.candidate, siblings)
    first_report = aggregate_redteam_selection(
        candidate=context.candidate,
        siblings=siblings,
        bridge_report=context.bridge_report,
        baseline_report=context.baseline_report,
        redteam_report=context.redteam_report,
    )
    second_report = aggregate_redteam_selection(
        candidate=context.candidate,
        siblings=siblings,
        bridge_report=context.bridge_report,
        baseline_report=context.baseline_report,
        redteam_report=context.redteam_report,
    )

    assert first_novelty == second_novelty
    assert first_report == second_report
    assert 0.0 <= first_report.rt_total <= 1.0


def test_known_prior_novelty_attack_fails_badly(tmp_path) -> None:
    _, ledger, _ = _run_stage_b_pipeline(tmp_path)
    context = load_stage_b_selection_contexts("run-1", ledger)[0]
    candidate = context.candidate.model_copy(update={"id": "known-prior-candidate"})

    attack = novelty_attack(candidate, [])

    assert attack.rt_novelty < 0.45
    assert not attack.passed
    assert attack.near_duplicate_reason


def test_stage_c_threshold_and_status_decisions(tmp_path) -> None:
    _, ledger, _ = _run_stage_b_pipeline(tmp_path)
    contexts = load_stage_b_selection_contexts("run-1", ledger)
    context = contexts[0]
    siblings = [loaded.candidate for loaded in contexts]
    weak_bridge = context.bridge_report.model_copy(
        update={"survival_score": 0.20, "survives": False}
    )

    weak_report = aggregate_redteam_selection(
        candidate=context.candidate,
        siblings=siblings,
        bridge_report=weak_bridge,
        baseline_report=context.baseline_report,
        redteam_report=context.redteam_report,
    )
    low_retrieval_report = aggregate_redteam_selection(
        candidate=context.candidate.model_copy(update={"literature": _weak_literature()}),
        siblings=siblings,
        bridge_report=context.bridge_report,
        baseline_report=context.baseline_report,
        redteam_report=context.redteam_report,
    )
    ready_report = aggregate_redteam_selection(
        candidate=context.candidate,
        siblings=siblings,
        bridge_report=context.bridge_report,
        baseline_report=context.baseline_report,
        redteam_report=context.redteam_report,
    )
    uncertain = UncertaintyEstimate(
        candidate_id=context.candidate.id,
        s_hat=0.60,
        u_s=0.20,
        s_lower=0.40,
        tau_s=0.50,
        passed=False,
        components={"forced": 0.20},
    )

    assert weak_report.rt_total < RT_THRESHOLD
    assert weak_report.status == BranchStatus.REJECTED_RED_TEAM
    assert low_retrieval_report.status == BranchStatus.INSUFFICIENT_RETRIEVAL_ADEQUACY
    assert (
        _decide_candidate_status(context.candidate, ready_report, uncertain).status
        == BranchStatus.PRUNED_UNCERTAIN
    )
    assert (
        _decide_candidate_status(
            context.candidate.model_copy(
                update={"data_requirement": DataRequirement.PUBLIC_DOWNLOAD}
            ),
            ready_report,
            uncertain.model_copy(update={"passed": True, "s_lower": 0.55}),
        ).status
        == BranchStatus.DEFERRED_REAL_DATA_CANDIDATE
    )
    assert (
        _decide_candidate_status(
            context.candidate.model_copy(
                update={"data_requirement": DataRequirement.USER_PROVIDED}
            ),
            ready_report,
            uncertain.model_copy(update={"passed": True, "s_lower": 0.55}),
        ).status
        == BranchStatus.REQUIRES_REAL_DATA
    )


def test_full_stage_c_selection_applies_gates_writes_artifacts_and_ledgers(tmp_path) -> None:
    store, ledger, _ = _run_stage_b_pipeline(tmp_path)

    result = run_stage_c_selection(run_id="run-1", store=store, ledger=ledger)

    assert len(result.stage_b_survivors) == 2
    assert len(result.selected_candidates) <= 1
    assert result.selected_candidates
    assert all(
        candidate.data_requirement in {DataRequirement.NO_DATA, DataRequirement.SYNTHETIC_ONLY}
        for candidate in result.selected_candidates
    )
    for candidate in result.selected_candidates:
        redteam = result.redteam_reports[candidate.id]
        uncertainty = result.uncertainty_estimates[candidate.id]
        assert candidate.status == BranchStatus.STAGE_C_READY
        assert redteam.rt_total >= 0.75
        assert redteam.retrieval_certificate.passed
        assert uncertainty.s_lower >= uncertainty.tau_s

    commits = ledger.list_commits("run-1")
    action_types = [commit.action_type for commit in commits]
    for action_type in [
        ControllerActionType.STAGE_C_SELECTION_STARTED,
        ControllerActionType.STAGE_C_REDTEAM_AGGREGATED,
        ControllerActionType.STAGE_C_UNCERTAINTY_COMPUTED,
        ControllerActionType.STAGE_C_SCORE_COMPUTED,
        ControllerActionType.STAGE_C_SELECTION_DECIDED,
        ControllerActionType.STAGE_C_BUDGET_SELECTED,
        ControllerActionType.STAGE_C_SELECTION_REPORT_WRITTEN,
    ]:
        assert action_type in action_types
    decision_commits = [
        commit
        for commit in commits
        if commit.action_type == ControllerActionType.STAGE_C_SELECTION_DECIDED
    ]
    assert len(decision_commits) >= len(result.stage_b_survivors)

    artifacts = [
        artifact for artifact_list in result.artifacts.values() for artifact in artifact_list
    ]
    artifacts.extend([result.budget_artifact, result.report_artifact])
    assert all(len(artifact.content_hash) == 64 for artifact in artifacts)
    assert all(artifact.producing_commit_hash for artifact in artifacts)
    assert (tmp_path / result.report_artifact.path).is_file()


def test_cli_select_stage_c_works_after_stage_a_and_b(tmp_path) -> None:
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
    stage_c = runner.invoke(
        app,
        ["select-stage-c", "--root", str(tmp_path), "--run-id", "run-1"],
    )

    assert stage_a.exit_code == 0
    assert stage_b.exit_code == 0
    assert stage_c.exit_code == 0
    assert "stage_b_survivors=2" in stage_c.output
    assert "stage_c_ready=1" in stage_c.output
    assert (
        "stage_c_selection_report=runs/run-1/reports/stage-c-selection-report.md"
        in stage_c.output
    )


def test_cli_select_stage_c_errors_without_stage_b(tmp_path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["select-stage-c", "--root", str(tmp_path), "--run-id", "run-1"],
    )

    assert result.exit_code == 1
    assert "Stage B survivors not found" in result.stderr


def _run_stage_b_pipeline(tmp_path) -> tuple[ArtifactStore, ResearchLedger, object]:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )
    stage_b_result = run_stage_b(run_id="run-1", store=store, ledger=ledger)
    return store, ledger, stage_b_result


def _weak_literature() -> LiteratureState:
    return LiteratureState(
        semantic=0.20,
        keyword=0.20,
        citation=0.20,
        diversity=0.20,
        adversarial=0.20,
        novelty_risk=0.80,
    )
