from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest

from factori.artifacts import ArtifactStore
from factori.hashing import sha256_bytes
from factori.ledger import ResearchLedger
from factori.run_all import run_deterministic_pipeline
from factori.schema_export import require_protocols_current
from factori.schemas import (
    ArtifactType,
    ControllerActionType,
    PipelineRunConfig,
    PipelineStage,
)
from factori.storage_protocols import (
    ArtifactStoreProtocol,
    Clock,
    FixedClock,
    LedgerProtocol,
    SystemClock,
)

FIXED_TIMESTAMP = "2030-01-02T03:04:05.000000Z"


def test_storage_protocols_are_runtime_checkable(tmp_path: Path) -> None:
    ledger = ResearchLedger(tmp_path / "ledger.sqlite")
    store = ArtifactStore(tmp_path)

    assert isinstance(SystemClock(), Clock)
    assert isinstance(FixedClock(FIXED_TIMESTAMP), Clock)
    assert isinstance(ledger, LedgerProtocol)
    assert isinstance(store, ArtifactStoreProtocol)


def test_fixed_clock_can_be_injected_into_ledger_and_per_commit(tmp_path: Path) -> None:
    ledger = ResearchLedger(
        tmp_path / "ledger.sqlite",
        clock=FixedClock(FIXED_TIMESTAMP),
    )
    root = ledger.append_commit(
        run_id="run-1",
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": "run-1"},
    )
    override_timestamp = "2040-02-03T04:05:06.000000Z"
    child = ledger.append_commit(
        run_id="run-1",
        parent_hash=root.commit_hash,
        action_type=ControllerActionType.VALIDATE_RUN,
        payload={"valid": True},
        clock=FixedClock(override_timestamp),
    )

    assert root.timestamp == FIXED_TIMESTAMP
    assert child.timestamp == override_timestamp


def test_default_clock_preserves_utc_timestamp_shape(tmp_path: Path) -> None:
    commit = ResearchLedger(tmp_path / "ledger.sqlite").append_commit(
        run_id="run-1",
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": "run-1"},
    )

    parsed = datetime.fromisoformat(commit.timestamp.replace("Z", "+00:00"))
    assert commit.timestamp.endswith("Z")
    assert parsed.tzinfo is not None


def test_fixed_clock_controls_pipeline_and_commit_timestamps(tmp_path: Path) -> None:
    report = run_deterministic_pipeline(
        PipelineRunConfig(
            run_id="fixed-clock-run",
            domain="machine learning",
            method="calibration",
            root=tmp_path,
            stop_after=PipelineStage.RUN_STAGE_A,
        ),
        clock=FixedClock(FIXED_TIMESTAMP),
    )
    ledger = ResearchLedger(tmp_path / "runs" / "fixed-clock-run" / "ledger.sqlite")
    stored_report = json.loads(
        (
            tmp_path
            / "runs"
            / "fixed-clock-run"
            / "reports"
            / "pipeline-run-report.json"
        ).read_text(encoding="utf-8")
    )

    assert report.started_at == FIXED_TIMESTAMP
    assert report.finished_at == FIXED_TIMESTAMP
    assert stored_report["started_at"] == FIXED_TIMESTAMP
    assert stored_report["finished_at"] == FIXED_TIMESTAMP
    assert all(result.started_at == FIXED_TIMESTAMP for result in report.stage_results)
    assert all(result.finished_at == FIXED_TIMESTAMP for result in report.stage_results)
    assert {commit.timestamp for commit in ledger.list_commits("fixed-clock-run")} == {
        FIXED_TIMESTAMP
    }


def test_text_writes_are_newline_stable_and_hash_final_bytes(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    windows_style = store.write_markdown(
        run_id="windows",
        artifact_id="report",
        artifact_type=ArtifactType.REPORT,
        markdown="first\r\nsecond\rthird\n",
    )
    unix_style = store.write_markdown(
        run_id="unix",
        artifact_id="report",
        artifact_type=ArtifactType.REPORT,
        markdown="first\nsecond\nthird\n",
    )
    final_path = tmp_path / windows_style.path

    assert final_path.read_bytes() == b"first\nsecond\nthird\n"
    assert windows_style.content_hash == unix_style.content_hash
    assert windows_style.content_hash == sha256_bytes(final_path.read_bytes())


def test_artifact_write_uses_same_directory_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(tmp_path)
    real_replace = os.replace
    replacements: list[tuple[Path, Path]] = []

    def observed_replace(source: str | Path, target: str | Path) -> None:
        source_path = Path(source)
        target_path = Path(target)
        assert source_path.parent == target_path.parent
        assert source_path.name.endswith(".tmp")
        assert source_path.read_bytes() == b"atomic\n"
        replacements.append((source_path, target_path))
        real_replace(source_path, target_path)

    monkeypatch.setattr("factori.artifacts.os.replace", observed_replace)
    artifact = store.write_markdown(
        run_id="run-1",
        artifact_id="atomic-report",
        artifact_type=ArtifactType.REPORT,
        markdown="atomic\n",
    )

    assert len(replacements) == 1
    assert replacements[0][1] == tmp_path / artifact.path
    assert (tmp_path / artifact.path).read_bytes() == b"atomic\n"


def test_failed_replace_preserves_final_file_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = ArtifactStore(tmp_path)
    original = store.write_markdown(
        run_id="run-1",
        artifact_id="report",
        artifact_type=ArtifactType.REPORT,
        markdown="original\n",
    )
    final_path = tmp_path / original.path

    def failing_replace(source: str | Path, target: str | Path) -> None:
        del source, target
        raise OSError("simulated replace failure")

    monkeypatch.setattr("factori.artifacts.os.replace", failing_replace)
    with pytest.raises(OSError, match="simulated replace failure"):
        store.write_markdown(
            run_id="run-1",
            artifact_id="report",
            artifact_type=ArtifactType.REPORT,
            markdown="replacement\n",
        )

    assert final_path.read_bytes() == b"original\n"
    assert list(final_path.parent.glob(f".{final_path.name}.*.tmp")) == []


def test_protocol_export_remains_current() -> None:
    assert require_protocols_current().up_to_date
