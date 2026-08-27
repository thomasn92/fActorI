"""Gated structured LLM questioner for adaptive post-execution decisions."""

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
from factori.schemas import BackendKind, StrictModel

AdaptiveActionProposal = Literal[
    "accept_supported_result",
    "accept_negative_result",
    "repair_code",
    "repair_evidence_plan",
    "downgrade_claim",
    "stop_weak_branch",
    "stop_no_progress",
    "stop_budget_exhausted",
    "blocked",
]


class AdaptiveQuestionAnswerProposal(StrictModel):
    question_id: str = Field(min_length=1)
    category: Literal[
        "implementation_fidelity",
        "numerical_validity",
        "baseline_control_adequacy",
        "evidence_sufficiency",
        "claim_scope",
        "repair_sufficiency",
        "stopping",
    ]
    status: Literal["pass", "fail", "unknown", "not_applicable"]
    explanation: str = Field(min_length=1)
    evidence_artifact_ids: list[str] = Field(default_factory=list)
    blocking: bool
    recommended_fix_optional: str | None = None


class AdaptiveQuestionerDecisionProposal(StrictModel):
    answers: list[AdaptiveQuestionAnswerProposal] = Field(min_length=1)
    recommended_action: AdaptiveActionProposal
    rationale: str = Field(min_length=1)
    repair_instructions: list[str] = Field(default_factory=list)
    unresolved_questions: list[str] = Field(default_factory=list)
    claim_disposition: Literal[
        "supported",
        "negative_result",
        "inconclusive",
        "deferred",
        "rejected",
    ]
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False
    creates_real_world_validation: Literal[False] = False
    creates_verified_theorem: Literal[False] = False
    novelty_proven: Literal[False] = False


class AdaptiveQuestionerEnvelope(StrictModel):
    decisions: list[AdaptiveQuestionerDecisionProposal] = Field(min_length=1, max_length=1)


@dataclass(frozen=True)
class AdaptiveQuestionerResponse:
    prompt_text: str
    requested_output_schema: dict[str, Any]
    raw_response: dict[str, Any]
    accepted: AdaptiveQuestionerDecisionProposal | None
    rejection_reasons: list[str]


class AdaptiveQuestionerClient(Protocol):
    backend_name: str
    backend_kind: BackendKind
    model: str
    fallback_used: bool
    fallback_disclosed: bool

    def review_evidence(
        self,
        *,
        prompt_id: str,
        questions_payload: list[dict[str, Any]],
        context_payload: dict[str, Any],
    ) -> AdaptiveQuestionerResponse: ...


