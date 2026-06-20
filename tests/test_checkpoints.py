from __future__ import annotations

from factori.checkpoints import (
    get_stage_output_paths,
    get_stage_prerequisites,
    inspect_stage_checkpoint,
)
from factori.schemas import PipelineStage


def test_get_stage_prerequisites_is_deterministic_for_every_stage() -> None:
    first = {
        stage: [item.model_dump(mode="json") for item in get_stage_prerequisites(stage)]
        for stage in PipelineStage
    }
    second = {
        stage: [item.model_dump(mode="json") for item in get_stage_prerequisites(stage)]
        for stage in PipelineStage
    }

    assert first == second
    assert first[PipelineStage.RUN_STAGE_A] == []
    assert first[PipelineStage.RUN_STAGE_B][0]["required_prior_stage"] == "run-stage-a"
    assert first[PipelineStage.DIAGNOSE_RUN][0]["blocking_if_missing"] is False


def test_stage_output_paths_are_run_specific() -> None:
    assert get_stage_output_paths(PipelineStage.RUN_STAGE_A, "run-1") == [
        "runs/run-1/reports/stage-a-report.md"
    ]
    assert "runs/run-1/research_object/paper-skeleton.json" in get_stage_output_paths(
        PipelineStage.ASSEMBLE_PAPER_SKELETON,
        "run-1",
    )


def test_stage_checkpoint_detects_missing_artifacts(tmp_path) -> None:
    checkpoint = inspect_stage_checkpoint("run-1", PipelineStage.RUN_STAGE_A, tmp_path)

    assert checkpoint.completed is False
    assert checkpoint.required_artifacts_present == []
    assert checkpoint.required_artifacts_missing == [
        "runs/run-1/reports/stage-a-report.md"
    ]


def test_stage_checkpoint_detects_present_artifact(tmp_path) -> None:
    report = tmp_path / "runs" / "run-1" / "reports" / "stage-a-report.md"
    report.parent.mkdir(parents=True)
    report.write_text("# Stage A\n", encoding="utf-8")

    checkpoint = inspect_stage_checkpoint("run-1", PipelineStage.RUN_STAGE_A, tmp_path)

    assert checkpoint.completed is True
    assert checkpoint.required_artifacts_present == [
        "runs/run-1/reports/stage-a-report.md"
    ]
    assert checkpoint.required_artifacts_missing == []
