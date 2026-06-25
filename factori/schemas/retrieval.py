"""Retrieval context and bounded adequacy schemas."""

from __future__ import annotations

from typing import Any

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
    raw_metadata_hash: str = Field(pattern=HASH_RE.pattern)
    source_provenance: SourceProvenance
    snippet: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    fake: bool = True
    is_verification_evidence: bool = False
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
    "RetrievedDocument",
    "RetrievalValidationResult",
    "RetrievalParseReport",
    "RetrievalRunReport",
    "RetrievalRunTrace",
]
