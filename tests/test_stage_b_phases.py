from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from typer.testing import CliRunner

from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.ledger import ResearchLedger
from factori.rerun_policy import decide_stage_rerun
from factori.schemas import (
    BranchStatus,
    ConstraintSet,
    ControllerActionType,
    PipelineStage,
    RerunPolicy,
    StageRerunStatus,
)
from factori.stage_a import run_stage_a
from factori.stage_b import StageBResult, run_stage_b
from factori.stage_b_phases import (
    StageBInputBundle,
    StageBRetrievalPhaseResult,
    apply_stage_b_gate_phase,
    expand_stage_b_children,
    load_stage_b_inputs,
    persist_stage_b_child_candidates,
    persist_stage_b_outputs,
    process_stage_b_child,
    process_stage_b_children,
    run_stage_b_retrieval_phase,
    select_stage_b_survivors,
    summarize_stage_b_rejections,
)
from factori.status import inspect_run_status


@dataclass(frozen=True)
class PhaseStageBResult:
    """Small test-only snapshot of Stage B phase orchestration."""

    inputs: StageBInputBundle
    retrieval: StageBRetrievalPhaseResult
    children_ids: list[str]
    final_children_ids: list[str]
    survivor_ids: list[str]
    gate_pruned_ids: list[str]
    rejected_bridge_ids: list[str]
    rejected_review_ids: list[str]
    rejected_baseline_ids: list[str]
    insufficient_retrieval_ids: list[str]
    artifact_ids: list[str]
    action_types: list[ControllerActionType]
    report_path: str


def test_stage_b_phase_module_imports() -> None:
    assert StageBInputBundle.__name__ == "StageBInputBundle"
    assert StageBRetrievalPhaseResult.__name__ == "StageBRetrievalPhaseResult"


def test_input_and_retrieval_phases_preserve_fake_behavior(tmp_path: Path) -> None:
    store, ledger = _run_stage_a(tmp_path)

    inputs = load_stage_b_inputs(run_id="run-1", store=store, ledger=ledger)
    retrieval = run_stage_b_retrieval_phase(
        run_id="run-1",
        stage_a_survivors=inputs.stage_a_survivors,
        store=store,
        ledger=ledger,
    )

    assert len(inputs.stage_a_survivors) == 4
    assert inputs.retrieval_adapter_metadata == {
        "backend": "fake",
        "class": "candidate_literature_state",
        "fake": True,
        "external_calls_enabled": False,
    }
    assert retrieval.retrieval_runs == {}
    assert retrieval.retrieval_artifacts == []
    assert retrieval.retrieval_certificates == {}


def test_child_expansion_and_processing_phase_preserve_decisions(tmp_path: Path) -> None:
    store, ledger = _run_stage_a(tmp_path)
    inputs = load_stage_b_inputs(run_id="run-1", store=store, ledger=ledger)
    children = expand_stage_b_children(inputs.stage_a_survivors)
    child_artifacts = persist_stage_b_child_candidates(
        run_id="run-1",
        children=children,
        store=store,
        ledger=ledger,
    )

    first = process_stage_b_child(
        run_id="run-1",
        child=children[0],
        retrieval_runs={},
        retrieval_certificates={},
        store=store,
        ledger=ledger,
    )

    assert len(children) == 16
    assert children[0].parent_candidate_id == inputs.stage_a_survivors[0].id
    assert first.reviewer_panel.candidate_id == children[0].id
    assert first.bridge_report.candidate_id == children[0].id
    assert first.baseline_report.candidate_id == children[0].id
    assert first.redteam_report.candidate_id == children[0].id
    assert first.candidate.status in {
        BranchStatus.ACTIVE,
        BranchStatus.INSUFFICIENT_RETRIEVAL_ADEQUACY,
        BranchStatus.PRUNED_UNCERTAIN,
    }
    artifact_ids = [artifact.id for artifact in child_artifacts[children[0].id]]
    artifact_ids.extend(artifact.id for artifact in first.artifacts)
    assert artifact_ids == [
        children[0].id,
        f"reviewer-report-{children[0].id}",
        f"stage-b-score-{children[0].id}",
        f"bridge-report-{children[0].id}",
        f"baseline-report-{children[0].id}",
        f"redteam-report-{children[0].id}",
    ]


def test_phase_orchestration_matches_public_run_stage_b(tmp_path: Path) -> None:
    public_store, public_ledger = _run_stage_a(tmp_path / "public")
    phase_store, phase_ledger = _run_stage_a(tmp_path / "phase")

    public_result = run_stage_b(run_id="run-1", store=public_store, ledger=public_ledger)
    phase_result = _run_stage_b_via_phases(phase_store, phase_ledger)

    assert phase_result.children_ids == [candidate.id for candidate in public_result.children]
    assert phase_result.final_children_ids == [candidate.id for candidate in public_result.children]
    assert phase_result.survivor_ids == [candidate.id for candidate in public_result.survivors]
    assert phase_result.gate_pruned_ids == [candidate.id for candidate in public_result.gate_pruned]
    assert phase_result.rejected_bridge_ids == [
        candidate.id for candidate in public_result.rejected_bridge
    ]
    assert phase_result.rejected_review_ids == [
        candidate.id for candidate in public_result.rejected_review
    ]
    assert phase_result.rejected_baseline_ids == [
        candidate.id for candidate in public_result.rejected_baseline
    ]
    assert phase_result.insufficient_retrieval_ids == [
        candidate.id for candidate in public_result.insufficient_retrieval
    ]
    assert phase_result.report_path == public_result.report_artifact.path


