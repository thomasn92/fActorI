"""Read-only protocol-directory compatibility comparison."""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import Field

from factori.schema_diff import (
    SchemaChange,
    SchemaChangeClassification,
    compare_json_schemas,
)
from factori.schemas import StrictModel


class ProtocolCompatibilityStatus(StrEnum):
    """Conservative aggregate status for one protocol comparison."""

    COMPATIBLE = "Compatible"
    COMPATIBLE_WITH_WARNINGS = "CompatibleWithWarnings"
    BREAKING_CHANGES_DETECTED = "BreakingChangesDetected"
    COMPARISON_FAILED = "ComparisonFailed"


class ProtocolCompatibilityReport(StrictModel):
    """Deterministic comparison of two JSON Schema directories."""

    old_protocol_version: str = Field(min_length=1)
    new_protocol_version: str = Field(min_length=1)
    schemas_added: list[str] = Field(default_factory=list)
    schemas_removed: list[str] = Field(default_factory=list)
    schemas_changed: list[str] = Field(default_factory=list)
    breaking_changes: list[SchemaChange] = Field(default_factory=list)
    nonbreaking_changes: list[SchemaChange] = Field(default_factory=list)
    documentation_changes: list[SchemaChange] = Field(default_factory=list)
    unknown_changes: list[SchemaChange] = Field(default_factory=list)
    compatibility_status: ProtocolCompatibilityStatus
    comparison_errors: list[str] = Field(default_factory=list)
    developer_contract_only: bool = True
    is_verification_evidence: bool = False


def compare_schema_dirs(old_dir: Path, new_dir: Path) -> ProtocolCompatibilityReport:
    """Compare matching schema files without writing or mutating either directory."""
    old_dir = Path(old_dir)
    new_dir = Path(new_dir)
    errors: list[str] = []
    if not old_dir.is_dir():
        errors.append(f"Old schema directory does not exist: {old_dir}")
    if not new_dir.is_dir():
        errors.append(f"New schema directory does not exist: {new_dir}")
    if errors:
        return _failed_report(old_dir, new_dir, errors)

    old_files = {path.name: path for path in old_dir.glob("*.schema.json")}
    new_files = {path.name: path for path in new_dir.glob("*.schema.json")}
    schemas_added = sorted(set(new_files) - set(old_files))
    schemas_removed = sorted(set(old_files) - set(new_files))
    common = sorted(set(old_files) & set(new_files))
    breaking: list[SchemaChange] = []
    nonbreaking: list[SchemaChange] = []
    documentation: list[SchemaChange] = []
    unknown: list[SchemaChange] = []
    changed: list[str] = []

    for name in schemas_removed:
        breaking.append(
            _file_change(
                name,
                SchemaChangeClassification.BREAKING,
                "Schema file was removed or renamed without a retained alias.",
            )
        )
    for name in schemas_added:
        nonbreaking.append(
            _file_change(
                name,
                SchemaChangeClassification.NON_BREAKING,
                "New schema file was added.",
            )
        )

    for name in common:
        try:
            old_schema = _load_schema(old_files[name])
            new_schema = _load_schema(new_files[name])
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"{name}: {exc}")
            continue
        result = compare_json_schemas(old_schema, new_schema, schema_name=name)
        if result.changed:
            changed.append(name)
        for change in result.changes:
            if change.classification == SchemaChangeClassification.BREAKING:
                breaking.append(change)
            elif change.classification == SchemaChangeClassification.NON_BREAKING:
                nonbreaking.append(change)
            elif change.classification == SchemaChangeClassification.DOCUMENTATION_ONLY:
                documentation.append(change)
            else:
                unknown.append(change)

    if errors:
        status = ProtocolCompatibilityStatus.COMPARISON_FAILED
    elif breaking:
        status = ProtocolCompatibilityStatus.BREAKING_CHANGES_DETECTED
    elif unknown:
        status = ProtocolCompatibilityStatus.COMPATIBLE_WITH_WARNINGS
    else:
        status = ProtocolCompatibilityStatus.COMPATIBLE
    return ProtocolCompatibilityReport(
        old_protocol_version=_load_version(old_dir),
        new_protocol_version=_load_version(new_dir),
        schemas_added=schemas_added,
        schemas_removed=schemas_removed,
        schemas_changed=changed,
        breaking_changes=sorted(breaking, key=_change_key),
        nonbreaking_changes=sorted(nonbreaking, key=_change_key),
        documentation_changes=sorted(documentation, key=_change_key),
        unknown_changes=sorted(unknown, key=_change_key),
        compatibility_status=status,
        comparison_errors=sorted(errors),
    )


def _failed_report(
    old_dir: Path,
    new_dir: Path,
    errors: list[str],
) -> ProtocolCompatibilityReport:
    return ProtocolCompatibilityReport(
        old_protocol_version=_load_version(old_dir),
        new_protocol_version=_load_version(new_dir),
        compatibility_status=ProtocolCompatibilityStatus.COMPARISON_FAILED,
        comparison_errors=sorted(errors),
    )


def _load_schema(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("schema root must be a JSON object")
    return payload


def _load_version(schema_dir: Path) -> str:
    candidates = (schema_dir.parent / "version.json", schema_dir / "version.json")
    for path in candidates:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            version = payload.get("protocol_version")
            if isinstance(version, str) and version:
                return version
    if schema_dir.is_dir():
        for path in sorted(schema_dir.glob("*.schema.json")):
            try:
                schema = _load_schema(path)
            except (OSError, json.JSONDecodeError, ValueError):
                continue
            version = schema.get("x-factori-protocol-version")
            if isinstance(version, str) and version:
                return version
    return "unknown"


def _file_change(
    name: str,
    classification: SchemaChangeClassification,
    message: str,
) -> SchemaChange:
    return SchemaChange(
        schema_name=name,
        path="$",
        classification=classification,
        message=message,
    )


def _change_key(change: SchemaChange) -> tuple[str, str, str]:
    return (change.schema_name, change.path, change.message)


__all__ = [
    "ProtocolCompatibilityReport",
    "ProtocolCompatibilityStatus",
    "compare_schema_dirs",
]
