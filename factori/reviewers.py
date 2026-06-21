"""Deterministic fake reviewer panels for Stage B."""

from __future__ import annotations

from statistics import mean

from factori.schemas import (
    Candidate,
    ReviewerDisagreementType,
    ReviewerPanelResult,
    ReviewerRecommendation,
    StageBReviewerReport,
)

STAGE_B_REVIEWER_RUBRIC = {
    "novelty": "Assess structural distinctness without claiming exhaustive literature coverage.",
    "feasibility": "Assess whether the stated method and assumptions are executable.",
    "verifiability": "Assess whether the claim can later be tested; do not verify it.",
    "clarity": "Assess precision of objects, assumptions, and proposed comparisons.",
    "significance": "Assess potential value conditionally, without publication approval.",
}


def run_reviewer_panel(candidate: Candidate) -> ReviewerPanelResult:
    """Run a deterministic fake reviewer panel."""
    reports = [
        _review(candidate, "reviewer-novelty"),
        _review(candidate, "reviewer-methods"),
        _review(candidate, "reviewer-skeptic"),
    ]
    return resolve_disagreement(candidate.id, reports)


def reviewer_disagreement(reports: list[StageBReviewerReport]) -> float:
    """Return population variance of reviewer aggregate scores."""
    aggregates = [report.aggregate_score() for report in reports]
    if not aggregates:
        return 0.0
    aggregate_mean = mean(aggregates)
    return round(sum((score - aggregate_mean) ** 2 for score in aggregates) / len(aggregates), 6)


def resolve_disagreement(
    candidate_id: str,
    reports: list[StageBReviewerReport],
) -> ReviewerPanelResult:
    """Resolve reviewer disagreement using the deterministic policy."""
    aggregates = [round(report.aggregate_score(), 6) for report in reports]
    disagreement = reviewer_disagreement(reports)
    excluded = _anomalous_reviewer(reports)
    if excluded is not None:
        remaining = [report for report in reports if report.reviewer_id != excluded.reviewer_id]
        resolved_scores = [report.aggregate_score() for report in remaining]
        return ReviewerPanelResult(
            candidate_id=candidate_id,
            reports=reports,
            aggregate_scores=aggregates,
            disagreement=disagreement,
            disagreement_type=ReviewerDisagreementType.REVIEWER_ERROR,
            excluded_reviewer_id=excluded.reviewer_id,
            resolved_aggregate_score=round(mean(resolved_scores), 6),
        )

    aggregate_mean = mean(aggregates) if aggregates else 0.0
    if aggregate_mean < 0.35:
        disagreement_type = ReviewerDisagreementType.FATAL_CONFUSION
    elif disagreement >= 0.020 and max(aggregates) >= 0.65 and min(aggregates) <= 0.50:
        disagreement_type = ReviewerDisagreementType.NOVEL_CONTROVERSY
    elif disagreement >= 0.010:
        disagreement_type = ReviewerDisagreementType.AMBIGUOUS_CLAIM
    else:
        disagreement_type = ReviewerDisagreementType.LOW_DISAGREEMENT

    return ReviewerPanelResult(
        candidate_id=candidate_id,
        reports=reports,
        aggregate_scores=aggregates,
        disagreement=disagreement,
        disagreement_type=disagreement_type,
        resolved_aggregate_score=round(aggregate_mean, 6),
        preserved=disagreement_type == ReviewerDisagreementType.NOVEL_CONTROVERSY,
        rejected=disagreement_type == ReviewerDisagreementType.FATAL_CONFUSION,
    )


def _review(candidate: Candidate, reviewer_id: str) -> StageBReviewerReport:
    variant_type = str(candidate.symbolic_state.get("variant_type", "narrow_scope"))
    base = _base_scores(candidate, variant_type)
    adjustment = {
        "reviewer-novelty": (0.04, -0.01, 0.00, 0.00, 0.05),
        "reviewer-methods": (-0.01, 0.04, 0.05, 0.02, 0.00),
        "reviewer-skeptic": (-0.03, -0.03, -0.04, -0.04, -0.03),
    }[reviewer_id]
    novelty, feasibility, verifiability, clarity, significance = [
        _clamp(value + delta) for value, delta in zip(base, adjustment, strict=True)
    ]
    aggregate = mean([novelty, feasibility, verifiability, clarity, significance])
    return StageBReviewerReport(
        reviewer_id=reviewer_id,
        candidate_id=candidate.id,
        novelty_score=novelty,
        feasibility_score=feasibility,
        verifiability_score=verifiability,
        clarity_score=clarity,
        significance_score=significance,
        objections=_objections(candidate, aggregate),
        recommendation=_recommendation(aggregate),
    )


def _base_scores(
    candidate: Candidate,
    variant_type: str,
) -> tuple[float, float, float, float, float]:
    if "fatal" in candidate.id:
        return (0.24, 0.28, 0.22, 0.25, 0.24)
    if "controversy" in candidate.id:
        return (0.70, 0.62, 0.60, 0.58, 0.74)
    method_bonus = {
        "optimal transport": 0.05,
        "spatial statistics": 0.04,
        "synthetic stress testing": 0.03,
        "calibration": 0.03,
    }.get((candidate.method or "").lower(), 0.0)
    variant_bonus = {
        "narrow_scope": (0.05, 0.06, 0.04, 0.08, 0.02),
        "stronger_baseline": (0.03, 0.08, 0.05, 0.05, 0.03),
        "synthetic_experiment_contract": (0.02, 0.07, 0.08, 0.04, 0.02),
        "theorem_or_conjecture_form": (0.06, 0.03, 0.07, 0.03, 0.06),
    }.get(variant_type, (0.0, 0.0, 0.0, 0.0, 0.0))
    return tuple(_clamp(0.58 + method_bonus + value) for value in variant_bonus)  # type: ignore[return-value]


def _anomalous_reviewer(reports: list[StageBReviewerReport]) -> StageBReviewerReport | None:
    if len(reports) != 3:
        return None
    aggregates = [report.aggregate_score() for report in reports]
    for index, report in enumerate(reports):
        others = [score for other_index, score in enumerate(aggregates) if other_index != index]
        if abs(others[0] - others[1]) <= 0.05 and abs(aggregates[index] - mean(others)) >= 0.30:
            return report
    return None


def _objections(candidate: Candidate, aggregate: float) -> list[str]:
    objections: list[str] = []
    if aggregate < 0.60:
        objections.append("claim needs sharper formulation")
    if "baseline" not in (candidate.baseline or "").lower():
        objections.append("baseline needs strengthening")
    return objections


def _recommendation(aggregate: float) -> ReviewerRecommendation:
    if aggregate >= 0.75:
        return ReviewerRecommendation.ACCEPT
    if aggregate >= 0.65:
        return ReviewerRecommendation.WEAK_ACCEPT
    if aggregate >= 0.55:
        return ReviewerRecommendation.REVISE
    if aggregate >= 0.45:
        return ReviewerRecommendation.WEAK_REJECT
    return ReviewerRecommendation.REJECT


def _clamp(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 4)
