"""Deterministic fake Stage B structural validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.schemas import (
    ArtifactRef,
    BaselineReport,
    BridgeReport,
    Candidate,
    RedTeamReport,
    RetrievalRunReport,
    ReviewerPanelResult,
    ScoreVector,
)
from factori.stage_b_phases import (
    MAX_STAGE_B_SURVIVORS,
    apply_stage_b_gate_phase,
    expand_stage_b_children,
    load_stage_b_inputs,
    persist_stage_b_child_candidates,
    persist_stage_b_outputs,
    process_stage_b_children,
    run_stage_b_retrieval_phase,
    select_stage_b_survivors,
    summarize_stage_b_rejections,
)
from factori.stage_b_phases import (
    StageBError as StageBError,
)
from factori.stage_b_phases import (
    compute_stage_b_score as compute_stage_b_score,
)
from factori.stage_b_phases import (
    load_stage_a_survivors as load_stage_a_survivors,
)

if TYPE_CHECKING:
    from factori.adapters.base import RetrievalClient, ReviewerClient


@dataclass(frozen=True)
class StageBResult:
    """Summary of deterministic Stage B execution."""

    run_id: str
    stage_a_survivors: list[Candidate]
    children: list[Candidate]
    reviewer_panels: dict[str, ReviewerPanelResult]
    bridge_reports: dict[str, BridgeReport]
    baseline_reports: dict[str, BaselineReport]
    redteam_reports: dict[str, RedTeamReport]
    scores: dict[str, ScoreVector]
    rejected_bridge: list[Candidate]
    rejected_review: list[Candidate]
    rejected_baseline: list[Candidate]
    insufficient_retrieval: list[Candidate]
    gate_pruned: list[Candidate]
    survivors: list[Candidate]
    artifacts: dict[str, list[ArtifactRef]]
    retrieval_runs: dict[str, RetrievalRunReport]
    retrieval_artifacts: list[ArtifactRef]
    llm_reviewer_artifacts: list[ArtifactRef]
    reviewer_adapter_metadata: dict[str, object]
    report_artifact: ArtifactRef
    report_commit_hash: str


def run_stage_b(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    retrieval_client: RetrievalClient | None = None,
    reviewer_client: ReviewerClient | None = None,
) -> StageBResult:
    """Run deterministic Stage B structural validation."""
    inputs = load_stage_b_inputs(
        run_id=run_id,
        store=store,
        ledger=ledger,
        retrieval_client=retrieval_client,
        reviewer_client=reviewer_client,
    )
    retrieval = run_stage_b_retrieval_phase(
        run_id=run_id,
        stage_a_survivors=inputs.stage_a_survivors,
        store=store,
        ledger=ledger,
        retrieval_client=retrieval_client,
    )
    children = expand_stage_b_children(inputs.stage_a_survivors)
    artifacts = persist_stage_b_child_candidates(
        run_id=run_id,
        children=children,
        store=store,
        ledger=ledger,
    )
    processed = process_stage_b_children(
        run_id=run_id,
        children=children,
        artifacts=artifacts,
        retrieval_runs=retrieval.retrieval_runs,
        retrieval_certificates=retrieval.retrieval_certificates,
        store=store,
        ledger=ledger,
        reviewer_client=reviewer_client,
    )
    gate = apply_stage_b_gate_phase(
        run_id=run_id,
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
        run_id=run_id,
        passing=gate.passing,
        scores=processed.scores,
        ledger=ledger,
        max_survivors=MAX_STAGE_B_SURVIVORS,
    )
    rejection_buckets = summarize_stage_b_rejections(
        final_children=gate.final_children,
        reviewer_panels=processed.reviewer_panels,
        bridge_reports=processed.bridge_reports,
        baseline_reports=processed.baseline_reports,
        redteam_reports=processed.redteam_reports,
    )
    report = persist_stage_b_outputs(
        run_id=run_id,
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

    return StageBResult(
        run_id=run_id,
        stage_a_survivors=inputs.stage_a_survivors,
        children=gate.final_children,
        reviewer_panels=processed.reviewer_panels,
        bridge_reports=processed.bridge_reports,
        baseline_reports=processed.baseline_reports,
        redteam_reports=processed.redteam_reports,
        scores=processed.scores,
        rejected_bridge=rejection_buckets.rejected_bridge,
        rejected_review=rejection_buckets.rejected_review,
        rejected_baseline=rejection_buckets.rejected_baseline,
        insufficient_retrieval=rejection_buckets.insufficient_retrieval,
        gate_pruned=gate.gate_pruned,
        survivors=survivors,
        artifacts=processed.artifacts,
        retrieval_runs=retrieval.retrieval_runs,
        retrieval_artifacts=retrieval.retrieval_artifacts,
        llm_reviewer_artifacts=processed.llm_reviewer_artifacts,
        reviewer_adapter_metadata=inputs.reviewer_adapter_metadata,
        report_artifact=report.report_artifact,
        report_commit_hash=report.report_commit_hash,
    )
