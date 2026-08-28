"""Read-only JSON Schema validation for exported fActorI protocol examples."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from factori.schema_export import DEFAULT_PROTOCOL_OUTPUT_DIR, EXAMPLE_PROTOCOLS
from factori.schemas import StrictModel

DEFAULT_PROTOCOL_EXAMPLES_DIR = Path("protocols/examples")


class ProtocolExampleValidationResult(StrictModel):
    """Validation result for one deterministic protocol example."""

    example_file: str = Field(min_length=1)
    schema_file: str = Field(min_length=1)
    valid: bool
    errors: list[str] = Field(default_factory=list)


class ProtocolExampleValidationReport(StrictModel):
    """Read-only report for JSON-Schema-level example validation."""

    schema_dir: str = Field(min_length=1)
    examples_dir: str = Field(min_length=1)
    examples_checked: int = Field(ge=0)
    examples_valid: int = Field(ge=0)
    examples_invalid: int = Field(ge=0)
    results: list[ProtocolExampleValidationResult]
    read_only: bool = True
    not_provenance: bool = True
    not_evidence: bool = True
    not_ledgered: bool = True


def validate_protocol_examples(
    schema_dir: Path = DEFAULT_PROTOCOL_OUTPUT_DIR,
    examples_dir: Path = DEFAULT_PROTOCOL_EXAMPLES_DIR,
) -> ProtocolExampleValidationReport:
    """Validate deterministic examples against checked-in JSON Schemas without writing files."""
    schema_dir = Path(schema_dir)
    examples_dir = Path(examples_dir)
    results: list[ProtocolExampleValidationResult] = []
    if not examples_dir.is_dir():
        return ProtocolExampleValidationReport(
            schema_dir=schema_dir.as_posix(),
            examples_dir=examples_dir.as_posix(),
            examples_checked=0,
            examples_valid=0,
            examples_invalid=1,
            results=[
                ProtocolExampleValidationResult(
                    example_file=examples_dir.as_posix(),
                    schema_file="",
                    valid=False,
                    errors=[f"Examples directory does not exist: {examples_dir}"],
                )
            ],
        )

    for example_path in sorted(examples_dir.glob("*.example.json")):
        protocol_name = EXAMPLE_PROTOCOLS.get(example_path.name)
        schema_file = _schema_filename(protocol_name, example_path.name)
        schema_path = schema_dir / schema_file
        errors: list[str] = []
        try:
            instance = json.loads(example_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            instance = None
            errors.append(f"Could not read example JSON: {exc}")
        try:
            schema = _load_schema(schema_path)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            schema = {}
            errors.append(f"Could not read schema JSON: {exc}")
        if not errors:
            errors.extend(_validate(instance, schema, schema, path="$"))
        results.append(
            ProtocolExampleValidationResult(
                example_file=example_path.as_posix(),
                schema_file=schema_path.as_posix(),
                valid=not errors,
                errors=errors,
            )
        )

    valid = sum(1 for result in results if result.valid)
    invalid = len(results) - valid
    return ProtocolExampleValidationReport(
        schema_dir=schema_dir.as_posix(),
        examples_dir=examples_dir.as_posix(),
        examples_checked=len(results),
        examples_valid=valid,
        examples_invalid=invalid,
        results=results,
    )


def _schema_filename(protocol_name: str | None, example_name: str) -> str:
    if protocol_name is None:
        return example_name.replace(".example.json", ".schema.json")
    first = re.sub(r"(.)([A-Z][a-z]+)", r"\1-\2", protocol_name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1-\2", first).lower() + ".schema.json"


def _load_schema(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("schema root must be a JSON object")
    return value


def _validate(
    instance: Any,
    schema: dict[str, Any],
    root: dict[str, Any],
    *,
    path: str,
) -> list[str]:
    errors: list[str] = []
    ref = schema.get("$ref")
    if isinstance(ref, str):
        target = _resolve_ref(root, ref)
        if target is None:
            return [f"{path}: unresolved reference {ref}"]
        return _validate(instance, target, root, path=path)

    any_of = schema.get("anyOf")
    if isinstance(any_of, list):
        branch_errors = [
            _validate(instance, branch, root, path=path)
            for branch in any_of
            if isinstance(branch, dict)
        ]
        if any(not branch for branch in branch_errors):
            return []
        return [f"{path}: value did not match any allowed schema"]

    one_of = schema.get("oneOf")
    if isinstance(one_of, list):
        branch_errors = [
            _validate(instance, branch, root, path=path)
            for branch in one_of
            if isinstance(branch, dict)
        ]
        if sum(not branch for branch in branch_errors) == 1:
            return []
        return [f"{path}: value did not match exactly one allowed schema"]

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}")
    enum = schema.get("enum")
    if isinstance(enum, list) and instance not in enum:
        errors.append(f"{path}: value {instance!r} is not in enum")

    expected_type = schema.get("type")
    if expected_type is not None:
        type_errors = _validate_type(instance, expected_type, path)
        if type_errors:
            return [*errors, *type_errors]

    if isinstance(instance, dict):
        errors.extend(_validate_object(instance, schema, root, path=path))
    elif isinstance(instance, list):
        errors.extend(_validate_array(instance, schema, root, path=path))
    elif isinstance(instance, str):
        errors.extend(_validate_string(instance, schema, path=path))
    elif isinstance(instance, int | float) and not isinstance(instance, bool):
        errors.extend(_validate_number(instance, schema, path=path))
    return errors


def _validate_object(
    instance: dict[str, Any],
    schema: dict[str, Any],
    root: dict[str, Any],
    *,
    path: str,
) -> list[str]:
    errors: list[str] = []
    properties = schema.get("properties")
    properties = properties if isinstance(properties, dict) else {}
    required = schema.get("required")
    if isinstance(required, list):
        for name in required:
            if isinstance(name, str) and name not in instance:
                errors.append(f"{path}: missing required property {name!r}")

    additional = schema.get("additionalProperties", True)
    for name, value in instance.items():
        child_path = f"{path}.{name}"
        child_schema = properties.get(name)
        if isinstance(child_schema, dict):
            errors.extend(_validate(value, child_schema, root, path=child_path))
        elif additional is False:
            errors.append(f"{child_path}: additional property is not allowed")
        elif isinstance(additional, dict):
            errors.extend(_validate(value, additional, root, path=child_path))
    return errors


def _validate_array(
    instance: list[Any],
    schema: dict[str, Any],
    root: dict[str, Any],
    *,
    path: str,
) -> list[str]:
    errors: list[str] = []
    minimum = schema.get("minItems")
    maximum = schema.get("maxItems")
    if isinstance(minimum, int) and len(instance) < minimum:
        errors.append(f"{path}: array has fewer than {minimum} items")
    if isinstance(maximum, int) and len(instance) > maximum:
        errors.append(f"{path}: array has more than {maximum} items")
    items = schema.get("items")
    if isinstance(items, dict):
        for index, value in enumerate(instance):
            errors.extend(_validate(value, items, root, path=f"{path}[{index}]"))
    return errors


def _validate_string(instance: str, schema: dict[str, Any], *, path: str) -> list[str]:
    errors: list[str] = []
    minimum = schema.get("minLength")
    maximum = schema.get("maxLength")
    pattern = schema.get("pattern")
    if isinstance(minimum, int) and len(instance) < minimum:
        errors.append(f"{path}: string is shorter than {minimum}")
    if isinstance(maximum, int) and len(instance) > maximum:
        errors.append(f"{path}: string is longer than {maximum}")
    if isinstance(pattern, str) and re.fullmatch(pattern, instance) is None:
        errors.append(f"{path}: string does not match required pattern")
    if schema.get("format") == "date-time" and not _is_rfc3339_datetime(instance):
        errors.append(f"{path}: string is not an RFC3339 date-time")
    return errors


def _validate_number(instance: int | float, schema: dict[str, Any], *, path: str) -> list[str]:
    errors: list[str] = []
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    if isinstance(minimum, int | float) and instance < minimum:
        errors.append(f"{path}: number is below minimum {minimum}")
    if isinstance(maximum, int | float) and instance > maximum:
        errors.append(f"{path}: number is above maximum {maximum}")
    return errors


def _validate_type(instance: Any, expected_type: Any, path: str) -> list[str]:
    types = expected_type if isinstance(expected_type, list) else [expected_type]
    if any(_matches_type(instance, item) for item in types if isinstance(item, str)):
        return []
    return [f"{path}: expected type {expected_type!r}"]


def _matches_type(instance: Any, expected_type: str) -> bool:
    if expected_type == "null":
        return instance is None
    if expected_type == "boolean":
        return isinstance(instance, bool)
    if expected_type == "string":
        return isinstance(instance, str)
    if expected_type == "integer":
        return isinstance(instance, int) and not isinstance(instance, bool)
    if expected_type == "number":
        return isinstance(instance, int | float) and not isinstance(instance, bool)
    if expected_type == "object":
        return isinstance(instance, dict)
    if expected_type == "array":
        return isinstance(instance, list)
    return True


def _resolve_ref(root: dict[str, Any], ref: str) -> dict[str, Any] | None:
    if not ref.startswith("#/"):
        return None
    current: Any = root
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, dict) else None


def _is_rfc3339_datetime(value: str) -> bool:
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return "T" in value


__all__ = [
    "DEFAULT_PROTOCOL_EXAMPLES_DIR",
    "ProtocolExampleValidationReport",
    "ProtocolExampleValidationResult",
    "validate_protocol_examples",
]
