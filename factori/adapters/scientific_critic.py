"""Gated structured LLM scientific criticism and cross-package adjudication."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import Field, ValidationError

from factori.adapters.errors import (
    AdapterExternalCallsDisabled,
    AdapterMissingCredentials,
    AdapterResponseParseError,
)
from factori.adapters.llm_real import LLMTransport, OpenAIResponsesTransport
from factori.schemas import (
    BackendKind,
    EvidencePackageDecision,
    ScientificCriticFindingSeverity,
    ScientificCriticFindingType,
    ScientificCriticRole,
    StrictModel,
)

MANDATORY_FORBIDDEN_CLAIMS = (
    "real-world validation",
    "verified theorem",
    "novelty proven",
    "underuse proven",
    "publication ready",
    "general domain truth",
)


class CriticFindingProposal(StrictModel):
    severity: ScientificCriticFindingSeverity
    finding_type: ScientificCriticFindingType
    description: str = Field(min_length=1)
    affected_claims: list[str] = Field(default_factory=list)
    recommended_fix: str = Field(min_length=1)
    blocking: bool


class CriticReviewProposal(StrictModel):
    summary: str = Field(min_length=1)
    findings: list[CriticFindingProposal] = Field(default_factory=list)
    score_delta: float = Field(ge=-1.0, le=1.0)
    recommended_decision: EvidencePackageDecision
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False
    creates_real_world_validation: Literal[False] = False


class CriticReviewEnvelope(StrictModel):
    reviews: list[CriticReviewProposal] = Field(min_length=1, max_length=1)


class AdjudicationDecisionProposal(StrictModel):
    package_id: str = Field(min_length=1)
    decision: EvidencePackageDecision
    rank: int = Field(ge=1)
    role: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    required_repairs: list[str] = Field(default_factory=list)
    allowed_claim_scope: str = Field(min_length=1)
    forbidden_claims: list[str] = Field(min_length=1)
    supporting_artifact_ids: list[str] = Field(default_factory=list)
    blocking_findings: list[str] = Field(default_factory=list)
    recommended_next_action: str = Field(min_length=1)
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False


class PaperNucleusProposal(StrictModel):
    primary_package_id: str = Field(min_length=1)
    central_claim_draft: str = Field(min_length=1)
    allowed_claim_scope: str = Field(min_length=1)
    forbidden_claims: list[str] = Field(min_length=1)
    supporting_package_ids: list[str] = Field(default_factory=list)
    appendix_package_ids: list[str] = Field(default_factory=list)
    negative_package_ids: list[str] = Field(default_factory=list)
    rejected_package_ids: list[str] = Field(default_factory=list)
    required_repairs_before_manuscript: list[str] = Field(default_factory=list)
    required_additional_checks: list[str] = Field(default_factory=list)
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False


class CrossPackageAdjudicationProposal(StrictModel):
    decisions: list[AdjudicationDecisionProposal] = Field(min_length=1)
    paper_nucleus_selection_optional: PaperNucleusProposal | None = None
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False
    creates_real_world_validation: Literal[False] = False


class CrossPackageAdjudicationEnvelope(StrictModel):
    adjudications: list[CrossPackageAdjudicationProposal] = Field(min_length=1, max_length=1)


@dataclass(frozen=True)
class ScientificCriticResponse:
    prompt_text: str
    requested_output_schema: dict[str, Any]
    raw_response: dict[str, Any]
    accepted: CriticReviewProposal | None
    rejection_reasons: list[str]


@dataclass(frozen=True)
class CrossPackageAdjudicationResponse:
    prompt_text: str
    requested_output_schema: dict[str, Any]
    raw_response: dict[str, Any]
    accepted: CrossPackageAdjudicationProposal | None
    rejection_reasons: list[str]


class ScientificCriticClient(Protocol):
    """Non-fake LLM interface for critic and adjudication calls."""

    backend_name: str
    backend_kind: BackendKind
    model: str
    fallback_used: bool
    fallback_disclosed: bool

    def critique_package(
        self,
        *,
        prompt_id: str,
        critic_role: ScientificCriticRole,
        package_payload: dict[str, Any],
        execution_payload: dict[str, Any],
    ) -> ScientificCriticResponse: ...

    def adjudicate_packages(
        self,
        *,
        prompt_id: str,
        packages_payload: list[dict[str, Any]],
        execution_payload: list[dict[str, Any]],
        critic_reviews_payload: list[dict[str, Any]],
        score_payload: list[dict[str, Any]],
    ) -> CrossPackageAdjudicationResponse: ...


@dataclass
class OpenAIScientificCritic:
    """Explicitly gated OpenAI critic/adjudicator with no deterministic fallback."""

    api_key: str = field(repr=False)
    model: str
    transport: LLMTransport = field(
        default_factory=lambda: OpenAIResponsesTransport(
            schema_name="factori_scientific_critic",
            nullable_optional_fields=False,
        )
    )
    allow_external_calls: bool = False
    backend_name: str = field(default="llm-openai", init=False)
    backend_kind: BackendKind = field(default=BackendKind.LLM_OPENAI, init=False)
    fallback_used: bool = field(default=False, init=False)
    fallback_disclosed: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if not self.allow_external_calls:
            raise AdapterExternalCallsDisabled(
                "External calls are disabled. Set allow_external_calls=true for scientific "
                "criticism."
            )
        if not self.api_key.strip():
            raise AdapterMissingCredentials(
                "OpenAI scientific criticism requested without an API key."
            )
        if not self.model.strip():
            raise ValueError("OpenAI scientific criticism requires a model name.")

    def critique_package(
        self,
        *,
        prompt_id: str,
        critic_role: ScientificCriticRole,
        package_payload: dict[str, Any],
        execution_payload: dict[str, Any],
    ) -> ScientificCriticResponse:
        prompt_text, schema = build_scientific_critic_prompt(
            prompt_id=prompt_id,
            backend_name=self.backend_name,
            model=self.model,
            critic_role=critic_role,
            package_payload=package_payload,
            execution_payload=execution_payload,
        )
        raw = self.transport.create_response(
            api_key=self.api_key,
            model=self.model,
            prompt=prompt_text,
            response_schema=schema,
        )
        payload = _json_object(raw, operation="scientific_critic")
        accepted, reasons = parse_scientific_critic_response(payload)
        return ScientificCriticResponse(prompt_text, schema, payload, accepted, reasons)

    def adjudicate_packages(
        self,
        *,
        prompt_id: str,
        packages_payload: list[dict[str, Any]],
        execution_payload: list[dict[str, Any]],
        critic_reviews_payload: list[dict[str, Any]],
        score_payload: list[dict[str, Any]],
    ) -> CrossPackageAdjudicationResponse:
        prompt_text, schema = build_cross_package_adjudication_prompt(
            prompt_id=prompt_id,
            backend_name=self.backend_name,
            model=self.model,
            packages_payload=packages_payload,
            execution_payload=execution_payload,
            critic_reviews_payload=critic_reviews_payload,
            score_payload=score_payload,
        )
        raw = self.transport.create_response(
            api_key=self.api_key,
            model=self.model,
            prompt=prompt_text,
            response_schema=schema,
        )
        payload = _json_object(raw, operation="cross_package_adjudication")
        accepted, reasons = parse_cross_package_adjudication_response(payload)
        return CrossPackageAdjudicationResponse(prompt_text, schema, payload, accepted, reasons)


def build_scientific_critic_prompt(
    *,
    prompt_id: str,
    backend_name: str,
    model: str,
    critic_role: ScientificCriticRole,
    package_payload: dict[str, Any],
    execution_payload: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    prompt_text = (
        f"Act as the {critic_role.value}. Review the hybrid evidence package using only the "
        "declared package artifacts and execution results. Look for weak or missing baselines, "
        "tautological or rigged synthetic settings, negative-control or robustness weaknesses, "
        "false bridges, overclaims, draft/proof inflation, novelty overstatement, technical "
        "incoherence, or interesting bounded failures. Do not invent metrics or artifacts. "
        "Every finding with blocking=true must use severity=blocking. "
        "Do not claim novelty, proof, real-world validation, publication readiness, or scientific "
        "validation. Return exactly one structured review.\n\n"
        f"Package:\n{json.dumps(package_payload, indent=2, sort_keys=True)}\n\n"
        f"Execution results:\n{json.dumps(execution_payload, indent=2, sort_keys=True)}"
    )
    return prompt_text, CriticReviewEnvelope.model_json_schema()


def build_cross_package_adjudication_prompt(
    *,
    prompt_id: str,
    backend_name: str,
    model: str,
    packages_payload: list[dict[str, Any]],
    execution_payload: list[dict[str, Any]],
    critic_reviews_payload: list[dict[str, Any]],
    score_payload: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    prompt_text = (
        "Adjudicate the supplied hybrid evidence packages into exactly one decision per package. "
        "Select at most one primary nucleus. A package with a blocking false bridge, overclaim, "
        "or missing baseline cannot be primary. A draft-only symbolic package cannot be primary by "
        "default. A package with no executed or checkable artifact cannot be primary. Prefer a "
        "bounded claim with an explicit baseline, controls, support artifacts, interpretable "
        "mechanism, and low false-bridge risk. The primary claim must remain synthetic, bounded, "
        "or draft-scoped as appropriate. Do not assert real-world validation, theorem proof, "
        "novelty proven, underuse proven, publication readiness, or general domain truth. Any "
        "paper nucleus must include every mandatory forbidden claim exactly as listed here: "
        f"{json.dumps(list(MANDATORY_FORBIDDEN_CLAIMS))}.\n\n"
        f"Packages:\n{json.dumps(packages_payload, indent=2, sort_keys=True)}\n\n"
        f"Execution results:\n{json.dumps(execution_payload, indent=2, sort_keys=True)}\n\n"
        f"Critic reviews:\n{json.dumps(critic_reviews_payload, indent=2, sort_keys=True)}\n\n"
        f"Local score aggregation:\n{json.dumps(score_payload, indent=2, sort_keys=True)}"
    )
    return prompt_text, CrossPackageAdjudicationEnvelope.model_json_schema()


def parse_scientific_critic_response(
    payload: dict[str, Any],
) -> tuple[CriticReviewProposal | None, list[str]]:
    raw_items = payload.get("reviews")
    if not isinstance(raw_items, list) or len(raw_items) != 1:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="scientific_critic",
            message="scientific critic response must contain exactly one review",
        )
    try:
        item = CriticReviewProposal.model_validate(raw_items[0])
    except ValidationError as exc:
        return None, [str(exc)]
    normalized_findings = []
    for finding in item.findings:
        blocking = (
            finding.blocking
            or finding.severity == ScientificCriticFindingSeverity.BLOCKING
        )
        normalized_findings.append(
            finding.model_copy(
                update={
                    "blocking": blocking,
                    "severity": (
                        ScientificCriticFindingSeverity.BLOCKING
                        if blocking
                        else finding.severity
                    ),
                }
            )
        )
    item = item.model_copy(update={"findings": normalized_findings})
    return item, validate_scientific_critic_proposal(item)


def parse_cross_package_adjudication_response(
    payload: dict[str, Any],
) -> tuple[CrossPackageAdjudicationProposal | None, list[str]]:
    raw_items = payload.get("adjudications")
    if not isinstance(raw_items, list) or len(raw_items) != 1:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="cross_package_adjudication",
            message="cross-package adjudication response must contain exactly one adjudication",
        )
    try:
        item = CrossPackageAdjudicationProposal.model_validate(raw_items[0])
    except ValidationError as exc:
        return None, [str(exc)]
    nucleus = item.paper_nucleus_selection_optional
    if nucleus is not None:
        normalized = {claim.strip().lower() for claim in nucleus.forbidden_claims}
        missing = [
            claim for claim in MANDATORY_FORBIDDEN_CLAIMS if claim not in normalized
        ]
        if missing:
            item = item.model_copy(
                update={
                    "paper_nucleus_selection_optional": nucleus.model_copy(
                        update={"forbidden_claims": [*nucleus.forbidden_claims, *missing]}
                    )
                }
            )
    return item, validate_cross_package_adjudication_proposal(item)


def validate_scientific_critic_proposal(item: CriticReviewProposal) -> list[str]:
    reasons: list[str] = []
    for finding in item.findings:
        if finding.blocking and finding.severity != ScientificCriticFindingSeverity.BLOCKING:
            reasons.append("blocking critic finding must use blocking severity")
    return reasons


def validate_cross_package_adjudication_proposal(
    item: CrossPackageAdjudicationProposal,
) -> list[str]:
    reasons: list[str] = []
    package_ids = [decision.package_id for decision in item.decisions]
    if len(package_ids) != len(set(package_ids)):
        reasons.append("adjudication contains duplicate package decisions")
    primary = [
        decision
        for decision in item.decisions
        if decision.decision == EvidencePackageDecision.PRIMARY_NUCLEUS
    ]
    if len(primary) > 1:
        reasons.append("adjudication selects more than one primary nucleus")
    if item.paper_nucleus_selection_optional is not None:
        nucleus = item.paper_nucleus_selection_optional
        if nucleus.primary_package_id not in package_ids:
            reasons.append("paper nucleus refers to a package without an adjudication decision")
        if not primary or primary[0].package_id != nucleus.primary_package_id:
            reasons.append("paper nucleus must match the primary_nucleus package decision")
        normalized = {claim.strip().lower() for claim in nucleus.forbidden_claims}
        missing = [claim for claim in MANDATORY_FORBIDDEN_CLAIMS if claim not in normalized]
        if missing:
            reasons.append("paper nucleus omits mandatory forbidden claims")
    elif primary:
        reasons.append("primary_nucleus decision requires a paper nucleus selection")
    return reasons


def _json_object(raw: Any, *, operation: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation=operation,
            message="response was not valid JSON",
            cause=exc,
        ) from exc
    if not isinstance(payload, dict):
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation=operation,
            message="response must be a JSON object",
        )
    return payload


__all__ = [
    "AdjudicationDecisionProposal",
    "CrossPackageAdjudicationEnvelope",
    "CrossPackageAdjudicationProposal",
    "CrossPackageAdjudicationResponse",
    "CriticFindingProposal",
    "CriticReviewEnvelope",
    "CriticReviewProposal",
    "MANDATORY_FORBIDDEN_CLAIMS",
    "OpenAIScientificCritic",
    "PaperNucleusProposal",
    "ScientificCriticClient",
    "ScientificCriticResponse",
    "build_cross_package_adjudication_prompt",
    "build_scientific_critic_prompt",
    "parse_cross_package_adjudication_response",
    "parse_scientific_critic_response",
    "validate_cross_package_adjudication_proposal",
    "validate_scientific_critic_proposal",
]
