from __future__ import annotations

import json
from copy import deepcopy
from typing import Any
from urllib.request import Request

from factori.adapters.deep_opportunity import OpportunityProposalEnvelope
from factori.adapters.llm_prompts import build_stage_a_candidate_prompt
from factori.adapters.llm_real import (
    OpenAIResponsesTransport,
    build_openai_request_diagnostics,
)
from factori.adapters.openai_schema import make_openai_strict_json_schema
from factori.adapters.prose_prompts import PROSE_OUTPUT_SCHEMA
from factori.adapters.reviewer_prompts import REVIEWER_OUTPUT_SCHEMA
from factori.hashing import sha256_json
from factori.schemas import ConstraintSet


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
