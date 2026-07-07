"""Gated structured LLM planning for hybrid evidence packages."""

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
from factori.schemas import BackendKind, EvidenceArtifactType, StrictModel

PACKAGE_ALLOWED_LABELS: dict[EvidenceArtifactType, tuple[str, ...]] = {
    EvidenceArtifactType.SYMBOLIC_REDUCTION: ("SymbolicReductionDraft", "InconclusiveResult"),
    EvidenceArtifactType.SYMBOLIC_DERIVATION: (
        "SymbolicDerivationDraft",
        "InconclusiveResult",
    ),
    EvidenceArtifactType.PROOF_PLAN: ("ProofPlanDraft", "InconclusiveResult"),
    EvidenceArtifactType.NUMERICAL_ILLUSTRATION: (
        "NumericalIllustrationEvidence",
        "InconclusiveResult",
    ),
    EvidenceArtifactType.SYNTHETIC_EXPERIMENT: (
        "SyntheticExperimentEvidence",
        "NegativeResult",
        "InconclusiveResult",
    ),
    EvidenceArtifactType.BENCHMARK_TOURNAMENT: (
        "BenchmarkEvidence",
        "NegativeResult",
        "InconclusiveResult",
    ),
    EvidenceArtifactType.COUNTEREXAMPLE_SEARCH: (
        "CounterexampleEvidence",
        "NegativeResult",
        "InconclusiveResult",
    ),
    EvidenceArtifactType.LITERATURE_NOVELTY_CHECK: (
        "RetrievalNoveltyAssessment",
        "InconclusiveResult",
    ),
    EvidenceArtifactType.NEGATIVE_CONTROL: ("NegativeControlEvidence", "InconclusiveResult"),
    EvidenceArtifactType.ROBUSTNESS_SWEEP: ("RobustnessSweepEvidence", "InconclusiveResult"),
    EvidenceArtifactType.DEFER_UNAVAILABLE_CHECKER: ("UnsupportedRouteDeferred",),
    EvidenceArtifactType.DEFER_INSUFFICIENT_SUPPORT: ("UnsupportedRouteDeferred",),
    EvidenceArtifactType.REJECT_FALSE_BRIDGE: ("RejectedFalseBridge",),
}

MANDATORY_PACKAGE_FORBIDDEN_CLAIMS = (
    "real-world validation",
    "verified theorem",
    "novelty proven",
    "underuse proven",
    "publication ready",
    "general domain truth",
)

_CODE_ARTIFACT_TYPES = {
    EvidenceArtifactType.NUMERICAL_ILLUSTRATION,
    EvidenceArtifactType.SYNTHETIC_EXPERIMENT,
    EvidenceArtifactType.BENCHMARK_TOURNAMENT,
    EvidenceArtifactType.COUNTEREXAMPLE_SEARCH,
    EvidenceArtifactType.NEGATIVE_CONTROL,
    EvidenceArtifactType.ROBUSTNESS_SWEEP,
}
_SYMBOLIC_TYPES = {
    EvidenceArtifactType.SYMBOLIC_REDUCTION,
    EvidenceArtifactType.SYMBOLIC_DERIVATION,
    EvidenceArtifactType.PROOF_PLAN,
}


class EvidenceArtifactPlanProposal(StrictModel):
    artifact_type: EvidenceArtifactType
    purpose: str = Field(min_length=1)
    claim_component_supported: str = Field(min_length=1)
    input_contract: dict[str, Any]
    output_contract: dict[str, Any]
    baseline_or_comparator_plan: list[str] = Field(default_factory=list)
    control_plan_optional: list[str] | None = None
    negative_control_plan_optional: list[str] | None = None
    metric_plan_optional: list[str] | None = None
    symbolic_obligations_optional: list[str] | None = None
    retrieval_requirements_optional: list[str] | None = None
    checker_requirements_optional: list[str] | None = None
    execution_backend_required: str = Field(min_length=1)
    requires_code_generation: bool
    requires_local_execution: bool
    requires_retrieval: bool
    requires_symbolic_checker: bool
    requires_llm_drafting: bool
    allowed_evidence_labels: list[str] = Field(min_length=1)
    forbidden_claims: list[str] = Field(min_length=1)
    success_criteria: list[str] = Field(min_length=1)
    failure_criteria: list[str] = Field(min_length=1)
    publication_ready: Literal[False] = False
    creates_scientific_validation: Literal[False] = False
    creates_real_world_validation: Literal[False] = False


