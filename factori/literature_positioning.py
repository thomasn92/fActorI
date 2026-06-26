"""Bounded literature-positioning contracts for manuscript drafts."""

from __future__ import annotations

from factori.hashing import sha256_json
from factori.schemas import (
    CitationRegistry,
    LiteratureGapStatement,
    LiteraturePositioningContract,
    LiteraturePositioningReport,
    NarrativeManuscriptContract,
)

NON_EXHAUSTIVENESS_DISCLAIMER = (
    "Retrieval is bounded context, not exhaustive literature coverage; retrieval adequacy "
    "is not proof of novelty."
)


def build_literature_positioning_contract(
    *,
    run_id: str,
    narrative_contract: NarrativeManuscriptContract,
    citation_registry: CitationRegistry,
) -> LiteraturePositioningContract:
    """Build a deterministic, non-verifying literature-positioning contract."""
    included = [record.citation_id for record in citation_registry.citations]
    queries = sorted(
        {
            query
            for record in citation_registry.citations
            for query in [record.source_artifact_id or ""]
            if query
        }
    )
    problem = (
        narrative_contract.problem_statement
        or narrative_contract.central_message
        or "The problem context is unavailable in the narrative contract."
    )
    gap = (
        narrative_contract.literature_gap
        or "The literature gap is bounded by the available retrieval metadata."
    )
    novelty = (
        narrative_contract.novelty_claim
        or "Novelty positioning is a manuscript claim, not a retrieval guarantee."
    )
    return LiteraturePositioningContract(
        run_id=run_id,
        contract_id=f"literature-positioning-{sha256_json([run_id, included])[:12]}",
        problem_context=problem,
        retrieval_queries_used=queries,
        included_citation_ids=included,
        excluded_or_deferred_sources=[],
        literature_gap_statement=gap,
        novelty_positioning_statement=(
            f"{novelty} Retrieval metadata may frame context but cannot prove novelty."
        ),
        coverage_limitations=[
            NON_EXHAUSTIVENESS_DISCLAIMER,
            "Citations support background and positioning only.",
            "Citations do not verify mathematical or experimental claims.",
        ],
        non_exhaustiveness_disclaimer=NON_EXHAUSTIVENESS_DISCLAIMER,
    )


def build_literature_positioning_report(
    *,
    run_id: str,
    citation_registry: CitationRegistry,
    narrative_contract: NarrativeManuscriptContract,
) -> LiteraturePositioningReport:
    """Build a citation-safe literature-positioning report for draft assembly."""
    contract = build_literature_positioning_contract(
        run_id=run_id,
        narrative_contract=narrative_contract,
        citation_registry=citation_registry,
    )
    keys = [record.citation_key for record in citation_registry.citations[:3]]
    citation_sentence = " ".join(f"[@{key}]" for key in keys)
    if citation_sentence:
        intro = (
            f"The manuscript positions this problem against bounded retrieval context "
            f"{citation_sentence}. {contract.non_exhaustiveness_disclaimer}"
        )
    else:
        intro = (
            "No retrieval-backed citations are available for this draft. "
            f"{contract.non_exhaustiveness_disclaimer}"
        )
    gap = LiteratureGapStatement(
        statement_id=f"literature-gap-{sha256_json(contract.model_dump(mode='json'))[:12]}",
        problem_context=contract.problem_context,
        statement=contract.literature_gap_statement,
        citation_ids=contract.included_citation_ids[:3],
        citation_keys=keys,
        limitations=list(contract.coverage_limitations),
    )
    limitations = (
        "Literature positioning is bounded by available retrieval metadata. It is not "
        "exhaustive coverage, not proof of novelty, and not verification evidence."
    )
    return LiteraturePositioningReport(
        run_id=run_id,
        citation_registry_id=f"citation-registry-{citation_registry.source_registry_hash[:12]}",
        contract=contract,
        gap_statement=gap,
        markdown_intro_paragraph=intro,
        literature_limitations_paragraph=limitations,
        citation_keys_used=keys,
        warnings=list(citation_registry.warnings),
    )


__all__ = [
    "NON_EXHAUSTIVENESS_DISCLAIMER",
    "build_literature_positioning_contract",
    "build_literature_positioning_report",
]
