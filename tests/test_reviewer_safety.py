import pytest

from factori.adapters.reviewer_safety import (
    LLMReviewerResponseError,
    parse_llm_reviewer_response,
    validate_llm_reviewer_report,
)
from factori.schemas import DataRequirement, ReviewerRecommendation


def test_parser_accepts_clamps_and_normalizes_structured_reviews() -> None:
    result = parse_llm_reviewer_response(
        {
            "reviews": [
                _review(
                    novelty_score=1.4,
                    feasibility_score=-0.2,
                    recommendation="weak accept",
                )
            ]
        },
        expected_candidate_id="candidate-1",
        data_requirement=DataRequirement.NO_DATA,
        backend="provider-neutral-reviewer",
        provider="provider-neutral-provider",
    )

    report = result.reports[0]
    assert report.novelty_score == 1.0
    assert report.feasibility_score == 0.0
    assert report.recommendation == ReviewerRecommendation.WEAK_ACCEPT
    assert report.fake is False
    assert report.is_verification_evidence is False
    assert report.metadata["adapter_backend"] == "provider-neutral-reviewer"
    assert report.metadata["adapter_provider"] == "provider-neutral-provider"
    assert validate_llm_reviewer_report(report).valid


def test_parser_rejects_malformed_response() -> None:
    with pytest.raises(LLMReviewerResponseError, match="valid JSON"):
        parse_llm_reviewer_response("not-json")


@pytest.mark.parametrize(
    "unsafe_field",
    [
        {"label": "LeanVerified"},
        {"objections": ["The proof has been verified."]},
        {"objections": ["This experiment is verified."]},
    ],
)
def test_parser_rejects_verification_authority(unsafe_field: dict[str, object]) -> None:
    raw = _review()
    raw.update(unsafe_field)

    result = parse_llm_reviewer_response(
        {"reviews": [raw]},
        expected_candidate_id="candidate-1",
    )

    assert result.reports == []
    assert result.rejected_reports


def test_parser_rejects_synthetic_to_real_world_validation() -> None:
    raw = _review(objections=["This establishes real-world validation."])

    result = parse_llm_reviewer_response(
        {"reviews": [raw]},
        expected_candidate_id="candidate-1",
        data_requirement=DataRequirement.SYNTHETIC_ONLY,
    )

    reasons = result.rejected_reports[0]["reasons"]
    assert any("synthetic-only" in reason for reason in reasons)


def _review(**updates: object) -> dict[str, object]:
    value: dict[str, object] = {
        "reviewer_id": "reviewer-methods",
        "candidate_id": "candidate-1",
        "novelty_score": 0.7,
        "feasibility_score": 0.8,
        "verifiability_score": 0.75,
        "clarity_score": 0.8,
        "significance_score": 0.7,
        "objections": ["Clarify the declared baseline."],
        "recommendation": "Revise",
    }
    value.update(updates)
    return value
