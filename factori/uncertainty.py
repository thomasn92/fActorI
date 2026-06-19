"""Deterministic uncertainty estimates for Stage C selection."""

from __future__ import annotations

from statistics import variance

from factori.schemas import (
    BaselineReport,
    BridgeReport,
    RetrievalAdequacyCertificate,
    ReviewerPanelResult,
    ScoreVector,
    UncertaintyEstimate,
)

TAU_S = 0.50


def estimate_score_uncertainty(
    *,
    candidate_id: str,
    score: ScoreVector,
    reviewer_panel: ReviewerPanelResult,
    bridge_report: BridgeReport,
    baseline_report: BaselineReport,
    retrieval_certificate: RetrievalAdequacyCertificate,
    tau_s: float = TAU_S,
) -> UncertaintyEstimate:
    """Estimate deterministic score uncertainty and conservative lower bound."""
    score_values = [
        score.novelty,
        score.feasibility,
        score.verifiability,
        score.reviewer,
        1.0 - score.difficulty,
        score.diversity,
    ]
    components = {
        "reviewer_disagreement": min(0.25, reviewer_panel.disagreement * 3.0),
        "retrieval_weakness": (1.0 - retrieval_certificate.rho_adequacy) * 0.20,
        "bridge_weakness": (1.0 - bridge_report.survival_score) * 0.15,
        "baseline_weakness": (1.0 - baseline_report.baseline_strength) * 0.15,
        "score_component_variance": min(0.20, variance(score_values) * 0.50),
        "score_declared_uncertainty": score.uncertainty * 0.50,
    }
    u_s = round(sum(components.values()), 6)
    s_hat = round(score.base_score(), 6)
    s_lower = round(max(0.0, s_hat - u_s), 6)
    return UncertaintyEstimate(
        candidate_id=candidate_id,
        s_hat=s_hat,
        u_s=u_s,
        s_lower=s_lower,
        tau_s=tau_s,
        passed=s_lower >= tau_s,
        components={key: round(value, 6) for key, value in components.items()},
    )
