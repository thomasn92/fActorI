"""Retrieval context and bounded adequacy schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from factori.schemas.base import HASH_RE, StrictModel
from factori.schemas.enums import BranchStatus


class RetrievalAdequacyCertificate(StrictModel):
    """Skeleton retrieval adequacy certificate."""

    semantic: float = Field(ge=0.0, le=1.0)
    keyword: float = Field(ge=0.0, le=1.0)
    citation: float = Field(ge=0.0, le=1.0)
    diversity: float = Field(ge=0.0, le=1.0)
    adversarial: float = Field(ge=0.0, le=1.0)
    weights: dict[str, float]
    rho_adequacy: float = Field(ge=0.0, le=1.0)
    tau_adequacy: float = Field(ge=0.0, le=1.0)
    passed: bool
    status: BranchStatus
    fake: bool = True
    provider: str | None = None
    source_count: int = Field(default=0, ge=0)
    bounded_signal: bool = True
    proves_novelty: bool = False
    claims_literature_coverage: bool = False
    limitations: list[str] = Field(
        default_factory=lambda: [
            "Retrieval adequacy is a bounded signal, not proof of novelty or literature coverage."
        ]
    )


class RetrievalQuery(StrictModel):
    """Deterministic provider query contract retained as non-evidence context."""

    query_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    limit: int = Field(ge=1)
    endpoint: str = Field(min_length=1)
    parameters: dict[str, Any] = Field(default_factory=dict)
    requires_credentials: bool = True
    fake: bool = False
    is_verification_evidence: bool = False
    proves_novelty: bool = False


class SourceProvenance(StrictModel):
    """Source-level provenance that cannot confer a verification label."""

    source_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    query: str = Field(min_length=1)
    rank: int = Field(ge=0)
    retrieved_at: str = Field(min_length=1)
    raw_metadata_hash: str = Field(pattern=HASH_RE.pattern)
    url: str | None = None
    doi: str | None = None
    is_verification_evidence: bool = False
    proves_novelty: bool = False
    claims_literature_coverage: bool = False


class RetrievalResult(StrictModel):
    """One normalized retrieval result used only for literature context."""

    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    year: int | None = Field(default=None, ge=0)
    venue: str | None = None
    abstract: str | None = None
    url: str | None = None
    doi: str | None = None
    provider: str = Field(min_length=1)
    retrieved_at: str = Field(min_length=1)
    query: str = Field(min_length=1)
    rank: int = Field(ge=0)
    score: float | None = Field(default=None, ge=0.0, le=1.0)
    relevance_score: float | None = Field(default=None, ge=0.0, le=1.0)
    topic_match_score: float | None = Field(default=None, ge=0.0, le=1.0)
    source_quality_score: float | None = Field(default=None, ge=0.0, le=1.0)
    accepted_for_registry: bool = True
    rejection_reason: str | None = None
    source_type: str = "retrieval_metadata"
    retrieval_backend: str = "unknown"
    fixture_only: bool = False
    raw_metadata_hash: str = Field(pattern=HASH_RE.pattern)
    source_provenance: SourceProvenance
    snippet: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    fake: bool = True
    is_verification_evidence: bool = False
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    proves_novelty: bool = False
    claims_literature_coverage: bool = False


class RetrievedDocument(StrictModel):
    """Fetched source metadata or abstract, never claim-verification evidence."""

    source_id: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    title: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)
    text_or_abstract: str | None = None
    content: str = ""
    raw_payload_hash: str = Field(pattern=HASH_RE.pattern)
    retrieved_at: str = Field(min_length=1)
    fetch_status: str = Field(min_length=1)
    fake: bool = True
    is_verification_evidence: bool = False
    proves_novelty: bool = False


class RetrievalValidationResult(StrictModel):
    """Deterministic safety result for one normalized retrieval source."""

    source_id: str | None = None
    valid: bool
    reasons: list[str] = Field(default_factory=list)


class RetrievalParseReport(StrictModel):
    """Non-evidence summary of provider response normalization."""

    provider: str = Field(min_length=1)
    raw_response_hash: str = Field(pattern=HASH_RE.pattern)
    accepted_source_ids: list[str] = Field(default_factory=list)
    rejected_results: list[dict[str, Any]] = Field(default_factory=list)
    truncated: bool = False
    fake: bool = False
    is_verification_evidence: bool = False


class RetrievalRunReport(StrictModel):
    """Bounded retrieval run context; this is not novelty or verification proof."""

    query: RetrievalQuery
    results: list[RetrievalResult]
    parse_report: RetrievalParseReport
    certificate: RetrievalAdequacyCertificate
    backend: str = Field(min_length=1)
    provider: str = Field(min_length=1)
    config_metadata: dict[str, Any] = Field(default_factory=dict)
    fake: bool = False
    is_verification_evidence: bool = False
    proves_novelty: bool = False
    claims_literature_coverage: bool = False


SourceRelevanceLabel = Literal[
    "highly_relevant_background",
    "partially_relevant_background",
    "weakly_relevant",
    "irrelevant",
    "metadata_insufficient",
    "duplicate",
    "unsafe_or_invalid_source",
]


class SourceRelevanceAdjudication(StrictModel):
    """Bounded source-relevance judgment; never verification evidence."""

    source_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    source_hash: str = Field(pattern=HASH_RE.pattern)
    query: str = Field(min_length=1)
    domain: str = Field(min_length=1)
    candidate_title_or_problem: str = Field(min_length=1)
    deterministic_relevance_score: float = Field(ge=0.0, le=1.0)
    adjudicated_relevance_label: SourceRelevanceLabel
    adjudicated_relevance_score: float = Field(ge=0.0, le=1.0)
    accepted_for_background_context: bool
    rejection_reason: str | None = None
    reasoning_brief: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    adjudicator_backend: str = Field(min_length=1)
    adjudicator_model: str | None = None
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    proves_novelty: bool = False
    claims_literature_coverage: bool = False


RetrievalAdequacyStatus = Literal[
    "not_evaluated",
    "insufficient_sources",
    "bounded_context_only",
    "adequate_for_background_context",
]


class RetrievalQualityReport(StrictModel):
    """Deterministic source-quality filter report; never verification evidence."""

    run_id: str = Field(min_length=1)
    retrieval_backend: str = Field(min_length=1)
    total_retrieved_sources: int = Field(ge=0)
    accepted_source_count: int = Field(ge=0)
    rejected_source_count: int = Field(ge=0)
    duplicate_count: int = Field(default=0, ge=0)
    low_relevance_count: int = Field(default=0, ge=0)
    metadata_incomplete_count: int = Field(default=0, ge=0)
    mean_relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    min_relevance_score: float = Field(default=0.0, ge=0.0, le=1.0)
    queries_used: list[str] = Field(default_factory=list)
    coverage_limitations: list[str] = Field(default_factory=list)
    adequacy_status: RetrievalAdequacyStatus = "not_evaluated"
    source_relevance_adjudication_enabled: bool = False
    source_relevance_adjudicator_backend: str = "off"
    source_relevance_adjudicator_model: str | None = None
    source_relevance_adjudication_calls: int = Field(default=0, ge=0)
    adjudicated_source_count: int = Field(default=0, ge=0)
    deterministic_accept_count: int = Field(default=0, ge=0)
    deterministic_reject_count: int = Field(default=0, ge=0)
    llm_accepted_count: int = Field(default=0, ge=0)
    llm_rejected_count: int = Field(default=0, ge=0)
    hard_reject_count: int = Field(default=0, ge=0)
    adjudication_items: list[SourceRelevanceAdjudication] = Field(default_factory=list)
    accepted_source_ids: list[str] = Field(default_factory=list)
    rejected_source_ids: list[str] = Field(default_factory=list)
    rejection_reasons: dict[str, str] = Field(default_factory=dict)
    creates_scientific_validation: bool = False
    implies_publication_readiness: bool = False
    is_verification_evidence: bool = False
    proves_novelty: bool = False
    claims_literature_coverage: bool = False


class RetrievalRunTrace(StrictModel):
    """Sanitized provider trace used to write retrieval provenance artifacts."""

    query: RetrievalQuery
    raw_response: Any
    parse_report: RetrievalParseReport
    results: list[RetrievalResult]
    fake: bool = False
    is_verification_evidence: bool = False

__all__ = [
    "RetrievalAdequacyCertificate",
    "RetrievalQuery",
    "SourceProvenance",
    "RetrievalResult",
    "RetrievalQualityReport",
    "RetrievedDocument",
    "RetrievalValidationResult",
    "RetrievalParseReport",
    "RetrievalRunReport",
    "RetrievalRunTrace",
    "SourceRelevanceAdjudication",
    "SourceRelevanceLabel",
]
