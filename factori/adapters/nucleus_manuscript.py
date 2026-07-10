"""Gated structured LLM planning, drafting, critique, and revision for M105."""

from __future__ import annotations

import json
import re
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
    ManuscriptCriticRole,
    NucleusPaperType,
    StrictModel,
)


class ManuscriptSectionProposal(StrictModel):
    section_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    purpose: str = Field(min_length=1)
    claim_ids: list[str] = Field(default_factory=list)
    artifact_ids: list[str] = Field(default_factory=list)
    supporting_package_ids: list[str] = Field(default_factory=list)
    required_citations: list[str] = Field(default_factory=list)
    scope_constraints: list[str] = Field(min_length=1)
    bullets: list[str] = Field(default_factory=list)


class ManuscriptPlanProposal(StrictModel):
    working_title: str = Field(min_length=1)
    paper_type: NucleusPaperType
    central_question: str = Field(min_length=1)
    central_claim: str = Field(min_length=1)
    section_plans: list[ManuscriptSectionProposal] = Field(min_length=1)
    supporting_package_roles: dict[str, str] = Field(default_factory=dict)
    appendix_package_roles: dict[str, str] = Field(default_factory=dict)
    negative_result_roles: dict[str, str] = Field(default_factory=dict)


class ManuscriptPlanEnvelope(StrictModel):
    plans: list[ManuscriptPlanProposal] = Field(min_length=1, max_length=1)


class ManuscriptDraftProposal(StrictModel):
    title: str = Field(min_length=1)
    abstract: str = Field(min_length=1)
    markdown: str = Field(min_length=1)
    latex: str = Field(min_length=1)
    claim_ids_used: list[str] = Field(min_length=1)
    citation_binding_ids: list[str] = Field(default_factory=list)


class ManuscriptDraftEnvelope(StrictModel):
    drafts: list[ManuscriptDraftProposal] = Field(min_length=1, max_length=1)


class ManuscriptCriticProposal(StrictModel):
    findings: list[str] = Field(default_factory=list)
    blocking_findings: list[str] = Field(default_factory=list)
    recommended_revisions: list[str] = Field(default_factory=list)
    score: float = Field(ge=0.0, le=1.0)
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False


class ManuscriptCriticEnvelope(StrictModel):
    reviews: list[ManuscriptCriticProposal] = Field(min_length=1, max_length=1)


class ManuscriptRevisionProposal(ManuscriptDraftProposal):
    applied_recommendations: list[str] = Field(default_factory=list)


class ManuscriptRevisionEnvelope(StrictModel):
    revisions: list[ManuscriptRevisionProposal] = Field(min_length=1, max_length=1)


@dataclass(frozen=True)
class NucleusManuscriptResponse:
    prompt_text: str
    requested_output_schema: dict[str, Any]
    raw_response: dict[str, Any]
    accepted: (
        ManuscriptPlanProposal
        | ManuscriptDraftProposal
        | ManuscriptCriticProposal
        | ManuscriptRevisionProposal
        | None
    )
    rejection_reasons: list[str]


class NucleusManuscriptClient(Protocol):
    """Non-fake client boundary for all M105 narrative-generation operations."""

    backend_name: str
    backend_kind: BackendKind
    model: str
    fallback_used: bool
    fallback_disclosed: bool

    def plan_manuscript(
        self, *, prompt_id: str, nucleus_payload: dict[str, Any], evidence_payload: dict[str, Any]
    ) -> NucleusManuscriptResponse: ...

    def synthesize_manuscript(
        self, *, prompt_id: str, plan_payload: dict[str, Any], evidence_payload: dict[str, Any]
    ) -> NucleusManuscriptResponse: ...

    def critique_manuscript(
        self,
        *,
        prompt_id: str,
        critic_role: ManuscriptCriticRole,
        draft_payload: dict[str, Any],
        evidence_payload: dict[str, Any],
    ) -> NucleusManuscriptResponse: ...

    def revise_manuscript(
        self,
        *,
        prompt_id: str,
        draft_payload: dict[str, Any],
        critic_reviews_payload: list[dict[str, Any]],
        evidence_payload: dict[str, Any],
    ) -> NucleusManuscriptResponse: ...


