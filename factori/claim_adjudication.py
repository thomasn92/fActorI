"""Bounded semantic adjudication for manuscript claim-support audits."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from factori.adapters.errors import (
    AdapterExternalCallsDisabled,
    AdapterMissingCredentials,
    AdapterResponseParseError,
)
from factori.adapters.llm_real import LLMTransport, OpenAIResponsesTransport
from factori.hashing import sha256_text
from factori.schemas import ClaimAdjudication

_RISK_TERMS = re.compile(
    r"\b(proof|prove[sd]?|validated?|validation|experiment(?:s|al)?|empirical|"
    r"novelty|publication[- ]ready|establish(?:es|ed)?|demonstrat(?:e|es|ed)|"
    r"verif(?:y|ies|ied)|theorem|lemma|proposition|conjecture|leanverified|"
    r"citation-backed|sources?|literature)\b",
    re.IGNORECASE,
)
_NEGATED_PROOF = re.compile(
    r"\b(no|not|without|lacks?|absence of|does not provide|does not establish|"
    r"cannot support)\b[^.]{0,80}\b(proof|theorem|lemma|proposition)\b",
    re.IGNORECASE,
)
_NEGATED_EXPERIMENT = re.compile(
    r"\b(no|not|without|lacks?|absence of|does not|do not|cannot support)\b"
    r"[^.]{0,80}\b(experiment|empirical|validation|validate|validated)\b",
    re.IGNORECASE,
)
_NEGATED_NOVELTY_OR_READINESS = re.compile(
    r"\b(no|not|without|does not establish|does not claim|do not claim|"
    r"cannot support)\b[^.]{0,80}"
    r"\b(novelty|publication[- ]ready|publication readiness|complete literature coverage)\b",
    re.IGNORECASE,
)
_NO_CITATION_CURRENT_RUN = re.compile(
    r"\b(current|present)\s+(draft|run|artifact|artifacts|section)\b"
    r"|\bpresent run artifacts\b"
    r"|\bthis\s+(draft|section|artifact|manuscript)\b",
    re.IGNORECASE,
)
_NO_CITATION_ABSENCE = re.compile(
    r"\b(no|not|without|lacks?|absence of|absent|unavailable|does not include|"
    r"cannot make|cannot support|does not make)\b[^.]{0,120}"
    r"\b(retrieval|literature|source[- ]context|source context|source|metadata|"
    r"citation|prior work|support)\b"
    r"|\b(retrieval|literature|source[- ]context|source context|source|metadata|"
    r"citation|prior work|support)\b[^.]{0,120}"
    r"\b(absent|unavailable|missing|limitations?|does not verify|does not validate|"
    r"cannot support|is absent)\b",
    re.IGNORECASE,
)
_NO_CITATION_SCAFFOLD = re.compile(
    r"\b(literature[- ]positioning|source[- ]context|problem[- ]framing|"
    r"problem framing|literature positioning)\b[^.]{0,80}\b(scaffold|role|section)\b"
    r"|\b(scaffold|role|section)\b[^.]{0,80}"
    r"\b(literature[- ]positioning|source[- ]context|problem[- ]framing|"
    r"problem framing|literature positioning)\b"
    r"|\bretrieval limitations?\b",
    re.IGNORECASE,
)
_POSITIVE_EXTERNAL_CLAIM = re.compile(
    r"\b(prior work|the literature|retrieved sources?|sources?|source x|studies|"
    r"field data|survey data)\b[^.]{0,120}"
    r"\b(show|shows|uses?|used|establish(?:es|ed)?|supports?|discuss(?:es|ed)?|"
    r"demonstrat(?:es|ed)?|models?|indicates?|finds?|argues?)\b"
    r"|\b(human geography|spatial interaction|migration flows?)\b[^.]{0,120}"
    r"\b(commonly|widely|typically|often)\b"
    r"|\b(commonly|widely|typically|often)\b[^.]{0,120}"
    r"\b(human geography|spatial interaction|migration flows?)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ClaimAdjudicationRequest:
    """Small, non-secret sentence payload supplied to an adjudicator."""

    sentence_id: str
    section_name: str
    sentence: str
    preliminary_claim_class: str
    citation_keys_present: list[str]
    registry_source_summaries: list[dict[str, Any]]
    available_evidence_artifacts: dict[str, bool]


class ClaimAdjudicator(Protocol):
    """Semantic-only adjudicator; artifact verification stays deterministic."""

    backend_name: str
    model: str | None
    call_count: int

    def adjudicate(
        self,
        requests: list[ClaimAdjudicationRequest],
    ) -> list[ClaimAdjudication]: ...


def sentence_requires_adjudication(sentence: str) -> bool:
    """Return whether semantic risk terms justify bounded adjudication."""
    return bool(_RISK_TERMS.search(sentence))


def deterministic_semantic_adjudication(
    request: ClaimAdjudicationRequest,
    *,
    backend: str = "deterministic_fallback",
) -> ClaimAdjudication:
    """Apply minimal negation-aware semantics for fake and fallback operation."""
    text = " ".join(request.sentence.casefold().split())
    claim_class = request.preliminary_claim_class
    citation_use = "local_support" if request.citation_keys_present else "none"
    reasoning = "The preliminary deterministic classification is retained."

    if _is_negated_boundary(request.sentence):
        claim_class = (
            "limitation_statement"
            if any(
                term in text
                for term in ("lack", "absence", "limitation", "no ", "withheld")
            )
            else "evidence_boundary_statement"
        )
        citation_use = "background_context" if request.citation_keys_present else "none"
        reasoning = "The sentence denies evidence or validation rather than claiming it."
    elif re.search(r"\b(we|this (paper|draft|work))\s+(prove|proves|establishes)\b", text):
        claim_class = "proof_claim"
        reasoning = "The sentence positively asserts proof or establishment."
    elif re.search(
        r"\b(experiments?|empirical results?)\s+(show|shows|prove|proves|validate)",
        text,
    ):
        claim_class = "experiment_claim"
        reasoning = "The sentence positively attributes a result to experiments."
    elif re.search(
        r"\b(sources?|citations?|literature)\b[^.]{0,80}"
        r"\b(validate|validates|prove|proves)\b",
        text,
    ):
        claim_class = "external_factual_claim"
        citation_use = "misused_as_validation"
        reasoning = "The sentence uses literature context as validation or proof."
    elif "publication ready" in text or "publication-ready" in text:
        claim_class = "publication_readiness_claim"
        citation_use = (
            "misused_as_publication_readiness"
            if request.citation_keys_present
            else "none"
        )
        reasoning = "The sentence positively claims publication readiness."
    elif "novelty" in text and any(term in text for term in ("prove", "establish", "validate")):
        claim_class = "novelty_claim"
        citation_use = "misused_as_novelty" if request.citation_keys_present else "none"
        reasoning = "The sentence positively claims novelty validation."
    elif _is_current_run_or_absence_statement(request.sentence):
        claim_class = _no_citation_claim_class(request.sentence, claim_class)
        citation_use = "none"
        reasoning = "The sentence describes scaffold role or missing current-run support."
    elif _POSITIVE_EXTERNAL_CLAIM.search(request.sentence):
        claim_class = (
            "external_factual_claim"
            if claim_class == "external_factual_claim"
            else "literature_background_claim"
        )
        reasoning = "The sentence makes a positive external or literature-context claim."

    forbidden = claim_class in {
        "proof_claim",
        "experiment_claim",
        "novelty_claim",
        "publication_readiness_claim",
    }
    requires_citation, citation_reason = citation_requirement_for_sentence(
        request.sentence,
        claim_class,
    )
    return ClaimAdjudication(
        sentence_id=request.sentence_id,
        section_name=request.section_name,
        sentence_hash=sha256_text(request.sentence),
        adjudicated_claim_class=claim_class,
        requires_citation=requires_citation,
        requires_citation_reason=citation_reason,
        citation_use=citation_use,
        forbidden_claim_detected=forbidden,
        citation_as_validation_misuse=citation_use
        in {
            "misused_as_proof",
            "misused_as_validation",
            "misused_as_novelty",
            "misused_as_publication_readiness",
        },
        publication_readiness_claim=claim_class == "publication_readiness_claim",
        reasoning_brief=reasoning,
        confidence=0.95 if _is_negated_boundary(request.sentence) else 0.85,
        adjudicator_backend=backend,
    )


@dataclass
class FakeClaimAdjudicator:
    """Deterministic semantic adjudicator used by tests and local smoke runs."""

    backend_name: str = "fake"
    model: str | None = None
    call_count: int = 0

    def adjudicate(
        self,
        requests: list[ClaimAdjudicationRequest],
    ) -> list[ClaimAdjudication]:
        if requests:
            self.call_count += 1
        return [
            deterministic_semantic_adjudication(request, backend=self.backend_name)
            for request in requests
        ]


@dataclass
class OpenAIClaimAdjudicator:
    """Explicitly gated OpenAI semantic adjudicator with bounded batch calls."""

    api_key: str = field(repr=False)
    model: str
    transport: LLMTransport = field(default_factory=OpenAIResponsesTransport)
    allow_external_calls: bool = False
    max_calls: int = 4
    batch_size: int = 24
    backend_name: str = field(default="openai", init=False)
    provider_name: str = field(default="openai", init=False)
    call_count: int = field(default=0, init=False)
    adjudication_requests: list[dict[str, Any]] = field(default_factory=list, init=False)
    raw_responses: list[Any] = field(default_factory=list, init=False)

    def __post_init__(self) -> None:
        if not self.allow_external_calls:
            raise AdapterExternalCallsDisabled(
                "External calls are disabled. Set allow_external_calls=true to use the "
                "OpenAI claim adjudicator."
            )
        if not self.api_key.strip():
            raise AdapterMissingCredentials(
                "OpenAI claim adjudicator requested but no API key is configured."
            )
        if not self.model.strip():
            raise ValueError("OpenAI claim adjudicator requires a non-empty model name.")
        if self.max_calls < 1:
            raise ValueError("OpenAI claim adjudicator requires max_calls >= 1.")

    def adjudicate(
        self,
        requests: list[ClaimAdjudicationRequest],
    ) -> list[ClaimAdjudication]:
        results: list[ClaimAdjudication] = []
        for start in range(0, len(requests), self.batch_size):
            batch = requests[start : start + self.batch_size]
            if self.call_count >= self.max_calls:
                results.extend(
                    deterministic_semantic_adjudication(request) for request in batch
                )
                continue
            payload = _request_payload(batch)
            raw = self.transport.create_response(
                api_key=self.api_key,
                model=self.model,
                prompt=_adjudicator_prompt(payload),
                response_schema=_response_schema(),
            )
            self.call_count += 1
            self.adjudication_requests.append(payload)
            self.raw_responses.append(raw)
            results.extend(_parse_response(raw, batch, self.model))
        return results


def _is_negated_boundary(sentence: str) -> bool:
    return bool(
        _NEGATED_PROOF.search(sentence)
        or _NEGATED_EXPERIMENT.search(sentence)
        or _NEGATED_NOVELTY_OR_READINESS.search(sentence)
        or re.search(
            r"\b(validation|proof|experiment)\b[^.]{0,80}\b(withheld|unavailable|absent)\b"
            r"|\b(withheld|unavailable|absent)\b[^.]{0,80}\b(validation|proof|experiment)\b",
            sentence,
            re.IGNORECASE,
        )
    )


def citation_requirement_for_sentence(
    sentence: str,
    claim_class: str,
) -> tuple[bool, str]:
    """Decide whether a sentence needs local source support."""
    if _is_negated_boundary(sentence):
        return False, "evidence_boundary_no_citation_required"
    if _is_current_run_or_absence_statement(sentence):
        text = " ".join(sentence.casefold().split())
        if "scaffold" in text or "problem-framing" in text or "problem framing" in text:
            return False, "scaffold_role_no_citation_required"
        if any(
            term in text
            for term in (
                "no ",
                "not ",
                "without",
                "lacks",
                "absence",
                "absent",
                "unavailable",
                "does not include",
                "cannot make",
                "does not make",
                "limitations",
            )
        ):
            return False, "absence_of_evidence_no_citation_required"
        return False, "current_run_status_no_citation_required"
    if _POSITIVE_EXTERNAL_CLAIM.search(sentence):
        if claim_class == "external_factual_claim":
            return True, "positive_external_claim"
        if claim_class == "source_context_claim":
            return True, "positive_source_context_claim"
        return True, "positive_literature_claim"
    if claim_class == "external_factual_claim":
        return True, "positive_external_claim"
    if claim_class == "source_context_claim":
        return True, "positive_source_context_claim"
    if claim_class == "literature_background_claim":
        return True, "positive_literature_claim"
    if claim_class == "evidence_boundary_statement":
        return False, "evidence_boundary_no_citation_required"
    if claim_class in {"limitation_statement", "pipeline_status_claim"}:
        return False, "absence_of_evidence_no_citation_required"
    if claim_class in {"scaffold_statement", "problem_framing_statement"}:
        return False, "scaffold_role_no_citation_required"
    return False, "claim_class_no_citation_required"


def _is_current_run_or_absence_statement(sentence: str) -> bool:
    text = " ".join(sentence.casefold().split())
    return bool(
        _NO_CITATION_ABSENCE.search(sentence)
        or _NO_CITATION_SCAFFOLD.search(sentence)
        or (
            _NO_CITATION_CURRENT_RUN.search(sentence)
            and any(
                term in text
                for term in (
                    "retrieval",
                    "literature",
                    "source-context",
                    "source context",
                    "metadata",
                    "citation",
                    "support",
                    "scaffold",
                    "limitation",
                    "cannot make",
                    "does not include",
                )
            )
        )
    )


def _no_citation_claim_class(sentence: str, fallback: str) -> str:
    text = " ".join(sentence.casefold().split())
    if _is_negated_boundary(sentence):
        return "evidence_boundary_statement"
    if "problem-framing" in text or "problem framing" in text:
        return "problem_framing_statement"
    if "scaffold" in text:
        return "scaffold_statement"
    if "limitation" in text or "does not include" in text or "cannot make" in text:
        return "limitation_statement"
    if "metadata" in text or "verify" in text or "validate" in text:
        return "evidence_boundary_statement"
    if "current" in text or "present run" in text:
        return "pipeline_status_claim"
    return fallback


def _request_payload(requests: list[ClaimAdjudicationRequest]) -> dict[str, Any]:
    return {
        "sentences": [
            {
                "sentence_id": request.sentence_id,
                "section_name": request.section_name,
                "sentence": request.sentence,
                "preliminary_claim_class": request.preliminary_claim_class,
                "citation_keys_present": request.citation_keys_present,
                "registry_source_summaries": request.registry_source_summaries,
                "available_evidence_artifacts": request.available_evidence_artifacts,
            }
            for request in requests
        ]
    }


def _adjudicator_prompt(payload: dict[str, Any]) -> str:
    return (
        "You audit manuscript sentence meaning only. Do not decide whether artifacts exist and "
        "do not infer hidden evidence. Sentences saying no proof, not proof, no experiment, no "
        "validation, no novelty, or not publication ready are boundary or limitation statements, "
        "not positive proof or experiment claims. A sentence only requires citation if it makes "
        "a positive claim about external literature, prior work, source contents, empirical facts, "
        "or external domain facts. A sentence does not require citation merely because it contains "
        "words like literature, source, retrieval, prior work, or metadata when it only describes "
        "the current run, absence of evidence, limitations, or scaffold role. Classify every "
        "supplied sentence.\n\n"
        + json.dumps(payload, sort_keys=True, ensure_ascii=True)
    )


def _response_schema() -> dict[str, Any]:
    claim_classes = [
        "scaffold_statement",
        "problem_framing_statement",
        "method_description_statement",
        "evidence_boundary_statement",
        "limitation_statement",
        "provenance_statement",
        "pipeline_status_claim",
        "source_context_claim",
        "literature_background_claim",
        "external_factual_claim",
        "proof_claim",
        "experiment_claim",
        "novelty_claim",
        "publication_readiness_claim",
    ]
    citation_uses = [
        "none",
        "background_context",
        "local_support",
        "misused_as_proof",
        "misused_as_validation",
        "misused_as_novelty",
        "misused_as_publication_readiness",
    ]
    return {
        "type": "object",
        "properties": {
            "adjudications": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "sentence_id": {"type": "string"},
                        "claim_class": {"type": "string", "enum": claim_classes},
                        "requires_citation": {"type": "boolean"},
                        "citation_use": {"type": "string", "enum": citation_uses},
                        "forbidden_claim_detected": {"type": "boolean"},
                        "citation_as_validation_misuse": {"type": "boolean"},
                        "publication_readiness_claim": {"type": "boolean"},
                        "reasoning_brief": {"type": "string", "maxLength": 400},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": [
                        "sentence_id",
                        "claim_class",
                        "requires_citation",
                        "citation_use",
                        "forbidden_claim_detected",
                        "citation_as_validation_misuse",
                        "publication_readiness_claim",
                        "reasoning_brief",
                        "confidence",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["adjudications"],
        "additionalProperties": False,
    }


def _parse_response(
    raw: Any,
    requests: list[ClaimAdjudicationRequest],
    model: str,
) -> list[ClaimAdjudication]:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
        rows = payload["adjudications"]
        by_id = {request.sentence_id: request for request in requests}
        parsed = []
        for row in rows:
            request = by_id[row["sentence_id"]]
            parsed.append(
                _normalized_adjudication(
                    request=request,
                    claim_class=row["claim_class"],
                    requires_citation=row["requires_citation"],
                    citation_use=row["citation_use"],
                    forbidden_claim_detected=row["forbidden_claim_detected"],
                    citation_as_validation_misuse=row["citation_as_validation_misuse"],
                    publication_readiness_claim=row["publication_readiness_claim"],
                    reasoning_brief=row["reasoning_brief"],
                    confidence=row["confidence"],
                    adjudicator_backend="openai",
                )
            )
        if {item.sentence_id for item in parsed} != set(by_id):
            raise ValueError("response did not adjudicate every requested sentence")
        return parsed
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="claim_adjudication",
            message=f"claim adjudicator returned invalid structured output for model={model}",
            cause=exc,
        ) from exc


def _normalized_adjudication(
    *,
    request: ClaimAdjudicationRequest,
    claim_class: str,
    requires_citation: bool,
    citation_use: str,
    forbidden_claim_detected: bool,
    citation_as_validation_misuse: bool,
    publication_readiness_claim: bool,
    reasoning_brief: str,
    confidence: float,
    adjudicator_backend: str,
) -> ClaimAdjudication:
    required_by_rule, citation_reason = citation_requirement_for_sentence(
        request.sentence,
        claim_class,
    )
    normalized_class = claim_class
    normalized_reason = reasoning_brief
    if (
        not required_by_rule
        and normalized_class
        in {
            "proof_claim",
            "experiment_claim",
            "novelty_claim",
            "publication_readiness_claim",
        }
        and citation_reason
        in {
            "evidence_boundary_no_citation_required",
            "absence_of_evidence_no_citation_required",
        }
    ):
        normalized_class = _no_citation_claim_class(request.sentence, claim_class)
        normalized_reason = (
            f"{reasoning_brief} Authority claim class suppressed: {citation_reason}."
        )[:400]
    elif not required_by_rule and requires_citation:
        normalized_class = _no_citation_claim_class(request.sentence, claim_class)
        normalized_reason = (
            f"{reasoning_brief} Citation requirement suppressed: {citation_reason}."
        )[:400]
    elif required_by_rule and not requires_citation:
        normalized_reason = (
            f"{reasoning_brief} Citation requirement enforced: {citation_reason}."
        )[:400]
    normalized_forbidden = normalized_class in {
        "proof_claim",
        "experiment_claim",
        "novelty_claim",
        "publication_readiness_claim",
    }
    normalized_citation_misuse = citation_use in {
        "misused_as_proof",
        "misused_as_validation",
        "misused_as_novelty",
        "misused_as_publication_readiness",
    }
    return ClaimAdjudication(
        sentence_id=request.sentence_id,
        section_name=request.section_name,
        sentence_hash=sha256_text(request.sentence),
        adjudicated_claim_class=normalized_class,
        requires_citation=required_by_rule,
        requires_citation_reason=citation_reason,
        citation_use=citation_use,
        forbidden_claim_detected=forbidden_claim_detected and normalized_forbidden,
        citation_as_validation_misuse=(
            citation_as_validation_misuse and normalized_citation_misuse
        ),
        publication_readiness_claim=(
            publication_readiness_claim
            and normalized_class == "publication_readiness_claim"
        ),
        reasoning_brief=normalized_reason,
        confidence=confidence,
        adjudicator_backend=adjudicator_backend,
    )


__all__ = [
    "ClaimAdjudicationRequest",
    "ClaimAdjudicator",
    "FakeClaimAdjudicator",
    "OpenAIClaimAdjudicator",
    "citation_requirement_for_sentence",
    "deterministic_semantic_adjudication",
    "sentence_requires_adjudication",
]