class HybridEvidencePackageProposal(StrictModel):
    title: str = Field(min_length=1)
    primary_claim_draft: str = Field(min_length=1)
    allowed_claim_scope: str = Field(min_length=1)
    package_rationale: str = Field(min_length=1)
    artifact_plans: list[EvidenceArtifactPlanProposal] = Field(min_length=1)
    minimum_required_artifacts: list[str] = Field(min_length=1)
    optional_supporting_artifacts: list[str] = Field(default_factory=list)
    artifact_dependency_graph: dict[str, list[str]] = Field(default_factory=dict)
    claim_support_map: dict[str, list[str]] = Field(default_factory=dict)
    known_gaps: list[str] = Field(min_length=1)
    unresolved_obligations: list[str] = Field(min_length=1)
    recommended_next_action: str = Field(min_length=1)
    publication_ready: Literal[False] = False
    creates_real_world_validation: Literal[False] = False
    creates_verified_theorem: Literal[False] = False
    novelty_proven: Literal[False] = False
    creates_scientific_validation: Literal[False] = False


class HybridEvidencePackageScoreProposal(StrictModel):
    claim_specificity: float = Field(ge=0.0, le=1.0)
    artifact_coherence: float = Field(ge=0.0, le=1.0)
    verification_feasibility: float = Field(ge=0.0, le=1.0)
    baseline_quality: float = Field(ge=0.0, le=1.0)
    control_quality: float = Field(ge=0.0, le=1.0)
    negative_control_quality: float = Field(ge=0.0, le=1.0)
    symbolic_obligation_clarity: float = Field(ge=0.0, le=1.0)
    retrieval_need_clarity: float = Field(ge=0.0, le=1.0)
    execution_feasibility: float = Field(ge=0.0, le=1.0)
    paper_shape_clarity: float = Field(ge=0.0, le=1.0)
    false_bridge_penalty: float = Field(ge=0.0, le=1.0)
    tautology_penalty: float = Field(ge=0.0, le=1.0)
    scope_risk_penalty: float = Field(ge=0.0, le=1.0)
    final_score: float = Field(ge=0.0, le=1.0)
    score_explanation: str = Field(min_length=1)


class HybridEvidencePackageProposalItem(StrictModel):
    package: HybridEvidencePackageProposal
    score: HybridEvidencePackageScoreProposal


class HybridEvidencePackageEnvelope(StrictModel):
    packages: list[HybridEvidencePackageProposalItem] = Field(min_length=1, max_length=1)


class HybridEvidenceDraftArtifact(StrictModel):
    artifact_type: EvidenceArtifactType
    definitions: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    steps_or_plan: list[str] = Field(default_factory=list)
    unresolved_obligations: list[str] = Field(min_length=1)
    checker_status: Literal["not_checked"] = "not_checked"
    novelty_risk_assessment: str | None = None
    overlap_risks: list[str] = Field(default_factory=list)
    closest_prior_work: list[str] = Field(default_factory=list)
    underuse_hypothesis: str | None = None
    novelty_proven: Literal[False] = False
    creates_verified_theorem: Literal[False] = False
    creates_real_world_validation: Literal[False] = False
    publication_ready: Literal[False] = False


class HybridEvidenceDraftEnvelope(StrictModel):
    artifact: HybridEvidenceDraftArtifact


@dataclass(frozen=True)
class HybridEvidencePackageResponse:
    prompt_text: str
    requested_output_schema: dict[str, Any]
    raw_response: dict[str, Any]
    accepted: HybridEvidencePackageProposalItem | None
    rejection_reasons: list[str]
    repair_actions: list[str]


@dataclass(frozen=True)
class HybridEvidenceDraftResponse:
    prompt_text: str
    requested_output_schema: dict[str, Any]
    raw_response: dict[str, Any]
    accepted: HybridEvidenceDraftArtifact | None
    rejection_reasons: list[str]


class HybridEvidenceClient(Protocol):
    backend_name: str
    backend_kind: BackendKind
    model: str
    fallback_used: bool
    fallback_disclosed: bool

    def plan_package(
        self,
        *,
        prompt_id: str,
        substrate_payload: dict[str, Any],
        route_payload: dict[str, Any] | None,
        retrieval_context_payload: dict[str, Any] | None,
    ) -> HybridEvidencePackageResponse: ...

    def draft_artifact(
        self,
        *,
        prompt_id: str,
        package_payload: dict[str, Any],
        artifact_plan_payload: dict[str, Any],
        retrieval_context_payload: dict[str, Any] | None,
    ) -> HybridEvidenceDraftResponse: ...


