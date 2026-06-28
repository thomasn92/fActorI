"""OpenAI structured-output JSON Schema compatibility helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


def make_openai_strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return an OpenAI strict-compatible transport schema.

    OpenAI strict structured outputs require object schemas to list every property
    in ``required``. Optional fActorI fields are therefore represented as nullable
    required fields in this adapter-local copy.
    """
    return _transform_schema(deepcopy(schema), optional=False)


def _transform_schema(value: Any, *, optional: bool) -> Any:
    if isinstance(value, list):
        return [_transform_schema(item, optional=False) for item in value]
    if not isinstance(value, dict):
        return value

    transformed = dict(value)
    _transform_schema_map(transformed, "$defs")
    _transform_schema_map(transformed, "definitions")
    _transform_schema_list(transformed, "anyOf")
    _transform_schema_list(transformed, "oneOf")
    _transform_schema_list(transformed, "allOf")

    if "items" in transformed:
        transformed["items"] = _transform_schema(transformed["items"], optional=False)

    properties = transformed.get("properties")
    if isinstance(properties, dict):
        original_required = _required_set(transformed.get("required"))
        strict_properties: dict[str, Any] = {}
        for name, property_schema in properties.items():
            strict_property = _transform_schema(property_schema, optional=False)
            if name not in original_required:
                strict_property = _make_nullable(strict_property)
            strict_properties[name] = strict_property
        transformed["properties"] = strict_properties
        transformed["required"] = list(strict_properties)
        transformed["additionalProperties"] = False
    elif transformed.get("type") == "object":
        transformed["additionalProperties"] = False

    if optional:
        transformed = _make_nullable(transformed)
    return transformed


def _transform_schema_map(schema: dict[str, Any], key: str) -> None:
    nested = schema.get(key)
    if isinstance(nested, dict):
        schema[key] = {
            name: _transform_schema(item, optional=False)
            for name, item in nested.items()
        }


def _transform_schema_list(schema: dict[str, Any], key: str) -> None:
    nested = schema.get(key)
    if isinstance(nested, list):
        schema[key] = [_transform_schema(item, optional=False) for item in nested]


def _required_set(value: Any) -> set[str]:
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    return set()


def _make_nullable(schema: dict[str, Any]) -> dict[str, Any]:
    if _allows_null(schema):
        return schema
    nullable = dict(schema)
    enum_values = nullable.get("enum")
    if isinstance(enum_values, list) and None not in enum_values:
        nullable["enum"] = [*enum_values, None]
    schema_type = nullable.get("type")
    if isinstance(schema_type, str):
        nullable["type"] = [schema_type, "null"]
        return nullable
    if isinstance(schema_type, list):
        nullable["type"] = [*schema_type, "null"]
        return nullable
    branch = dict(nullable)
    wrapped: dict[str, Any] = {
        "anyOf": [
            branch,
            {"type": "null"},
        ]
    }
    for key in ("title", "description"):
        if key in nullable:
            wrapped[key] = nullable[key]
    return wrapped


def _allows_null(schema: dict[str, Any]) -> bool:
    schema_type = schema.get("type")
    if schema_type == "null":
        return True
    if isinstance(schema_type, list) and "null" in schema_type:
        return True
    enum_values = schema.get("enum")
    if isinstance(enum_values, list) and None in enum_values:
        return True
    for key in ("anyOf", "oneOf"):
        branches = schema.get(key)
        if isinstance(branches, list) and any(_allows_null_branch(item) for item in branches):
            return True
    return False


def _allows_null_branch(value: Any) -> bool:
    return isinstance(value, dict) and _allows_null(value)


__all__ = ["make_openai_strict_json_schema"]
