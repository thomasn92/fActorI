"""Gated structured LLM planning for scientific routes and execution specs."""

from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from pydantic import Field, ValidationError, field_validator, model_validator

from factori.adapters.errors import (
    AdapterExternalCallsDisabled,
    AdapterMissingCredentials,
    AdapterResponseParseError,
    AdapterTransportError,
)
from factori.adapters.llm_real import LLMTransport, OpenAIResponsesTransport
from factori.schemas import (
    BackendKind,
    BranchRouteType,
    LLMRoutePlanningPrompt,
    RouteExecutionInputContract,
    RouteExecutionOutputContract,
    StrictModel,
)

ROUTE_ALLOWED_LABELS: dict[BranchRouteType, tuple[str, ...]] = {
    BranchRouteType.SYNTHETIC_EXPERIMENT: (
        "SyntheticExperimentEvidence",
        "NegativeResult",
        "InconclusiveResult",
    ),
    BranchRouteType.BENCHMARK_TOURNAMENT: (
        "BenchmarkEvidence",
        "NegativeResult",
        "InconclusiveResult",
    ),
    BranchRouteType.COUNTEREXAMPLE_SEARCH: (
        "CounterexampleEvidence",
        "NegativeResult",
        "InconclusiveResult",
    ),
    BranchRouteType.SYMBOLIC_DERIVATION: (
        "SymbolicDerivationDraft",
        "InconclusiveResult",
    ),
    BranchRouteType.APPLIED_MATH_REDUCTION: (
        "SymbolicReductionDraft",
        "InconclusiveResult",
    ),
    BranchRouteType.PROOF_PLAN: ("ProofPlanDraft", "InconclusiveResult"),
    BranchRouteType.LITERATURE_NOVELTY_CHECK: (
        "RetrievalNoveltyAssessment",
        "InconclusiveResult",
    ),
    BranchRouteType.DEFER_INSUFFICIENT_SUBSTRATE: ("UnsupportedRouteDeferred",),
    BranchRouteType.REJECT_FALSE_BRIDGE: ("RejectedFalseBridge",),
}

MANDATORY_FORBIDDEN_CLAIMS = (
    "real-world validation",
    "broad empirical validation",
    "formal proof or theorem verification before checker evidence",
    "novelty established",
    "publication ready",
)

_CODE_ROUTES = {
    BranchRouteType.SYNTHETIC_EXPERIMENT,
    BranchRouteType.BENCHMARK_TOURNAMENT,
    BranchRouteType.COUNTEREXAMPLE_SEARCH,
}


class RouteDecisionProposal(StrictModel):
    route_type: BranchRouteType
    fallback_route_type_optional: BranchRouteType | None = None
    route_confidence: float = Field(ge=0.0, le=1.0)
    scientific_reason: str = Field(min_length=1)
    why_not_other_routes: list[str] = Field(min_length=1)
    required_artifacts: list[str] = Field(min_length=1)
    allowed_evidence_labels: list[str] = Field(min_length=1)
    forbidden_claims: list[str] = Field(min_length=1)
    defer_or_reject_reason_optional: str | None = None
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False


class RouteParameterValues(StrictModel):
    """Closed OpenAI-compatible parameter vocabulary for executable routes."""

    sample_size: int | None = Field(default=None, ge=1)
    n_units: int | None = Field(default=None, ge=1)
    n_timepoints: int | None = Field(default=None, ge=1)
    time_horizon: int | None = Field(default=None, ge=1)
    noise_level: float | None = Field(default=None, ge=0.0)
    confounding_strength: float | None = Field(default=None, ge=0.0)
    overlap_floor: float | None = Field(default=None, ge=0.0, le=1.0)
    train_test_split: float | None = Field(default=None, ge=0.0, le=1.0)
    validation_split: float | None = Field(default=None, ge=0.0, le=1.0)
    regime: str | None = None
    regime_settings: list[str] | None = None
    perturbation_levels: list[float] | None = None
    window_size: int | None = Field(default=None, ge=1)
    bootstrap_replicates: int | None = Field(default=None, ge=1)
    rank_k: int | None = Field(default=None, ge=1)
    seed: int | None = Field(default=None, ge=0)
    route_family: str | None = None
    bounded: bool | None = None

    @model_validator(mode="after")
    def _require_one_parameter(self) -> RouteParameterValues:
        if not self.model_dump(exclude_none=True):
            raise ValueError("route parameters must contain at least one concrete value")
        return self


