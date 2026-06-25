from __future__ import annotations

import os
from pathlib import Path

from factori.artifacts import ArtifactStore
from factori.hashing import sha256_file
from factori.ledger import ResearchLedger
from factori.output_hygiene import inspect_output_hygiene
from factori.persistence import (
    ArtifactWriteSpec,
    persist_artifacts_with_commit,
    persist_json_artifact,
    persist_markdown_artifact,
)
from factori.schemas import (
    ArtifactType,
    ConstraintSet,
    ControllerActionType,
    OutputHygieneStatus,
)
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.storage_protocols import FixedClock

FIXED_TIMESTAMP = "2035-01-02T03:04:05.000000Z"


def test_persist_json_artifact_writes_commits_links_and_hashes(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    result = persist_json_artifact(
        run_id="run-1",
        store=store,
        ledger=ledger,
        artifact_id="candidate-001",
        artifact_type=ArtifactType.CANDIDATE,
        payload={"id": "candidate-001"},
        action_type=ControllerActionType.ADD_CANDIDATE,
        commit_payload={"candidate_id": "candidate-001"},
        candidate_id="candidate-001",
        metadata={"stage": "test", "fake": True},
    )

    assert (tmp_path / result.artifact.path).is_file()
    assert result.artifact.content_hash == sha256_file(tmp_path / result.artifact.path)
    assert result.artifact.producing_commit_hash == result.commit.commit_hash
    assert result.commit.candidate_id == "candidate-001"
    assert result.commit.artifact_refs[0].producing_commit_hash == result.commit.commit_hash
    assert (tmp_path / f"{result.artifact.path}.meta.json").is_file()


def test_persist_markdown_artifact_uses_atomic_artifact_store_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    real_replace = os.replace
    replacements: list[tuple[Path, Path]] = []

    def observed_replace(source: str | Path, target: str | Path) -> None:
        source_path = Path(source)
        target_path = Path(target)
        assert source_path.parent == target_path.parent
        replacements.append((source_path, target_path))
        real_replace(source_path, target_path)

    monkeypatch.setattr("factori.artifacts.os.replace", observed_replace)

    result = persist_markdown_artifact(
        run_id="run-1",
        store=store,
        ledger=ledger,
        artifact_id="report-001",
        artifact_type=ArtifactType.REPORT,
        markdown="# Report\r\n",
        action_type=ControllerActionType.WRITE_ARTIFACT,
        commit_payload={"artifact_id": "report-001"},
    )

    assert len(replacements) >= 2
    assert replacements[0][1] == tmp_path / result.artifact.path
    assert (tmp_path / result.artifact.path).read_bytes() == b"# Report\n"


def test_persistence_helper_defaults_to_non_verification_metadata(tmp_path: Path) -> None:
    result = persist_json_artifact(
        run_id="run-1",
        store=ArtifactStore(tmp_path),
        ledger=ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite"),
        artifact_id="report-001",
        artifact_type=ArtifactType.REPORT,
        payload={"context": True},
        action_type=ControllerActionType.WRITE_ARTIFACT,
        commit_payload={"artifact_id": "report-001"},
    )

    assert result.artifact.metadata["is_verification_evidence"] is False
    assert not result.artifact.is_mvp_verification_evidence()


def test_persistence_helper_accepts_metadata_candidate_and_fixed_clock(
    tmp_path: Path,
) -> None:
    first = _persist_fixed_clock(tmp_path / "a")
    second = _persist_fixed_clock(tmp_path / "b")

    assert first.commit.timestamp == FIXED_TIMESTAMP
    assert first.commit.commit_hash == second.commit.commit_hash
    assert first.artifact.content_hash == second.artifact.content_hash
    assert first.artifact.metadata["custom"] == "metadata"
    assert first.commit.candidate_id == "candidate-001"


def test_persist_artifacts_with_commit_handles_multiple_artifacts(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    result = persist_artifacts_with_commit(
        run_id="run-1",
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                artifact_id="request",
                artifact_type=ArtifactType.REPORT,
                payload={"request": 1},
                artifact_format="json",
            ),
            ArtifactWriteSpec(
                artifact_id="response",
                artifact_type=ArtifactType.REPORT,
                payload={"response": 1},
                artifact_format="json",
            ),
        ],
        action_type=ControllerActionType.WRITE_ARTIFACT,
        commit_payload={"bundle": True},
    )

    assert [artifact.id for artifact in result.artifacts] == ["request", "response"]
    assert all(
        artifact.producing_commit_hash == result.commit.commit_hash
        for artifact in result.artifacts
    )
    assert len(result.commit.artifact_refs) == 2


