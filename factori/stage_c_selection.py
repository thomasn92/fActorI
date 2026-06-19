"""Deterministic Stage B-to-C red-team filtering and Stage C candidate selection."""

from __future__ import annotations

from dataclasses import dataclass

from factori.artifacts import ArtifactStore
from factori.budget import select_stage_c_budget, stage_c_cost_aware_score
from factori.dedup import candidate_distance
from factori.ledger import ResearchLedger
from factori.retrieval import compute_retrieval_adequacy
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BaselineReport,
    BranchStatus,
    BridgeReport,
    BudgetSelectionReport,
    Candidate,
    ControllerActionType,
    DataRequirement,
    NoveltyAttackResult,
    RedTeamReport,
    RetrievalAdequacyCertificate,
    ReviewerPanelResult,
    ScoreVector,
    StageCRedTeamSelectionReport,
    UncertaintyEstimate,
)
from factori.uncertainty import estimate_score_uncertainty

RT_THRESHOLD = 0.75
NOVELTY_REJECTION_THRESHOLD = 0.45


class StageCSelectionError(RuntimeError):
    """Raised when Stage C selection prerequisites are missing."""


@dataclass(frozen=True)
class StageBSelectionContext:
    """Loaded Stage B survivor context."""

    candidate: Candidate
    reviewer_panel: ReviewerPanelResult
    bridge_report: BridgeReport
    baseline_report: BaselineReport
    redteam_report: RedTeamReport
    score: ScoreVector


@dataclass(frozen=True)
class StageCSelectionResult:
    """Result of deterministic Stage C candidate selection."""

    run_id: str
    stage_b_survivors: list[Candidate]
    redteam_reports: dict[str, StageCRedTeamSelectionReport]
    uncertainty_estimates: dict[str, UncertaintyEstimate]
    scores: dict[str, ScoreVector]
    selected_candidates: list[Candidate]
    rejected_redteam: list[Candidate]
    pruned_uncertain: list[Candidate]
    insufficient_retrieval: list[Candidate]
    deferred_data: list[Candidate]
    budget_deferred: list[Candidate]
    budget_report: BudgetSelectionReport
    artifacts: dict[str, list[ArtifactRef]]
    budget_artifact: ArtifactRef
    report_artifact: ArtifactRef
    report_commit_hash: str


def load_stage_b_selection_contexts(
    run_id: str,
    ledger: ResearchLedger,
) -> list[StageBSelectionContext]:
    """Load latest Stage B survivors and their Stage B validation reports."""
    commits = ledger.list_commits(run_id)
    survivor_commit = next(
        (
            commit
            for commit in reversed(commits)
            if commit.action_type == ControllerActionType.STAGE_B_SURVIVORS_SELECTED
        ),
        None,
    )
    if survivor_commit is None:
        raise StageCSelectionError("Stage B survivors not found; run factori run-stage-b first")

    survivor_ids = list(survivor_commit.payload.get("survivor_ids", []))
    candidates: dict[str, Candidate] = {}
    reviewers: dict[str, ReviewerPanelResult] = {}
    bridges: dict[str, BridgeReport] = {}
    baselines: dict[str, BaselineReport] = {}
    redteams: dict[str, RedTeamReport] = {}
    scores: dict[str, ScoreVector] = {}

    for commit in commits:
        candidate_id = commit.candidate_id
        if commit.action_type == ControllerActionType.STAGE_B_CHILD_GENERATED:
            payload = commit.payload.get("candidate")
            if payload is not None:
                candidate = Candidate.model_validate(payload)
                candidates[candidate.id] = candidate
        elif commit.action_type == ControllerActionType.STAGE_B_REVIEWERS_RUN and candidate_id:
            reviewers[candidate_id] = ReviewerPanelResult.model_validate(commit.payload)
        elif commit.action_type == ControllerActionType.STAGE_B_BRIDGE_CHECKED and candidate_id:
            bridges[candidate_id] = BridgeReport.model_validate(commit.payload)
        elif commit.action_type == ControllerActionType.STAGE_B_BASELINE_CHECKED and candidate_id:
            baselines[candidate_id] = BaselineReport.model_validate(commit.payload)
        elif commit.action_type == ControllerActionType.STAGE_B_REDTEAM_CHECKED and candidate_id:
            redteams[candidate_id] = RedTeamReport.model_validate(commit.payload)
        elif commit.action_type == ControllerActionType.STAGE_B_SCORE_COMPUTED and candidate_id:
            scores[candidate_id] = ScoreVector.model_validate(commit.payload["score"])

    contexts: list[StageBSelectionContext] = []
    missing: list[str] = []
    for candidate_id in survivor_ids:
        if not all(
            candidate_id in mapping
            for mapping in [candidates, reviewers, bridges, baselines, redteams, scores]
        ):
            missing.append(candidate_id)
            continue
        contexts.append(
            StageBSelectionContext(
                candidate=candidates[candidate_id],
                reviewer_panel=reviewers[candidate_id],
                bridge_report=bridges[candidate_id],
                baseline_report=baselines[candidate_id],
                redteam_report=redteams[candidate_id],
                score=scores[candidate_id],
            )
        )
    if missing:
        raise StageCSelectionError(
            "Stage B validation artifacts missing for survivors: " + ", ".join(missing)
        )
    return contexts