class RoutePlanningInputContract(RouteExecutionInputContract):
    """LLM-facing input contract with required executable route parameters."""

    route_parameters: RouteParameterValues

    @field_validator("route_parameters", mode="before")
    @classmethod
    def _require_route_parameters(cls, value: Any) -> Any:
        if not value:
            raise ValueError("route parameters must be non-empty")
        return value


class ExecutionSpecProposal(StrictModel):
    route_type: BranchRouteType
    title: str = Field(min_length=1)
    objective: str = Field(min_length=1)
    input_contract: RoutePlanningInputContract
    output_contract: RouteExecutionOutputContract
    baseline_plan: list[str] = Field(min_length=1)
    control_plan: list[str] = Field(min_length=1)
    negative_control_plan: list[str] = Field(min_length=1)
    robustness_plan: list[str] = Field(min_length=1)
    metric_plan: list[str] = Field(min_length=1)
    success_criteria: list[str] = Field(min_length=1)
    failure_criteria: list[str] = Field(min_length=1)
    proof_obligations: list[str] = Field(default_factory=list)
    formalization_target_optional: str | None = None
    retrieval_queries: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(min_length=1)
    sandbox_requirements: list[str] = Field(default_factory=list)
    allowed_evidence_labels: list[str] = Field(min_length=1)
    forbidden_claims: list[str] = Field(min_length=1)
    execution_backend_required: str = Field(min_length=1)
    requires_code_generation: bool
    requires_literature_retrieval: bool
    requires_symbolic_checker: bool
    requires_human_review: bool
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False
    creates_real_world_validation: Literal[False] = False

    @field_validator("input_contract", mode="before")
    @classmethod
    def _coerce_public_input_contract(cls, value: Any) -> Any:
        if isinstance(value, RouteExecutionInputContract):
            return value.model_dump(mode="python")
        return value


class RoutePlanningScoreProposal(StrictModel):
    route_fit: float = Field(ge=0.0, le=1.0)
    baseline_quality: float = Field(ge=0.0, le=1.0)
    control_quality: float = Field(ge=0.0, le=1.0)
    metric_clarity: float = Field(ge=0.0, le=1.0)
    execution_feasibility: float = Field(ge=0.0, le=1.0)
    claim_safety: float = Field(ge=0.0, le=1.0)
    failure_mode_value: float = Field(ge=0.0, le=1.0)
    paper_coherence: float = Field(ge=0.0, le=1.0)
    false_bridge_penalty: float = Field(ge=0.0, le=1.0)
    tautology_penalty: float = Field(ge=0.0, le=1.0)
    scope_risk_penalty: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)
    score_explanation: str = Field(min_length=1)


class RoutePlanningProposalItem(StrictModel):
    decision: RouteDecisionProposal
    execution_spec: ExecutionSpecProposal
    score: RoutePlanningScoreProposal


class RoutePlanningProposalEnvelope(StrictModel):
    plans: list[RoutePlanningProposalItem] = Field(min_length=1, max_length=1)


@dataclass(frozen=True)
class RoutePlanningResponse:
    prompt: LLMRoutePlanningPrompt
    raw_response: dict[str, Any]
    accepted: RoutePlanningProposalItem | None
    rejection_reasons: list[str]
    repair_actions: list[str]


class RoutePlanningClient(Protocol):
    backend_name: str
    backend_kind: BackendKind
    model: str
    fallback_used: bool
    fallback_disclosed: bool

    def plan_route(
        self,
        *,
        prompt_id: str,
        substrate_payload: dict[str, Any],
        source_metadata_payload: dict[str, Any],
        retrieval_context_payload: dict[str, Any],
    ) -> RoutePlanningResponse: ...