@dataclass
class OpenAIHybridEvidencePlanner:
    """Explicitly gated OpenAI hybrid package planner without deterministic fallback."""

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
                "External calls are disabled. Set allow_external_calls=true for hybrid evidence."
            )
        if not self.api_key.strip():
            raise AdapterMissingCredentials(
                "OpenAI hybrid evidence planning requested without an API key."
            )
        if not self.model.strip():
            raise ValueError("OpenAI hybrid evidence planning requires a model name.")

    def plan_package(
        self,
        *,
        prompt_id: str,
        substrate_payload: dict[str, Any],
        route_payload: dict[str, Any] | None,
        retrieval_context_payload: dict[str, Any] | None,
    ) -> HybridEvidencePackageResponse:
        prompt, schema = build_hybrid_package_prompt(
            prompt_id=prompt_id,
            substrate_payload=substrate_payload,
            route_payload=route_payload,
            retrieval_context_payload=retrieval_context_payload,
        )
        raw = self.transport.create_response(
            api_key=self.api_key,
            model=self.model,
            prompt=prompt,
            response_schema=schema,
        )
        payload = _json_object(raw, operation="hybrid_evidence_package")
        accepted, reasons, repairs = parse_hybrid_package_response(payload)
        return HybridEvidencePackageResponse(
            prompt_text=prompt,
            requested_output_schema=schema,
            raw_response=payload,
            accepted=accepted,
            rejection_reasons=reasons,
            repair_actions=repairs,
        )

    def draft_artifact(
        self,
        *,
        prompt_id: str,
        package_payload: dict[str, Any],
        artifact_plan_payload: dict[str, Any],
        retrieval_context_payload: dict[str, Any] | None,
    ) -> HybridEvidenceDraftResponse:
        prompt, schema = build_hybrid_draft_prompt(
            prompt_id=prompt_id,
            package_payload=package_payload,
            artifact_plan_payload=artifact_plan_payload,
            retrieval_context_payload=retrieval_context_payload,
        )
        raw = self.transport.create_response(
            api_key=self.api_key,
            model=self.model,
            prompt=prompt,
            response_schema=schema,
        )
        payload = _json_object(raw, operation="hybrid_evidence_draft")
        accepted, reasons = parse_hybrid_draft_response(payload)
        return HybridEvidenceDraftResponse(
            prompt_text=prompt,
            requested_output_schema=schema,
            raw_response=payload,
            accepted=accepted,
            rejection_reasons=reasons,
        )


