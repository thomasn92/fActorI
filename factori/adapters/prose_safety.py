"""Parsing and safety checks for generated manuscript prose."""

from __future__ import annotations

import json
import re
from typing import Any

from factori.adapters.errors import AdapterResponseParseError
from factori.schemas import (
    Claim,
    ClaimTable,
    GeneratedSectionDraft,
    ProseGenerationParseResult,
    ProseSafetyReport,
    ProseSectionContract,
    VerificationLabel,
)

_NUMBERED_THEOREM_RE = re.compile(r"\b(theorem|proposition)\s+\d+(\.\d+)?\b", re.I)
_EMPIRICAL_VALIDATION_PHRASES = (
    "empirical validation",
    "empirically validated",
    "real-world validation",
    "real world validation",
    "validated on real data",
)
_ASSERTIVE_CLAIM_PHRASES = (
    "we prove",
    "we show",
    "we demonstrate",
    "we establish",
)


def parse_prose_generation_response(raw_response: Any) -> ProseGenerationParseResult:
    """Parse a structured prose response without trusting it."""
    try:
        payload = _payload(raw_response)
    except AdapterResponseParseError as exc:
        return ProseGenerationParseResult(
            rejected=True,
            reasons=[str(exc)],
            raw_response_type=type(raw_response).__name__,
        )
    missing = [
        key
        for key in (
            "section_id",
            "title",
            "draft_markdown",
            "used_claim_ids",
            "used_evidence_artifact_ids",
            "used_citation_ids",
            "unsupported_sentences",
            "warnings",
        )
        if key not in payload
    ]
    if missing:
        return ProseGenerationParseResult(
            rejected=True,
            reasons=[f"missing required prose response fields: {', '.join(missing)}"],
            raw_response_type=type(raw_response).__name__,
        )
    try:
        draft = GeneratedSectionDraft(
            section_id=str(payload["section_id"]),
            title=str(payload["title"]),
            content=str(payload["draft_markdown"]),
            claim_ids=[str(value) for value in payload.get("used_claim_ids", [])],
            used_claim_ids=[str(value) for value in payload.get("used_claim_ids", [])],
            used_evidence_artifact_ids=[
                str(value) for value in payload.get("used_evidence_artifact_ids", [])
            ],
            used_citation_ids=[str(value) for value in payload.get("used_citation_ids", [])],
            unsupported_sentences=[
                str(value) for value in payload.get("unsupported_sentences", [])
            ],
            warnings=[str(value) for value in payload.get("warnings", [])],
            polished=False,
            fake=False,
            is_verification_evidence=False,
        )
    except (TypeError, ValueError) as exc:
        return ProseGenerationParseResult(
            rejected=True,
            reasons=[f"invalid prose response shape: {exc}"],
            raw_response_type=type(raw_response).__name__,
        )
    return ProseGenerationParseResult(
        section_draft=draft,
        raw_response_type=type(raw_response).__name__,
    )