@dataclass
class OpenAILLMRoutePlanner:
    """Explicitly gated OpenAI route/spec planner without deterministic fallback."""

    api_key: str = field(repr=False)
    model: str
    transport: LLMTransport = field(
        default_factory=lambda: OpenAIResponsesTransport(
            schema_name="factori_llm_routes",
        )
    )
    allow_external_calls: bool = False
    max_transport_retries: int = 1
    retry_backoff_seconds: float = 0.5
    backend_name: str = field(default="llm-openai", init=False)
    backend_kind: BackendKind = field(default=BackendKind.LLM_OPENAI, init=False)
    fallback_used: bool = field(default=False, init=False)
    fallback_disclosed: bool = field(default=True, init=False)

    def __post_init__(self) -> None:
        if not self.allow_external_calls:
            raise AdapterExternalCallsDisabled(
                "External calls are disabled. Set allow_external_calls=true for LLM routes."
            )
        if not self.api_key.strip():
            raise AdapterMissingCredentials(
                "OpenAI LLM route planning requested without an API key."
            )
        if not self.model.strip():
            raise ValueError("OpenAI LLM route planning requires a model name.")
        if self.max_transport_retries < 0:
            raise ValueError("max_transport_retries must be non-negative.")
        if self.retry_backoff_seconds < 0:
            raise ValueError("retry_backoff_seconds must be non-negative.")

    def plan_route(
        self,
        *,
        prompt_id: str,
        substrate_payload: dict[str, Any],
        source_metadata_payload: dict[str, Any],
        retrieval_context_payload: dict[str, Any],
    ) -> RoutePlanningResponse:
        prompt = build_llm_route_planning_prompt(
            prompt_id=prompt_id,
            backend_name=self.backend_name,
            model=self.model,
            substrate_payload=substrate_payload,
            source_metadata_payload=source_metadata_payload,
            retrieval_context_payload=retrieval_context_payload,
        )
        raw = self._create_response_with_retries(prompt)
        payload = _json_object(raw)
        accepted, reasons, repairs = parse_route_planning_response(payload)
        return RoutePlanningResponse(
            prompt=prompt,
            raw_response=payload,
            accepted=accepted,
            rejection_reasons=reasons,
            repair_actions=repairs,
        )

    def _create_response_with_retries(self, prompt: LLMRoutePlanningPrompt) -> Any:
        for attempt in range(self.max_transport_retries + 1):
            try:
                return self.transport.create_response(
                    api_key=self.api_key,
                    model=self.model,
                    prompt=prompt.prompt_text,
                    response_schema=prompt.requested_output_schema,
                )
            except AdapterTransportError as exc:
                if attempt >= self.max_transport_retries or not _is_retryable_transport_error(exc):
                    raise
                delay = self.retry_backoff_seconds * (2**attempt)
                if delay:
                    time.sleep(delay)
        raise AssertionError("unreachable")


def build_llm_route_planning_prompt(
    *,
    prompt_id: str,
    backend_name: str,
    model: str,
    substrate_payload: dict[str, Any],
    source_metadata_payload: dict[str, Any],
    retrieval_context_payload: dict[str, Any],
) -> LLMRoutePlanningPrompt:
    label_policy = {
        route.value: list(labels) for route, labels in ROUTE_ALLOWED_LABELS.items()
    }
    prompt_text = (
        "Choose exactly one scientifically appropriate route and construct exactly one bounded, "
        "non-executing specification for the supplied LLM ScientificSubstrate. Include one "
        "optional fallback route only when useful. The spec must define executable inputs and "
        "outputs, baselines, controls, negative controls, robustness checks, metrics, success and "
        "failure criteria, required artifacts, execution backend, and claim boundaries. Proof-plan "
        "routes require explicit proof obligations and a formalization target. Literature routes "
        "require retrieval queries and requires_literature_retrieval=true. Experiment, benchmark, "
        "and counterexample routes require code generation, sandbox requirements, and a non-empty "
        "input_contract.route_parameters object containing concrete named parameters such as "
        "sample_size, time_horizon, noise_level, regime_settings, train_test_split, and seed. "
        "For example, use {\"sample_size\": 1000, \"time_horizon\": 20, "
        "\"noise_level\": 0.1, \"regime_settings\": [\"null\", \"alternative\"], "
        "\"train_test_split\": 0.8, \"seed\": 17}; adapt the values to the substrate. "
        "Do not return null or {} for route_parameters on executable routes. Use exactly "
        "the route-specific allowed evidence labels in this policy; these are future permissions, "
        "not evidence created by this plan. Do not claim proof, novelty, real-world validation, "
        "publication readiness, or computed results. All score fields must be decimals from 0.0 "
        "to 1.0 inclusive; never use a 0-10 score scale. Return only the structured response.\n\n"
        f"Allowed-label policy:\n{json.dumps(label_policy, indent=2, sort_keys=True)}\n\n"
        f"Scientific substrate:\n{json.dumps(substrate_payload, indent=2, sort_keys=True)}\n\n"
        f"Source metadata:\n{json.dumps(source_metadata_payload, indent=2, sort_keys=True)}\n\n"
        "Retrieval context:\n"
        f"{json.dumps(retrieval_context_payload, indent=2, sort_keys=True)}"
    )
    return LLMRoutePlanningPrompt(
        prompt_id=prompt_id,
        backend_name=backend_name,
        model=model,
        source_substrate_id=str(substrate_payload["substrate_id"]),
        substrate_payload=substrate_payload,
        source_metadata_payload=source_metadata_payload,
        retrieval_context_payload=retrieval_context_payload,
        prompt_text=prompt_text,
        requested_output_schema=RoutePlanningProposalEnvelope.model_json_schema(),
    )