@dataclass
class OpenAIAdaptiveQuestioner:
    """Explicitly gated OpenAI questioner without scientific fallback authority."""

    api_key: str = field(repr=False)
    model: str
    transport: LLMTransport = field(
        default_factory=lambda: OpenAIResponsesTransport(
            schema_name="factori_adaptive_questioner",
            nullable_optional_fields=False,
            max_output_tokens=24_000,
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
                "External calls are disabled. Set allow_external_calls=true for adaptive "
                "scientific questioning."
            )
        if not self.api_key.strip():
            raise AdapterMissingCredentials(
                "OpenAI adaptive scientific questioning requested without an API key."
            )
        if not self.model.strip():
            raise ValueError("OpenAI adaptive scientific questioning requires a model name.")

    def review_evidence(
        self,
        *,
        prompt_id: str,
        questions_payload: list[dict[str, Any]],
        context_payload: dict[str, Any],
    ) -> AdaptiveQuestionerResponse:
        prompt, schema = build_adaptive_questioner_prompt(
            prompt_id=prompt_id,
            questions_payload=questions_payload,
            context_payload=context_payload,
        )
        raw = self.transport.create_response(
            api_key=self.api_key,
            model=self.model,
            prompt=prompt,
            response_schema=schema,
        )
        payload = _json_object(raw)
        accepted, reasons = parse_adaptive_questioner_response(
            payload,
            questions_payload=questions_payload,
        )
        return AdaptiveQuestionerResponse(prompt, schema, payload, accepted, reasons)


def build_adaptive_questioner_prompt(
    *,
    prompt_id: str,
    questions_payload: list[dict[str, Any]],
    context_payload: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    prompt = (
        f"Prompt id: {prompt_id}\n"
        "Act as a bounded scientific questioner over one executed evidence package. Answer "
        "every supplied question exactly once using only the package, generated code, safety "
        "audit, sandbox execution, metric sources, controls, and deterministic diagnostics in "
        "the context. Inspect whether the code faithfully implements the declared model, "
        "baseline, controls, metrics, and workload. Distinguish implementation defects from "
        "an honest negative scientific result. A negative result is acceptable only when the "
        "implementation, convergence, controls, output contract, and metric provenance are "
        "trustworthy. Recommend code repair for implementation defects and evidence-plan repair "
        "for invalid baselines, controls, DGPs, metrics, or contracts. Do not tune thresholds, "
        "DGPs, metrics, or claims to make an observed result favorable. Generated experiments "
        "intentionally represent required non-output files as compact entries under "
        "output.json.logical_artifacts; do not require separate physical files when deterministic "
        "contract validation confirms those entries are present. Assess whether their content is "
        "substantive and execution-derived instead. Stop a false bridge or "
        "scientifically empty branch instead of inventing a replacement branch. Do not invent "
        "metrics or evidence, upgrade symbolic work to proof, establish novelty, claim real-world "
        "validation, or claim publication readiness. Return exactly one structured decision.\n\n"
        "For each answer, status=pass or status=not_applicable requires blocking=false. "
        f"Questions:\n{json.dumps(questions_payload, indent=2, sort_keys=True)}\n\n"
        f"Evidence context:\n{json.dumps(context_payload, indent=2, sort_keys=True)}"
    )
    return prompt, AdaptiveQuestionerEnvelope.model_json_schema()


def parse_adaptive_questioner_response(
    payload: dict[str, Any],
    *,
    questions_payload: list[dict[str, Any]],
) -> tuple[AdaptiveQuestionerDecisionProposal | None, list[str]]:
    raw = payload.get("decisions")
    if isinstance(raw, dict):
        raw = [raw]
    elif raw is None and {
        "answers",
        "recommended_action",
        "rationale",
    }.issubset(payload):
        raw = [payload]
    if not isinstance(raw, list) or not raw:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="adaptive_questioner",
            message="adaptive questioner response must contain exactly one decision",
        )
    candidates = [
        _validated_decision_candidate(item, questions_payload=questions_payload)
        for item in raw
    ]
    if len(raw) == 1:
        return candidates[0]

    valid = [decision for decision, reasons in candidates if decision and not reasons]
    unique = {
        json.dumps(item.model_dump(mode="json"), sort_keys=True): item for item in valid
    }
    if len(unique) == 1:
        return next(iter(unique.values())), []
    raise AdapterResponseParseError(
        backend="openai",
        provider="openai",
        operation="adaptive_questioner",
        message="adaptive questioner response must contain exactly one decision",
    )


def _validated_decision_candidate(
    payload: Any,
    *,
    questions_payload: list[dict[str, Any]],
) -> tuple[AdaptiveQuestionerDecisionProposal | None, list[str]]:
    try:
        decision = AdaptiveQuestionerDecisionProposal.model_validate(payload)
    except ValidationError as exc:
        return None, [str(exc)]
    decision = decision.model_copy(
        update={
            "answers": [
                answer.model_copy(update={"blocking": False})
                if answer.status in {"pass", "not_applicable"} and answer.blocking
                else answer
                for answer in decision.answers
            ]
        }
    )

    expected = {str(item["question_id"]): str(item["category"]) for item in questions_payload}
    observed = [answer.question_id for answer in decision.answers]
    reasons: list[str] = []
    if len(observed) != len(set(observed)):
        reasons.append("adaptive questioner returned duplicate question ids")
    if set(observed) != set(expected):
        reasons.append("adaptive questioner did not answer exactly the selected questions")
    for answer in decision.answers:
        if answer.question_id in expected and answer.category != expected[answer.question_id]:
            reasons.append(f"category mismatch for question {answer.question_id}")
    if decision.recommended_action in {"repair_code", "repair_evidence_plan"} and not (
        decision.repair_instructions
    ):
        reasons.append("repair recommendation lacks explicit repair instructions")
    return (None, reasons) if reasons else (decision, [])


def _json_object(raw: Any) -> dict[str, Any]:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="adaptive_questioner",
            message="adaptive questioner response was not valid JSON",
            cause=exc,
        ) from exc
    if not isinstance(payload, dict):
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="adaptive_questioner",
            message="adaptive questioner response must be a JSON object",
        )
    return payload


__all__ = [
    "AdaptiveQuestionAnswerProposal",
    "AdaptiveQuestionerClient",
    "AdaptiveQuestionerDecisionProposal",
    "AdaptiveQuestionerEnvelope",
    "AdaptiveQuestionerResponse",
    "OpenAIAdaptiveQuestioner",
    "build_adaptive_questioner_prompt",
    "parse_adaptive_questioner_response",
]
