from __future__ import annotations

import json
from copy import deepcopy
from typing import Any
from urllib.request import Request

import pytest

from factori.adapters.adaptive_questioner import (
    AdaptiveQuestionerEnvelope,
    OpenAIAdaptiveQuestioner,
)
from factori.adapters.deep_opportunity import OpportunityProposalEnvelope
from factori.adapters.hybrid_evidence import (
    HybridEvidencePackageEnvelope,
    OpenAIHybridEvidencePlanner,
)
from factori.adapters.llm_experiment_codegen import ExperimentCodePatchEnvelope
from factori.adapters.llm_prompts import build_stage_a_candidate_prompt
from factori.adapters.llm_real import (
    OpenAIResponsesTransport,
    build_openai_request_diagnostics,
)
from factori.adapters.llm_route_planning import (
    OpenAILLMRoutePlanner,
    build_llm_route_planning_prompt,
)
from factori.adapters.nucleus_manuscript import (
    ManuscriptCriticEnvelope,
    ManuscriptCriticProposal,
    ManuscriptDraftEnvelope,
    ManuscriptPlanTransportEnvelope,
    ManuscriptRevisionEnvelope,
    OpenAINucleusManuscript,
    _boundary_reasons,
    _critic_prompt,
    _parse_one,
    _revision_prompt,
)
from factori.adapters.openai_schema import make_openai_strict_json_schema
from factori.adapters.prose_prompts import PROSE_OUTPUT_SCHEMA
from factori.adapters.reviewer_prompts import REVIEWER_OUTPUT_SCHEMA
from factori.adapters.scientific_critic import (
    CriticReviewEnvelope,
    CrossPackageAdjudicationEnvelope,
    OpenAIScientificCritic,
)
from factori.hashing import sha256_json
from factori.schemas import ConstraintSet, ManuscriptCriticRole


class CapturingResponse:
    def __enter__(self) -> CapturingResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return b'{"output_text":"{}"}'


def test_helper_does_not_mutate_input_schema() -> None:
    schema = _basic_schema()
    original = deepcopy(schema)

    make_openai_strict_json_schema(schema)

    assert schema == original


def test_object_properties_become_required_and_closed() -> None:
    strict = make_openai_strict_json_schema(_basic_schema())

    assert strict["required"] == ["name", "age"]
    assert strict["additionalProperties"] is False
    assert_openai_strict_schema(strict)


def test_optional_string_property_becomes_nullable_and_required() -> None:
    strict = make_openai_strict_json_schema(_basic_schema())

    assert "age" in strict["required"]
    assert strict["properties"]["age"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]


def test_originally_required_property_remains_required_without_nullable_change() -> None:
    strict = make_openai_strict_json_schema(_basic_schema())

    assert "name" in strict["required"]
    assert strict["properties"]["name"]["type"] == "string"


def test_nested_object_properties_are_strict() -> None:
    strict = make_openai_strict_json_schema(
        {
            "type": "object",
            "properties": {
                "outer": {
                    "type": "object",
                    "properties": {"inner": {"type": "string"}},
                    "required": [],
                }
            },
            "required": ["outer"],
        }
    )

    inner = strict["properties"]["outer"]
    assert inner["required"] == ["inner"]
    assert inner["additionalProperties"] is False
    assert inner["properties"]["inner"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert_openai_strict_schema(strict)


def test_array_item_object_properties_are_strict() -> None:
    strict = make_openai_strict_json_schema(
        {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {"label": {"type": "string"}},
                    },
                }
            },
        }
    )

    item_schema = strict["properties"]["items"]["anyOf"][0]["items"]
    assert item_schema["required"] == ["label"]
    assert item_schema["additionalProperties"] is False
    assert_openai_strict_schema(strict)


def test_defs_and_definitions_are_processed() -> None:
    strict = make_openai_strict_json_schema(
        {
            "type": "object",
            "properties": {"top": {"type": "string"}},
            "$defs": {
                "Nested": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                }
            },
            "definitions": {
                "Legacy": {
                    "type": "object",
                    "properties": {"code": {"type": "string"}},
                }
            },
        }
    )

    assert strict["$defs"]["Nested"]["required"] == ["name"]
    assert strict["definitions"]["Legacy"]["required"] == ["code"]
    assert_openai_strict_schema(strict)


