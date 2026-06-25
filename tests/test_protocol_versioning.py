from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from factori.cli import app
from factori.protocol_compat import compare_schema_dirs
from factori.protocol_versioning import (
    ProtocolVersionBump,
    ProtocolVersionCheckStatus,
    check_protocol_version_bump,
    observed_version_bump,
    required_version_bump,
)


def test_versioning_rules_classify_required_bumps(tmp_path: Path) -> None:
    old_dir = _schema_dir(tmp_path / "old", {"candidate.schema.json": _base_schema()}, "0.1.0")
    added_optional = _base_schema()
    added_optional["properties"]["title"] = {"type": "string"}
    new_dir = _schema_dir(
        tmp_path / "new",
        {"candidate.schema.json": added_optional},
        "0.2.0",
    )

    report = compare_schema_dirs(old_dir, new_dir)
    version_report = check_protocol_version_bump(report)

    assert required_version_bump(report) == ProtocolVersionBump.MINOR
    assert version_report.required_bump == ProtocolVersionBump.MINOR
    assert version_report.observed_bump == ProtocolVersionBump.MINOR
    assert version_report.status == ProtocolVersionCheckStatus.PASSED


def test_breaking_change_with_patch_bump_fails(tmp_path: Path) -> None:
    old_dir = _schema_dir(tmp_path / "old", {"candidate.schema.json": _base_schema()}, "1.2.0")
    new_schema = _base_schema()
    new_schema["properties"] = {}
    new_schema["required"] = []
    new_dir = _schema_dir(tmp_path / "new", {"candidate.schema.json": new_schema}, "1.2.1")

    report = compare_schema_dirs(old_dir, new_dir)
    version_report = check_protocol_version_bump(report)

    assert version_report.required_bump == ProtocolVersionBump.MAJOR
    assert version_report.observed_bump == ProtocolVersionBump.PATCH
    assert version_report.status == ProtocolVersionCheckStatus.FAILED


def test_documentation_only_change_with_patch_bump_passes(tmp_path: Path) -> None:
    old_dir = _schema_dir(tmp_path / "old", {"candidate.schema.json": _base_schema()}, "1.2.0")
    new_schema = _base_schema()
    new_schema["description"] = "Updated documentation only."
    new_dir = _schema_dir(tmp_path / "new", {"candidate.schema.json": new_schema}, "1.2.1")

    report = compare_schema_dirs(old_dir, new_dir)
    version_report = check_protocol_version_bump(report)

    assert version_report.required_bump == ProtocolVersionBump.PATCH
    assert version_report.observed_bump == ProtocolVersionBump.PATCH
    assert version_report.status == ProtocolVersionCheckStatus.PASSED


def test_identical_schema_may_keep_same_version(tmp_path: Path) -> None:
    old_dir = _schema_dir(tmp_path / "old", {"candidate.schema.json": _base_schema()}, "1.2.0")
    new_dir = _schema_dir(tmp_path / "new", {"candidate.schema.json": _base_schema()}, "1.2.0")

    report = compare_schema_dirs(old_dir, new_dir)
    version_report = check_protocol_version_bump(report)

    assert version_report.required_bump == ProtocolVersionBump.NONE
    assert version_report.observed_bump == ProtocolVersionBump.NONE
    assert version_report.status == ProtocolVersionCheckStatus.PASSED


def test_unknown_change_requires_human_review(tmp_path: Path) -> None:
    old_dir = _schema_dir(
        tmp_path / "old",
        {"value.schema.json": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        "1.2.0",
    )
    new_dir = _schema_dir(
        tmp_path / "new",
        {"value.schema.json": {"anyOf": [{"type": "integer"}, {"type": "null"}]}},
        "1.3.0",
    )

    report = compare_schema_dirs(old_dir, new_dir)
    version_report = check_protocol_version_bump(report)

    assert version_report.required_bump == ProtocolVersionBump.HUMAN_REVIEW
    assert version_report.status == ProtocolVersionCheckStatus.HUMAN_REVIEW_REQUIRED


def test_observed_version_bump_classification() -> None:
    assert observed_version_bump("1.2.3", "2.0.0") == ProtocolVersionBump.MAJOR
    assert observed_version_bump("1.2.3", "1.3.0") == ProtocolVersionBump.MINOR
    assert observed_version_bump("1.2.3", "1.2.4") == ProtocolVersionBump.PATCH
    assert observed_version_bump("1.2.3", "1.2.3") == ProtocolVersionBump.NONE


def test_check_protocol_version_cli(tmp_path: Path) -> None:
    old_dir = _schema_dir(tmp_path / "old", {"candidate.schema.json": _base_schema()}, "1.2.0")
    new_schema = _base_schema()
    new_schema["properties"]["title"] = {"type": "string"}
    new_dir = _schema_dir(tmp_path / "new", {"candidate.schema.json": new_schema}, "1.3.0")
    runner = CliRunner()

    text = runner.invoke(
        app,
        ["check-protocol-version", "--old-dir", str(old_dir), "--new-dir", str(new_dir)],
    )
    json_result = runner.invoke(
        app,
        [
            "check-protocol-version",
            "--old-dir",
            str(old_dir),
            "--new-dir",
            str(new_dir),
            "--json",
        ],
    )

    assert text.exit_code == 0, text.output
    assert "version_check_status=Passed" in text.output
    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["required_bump"] == "Minor"


def _base_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "description": "Base schema.",
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }


def _schema_dir(
    root: Path,
    schemas: dict[str, dict[str, object]],
    version: str,
) -> Path:
    schema_dir = root / "jsonschema"
    schema_dir.mkdir(parents=True)
    (root / "version.json").write_text(
        json.dumps({"protocol_version": version}, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for name, schema in schemas.items():
        (schema_dir / name).write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return schema_dir
