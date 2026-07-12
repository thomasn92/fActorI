"""OpenAI structured-output JSON Schema compatibility helpers."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

# Keep provider-version-sensitive validation constraints in the local Pydantic
# contract, while omitting them from the transport copy. The response is still
# validated against the full adapter model after it returns.
_PORTABLE_SCHEMA_OMISSIONS = frozenset(
    {
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
    }
)


def make_openai_strict_json_schema(
    schema: dict[str, Any], *, nullable_optional_fields: bool = True
) -> dict[str, Any]:
    """Return a portable OpenAI strict-compatible transport schema.

    OpenAI strict structured outputs require object schemas to list every property
    in ``required``. Optional fActorI fields are therefore represented as nullable
    required fields in this adapter-local copy. Provider-sensitive validation
    constraints are omitted here and remain enforced by the full local model.
    """
    return _transform_schema(
        deepcopy(schema), optional=False, nullable_optional_fields=nullable_optional_fields
    )


def _transform_schema(
    value: Any, *, optional: bool, nullable_optional_fields: bool
) -> Any:
    if isinstance(value, list):
        return [
            _transform_schema(
                item, optional=False, nullable_optional_fields=nullable_optional_fields
            )
            for item in value
        ]
    if not isinstance(value, dict):
        return value

    transformed = dict(value)
    for key in _PORTABLE_SCHEMA_OMISSIONS:
        transformed.pop(key, None)
    _transform_schema_map(transformed, "$defs", nullable_optional_fields)
    _transform_schema_map(transformed, "definitions", nullable_optional_fields)
    _transform_schema_list(transformed, "anyOf", nullable_optional_fields)
    _transform_schema_list(transformed, "oneOf", nullable_optional_fields)
    _transform_schema_list(transformed, "allOf", nullable_optional_fields)

    if "items" in transformed:
        transformed["items"] = _transform_schema(
            transformed["items"],
            optional=False,
            nullable_optional_fields=nullable_optional_fields,
        )

    properties = transformed.get("properties")
    if isinstance(properties, dict):
        original_required = _required_set(transformed.get("required"))
        strict_properties: dict[str, Any] = {}
        for name, property_schema in properties.items():
            strict_property = _transform_schema(
                property_schema,
                optional=False,
                nullable_optional_fields=nullable_optional_fields,
            )
            if name not in original_required and nullable_optional_fields:
                strict_property = _make_nullable(strict_property)
            strict_properties[name] = strict_property
        transformed["properties"] = strict_properties
        transformed["required"] = list(strict_properties)
        transformed["additionalProperties"] = False
    elif transformed.get("type") == "object":
        transformed["additionalProperties"] = False

    if isinstance(transformed.get("type"), list) and "null" in transformed["type"]:
        transformed = _nullable_type_union(transformed)

    if optional:
        transformed = _make_nullable(transformed)
    return transformed


def _transform_schema_map(
    schema: dict[str, Any], key: str, nullable_optional_fields: bool
) -> None:
    nested = schema.get(key)
    if isinstance(nested, dict):
        schema[key] = {
            name: _transform_schema(
                item,
                optional=False,
                nullable_optional_fields=nullable_optional_fields,
            )
            for name, item in nested.items()
        }


def _transform_schema_list(
    schema: dict[str, Any], key: str, nullable_optional_fields: bool
) -> None:
    nested = schema.get(key)
    if isinstance(nested, list):
        schema[key] = [
            _transform_schema(
                item,
                optional=False,
                nullable_optional_fields=nullable_optional_fields,
            )
            for item in nested
        ]


def _required_set(value: Any) -> set[str]:
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    return set()


def _make_nullable(schema: dict[str, Any]) -> dict[str, Any]:
    nullable = dict(schema)
    schema_type = nullable.get("type")
    if isinstance(schema_type, list) and "null" in schema_type:
        return _nullable_type_union(nullable)
    if _allows_null(schema):
        return schema
    enum_values = nullable.get("enum")
    if isinstance(enum_values, list) and None not in enum_values:
        nullable["enum"] = [*enum_values, None]
    # Responses Structured Outputs accepts nullable unions through anyOf. A
    # JSON-Schema type array (for example ["array", "null"]) is valid JSON
    # Schema in general, but is not part of the provider's strict subset.
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


def _nullable_type_union(schema: dict[str, Any]) -> dict[str, Any]:
    schema_type = schema.get("type")
    if not isinstance(schema_type, list):
        return schema
    base = dict(schema)
    base.pop("type", None)
    branches = [
        {**base, "type": item}
        for item in schema_type
        if isinstance(item, str) and item != "null"
    ]
    branches.append({"type": "null"})
    return {"anyOf": branches}


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