def test_composition_branches_are_processed() -> None:
    strict = make_openai_strict_json_schema(
        {
            "anyOf": [{"type": "object", "properties": {"a": {"type": "string"}}}],
            "oneOf": [{"type": "object", "properties": {"b": {"type": "string"}}}],
            "allOf": [{"type": "object", "properties": {"c": {"type": "string"}}}],
        }
    )

    assert strict["anyOf"][0]["required"] == ["a"]
    assert strict["oneOf"][0]["required"] == ["b"]
    assert strict["allOf"][0]["required"] == ["c"]
    assert_openai_strict_schema(strict)


def test_enum_constraints_and_descriptions_are_preserved() -> None:
    strict = make_openai_strict_json_schema(
        {
            "type": "object",
            "title": "Example",
            "description": "Example schema",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": ["A", "B"],
                    "description": "Kind value",
                },
                "required_kind": {"type": "string", "enum": ["C"]},
            },
            "required": ["required_kind"],
        }
    )

    assert strict["title"] == "Example"
    assert strict["description"] == "Example schema"
    assert strict["properties"]["kind"]["description"] == "Kind value"
    kind_schema = strict["properties"]["kind"]["anyOf"][0]
    assert kind_schema["enum"] == ["A", "B", None]
    assert strict["properties"]["required_kind"]["enum"] == ["C"]


def test_stage_a_candidate_response_schema_is_openai_strict() -> None:
    contract = build_stage_a_candidate_prompt(
        "human geography",
        None,
        ConstraintSet(domain="human geography"),
        3,
    )
    strict = make_openai_strict_json_schema(contract.requested_output_schema)

    item_schema = strict["properties"]["candidates"]["items"]
    assert "question" in item_schema["properties"]
    assert "question" in item_schema["required"]
    assert item_schema["properties"]["question"]["anyOf"] == [
        {"type": "string"},
        {"type": "null"},
    ]
    assert_openai_strict_schema(strict)


def test_reviewer_response_schema_is_openai_strict() -> None:
    strict = make_openai_strict_json_schema(REVIEWER_OUTPUT_SCHEMA)

    assert_openai_strict_schema(strict)


def test_prose_response_schema_is_openai_strict() -> None:
    strict = make_openai_strict_json_schema(PROSE_OUTPUT_SCHEMA)

    assert_openai_strict_schema(strict)


def test_experiment_code_patch_schema_is_openai_strict() -> None:
    strict = make_openai_strict_json_schema(
        ExperimentCodePatchEnvelope.model_json_schema()
    )

    assert_openai_strict_schema(strict)


def test_openai_transport_payload_uses_strict_schema_without_network() -> None:
    observed_payloads: list[dict[str, Any]] = []

    def opener(request: Request, timeout: float) -> CapturingResponse:
        del timeout
        assert request.data is not None
        observed_payloads.append(json.loads(request.data.decode("utf-8")))
        return CapturingResponse()

    contract = build_stage_a_candidate_prompt(
        "human geography",
        None,
        ConstraintSet(domain="human geography"),
        1,
    )
    transport = OpenAIResponsesTransport(opener=opener)

    transport.create_response(
        api_key="test-key",
        model="test-model",
        prompt=contract.prompt_text,
        response_schema=contract.requested_output_schema,
    )

    schema = observed_payloads[0]["text"]["format"]["schema"]
    item_schema = schema["properties"]["candidates"]["items"]
    assert "question" in item_schema["required"]
    assert_openai_strict_schema(schema)
    assert "reasoning" not in observed_payloads[0]


