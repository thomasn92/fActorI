"""Deterministic run checkpoint and prerequisite tables."""

from __future__ import annotations

from pathlib import Path

from factori.config import LEDGER_FILENAME
from factori.pipeline import PIPELINE_STAGE_ORDER
from factori.schemas import (
    DiagnosticReport,
    PipelineRunReport,
    PipelineStage,
    ReplayVerificationReport,
    StageCheckpoint,
    StagePrerequisite,
)

OPTIONAL_CHECKPOINT_STAGES = {PipelineStage.DIAGNOSE_RUN}

_STAGE_OUTPUTS: dict[PipelineStage, tuple[str, ...]] = {
    PipelineStage.RUN_STAGE_A: ("reports/stage-a-report.md",),
    PipelineStage.RUN_STAGE_B: ("reports/stage-b-report.md",),
    PipelineStage.SELECT_STAGE_C: (
        "reports/budget-selection.json",
        "reports/stage-c-selection-report.md",
    ),
    PipelineStage.RUN_STAGE_C: ("reports/stage-c-verification-report.md",),
    PipelineStage.SYNTHESIZE_ABSTRACT: (
        "reports/final-nucleus.json",
        "reports/abstract-synthesis-report.md",
    ),
    PipelineStage.PLAN_MANUSCRIPT: (
        "reports/claim-table.json",
        "reports/blocked-claims.json",
        "reports/manuscript-plan.json",
        "reports/manuscript-plan.md",
    ),
    PipelineStage.BUILD_DRAFT_SKELETON: (
        "reports/draft-skeleton.json",
        "reports/draft-skeleton.md",
        "reports/manuscript-checklist.json",
        "reports/manuscript-checklist.md",
    ),
    PipelineStage.PACKAGE_RESEARCH_OBJECT: (
        "research_object/research-object.json",
        "research_object/research-object.md",
        "research_object/artifact-manifest.json",
        "research_object/ledger-summary.json",
        "research_object/branch-outcomes.json",
        "research_object/reproducibility-manifest.json",
    ),
    PipelineStage.ASSEMBLE_PAPER_SKELETON: (
        "research_object/paper-skeleton.json",
        "research_object/paper-skeleton.md",
        "research_object/paper-assembly-report.json",
    ),
    PipelineStage.FINAL_AUDIT: (
        "reports/final-audit-report.json",
        "reports/final-audit-report.md",
        "reports/release-gate-decision.json",
        "reports/release-gate-decision.md",
    ),
    PipelineStage.PREPARE_EXPORT: (
        "reports/prose-generation-contract.json",
        "reports/latex-export-plan.json",
        "reports/export-section-map.json",
        "reports/export-claim-map.json",
        "reports/export-readiness-report.json",
        "reports/export-readiness-report.md",
        "reports/export-bundle-manifest.json",
    ),
}

_REPLAY_OUTPUTS = (
    "replay/replay-verification-report.json",
    "reports/pipeline-run-report.json with replay_status",
)
_DIAGNOSTIC_OUTPUTS = (
    "diagnostics/diagnostic-report.json",
    "reports/pipeline-run-report.json with diagnostic_status",
)