def test_gate_selection_persistence_and_ledger_sequence_are_stable(tmp_path: Path) -> None:
    store, ledger = _run_stage_a(tmp_path)

    phase_result = _run_stage_b_via_phases(store, ledger)

    assert len(phase_result.survivor_ids) == 2
    assert phase_result.report_path == "runs/run-1/reports/stage-b-report.md"
    assert "stage-b-report" in phase_result.artifact_ids
    assert ControllerActionType.STAGE_B_STARTED in phase_result.action_types
    assert ControllerActionType.STAGE_B_CHILD_GENERATED in phase_result.action_types
    assert ControllerActionType.STAGE_B_REVIEWERS_RUN in phase_result.action_types
    assert ControllerActionType.STAGE_B_BRIDGE_CHECKED in phase_result.action_types
    assert ControllerActionType.STAGE_B_BASELINE_CHECKED in phase_result.action_types
    assert ControllerActionType.STAGE_B_REDTEAM_CHECKED in phase_result.action_types
    assert ControllerActionType.STAGE_B_SURVIVORS_SELECTED in phase_result.action_types
    assert ControllerActionType.STAGE_B_REPORT_WRITTEN in phase_result.action_types
    assert phase_result.action_types[-1] == ControllerActionType.STAGE_B_REPORT_WRITTEN


def test_public_stage_b_shape_cli_run_all_and_rerun_policy_still_work(tmp_path: Path) -> None:
    store, ledger = _run_stage_a(tmp_path)

    result = run_stage_b(run_id="run-1", store=store, ledger=ledger)
    status = inspect_run_status("run-1", root=tmp_path)
    rerun_decision = decide_stage_rerun(
        "run-1",
        PipelineStage.RUN_STAGE_B,
        RerunPolicy.FAIL_IF_EXISTS,
        status,
        root=tmp_path,
    )

    assert isinstance(result, StageBResult)
    assert rerun_decision.status == StageRerunStatus.BLOCKED_ALREADY_COMPLETE

    runner = CliRunner()
    full = runner.invoke(
        app,
        [
            "run-all",
            "--root",
            str(tmp_path / "run-all"),
            "--run-id",
            "run-2",
            "--domain",
            "human geography",
            "--stop-after",
            "run-stage-b",
        ],
    )
    assert full.exit_code == 0
    assert "pipeline_status=PipelineSucceeded" in full.output


def _run_stage_b_via_phases(
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PhaseStageBResult:
    inputs = load_stage_b_inputs(run_id="run-1", store=store, ledger=ledger)
    retrieval = run_stage_b_retrieval_phase(
        run_id="run-1",
        stage_a_survivors=inputs.stage_a_survivors,
        store=store,
        ledger=ledger,
    )
    children = expand_stage_b_children(inputs.stage_a_survivors)
    artifacts = persist_stage_b_child_candidates(
        run_id="run-1",
        children=children,
        store=store,
        ledger=ledger,
    )
    processed = process_stage_b_children(
        run_id="run-1",
        children=children,
        artifacts=artifacts,
        retrieval_runs=retrieval.retrieval_runs,
        retrieval_certificates=retrieval.retrieval_certificates,
        store=store,
        ledger=ledger,
    )
    gate = apply_stage_b_gate_phase(
        run_id="run-1",
        children=children,
        candidate_by_id=processed.candidate_by_id,
        reviewer_panels=processed.reviewer_panels,
        bridge_reports=processed.bridge_reports,
        baseline_reports=processed.baseline_reports,
        redteam_reports=processed.redteam_reports,
        scores=processed.scores,
        ledger=ledger,
    )
    survivors = select_stage_b_survivors(
        run_id="run-1",
        passing=gate.passing,
        scores=processed.scores,
        ledger=ledger,
    )
    rejection_buckets = summarize_stage_b_rejections(
        final_children=gate.final_children,
        reviewer_panels=processed.reviewer_panels,
        bridge_reports=processed.bridge_reports,
        baseline_reports=processed.baseline_reports,
        redteam_reports=processed.redteam_reports,
    )
    report = persist_stage_b_outputs(
        run_id="run-1",
        stage_a_survivors=inputs.stage_a_survivors,
        children=gate.final_children,
        bridge_reports=processed.bridge_reports,
        baseline_reports=processed.baseline_reports,
        redteam_reports=processed.redteam_reports,
        rejected_review=rejection_buckets.rejected_review,
        gate_pruned=gate.gate_pruned,
        survivors=survivors,
        scores=processed.scores,
        store=store,
        ledger=ledger,
        retrieval_adapter_metadata=inputs.retrieval_adapter_metadata,
        reviewer_adapter_metadata=inputs.reviewer_adapter_metadata,
    )
    artifact_ids = [
        artifact.id
        for artifact_list in processed.artifacts.values()
        for artifact in artifact_list
    ]
    artifact_ids.append(report.report_artifact.id)
    return PhaseStageBResult(
        inputs=inputs,
        retrieval=retrieval,
        children_ids=[candidate.id for candidate in children],
        final_children_ids=[candidate.id for candidate in gate.final_children],
        survivor_ids=[candidate.id for candidate in survivors],
        gate_pruned_ids=[candidate.id for candidate in gate.gate_pruned],
        rejected_bridge_ids=[candidate.id for candidate in rejection_buckets.rejected_bridge],
        rejected_review_ids=[candidate.id for candidate in rejection_buckets.rejected_review],
        rejected_baseline_ids=[candidate.id for candidate in rejection_buckets.rejected_baseline],
        insufficient_retrieval_ids=[
            candidate.id for candidate in rejection_buckets.insufficient_retrieval
        ],
        artifact_ids=artifact_ids,
        action_types=[commit.action_type for commit in ledger.list_commits("run-1")],
        report_path=report.report_artifact.path,
    )


def _run_stage_a(tmp_path: Path) -> tuple[ArtifactStore, ResearchLedger]:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )
    return store, ledger
