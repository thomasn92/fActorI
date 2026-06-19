from __future__ import annotations

from factori.schemas import (
    BaselineReport,
    BranchStatus,
    BridgeReport,
    ControllerDecisionAction,
    RetrievalAdequacyCertificate,
    ReviewerDisagreementType,
    ReviewerPanelResult,
    ScoreVector,
)
from factori.uncertainty import estimate_score_uncertainty


def test_uncertainty_estimate_is_deterministic() -> None:
    score = _score()

    first = estimate_score_uncertainty(
        candidate_id="candidate-1",
        score=score,
        reviewer_panel=_panel(),
        bridge_report=_bridge(),
        baseline_report=_baseline(),
        retrieval_certificate=_retrieval(),
    )
    second = estimate_score_uncertainty(
        candidate_id="candidate-1",
        score=score,
        reviewer_panel=_panel(),
        bridge_report=_bridge(),
        baseline_report=_baseline(),
        retrieval_certificate=_retrieval(),
    )

    assert first == second
    assert first.s_lower == round(first.s_hat - first.u_s, 6)
    assert first.passed


def test_uncertainty_lower_bound_can_prune_candidate() -> None:
    estimate = estimate_score_uncertainty(
        candidate_id="candidate-weak",
        score=_score(novelty=0.55, feasibility=0.55, verifiability=0.55),
        reviewer_panel=_panel(disagreement=0.20),
        bridge_report=_bridge(survival_score=0.70),
        baseline_report=_baseline(baseline_strength=0.60),
        retrieval_certificate=_retrieval(rho_adequacy=0.52),
    )

    assert estimate.s_lower < estimate.tau_s
    assert not estimate.passed


def _score(
    novelty: float = 0.78,
    feasibility: float = 0.80,
    verifiability: float = 0.76,
) -> ScoreVector:
    return ScoreVector(
        novelty=novelty,
        feasibility=feasibility,
        verifiability=verifiability,
        reviewer=0.78,
        difficulty=0.35,
        diversity=0.58,
        uncertainty=0.04,
    )


def _panel(disagreement: float = 0.01) -> ReviewerPanelResult:
    return ReviewerPanelResult(
        candidate_id="candidate-1",
        reports=[],
        aggregate_scores=[0.78, 0.79, 0.80],
        disagreement=disagreement,
        disagreement_type=ReviewerDisagreementType.LOW_DISAGREEMENT,
        resolved_aggregate_score=0.79,
    )


def _bridge(survival_score: float = 0.86) -> BridgeReport:
    return BridgeReport(
        candidate_id="candidate-1",
        map_score=0.86,
        transfer_score=0.86,
        baseline_score=0.86,
        data_score=0.86,
        falsify_score=0.86,
        nondecorative_score=0.86,
        survival_score=survival_score,
        survives=survival_score >= 0.70,
    )


def _baseline(baseline_strength: float = 0.78) -> BaselineReport:
    return BaselineReport(
        candidate_id="candidate-1",
        baseline_strength=baseline_strength,
        candidate_score_advantage=0.08,
        baseline_valid=baseline_strength >= 0.60,
        repairable=True,
        routed_action=ControllerDecisionAction.CONTINUE,
    )


def _retrieval(rho_adequacy: float = 0.82) -> RetrievalAdequacyCertificate:
    return RetrievalAdequacyCertificate(
        semantic=rho_adequacy,
        keyword=rho_adequacy,
        citation=rho_adequacy,
        diversity=rho_adequacy,
        adversarial=rho_adequacy,
        weights={
            "semantic": 0.20,
            "keyword": 0.20,
            "citation": 0.20,
            "diversity": 0.20,
            "adversarial": 0.20,
        },
        rho_adequacy=rho_adequacy,
        tau_adequacy=0.60,
        passed=rho_adequacy >= 0.60,
        status=BranchStatus.ACTIVE
        if rho_adequacy >= 0.60
        else BranchStatus.INSUFFICIENT_RETRIEVAL_ADEQUACY,
    )
