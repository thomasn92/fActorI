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
_FORMAL_RESULT_LABEL_RE = re.compile(
    r"\b(theorem|lemma|proposition|corollary|conjecture|verifiedtheorem|"
    r"empiricalresult|validatedresult)\b",
    re.I,
)
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
_CITATION_MARKER_RE = re.compile(r"\[@([A-Za-z0-9][A-Za-z0-9_.:-]*)\]")
_EXHAUSTIVE_LITERATURE_PHRASES = (
    "exhaustive literature coverage",
    "complete literature coverage",
    "covers all prior work",
    "all prior work",
    "comprehensive literature review",
    "exhaustive review",
)
_RETRIEVAL_AS_PROOF_PHRASES = (
    "retrieval proves novelty",
    "retrieval proves",
    "citations prove novelty",
    "citation proves novelty",
    "novelty is proven by retrieval",
    "novelty is proven",
    "retrieval as proof",
    "citation as proof",
    "citations are proof evidence",
    "citations are experiment evidence",
)
_PUBLICATION_READY_PHRASES = (
    "publication ready",
    "publication-ready",
    "ready for publication",
    "suitable for publication",
    "publishable as-is",
)
_EXTERNAL_FACT_PHRASES = (
    "studies show",
    "prior work shows",
    "the literature shows",
    "field data show",
    "survey data show",
    "according to",
)
_NEGATION_CUES = (
    "not",
    "no",
    "without",
    "does not",
    "do not",
    "cannot",
    "can't",
    "unavailable",
    "lacks",
    "lack",
    "absence of",
    "missing",
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
            used_citation_keys=[
                str(value) for value in payload.get("used_citation_keys", [])
            ],
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
    allowed_citation_keys = set(section_contract.allowed_citation_keys)
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
    claims_by_id = {claim.claim_id: claim for claim in claim_table.claims}
    used_claims = [
        claims_by_id[claim_id]
        for claim_id in used_claim_ids
        if claim_id in claims_by_id
    ]
    text = section_draft.content
    sanitized = _sanitize_section_text(
        text=text,
        unsupported_sentences=section_draft.unsupported_sentences,
        section_contract=section_contract,
        used_claims=used_claims,
        has_linked_claim=bool(used_claim_ids),
    )
    marker_keys = set(_CITATION_MARKER_RE.findall(sanitized["sanitized_content"]))
    used_citation_keys = sorted(set(section_draft.used_citation_keys) | marker_keys)

    unknown_citation_ids = sorted(set(used_citation_ids) - allowed_citation_ids)
    if unknown_citation_ids:
        reasons.append(f"unknown or invented citation IDs: {', '.join(unknown_citation_ids)}")
    unknown_citation_keys = sorted(set(used_citation_keys) - allowed_citation_keys)
    if unknown_citation_keys:
        reasons.append(
            f"unknown or invented citation keys: {', '.join(unknown_citation_keys)}"
        )
    if not sanitized["sanitized_content"].strip():
        reasons.extend(sanitized["removal_reasons"])
        reasons.append("no safe prose content remains after safety filtering")
    if _NUMBERED_THEOREM_RE.search(sanitized["sanitized_content"]) and not _numbering_allowed(
        sanitized["sanitized_content"],
        section_contract,
    ):
        reasons.append("invented theorem or proposition numbering is not allowed")
    lowered = sanitized["sanitized_content"].lower()
    if _contains_unbounded_claim(lowered, _EXHAUSTIVE_LITERATURE_PHRASES):
        reasons.append("generated prose claims exhaustive literature coverage")
    if _contains_unbounded_claim(lowered, _RETRIEVAL_AS_PROOF_PHRASES):
        reasons.append("generated prose treats retrieval or citations as novelty/proof evidence")
    words = [word for word in sanitized["sanitized_content"].split() if word.strip()]
    if len(words) > section_contract.max_words:
        warnings.append(
            f"word limit exceeded: {len(words)} words > {section_contract.max_words}"
        )
    if sanitized["unsafe_sentences_removed"]:
        warnings.append("unsafe prose sentences were removed before assembly")
    created_or_upgraded = any(
        "label" in reason.lower()
        or "LeanVerified" in reason
        or "SyntheticExperimentVerified" in reason
        or "RealDataExperimentVerified" in reason
        for reason in [*reasons, *sanitized["removal_reasons"]]
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
        used_citation_keys=used_citation_keys,
        allowed_statement_classes_used=sorted(
            set(sanitized["allowed_statement_classes_used"])
        ),
        safe_scaffold_sentences_retained=sanitized["safe_scaffold_sentences_retained"],
        unsafe_sentences_removed=sanitized["unsafe_sentences_removed"],
        sanitized_content=sanitized["sanitized_content"],
        original_sentence_count=sanitized["original_sentence_count"],
        removed_sentence_count=len(sanitized["unsafe_sentences_removed"]),
        retained_sentence_count=sanitized["retained_sentence_count"],
        section_status=(
            "omitted"
            if reasons and not sanitized["sanitized_content"].strip()
            else "partially_sanitized"
            if sanitized["unsafe_sentences_removed"]
            else "retained"
        ),
        removal_reasons=sorted(set(sanitized["removal_reasons"])),
        forbidden_labels_detected=sorted(set(sanitized["forbidden_labels_detected"])),
        forbidden_labels_allowed_as_scaffold=sorted(
            set(sanitized["forbidden_labels_allowed_as_scaffold"])
        ),
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
        if label == VerificationLabel.LIMITATION:
            continue
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


def _sanitize_section_text(
    *,
    text: str,
    unsupported_sentences: list[str],
    section_contract: ProseSectionContract,
    used_claims: list[Claim],
    has_linked_claim: bool,
) -> dict[str, Any]:
    unsupported = {_normalize_sentence(sentence) for sentence in unsupported_sentences}
    paragraphs = _split_paragraph_sentences(text)
    retained_paragraphs: list[str] = []
    safe_scaffold_sentences: list[str] = []
    unsafe_sentences_removed: list[str] = []
    removal_reasons: list[str] = []
    classes_used: list[str] = []
    forbidden_labels_detected: list[str] = []
    forbidden_labels_allowed_as_scaffold: list[str] = []
    original_count = 0
    retained_count = 0
    for paragraph in paragraphs:
        retained_sentences: list[str] = []
        for sentence in paragraph:
            normalized = _normalize_sentence(sentence)
            if not normalized:
                continue
            original_count += 1
            sentence_classes = _statement_classes(sentence)
            allowed_classes = sorted(
                sentence_classes.intersection(section_contract.allowed_statement_classes)
            )
            unsafe_reasons = _unsafe_sentence_reasons(
                sentence=sentence,
                used_claims=used_claims,
                section_contract=section_contract,
                has_linked_claim=has_linked_claim,
            )
            forbidden_labels_detected.extend(
                _forbidden_labels_in_sentence(sentence, section_contract)
            )
            if (
                VerificationLabel.LIMITATION.value in _forbidden_labels_in_sentence(
                    sentence,
                    section_contract,
                    include_allowed_scaffold=True,
                )
                and allowed_classes
            ):
                forbidden_labels_allowed_as_scaffold.append(
                    VerificationLabel.LIMITATION.value
                )
            if normalized in unsupported and not allowed_classes:
                unsafe_reasons.append(
                    "unsupported sentence is not covered by allowed statement classes"
                )
            if unsafe_reasons:
                unsafe_sentences_removed.append(sentence.strip())
                removal_reasons.extend(unsafe_reasons)
                continue
            retained_sentences.append(sentence.strip())
            retained_count += 1
            if allowed_classes:
                classes_used.extend(allowed_classes)
                safe_scaffold_sentences.append(sentence.strip())
        if retained_sentences:
            retained_paragraphs.append(" ".join(retained_sentences))
    return {
        "sanitized_content": "\n\n".join(retained_paragraphs).strip(),
        "safe_scaffold_sentences_retained": safe_scaffold_sentences,
        "unsafe_sentences_removed": unsafe_sentences_removed,
        "allowed_statement_classes_used": classes_used,
        "original_sentence_count": original_count,
        "retained_sentence_count": retained_count,
        "removal_reasons": removal_reasons,
        "forbidden_labels_detected": forbidden_labels_detected,
        "forbidden_labels_allowed_as_scaffold": forbidden_labels_allowed_as_scaffold,
    }


def _split_paragraph_sentences(text: str) -> list[list[str]]:
    paragraphs: list[list[str]] = []
    for paragraph in re.split(r"\n\s*\n", text.strip()):
        sentences = [
            match.group(0).strip()
            for match in re.finditer(r"[^.!?]+(?:[.!?]+|$)", paragraph.strip(), re.S)
            if match.group(0).strip()
        ]
        if sentences:
            paragraphs.append(sentences)
    return paragraphs


def _normalize_sentence(sentence: str) -> str:
    return " ".join(sentence.split()).casefold().strip()


def _statement_classes(sentence: str) -> set[str]:
    lowered = sentence.casefold()
    classes: set[str] = set()
    if any(
        phrase in lowered
        for phrase in (
            "problem statement",
            "research problem",
            "problem addressed",
            "problem framing",
            "the problem",
        )
    ):
        classes.add("problem_framing")
    if any(phrase in lowered for phrase in ("motivation", "why", "matters")):
        classes.add("motivation_statement")
    if any(
        phrase in lowered
        for phrase in (
            "central contribution",
            "contribution of this draft",
            "this draft contributes",
        )
    ):
        classes.add("central_contribution_summary")
    if any(
        phrase in lowered
        for phrase in (
            "method summary",
            "method summarized",
            "mechanical summary",
            "method",
            "model",
            "approach",
        )
    ):
        classes.add("method_description")
    if any(
        phrase in lowered
        for phrase in (
            "pipeline",
            "assembles",
            "constructs",
            "drafts sections",
            "safety repair",
            "audit reports",
        )
    ):
        classes.add("pipeline_description")
    if any(
        phrase in lowered
        for phrase in (
            "evidence boundary",
            "evidence boundaries",
            "not proof evidence",
            "not verification evidence",
            "cannot create evidence",
            "does not create evidence",
            "does not provide proof",
            "does not provide empirical validation",
            "cannot upgrade",
        )
    ):
        classes.add("evidence_boundary_statement")
    if any(
        phrase in lowered
        for phrase in (
            "limitation",
            "limitations",
            "lacks",
            "absence of",
            "missing",
            "unavailable",
            "does not provide",
            "does not establish",
        )
    ):
        classes.add("limitation_statement")
    if any(
        phrase in lowered
        for phrase in (
            "missing retrieval",
            "proof artifacts",
            "experiment artifacts",
            "human validation",
            "human approval",
        )
    ):
        classes.add("missing_evidence_statement")
    if any(
        phrase in lowered
        for phrase in (
            "demonstration status",
            "current artifact",
            "mvp run",
            "records demonstration",
            "procedural rather than evidential",
        )
    ):
        classes.add("demonstration_status_statement")
    if any(phrase in lowered for phrase in ("provenance", "audit", "ledger", "artifact", "run id")):
        classes.add("provenance_statement")
    if any(
        phrase in lowered
        for phrase in (
            "no retrieval-backed citations",
            "citation markers are omitted",
            "citations are unavailable",
            "no citation",
        )
    ):
        classes.add("citation_status_statement")
    if any(
        phrase in lowered
        for phrase in (
            "non-evidence",
            "non-evidential",
            "not scientific validation",
            "not publication readiness",
            "human-review-only",
            "presentation-only",
        )
    ):
        classes.add("non_evidence_disclaimer")
    return classes


def _unsafe_sentence_reasons(
    *,
    sentence: str,
    used_claims: list[Claim],
    section_contract: ProseSectionContract,
    has_linked_claim: bool,
) -> list[str]:
    lowered = sentence.casefold()
    reasons: list[str] = []
    reasons.extend(_label_inflation_reasons(sentence, used_claims, section_contract))
    if _formal_label_without_negation(sentence):
        reasons.append("formal result label appears without proof evidence")
    if _NUMBERED_THEOREM_RE.search(sentence) and not _numbering_allowed(
        sentence,
        section_contract,
    ):
        reasons.append("invented theorem or proposition numbering is not allowed")
    if _unsafe_phrase_present(lowered, _EMPIRICAL_VALIDATION_PHRASES):
        reasons.append("empirical or real-world validation is claimed without evidence")
    if _unsafe_phrase_present(lowered, _PUBLICATION_READY_PHRASES):
        reasons.append("publication-readiness language is not allowed")
    if not has_linked_claim and any(phrase in lowered for phrase in _ASSERTIVE_CLAIM_PHRASES):
        reasons.append("assertive scientific prose appears without a linked claim_id")
    if _contains_unbounded_claim(lowered, _EXHAUSTIVE_LITERATURE_PHRASES):
        reasons.append("generated prose claims exhaustive literature coverage")
    if _contains_unbounded_claim(lowered, _RETRIEVAL_AS_PROOF_PHRASES):
        reasons.append("generated prose treats retrieval or citations as novelty/proof evidence")
    if _CITATION_MARKER_RE.search(sentence) and not section_contract.allowed_citation_keys:
        reasons.append("citation marker appears without an allowed citation key")
    if (
        any(phrase in lowered for phrase in _EXTERNAL_FACT_PHRASES)
        and not _CITATION_MARKER_RE.search(sentence)
    ):
        reasons.append("external factual claim appears without citation")
    return sorted(set(reasons))


def _forbidden_labels_in_sentence(
    sentence: str,
    section_contract: ProseSectionContract,
    *,
    include_allowed_scaffold: bool = False,
) -> list[str]:
    labels = []
    lowered = sentence.casefold()
    for label in section_contract.forbidden_labels:
        if label == VerificationLabel.LIMITATION and not include_allowed_scaffold:
            continue
        if label.value.casefold() in lowered:
            labels.append(label.value)
    return labels


def _formal_label_without_negation(sentence: str) -> bool:
    match = _FORMAL_RESULT_LABEL_RE.search(sentence)
    if not match:
        return False
    lowered = sentence.casefold()
    label = match.group(1).casefold()
    safe_patterns = (
        f"no {label}",
        f"not a {label}",
        f"not an {label}",
        f"without {label}",
        f"does not provide {label}",
        f"cannot claim {label}",
        f"{label} label",
        f"{label} labels",
    )
    return not any(pattern in lowered for pattern in safe_patterns)


def _unsafe_phrase_present(text: str, phrases: tuple[str, ...]) -> bool:
    for phrase in phrases:
        if phrase not in text:
            continue
        window = text[max(0, text.find(phrase) - 40) : text.find(phrase) + len(phrase)]
        if any(cue in window for cue in _NEGATION_CUES):
            continue
        return True
    return False


def _numbering_allowed(text: str, section_contract: ProseSectionContract) -> bool:
    allowed = " ".join(section_contract.required_subsections).lower()
    match = _NUMBERED_THEOREM_RE.search(text)
    return bool(match and match.group(0).lower() in allowed)


def _contains_unbounded_claim(text: str, phrases: tuple[str, ...]) -> bool:
    return any(
        phrase in text
        and f"not {phrase}" not in text
        and f"not proof of {phrase}" not in text
        for phrase in phrases
    )


__all__ = [
    "parse_prose_generation_response",
    "validate_generated_section",
]