def novelty_attack(candidate: Candidate, siblings: list[Candidate]) -> NoveltyAttackResult:
    """Run deterministic fake novelty attack against sibling candidates."""
    other_siblings = [sibling for sibling in siblings if sibling.id != candidate.id]
    distances = [
        (sibling.id, candidate_distance(candidate, sibling))
        for sibling in other_siblings
    ]
    nearest = min(distances, key=lambda item: (item[1], item[0])) if distances else None
    if "known-prior" in candidate.id:
        rt_novelty = 0.25
        reason = "candidate id marks a known-prior collision"
    elif nearest is not None and nearest[1] <= 0.15:
        rt_novelty = 0.35
        reason = f"near duplicate of {nearest[0]} at distance {nearest[1]:.3f}"
    else:
        variant_bonus = 0.05 if candidate.variant_type == "theorem_or_conjecture_form" else 0.0
        method_bonus = 0.04 if candidate.method else 0.0
        rt_novelty = min(1.0, 0.78 + variant_bonus + method_bonus)
        reason = None
    novelty_risk = round(max(candidate.literature.novelty_risk, 1.0 - rt_novelty), 6)
    return NoveltyAttackResult(
        candidate_id=candidate.id,
        rt_novelty=round(rt_novelty, 6),
        novelty_risk=novelty_risk,
        near_duplicate_reason=reason,
        passed=rt_novelty >= NOVELTY_REJECTION_THRESHOLD,
    )


def aggregate_redteam_selection(
    *,
    candidate: Candidate,
    siblings: list[Candidate],
    bridge_report: BridgeReport,
    baseline_report: BaselineReport,
    redteam_report: RedTeamReport,
    retrieval_certificate: RetrievalAdequacyCertificate | None = None,
    rt_threshold: float = RT_THRESHOLD,
) -> StageCRedTeamSelectionReport:
    """Aggregate deterministic pre-Stage-C red-team components."""
    retrieval_certificate = retrieval_certificate or compute_retrieval_adequacy(
        candidate.literature
    )
    novelty = novelty_attack(candidate, siblings)
    rt_bridge = bridge_report.survival_score if bridge_report.survives else 0.0
    rt_baseline = (
        baseline_report.baseline_strength
        if baseline_report.baseline_valid and baseline_report.candidate_score_advantage > 0
        else 0.0
    )
    rt_triviality = (
        redteam_report.triviality_score
        if redteam_report.triviality_score is not None
        else 1.0
    )
    rt_retrieval = retrieval_certificate.rho_adequacy
    rt_total = round(
        0.25 * novelty.rt_novelty
        + 0.25 * rt_bridge
        + 0.20 * rt_baseline
        + 0.15 * rt_triviality
        + 0.15 * rt_retrieval,
        6,
    )
    redteam_passed = (
        novelty.passed
        and rt_total >= rt_threshold
        and not redteam_report.redteam_rejection
    )
    status = BranchStatus.ACTIVE if redteam_passed else BranchStatus.REJECTED_RED_TEAM
    if not retrieval_certificate.passed:
        status = BranchStatus.INSUFFICIENT_RETRIEVAL_ADEQUACY
    return StageCRedTeamSelectionReport(
        candidate_id=candidate.id,
        novelty=novelty,
        rt_bridge=round(rt_bridge, 6),
        rt_baseline=round(rt_baseline, 6),
        rt_triviality=round(rt_triviality, 6),
        rt_retrieval=round(rt_retrieval, 6),
        rt_total=rt_total,
        rt_threshold=rt_threshold,
        retrieval_certificate=retrieval_certificate,
        redteam_passed=redteam_passed,
        stage_c_ready=redteam_passed and retrieval_certificate.passed,
        status=status,
    )


