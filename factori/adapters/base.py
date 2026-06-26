"""Small protocol interfaces for future fActorI backend adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol, runtime_checkable

from factori.schemas import (
    Candidate,
    ClaimTable,
    ConstraintSet,
    FakeExperimentResult,
    FakeProofResult,
    GeneratedSectionDraft,
    HumanReviewDecision,
    ManuscriptSectionPlan,
    ProseSectionContract,
    RetrievalAdequacyCertificate,
    RetrievalResult,
    RetrievedDocument,
    ReviewerPanelResult,
    ReviewReport,
)
from factori.schemas import (
    ExperimentRunResult as RealExperimentRunResult,
)
from factori.schemas import (
    ProofVerificationResult as RealProofVerificationResult,
)

CandidateLike = Candidate
ReviewerLike = ReviewReport
ProofVerificationResult = FakeProofResult | RealProofVerificationResult
ExperimentRunResult = FakeExperimentResult | RealExperimentRunResult


class AdapterClient(Protocol):
    """Shared metadata required from every adapter implementation."""

    backend_name: str
    is_fake: bool
    external_calls_enabled: bool


@runtime_checkable
class CandidateGenerationClient(AdapterClient, Protocol):
    """Stage A candidate-generation seam."""

    def generate_candidates(
        self,
        prompt: str,
        constraints: ConstraintSet,
    ) -> list[CandidateLike]: ...


@runtime_checkable
class ContextSummarizationClient(AdapterClient, Protocol):
    """Context summarization seam for future backends."""

    def summarize_context(self, context: str | Mapping[str, Any]) -> str: ...


@runtime_checkable
class LLMClient(CandidateGenerationClient, ContextSummarizationClient, Protocol):
    """Backward-compatible aggregate LLM seam."""

    def review_candidate(
        self,
        candidate: Candidate,
        rubric: Mapping[str, Any],
    ) -> ReviewerLike: ...


@runtime_checkable
class RetrievalClient(AdapterClient, Protocol):
    """Search, fetch, and adequacy operations for future retrieval backends."""

    def search(self, query: str, limit: int) -> list[RetrievalResult]: ...

    def fetch(self, source_id: str) -> RetrievedDocument: ...

    def build_adequacy_certificate(
        self,
        query: str,
        results: list[RetrievalResult],
    ) -> RetrievalAdequacyCertificate: ...


@runtime_checkable
class ReviewerClient(AdapterClient, Protocol):
    """Stage B structural-review seam with no verification authority."""

    def review_candidate(
        self,
        candidate: Candidate,
        rubric: Mapping[str, Any],
        retrieval_context: Mapping[str, Any] | None = None,
    ) -> ReviewerPanelResult: ...


@runtime_checkable
class ProofVerifier(AdapterClient, Protocol):
    """Proof-verification seam with fake defaults and explicitly gated local tools."""

    def verify_proof(
        self,
        candidate: Candidate,
        proof_payload: Mapping[str, Any],
    ) -> ProofVerificationResult: ...


@runtime_checkable
class ExperimentRunner(AdapterClient, Protocol):
    """Synthetic experiment seam; no real runner is implemented yet."""

    def run_synthetic_experiment(
        self,
        candidate: Candidate,
        experiment_spec: Mapping[str, Any],
    ) -> ExperimentRunResult: ...


@runtime_checkable
class ProseGenerator(AdapterClient, Protocol):
    """Future prose seam restricted to deterministic stubs in this milestone."""

    def generate_section(
        self,
        section_contract: ManuscriptSectionPlan | ProseSectionContract | Mapping[str, Any],
        claim_table: ClaimTable,
    ) -> GeneratedSectionDraft: ...


@runtime_checkable
class HumanReviewClient(AdapterClient, Protocol):
    """Future human-review seam that cannot imply approval without a real human."""

    def request_review(
        self,
        review_packet: Mapping[str, Any],
    ) -> HumanReviewDecision: ...


__all__ = [
    "AdapterClient",
    "CandidateGenerationClient",
    "CandidateLike",
    "ContextSummarizationClient",
    "ExperimentRunResult",
    "ExperimentRunner",
    "HumanReviewClient",
    "LLMClient",
    "ProofVerificationResult",
    "ProofVerifier",
    "ProseGenerator",
    "RetrievalClient",
    "ReviewerClient",
    "ReviewerLike",
]
