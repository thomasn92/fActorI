"""Validation and parsing for untrusted Stage A LLM candidate proposals."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from factori.hashing import canonical_json, sha256_json
from factori.schemas import (
    Candidate,
    CandidateValidationResult,
    ConstraintSet,
    DataRequirement,
    LLMCandidateParseReport,
)

PROHIBITED_VERIFICATION_LABELS = (
    "LeanVerified",
    "ExperimentVerified",
    "SyntheticExperimentVerified",
    "RealDataExperimentVerified",
)
REAL_WORLD_VALIDATION_PATTERNS = (
    "real-world validation",
    "real world validation",
    "validated in the real world",
    "proven real-world",
    "production-validated",
)
UNSUPPORTED_EVIDENCE_PATTERNS = (
    "evidence proves",
    "evidence shows",
    "experiments prove",
    "experiments demonstrate",
    "we prove",
    "we verified",
    "has been verified",
)
EXTERNAL_DEPENDENCY_PATTERNS = (
    "external api",
    "private database",
    "proprietary dataset",
    "user-provided data",
    "downloaded dataset",
)


class LLMCandidateResponseError(ValueError):
    """Raised when an LLM response is not a structured candidate collection."""


def parse_llm_candidate_response(
    raw_response: Any,
    max_candidates: int = 4,
    *,
    backend: str = "unknown",
    provider: str = "unknown",
) -> list[Candidate]:
    """Parse valid candidates and discard malformed or unsafe proposals."""
    candidates, _ = parse_llm_candidate_response_with_report(
        raw_response,
        max_candidates,
        backend=backend,
        provider=provider,
    )
    return candidates


def parse_llm_candidate_response_with_report(
    raw_response: Any,
    max_candidates: int = 4,
    *,
    backend: str = "unknown",
    provider: str = "unknown",
) -> tuple[list[Candidate], LLMCandidateParseReport]:
    """Parse an untrusted structured response and return an explicit rejection report."""
    if max_candidates < 1:
        raise ValueError("max_candidates must be at least 1")
    payload = _structured_payload(raw_response)
    raw_candidates = payload.get("candidates")
    if not isinstance(raw_candidates, list):
        raise LLMCandidateResponseError("LLM response must contain a candidates list")

    accepted: list[Candidate] = []
    rejected: list[dict[str, Any]] = []
    for index, item in enumerate(raw_candidates):
        if len(accepted) >= max_candidates:
            break
        try:
            candidate = _candidate_from_item(item, index, backend=backend, provider=provider)
        except (TypeError, ValueError, ValidationError) as exc:
            rejected.append({"index": index, "reasons": [str(exc)]})
            continue
        validation = validate_llm_candidate(candidate)
        if validation.valid:
            accepted.append(candidate)
        else:
            rejected.append({"index": index, "reasons": validation.reasons})

    return accepted, LLMCandidateParseReport(
        accepted_candidate_ids=[candidate.id for candidate in accepted],
        rejected_candidates=rejected,
        max_candidates=max_candidates,
        truncated=len(raw_candidates) > len(accepted) + len(rejected),
    )


def validate_llm_candidate(candidate: Candidate) -> CandidateValidationResult:
    """Enforce the Stage A proposal boundary without treating ideas as evidence."""
    reasons: list[str] = []
    proposal_text = _candidate_proposal_text(candidate)
    lowered = proposal_text.lower()

    for label in PROHIBITED_VERIFICATION_LABELS:
        if label.lower() in lowered:
            reasons.append(f"verification-label inflation is forbidden: {label}")
    if any(pattern in lowered for pattern in REAL_WORLD_VALIDATION_PATTERNS):
        reasons.append("candidate claims real-world validation without real-data evidence")
    if any(pattern in lowered for pattern in UNSUPPORTED_EVIDENCE_PATTERNS):
        reasons.append("candidate claims evidence or verification that was not provided")
    if candidate.data_requirement in {
        DataRequirement.NO_DATA,
        DataRequirement.SYNTHETIC_ONLY,
    } and any(pattern in lowered for pattern in EXTERNAL_DEPENDENCY_PATTERNS):
        reasons.append("candidate has an unsafe external dependency for its data regime")

    return CandidateValidationResult(
        candidate_id=candidate.id,
        valid=not reasons,
        deferred_by_mvp_data_gate=candidate.data_requirement
        in {DataRequirement.PUBLIC_DOWNLOAD, DataRequirement.USER_PROVIDED},
        reasons=sorted(set(reasons)),
    )


def _structured_payload(raw_response: Any) -> dict[str, Any]:
    payload = raw_response
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise LLMCandidateResponseError("LLM response is not valid JSON") from exc
    if isinstance(payload, list):
        payload = {"candidates": payload}
    if not isinstance(payload, dict):
        raise LLMCandidateResponseError("LLM response must be a JSON object or list")
    return payload


def _candidate_from_item(
    item: Any,
    index: int,
    *,
    backend: str,
    provider: str,
) -> Candidate:
    if not isinstance(item, dict):
        raise TypeError("candidate must be a JSON object")
    required = {
        "title",
        "domain",
        "method",
        "claim_type",
        "assumptions",
        "data_requirement",
        "possible_synthetic_experiment",
        "risks",
    }
    missing = sorted(required - item.keys())
    if missing:
        raise ValueError(f"candidate missing required fields: {', '.join(missing)}")
    title = _required_text(item["title"], "title")
    domain = _required_text(item["domain"], "domain")
    method = _optional_text(item["method"], "method")
    claim_type = _required_text(item["claim_type"], "claim_type")
    assumptions = _text_list(item["assumptions"], "assumptions")
    risks = _text_list(item["risks"], "risks")
    primitives = _text_list(item.get("primitives", []), "primitives")
    data_requirement = DataRequirement(item["data_requirement"])
    experiment = _optional_text(
        item["possible_synthetic_experiment"],
        "possible_synthetic_experiment",
    )
    baseline = _optional_text(item.get("baseline"), "baseline")
    question = _optional_text(item.get("question"), "question") or title
    hypothesis = _optional_text(item.get("hypothesis"), "hypothesis")
    theory = "; ".join(assumptions) if assumptions else None
    normalized_item = {
        key: item[key] for key in sorted(item) if key not in {"candidate_id", "id"}
    }
    digest = sha256_json(normalized_item)[:10]
    candidate_id = f"llm-{_slug(title)}-{digest}-{index + 1:02d}"
    constraints = ConstraintSet(
        domain=domain,
        primitives=primitives,
        method=method,
        question=question,
        hypothesis=hypothesis,
        theory=theory,
        experiment=experiment,
        baseline=baseline,
        data_requirement=data_requirement,
    )
    return Candidate(
        id=candidate_id,
        constraints=constraints,
        domain=domain,
        primitives=primitives,
        method=method,
        question=question,
        hypothesis=hypothesis,
        theory=theory,
        experiment=experiment,
        baseline=baseline,
        data_requirement=data_requirement,
        symbolic_state={
            "title": title,
            "claim_type": claim_type,
            "assumptions": assumptions,
            "risks": risks,
            "adapter_backend": backend,
            "adapter_provider": provider,
            "llm_proposed": True,
            "fake": False,
            "raw_candidate_hash": sha256_json(normalized_item),
        },
    )


def _candidate_proposal_text(candidate: Candidate) -> str:
    values = {
        "domain": candidate.domain,
        "method": candidate.method,
        "question": candidate.question,
        "hypothesis": candidate.hypothesis,
        "theory": candidate.theory,
        "experiment": candidate.experiment,
        "baseline": candidate.baseline,
        "symbolic_state": candidate.symbolic_state,
    }
    return canonical_json(values)


def _required_text(value: Any, field: str) -> str:
    normalized = _optional_text(value, field)
    if normalized is None:
        raise ValueError(f"{field} must be a non-empty string")
    return normalized


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string or null")
    normalized = " ".join(value.split())
    if not normalized:
        return None
    return normalized


def _text_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list):
        raise TypeError(f"{field} must be a list of strings")
    result = []
    for item in value:
        normalized = _required_text(item, field)
        result.append(normalized)
    return result


def _slug(value: str) -> str:
    slug = "-".join(re.findall(r"[a-z0-9]+", value.lower()))
    return slug[:48] or "candidate"


__all__ = [
    "LLMCandidateResponseError",
    "parse_llm_candidate_response",
    "parse_llm_candidate_response_with_report",
    "validate_llm_candidate",
]
