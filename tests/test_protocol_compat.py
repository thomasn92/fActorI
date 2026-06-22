from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from factori.cli import app
from factori.protocol_compat import ProtocolCompatibilityStatus, compare_schema_dirs


def test_identical_schema_dirs_are_compatible_and_deterministic(tmp_path: Path) -> None:
    old_dir = _schema_dir(tmp_path / "old", {"candidate.schema.json": _base_schema()}, "0.1.0")
    new_dir = _schema_dir(tmp_path / "new", {"candidate.schema.json": _base_schema()}, "0.1.1")

    first = compare_schema_dirs(old_dir, new_dir)
    second = compare_schema_dirs(old_dir, new_dir)

    assert first == second
    assert first.compatibility_status == ProtocolCompatibilityStatus.COMPATIBLE
    assert first.old_protocol_version == "0.1.0"
    assert first.new_protocol_version == "0.1.1"
    assert first.schemas_changed == []


def test_added_schema_is_nonbreaking(tmp_path: Path) -> None:
    old_dir = _schema_dir(tmp_path / "old", {"candidate.schema.json": _base_schema()})
    new_dir = _schema_dir(
        tmp_path / "new",
        {
            "candidate.schema.json": _base_schema(),
            "claim.schema.json": _base_schema(),
        },
    )

    report = compare_schema_dirs(old_dir, new_dir)

    assert report.schemas_added == ["claim.schema.json"]
    assert len(report.nonbreaking_changes) == 1
    assert report.compatibility_status == ProtocolCompatibilityStatus.COMPATIBLE


def test_removed_schema_is_breaking(tmp_path: Path) -> None:
    old_dir = _schema_dir(
        tmp_path / "old",
        {
            "candidate.schema.json": _base_schema(),
            "claim.schema.json": _base_schema(),
        },
    )
    new_dir = _schema_dir(tmp_path / "new", {"candidate.schema.json": _base_schema()})

    report = compare_schema_dirs(old_dir, new_dir)

    assert report.schemas_removed == ["claim.schema.json"]
    assert len(report.breaking_changes) == 1
    assert report.compatibility_status == (
        ProtocolCompatibilityStatus.BREAKING_CHANGES_DETECTED
    )


def test_unknown_change_produces_compatible_with_warnings(tmp_path: Path) -> None:
    old = {"anyOf": [{"type": "string"}, {"type": "null"}]}
    new = {"anyOf": [{"type": "integer"}, {"type": "null"}]}
    old_dir = _schema_dir(tmp_path / "old", {"value.schema.json": old})
    new_dir = _schema_dir(tmp_path / "new", {"value.schema.json": new})

    report = compare_schema_dirs(old_dir, new_dir)

    assert report.schemas_changed == ["value.schema.json"]
    assert len(report.unknown_changes) == 1
    assert report.compatibility_status == (
        ProtocolCompatibilityStatus.COMPATIBLE_WITH_WARNINGS
    )


def test_cli_json_is_valid_and_fail_on_breaking_is_nonzero(tmp_path: Path) -> None:
    old_dir = _schema_dir(tmp_path / "old", {"candidate.schema.json": _base_schema()})
    new_schema = _base_schema()
    new_schema["properties"] = {}
    new_schema["required"] = []
    new_dir = _schema_dir(tmp_path / "new", {"candidate.schema.json": new_schema})
    runner = CliRunner()

    json_result = runner.invoke(
        app,
        [
            "check-protocol-compat",
            "--old-dir",
            str(old_dir),
            "--new-dir",
            str(new_dir),
            "--json",
        ],
    )
    failing_result = runner.invoke(
        app,
        [
            "check-protocol-compat",
            "--old-dir",
            str(old_dir),
            "--new-dir",
            str(new_dir),
            "--fail-on-breaking",
        ],
    )

    assert json_result.exit_code == 0, json_result.output
    payload = json.loads(json_result.output)
    assert payload["compatibility_status"] == "BreakingChangesDetected"
    assert failing_result.exit_code == 1
    assert "breaking_changes=1" in failing_result.output


def test_comparison_is_read_only_and_creates_no_run_artifacts(tmp_path: Path) -> None:
    old_dir = _schema_dir(tmp_path / "old", {"candidate.schema.json": _base_schema()})
    new_dir = _schema_dir(tmp_path / "new", {"candidate.schema.json": _base_schema()})
    before = _tree_snapshot(tmp_path)

    compare_schema_dirs(old_dir, new_dir)
    result = CliRunner().invoke(
        app,
        [
            "check-protocol-compat",
            "--old-dir",
            str(old_dir),
            "--new-dir",
            str(new_dir),
        ],
    )

    assert result.exit_code == 0, result.output
    assert _tree_snapshot(tmp_path) == before
    assert not (tmp_path / "runs").exists()


def test_missing_directory_returns_comparison_failed(tmp_path: Path) -> None:
    report = compare_schema_dirs(tmp_path / "missing-old", tmp_path / "missing-new")

    assert report.compatibility_status == ProtocolCompatibilityStatus.COMPARISON_FAILED
    assert len(report.comparison_errors) == 2


def _base_schema() -> dict[str, object]:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {"id": {"type": "string"}},
        "required": ["id"],
    }


def _schema_dir(
    root: Path,
    schemas: dict[str, dict[str, object]],
    version: str = "0.1.0",
) -> Path:
    schema_dir = root / "jsonschema"
    schema_dir.mkdir(parents=True)
    (schema_dir / "version.json").write_text(
        json.dumps({"protocol_version": version}, sort_keys=True),
        encoding="utf-8",
    )
    for name, schema in schemas.items():
        (schema_dir / name).write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    return schema_dir


def _tree_snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }
