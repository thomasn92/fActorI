from __future__ import annotations

from factori.reviewers import resolve_disagreement, reviewer_disagreement, run_reviewer_panel
from factori.schemas import (
    Candidate,
    ReviewerDisagreementType,
    ReviewerRecommendation,
    StageBReviewerReport,
)


def test_reviewer_reports_are_deterministic() -> None:
    candidate = Candidate(
        id="candidate-review",
        domain="demo",
        method="optimal transport",
        question="Can reviewers evaluate deterministically?",
        baseline="Compare against a deterministic baseline.",
        variant_type="narrow_scope",
        symbolic_state={"variant_type": "narrow_scope"},
    )

    first = run_reviewer_panel(candidate)
    second = run_reviewer_panel(candidate)

    assert first == second
    assert len(first.reports) == 3
    assert {report.reviewer_id for report in first.reports} == {
        "reviewer-novelty",
        "reviewer-methods",
        "reviewer-skeptic",
    }


def test_reviewer_disagreement_is_deterministic() -> None:
    reports = [
        _report("a", 0.80),
        _report("b", 0.60),
        _report("c", 0.40),
    ]

    assert reviewer_disagreement(reports) == reviewer_disagreement(reports)
    assert reviewer_disagreement(reports) == 0.026667


def test_novel_controversy_preserves_branch() -> None:
    result = resolve_disagreement(
        "candidate-controversy",
        [_report("a", 0.85), _report("b", 0.45), _report("c", 0.60)],
    )

    assert result.disagreement_type == ReviewerDisagreementType.NOVEL_CONTROVERSY
    assert result.preserved
    assert not result.rejected


def test_fatal_confusion_rejects_branch() -> None:
    result = resolve_disagreement(
        "candidate-fatal",
        [_report("a", 0.30), _report("b", 0.28), _report("c", 0.32)],
    )

    assert result.disagreement_type == ReviewerDisagreementType.FATAL_CONFUSION
    assert result.rejected


def test_reviewer_error_excludes_anomalous_reviewer() -> None:
    result = resolve_disagreement(
        "candidate-error",
        [_report("a", 0.70), _report("b", 0.72), _report("c", 0.20)],
    )

    assert result.disagreement_type == ReviewerDisagreementType.REVIEWER_ERROR
    assert result.excluded_reviewer_id == "c"


def _report(reviewer_id: str, score: float) -> StageBReviewerReport:
    return StageBReviewerReport(
        reviewer_id=reviewer_id,
        candidate_id="candidate",
        novelty_score=score,
        feasibility_score=score,
        verifiability_score=score,
        clarity_score=score,
        significance_score=score,
        recommendation=ReviewerRecommendation.REVISE,
    )
