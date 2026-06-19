from __future__ import annotations

from factori.abstract_synthesis import run_abstract_synthesis
from factori.artifacts import ArtifactStore
from factori.draft_skeleton import run_draft_skeleton_generation
from factori.ledger import ResearchLedger
from factori.manuscript_plan import run_manuscript_planning
from factori.run_summary import build_branch_outcomes, build_ledger_summary
from factori.schemas import BranchStatus, ConstraintSet, VerificationLabel
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_selection import run_stage_c_selection


def test_ledger_summary_is_deterministic(tmp_path) -> None:
    _, ledger = _run_pipeline_to_draft(tmp_path)

    first = build_ledger_summary("run-1", ledger)
    second = build_ledger_summary("run-1", ledger)

    assert first == second
    assert first.commit_count > 0
    assert first.root_commit_hash
    assert first.latest_commit_hash


def test_ledger_summary_does_not_mutate_ledger(tmp_path) -> None:
    _, ledger = _run_pipeline_to_draft(tmp_path)
    before = ledger.list_commits("run-1")

    build_ledger_summary("run-1", ledger)

    assert ledger.list_commits("run-1") == before


def test_branch_outcome_summary_is_deterministic(tmp_path) -> None:
    store, ledger = _run_pipeline_to_draft(tmp_path)

    first = build_branch_outcomes("run-1", ledger, store)
    second = build_branch_outcomes("run-1", ledger, store)

    assert first == second
    assert first


def test_deferred_real_data_branches_are_summarized(tmp_path) -> None:
    store, ledger = _run_pipeline_to_draft(tmp_path)

    outcomes = build_branch_outcomes("run-1", ledger, store)
    statuses = {outcome.status for outcome in outcomes}

    assert BranchStatus.DEFERRED_REAL_DATA_CANDIDATE in statuses
    assert BranchStatus.REQUIRES_REAL_DATA in statuses


def test_pruned_and_rejected_branches_are_summarized(tmp_path) -> None:
    store, ledger = _run_pipeline_to_draft(tmp_path)

    outcomes = build_branch_outcomes("run-1", ledger, store)
    status_values = {outcome.status for outcome in outcomes}

    assert (
        BranchStatus.PRUNED_DUPLICATE in status_values
        or BranchStatus.REJECTED_RED_TEAM in status_values
        or BranchStatus.PRUNED_UNCERTAIN in status_values
    )


def test_verification_labeled_branches_are_summarized(tmp_path) -> None:
    store, ledger = _run_pipeline_to_draft(tmp_path)

    outcomes = build_branch_outcomes("run-1", ledger, store)
    labels = {outcome.verification_label for outcome in outcomes}

    assert VerificationLabel.LEAN_VERIFIED in labels


def _run_pipeline_to_draft(tmp_path) -> tuple[ArtifactStore, ResearchLedger]:
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
    return store, ledger