def validate_generated_section(
    section_draft: GeneratedSectionDraft,
    section_contract: ProseSectionContract,
    claim_table: ClaimTable,
    evidence_map: dict[str, dict[str, Any]],
) -> ProseSafetyReport:
    """Validate generated prose against section, claim, and evidence contracts."""
    reasons: list[str] = []
    warnings: list[str] = []
    if section_draft.section_id != section_contract.section_id:
        reasons.append("section_id does not match section contract")
    allowed_claim_ids = set(section_contract.allowed_claim_ids)
    allowed_evidence_ids = set(section_contract.allowed_evidence_artifact_ids)
    allowed_citation_ids = set(section_contract.allowed_citation_ids)
    used_claim_ids = sorted(set(section_draft.used_claim_ids or section_draft.claim_ids))
    used_evidence_ids = sorted(set(section_draft.used_evidence_artifact_ids))
    used_citation_ids = sorted(set(section_draft.used_citation_ids))

    unknown_claim_ids = sorted(set(used_claim_ids) - allowed_claim_ids)
    if unknown_claim_ids:
        reasons.append(f"unknown or disallowed claim IDs: {', '.join(unknown_claim_ids)}")
    unknown_evidence_ids = sorted(set(used_evidence_ids) - allowed_evidence_ids)
    if unknown_evidence_ids:
        reasons.append(
            f"unknown or disallowed evidence artifact IDs: {', '.join(unknown_evidence_ids)}"
        )
    missing_evidence_records = sorted(
        evidence_id for evidence_id in used_evidence_ids if evidence_id not in evidence_map
    )
    if missing_evidence_records:
        reasons.append(
            f"evidence IDs missing from evidence map: {', '.join(missing_evidence_records)}"
        )
    unknown_citation_ids = sorted(set(used_citation_ids) - allowed_citation_ids)
    if unknown_citation_ids:
        reasons.append(f"unknown or invented citation IDs: {', '.join(unknown_citation_ids)}")
    if section_draft.unsupported_sentences:
        reasons.append("generated prose contains unsupported sentences")

    claims_by_id = {claim.claim_id: claim for claim in claim_table.claims}
    used_claims = [
        claims_by_id[claim_id]
        for claim_id in used_claim_ids
        if claim_id in claims_by_id
    ]
    text = section_draft.content
    lowered = text.lower()
    label_inflation = _label_inflation_reasons(text, used_claims, section_contract)
    reasons.extend(label_inflation)
    if any(phrase in lowered for phrase in _EMPIRICAL_VALIDATION_PHRASES) and any(
        claim.claim_label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
        for claim in used_claims
    ):
        reasons.append("synthetic evidence is described as empirical or real-world validation")
    if _NUMBERED_THEOREM_RE.search(text) and not _numbering_allowed(text, section_contract):
        reasons.append("invented theorem or proposition numbering is not allowed")
    if not used_claim_ids and any(phrase in lowered for phrase in _ASSERTIVE_CLAIM_PHRASES):
        reasons.append("assertive scientific prose appears without a linked claim_id")
    words = [word for word in text.split() if word.strip()]
    if len(words) > section_contract.max_words:
        warnings.append(
            f"word limit exceeded: {len(words)} words > {section_contract.max_words}"
        )
    created_or_upgraded = any(
        "label" in reason.lower()
        or "LeanVerified" in reason
        or "SyntheticExperimentVerified" in reason
        or "RealDataExperimentVerified" in reason
        for reason in reasons
    )
    return ProseSafetyReport(
        section_id=section_contract.section_id,
        safe=not reasons,
        rejected=bool(reasons),
        reasons=sorted(set(reasons)),
        warnings=sorted(set([*warnings, *section_draft.warnings])),
        used_claim_ids=used_claim_ids,
        used_evidence_artifact_ids=used_evidence_ids,
        used_citation_ids=used_citation_ids,
        created_or_upgraded_labels=created_or_upgraded,
    )


def _payload(raw_response: Any) -> dict[str, Any]:
    if isinstance(raw_response, str):
        try:
            parsed = json.loads(raw_response)
        except json.JSONDecodeError as exc:
            raise AdapterResponseParseError(
                backend="prose",
                provider="unknown",
                operation="parse_prose_response",
                message="prose response is not valid JSON",
                cause=exc,
            ) from exc
    else:
        parsed = raw_response
    if not isinstance(parsed, dict):
        raise AdapterResponseParseError(
            backend="prose",
            provider="unknown",
            operation="parse_prose_response",
            message="prose response must be a JSON object",
        )
    return parsed


def _label_inflation_reasons(
    text: str,
    used_claims: list[Claim],
    section_contract: ProseSectionContract,
) -> list[str]:
    reasons: list[str] = []
    labels_by_claim = {claim.claim_id: claim.claim_label for claim in used_claims}
    labels_used = set(labels_by_claim.values())
    for label in section_contract.forbidden_labels:
        if label.value.lower() in text.lower():
            reasons.append(f"forbidden label appears in generated prose: {label.value}")
    if "LeanVerified" in text and VerificationLabel.LEAN_VERIFIED not in labels_used:
        reasons.append("LeanVerified text appears without a LeanVerified allowed claim")
    if (
        "SyntheticExperimentVerified" in text
        and VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED not in labels_used
    ):
        reasons.append(
            "SyntheticExperimentVerified text appears without a SyntheticExperimentVerified "
            "allowed claim"
        )
    if "RealDataExperimentVerified" in text:
        reasons.append("RealDataExperimentVerified is not allowed in the MVP")
    return reasons


def _numbering_allowed(text: str, section_contract: ProseSectionContract) -> bool:
    allowed = " ".join(section_contract.required_subsections).lower()
    match = _NUMBERED_THEOREM_RE.search(text)
    return bool(match and match.group(0).lower() in allowed)


__all__ = [
    "parse_prose_generation_response",
    "validate_generated_section",
]