def run_stage_c_selection(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    max_stage_c_candidates: int = 1,
) -> StageCSelectionResult:
    """Run deterministic Stage B-to-C filtering and candidate selection."""
    store.init_run(run_id)
    contexts = load_stage_b_selection_contexts(run_id, ledger)
    stage_b_survivors = [context.candidate for context in contexts]
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_SELECTION_STARTED,
        payload={"stage_b_survivor_ids": [candidate.id for candidate in stage_b_survivors]},
    )

    redteam_reports: dict[str, StageCRedTeamSelectionReport] = {}
    uncertainty_estimates: dict[str, UncertaintyEstimate] = {}
    scores: dict[str, ScoreVector] = {}
    artifacts: dict[str, list[ArtifactRef]] = {}
    rejected_redteam: list[Candidate] = []
    pruned_uncertain: list[Candidate] = []
    insufficient_retrieval: list[Candidate] = []
    deferred_data: list[Candidate] = []
    ready_candidates: list[Candidate] = []

    for context in contexts:
        candidate = context.candidate
        scores[candidate.id] = context.score
        artifacts[candidate.id] = []
        retrieval_certificate = compute_retrieval_adequacy(candidate.literature)
        redteam_report = aggregate_redteam_selection(
            candidate=candidate,
            siblings=stage_b_survivors,
            bridge_report=context.bridge_report,
            baseline_report=context.baseline_report,
            redteam_report=context.redteam_report,
            retrieval_certificate=retrieval_certificate,
        )
        redteam_reports[candidate.id] = redteam_report
        artifacts[candidate.id].append(
            _write_redteam_selection_artifact(run_id, redteam_report, store, ledger)
        )

        uncertainty = estimate_score_uncertainty(
            candidate_id=candidate.id,
            score=context.score,
            reviewer_panel=context.reviewer_panel,
            bridge_report=context.bridge_report,
            baseline_report=context.baseline_report,
            retrieval_certificate=retrieval_certificate,
        )
        uncertainty_estimates[candidate.id] = uncertainty
        artifacts[candidate.id].append(
            _write_uncertainty_artifact(run_id, uncertainty, store, ledger)
        )
        artifacts[candidate.id].append(
            _write_stage_c_score_artifact(
                run_id,
                candidate,
                context.score,
                redteam_report,
                uncertainty,
                store,
                ledger,
            )
        )

        decided = _decide_candidate_status(candidate, redteam_report, uncertainty)
        ledger.append_commit(
            run_id=run_id,
            candidate_id=candidate.id,
            parent_hash=ledger.latest_commit_hash(run_id),
            action_type=ControllerActionType.STAGE_C_SELECTION_DECIDED,
            payload={
                "candidate_id": candidate.id,
                "from_status": candidate.status.value,
                "to_status": decided.status.value,
                "rt_total": redteam_report.rt_total,
                "s_lower": uncertainty.s_lower,
                "retrieval_passed": redteam_report.retrieval_certificate.passed,
                "data_requirement": candidate.data_requirement.value,
            },
        )

        if decided.status == BranchStatus.STAGE_C_READY:
            ready_candidates.append(decided)
        elif decided.status == BranchStatus.REJECTED_RED_TEAM:
            rejected_redteam.append(decided)
        elif decided.status == BranchStatus.PRUNED_UNCERTAIN:
            pruned_uncertain.append(decided)
        elif decided.status == BranchStatus.INSUFFICIENT_RETRIEVAL_ADEQUACY:
            insufficient_retrieval.append(decided)
        elif decided.status in {
            BranchStatus.DEFERRED_REAL_DATA_CANDIDATE,
            BranchStatus.REQUIRES_REAL_DATA,
        }:
            deferred_data.append(decided)

    selected, budget_deferred, budget_report = select_stage_c_budget(
        ready_candidates,
        scores,
        max_stage_c_candidates=max_stage_c_candidates,
    )
    budget_deferred = [
        candidate.model_copy(update={"status": BranchStatus.BUDGET_DEFERRED})
        for candidate in budget_deferred
    ]
    for candidate in budget_deferred:
        ledger.append_commit(
            run_id=run_id,
            candidate_id=candidate.id,
            parent_hash=ledger.latest_commit_hash(run_id),
            action_type=ControllerActionType.STAGE_C_SELECTION_DECIDED,
            payload={
                "candidate_id": candidate.id,
                "from_status": BranchStatus.STAGE_C_READY.value,
                "to_status": BranchStatus.BUDGET_DEFERRED.value,
                "reason": "stage_c_budget_limit",
            },
        )

    budget_artifact = _write_budget_selection_artifact(run_id, budget_report, store)
    budget_commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_BUDGET_SELECTED,
        payload=budget_report.model_dump(mode="json"),
        artifact_refs=[budget_artifact],
    )
    budget_artifact = store.link_artifact_to_commit(budget_artifact, budget_commit.commit_hash)

    report_artifact, report_commit_hash = _write_stage_c_selection_report(
        run_id=run_id,
        stage_b_survivors=stage_b_survivors,
        selected_candidates=selected,
        rejected_redteam=rejected_redteam,
        pruned_uncertain=pruned_uncertain,
        insufficient_retrieval=insufficient_retrieval,
        deferred_data=deferred_data,
        budget_deferred=budget_deferred,
        redteam_reports=redteam_reports,
        uncertainty_estimates=uncertainty_estimates,
        scores=scores,
        store=store,
        ledger=ledger,
    )

    return StageCSelectionResult(
        run_id=run_id,
        stage_b_survivors=stage_b_survivors,
        redteam_reports=redteam_reports,
        uncertainty_estimates=uncertainty_estimates,
        scores=scores,
        selected_candidates=selected,
        rejected_redteam=rejected_redteam,
        pruned_uncertain=pruned_uncertain,
        insufficient_retrieval=insufficient_retrieval,
        deferred_data=deferred_data,
        budget_deferred=budget_deferred,
        budget_report=budget_report,
        artifacts=artifacts,
        budget_artifact=budget_artifact,
        report_artifact=report_artifact,
        report_commit_hash=report_commit_hash,
    )


