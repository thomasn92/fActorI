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
    RetrievalAdequacyCertificate,
    RetrievalResult,
    RetrievedDocument,
    ReviewReport,
)

CandidateLike = Candidate
ReviewerLike = ReviewReport
ProofVerificationResult = FakeProofResult
ExperimentRunResult = FakeExperimentResult


class AdapterClient(Protocol):
    """Shared metadata required from every adapter implementation."""

    backend_name: str
    is_fake: bool
    external_calls_enabled: bool


@runtime_checkable
class LLMClient(AdapterClient, Protocol):
    """Candidate, review, and context operations for a future model backend."""

    def generate_candidates(
        self,
        prompt: str,
        constraints: ConstraintSet,
    ) -> list[CandidateLike]: ...

    def review_candidate(
        self,
        candidate: Candidate,
        rubric: Mapping[str, Any],
    ) -> ReviewerLike: ...

    def summarize_context(self, context: str | Mapping[str, Any]) -> str: ...


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
class ProofVerifier(AdapterClient, Protocol):
    """Proof-verification seam; no real prover is implemented yet."""

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
        section_contract: ManuscriptSectionPlan | Mapping[str, Any],
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
    "CandidateLike",
    "ExperimentRunResult",
    "ExperimentRunner",
    "HumanReviewClient",
    "LLMClient",
    "ProofVerificationResult",
    "ProofVerifier",
    "ProseGenerator",
    "RetrievalClient",
    "ReviewerLike",
]
