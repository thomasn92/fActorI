"""Explicit expected-output tables for read-only pipeline dry-runs."""

from __future__ import annotations

from factori.checkpoints import get_stage_output_paths
from factori.pipeline import PIPELINE_STAGE_ORDER, stage_is_read_only
from factori.schemas import PipelineStage, PlannedOutput

_OUTPUT_KIND_BY_PATH = {
    "stage-a-report.md": "stage_a_report",
    "stage-b-report.md": "stage_b_report",
    "budget-selection.json": "budget_selection",
    "stage-c-selection-report.md": "stage_c_selection_report",
    "stage-c-verification-report.md": "stage_c_verification_report",
    "final-nucleus.json": "final_nucleus",
    "abstract-synthesis-report.md": "abstract_synthesis_report",
    "claim-table.json": "claim_table",
    "blocked-claims.json": "blocked_claims",
    "manuscript-plan.json": "manuscript_plan",
    "manuscript-plan.md": "manuscript_plan_report",
    "draft-skeleton.json": "draft_skeleton",
    "draft-skeleton.md": "draft_skeleton_report",
    "manuscript-checklist.json": "manuscript_checklist",
    "manuscript-checklist.md": "manuscript_checklist_report",
    "research-object.json": "research_object",
    "research-object.md": "research_object_report",
    "artifact-manifest.json": "artifact_manifest",
    "ledger-summary.json": "ledger_summary",
    "branch-outcomes.json": "branch_outcomes",
    "reproducibility-manifest.json": "reproducibility_manifest",
    "paper-skeleton.json": "paper_skeleton",
    "paper-skeleton.md": "paper_skeleton_report",
    "paper-assembly-report.json": "paper_assembly_report",
    "final-audit-report.json": "final_audit_report",
    "final-audit-report.md": "final_audit_report_markdown",
    "release-gate-decision.json": "release_gate_decision",
    "release-gate-decision.md": "release_gate_decision_markdown",
    "prose-generation-contract.json": "prose_generation_contract",
    "latex-export-plan.json": "latex_export_plan",
    "export-section-map.json": "export_section_map",
    "export-claim-map.json": "export_claim_map",
    "export-readiness-report.json": "export_readiness_report",
    "export-readiness-report.md": "export_readiness_report_markdown",
    "export-bundle-manifest.json": "export_bundle_manifest",
}


def expected_outputs_for_stage(
    stage: PipelineStage,
    run_id: str,
    *,
    write_replay_report: bool = False,
    write_diagnostic_report: bool = False,
) -> list[PlannedOutput]:
    """Return deterministic expected outputs for one planned stage."""
    if stage == PipelineStage.REPLAY_VERIFY:
        if not write_replay_report:
            return [
                PlannedOutput(
                    output_kind="replay_status",
                    path=None,
                    required_for_completion=False,
                    optional=True,
                    description="Read-only replay status returned in memory/stdout.",
                )
            ]
        return [
            PlannedOutput(
                output_kind="replay_verification_report",
                path=f"runs/{run_id}/replay/replay-verification-report.json",
                required_for_completion=False,
                optional=True,
                description="Optional non-provenance replay JSON report.",
            ),
            PlannedOutput(
                output_kind="replay_verification_report_markdown",
                path=f"runs/{run_id}/replay/replay-verification-report.md",
                required_for_completion=False,
                optional=True,
                description="Optional non-provenance replay Markdown report.",
            ),
        ]
    if stage == PipelineStage.DIAGNOSE_RUN:
        if not write_diagnostic_report:
            return [
                PlannedOutput(
                    output_kind="diagnostic_status",
                    path=None,
                    required_for_completion=False,
                    optional=True,
                    description="Read-only diagnostic status returned in memory/stdout.",
                )
            ]
        return [
            PlannedOutput(
                output_kind="diagnostic_report",
                path=f"runs/{run_id}/diagnostics/diagnostic-report.json",
                required_for_completion=False,
                optional=True,
                description="Optional non-provenance diagnostic JSON report.",
            ),
            PlannedOutput(
                output_kind="diagnostic_report_markdown",
                path=f"runs/{run_id}/diagnostics/diagnostic-report.md",
                required_for_completion=False,
                optional=True,
                description="Optional non-provenance diagnostic Markdown report.",
            ),
        ]

    outputs: list[PlannedOutput] = []
    for path in get_stage_output_paths(stage, run_id):
        filename = path.rsplit("/", maxsplit=1)[-1]
        outputs.append(
            PlannedOutput(
                output_kind=_OUTPUT_KIND_BY_PATH.get(filename, filename.replace("-", "_")),
                path=path,
                required_for_completion=True,
                optional=False,
                description=f"Expected {stage.value} output.",
            )
        )
    return outputs


def expected_pipeline_report_outputs(run_id: str) -> list[PlannedOutput]:
    """Return the run-all pipeline report outputs."""
    return [
        PlannedOutput(
            output_kind="pipeline_run_report",
            path=f"runs/{run_id}/reports/pipeline-run-report.json",
            required_for_completion=False,
            optional=False,
            description="Ledgered pipeline run JSON report written by run-all.",
        ),
        PlannedOutput(
            output_kind="pipeline_run_report_markdown",
            path=f"runs/{run_id}/reports/pipeline-run-report.md",
            required_for_completion=False,
            optional=False,
            description="Ledgered pipeline run Markdown report written by run-all.",
        ),
    ]


def all_supported_stage_names() -> list[str]:
    """Return supported stage names in canonical run-all order."""
    return [stage.value for stage in PIPELINE_STAGE_ORDER]


def planned_stage_is_read_only(stage_name: str) -> bool:
    """Return whether a planned stage name is a known read-only stage."""
    try:
        return stage_is_read_only(PipelineStage(stage_name))
    except ValueError:
        return False