_PREREQUISITES: dict[PipelineStage, tuple[StagePrerequisite, ...]] = {
    PipelineStage.RUN_STAGE_A: (),
    PipelineStage.RUN_STAGE_B: (
        StagePrerequisite(
            stage_name=PipelineStage.RUN_STAGE_B,
            required_prior_stage=PipelineStage.RUN_STAGE_A,
            required_artifact_path_or_kind="runs/<run_id>/reports/stage-a-report.md",
            required_report="stage-a-report",
            message="Stage B requires Stage A survivor artifacts/report.",
        ),
    ),
    PipelineStage.SELECT_STAGE_C: (
        StagePrerequisite(
            stage_name=PipelineStage.SELECT_STAGE_C,
            required_prior_stage=PipelineStage.RUN_STAGE_B,
            required_artifact_path_or_kind="runs/<run_id>/reports/stage-b-report.md",
            required_report="stage-b-report",
            message="Stage C selection requires Stage B survivor artifacts/report.",
        ),
    ),
    PipelineStage.RUN_STAGE_C: (
        StagePrerequisite(
            stage_name=PipelineStage.RUN_STAGE_C,
            required_prior_stage=PipelineStage.SELECT_STAGE_C,
            required_artifact_path_or_kind=(
                "runs/<run_id>/reports/stage-c-selection-report.md"
            ),
            required_report="stage-c-selection-report",
            message="Stage C verification requires Stage C selection artifacts.",
        ),
    ),
    PipelineStage.SYNTHESIZE_ABSTRACT: (
        StagePrerequisite(
            stage_name=PipelineStage.SYNTHESIZE_ABSTRACT,
            required_prior_stage=PipelineStage.RUN_STAGE_C,
            required_artifact_path_or_kind=(
                "runs/<run_id>/reports/stage-c-verification-report.md"
            ),
            required_report="stage-c-verification-report",
            message="Abstract synthesis requires Stage C verification artifacts.",
        ),
    ),
    PipelineStage.PLAN_MANUSCRIPT: (
        StagePrerequisite(
            stage_name=PipelineStage.PLAN_MANUSCRIPT,
            required_prior_stage=PipelineStage.SYNTHESIZE_ABSTRACT,
            required_artifact_path_or_kind="runs/<run_id>/reports/final-nucleus.json",
            required_report="final-nucleus",
            message="Manuscript planning requires the final nucleus artifact.",
        ),
    ),
    PipelineStage.BUILD_DRAFT_SKELETON: (
        StagePrerequisite(
            stage_name=PipelineStage.BUILD_DRAFT_SKELETON,
            required_prior_stage=PipelineStage.PLAN_MANUSCRIPT,
            required_artifact_path_or_kind="runs/<run_id>/reports/manuscript-plan.json",
            required_report="manuscript-plan",
            message="Draft skeleton generation requires the manuscript plan.",
        ),
        StagePrerequisite(
            stage_name=PipelineStage.BUILD_DRAFT_SKELETON,
            required_prior_stage=PipelineStage.PLAN_MANUSCRIPT,
            required_artifact_path_or_kind="runs/<run_id>/reports/claim-table.json",
            required_report="claim-table",
            message="Draft skeleton generation requires the claim table.",
        ),
    ),
    PipelineStage.PACKAGE_RESEARCH_OBJECT: (
        StagePrerequisite(
            stage_name=PipelineStage.PACKAGE_RESEARCH_OBJECT,
            required_prior_stage=PipelineStage.BUILD_DRAFT_SKELETON,
            required_artifact_path_or_kind="runs/<run_id>/reports/draft-skeleton.json",
            required_report="draft-skeleton",
            message="Research object packaging requires draft skeleton artifacts.",
        ),
        StagePrerequisite(
            stage_name=PipelineStage.PACKAGE_RESEARCH_OBJECT,
            required_prior_stage=PipelineStage.PLAN_MANUSCRIPT,
            required_artifact_path_or_kind="runs/<run_id>/reports/manuscript-plan.json",
            required_report="manuscript-plan",
            message="Research object packaging requires manuscript planning artifacts.",
        ),
        StagePrerequisite(
            stage_name=PipelineStage.PACKAGE_RESEARCH_OBJECT,
            required_prior_stage=PipelineStage.PLAN_MANUSCRIPT,
            required_artifact_path_or_kind="runs/<run_id>/reports/claim-table.json",
            required_report="claim-table",
            message="Research object packaging requires the claim table.",
        ),
        StagePrerequisite(
            stage_name=PipelineStage.PACKAGE_RESEARCH_OBJECT,
            required_prior_stage=PipelineStage.PLAN_MANUSCRIPT,
            required_artifact_path_or_kind="runs/<run_id>/reports/blocked-claims.json",
            required_report="blocked-claims",
            message="Research object packaging requires blocked-claim artifacts.",
        ),
        StagePrerequisite(
            stage_name=PipelineStage.PACKAGE_RESEARCH_OBJECT,
            required_prior_stage=PipelineStage.BUILD_DRAFT_SKELETON,
            required_artifact_path_or_kind=(
                "runs/<run_id>/reports/manuscript-checklist.json"
            ),
            required_report="manuscript-checklist",
            message="Research object packaging requires manuscript checklist artifacts.",
        ),
    ),
    PipelineStage.ASSEMBLE_PAPER_SKELETON: (
        StagePrerequisite(
            stage_name=PipelineStage.ASSEMBLE_PAPER_SKELETON,
            required_prior_stage=PipelineStage.PACKAGE_RESEARCH_OBJECT,
            required_artifact_path_or_kind=(
                "runs/<run_id>/research_object/research-object.json"
            ),
            required_report="research-object",
            message="Paper assembly requires research object packaging artifacts.",
        ),
    ),
    PipelineStage.FINAL_AUDIT: (
        StagePrerequisite(
            stage_name=PipelineStage.FINAL_AUDIT,
            required_prior_stage=PipelineStage.ASSEMBLE_PAPER_SKELETON,
            required_artifact_path_or_kind=(
                "runs/<run_id>/research_object/paper-skeleton.json"
            ),
            required_report="paper-skeleton",
            message="Final audit requires paper skeleton artifacts.",
        ),
    ),
    PipelineStage.PREPARE_EXPORT: (
        StagePrerequisite(
            stage_name=PipelineStage.PREPARE_EXPORT,
            required_prior_stage=PipelineStage.FINAL_AUDIT,
            required_artifact_path_or_kind="runs/<run_id>/reports/final-audit-report.json",
            required_report="final-audit-report",
            message="Export preparation requires final audit artifacts.",
        ),
        StagePrerequisite(
            stage_name=PipelineStage.PREPARE_EXPORT,
            required_prior_stage=PipelineStage.FINAL_AUDIT,
            required_artifact_path_or_kind=(
                "runs/<run_id>/reports/release-gate-decision.json"
            ),
            required_report="release-gate-decision",
            message="Export preparation requires release gate artifacts.",
        ),
    ),
    PipelineStage.REPLAY_VERIFY: (
        StagePrerequisite(
            stage_name=PipelineStage.REPLAY_VERIFY,
            required_prior_stage=PipelineStage.PREPARE_EXPORT,
            required_artifact_path_or_kind=(
                "runs/<run_id>/reports/export-readiness-report.json"
            ),
            required_report="export-readiness-report",
            message="Replay verification requires export preparation artifacts.",
        ),
    ),
    PipelineStage.DIAGNOSE_RUN: (
        StagePrerequisite(
            stage_name=PipelineStage.DIAGNOSE_RUN,
            required_prior_stage=PipelineStage.FINAL_AUDIT,
            required_artifact_path_or_kind="runs/<run_id>/reports/final-audit-report.json",
            required_report="final-audit-report",
            blocking_if_missing=False,
            message="Diagnostics can use final audit outputs.",
        ),
        StagePrerequisite(
            stage_name=PipelineStage.DIAGNOSE_RUN,
            required_prior_stage=PipelineStage.REPLAY_VERIFY,
            required_artifact_path_or_kind=(
                "runs/<run_id>/replay/replay-verification-report.json"
            ),
            required_report="replay-verification-report",
            blocking_if_missing=False,
            message="Diagnostics can use replay outputs.",
        ),
    ),
}