def test_persistence_helpers_do_not_print_or_exit(tmp_path: Path, capsys) -> None:
    persist_json_artifact(
        run_id="run-1",
        store=ArtifactStore(tmp_path),
        ledger=ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite"),
        artifact_id="quiet",
        artifact_type=ArtifactType.REPORT,
        payload={"quiet": True},
        action_type=ControllerActionType.WRITE_ARTIFACT,
        commit_payload={"quiet": True},
    )

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_stage_a_artifact_ids_and_ledger_actions_remain_stable(tmp_path: Path) -> None:
    store, ledger, result = _run_stage_a(tmp_path)
    del store
    action_types = [commit.action_type for commit in ledger.list_commits("run-1")]

    assert result.report_artifact.id == "stage-a-report"
    assert sorted(result.candidate_artifacts) == sorted(
        candidate.id for candidate in result.generated_candidates
    )
    assert sorted(artifact.id for artifact in result.score_artifacts.values()) == sorted(
        f"{candidate.id}-score" for candidate in result.generated_candidates
    )
    assert action_types[0] == ControllerActionType.STAGE_A_STARTED
    assert ControllerActionType.STAGE0_OPPORTUNITY_DISCOVERY in action_types
    assert action_types.count(ControllerActionType.STAGE_A_CANDIDATE_GENERATED) == 15
    assert action_types.count(ControllerActionType.STAGE_A_SCORE_COMPUTED) == 15
    assert ControllerActionType.STAGE_A_SURVIVORS_SELECTED in action_types
    assert action_types[-1] == ControllerActionType.STAGE_A_REPORT_WRITTEN


def test_stage_b_artifact_ids_and_ledger_actions_remain_stable(tmp_path: Path) -> None:
    store, ledger, _ = _run_stage_a(tmp_path)
    result = run_stage_b(run_id="run-1", store=store, ledger=ledger)
    action_types = [commit.action_type for commit in ledger.list_commits("run-1")]
    artifact_ids = [
        artifact.id
        for artifact_list in result.artifacts.values()
        for artifact in artifact_list
    ]

    assert result.report_artifact.id == "stage-b-report"
    assert len(result.children) == 16
    assert result.children[0].id in artifact_ids
    assert f"reviewer-report-{result.children[0].id}" in artifact_ids
    assert f"stage-b-score-{result.children[0].id}" in artifact_ids
    assert f"bridge-report-{result.children[0].id}" in artifact_ids
    assert f"baseline-report-{result.children[0].id}" in artifact_ids
    assert f"redteam-report-{result.children[0].id}" in artifact_ids
    assert action_types.count(ControllerActionType.STAGE_B_CHILD_GENERATED) == 16
    assert action_types.count(ControllerActionType.STAGE_B_REVIEWERS_RUN) == 16
    assert action_types.count(ControllerActionType.STAGE_B_SCORE_COMPUTED) == 16
    assert action_types.count(ControllerActionType.STAGE_B_BRIDGE_CHECKED) == 16
    assert action_types.count(ControllerActionType.STAGE_B_BASELINE_CHECKED) == 16
    assert action_types.count(ControllerActionType.STAGE_B_REDTEAM_CHECKED) == 16
    assert ControllerActionType.STAGE_B_SURVIVORS_SELECTED in action_types
    assert action_types[-1] == ControllerActionType.STAGE_B_REPORT_WRITTEN


def test_hygiene_still_accepts_stage_a_b_outputs(tmp_path: Path) -> None:
    store, ledger, _ = _run_stage_a(tmp_path)
    run_stage_b(run_id="run-1", store=store, ledger=ledger)

    hygiene_report = inspect_output_hygiene("run-1", root=tmp_path)

    assert hygiene_report.hygiene_status in {
        OutputHygieneStatus.CLEAN,
        OutputHygieneStatus.CLEAN_WITH_WARNINGS,
        OutputHygieneStatus.HYGIENE_ISSUES_FOUND,
    }


def _persist_fixed_clock(root: Path):
    store = ArtifactStore(root)
    ledger = ResearchLedger(
        root / "runs" / "run-1" / "ledger.sqlite",
        clock=FixedClock(FIXED_TIMESTAMP),
    )
    return persist_json_artifact(
        run_id="run-1",
        store=store,
        ledger=ledger,
        artifact_id="candidate-001",
        artifact_type=ArtifactType.CANDIDATE,
        payload={"id": "candidate-001"},
        action_type=ControllerActionType.ADD_CANDIDATE,
        commit_payload={"candidate_id": "candidate-001"},
        candidate_id="candidate-001",
        metadata={"custom": "metadata"},
    )


def _run_stage_a(tmp_path: Path):
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    result = run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )
    return store, ledger, result