def build_hybrid_package_prompt(
    *,
    prompt_id: str,
    substrate_payload: dict[str, Any],
    route_payload: dict[str, Any] | None,
    retrieval_context_payload: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    label_policy = {
        artifact_type.value: list(labels)
        for artifact_type, labels in PACKAGE_ALLOWED_LABELS.items()
    }
    prompt = (
        f"Prompt id: {prompt_id}\n"
        "Plan exactly one hybrid evidence package for the supplied ScientificSubstrate. "
        "Choose any scientifically appropriate mix of symbolic, numerical, benchmark, "
        "counterexample, retrieval, proof-plan, negative-control, or robustness artifacts. "
        "Every artifact plan must define its purpose, input/output contract, baselines or "
        "comparators when relevant, controls, negative controls, metric plan when relevant, "
        "success/failure criteria, allowed evidence labels from the policy, forbidden claims, "
        "and unresolved obligations. Do not claim proof, novelty, underuse, real-world "
        "validation, publication readiness, or computed results. Return only the structured "
        "response.\n\n"
        f"Allowed-label policy:\n{json.dumps(label_policy, indent=2, sort_keys=True)}\n\n"
        f"Scientific substrate:\n{json.dumps(substrate_payload, indent=2, sort_keys=True)}\n\n"
        f"Route/spec context:\n{json.dumps(route_payload or {}, indent=2, sort_keys=True)}\n\n"
        "Retrieval context:\n"
        f"{json.dumps(retrieval_context_payload or {}, indent=2, sort_keys=True)}"
    )
    return prompt, HybridEvidencePackageEnvelope.model_json_schema()


def build_hybrid_draft_prompt(
    *,
    prompt_id: str,
    package_payload: dict[str, Any],
    artifact_plan_payload: dict[str, Any],
    retrieval_context_payload: dict[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    prompt = (
        f"Prompt id: {prompt_id}\n"
        "Draft only the bounded artifact requested by the artifact plan. Symbolic and proof-plan "
        "artifacts must be draft-labeled with unresolved obligations and checker_status="
        "not_checked. Literature novelty artifacts must be novelty-risk assessments with "
        "novelty_proven=false. Do not claim a verified theorem, established novelty, real-world "
        "validation, or publication readiness. Return only the structured response.\n\n"
        f"Package:\n{json.dumps(package_payload, indent=2, sort_keys=True)}\n\n"
        f"Artifact plan:\n{json.dumps(artifact_plan_payload, indent=2, sort_keys=True)}\n\n"
        "Retrieval context:\n"
        f"{json.dumps(retrieval_context_payload or {}, indent=2, sort_keys=True)}"
    )
    return prompt, HybridEvidenceDraftEnvelope.model_json_schema()


def parse_hybrid_package_response(
    payload: dict[str, Any],
) -> tuple[HybridEvidencePackageProposalItem | None, list[str], list[str]]:
    raw_items = payload.get("packages")
    if not isinstance(raw_items, list) or len(raw_items) != 1:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation="hybrid_evidence_package",
            message="hybrid evidence response must contain exactly one package",
        )
    try:
        item = HybridEvidencePackageProposalItem.model_validate(raw_items[0])
    except ValidationError as exc:
        return None, [str(exc)], []
    repaired, repairs = _repair_forbidden_claim_boundaries(item)
    reasons = validate_hybrid_package_proposal(repaired)
    if reasons:
        return None, reasons, repairs
    return repaired, [], repairs


def parse_hybrid_draft_response(
    payload: dict[str, Any],
) -> tuple[HybridEvidenceDraftArtifact | None, list[str]]:
    try:
        envelope = HybridEvidenceDraftEnvelope.model_validate(payload)
    except ValidationError as exc:
        return None, [str(exc)]
    artifact = envelope.artifact
    reasons: list[str] = []
    if artifact.novelty_proven:
        reasons.append("draft artifact claims novelty is proven")
    if artifact.creates_verified_theorem:
        reasons.append("draft artifact claims verified theorem status")
    if artifact.creates_real_world_validation:
        reasons.append("draft artifact claims real-world validation")
    if artifact.checker_status != "not_checked":
        reasons.append("draft artifact must remain checker_status=not_checked")
    combined = " ".join(
        [
            *artifact.definitions,
            *artifact.assumptions,
            *artifact.steps_or_plan,
            *artifact.unresolved_obligations,
            artifact.novelty_risk_assessment or "",
            artifact.underuse_hypothesis or "",
        ]
    ).lower()
    if "publication ready" in combined:
        reasons.append("draft artifact asserts publication readiness")
    if "has been proven" in combined or "is proven" in combined:
        reasons.append("draft artifact claims proof without checker evidence")
    if "establishes novelty" in combined:
        reasons.append("draft artifact asserts novelty as fact")
    if reasons:
        return None, reasons
    return artifact, []


def validate_hybrid_package_proposal(item: HybridEvidencePackageProposalItem) -> list[str]:
    package = item.package
    reasons: list[str] = []
    if not package.primary_claim_draft.strip():
        reasons.append("package lacks a primary claim draft")
    if not package.allowed_claim_scope.strip():
        reasons.append("package lacks an allowed claim scope")
    if not package.artifact_plans:
        reasons.append("package lacks artifact plans")
    checkable = False
    for plan in package.artifact_plans:
        allowed = set(PACKAGE_ALLOWED_LABELS[plan.artifact_type])
        labels = set(plan.allowed_evidence_labels)
        if not labels or not labels.issubset(allowed):
            reasons.append(
                f"{plan.artifact_type.value} uses incompatible evidence labels: "
                + ", ".join(sorted(labels))
            )
        if plan.artifact_type in _CODE_ARTIFACT_TYPES:
            checkable = True
            if not plan.requires_code_generation or not plan.requires_local_execution:
                reasons.append(f"{plan.artifact_type.value} must require code and local execution")
            if not plan.metric_plan_optional:
                reasons.append(f"{plan.artifact_type.value} lacks a metric plan")
            if not plan.baseline_or_comparator_plan:
                reasons.append(f"{plan.artifact_type.value} lacks a baseline or comparator")
        if plan.artifact_type in _SYMBOLIC_TYPES:
            checkable = True
            if not plan.requires_llm_drafting:
                reasons.append(f"{plan.artifact_type.value} must require LLM draft construction")
            if not plan.symbolic_obligations_optional:
                reasons.append(f"{plan.artifact_type.value} lacks unresolved symbolic obligations")
        if plan.artifact_type == EvidenceArtifactType.LITERATURE_NOVELTY_CHECK:
            checkable = True
            if not plan.requires_retrieval or not plan.retrieval_requirements_optional:
                reasons.append("literature novelty check lacks retrieval requirements")
        if plan.artifact_type == EvidenceArtifactType.PROOF_PLAN and (
            not plan.symbolic_obligations_optional and not plan.checker_requirements_optional
        ):
            reasons.append("proof plan lacks proof obligations")
        if not plan.success_criteria or not plan.failure_criteria:
            reasons.append(f"{plan.artifact_type.value} lacks success/failure criteria")
    if not checkable:
        reasons.append("package has no executable, retrieval, symbolic, or checkable path")

    combined = " ".join(
        [
            package.title,
            package.primary_claim_draft,
            package.allowed_claim_scope,
            package.package_rationale,
            package.recommended_next_action,
            *[
                " ".join(
                    [
                        plan.purpose,
                        plan.claim_component_supported,
                        *plan.success_criteria,
                        *plan.failure_criteria,
                    ]
                )
                for plan in package.artifact_plans
            ],
        ]
    ).lower()
    forbidden_assertions = {
        "publication_ready=true": "package asserts publication readiness",
        "publication ready": "package asserts publication readiness",
        "real-world validation": "package asserts real-world validation",
        "real world validation": "package asserts real-world validation",
        "verified theorem": "package claims theorem verification without checker evidence",
        "has been proven": "package claims proof without checker evidence",
        "is proven": "package claims proof without checker evidence",
        "novelty proven": "package asserts novelty as fact",
        "establishes novelty": "package asserts novelty as fact",
        "underuse proven": "package asserts underuse as fact",
    }
    reasons.extend(
        message for phrase, message in forbidden_assertions.items() if phrase in combined
    )
    return reasons


def _repair_forbidden_claim_boundaries(
    item: HybridEvidencePackageProposalItem,
) -> tuple[HybridEvidencePackageProposalItem, list[str]]:
    repairs: list[str] = []

    def merged(values: list[str], owner: str) -> list[str]:
        normalized = {value.strip().lower() for value in values}
        missing = [
            value for value in MANDATORY_PACKAGE_FORBIDDEN_CLAIMS if value not in normalized
        ]
        if missing:
            repairs.append(f"added mandatory forbidden claims to {owner}")
        return [*values, *missing]

    plans = [
        plan.model_copy(update={"forbidden_claims": merged(plan.forbidden_claims, "artifact")})
        for plan in item.package.artifact_plans
    ]
    package = item.package.model_copy(update={"artifact_plans": plans})
    return item.model_copy(update={"package": package}), repairs


def _json_object(raw: Any, *, operation: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError as exc:
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation=operation,
            message="hybrid evidence response was not valid JSON",
            cause=exc,
        ) from exc
    if not isinstance(payload, dict):
        raise AdapterResponseParseError(
            backend="openai",
            provider="openai",
            operation=operation,
            message="hybrid evidence response must be a JSON object",
        )
    return payload


__all__ = [
    "EvidenceArtifactPlanProposal",
    "HybridEvidenceClient",
    "HybridEvidenceDraftArtifact",
    "HybridEvidenceDraftResponse",
    "HybridEvidencePackageEnvelope",
    "HybridEvidencePackageProposal",
    "HybridEvidencePackageProposalItem",
    "HybridEvidencePackageResponse",
    "HybridEvidencePackageScoreProposal",
    "MANDATORY_PACKAGE_FORBIDDEN_CLAIMS",
    "OpenAIHybridEvidencePlanner",
    "PACKAGE_ALLOWED_LABELS",
    "build_hybrid_draft_prompt",
    "build_hybrid_package_prompt",
    "parse_hybrid_draft_response",
    "parse_hybrid_package_response",
    "validate_hybrid_package_proposal",
]
