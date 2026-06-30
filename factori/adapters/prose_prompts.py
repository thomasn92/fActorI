"""Deterministic prompt construction for one-section prose drafting."""

from __future__ import annotations

from typing import Any

from factori.hashing import canonical_json
from factori.schemas import (
    ClaimTable,
    NarrativeManuscriptContract,
    ProsePromptContract,
    ProseSectionContract,
    VerificationLabel,
)

PROSE_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "section_id",
        "title",
        "draft_markdown",
        "used_claim_ids",
        "used_evidence_artifact_ids",
        "used_citation_ids",
        "used_citation_keys",
        "unsupported_sentences",
        "warnings",
    ],
    "properties": {
        "section_id": {"type": "string"},
        "title": {"type": "string"},
        "draft_markdown": {"type": "string"},
        "used_claim_ids": {"type": "array", "items": {"type": "string"}},
        "used_evidence_artifact_ids": {"type": "array", "items": {"type": "string"}},
        "used_citation_ids": {"type": "array", "items": {"type": "string"}},
        "used_citation_keys": {"type": "array", "items": {"type": "string"}},
        "unsupported_sentences": {"type": "array", "items": {"type": "string"}},
        "warnings": {"type": "array", "items": {"type": "string"}},
    },
}

FORBIDDEN_PROSE_OUTPUTS = [
    "Do not create new scientific claims.",
    "Do not upgrade Conjecture, NegativeResult, or Unsupported labels.",
    "Do not invent theorem or proposition numbering.",
    "Do not invent proof results.",
    "Do not invent experiment results.",
    "Do not invent bibliography keys or citations.",
    "Do not claim exhaustive literature coverage.",
    "Do not claim retrieval proves novelty.",
    "Do not use citations as proof or experiment evidence.",
    "Use accepted registry citations only for bounded background context.",
    "Sources were accepted only as bounded background context after retrieval-quality filtering.",
    "Do not claim the accepted sources exhaust the literature.",
    "Do not claim source relevance proves novelty, correctness, validation, or "
    "publication readiness.",
    "Do not say sources validate, prove, establish novelty, or exhaust the literature.",
    "Use citations only for background/source-context claims.",
    "Place citations in the same sentence or paragraph as the supported claim.",
    "Do not cite every sentence mechanically.",
    "Do not claim empirical or real-world validation from synthetic evidence.",
    "Do not edit the claim table or evidence classification.",
    "Do not add Markdown headings; return paragraphs for the requested section body only.",
]

EVIDENCE_BOUNDARY_INSTRUCTIONS = [
    "Generated prose is not proof evidence.",
    "Generated prose is not experiment evidence.",
    "Generated prose is not retrieval evidence.",
    "Use only allowed claim IDs and evidence artifact IDs.",
    "Use only allowed citation IDs and citation keys.",
    "Preserve all claim labels exactly.",
]


def build_prose_section_prompt(
    section_contract: ProseSectionContract,
    claim_table: ClaimTable,
    evidence_map: dict[str, dict[str, Any]],
    narrative_contract: NarrativeManuscriptContract,
    *,
    backend: str = "fake",
    provider: str = "fake",
) -> ProsePromptContract:
    """Build a deterministic, grounded one-section prose prompt contract."""
    allowed_claim_ids = set(section_contract.allowed_claim_ids)
    allowed_claims = [
        {
            "claim_id": claim.claim_id,
            "candidate_id": claim.candidate_id,
            "claim_text": claim.claim_text,
            "claim_label": claim.claim_label.value,
            "evidence_artifact_ids": list(claim.evidence_artifact_ids),
            "allowed_section": claim.allowed_section,
        }
        for claim in sorted(claim_table.claims, key=lambda item: item.claim_id)
        if claim.claim_id in allowed_claim_ids
    ]
    narrative_context = {
        "contract_id": narrative_contract.contract_id,
        "central_message": narrative_contract.central_message,
        "problem_statement": narrative_contract.problem_statement,
        "main_result_id": narrative_contract.main_result_id,
        "synthetic_study_boundary": narrative_contract.synthetic_study_boundary,
        "empirical_study_boundary": narrative_contract.empirical_study_boundary,
        "section_plan": [
            item
            for item in narrative_contract.section_plan
            if item.get("section_id") == section_contract.section_id
        ],
    }
    prompt_payload = {
        "task": "Draft prose only for the requested manuscript section.",
        "heading_policy": (
            "Do not include Markdown headings in draft_markdown. The assembler owns all "
            "paper headings and will place this section body under the planned heading."
        ),
        "section_contract": section_contract.model_dump(mode="json"),
        "allowed_claims": allowed_claims,
        "evidence_map": {
            key: evidence_map[key]
            for key in sorted(evidence_map)
            if key in set(section_contract.allowed_evidence_artifact_ids)
        },
        "narrative_context": narrative_context,
        "literature_positioning_context": (
            section_contract.literature_positioning_context or {}
        ),
        "allowed_citation_keys": list(section_contract.allowed_citation_keys),
        "citation_policy": section_contract.citation_policy,
        "citation_instructions": list(
            section_contract.citation_boundary_instructions
        ),
        "allowed_statement_classes": list(section_contract.allowed_statement_classes),
        "forbidden_outputs": FORBIDDEN_PROSE_OUTPUTS,
        "evidence_boundary_instructions": EVIDENCE_BOUNDARY_INSTRUCTIONS,
        "requested_output_schema": PROSE_OUTPUT_SCHEMA,
    }
    return ProsePromptContract(
        run_id=section_contract.run_id,
        section_id=section_contract.section_id,
        backend=backend,
        provider=provider,
        section_contract=section_contract,
        allowed_claims=allowed_claims,
        evidence_map=prompt_payload["evidence_map"],
        narrative_context=narrative_context,
        requested_output_schema=PROSE_OUTPUT_SCHEMA,
        forbidden_outputs=FORBIDDEN_PROSE_OUTPUTS,
        evidence_boundary_instructions=EVIDENCE_BOUNDARY_INSTRUCTIONS,
        prompt_text=canonical_json(prompt_payload),
        fake=backend == "fake",
    )


def forbidden_labels_for_section(
    section_contract: ProseSectionContract,
    claim_table: ClaimTable,
) -> list[VerificationLabel]:
    """Return labels that cannot be introduced in the requested section."""
    allowed = {
        claim.claim_label
        for claim in claim_table.claims
        if claim.claim_id in set(section_contract.allowed_claim_ids)
    }
    forbidden = [
        VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED,
        VerificationLabel.EXPERIMENT_VERIFIED,
    ]
    forbidden.extend(label for label in VerificationLabel if label not in allowed)
    forbidden = [label for label in forbidden if label != VerificationLabel.LIMITATION]
    return list(dict.fromkeys(forbidden))


__all__ = [
    "EVIDENCE_BOUNDARY_INSTRUCTIONS",
    "FORBIDDEN_PROSE_OUTPUTS",
    "PROSE_OUTPUT_SCHEMA",
    "build_prose_section_prompt",
    "forbidden_labels_for_section",
]