def get_stage_output_paths(stage_name: str | PipelineStage, run_id: str) -> list[str]:
    """Return relative completion artifacts for a stage and run."""
    stage = PipelineStage(stage_name)
    return [f"runs/{run_id}/{path}" for path in _STAGE_OUTPUTS.get(stage, ())]


def get_stage_prerequisites(stage_name: str | PipelineStage) -> list[StagePrerequisite]:
    """Return deterministic prerequisites for a pipeline stage."""
    stage = PipelineStage(stage_name)
    return list(_PREREQUISITES[stage])


def stage_is_optional_checkpoint(stage: PipelineStage) -> bool:
    """Return whether missing stage output should not block normal completeness."""
    return stage in OPTIONAL_CHECKPOINT_STAGES


def inspect_stage_checkpoint(
    run_id: str,
    stage_name: str | PipelineStage,
    root: str | Path = ".",
) -> StageCheckpoint:
    """Inspect one stage's completion from disk without mutating provenance."""
    root_path = Path(root)
    stage = PipelineStage(stage_name)
    if stage == PipelineStage.REPLAY_VERIFY:
        return _special_report_checkpoint(
            run_id=run_id,
            stage=stage,
            root=root_path,
            output_descriptions=_REPLAY_OUTPUTS,
            report_path=root_path / "runs" / run_id / "replay" / "replay-verification-report.json",
            pipeline_attribute="replay_status",
        )
    if stage == PipelineStage.DIAGNOSE_RUN:
        return _special_report_checkpoint(
            run_id=run_id,
            stage=stage,
            root=root_path,
            output_descriptions=_DIAGNOSTIC_OUTPUTS,
            report_path=root_path / "runs" / run_id / "diagnostics" / "diagnostic-report.json",
            pipeline_attribute="diagnostic_status",
        )

    relative_paths = get_stage_output_paths(stage, run_id)
    present = [path for path in relative_paths if (root_path / path).is_file()]
    missing = [path for path in relative_paths if not (root_path / path).is_file()]
    return StageCheckpoint(
        stage_name=stage,
        completed=bool(relative_paths) and not missing,
        required_artifacts_present=present,
        required_artifacts_missing=missing,
        completion_evidence=present,
        optional=stage_is_optional_checkpoint(stage),
    )


