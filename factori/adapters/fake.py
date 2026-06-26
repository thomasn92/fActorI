"""Deterministic local fake implementations of all adapter protocols."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from statistics import mean
from typing import Any

from factori.hashing import canonical_json, sha256_json, sha256_text
from factori.schemas import (
    BranchStatus,
    Candidate,
    ClaimTable,
    ConstraintSet,
    FakeExperimentResult,
    FakeProofResult,
    GeneratedSectionDraft,
    HumanReviewDecision,
    LiteratureState,
    ManuscriptSectionPlan,
    ProseSectionContract,
    RetrievalAdequacyCertificate,
    RetrievalResult,
    RetrievedDocument,
    ReviewReport,
    SourceProvenance,
)

FAKE_RETRIEVAL_TIMESTAMP = "1970-01-01T00:00:00Z"


@dataclass(frozen=True)
class FakeLLMClient:
    """Template-driven candidate and review adapter with no model calls."""

    backend_name: str = "fake"
    is_fake: bool = True
    external_calls_enabled: bool = False

    def generate_candidates(
        self,
        prompt: str,
        constraints: ConstraintSet,
    ) -> list[Candidate]:
        """Reuse current deterministic Stage A templates."""
        del prompt
        from factori.stage_a import generate_candidates

        return generate_candidates([constraints])

    def review_candidate(
        self,
        candidate: Candidate,
        rubric: Mapping[str, Any],
    ) -> ReviewReport:
        """Return a deterministic fake review without invoking a model."""
        from factori.scoring import score_candidate

        rubric_keys = ", ".join(sorted(str(key) for key in rubric)) or "default"
        score = score_candidate(candidate)
        objections = []
        if score.novelty < 0.5:
            objections.append("fake review: novelty needs sharper differentiation")
        return ReviewReport(
            id=f"fake-review-{candidate.id}",
            reviewer="FakeLLMClient",
            summary=f"Deterministic fake review using rubric keys: {rubric_keys}.",
            scores=score,
            objections=objections,
            recommendation=BranchStatus.ACTIVE,
        )

    def summarize_context(self, context: str | Mapping[str, Any]) -> str:
        """Return a compact deterministic marker, not model-generated prose."""
        serialized = context if isinstance(context, str) else canonical_json(dict(context))
        normalized = " ".join(serialized.split())
        return f"[FAKE CONTEXT SUMMARY] {normalized[:240]}"


@dataclass(frozen=True)
class FakeRetrievalClient:
    """Synthetic retrieval records and adequacy with no search service calls."""

    backend_name: str = "fake"
    is_fake: bool = True
    external_calls_enabled: bool = False

    def search(self, query: str, limit: int) -> list[RetrievalResult]:
        if limit < 0:
            raise ValueError("retrieval limit must be non-negative")
        normalized = " ".join(query.lower().split()) or "empty-query"
        digest = sha256_text(normalized)[:12]
        results = []
        for index in range(limit):
            source_id = f"fake-source-{digest}-{index + 1:03d}"
            raw_hash = sha256_json(
                {"source_id": source_id, "query": normalized, "rank": index}
            )
            provenance = SourceProvenance(
                source_id=source_id,
                provider="fake",
                query=normalized,
                rank=index,
                retrieved_at=FAKE_RETRIEVAL_TIMESTAMP,
                raw_metadata_hash=raw_hash,
            )
            results.append(
                RetrievalResult(
                    source_id=source_id,
                    title=f"Fake retrieval result {index + 1} for {normalized}",
                    provider="fake",
                    retrieved_at=FAKE_RETRIEVAL_TIMESTAMP,
                    query=normalized,
                    rank=index,
                    raw_metadata_hash=raw_hash,
                    source_provenance=provenance,
                    snippet=(
                        "Deterministic placeholder literature result; no source was retrieved."
                    ),
                    score=round(max(0.5, 0.9 - 0.04 * index), 6),
                    metadata={"query": normalized, "rank": index, "backend": "fake"},
                )
            )
        return results

    def fetch(self, source_id: str) -> RetrievedDocument:
        payload = {
            "source_id": source_id,
            "backend": "fake",
            "external_fetch_performed": False,
        }
        return RetrievedDocument(
            source_id=source_id,
            provider="fake",
            title=f"Fake document {source_id}",
            content=(
                "[FAKE RETRIEVED DOCUMENT] No external source was fetched. "
                f"Synthetic source identifier: {source_id}."
            ),
            text_or_abstract=(
                "[FAKE RETRIEVED DOCUMENT] No external source was fetched."
            ),
            raw_payload_hash=sha256_json(payload),
            retrieved_at=FAKE_RETRIEVAL_TIMESTAMP,
            fetch_status="FakeNoFetchPerformed",
            metadata=payload,
        )

    def build_adequacy_certificate(
        self,
        query: str,
        results: list[RetrievalResult],
    ) -> RetrievalAdequacyCertificate:
        from factori.retrieval import compute_retrieval_adequacy

        normalized = " ".join(query.split())
        average_score = (
            mean(result.score or 0.0 for result in results) if results else 0.0
        )
        result_count = len(results)
        literature_state = LiteratureState(
            k=result_count,
            semantic=round(min(1.0, average_score), 6),
            keyword=round(min(1.0, 0.55 + 0.04 * len(normalized.split())), 6)
            if results
            else 0.0,
            citation=round(min(1.0, 0.50 + 0.04 * result_count), 6),
            diversity=round(min(1.0, 0.45 + 0.05 * result_count), 6),
            adversarial=round(min(1.0, 0.40 + 0.05 * result_count), 6),
            novelty_risk=round(max(0.0, 1.0 - average_score), 6),
            closest_priors=[result.source_id for result in results[:3]],
        )
        return compute_retrieval_adequacy(literature_state).model_copy(
            update={
                "provider": "fake",
                "source_count": result_count,
                "limitations": [
                    "Fake retrieval adequacy is a bounded test signal, not novelty proof or "
                    "literature coverage."
                ],
            }
        )


@dataclass(frozen=True)
class FakeProofVerifier:
    """Adapter wrapper around the existing fake proof validator."""

    backend_name: str = "fake"
    is_fake: bool = True
    external_calls_enabled: bool = False

    def verify_proof(
        self,
        candidate: Candidate,
        proof_payload: Mapping[str, Any],
    ) -> FakeProofResult:
        del proof_payload
        from factori.proof_fake import run_fake_proof_validation

        return run_fake_proof_validation(candidate)


@dataclass(frozen=True)
class FakeExperimentRunner:
    """Adapter wrapper around the existing fake synthetic experiment validator."""

    backend_name: str = "fake"
    is_fake: bool = True
    external_calls_enabled: bool = False

    def run_synthetic_experiment(
        self,
        candidate: Candidate,
        experiment_spec: Mapping[str, Any],
    ) -> FakeExperimentResult:
        del experiment_spec
        from factori.experiments_fake import run_fake_synthetic_experiment

        return run_fake_synthetic_experiment(candidate)


@dataclass(frozen=True)
class FakeProseGenerator:
    """Placeholder-only prose adapter that cannot produce polished manuscript text."""

    backend_name: str = "fake"
    is_fake: bool = True
    external_calls_enabled: bool = False

    def generate_section(
        self,
        section_contract: ManuscriptSectionPlan | ProseSectionContract | Mapping[str, Any],
        claim_table: ClaimTable,
    ) -> GeneratedSectionDraft:
        evidence_ids: list[str] = []
        if isinstance(section_contract, ProseSectionContract):
            section_id = section_contract.section_id
            title = section_contract.section_title
            requested_claim_ids = section_contract.allowed_claim_ids
            evidence_ids = list(section_contract.allowed_evidence_artifact_ids)
        elif isinstance(section_contract, ManuscriptSectionPlan):
            section_id = section_contract.section_id
            title = section_contract.title
            requested_claim_ids = section_contract.allowed_claim_ids
        else:
            section_id = str(section_contract.get("section_id", "section-stub"))
            title = str(
                section_contract.get(
                    "section_title",
                    section_contract.get("title", "Section Stub"),
                )
            )
            requested_claim_ids = [
                str(value) for value in section_contract.get("allowed_claim_ids", [])
            ]
            evidence_ids = [
                str(value) for value in section_contract.get("allowed_evidence_artifact_ids", [])
            ]
        known_claim_ids = {claim.claim_id for claim in claim_table.claims}
        claim_ids = sorted(
            claim_id for claim_id in requested_claim_ids if claim_id in known_claim_ids
        )
        if not evidence_ids:
            evidence_ids = sorted(
                {
                    evidence_id
                    for claim in claim_table.claims
                    if claim.claim_id in set(claim_ids)
                    for evidence_id in claim.evidence_artifact_ids
                }
            )
        return GeneratedSectionDraft(
            section_id=section_id,
            title=title,
            content=(
                "[FAKE PROSE DRAFT] "
                f"section_stub={len(section_id)}; claim_count={len(claim_ids)}; "
                f"evidence_count={len(evidence_ids)}; "
                "No scientific label is created or upgraded."
            ),
            claim_ids=claim_ids,
            used_claim_ids=claim_ids,
            used_evidence_artifact_ids=evidence_ids,
            used_citation_ids=[],
            unsupported_sentences=[],
            warnings=["Fake prose draft is a deterministic placeholder, not polished prose."],
        )


@dataclass(frozen=True)
class FakeHumanReviewClient:
    """Explicit no-human-review response for deterministic local runs."""

    backend_name: str = "fake"
    is_fake: bool = True
    external_calls_enabled: bool = False

    def request_review(
        self,
        review_packet: Mapping[str, Any],
    ) -> HumanReviewDecision:
        packet_hash = sha256_text(canonical_json(dict(review_packet)))[:12]
        return HumanReviewDecision(
            request_id=f"fake-human-review-{packet_hash}",
            decision="NoHumanReviewPerformed",
            approved=False,
            reviewer_is_human=False,
            reason="Fake adapter cannot request or claim approval from a real human.",
        )


__all__ = [
    "FakeExperimentRunner",
    "FakeHumanReviewClient",
    "FakeLLMClient",
    "FakeProofVerifier",
    "FakeProseGenerator",
    "FakeRetrievalClient",
]