def _decide_candidate_status(
    candidate: Candidate,
    redteam_report: StageCRedTeamSelectionReport,
    uncertainty: UncertaintyEstimate,
) -> Candidate:
    if candidate.data_requirement == DataRequirement.PUBLIC_DOWNLOAD:
        return candidate.model_copy(update={"status": BranchStatus.DEFERRED_REAL_DATA_CANDIDATE})
    if candidate.data_requirement == DataRequirement.USER_PROVIDED:
        return candidate.model_copy(update={"status": BranchStatus.REQUIRES_REAL_DATA})
    if redteam_report.status == BranchStatus.INSUFFICIENT_RETRIEVAL_ADEQUACY:
        return candidate.model_copy(update={"status": BranchStatus.INSUFFICIENT_RETRIEVAL_ADEQUACY})
    if not redteam_report.redteam_passed:
        return candidate.model_copy(update={"status": BranchStatus.REJECTED_RED_TEAM})
    if not uncertainty.passed:
        return candidate.model_copy(update={"status": BranchStatus.PRUNED_UNCERTAIN})
    return candidate.model_copy(update={"status": BranchStatus.STAGE_C_READY})


def _write_redteam_selection_artifact(
    run_id: str,
    report: StageCRedTeamSelectionReport,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"redteam-selection-{report.candidate_id}",
        artifact_type=ArtifactType.REPORT,
        data=report,
        metadata={"stage": "stage_c_selection", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=report.candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_REDTEAM_AGGREGATED,
        payload=report.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_uncertainty_artifact(
    run_id: str,
    estimate: UncertaintyEstimate,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"uncertainty-{estimate.candidate_id}",
        artifact_type=ArtifactType.SCORE,
        data=estimate,
        metadata={"stage": "stage_c_selection", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=estimate.candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_UNCERTAINTY_COMPUTED,
        payload=estimate.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_stage_c_score_artifact(
    run_id: str,
    candidate: Candidate,
    score: ScoreVector,
    redteam_report: StageCRedTeamSelectionReport,
    uncertainty: UncertaintyEstimate,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    payload = {
        "candidate_id": candidate.id,
        "score": score.model_dump(mode="json"),
        "base_score": round(score.base_score(), 6),
        "cost_aware_score": stage_c_cost_aware_score(candidate, score),
        "rt_total": redteam_report.rt_total,
        "s_lower": uncertainty.s_lower,
        "fake": True,
    }
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"stage-c-score-{candidate.id}",
        artifact_type=ArtifactType.SCORE,
        data=payload,
        metadata={"stage": "stage_c_selection", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=candidate.id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_SCORE_COMPUTED,
        payload=payload,
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_budget_selection_artifact(
    run_id: str,
    report: BudgetSelectionReport,
    store: ArtifactStore,
) -> ArtifactRef:
    return store.write_json(
        run_id=run_id,
        artifact_id="budget-selection",
        artifact_type=ArtifactType.REPORT,
        data=report,
        metadata={"stage": "stage_c_selection", "fake": True},
    )


def _write_stage_c_selection_report(
    *,
    run_id: str,
    stage_b_survivors: list[Candidate],
    selected_candidates: list[Candidate],
    rejected_redteam: list[Candidate],
    pruned_uncertain: list[Candidate],
    insufficient_retrieval: list[Candidate],
    deferred_data: list[Candidate],
    budget_deferred: list[Candidate],
    redteam_reports: dict[str, StageCRedTeamSelectionReport],
    uncertainty_estimates: dict[str, UncertaintyEstimate],
    scores: dict[str, ScoreVector],
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> tuple[ArtifactRef, str]:
    from factori.reports import render_stage_c_selection_report

    markdown = render_stage_c_selection_report(
        run_id=run_id,
        stage_b_survivors=stage_b_survivors,
        selected_candidates=selected_candidates,
        rejected_redteam=rejected_redteam,
        pruned_uncertain=pruned_uncertain,
        insufficient_retrieval=insufficient_retrieval,
        deferred_data=deferred_data,
        budget_deferred=budget_deferred,
        redteam_reports=redteam_reports,
        uncertainty_estimates=uncertainty_estimates,
        scores=scores,
    )
    artifact = store.write_markdown(
        run_id=run_id,
        artifact_id="stage-c-selection-report",
        artifact_type=ArtifactType.REPORT,
        markdown=markdown,
        metadata={"stage": "stage_c_selection", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_SELECTION_REPORT_WRITTEN,
        payload={
            "stage_b_survivors": len(stage_b_survivors),
            "stage_c_ready": len(selected_candidates),
            "rejected_redteam": len(rejected_redteam),
            "pruned_uncertain": len(pruned_uncertain),
            "insufficient_retrieval": len(insufficient_retrieval),
            "deferred_data": len(deferred_data),
            "budget_deferred": len(budget_deferred),
        },
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash), commit.commit_hash
