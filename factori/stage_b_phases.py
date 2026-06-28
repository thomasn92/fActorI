"""Internal deterministic phases for Stage B structural validation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from factori.artifacts import ArtifactStore
from factori.baselines import evaluate_baseline
from factori.bridge import run_bridge_check
from factori.ledger import ResearchLedger
from factori.persistence import (
    ArtifactWriteSpec,
    persist_artifacts_with_commit,
    persist_json_artifact,
    persist_markdown_artifact,
)
from factori.questioner import route_questions_to_action, select_questions
from factori.redteam import run_redteam_checks
from factori.reports import render_stage_b_report
from factori.retrieval import run_retrieval_with_provenance
from factori.reviewers import STAGE_B_REVIEWER_RUBRIC, run_reviewer_panel
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
    LLMReviewerTrace,
    RedTeamReport,
    RetrievalAdequacyCertificate,
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
    from factori.adapters.base import RetrievalClient, ReviewerClient

MAX_STAGE_B_SURVIVORS = 2
STAGE_B_CHILD_VARIANTS = (
    "narrow_scope",
    "stronger_baseline",
    "synthetic_experiment_contract",
    "theorem_or_conjecture_form",
)


class StageBError(RuntimeError):
    """Raised when Stage B prerequisites are missing."""


@dataclass(frozen=True)
class StageBInputBundle:
    """Loaded Stage B inputs plus adapter metadata for the start commit."""

    stage_a_survivors: list[Candidate]
    retrieval_adapter_metadata: dict[str, object]
    reviewer_adapter_metadata: dict[str, object]


@dataclass(frozen=True)
class StageBRetrievalPhaseResult:
    """Stage B retrieval context and artifacts keyed by Stage A parent."""

    retrieval_runs: dict[str, RetrievalRunReport]
    retrieval_artifacts: list[ArtifactRef]
    retrieval_certificates: dict[str, RetrievalAdequacyCertificate]


@dataclass(frozen=True)
class StageBChildProcessingResult:
    """Per-child Stage B structural validation result."""

    candidate: Candidate
    reviewer_panel: ReviewerPanelResult
    bridge_report: BridgeReport
    baseline_report: BaselineReport
    redteam_report: RedTeamReport
    score: ScoreVector
    artifacts: list[ArtifactRef]
    llm_reviewer_artifacts: list[ArtifactRef]


@dataclass(frozen=True)
class StageBChildrenProcessingResult:
    """Aggregated per-child processing results."""

    candidate_by_id: dict[str, Candidate]
    reviewer_panels: dict[str, ReviewerPanelResult]
    bridge_reports: dict[str, BridgeReport]
    baseline_reports: dict[str, BaselineReport]
    redteam_reports: dict[str, RedTeamReport]
    scores: dict[str, ScoreVector]
    artifacts: dict[str, list[ArtifactRef]]
    llm_reviewer_artifacts: list[ArtifactRef]


@dataclass(frozen=True)
class StageBGatePhaseResult:
    """Stage B gate classification and active passing candidates."""

    candidate_by_id: dict[str, Candidate]
    gate_pruned: list[Candidate]
    passing: list[Candidate]
    final_children: list[Candidate]


@dataclass(frozen=True)
class StageBRejectionBuckets:
    """Deterministic Stage B rejection bucket summaries."""

    rejected_bridge: list[Candidate]
    rejected_review: list[Candidate]
    rejected_baseline: list[Candidate]
    insufficient_retrieval: list[Candidate]


@dataclass(frozen=True)
class StageBReportPersistenceResult:
    """Persisted Stage B Markdown report and producing commit."""

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


def load_stage_b_inputs(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    retrieval_client: RetrievalClient | None = None,
    reviewer_client: ReviewerClient | None = None,
) -> StageBInputBundle:
    """Initialize the run, load Stage A survivors, and commit Stage B start."""
    store.init_run(run_id)
    stage_a_survivors = load_stage_a_survivors(run_id, ledger)
    retrieval_metadata = retrieval_adapter_metadata(retrieval_client)
    reviewer_metadata = reviewer_adapter_metadata(reviewer_client)
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_STARTED,
        payload={
            "stage_a_survivor_ids": [candidate.id for candidate in stage_a_survivors],
            "retrieval_adapter": retrieval_metadata,
            "reviewer_adapter": reviewer_metadata,
        },
    )
    return StageBInputBundle(
        stage_a_survivors=stage_a_survivors,
        retrieval_adapter_metadata=retrieval_metadata,
        reviewer_adapter_metadata=reviewer_metadata,
    )


def run_stage_b_retrieval_phase(
    *,
    run_id: str,
    stage_a_survivors: list[Candidate],
    store: ArtifactStore,
    ledger: ResearchLedger,
    retrieval_client: RetrievalClient | None = None,
) -> StageBRetrievalPhaseResult:
    """Run optional gated retrieval once per Stage A parent."""
    retrieval_runs: dict[str, RetrievalRunReport] = {}
    retrieval_artifacts: list[ArtifactRef] = []
    retrieval_certificates: dict[str, RetrievalAdequacyCertificate] = {}
    if retrieval_client is not None and not retrieval_client.is_fake:
        limit = int(getattr(retrieval_client, "default_limit", 5))
        for parent in stage_a_survivors:
            retrieval_execution = run_retrieval_with_provenance(
                run_id=run_id,
                query=retrieval_query_for_candidate(parent),
                limit=limit,
                retrieval_client=retrieval_client,
                store=store,
                ledger=ledger,
            )
            retrieval_runs[parent.id] = retrieval_execution.report
            retrieval_artifacts.extend(retrieval_execution.artifacts.values())
            retrieval_certificates[parent.id] = retrieval_execution.report.certificate
    return StageBRetrievalPhaseResult(
        retrieval_runs=retrieval_runs,
        retrieval_artifacts=retrieval_artifacts,
        retrieval_certificates=retrieval_certificates,
    )


def expand_stage_b_children(stage_a_survivors: list[Candidate]) -> list[Candidate]:
    """Expand Stage A survivors into deterministic localized child variants."""
    children: list[Candidate] = []
    for parent in stage_a_survivors:
        children.extend(_child(parent, variant) for variant in STAGE_B_CHILD_VARIANTS)
    return children


def planned_stage_b_review_calls(stage_a_survivor_count: int) -> int:
    """Return one external reviewer call per deterministic Stage B child."""
    if stage_a_survivor_count < 0:
        raise ValueError("stage_a_survivor_count must be non-negative")
    return stage_a_survivor_count * len(STAGE_B_CHILD_VARIANTS)


def persist_stage_b_child_candidates(
    *,
    run_id: str,
    children: list[Candidate],
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> dict[str, list[ArtifactRef]]:
    """Write child candidate artifacts and child-generation commits."""
    return {
        child.id: [_write_child_candidate(run_id, child, store, ledger)]
        for child in children
    }


def process_stage_b_child(
    *,
    run_id: str,
    child: Candidate,
    retrieval_runs: dict[str, RetrievalRunReport],
    retrieval_certificates: dict[str, RetrievalAdequacyCertificate],
    store: ArtifactStore,
    ledger: ResearchLedger,
    reviewer_client: ReviewerClient | None = None,
) -> StageBChildProcessingResult:
    """Run reviewer, score, bridge, baseline, red-team, and questioner phases for one child."""
    artifacts: list[ArtifactRef] = []
    llm_reviewer_artifacts: list[ArtifactRef] = []
    current = child

    if reviewer_client is not None and not reviewer_client.is_fake:
        panel = reviewer_client.review_candidate(
            child,
            STAGE_B_REVIEWER_RUBRIC,
            retrieval_context_for_reviewer(child, retrieval_runs),
        )
        trace = latest_reviewer_trace(reviewer_client)
        trace_artifacts = _write_llm_reviewer_trace_artifacts(
            run_id,
            child.id,
            trace,
            store,
            ledger,
        )
        artifacts.extend(trace_artifacts)
        llm_reviewer_artifacts.extend(trace_artifacts)
    else:
        panel = run_reviewer_panel(child)
    artifacts.append(_write_reviewer_artifact(run_id, panel, store, ledger))
    _commit_disagreement(run_id, panel, ledger)
    if panel.disagreement_type == ReviewerDisagreementType.FATAL_CONFUSION:
        current = current.model_copy(update={"status": BranchStatus.REJECTED_RED_TEAM})

    score = compute_stage_b_score(child, panel)
    artifacts.append(_write_score_artifact(run_id, child, score, store, ledger))

    bridge = run_bridge_check(child)
    artifacts.append(_write_bridge_artifact(run_id, bridge, store, ledger))
    if bridge.repair_attempted:
        _commit_bridge_repair(run_id, bridge, ledger)
    if not bridge.survives:
        current = current.model_copy(update={"status": bridge.final_status})

    baseline = evaluate_baseline(child, score)
    artifacts.append(_write_baseline_artifact(run_id, baseline, store, ledger))

    redteam = run_redteam_checks(
        child,
        score,
        retrieval_certificate=retrieval_certificates.get(child.parent_candidate_id or ""),
    )
    artifacts.append(_write_redteam_artifact(run_id, redteam, store, ledger))
    if redteam.status != BranchStatus.ACTIVE:
        current = current.model_copy(
            update={
                "status": redteam.status,
                "verification": VerificationState(labels=[VerificationLabel.CONJECTURE])
                if redteam.status == BranchStatus.TRIVIAL_THEOREM_CANDIDATE
                else current.verification,
            }
        )

    questions = select_questions(
        "stage_b",
        current,
        score,
        child.literature,
        current.verification,
        triggers=question_triggers(current, panel, baseline, redteam),
    )
    routed = route_questions_to_action(
        questions,
        current,
        score,
        child.literature,
        current.verification,
    )
    ledger.append_commit(
        run_id=run_id,
        candidate_id=child.id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_QUESTIONER_ROUTED,
        payload=routed.model_dump(mode="json"),
    )

    return StageBChildProcessingResult(
        candidate=current,
        reviewer_panel=panel,
        bridge_report=bridge,
        baseline_report=baseline,
        redteam_report=redteam,
        score=score,
        artifacts=artifacts,
        llm_reviewer_artifacts=llm_reviewer_artifacts,
    )


def process_stage_b_children(
    *,
    run_id: str,
    children: list[Candidate],
    artifacts: dict[str, list[ArtifactRef]],
    retrieval_runs: dict[str, RetrievalRunReport],
    retrieval_certificates: dict[str, RetrievalAdequacyCertificate],
    store: ArtifactStore,
    ledger: ResearchLedger,
    reviewer_client: ReviewerClient | None = None,
) -> StageBChildrenProcessingResult:
    """Run all per-child Stage B structural checks."""
    reviewer_panels: dict[str, ReviewerPanelResult] = {}
    bridge_reports: dict[str, BridgeReport] = {}
    baseline_reports: dict[str, BaselineReport] = {}
    redteam_reports: dict[str, RedTeamReport] = {}
    scores: dict[str, ScoreVector] = {}
    candidate_by_id = {child.id: child for child in children}
    llm_reviewer_artifacts: list[ArtifactRef] = []

    for child in children:
        result = process_stage_b_child(
            run_id=run_id,
            child=child,
            retrieval_runs=retrieval_runs,
            retrieval_certificates=retrieval_certificates,
            store=store,
            ledger=ledger,
            reviewer_client=reviewer_client,
        )
        candidate_by_id[child.id] = result.candidate
        reviewer_panels[child.id] = result.reviewer_panel
        bridge_reports[child.id] = result.bridge_report
        baseline_reports[child.id] = result.baseline_report
        redteam_reports[child.id] = result.redteam_report
        scores[child.id] = result.score
        artifacts[child.id].extend(result.artifacts)
        llm_reviewer_artifacts.extend(result.llm_reviewer_artifacts)

    return StageBChildrenProcessingResult(
        candidate_by_id=candidate_by_id,
        reviewer_panels=reviewer_panels,
        bridge_reports=bridge_reports,
        baseline_reports=baseline_reports,
        redteam_reports=redteam_reports,
        scores=scores,
        artifacts=artifacts,
        llm_reviewer_artifacts=llm_reviewer_artifacts,
    )


def apply_stage_b_gate_phase(
    *,
    run_id: str,
    children: list[Candidate],
    candidate_by_id: dict[str, Candidate],
    reviewer_panels: dict[str, ReviewerPanelResult],
    bridge_reports: dict[str, BridgeReport],
    baseline_reports: dict[str, BaselineReport],
    redteam_reports: dict[str, RedTeamReport],
    scores: dict[str, ScoreVector],
    ledger: ResearchLedger,
) -> StageBGatePhaseResult:
    """Apply Stage B gate and ledger deterministic pruned decisions."""
    gate_pruned: list[Candidate] = []
    passing: list[Candidate] = []
    for child in children:
        current = candidate_by_id[child.id]
        stagnation_state = stage_b_stagnation_state(current, scores[child.id])
        if passes_stage_b_gate(
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
        pruned = stage_b_pruned_candidate(
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
                "stage_b_gate": gate_payload(
                    reviewer_panels[child.id],
                    bridge_reports[child.id],
                    baseline_reports[child.id],
                    redteam_reports[child.id],
                    stagnation_state.stagnant,
                ),
            },
        )

    return StageBGatePhaseResult(
        candidate_by_id=candidate_by_id,
        gate_pruned=gate_pruned,
        passing=passing,
        final_children=list(candidate_by_id.values()),
    )


def select_stage_b_survivors(
    *,
    run_id: str,
    passing: list[Candidate],
    scores: dict[str, ScoreVector],
    ledger: ResearchLedger,
    max_survivors: int = MAX_STAGE_B_SURVIVORS,
) -> list[Candidate]:
    """Rank passing Stage B candidates, keep the top survivors, and ledger the decision."""
    survivors = rank_stage_b_survivors(passing, scores)[:max_survivors]
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_SURVIVORS_SELECTED,
        payload={
            "survivor_ids": [candidate.id for candidate in survivors],
            "passing_candidate_ids": [candidate.id for candidate in passing],
            "max_survivors": max_survivors,
        },
    )
    return survivors


def summarize_stage_b_rejections(
    *,
    final_children: list[Candidate],
    reviewer_panels: dict[str, ReviewerPanelResult],
    bridge_reports: dict[str, BridgeReport],
    baseline_reports: dict[str, BaselineReport],
    redteam_reports: dict[str, RedTeamReport],
) -> StageBRejectionBuckets:
    """Compute deterministic Stage B rejection bucket lists."""
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
    return StageBRejectionBuckets(
        rejected_bridge=rejected_bridge,
        rejected_review=rejected_review,
        rejected_baseline=rejected_baseline,
        insufficient_retrieval=insufficient_retrieval,
    )


def persist_stage_b_outputs(
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
    reviewer_adapter_metadata: dict[str, object],
) -> StageBReportPersistenceResult:
    """Persist the Stage B Markdown report and report ledger commit."""
    report_artifact, report_commit_hash = _write_stage_b_report(
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
        store=store,
        ledger=ledger,
        retrieval_adapter_metadata=retrieval_adapter_metadata,
        reviewer_adapter_metadata=reviewer_adapter_metadata,
    )
    return StageBReportPersistenceResult(
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


def passes_stage_b_gate(
    candidate: Candidate,
    score: ScoreVector,
    panel: ReviewerPanelResult,
    bridge: BridgeReport,
    baseline: BaselineReport,
    redteam: RedTeamReport,
    *,
    stagnation_stop: bool,
) -> bool:
    """Return whether a candidate satisfies the Stage B gate."""
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


def stage_b_pruned_candidate(
    candidate: Candidate,
    panel: ReviewerPanelResult,
    bridge: BridgeReport,
    baseline: BaselineReport,
    redteam: RedTeamReport,
    *,
    stagnation_stop: bool,
) -> Candidate:
    """Return the deterministic pruned candidate status for a failed Stage B gate."""
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


def question_triggers(
    candidate: Candidate,
    panel: ReviewerPanelResult,
    baseline: BaselineReport,
    redteam: RedTeamReport,
) -> set[str]:
    """Return deterministic Strategic Questioner triggers for Stage B."""
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


def stage_b_stagnation_state(candidate: Candidate, score: ScoreVector):
    """Return deterministic Stage B stagnation state."""
    stagnant = "stagnant" in candidate.id
    history = [
        StagnationEvent(action="StageA", score=max(0.0, score.base_score() - 0.002)),
        StagnationEvent(action="Review", score=score.base_score()),
        StagnationEvent(action="Bridge", score=score.base_score() + (0.0 if stagnant else 0.03)),
    ]
    return compute_stagnation(history, epsilon_score=0.01, window=3, n_stag=2)


def rank_stage_b_survivors(
    candidates: list[Candidate],
    scores: dict[str, ScoreVector],
) -> list[Candidate]:
    """Return deterministic Stage B survivor ranking."""
    return sorted(
        candidates,
        key=lambda candidate: (-cost_aware_score(candidate, scores[candidate.id]), candidate.id),
    )


def retrieval_query_for_candidate(candidate: Candidate) -> str:
    """Return the deterministic Stage B retrieval query for one candidate."""
    return " ".join(
        part
        for part in [candidate.domain, candidate.method, candidate.question]
        if part
    )


def retrieval_adapter_metadata(
    retrieval_client: RetrievalClient | None,
) -> dict[str, object]:
    """Return Stage B retrieval adapter metadata for reports and commits."""
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


def reviewer_adapter_metadata(
    reviewer_client: ReviewerClient | None,
) -> dict[str, object]:
    """Return Stage B reviewer adapter metadata for reports and commits."""
    if reviewer_client is None:
        return {
            "backend": "fake",
            "class": "deterministic_fake_reviewer_panel",
            "model": None,
            "fake": True,
            "external_calls_enabled": False,
            "has_verification_authority": False,
        }
    return {
        "backend": reviewer_client.backend_name,
        "class": type(reviewer_client).__name__,
        "model": getattr(reviewer_client, "model", None),
        "fake": reviewer_client.is_fake,
        "external_calls_enabled": reviewer_client.external_calls_enabled,
        "has_verification_authority": False,
    }


def retrieval_context_for_reviewer(
    candidate: Candidate,
    retrieval_runs: dict[str, RetrievalRunReport],
) -> dict[str, object] | None:
    """Return bounded retrieval context for reviewer prompts."""
    parent_id = candidate.parent_candidate_id or ""
    retrieval_run = retrieval_runs.get(parent_id)
    if retrieval_run is None:
        return {
            "source": "bounded_candidate_literature_state",
            "fake": True,
            "source_count": candidate.literature.k,
            "closest_prior_ids": candidate.literature.closest_priors,
            "is_exhaustive_literature_coverage": False,
        }
    return {
        "source": retrieval_run.provider,
        "fake": retrieval_run.fake,
        "source_count": len(retrieval_run.results),
        "source_ids": [result.source_id for result in retrieval_run.results],
        "rho_adequacy": retrieval_run.certificate.rho_adequacy,
        "is_exhaustive_literature_coverage": False,
    }


def latest_reviewer_trace(reviewer_client: ReviewerClient) -> LLMReviewerTrace:
    """Return the latest reviewer trace or fail clearly."""
    traces = getattr(reviewer_client, "review_traces", None)
    if not isinstance(traces, list) or not traces:
        raise StageBError("Real LLM reviewer did not provide a provenance trace")
    return LLMReviewerTrace.model_validate(traces[-1])


def gate_payload(
    panel: ReviewerPanelResult,
    bridge: BridgeReport,
    baseline: BaselineReport,
    redteam: RedTeamReport,
    stagnation_stop: bool,
) -> dict[str, object]:
    """Return the deterministic Stage B gate payload."""
    return {
        "reviewer_disagreement_type": panel.disagreement_type.value,
        "bridge_survives": bridge.survives,
        "baseline_valid": baseline.baseline_valid,
        "redteam_rejection": redteam.redteam_rejection,
        "stage_c_ready": redteam.stage_c_ready,
        "stagnation_stop": stagnation_stop,
    }


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


def _write_child_candidate(
    run_id: str,
    candidate: Candidate,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    result = persist_json_artifact(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id=candidate.id,
        artifact_type=ArtifactType.CANDIDATE,
        payload=candidate,
        metadata={"stage": "stage_b", "fake": True},
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_CHILD_GENERATED,
        commit_payload={
            "candidate": candidate.model_dump(mode="json"),
            "parent_candidate_id": candidate.parent_candidate_id,
            "variant_type": candidate.variant_type,
        },
        candidate_id=candidate.id,
    )
    return result.artifact


def _write_reviewer_artifact(
    run_id: str,
    panel: ReviewerPanelResult,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    fake = all(report.fake for report in panel.reports)
    result = persist_json_artifact(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id=f"reviewer-report-{panel.candidate_id}",
        artifact_type=ArtifactType.REPORT,
        payload=panel,
        metadata={
            "stage": "stage_b",
            "report": "reviewers",
            "fake": fake,
            "is_verification_evidence": False,
            "scientific_approval": False,
        },
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_REVIEWERS_RUN,
        commit_payload=panel.model_dump(mode="json"),
        candidate_id=panel.candidate_id,
    )
    return result.artifact


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
    result = persist_json_artifact(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id=f"stage-b-score-{candidate.id}",
        artifact_type=ArtifactType.SCORE,
        payload=payload,
        metadata={"stage": "stage_b", "candidate_id": candidate.id, "fake": True},
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_SCORE_COMPUTED,
        commit_payload=payload,
        candidate_id=candidate.id,
    )
    return result.artifact


def _write_bridge_artifact(
    run_id: str,
    bridge: BridgeReport,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    result = persist_json_artifact(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id=f"bridge-report-{bridge.candidate_id}",
        artifact_type=ArtifactType.REPORT,
        payload=bridge,
        metadata={"stage": "stage_b", "report": "bridge", "fake": True},
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_BRIDGE_CHECKED,
        commit_payload=bridge.model_dump(mode="json"),
        candidate_id=bridge.candidate_id,
    )
    return result.artifact


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
    result = persist_json_artifact(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id=f"baseline-report-{baseline.candidate_id}",
        artifact_type=ArtifactType.REPORT,
        payload=baseline,
        metadata={"stage": "stage_b", "report": "baseline", "fake": True},
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_BASELINE_CHECKED,
        commit_payload=baseline.model_dump(mode="json"),
        candidate_id=baseline.candidate_id,
    )
    return result.artifact


def _write_redteam_artifact(
    run_id: str,
    redteam: RedTeamReport,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    result = persist_json_artifact(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id=f"redteam-report-{redteam.candidate_id}",
        artifact_type=ArtifactType.REPORT,
        payload=redteam,
        metadata={"stage": "stage_b", "report": "redteam", "fake": True},
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_REDTEAM_CHECKED,
        commit_payload=redteam.model_dump(mode="json"),
        candidate_id=redteam.candidate_id,
    )
    return result.artifact


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
    reviewer_adapter_metadata: dict[str, object],
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
        reviewer_adapter_metadata=reviewer_adapter_metadata,
    )
    result = persist_markdown_artifact(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id="stage-b-report",
        artifact_type=ArtifactType.REPORT,
        markdown=markdown,
        metadata={
            "stage": "stage_b",
            "fake": True,
            "retrieval_adapter": retrieval_adapter_metadata,
            "reviewer_adapter": reviewer_adapter_metadata,
            "is_verification_evidence": False,
        },
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_REPORT_WRITTEN,
        commit_payload={
            "stage_a_survivors": len(stage_a_survivors),
            "stage_b_children": len(children),
            "survivor_ids": [candidate.id for candidate in survivors],
            "retrieval_adapter": retrieval_adapter_metadata,
            "reviewer_adapter": reviewer_adapter_metadata,
            "reviewer_has_verification_authority": False,
        },
    )
    return result.artifact, result.commit.commit_hash


def _write_llm_reviewer_trace_artifacts(
    run_id: str,
    candidate_id: str,
    trace: LLMReviewerTrace,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> list[ArtifactRef]:
    common_metadata = {
        "stage": "stage_b",
        "candidate_id": candidate_id,
        "artifact_role": "llm_reviewer_context",
        "fake": False,
        "is_verification_evidence": False,
        "scientific_approval": False,
    }
    result = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                artifact_id=f"llm-stage-b-reviewer-request-{candidate_id}",
                artifact_type=ArtifactType.REPORT,
                payload=trace.request,
                artifact_format="json",
                metadata={**common_metadata, "trace_part": "request"},
            ),
            ArtifactWriteSpec(
                artifact_id=f"llm-stage-b-reviewer-response-{candidate_id}",
                artifact_type=ArtifactType.REPORT,
                payload=trace.raw_response,
                artifact_format="json",
                metadata={**common_metadata, "trace_part": "response"},
            ),
            ArtifactWriteSpec(
                artifact_id=f"llm-stage-b-reviewer-parse-report-{candidate_id}",
                artifact_type=ArtifactType.REPORT,
                payload=trace.parse_result,
                artifact_format="json",
                metadata={**common_metadata, "trace_part": "parse_report"},
            ),
        ],
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_B_LLM_REVIEW_RECORDED,
        commit_payload={
            "candidate_id": candidate_id,
            "backend": trace.request.get("backend"),
            "model": trace.request.get("model"),
            "accepted_reports": len(trace.parse_result.reports),
            "rejected_reports": len(trace.parse_result.rejected_reports),
            "fallback_used": trace.parse_result.fallback_used,
            "is_verification_evidence": False,
        },
        candidate_id=candidate_id,
    )
    return result.artifacts


def _clamp(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 4)