def test_openai_transport_payload_includes_explicit_reasoning_effort() -> None:
    observed_payloads: list[dict[str, Any]] = []

    def opener(request: Request, timeout: float) -> CapturingResponse:
        del timeout
        assert request.data is not None
        observed_payloads.append(json.loads(request.data.decode("utf-8")))
        return CapturingResponse()

    transport = OpenAIResponsesTransport(opener=opener, reasoning_effort="high")
    transport.create_response(
        api_key="test-key",
        model="test-model",
        prompt="reason carefully",
        response_schema=_basic_schema(),
    )

    assert observed_payloads[0]["reasoning"] == {"effort": "high"}
    diagnostics = build_openai_request_diagnostics(
        model="test-model",
        prompt="reason carefully",
        response_schema=_basic_schema(),
        reasoning_effort="high",
    )
    assert diagnostics["reasoning_effort"] == "high"
    assert diagnostics["request_payload_hash"] == sha256_json(observed_payloads[0])


def test_openai_transport_payload_includes_explicit_output_token_limit() -> None:
    observed_payloads: list[dict[str, Any]] = []

    def opener(request: Request, timeout: float) -> CapturingResponse:
        del timeout
        assert request.data is not None
        observed_payloads.append(json.loads(request.data.decode("utf-8")))
        return CapturingResponse()

    transport = OpenAIResponsesTransport(opener=opener, max_output_tokens=12_000)
    transport.create_response(
        api_key="test-key",
        model="test-model",
        prompt="bounded response",
        response_schema=_basic_schema(),
    )

    assert observed_payloads[0]["max_output_tokens"] == 12_000


def test_openai_transport_rejects_unknown_reasoning_effort() -> None:
    with pytest.raises(ValueError, match="reasoning_effort"):
        OpenAIResponsesTransport(reasoning_effort="extreme")


def test_deep_opportunity_transport_uses_portable_schema_and_name() -> None:
    observed_payloads: list[dict[str, Any]] = []

    def opener(request: Request, timeout: float) -> CapturingResponse:
        del timeout
        assert request.data is not None
        observed_payloads.append(json.loads(request.data.decode("utf-8")))
        return CapturingResponse()

    transport = OpenAIResponsesTransport(
        opener=opener,
        schema_name="factori_deep_opportunities",
        nullable_optional_fields=False,
    )
    transport.create_response(
        api_key="test-key",
        model="test-model",
        prompt="deep opportunity prompt",
        response_schema=OpportunityProposalEnvelope.model_json_schema(),
    )

    format_payload = observed_payloads[0]["text"]["format"]
    schema = format_payload["schema"]
    assert format_payload["name"] == "factori_deep_opportunities"
    assert_openai_strict_schema(schema)
    opportunity_schema = schema["$defs"]["OpportunityProposal"]
    for field_name in (
        "retrieval_contradictions",
        "false_bridge_risks",
        "tautology_risks",
    ):
        field_schema = opportunity_schema["properties"][field_name]
        assert field_schema["type"] == "array"
        assert "anyOf" not in field_schema
    assert_no_portable_schema_omissions(schema)


def test_deep_request_diagnostics_match_transport_schema_mode() -> None:
    schema = OpportunityProposalEnvelope.model_json_schema()
    diagnostics = build_openai_request_diagnostics(
        model="test-model",
        prompt="deep opportunity prompt",
        response_schema=schema,
        schema_name="factori_deep_opportunities",
        nullable_optional_fields=False,
    )
    payload = {
        "model": "test-model",
        "input": "deep opportunity prompt",
        "text": {
            "format": {
                "type": "json_schema",
                "name": "factori_deep_opportunities",
                "strict": True,
                "schema": make_openai_strict_json_schema(
                    schema, nullable_optional_fields=False
                ),
            }
        },
    }
    assert diagnostics["request_payload_hash"] == sha256_json(payload)


def test_route_planning_transport_schema_closes_parameter_vocabulary() -> None:
    contract = build_llm_route_planning_prompt(
        prompt_id="route-prompt",
        backend_name="llm-openai",
        model="test-model",
        substrate_payload={"substrate_id": "substrate-1"},
        source_metadata_payload={},
        retrieval_context_payload={},
    )
    strict = make_openai_strict_json_schema(contract.requested_output_schema)
    parameters = strict["$defs"]["RouteParameterValues"]

    assert parameters["additionalProperties"] is False
    assert set(parameters["required"]) == set(parameters["properties"])
    assert "sample_size" in parameters["properties"]
    assert "seed" in parameters["properties"]

    planner = OpenAILLMRoutePlanner(
        api_key="test-key",
        model="test-model",
        allow_external_calls=True,
    )
    assert planner.transport.schema_name == "factori_llm_routes"


