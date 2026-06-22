"""Conservative deterministic JSON Schema change classification."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field

from factori.schemas import StrictModel


class SchemaChangeClassification(StrEnum):
    """Compatibility class for one structural schema difference."""

    BREAKING = "Breaking"
    NON_BREAKING = "NonBreaking"
    DOCUMENTATION_ONLY = "DocumentationOnly"
    UNKNOWN = "Unknown"


class SchemaChange(StrictModel):
    """One deterministic JSON Schema difference."""

    schema_name: str = Field(min_length=1)
    path: str = Field(min_length=1)
    classification: SchemaChangeClassification
    message: str = Field(min_length=1)
    old_value: Any = None
    new_value: Any = None


class SchemaDiffResult(StrictModel):
    """All classified changes for one matching schema file."""

    schema_name: str = Field(min_length=1)
    changes: list[SchemaChange] = Field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changes)


_DOCUMENTATION_KEYS = {
    "$comment",
    "$id",
    "description",
    "example",
    "examples",
    "title",
    "x-factori-protocol-version",
    "x-factori-source-model",
}
_COMPOSITION_KEYS = {"allOf", "anyOf", "oneOf", "not"}
_MINIMUM_KEYS = {"exclusiveMinimum", "minItems", "minLength", "minimum", "minProperties"}
_MAXIMUM_KEYS = {"exclusiveMaximum", "maxItems", "maxLength", "maximum", "maxProperties"}
_RESTRICTIVE_KEYS = {"multipleOf", "pattern"}


def compare_json_schemas(
    old_schema: dict[str, Any],
    new_schema: dict[str, Any],
    *,
    schema_name: str,
) -> SchemaDiffResult:
    """Compare two schemas conservatively without claiming full semantic analysis."""
    changes: list[SchemaChange] = []
    _compare_node(
        old_schema,
        new_schema,
        path="$",
        schema_name=schema_name,
        old_root=old_schema,
        new_root=new_schema,
        changes=changes,
        resolved_refs=set(),
    )
    return SchemaDiffResult(
        schema_name=schema_name,
        changes=sorted(
            changes,
            key=lambda item: (item.path, item.classification.value, item.message),
        ),
    )


def _compare_node(
    old: Any,
    new: Any,
    *,
    path: str,
    schema_name: str,
    old_root: dict[str, Any],
    new_root: dict[str, Any],
    changes: list[SchemaChange],
    resolved_refs: set[tuple[str, str]],
) -> None:
    if old == new:
        return
    if not isinstance(old, dict) or not isinstance(new, dict):
        _add(
            changes,
            schema_name,
            path,
            SchemaChangeClassification.UNKNOWN,
            "Complex schema value changed and cannot be classified safely.",
            old,
            new,
        )
        return

    handled: set[str] = set()
    for key in sorted(_DOCUMENTATION_KEYS):
        handled.add(key)
        if old.get(key) != new.get(key):
            _add(
                changes,
                schema_name,
                _path(path, key),
                SchemaChangeClassification.DOCUMENTATION_ONLY,
                "Documentation or protocol metadata changed.",
                old.get(key),
                new.get(key),
            )

    for key in sorted(_COMPOSITION_KEYS):
        handled.add(key)
        if old.get(key) != new.get(key):
            _add(
                changes,
                schema_name,
                _path(path, key),
                SchemaChangeClassification.UNKNOWN,
                "Schema composition changed and is not classified automatically.",
                old.get(key),
                new.get(key),
            )

    handled.add("$ref")
    _compare_ref(
        old.get("$ref"),
        new.get("$ref"),
        path=path,
        schema_name=schema_name,
        old_root=old_root,
        new_root=new_root,
        changes=changes,
        resolved_refs=resolved_refs,
    )

    handled.add("type")
    _compare_type(old.get("type"), new.get("type"), path, schema_name, changes)

    handled.add("enum")
    _compare_enum(old.get("enum"), new.get("enum"), path, schema_name, changes)

    handled.add("const")
    _compare_const(old.get("const"), new.get("const"), path, schema_name, changes)

    for key in sorted(_MINIMUM_KEYS | _MAXIMUM_KEYS):
        handled.add(key)
        _compare_numeric_constraint(
            key,
            old.get(key),
            new.get(key),
            path,
            schema_name,
            changes,
        )

    for key in sorted(_RESTRICTIVE_KEYS):
        handled.add(key)
        _compare_restrictive_constraint(
            key,
            old.get(key),
            new.get(key),
            path,
            schema_name,
            changes,
        )

    handled.update({"properties", "required"})
    _compare_properties(
        old,
        new,
        path=path,
        schema_name=schema_name,
        old_root=old_root,
        new_root=new_root,
        changes=changes,
        resolved_refs=resolved_refs,
    )

    handled.update({"$defs", "definitions"})
    for defs_key in ("$defs", "definitions"):
        _compare_definitions(
            old.get(defs_key),
            new.get(defs_key),
            path=_path(path, defs_key),
            schema_name=schema_name,
            old_root=old_root,
            new_root=new_root,
            changes=changes,
            resolved_refs=resolved_refs,
        )

    handled.add("items")
    if old.get("items") != new.get("items"):
        _compare_node(
            old.get("items"),
            new.get("items"),
            path=_path(path, "items"),
            schema_name=schema_name,
            old_root=old_root,
            new_root=new_root,
            changes=changes,
            resolved_refs=resolved_refs,
        )

    handled.add("additionalProperties")
    _compare_additional_properties(
        old.get("additionalProperties"),
        new.get("additionalProperties"),
        path,
        schema_name,
        changes,
    )

    handled.add("default")
    if old.get("default") != new.get("default"):
        _add(
            changes,
            schema_name,
            _path(path, "default"),
            SchemaChangeClassification.UNKNOWN,
            "Default value changed; consumer behavior may depend on it.",
            old.get("default"),
            new.get("default"),
        )

    for key in sorted(set(old) | set(new)):
        if key in handled or old.get(key) == new.get(key):
            continue
        _add(
            changes,
            schema_name,
            _path(path, key),
            SchemaChangeClassification.UNKNOWN,
            "Schema keyword changed and has no safe compatibility rule.",
            old.get(key),
            new.get(key),
        )


def _compare_properties(
    old: dict[str, Any],
    new: dict[str, Any],
    *,
    path: str,
    schema_name: str,
    old_root: dict[str, Any],
    new_root: dict[str, Any],
    changes: list[SchemaChange],
    resolved_refs: set[tuple[str, str]],
) -> None:
    old_properties = old.get("properties") or {}
    new_properties = new.get("properties") or {}
    if not isinstance(old_properties, dict) or not isinstance(new_properties, dict):
        if old_properties != new_properties:
            _add(
                changes,
                schema_name,
                _path(path, "properties"),
                SchemaChangeClassification.UNKNOWN,
                "Properties changed in a non-object form.",
                old_properties,
                new_properties,
            )
        return
    old_required = _string_set(old.get("required"))
    new_required = _string_set(new.get("required"))
    removed = set(old_properties) - set(new_properties)
    added = set(new_properties) - set(old_properties)
    common = set(old_properties) & set(new_properties)

    for name in sorted(removed):
        qualifier = "required" if name in old_required else "optional"
        _add(
            changes,
            schema_name,
            _path(path, f"properties.{name}"),
            SchemaChangeClassification.BREAKING,
            f"Removed {qualifier} property '{name}'.",
            old_properties[name],
            None,
        )
    for name in sorted(added):
        classification = (
            SchemaChangeClassification.BREAKING
            if name in new_required
            else SchemaChangeClassification.NON_BREAKING
        )
        message = (
            f"Added required property '{name}'."
            if name in new_required
            else f"Added optional property '{name}'."
        )
        _add(
            changes,
            schema_name,
            _path(path, f"properties.{name}"),
            classification,
            message,
            None,
            new_properties[name],
        )
    for name in sorted(common):
        _compare_node(
            old_properties[name],
            new_properties[name],
            path=_path(path, f"properties.{name}"),
            schema_name=schema_name,
            old_root=old_root,
            new_root=new_root,
            changes=changes,
            resolved_refs=resolved_refs,
        )
    for name in sorted((new_required - old_required) & common):
        _add(
            changes,
            schema_name,
            _path(path, f"required.{name}"),
            SchemaChangeClassification.BREAKING,
            f"Existing property '{name}' became required.",
            False,
            True,
        )
    for name in sorted((old_required - new_required) & common):
        _add(
            changes,
            schema_name,
            _path(path, f"required.{name}"),
            SchemaChangeClassification.NON_BREAKING,
            f"Existing property '{name}' became optional.",
            True,
            False,
        )


def _compare_definitions(
    old_value: Any,
    new_value: Any,
    *,
    path: str,
    schema_name: str,
    old_root: dict[str, Any],
    new_root: dict[str, Any],
    changes: list[SchemaChange],
    resolved_refs: set[tuple[str, str]],
) -> None:
    old_defs = old_value or {}
    new_defs = new_value or {}
    if not isinstance(old_defs, dict) or not isinstance(new_defs, dict):
        if old_defs != new_defs:
            _add(
                changes,
                schema_name,
                path,
                SchemaChangeClassification.UNKNOWN,
                "Schema definitions changed in a form that cannot be compared.",
                old_defs,
                new_defs,
            )
        return
    for name in sorted(set(old_defs) - set(new_defs)):
        _add(
            changes,
            schema_name,
            _path(path, name),
            SchemaChangeClassification.UNKNOWN,
            f"Definition '{name}' was removed; reference impact is uncertain.",
            old_defs[name],
            None,
        )
    for name in sorted(set(new_defs) - set(old_defs)):
        _add(
            changes,
            schema_name,
            _path(path, name),
            SchemaChangeClassification.NON_BREAKING,
            f"Definition '{name}' was added.",
            None,
            new_defs[name],
        )
    for name in sorted(set(old_defs) & set(new_defs)):
        _compare_node(
            old_defs[name],
            new_defs[name],
            path=_path(path, name),
            schema_name=schema_name,
            old_root=old_root,
            new_root=new_root,
            changes=changes,
            resolved_refs=resolved_refs,
        )


def _compare_ref(
    old_ref: Any,
    new_ref: Any,
    *,
    path: str,
    schema_name: str,
    old_root: dict[str, Any],
    new_root: dict[str, Any],
    changes: list[SchemaChange],
    resolved_refs: set[tuple[str, str]],
) -> None:
    if old_ref == new_ref:
        return
    if not isinstance(old_ref, str) or not isinstance(new_ref, str):
        _add(
            changes,
            schema_name,
            _path(path, "$ref"),
            SchemaChangeClassification.UNKNOWN,
            "Reference was added or removed and cannot be classified safely.",
            old_ref,
            new_ref,
        )
        return
    pair = (old_ref, new_ref)
    old_target = _resolve_local_ref(old_root, old_ref)
    new_target = _resolve_local_ref(new_root, new_ref)
    if old_target is None or new_target is None or pair in resolved_refs:
        _add(
            changes,
            schema_name,
            _path(path, "$ref"),
            SchemaChangeClassification.UNKNOWN,
            "Reference changed and could not be resolved safely.",
            old_ref,
            new_ref,
        )
        return
    before = len(changes)
    _compare_node(
        old_target,
        new_target,
        path=_path(path, "$ref"),
        schema_name=schema_name,
        old_root=old_root,
        new_root=new_root,
        changes=changes,
        resolved_refs={*resolved_refs, pair},
    )
    if len(changes) == before:
        _add(
            changes,
            schema_name,
            _path(path, "$ref"),
            SchemaChangeClassification.NON_BREAKING,
            "Reference changed to an equivalent resolved schema.",
            old_ref,
            new_ref,
        )


def _compare_type(
    old_type: Any,
    new_type: Any,
    path: str,
    schema_name: str,
    changes: list[SchemaChange],
) -> None:
    if old_type == new_type:
        return
    old_types = _type_set(old_type)
    new_types = _type_set(new_type)
    target = _path(path, "type")
    if not old_types and new_types:
        classification = SchemaChangeClassification.BREAKING
        message = "A type constraint was added."
    elif old_types and not new_types:
        classification = SchemaChangeClassification.NON_BREAKING
        message = "A type constraint was removed."
    elif old_types == {"integer"} and new_types == {"number"}:
        classification = SchemaChangeClassification.NON_BREAKING
        message = "Type widened from integer to number."
    elif old_types == {"number"} and new_types == {"integer"}:
        classification = SchemaChangeClassification.BREAKING
        message = "Type narrowed from number to integer."
    elif old_types <= new_types:
        classification = SchemaChangeClassification.NON_BREAKING
        message = "Accepted JSON types were widened."
    else:
        classification = SchemaChangeClassification.BREAKING
        message = "Accepted JSON types changed incompatibly or were narrowed."
    _add(changes, schema_name, target, classification, message, old_type, new_type)


def _compare_enum(
    old_enum: Any,
    new_enum: Any,
    path: str,
    schema_name: str,
    changes: list[SchemaChange],
) -> None:
    if old_enum == new_enum:
        return
    target = _path(path, "enum")
    if old_enum is None and isinstance(new_enum, list):
        _add(
            changes,
            schema_name,
            target,
            SchemaChangeClassification.BREAKING,
            "An enum restriction was added.",
            old_enum,
            new_enum,
        )
        return
    if isinstance(old_enum, list) and new_enum is None:
        _add(
            changes,
            schema_name,
            target,
            SchemaChangeClassification.NON_BREAKING,
            "An enum restriction was removed.",
            old_enum,
            new_enum,
        )
        return
    if not isinstance(old_enum, list) or not isinstance(new_enum, list):
        _add(
            changes,
            schema_name,
            target,
            SchemaChangeClassification.UNKNOWN,
            "Enum changed in a non-list form.",
            old_enum,
            new_enum,
        )
        return
    removed = [value for value in old_enum if value not in new_enum]
    added = [value for value in new_enum if value not in old_enum]
    if removed:
        _add(
            changes,
            schema_name,
            target,
            SchemaChangeClassification.BREAKING,
            "Enum values were removed.",
            removed,
            None,
        )
    if added:
        _add(
            changes,
            schema_name,
            target,
            SchemaChangeClassification.NON_BREAKING,
            "Enum values were added.",
            None,
            added,
        )


def _compare_const(
    old_const: Any,
    new_const: Any,
    path: str,
    schema_name: str,
    changes: list[SchemaChange],
) -> None:
    if old_const == new_const:
        return
    classification = (
        SchemaChangeClassification.NON_BREAKING
        if old_const is not None and new_const is None
        else SchemaChangeClassification.BREAKING
    )
    _add(
        changes,
        schema_name,
        _path(path, "const"),
        classification,
        "Constant-value constraint changed.",
        old_const,
        new_const,
    )


def _compare_numeric_constraint(
    key: str,
    old_value: Any,
    new_value: Any,
    path: str,
    schema_name: str,
    changes: list[SchemaChange],
) -> None:
    if old_value == new_value:
        return
    target = _path(path, key)
    if old_value is None:
        classification = SchemaChangeClassification.BREAKING
        message = f"Constraint '{key}' was added."
    elif new_value is None:
        classification = SchemaChangeClassification.NON_BREAKING
        message = f"Constraint '{key}' was removed."
    elif not _is_number(old_value) or not _is_number(new_value):
        classification = SchemaChangeClassification.UNKNOWN
        message = f"Constraint '{key}' changed in a non-numeric form."
    else:
        stricter = (
            new_value > old_value if key in _MINIMUM_KEYS else new_value < old_value
        )
        classification = (
            SchemaChangeClassification.BREAKING
            if stricter
            else SchemaChangeClassification.NON_BREAKING
        )
        message = f"Constraint '{key}' became {'stricter' if stricter else 'less restrictive'}."
    _add(
        changes,
        schema_name,
        target,
        classification,
        message,
        old_value,
        new_value,
    )


def _compare_restrictive_constraint(
    key: str,
    old_value: Any,
    new_value: Any,
    path: str,
    schema_name: str,
    changes: list[SchemaChange],
) -> None:
    if old_value == new_value:
        return
    if old_value is None:
        classification = SchemaChangeClassification.BREAKING
        message = f"Restrictive constraint '{key}' was added."
    elif new_value is None:
        classification = SchemaChangeClassification.NON_BREAKING
        message = f"Restrictive constraint '{key}' was removed."
    else:
        classification = SchemaChangeClassification.UNKNOWN
        message = f"Constraint '{key}' changed and relative strictness is unknown."
    _add(
        changes,
        schema_name,
        _path(path, key),
        classification,
        message,
        old_value,
        new_value,
    )


def _compare_additional_properties(
    old_value: Any,
    new_value: Any,
    path: str,
    schema_name: str,
    changes: list[SchemaChange],
) -> None:
    if old_value == new_value:
        return
    if old_value is False and (new_value is True or new_value is None):
        classification = SchemaChangeClassification.NON_BREAKING
        message = "Additional properties became allowed."
    elif (old_value is True or old_value is None) and new_value is False:
        classification = SchemaChangeClassification.BREAKING
        message = "Additional properties became forbidden."
    else:
        classification = SchemaChangeClassification.UNKNOWN
        message = "Additional-properties schema changed in a complex form."
    _add(
        changes,
        schema_name,
        _path(path, "additionalProperties"),
        classification,
        message,
        old_value,
        new_value,
    )


def _resolve_local_ref(root: dict[str, Any], reference: str) -> dict[str, Any] | None:
    if not reference.startswith("#/"):
        return None
    current: Any = root
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, dict) else None


def _type_set(value: Any) -> set[str]:
    if isinstance(value, str):
        return {value}
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return set(value)
    return set()


def _string_set(value: Any) -> set[str]:
    if isinstance(value, list):
        return {item for item in value if isinstance(item, str)}
    return set()


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def _path(parent: str, child: str) -> str:
    return f"{parent}.{child}"


def _add(
    changes: list[SchemaChange],
    schema_name: str,
    path: str,
    classification: SchemaChangeClassification,
    message: str,
    old_value: Any,
    new_value: Any,
) -> None:
    changes.append(
        SchemaChange(
            schema_name=schema_name,
            path=path,
            classification=classification,
            message=message,
            old_value=old_value,
            new_value=new_value,
        )
    )


__all__ = [
    "SchemaChange",
    "SchemaChangeClassification",
    "SchemaDiffResult",
    "compare_json_schemas",
]
