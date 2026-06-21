"""Parsing and safety checks for gated Stage B LLM reviewer output."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from factori.schemas import (
    DataRequirement,
    LLMReviewerParseResult,
    ReviewerRecommendation,
    ReviewerValidationResult,
    StageBReviewerReport,
)

MAX_PANEL_REPORTS = 3
_VERIFICATION_LABELS = (
    "leanverified",
    "experimentverified",
    "syntheticexperimentverified",
    "realdataexperimentverified",
)
_PROHIBITED_AUTHORITY_PATTERNS = (
    r"proof\s+(?:is\s+|has\s+been\s+)?verified",
    r"experiment(?:ally)?\s+(?:is\s+|has\s+been\s+)?verified",
    r"real[- ]world\s+(?:validation|validated|performance)",
    r"approve(?:d|s)?\s+(?:this\s+)?(?:paper|manuscript|publication)",
    r"exhaustive\s+literature\s+(?:coverage|review|search)",
)
_RECOMMENDATIONS = {
    "accept": ReviewerRecommendation.ACCEPT,
    "strongaccept": ReviewerRecommendation.ACCEPT,
    "weakaccept": ReviewerRecommendation.WEAK_ACCEPT,
    "revise": ReviewerRecommendation.REVISE,
    "revision": ReviewerRecommendation.REVISE,
    "majorrevision": ReviewerRecommendation.REVISE,
    "minorrevision": ReviewerRecommendation.REVISE,
    "weakreject": ReviewerRecommendation.WEAK_REJECT,
    "reject": ReviewerRecommendation.REJECT,
    "strongreject": ReviewerRecommendation.REJECT,
}


class LLMReviewerResponseError(ValueError):
    """Raised when an LLM reviewer response is not structured JSON."""


def parse_llm_reviewer_response(
    raw_response: Any,
    *,
    expected_candidate_id: str | None = None,
    data_requirement: DataRequirement | None = None,
    max_objections: int = 5,
) -> LLMReviewerParseResult:
    """Parse, clamp, normalize, and locally validate structural reviews."""
    payload = _structured_payload(raw_response)
    raw_reviews = payload.get("reviews")
    if not isinstance(raw_reviews, list):
        raise LLMReviewerResponseError("LLM reviewer response must contain a reviews list")

    reports: list[StageBReviewerReport] = []
    rejected: list[dict[str, Any]] = []
    truncated = len(raw_reviews) > MAX_PANEL_REPORTS
    for index, raw_review in enumerate(raw_reviews[:MAX_PANEL_REPORTS]):
        reasons = _raw_review_reasons(
            raw_review,
            expected_candidate_id=expected_candidate_id,
            data_requirement=data_requirement,
        )
        if reasons:
            rejected.append({"index": index, "reasons": reasons})
            continue
        try:
            report = _normalize_report(raw_review, max_objections=max_objections)
        except (TypeError, ValueError, ValidationError) as exc:
            rejected.append({"index": index, "reasons": [str(exc)]})
            continue
        validation = validate_llm_reviewer_report(
            report,
            data_requirement=data_requirement,
        )
        if not validation.valid:
            rejected.append({"index": index, "reasons": validation.reasons})
            continue
        reports.append(report)

    return LLMReviewerParseResult(
        reports=reports,
        rejected_reports=rejected,
        truncated=truncated,
        reasons=[] if reports else ["No valid structural reviewer reports were returned."],
    )


def validate_llm_reviewer_report(
    report: StageBReviewerReport,
    *,
    data_requirement: DataRequirement | None = None,
) -> ReviewerValidationResult:
    """Validate that a reviewer report has no verification or approval authority."""
    reasons: list[str] = []
    if not report.reviewer_id.strip():
        reasons.append("reviewer_id is required")
    if not report.candidate_id.strip():
        reasons.append("candidate_id is required")
    if report.is_verification_evidence:
        reasons.append("reviewer output cannot be verification evidence")
    if report.scientific_approval:
        reasons.append("reviewer output cannot grant scientific or publication approval")
    text = " ".join(report.objections).lower()
    reasons.extend(_authority_reasons(text, data_requirement=data_requirement))
    return ReviewerValidationResult(
        reviewer_id=report.reviewer_id,
        candidate_id=report.candidate_id,
        valid=not reasons,
        reasons=sorted(set(reasons)),
    )


def safe_failure_report(
    candidate_id: str,
    index: int,
    reasons: list[str],
) -> StageBReviewerReport:
    """Return a deterministic rejecting report when model output is unsafe."""
    reason = "; ".join(sorted(set(reasons))) or "LLM reviewer output was rejected."
    return StageBReviewerReport(
        reviewer_id=f"llm-reviewer-safety-fallback-{index}",
        candidate_id=candidate_id,
        novelty_score=0.20,
        feasibility_score=0.20,
        verifiability_score=0.20,
        clarity_score=0.20,
        significance_score=0.20,
        objections=[f"Unsafe or malformed LLM reviewer output: {reason}"],
        recommendation=ReviewerRecommendation.REJECT,
        fake=True,
    )


def _structured_payload(raw_response: Any) -> dict[str, Any]:
    if isinstance(raw_response, str):
        try:
            value = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise LLMReviewerResponseError(
                "LLM reviewer response is not valid JSON"
            ) from exc
    else:
        value = raw_response
    if isinstance(value, list):
        value = {"reviews": value}
    if not isinstance(value, dict):
        raise LLMReviewerResponseError("LLM reviewer response must be a JSON object")
    return value


def _raw_review_reasons(
    raw_review: Any,
    *,
    expected_candidate_id: str | None,
    data_requirement: DataRequirement | None,
) -> list[str]:
    if not isinstance(raw_review, dict):
        return ["review entry must be a JSON object"]
    reasons: list[str] = []
    if not str(raw_review.get("reviewer_id", "")).strip():
        reasons.append("reviewer_id is required")
    candidate_id = str(raw_review.get("candidate_id", "")).strip()
    if not candidate_id:
        reasons.append("candidate_id is required")
    elif expected_candidate_id is not None and candidate_id != expected_candidate_id:
        reasons.append("candidate_id does not match the reviewed candidate")
    serialized = json.dumps(raw_review, sort_keys=True, ensure_ascii=True).lower()
    reasons.extend(_authority_reasons(serialized, data_requirement=data_requirement))
    return sorted(set(reasons))


def _authority_reasons(
    text: str,
    *,
    data_requirement: DataRequirement | None,
) -> list[str]:
    reasons: list[str] = []
    compact = re.sub(r"[^a-z]", "", text.lower())
    if any(label in compact for label in _VERIFICATION_LABELS):
        reasons.append("verification labels are forbidden in LLM reviewer output")
    for pattern in _PROHIBITED_AUTHORITY_PATTERNS:
        if re.search(pattern, text, flags=re.IGNORECASE):
            reasons.append("reviewer output claims forbidden verification or approval authority")
            break
    if (
        data_requirement == DataRequirement.SYNTHETIC_ONLY
        and re.search(r"real[- ]world\s+(?:validation|validated|performance)", text)
    ):
        reasons.append("synthetic-only evidence cannot support real-world validation")
    return reasons


def _normalize_report(raw_review: dict[str, Any], *, max_objections: int) -> StageBReviewerReport:
    if max_objections < 1:
        raise ValueError("max_objections must be at least 1")
    score_fields = (
        "novelty_score",
        "feasibility_score",
        "verifiability_score",
        "clarity_score",
        "significance_score",
    )
    normalized_scores = {
        field: _clamped_score(raw_review.get(field), field) for field in score_fields
    }
    raw_objections = raw_review.get("objections")
    if not isinstance(raw_objections, list) or not all(
        isinstance(item, str) for item in raw_objections
    ):
        raise ValueError("objections must be a list of strings")
    recommendation = _normalize_recommendation(raw_review.get("recommendation"))
    return StageBReviewerReport(
        reviewer_id=str(raw_review["reviewer_id"]).strip(),
        candidate_id=str(raw_review["candidate_id"]).strip(),
        objections=[item.strip() for item in raw_objections[:max_objections] if item.strip()],
        recommendation=recommendation,
        fake=False,
        **normalized_scores,
    )


def _clamped_score(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        numeric = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    return round(min(1.0, max(0.0, numeric)), 4)


def _normalize_recommendation(value: Any) -> ReviewerRecommendation:
    if not isinstance(value, str):
        raise ValueError("recommendation must be a string")
    key = re.sub(r"[^a-z]", "", value.lower())
    try:
        return _RECOMMENDATIONS[key]
    except KeyError as exc:
        raise ValueError("recommendation is not a supported Stage B value") from exc


__all__ = [
    "LLMReviewerResponseError",
    "parse_llm_reviewer_response",
    "safe_failure_report",
    "validate_llm_reviewer_report",
]