def inspect_all_stage_checkpoints(
    run_id: str,
    root: str | Path = ".",
    *,
    include_optional: bool = True,
) -> list[StageCheckpoint]:
    """Inspect all known stage checkpoints in canonical order."""
    checkpoints: list[StageCheckpoint] = []
    for stage in PIPELINE_STAGE_ORDER:
        if not include_optional and stage_is_optional_checkpoint(stage):
            continue
        checkpoints.append(inspect_stage_checkpoint(run_id, stage, root))
    return checkpoints


def materialize_prerequisite_path(prerequisite: StagePrerequisite, run_id: str) -> str:
    """Replace the stable run placeholder in a prerequisite path."""
    return prerequisite.required_artifact_path_or_kind.replace("<run_id>", run_id)


def prerequisite_exists(
    run_id: str,
    prerequisite: StagePrerequisite,
    root: str | Path = ".",
) -> bool:
    """Return whether a prerequisite artifact exists on disk."""
    path = materialize_prerequisite_path(prerequisite, run_id)
    return (Path(root) / path).is_file()


def ledger_path(root: str | Path, run_id: str) -> Path:
    """Return the canonical ledger path for read-only inspection."""
    return Path(root) / "runs" / run_id / LEDGER_FILENAME


def load_pipeline_report(root: str | Path, run_id: str) -> PipelineRunReport | None:
    """Load a ledgered pipeline report if one exists and is readable."""
    path = Path(root) / "runs" / run_id / "reports" / "pipeline-run-report.json"
    if not path.is_file():
        return None
    return PipelineRunReport.model_validate_json(path.read_text(encoding="utf-8"))


def load_replay_report(root: str | Path, run_id: str) -> ReplayVerificationReport | None:
    """Load an optional non-provenance replay report if present."""
    path = Path(root) / "runs" / run_id / "replay" / "replay-verification-report.json"
    if not path.is_file():
        return None
    return ReplayVerificationReport.model_validate_json(path.read_text(encoding="utf-8"))


def load_diagnostic_report(root: str | Path, run_id: str) -> DiagnosticReport | None:
    """Load an optional non-provenance diagnostic report if present."""
    path = Path(root) / "runs" / run_id / "diagnostics" / "diagnostic-report.json"
    if not path.is_file():
        return None
    return DiagnosticReport.model_validate_json(path.read_text(encoding="utf-8"))


def _special_report_checkpoint(
    *,
    run_id: str,
    stage: PipelineStage,
    root: Path,
    output_descriptions: tuple[str, ...],
    report_path: Path,
    pipeline_attribute: str,
) -> StageCheckpoint:
    present: list[str] = []
    warnings: list[str] = []
    if report_path.is_file():
        present.append(report_path.relative_to(root).as_posix())
    try:
        pipeline_report = load_pipeline_report(root, run_id)
    except ValueError as exc:
        pipeline_report = None
        warnings.append(f"pipeline report could not be parsed: {exc}")
    if pipeline_report is not None and getattr(pipeline_report, pipeline_attribute) is not None:
        present.append(f"runs/{run_id}/reports/pipeline-run-report.json")
    missing = [] if present else [f"runs/{run_id}/{item}" for item in output_descriptions]
    return StageCheckpoint(
        stage_name=stage,
        completed=bool(present),
        required_artifacts_present=sorted(set(present)),
        required_artifacts_missing=missing,
        completion_evidence=sorted(set(present)),
        optional=stage_is_optional_checkpoint(stage),
        warnings=warnings,
    )