def test_hybrid_evidence_transport_schema_has_no_open_mapping_fields() -> None:
    strict = make_openai_strict_json_schema(
        HybridEvidencePackageEnvelope.model_json_schema()
    )

    assert_openai_strict_schema(strict)
    for definition_name in (
        "EvidenceArtifactInputContractProposal",
        "EvidenceArtifactOutputContractProposal",
        "ArtifactDependencyProposal",
        "ClaimSupportProposal",
    ):
        definition = strict["$defs"][definition_name]
        assert definition["additionalProperties"] is False
        assert definition["properties"]
        assert set(definition["required"]) == set(definition["properties"])

    planner = OpenAIHybridEvidencePlanner(
        api_key="test-key",
        model="test-model",
        allow_external_calls=True,
    )
    assert planner.transport.schema_name == "factori_hybrid_evidence"


def test_adaptive_questioner_transport_schema_is_openai_strict() -> None:
    strict = make_openai_strict_json_schema(
        AdaptiveQuestionerEnvelope.model_json_schema()
    )

    assert_openai_strict_schema(strict)
    questioner = OpenAIAdaptiveQuestioner(
        api_key="test-key",
        model="test-model",
        allow_external_calls=True,
    )
    assert questioner.transport.schema_name == "factori_adaptive_questioner"
    assert questioner.transport.nullable_optional_fields is False
    assert questioner.transport.max_output_tokens == 24_000


def test_scientific_critic_transport_schemas_are_openai_strict() -> None:
    for envelope in (CriticReviewEnvelope, CrossPackageAdjudicationEnvelope):
        strict = make_openai_strict_json_schema(envelope.model_json_schema())
        assert_openai_strict_schema(strict)

    critic = OpenAIScientificCritic(
        api_key="test-key",
        model="test-model",
        allow_external_calls=True,
    )
    assert critic.transport.schema_name == "factori_scientific_critic"
    assert critic.transport.nullable_optional_fields is False


def test_nucleus_manuscript_transport_schemas_are_openai_strict() -> None:
    for envelope in (
        ManuscriptPlanTransportEnvelope,
        ManuscriptDraftEnvelope,
        ManuscriptCriticEnvelope,
        ManuscriptRevisionEnvelope,
    ):
        strict = make_openai_strict_json_schema(envelope.model_json_schema())
        assert_openai_strict_schema(strict)

    manuscript = OpenAINucleusManuscript(
        api_key="test-key",
        model="test-model",
        allow_external_calls=True,
    )
    assert manuscript.transport.schema_name == "factori_nucleus_manuscript"
    assert manuscript.transport.nullable_optional_fields is False


def test_nucleus_manuscript_normalizes_transport_role_entries() -> None:
    class StaticTransport:
        def create_response(self, **_kwargs: Any) -> str:
            return json.dumps(
                {
                    "plans": [
                        {
                            "working_title": "Bounded synthetic study",
                            "paper_type": "synthetic_benchmark",
                            "central_question": "What happens in the bounded benchmark?",
                            "central_claim": (
                                "The bounded benchmark reports a conditional result and does not "
                                "establish real-world validation."
                            ),
                            "section_plans": [
                                {
                                    "section_id": "results",
                                    "title": "Results",
                                    "purpose": "Report bounded results.",
                                    "claim_ids": [],
                                    "artifact_ids": [],
                                    "supporting_package_ids": [],
                                    "required_citations": [],
                                    "scope_constraints": ["Synthetic evidence only."],
                                    "bullets": [],
                                }
                            ],
                            "supporting_package_roles": [
                                {"package_id": "package-2", "role": "robustness"}
                            ],
                            "appendix_package_roles": [],
                            "negative_result_roles": [],
                        }
                    ]
                }
            )

    manuscript = OpenAINucleusManuscript(
        api_key="test-key",
        model="test-model",
        transport=StaticTransport(),
        allow_external_calls=True,
    )

    response = manuscript.plan_manuscript(
        prompt_id="prompt-1",
        nucleus_payload={"primary_package_id": "package-1"},
        evidence_payload={},
    )

    assert response.accepted is not None
    assert response.accepted.supporting_package_roles == {"package-2": "robustness"}
    assert response.rejection_reasons == []
    unsafe = response.accepted.model_copy(
        update={"central_claim": "This establishes real-world validation."}
    )
    assert _boundary_reasons(unsafe) == [
        "manuscript output asserts real-world validation"
    ]
    bounded = response.accepted.model_copy(
        update={
            "central_claim": (
                "This bounded plan does not claim publication readiness and must not be "
                "described as publication ready."
            )
        }
    )
    assert _boundary_reasons(bounded) == []
    publication = response.accepted.model_copy(
        update={"central_claim": "This manuscript is publication ready."}
    )
    assert _boundary_reasons(publication) == [
        "manuscript output asserts publication readiness"
    ]
    role_schema = response.requested_output_schema["$defs"][
        "ManuscriptPlanTransportProposal"
    ]["properties"]["supporting_package_roles"]
    assert role_schema["type"] == "array"


