"""Adapter request, response, trace, and safety schemas."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from factori.schemas.base import StrictModel
from factori.schemas.enums import DataRequirement
from factori.schemas.stages import StageBReviewerReport


class GeneratedSectionDraft(StrictModel):
    """Deterministic placeholder from a prose adapter, never polished prose."""

    section_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    content: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    polished: bool = False
    fake: bool = True
    is_verification_evidence: bool = False


class HumanReviewDecision(StrictModel):
    """Adapter response that explicitly records no real human review in the MVP."""

    request_id: str = Field(min_length=1)
    decision: str = Field(min_length=1)
    approved: bool = False
    reviewer_is_human: bool = False
    reason: str = Field(min_length=1)
    fake: bool = True


class ReviewerPromptContract(StrictModel):
    """Deterministic prompt contract for Stage B structural critique only."""

    candidate_id: str = Field(min_length=1)
    candidate_summary: dict[str, Any]
    domain: str = Field(min_length=1)
    method: str | None = None
    data_requirement: DataRequirement
    retrieval_context_summary: dict[str, Any] | None = None
    rubric: dict[str, Any]
    requested_output_schema: dict[str, Any]
    forbidden_outputs: list[str]
    evidence_boundary_instructions: list[str]
    max_objections: int = Field(ge=1)
    prompt_text: str = Field(min_length=1)


class ReviewerValidationResult(StrictModel):
    """Safety result for one LLM-generated structural reviewer report."""

    reviewer_id: str | None = None
    candidate_id: str | None = None
    valid: bool
    reasons: list[str] = Field(default_factory=list)


class LLMReviewerParseResult(StrictModel):
    """Normalized non-evidence Stage B reviewer response."""

    reports: list[StageBReviewerReport] = Field(default_factory=list)
    rejected_reports: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    fallback_used: bool = False
    reasons: list[str] = Field(default_factory=list)
    fake: bool = False
    is_verification_evidence: bool = False


class LLMReviewerTrace(StrictModel):
    """Sanitized LLM reviewer request/response retained as non-evidence context."""

    request: dict[str, Any]
    raw_response: Any
    parse_result: LLMReviewerParseResult
    fake: bool = False
    is_verification_evidence: bool = False


class LLMPromptContract(StrictModel):
    """Deterministic Stage A prompt contract for candidate proposal only."""

    domain: str = Field(min_length=1)
    method: str | None = None
    constraints: dict[str, Any]
    data_regime_policy: list[DataRequirement]
    mvp_data_gate: dict[str, list[DataRequirement]]
    requested_output_schema: dict[str, Any]
    forbidden_claims: list[str]
    evidence_boundary_instructions: list[str]
    max_candidates: int = Field(ge=1)
    prompt_text: str = Field(min_length=1)


class CandidateValidationResult(StrictModel):
    """Safety result for one LLM-proposed candidate."""

    candidate_id: str | None = None
    valid: bool
    deferred_by_mvp_data_gate: bool = False
    reasons: list[str] = Field(default_factory=list)


class LLMCandidateParseReport(StrictModel):
    """Non-evidence parse summary for one structured LLM response."""

    accepted_candidate_ids: list[str] = Field(default_factory=list)
    rejected_candidates: list[dict[str, Any]] = Field(default_factory=list)
    max_candidates: int = Field(ge=1)
    truncated: bool = False
    fake: bool = False
    is_verification_evidence: bool = False


class LLMGenerationTrace(StrictModel):
    """Sanitized request/response trace retained as non-evidence provenance context."""

    request: dict[str, Any]
    raw_response: Any
    parse_report: LLMCandidateParseReport
    fake: bool = False
    is_verification_evidence: bool = False

__all__ = [
    "GeneratedSectionDraft",
    "HumanReviewDecision",
    "ReviewerPromptContract",
    "ReviewerValidationResult",
    "LLMReviewerParseResult",
    "LLMReviewerTrace",
    "LLMPromptContract",
    "CandidateValidationResult",
    "LLMCandidateParseReport",
    "LLMGenerationTrace",
]