@dataclass
class OpenAINucleusManuscript:
    """Explicitly gated OpenAI M105 narrative adapter with no deterministic fallback."""

    api_key: str = field(repr=False)
    model: str
    transport: LLMTransport = field(default_factory=OpenAIResponsesTransport)
    allow_external_calls: bool = False
    backend_name: str = field(default="llm-openai", init=False)
    backend_kind: BackendKind = field(default=BackendKind.LLM_OPENAI, init=False)
    fallback_used: bool = field(default=False, init=False)
    fallback_disclosed: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if not self.allow_external_calls:
            raise AdapterExternalCallsDisabled(
                "External calls are disabled. Set allow_external_calls=true for nucleus "
                "manuscripts."
            )
        if not self.api_key.strip():
            raise AdapterMissingCredentials(
                "OpenAI nucleus manuscript synthesis requested without an API key."
            )
        if not self.model.strip():
            raise ValueError("OpenAI nucleus manuscript synthesis requires a model name.")

    def plan_manuscript(
        self, *, prompt_id: str, nucleus_payload: dict[str, Any], evidence_payload: dict[str, Any]
    ) -> NucleusManuscriptResponse:
        return self._request(
            operation="planning",
            prompt_text=_planning_prompt(nucleus_payload, evidence_payload),
            schema=ManuscriptPlanEnvelope.model_json_schema(),
            envelope_key="plans",
            model_type=ManuscriptPlanProposal,
        )

    def synthesize_manuscript(
        self, *, prompt_id: str, plan_payload: dict[str, Any], evidence_payload: dict[str, Any]
    ) -> NucleusManuscriptResponse:
        return self._request(
            operation="synthesis",
            prompt_text=_synthesis_prompt(plan_payload, evidence_payload),
            schema=ManuscriptDraftEnvelope.model_json_schema(),
            envelope_key="drafts",
            model_type=ManuscriptDraftProposal,
        )

    def critique_manuscript(
        self,
        *,
        prompt_id: str,
        critic_role: ManuscriptCriticRole,
        draft_payload: dict[str, Any],
        evidence_payload: dict[str, Any],
    ) -> NucleusManuscriptResponse:
        return self._request(
            operation="critic",
            prompt_text=_critic_prompt(critic_role, draft_payload, evidence_payload),
            schema=ManuscriptCriticEnvelope.model_json_schema(),
            envelope_key="reviews",
            model_type=ManuscriptCriticProposal,
        )

    def revise_manuscript(
        self,
        *,
        prompt_id: str,
        draft_payload: dict[str, Any],
        critic_reviews_payload: list[dict[str, Any]],
        evidence_payload: dict[str, Any],
    ) -> NucleusManuscriptResponse:
        return self._request(
            operation="revision",
            prompt_text=_revision_prompt(draft_payload, critic_reviews_payload, evidence_payload),
            schema=ManuscriptRevisionEnvelope.model_json_schema(),
            envelope_key="revisions",
            model_type=ManuscriptRevisionProposal,
        )

    def _request(
        self,
        *,
        operation: str,
        prompt_text: str,
        schema: dict[str, Any],
        envelope_key: str,
        model_type: type[StrictModel],
    ) -> NucleusManuscriptResponse:
        raw = self.transport.create_response(
            api_key=self.api_key,
            model=self.model,
            prompt=prompt_text,
            response_schema=schema,
        )
        payload = _json_object(raw, operation=operation)
        accepted, reasons = _parse_one(payload, envelope_key=envelope_key, model_type=model_type)
        return NucleusManuscriptResponse(
            prompt_text=prompt_text,
            requested_output_schema=schema,
            raw_response=payload,
            accepted=accepted,
            rejection_reasons=reasons,
        )


def _planning_prompt(nucleus: dict[str, Any], evidence: dict[str, Any]) -> str:
    return (
        "Plan a bounded scientific manuscript around the selected paper nucleus. Choose the most "
        "coherent paper type from the evidence package rather than describing the pipeline. Define "
        "a title, central question, central claim, flexible sections, and appropriate roles for "
        "supporting, appendix, and negative packages. Every section needs scope constraints. "
        "Do not "
        "claim proof, novelty, underuse, real-world validation, publication readiness, or general "
        "domain truth. Return exactly one structured plan.\n\n"
        f"Nucleus:\n{json.dumps(nucleus, indent=2, sort_keys=True)}\n\n"
        f"Bound evidence:\n{json.dumps(evidence, indent=2, sort_keys=True)}"
    )