def test_nucleus_manuscript_normalizes_five_point_critic_score() -> None:
    payload = {
        "reviews": [
            {
                "findings": ["The bounded claim needs a clearer qualifier."],
                "blocking_findings": [],
                "recommended_revisions": ["Add the qualifier."],
                "score": 3.5,
                "publication_ready": False,
                "creates_scientific_validation": False,
            }
        ]
    }

    accepted, reasons = _parse_one(
        payload,
        envelope_key="reviews",
        model_type=ManuscriptCriticProposal,
    )

    assert isinstance(accepted, ManuscriptCriticProposal)
    assert accepted.score == pytest.approx(0.7)
    assert reasons == []


def test_nucleus_manuscript_prompts_distinguish_limitations_from_blockers() -> None:
    critic = _critic_prompt(
        ManuscriptCriticRole.CLAIM_EVIDENCE_ALIGNMENT,
        {"draft_id": "draft-1"},
        {"evidence_citation_bindings": []},
    )
    revision = _revision_prompt(
        {"draft_id": "draft-1"},
        [{"blocking_findings": ["Remove an unsupported comparison."]}],
        {"evidence_citation_bindings": []},
    )

    assert "unresolved limitation, not by itself a blocking manuscript defect" in critic
    assert "complete persisted execution artifact" in critic
    assert "remove it from the claim-bearing manuscript" in revision
    assert "artifact-summary report" in revision


def assert_no_portable_schema_omissions(schema: Any) -> None:
    if isinstance(schema, list):
        for item in schema:
            assert_no_portable_schema_omissions(item)
        return
    if not isinstance(schema, dict):
        return
    for key in {
        "contentEncoding",
        "contentMediaType",
        "exclusiveMaximum",
        "exclusiveMinimum",
        "format",
        "maxItems",
        "maxLength",
        "maxProperties",
        "minItems",
        "minLength",
        "minProperties",
        "maximum",
        "minimum",
        "multipleOf",
        "pattern",
        "patternProperties",
        "uniqueItems",
    }:
        assert key not in schema
    for value in schema.values():
        assert_no_portable_schema_omissions(value)


def assert_openai_strict_schema(schema: Any) -> None:
    if isinstance(schema, list):
        for item in schema:
            assert_openai_strict_schema(item)
        return
    if not isinstance(schema, dict):
        return
    properties = schema.get("properties")
    if isinstance(properties, dict):
        assert set(schema.get("required", [])) == set(properties)
        assert schema.get("additionalProperties") is False
        for property_schema in properties.values():
            assert_openai_strict_schema(property_schema)
    if "items" in schema:
        assert_openai_strict_schema(schema["items"])
    for key in ("$defs", "definitions"):
        nested = schema.get(key)
        if isinstance(nested, dict):
            for item in nested.values():
                assert_openai_strict_schema(item)
    for key in ("anyOf", "oneOf", "allOf"):
        branches = schema.get(key)
        if isinstance(branches, list):
            for branch in branches:
                assert_openai_strict_schema(branch)


def _basic_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "age": {"type": "string"},
        },
        "required": ["name"],
    }
