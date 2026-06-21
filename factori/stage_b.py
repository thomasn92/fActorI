"""Deterministic fake Stage B structural validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from factori.artifacts import ArtifactStore
from factori.baselines import evaluate_baseline
from factori.bridge import run_bridge_check
from factori.ledger import ResearchLedger
from factori.questioner import route_questions_to_action, select_questions
from factori.redteam import run_redteam_checks
from factori.reports import render_stage_b_report
from factori.retrieval import run_retrieval_with_provenance
from factori.reviewers import run_reviewer_panel
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BaselineReport,
    BranchStatus,
    BridgeReport,
    Candidate,
    ControllerActionType,
    DataRequirement,
    LiteratureState,
    RedTeamReport,
    RetrievalRunReport,
    ReviewerDisagreementType,
    ReviewerPanelResult,
    ScoreVector,
    StagnationEvent,
    VerificationLabel,
    VerificationState,
)
from factori.scoring import cost_aware_score, score_candidate, score_payload
from factori.stagnation import compute_stagnation

if TYPE_CHECKING:
    from factori.adapters.base import RetrievalClient

MAX_STAGE_B_SURVIVORS = 2


class StageBError(RuntimeError):
    """Raised when Stage B prerequisites are missing."""


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
    report_artifact: ArtifactRef
    report_commit_hash: str


def load_stage_a_survivors(run_id: str, ledger: ResearchLedger) -> list[Candidate]:
    """Load latest Stage A survivors from ledger commits."""
    commits = ledger.list_commits(run_id)
    survivor_commit = next(
        (
            commit
            for commit in reversed(commits)
            if commit.action_type == ControllerActionType.STAGE_A_SURVIVORS_SELECTED
        ),
        None,
    )
    if survivor_commit is None:
        raise StageBError("Stage A survivors not found; run factori run-stage-a first")

    survivor_ids = list(survivor_commit.payload.get("survivor_ids", []))
    candidates: dict[str, Candidate] = {}
    for commit in commits:
        if commit.action_type != ControllerActionType.STAGE_A_CANDIDATE_GENERATED:
            continue
        payload = commit.payload.get("candidate")
        if payload is None:
            continue
        candidate = Candidate.model_validate(payload)
        candidates[candidate.id] = candidate

    missing = [candidate_id for candidate_id in survivor_ids if candidate_id not in candidates]
    if missing:
        raise StageBError(f"Stage A survivor candidate payloads missing: {', '.join(missing)}")
    return [candidates[candidate_id] for candidate_id in survivor_ids]


def expand_stage_b_children(stage_a_survivors: list[Candidate]) -> list[Candidate]:
    """Expand Stage A survivors into deterministic localized child variants."""
    children: list[Candidate] = []
    for parent in stage_a_survivors:
        children.extend(
            [
                _child(parent, "narrow_scope"),
                _child(parent, "stronger_baseline"),
                _child(parent, "synthetic_experiment_contract"),
                _child(parent, "theorem_or_conjecture_form"),
            ]
        )
    return children


def run_stage_b(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    retrieval_client: RetrievalClient | None = None,
) -> StageBResult:
    """Run deterministic Stage B structural validation."""
    store.init_run(run_id)
    stage_a_survivors = load_stage_a_survivors(run_id, ledger)
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_STARTED,
        payload={
            "stage_a_survivor_ids": [candidate.id for candidate in stage_a_survivors],
            "retrieval_adapter": _retrieval_adapter_metadata(retrieval_client),
        },
    )

    retrieval_runs: dict[str, RetrievalRunReport] = {}
    retrieval_artifacts: list[ArtifactRef] = []
    retrieval_certificates = {}
    if retrieval_client is not None and not retrieval_client.is_fake:
        limit = int(getattr(retrieval_client, "default_limit", 5))
        for parent in stage_a_survivors:
            retrieval_execution = run_retrieval_with_provenance(
                run_id=run_id,
                query=_retrieval_query_for_candidate(parent),
                limit=limit,
                retrieval_client=retrieval_client,
                store=store,
                ledger=ledger,
            )
            retrieval_runs[parent.id] = retrieval_execution.report
            retrieval_artifacts.extend(retrieval_execution.artifacts.values())
            retrieval_certificates[parent.id] = retrieval_execution.report.certificate

    children = expand_stage_b_children(stage_a_survivors)
    artifacts: dict[str, list[ArtifactRef]] = {}
    for child in children:
        artifacts[child.id] = [_write_child_candidate(run_id, child, store, ledger)]

    reviewer_panels: dict[str, ReviewerPanelResult] = {}
    bridge_reports: dict[str, BridgeReport] = {}
    baseline_reports: dict[str, BaselineReport] = {}
    redteam_reports: dict[str, RedTeamReport] = {}
    scores: dict[str, ScoreVector] = {}
    candidate_by_id = {child.id: child for child in children}

    for child in children:
        panel = run_reviewer_panel(child)
        reviewer_panels[child.id] = panel
        artifacts[child.id].append(_write_reviewer_artifact(run_id, panel, store, ledger))
        _commit_disagreement(run_id, panel, ledger)
        if panel.disagreement_type == ReviewerDisagreementType.FATAL_CONFUSION:
            candidate_by_id[child.id] = child.model_copy(
                update={"status": BranchStatus.REJECTED_RED_TEAM}
            )

        score = compute_stage_b_score(child, panel)
        scores[child.id] = score
        artifacts[child.id].append(_write_score_artifact(run_id, child, score, store, ledger))

        bridge = run_bridge_check(child)
        bridge_reports[child.id] = bridge
        artifacts[child.id].append(_write_bridge_artifact(run_id, bridge, store, ledger))
        if bridge.repair_attempted:
            _commit_bridge_repair(run_id, bridge, ledger)
        if not bridge.survives:
            candidate_by_id[child.id] = candidate_by_id[child.id].model_copy(
                update={"status": bridge.final_status}
            )

        baseline = evaluate_baseline(child, score)
        baseline_reports[child.id] = baseline
        artifacts[child.id].append(_write_baseline_artifact(run_id, baseline, store, ledger))

        redteam = run_redteam_checks(
            child,
            score,
            retrieval_certificate=retrieval_certificates.get(child.parent_candidate_id or ""),
        )
        redteam_reports[child.id] = redteam
        artifacts[child.id].append(_write_redteam_artifact(run_id, redteam, store, ledger))
        if redteam.status != BranchStatus.ACTIVE:
            candidate_by_id[child.id] = candidate_by_id[child.id].model_copy(
                update={
                    "status": redteam.status,
                    "verification": VerificationState(labels=[VerificationLabel.CONJECTURE])
                    if redteam.status == BranchStatus.TRIVIAL_THEOREM_CANDIDATE
                    else candidate_by_id[child.id].verification,
                }
            )

        questions = select_questions(
            "stage_b",
            candidate_by_id[child.id],
            score,
            child.literature,
            candidate_by_id[child.id].verification,
            triggers=_question_triggers(candidate_by_id[child.id], panel, baseline, redteam),
        )
        routed = route_questions_to_action(
            questions,
            candidate_by_id[child.id],
            score,
            child.literature,
            candidate_by_id[child.id].verification,
        )
        ledger.append_commit(
            run_id=run_id,
            candidate_id=child.id,
            parent_hash=ledger.latest_commit_hash(run_id),
            action_type=ControllerActionType.STAGE_B_QUESTIONER_ROUTED,
            payload=routed.model_dump(mode="json"),
        )

    gate_pruned: list[Candidate] = []
    passing: list[Candidate] = []
    for child in children:
        current = candidate_by_id[child.id]
        stagnation_state = _stage_b_stagnation_state(current, scores[child.id])
        if _passes_stage_b_gate(
            current,
            scores[child.id],
            reviewer_panels[child.id],
            bridge_reports[child.id],
            baseline_reports[child.id],
            redteam_reports[child.id],
            stagnation_stop=stagnation_state.stagnant,
        ):
            passing.append(current)
            continue
        pruned = _stage_b_pruned_candidate(
            current,
            reviewer_panels[child.id],
            bridge_reports[child.id],
            baseline_reports[child.id],
            redteam_reports[child.id],
            stagnation_stop=stagnation_state.stagnant,
        )
        candidate_by_id[child.id] = pruned
        gate_pruned.append(pruned)
        ledger.append_commit(
            run_id=run_id,
            candidate_id=child.id,
            parent_hash=ledger.latest_commit_hash(run_id),
            action_type=ControllerActionType.STAGE_B_GATE_PRUNED,
            payload={
                "candidate_id": child.id,
                "from_status": current.status.value,
                "to_status": pruned.status.value,
                "score": scores[child.id].model_dump(mode="json"),
                "stage_b_gate": _gate_payload(
                    reviewer_panels[child.id],
                    bridge_reports[child.id],
                    baseline_reports[child.id],
                    redteam_reports[child.id],
                    stagnation_state.stagnant,
                ),
            },
        )

    survivors = _rank_stage_b_survivors(passing, scores)[:MAX_STAGE_B_SURVIVORS]
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_SURVIVORS_SELECTED,
        payload={
            "survivor_ids": [candidate.id for candidate in survivors],
            "passing_candidate_ids": [candidate.id for candidate in passing],
            "max_survivors": MAX_STAGE_B_SURVIVORS,
        },
    )

    final_children = list(candidate_by_id.values())
    rejected_bridge = [
        candidate for candidate in final_children if not bridge_reports[candidate.id].survives
    ]
    rejected_review = [
        candidate
        for candidate in final_children
        if reviewer_panels[candidate.id].disagreement_type
        == ReviewerDisagreementType.FATAL_CONFUSION
    ]
    rejected_baseline = [
        candidate
        for candidate in final_children
        if not baseline_reports[candidate.id].baseline_valid
    ]
    insufficient_retrieval = [
        candidate
        for candidate in final_children
        if redteam_reports[candidate.id].status
        == BranchStatus.INSUFFICIENT_RETRIEVAL_ADEQUACY
    ]
    report_artifact, report_commit_hash = _write_stage_b_report(
        run_id=run_id,
        stage_a_survivors=stage_a_survivors,
        children=final_children,
        bridge_reports=bridge_reports,
        baseline_reports=baseline_reports,
        redteam_reports=redteam_reports,
        rejected_review=rejected_review,
        gate_pruned=gate_pruned,
        survivors=survivors,
        scores=scores,
        store=store,
        ledger=ledger,
        retrieval_adapter_metadata=_retrieval_adapter_metadata(retrieval_client),
    )

    return StageBResult(
        run_id=run_id,
        stage_a_survivors=stage_a_survivors,
        children=final_children,
        reviewer_panels=reviewer_panels,
        bridge_reports=bridge_reports,
        baseline_reports=baseline_reports,
        redteam_reports=redteam_reports,
        scores=scores,
        rejected_bridge=rejected_bridge,
        rejected_review=rejected_review,
        rejected_baseline=rejected_baseline,
        insufficient_retrieval=insufficient_retrieval,
        gate_pruned=gate_pruned,
        survivors=survivors,
        artifacts=artifacts,
        retrieval_runs=retrieval_runs,
        retrieval_artifacts=retrieval_artifacts,
        report_artifact=report_artifact,
        report_commit_hash=report_commit_hash,
    )


def compute_stage_b_score(candidate: Candidate, panel: ReviewerPanelResult) -> ScoreVector:
    """Compute deterministic fake Stage B score."""
    base = score_candidate(candidate)
    reviewer_score = panel.resolved_aggregate_score
    variant_type = candidate.variant_type or ""
    novelty_bonus = 0.03 if variant_type == "theorem_or_conjecture_form" else 0.01
    feasibility_bonus = 0.08 if variant_type in {"narrow_scope", "stronger_baseline"} else 0.04
    verifiability_bonus = (
        0.10
        if variant_type in {"synthetic_experiment_contract", "theorem_or_conjecture_form"}
        else 0.05
    )
    return ScoreVector(
        novelty=_clamp(base.novelty + novelty_bonus),
        feasibility=_clamp(base.feasibility + feasibility_bonus),
        verifiability=_clamp(base.verifiability + verifiability_bonus),
        reviewer=_clamp(reviewer_score),
        difficulty=_clamp(base.difficulty - 0.04),
        diversity=base.diversity,
        uncertainty=0.12
        if panel.disagreement_type == ReviewerDisagreementType.NOVEL_CONTROVERSY
        else 0.08,
    )


def _child(parent: Candidate, variant_type: str) -> Candidate:
    candidate_id = f"{parent.id}-b-{variant_type.replace('_', '-')}"
    question_suffix = {
        "narrow_scope": "under a narrower falsifiable scope",
        "stronger_baseline": "against a stronger deterministic baseline",
        "synthetic_experiment_contract": "with a synthetic experiment contract",
        "theorem_or_conjecture_form": "as a theorem or conjecture form",
    }[variant_type]
    data_requirement = (
        parent.data_requirement
        if variant_type != "synthetic_experiment_contract"
        else DataRequirement.SYNTHETIC_ONLY
    )
    theory = parent.theory
    if variant_type == "theorem_or_conjecture_form":
        theory = f"Theorem-style formulation: {parent.theory or parent.question}"
    constraints = parent.constraints.model_copy(
        update={
            "question": f"{parent.question} {question_suffix}",
            "theory": theory,
            "experiment": "Deterministic synthetic contract"
            if variant_type == "synthetic_experiment_contract"
            else parent.experiment,
            "data_requirement": data_requirement,
        }
    )
    return parent.model_copy(
        update={
            "id": candidate_id,
            "parent_candidate_id": parent.id,
            "variant_type": variant_type,
            "constraints": constraints,
            "question": f"{parent.question} {question_suffix}",
            "theory": theory,
            "experiment": "Deterministic synthetic contract"
            if variant_type == "synthetic_experiment_contract"
            else parent.experiment,
            "data_requirement": data_requirement,
            "literature": _child_literature(parent, variant_type),
            "symbolic_state": {
                **parent.symbolic_state,
                "parent_candidate_id": parent.id,
                "variant_type": variant_type,
                "stage": "stage_b",
                "fake": True,
            },
            "status": BranchStatus.ACTIVE,
        }
    )


def _child_literature(parent: Candidate, variant_type: str) -> LiteratureState:
    if variant_type in {"narrow_scope", "stronger_baseline", "theorem_or_conjecture_form"}:
        return LiteratureState(
            k=max(parent.literature.k, 20),
            semantic=0.84,
            keyword=0.82,
            citation=0.81,
            diversity=0.80,
            adversarial=0.80,
            novelty_risk=0.16,
            closest_priors=[*parent.literature.closest_priors, "fake-stage-b-prior"],
        )
    return LiteratureState(
        k=max(parent.literature.k, 10),
        semantic=0.76,
        keyword=0.76,
        citation=0.74,
        diversity=0.73,
        adversarial=0.72,
        novelty_risk=0.28,
        closest_priors=parent.literature.closest_priors,
    )


def _passes_stage_b_gate(
    candidate: Candidate,
    score: ScoreVector,
    panel: ReviewerPanelResult,
    bridge: BridgeReport,
    baseline: BaselineReport,
    redteam: RedTeamReport,
    *,
    stagnation_stop: bool,
) -> bool:
    return (
        candidate.status == BranchStatus.ACTIVE
        and score.novelty >= 0.45
        and score.feasibility >= 0.75
        and score.verifiability >= 0.70
        and bridge.survives
        and baseline.baseline_valid
        and not redteam.redteam_rejection
        and panel.disagreement_type != ReviewerDisagreementType.FATAL_CONFUSION
        and not stagnation_stop
        and redteam.stage_c_ready
    )


def _stage_b_pruned_candidate(
    candidate: Candidate,
    panel: ReviewerPanelResult,
    bridge: BridgeReport,
    baseline: BaselineReport,
    redteam: RedTeamReport,
    *,
    stagnation_stop: bool,
) -> Candidate:
    if panel.disagreement_type == ReviewerDisagreementType.FATAL_CONFUSION:
        return candidate.model_copy(update={"status": BranchStatus.REJECTED_RED_TEAM})
    if not bridge.survives:
        return candidate.model_copy(update={"status": bridge.final_status})
    if redteam.status != BranchStatus.ACTIVE:
        return candidate.model_copy(update={"status": redteam.status})
    if stagnation_stop:
        return candidate.model_copy(update={"status": BranchStatus.STAGNATION_STOP})
    if not baseline.baseline_valid:
        return candidate.model_copy(update={"status": BranchStatus.PRUNED_UNCERTAIN})
    return candidate.model_copy(update={"status": BranchStatus.PRUNED_UNCERTAIN})


def _question_triggers(
    candidate: Candidate,
    panel: ReviewerPanelResult,
    baseline: BaselineReport,
    redteam: RedTeamReport,
) -> set[str]:
    triggers: set[str] = set()
    if not baseline.baseline_valid:
        triggers.add("weak_baseline")
    if not redteam.retrieval_certificate.passed:
        triggers.add("low_retrieval_adequacy")
    if candidate.variant_type == "narrow_scope":
        triggers.add("recent_repair")
    if panel.disagreement_type == ReviewerDisagreementType.AMBIGUOUS_CLAIM:
        triggers.add("high_complexity")
    if redteam.triviality_score is not None and not redteam.triviality_passed:
        triggers.add("verification_missing")
    return triggers


def _stage_b_stagnation_state(candidate: Candidate, score: ScoreVector):
    stagnant = "stagnant" in candidate.id
    history = [
        StagnationEvent(action="StageA", score=max(0.0, score.base_score() - 0.002)),
        StagnationEvent(action="Review", score=score.base_score()),
        StagnationEvent(action="Bridge", score=score.base_score() + (0.0 if stagnant else 0.03)),
    ]
    return compute_stagnation(history, epsilon_score=0.01, window=3, n_stag=2)


def _rank_stage_b_survivors(
    candidates: list[Candidate],
    scores: dict[str, ScoreVector],
) -> list[Candidate]:
    return sorted(
        candidates,
        key=lambda candidate: (-cost_aware_score(candidate, scores[candidate.id]), candidate.id),
    )


def _write_child_candidate(
    run_id: str,
    candidate: Candidate,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=candidate.id,
        artifact_type=ArtifactType.CANDIDATE,
        data=candidate,
        metadata={"stage": "stage_b", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=candidate.id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_CHILD_GENERATED,
        payload={
            "candidate": candidate.model_dump(mode="json"),
            "parent_candidate_id": candidate.parent_candidate_id,
            "variant_type": candidate.variant_type,
        },
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_reviewer_artifact(
    run_id: str,
    panel: ReviewerPanelResult,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"reviewer-report-{panel.candidate_id}",
        artifact_type=ArtifactType.REPORT,
        data=panel,
        metadata={"stage": "stage_b", "report": "reviewers", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=panel.candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_REVIEWERS_RUN,
        payload=panel.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _commit_disagreement(
    run_id: str,
    panel: ReviewerPanelResult,
    ledger: ResearchLedger,
) -> None:
    ledger.append_commit(
        run_id=run_id,
        candidate_id=panel.candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_DISAGREEMENT_RESOLVED,
        payload={
            "candidate_id": panel.candidate_id,
            "disagreement": panel.disagreement,
            "disagreement_type": panel.disagreement_type.value,
            "preserved": panel.preserved,
            "rejected": panel.rejected,
            "excluded_reviewer_id": panel.excluded_reviewer_id,
        },
    )


def _write_score_artifact(
    run_id: str,
    candidate: Candidate,
    score: ScoreVector,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    payload = score_payload(candidate, score)
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"stage-b-score-{candidate.id}",
        artifact_type=ArtifactType.SCORE,
        data=payload,
        metadata={"stage": "stage_b", "candidate_id": candidate.id, "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=candidate.id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_SCORE_COMPUTED,
        payload=payload,
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_bridge_artifact(
    run_id: str,
    bridge: BridgeReport,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"bridge-report-{bridge.candidate_id}",
        artifact_type=ArtifactType.REPORT,
        data=bridge,
        metadata={"stage": "stage_b", "report": "bridge", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=bridge.candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_BRIDGE_CHECKED,
        payload=bridge.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _commit_bridge_repair(run_id: str, bridge: BridgeReport, ledger: ResearchLedger) -> None:
    ledger.append_commit(
        run_id=run_id,
        candidate_id=bridge.candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_BRIDGE_REPAIRED,
        payload={
            "candidate_id": bridge.candidate_id,
            "repair_action": bridge.repair_action.value if bridge.repair_action else None,
            "survival_score": bridge.survival_score,
            "survives": bridge.survives,
            "final_status": bridge.final_status.value,
        },
    )


def _write_baseline_artifact(
    run_id: str,
    baseline: BaselineReport,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"baseline-report-{baseline.candidate_id}",
        artifact_type=ArtifactType.REPORT,
        data=baseline,
        metadata={"stage": "stage_b", "report": "baseline", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=baseline.candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_BASELINE_CHECKED,
        payload=baseline.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_redteam_artifact(
    run_id: str,
    redteam: RedTeamReport,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"redteam-report-{redteam.candidate_id}",
        artifact_type=ArtifactType.REPORT,
        data=redteam,
        metadata={"stage": "stage_b", "report": "redteam", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=redteam.candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_REDTEAM_CHECKED,
        payload=redteam.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_stage_b_report(
    *,
    run_id: str,
    stage_a_survivors: list[Candidate],
    children: list[Candidate],
    bridge_reports: dict[str, BridgeReport],
    baseline_reports: dict[str, BaselineReport],
    redteam_reports: dict[str, RedTeamReport],
    rejected_review: list[Candidate],
    gate_pruned: list[Candidate],
    survivors: list[Candidate],
    scores: dict[str, ScoreVector],
    store: ArtifactStore,
    ledger: ResearchLedger,
    retrieval_adapter_metadata: dict[str, object],
) -> tuple[ArtifactRef, str]:
    markdown = render_stage_b_report(
        run_id=run_id,
        stage_a_survivors=stage_a_survivors,
        children=children,
        bridge_reports=bridge_reports,
        baseline_reports=baseline_reports,
        redteam_reports=redteam_reports,
        rejected_review=rejected_review,
        gate_pruned=gate_pruned,
        survivors=survivors,
        scores=scores,
        retrieval_adapter_metadata=retrieval_adapter_metadata,
    )
    artifact = store.write_markdown(
        run_id=run_id,
        artifact_id="stage-b-report",
        artifact_type=ArtifactType.REPORT,
        markdown=markdown,
        metadata={
            "stage": "stage_b",
            "fake": True,
            "retrieval_adapter": retrieval_adapter_metadata,
            "is_verification_evidence": False,
        },
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_REPORT_WRITTEN,
        payload={
            "stage_a_survivors": len(stage_a_survivors),
            "stage_b_children": len(children),
            "survivor_ids": [candidate.id for candidate in survivors],
            "retrieval_adapter": retrieval_adapter_metadata,
        },
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash), commit.commit_hash


def _retrieval_query_for_candidate(candidate: Candidate) -> str:
    return " ".join(
        part
        for part in [candidate.domain, candidate.method, candidate.question]
        if part
    )


def _retrieval_adapter_metadata(
    retrieval_client: RetrievalClient | None,
) -> dict[str, object]:
    if retrieval_client is None:
        return {
            "backend": "fake",
            "class": "candidate_literature_state",
            "fake": True,
            "external_calls_enabled": False,
        }
    return {
        "backend": retrieval_client.backend_name,
        "class": type(retrieval_client).__name__,
        "provider": getattr(retrieval_client, "provider", retrieval_client.backend_name),
        "fake": retrieval_client.is_fake,
        "external_calls_enabled": retrieval_client.external_calls_enabled,
    }


def _gate_payload(
    panel: ReviewerPanelResult,
    bridge: BridgeReport,
    baseline: BaselineReport,
    redteam: RedTeamReport,
    stagnation_stop: bool,
) -> dict[str, object]:
    return {
        "reviewer_disagreement_type": panel.disagreement_type.value,
        "bridge_survives": bridge.survives,
        "baseline_valid": baseline.baseline_valid,
        "redteam_rejection": redteam.redteam_rejection,
        "stage_c_ready": redteam.stage_c_ready,
        "stagnation_stop": stagnation_stop,
    }


def _clamp(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 4)