def _synthesis_prompt(plan: dict[str, Any], evidence: dict[str, Any]) -> str:
    return (
        "Write a coherent research manuscript from this approved plan and the supplied artifact "
        "bindings. Center the scientific nucleus, not the pipeline. Use only claim IDs and "
        "citation "
        "binding IDs supplied in the evidence. Numerical values must be copied exactly from the "
        "artifact table supplied by the system; do not calculate or invent numbers. State symbolic "
        "material as a draft with unresolved obligations. Place negative and secondary packages in "
        "appendices. Do not claim proof, novelty, underuse, real-world validation, publication "
        "readiness, or general domain truth. Return exactly one structured draft.\n\n"
        f"Plan:\n{json.dumps(plan, indent=2, sort_keys=True)}\n\n"
        f"Artifact-bound evidence:\n{json.dumps(evidence, indent=2, sort_keys=True)}"
    )


def _critic_prompt(
    role: ManuscriptCriticRole, draft: dict[str, Any], evidence: dict[str, Any]
) -> str:
    return (
        f"Act as the {role.value} critic for a bounded scientific manuscript. Inspect the draft "
        "only against the attached claim/artifact bindings and citations. Identify claim-evidence "
        "misalignment, incoherence, unsafe scope, missing baselines or controls, negative-result "
        "omissions, citation problems, or readability defects. Do not invent metrics or evidence. "
        "Do not grant proof, novelty, real-world validation, scientific validation, or publication "
        "readiness. Return exactly one structured review.\n\n"
        f"Draft:\n{json.dumps(draft, indent=2, sort_keys=True)}\n\n"
        f"Bindings:\n{json.dumps(evidence, indent=2, sort_keys=True)}"
    )


def _revision_prompt(
    draft: dict[str, Any], reviews: list[dict[str, Any]], evidence: dict[str, Any]
) -> str:
    return (
        "Revise the bounded manuscript only to address the attached critic recommendations. "
        "Preserve "
        "the allowed claim IDs, metric values, citation bindings, scope qualifications, and all "
        "unresolved obligations. If a blocking issue cannot be repaired from the supplied "
        "evidence, "
        "return a draft that makes the limitation explicit rather than inventing support. Do not "
        "claim proof, novelty, underuse, real-world validation, publication readiness, or general "
        "domain truth. Return exactly one structured revision.\n\n"
        f"Draft:\n{json.dumps(draft, indent=2, sort_keys=True)}\n\n"
        f"Critic reviews:\n{json.dumps(reviews, indent=2, sort_keys=True)}\n\n"
        f"Bindings:\n{json.dumps(evidence, indent=2, sort_keys=True)}"
    )


def _parse_one(
    payload: dict[str, Any],
    *,
    envelope_key: str,
    model_type: type[StrictModel],
) -> tuple[StrictModel | None, list[str]]:
    items = payload.get(envelope_key)
    if not isinstance(items, list) or len(items) != 1:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="nucleus_manuscript",
            message=f"response must contain exactly one {envelope_key} item",
        )
    try:
        item = model_type.model_validate(items[0])
    except ValidationError as exc:
        return None, [str(exc)]
    return item, _boundary_reasons(item)


def _boundary_reasons(item: StrictModel) -> list[str]:
    text = json.dumps(item.model_dump(mode="json"), sort_keys=True).lower()
    forbidden = {
        r"publication_ready=true": "manuscript output asserts publication readiness",
        r"\bpublication[- ]ready\b": "manuscript output asserts publication readiness",
        r"\bnovelty (?:is )?(?:proven|established)\b": "manuscript output asserts novelty",
        r"\bunderuse (?:is )?(?:proven|established)\b": "manuscript output asserts underuse",
        r"\bwe prove\b": "manuscript output claims proof without checker evidence",
        r"\btheorem (?:is )?proved\b": "manuscript output claims proof without checker evidence",
        r"\breal[- ]world validation\b": "manuscript output asserts real-world validation",
        r"\bgeneral(?:ly)? (?:shows|establishes|proves)\b": (
            "manuscript output overgeneralizes evidence"
        ),
    }
    return [message for pattern, message in forbidden.items() if re.search(pattern, text)]


def _json_object(raw: Any, *, operation: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation=f"nucleus_manuscript_{operation}",
            message="response was not valid JSON",
            cause=exc,
        ) from exc
    if not isinstance(payload, dict):
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation=f"nucleus_manuscript_{operation}",
            message="response must be a JSON object",
        )
    return payload


__all__ = [
    "ManuscriptCriticProposal",
    "ManuscriptDraftProposal",
    "ManuscriptPlanProposal",
    "ManuscriptRevisionProposal",
    "NucleusManuscriptClient",
    "NucleusManuscriptResponse",
    "OpenAINucleusManuscript",
]