def parse_route_planning_response(
    payload: dict[str, Any],
) -> tuple[RoutePlanningProposalItem | None, list[str], list[str]]:
    prepared_payload, repairs = _prepare_route_planning_payload(payload)
    item, reasons, parser_repairs = _parse_prepared_route_planning_response(prepared_payload)
    return item, reasons, [*repairs, *parser_repairs]


def _parse_prepared_route_planning_response(
    payload: dict[str, Any],
) -> tuple[RoutePlanningProposalItem | None, list[str], list[str]]:
    raw_items = payload.get("plans")
    if not isinstance(raw_items, list) or len(raw_items) != 1:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="llm_route_planning",
            message="route planning response must contain exactly one plan",
        )
    try:
        item = RoutePlanningProposalItem.model_validate(raw_items[0])
    except ValidationError as exc:
        return None, [str(exc)], []
    repaired, repairs = _repair_forbidden_claim_boundaries(item)
    reasons = validate_route_planning_proposal(repaired)
    if reasons:
        return None, reasons, repairs
    return repaired, [], repairs


def _prepare_route_planning_payload(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Normalize JSON nulls for fields whose schema defaults are collections."""
    prepared = deepcopy(payload)
    repairs: list[str] = []
    raw_plans = prepared.get("plans")
    if not isinstance(raw_plans, list) or len(raw_plans) != 1:
        return prepared, repairs
    raw_item = raw_plans[0]
    if not isinstance(raw_item, dict):
        return prepared, repairs
    spec = raw_item.get("execution_spec")
    if isinstance(spec, dict):
        for field_name in ("proof_obligations", "retrieval_queries", "sandbox_requirements"):
            if spec.get(field_name) is None:
                spec[field_name] = []
                repairs.append(f"normalized null execution_spec.{field_name} to []")
        input_contract = spec.get("input_contract")
        if isinstance(input_contract, dict):
            for field_name in ("variables_and_notation", "assumptions", "metrics"):
                if input_contract.get(field_name) is None:
                    input_contract[field_name] = []
                    repairs.append(f"normalized null input_contract.{field_name} to []")
            if input_contract.get("route_parameters") is None:
                input_contract["route_parameters"] = {}
                repairs.append("normalized null input_contract.route_parameters to {}")
        output_contract = spec.get("output_contract")
        if isinstance(output_contract, dict):
            for field_name in ("required_metrics", "required_payload_fields"):
                if output_contract.get(field_name) is None:
                    output_contract[field_name] = []
                    repairs.append(f"normalized null output_contract.{field_name} to []")
    return prepared, repairs


def validate_route_planning_proposal(item: RoutePlanningProposalItem) -> list[str]:
    decision = item.decision
    spec = item.execution_spec
    reasons: list[str] = []
    if decision.route_type != spec.route_type:
        reasons.append("route decision and execution spec use different route types")
    expected_labels = set(ROUTE_ALLOWED_LABELS[decision.route_type])
    if set(decision.allowed_evidence_labels) != expected_labels:
        reasons.append("route decision allowed evidence labels do not match route policy")
    if set(spec.allowed_evidence_labels) != expected_labels:
        reasons.append("execution spec allowed evidence labels do not match route policy")
    if decision.fallback_route_type_optional == decision.route_type:
        reasons.append("fallback route must differ from the preferred route")

    if decision.route_type in _CODE_ROUTES:
        if not spec.requires_code_generation:
            reasons.append("experiment/benchmark/counterexample route requires code generation")
        if not spec.sandbox_requirements:
            reasons.append("experiment route lacks executable sandbox requirements")
        if spec.execution_backend_required.strip().lower() in {"", "none", "off"}:
            reasons.append("experiment route lacks an executable backend")
        if not spec.input_contract.route_parameters:
            reasons.append("experiment route lacks executable route parameters")
        if not spec.output_contract.required_metrics:
            reasons.append("experiment route lacks required output metrics")
    if decision.route_type == BranchRouteType.PROOF_PLAN and (
        not spec.proof_obligations or not spec.formalization_target_optional
    ):
        reasons.append("proof route lacks formalization target or proof obligations")
    if decision.route_type == BranchRouteType.LITERATURE_NOVELTY_CHECK and (
        not spec.requires_literature_retrieval or not spec.retrieval_queries
    ):
        reasons.append("literature route lacks retrieval requirement or queries")

    combined = " ".join(
        [
            decision.scientific_reason,
            *decision.why_not_other_routes,
            spec.title,
            spec.objective,
            *spec.success_criteria,
            *spec.failure_criteria,
        ]
    ).lower()
    forbidden_assertions = {
        "publication_ready=true": "route plan asserts publication readiness",
        "publication ready": "route plan asserts publication readiness",
        "real-world validation": "route plan asserts real-world validation",
        "real world validation": "route plan asserts real-world validation",
        "has been proven": "route plan claims proof without checker evidence",
        "is proven": "route plan claims proof without checker evidence",
        "is verified": "route plan claims verification without evidence",
        "establishes novelty": "route plan asserts novelty as fact",
    }
    reasons.extend(
        message
        for phrase, message in forbidden_assertions.items()
        if _contains_affirmative_forbidden_claim(combined, phrase)
    )
    return reasons


def _contains_affirmative_forbidden_claim(text: str, phrase: str) -> bool:
    """Reject authority claims while allowing explicit caveats and limitations."""
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text.lower()):
        for match in re.finditer(re.escape(phrase), sentence):
            before = sentence[: match.start()]
            after = sentence[match.end() :]
            if _has_forbidden_claim_negation(before, after):
                continue
            if phrase in {
                "real-world validation",
                "real world validation",
            } and not _has_affirmative_validation_context(before, after):
                continue
            return True
    return False


def _has_forbidden_claim_negation(before: str, after: str) -> bool:
    if re.search(
        r"\b(?:not|no|never|without|avoid|avoids|excluding|exclude|cannot|can't|"
        r"doesn't|does not|do not|don't|isn't|is not|unverified|unproven|"
        r"unresolved|remains open)\b",
        before,
    ):
        return True
    return bool(
        re.match(
            r"\s*(?:(?:claims?|evidence|support|results?|route|study|analysis|"
            r"use|assessment|path)s+)?(?:is|are|was|were|has been|have been|"
            r"remains?)?\s*(?:not|never|unverified|unproven|unresolved|unsupported|"
            r"forbidden|disallowed|absent|outside|out of scope|should not|must not|"
            r"cannot)\b",
            after,
        )
    )


def _has_affirmative_validation_context(before: str, after: str) -> bool:
    return bool(
        re.search(
            r"\b(?:achieves?|confirms?|constitutes?|creates?|demonstrates?|establishes?|"
            r"provides?|proves?|represents?|supports?|validates?|verifies?)\s+$",
            before,
        )
        or re.search(r"\b(?:is|was|becomes?)\s+$", before)
        or re.match(
            r"\s+(?:is|was|has been|can be)\s+(?:achieved|confirmed|demonstrated|"
            r"established|provided|proven|supported|validated|verified)\b",
            after,
        )
    )


def _is_retryable_transport_error(error: AdapterTransportError) -> bool:
    """Retry transient transport failures, never rejected requests or bad schemas."""
    if error.status_code is None:
        return True
    return error.status_code in {408, 409, 429} or error.status_code >= 500


def _repair_forbidden_claim_boundaries(
    item: RoutePlanningProposalItem,
) -> tuple[RoutePlanningProposalItem, list[str]]:
    repairs: list[str] = []

    def merged(values: list[str], owner: str) -> list[str]:
        normalized = {value.strip().lower() for value in values}
        missing = [value for value in MANDATORY_FORBIDDEN_CLAIMS if value not in normalized]
        if missing:
            repairs.append(f"added mandatory forbidden claims to {owner}")
        return [*values, *missing]

    decision = item.decision.model_copy(
        update={
            "forbidden_claims": merged(item.decision.forbidden_claims, "route decision")
        }
    )
    spec = item.execution_spec.model_copy(
        update={"forbidden_claims": merged(item.execution_spec.forbidden_claims, "execution spec")}
    )
    return item.model_copy(update={"decision": decision, "execution_spec": spec}), repairs


def _json_object(raw: Any) -> dict[str, Any]:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="llm_route_planning",
            message="route planning response was not valid JSON",
            cause=exc,
        ) from exc
    if not isinstance(payload, dict):
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="llm_route_planning",
            message="route planning response must be a JSON object",
        )
    return payload


__all__ = [
    "ExecutionSpecProposal",
    "MANDATORY_FORBIDDEN_CLAIMS",
    "OpenAILLMRoutePlanner",
    "ROUTE_ALLOWED_LABELS",
    "RouteDecisionProposal",
    "RouteParameterValues",
    "RoutePlanningInputContract",
    "RoutePlanningClient",
    "RoutePlanningProposalEnvelope",
    "RoutePlanningProposalItem",
    "RoutePlanningResponse",
    "RoutePlanningScoreProposal",
    "build_llm_route_planning_prompt",
    "parse_route_planning_response",
    "validate_route_planning_proposal",
]
