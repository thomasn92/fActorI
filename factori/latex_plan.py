"""Deterministic LaTeX export plans without generating LaTeX."""

from __future__ import annotations

from factori.schemas import LatexExportPlan, PaperSkeleton, ProseGenerationContract

FORBIDDEN_LATEX_COMMANDS = [
    "\\write18",
    "\\input from external absolute paths",
    "\\include from external absolute paths",
    "shell escape dependent commands",
]

LATEX_SAFETY_WARNINGS = [
    "This plan is not LaTeX source.",
    "Do not enable shell escape for future exports.",
    "Do not import external absolute paths.",
    "Preserve claim labels and evidence placeholders in any future LaTeX layer.",
]


def build_latex_export_plan(
    run_id: str,
    paper_skeleton: PaperSkeleton,
    prose_contract: ProseGenerationContract,
) -> LatexExportPlan:
    """Build a deterministic plan for future LaTeX export."""
    evidence_placeholder_ids = sorted(
        {
            f"evidence-{artifact_id}"
            for placeholder in paper_skeleton.claim_placeholders
            for artifact_id in placeholder.evidence_artifact_ids
        }
    )
    return LatexExportPlan(
        run_id=run_id,
        target_template_name="factori-mvp-paper-skeleton",
        section_order=[section.title for section in paper_skeleton.sections],
        section_ids=[section.section_id for section in paper_skeleton.sections],
        claim_placeholder_ids=[
            placeholder.claim_id for placeholder in paper_skeleton.claim_placeholders
        ],
        evidence_placeholder_ids=evidence_placeholder_ids,
        appendix_order=[appendix.title for appendix in paper_skeleton.appendices],
        bibliography_placeholder_policy=(
            "Use deterministic evidence placeholders only; no bibliography is generated."
        ),
        figure_placeholder_policy="No figures are generated; preserve future figure placeholders.",
        table_placeholder_policy="No tables are generated; preserve future table placeholders.",
        forbidden_latex_commands=FORBIDDEN_LATEX_COMMANDS,
        latex_safety_warnings=LATEX_SAFETY_WARNINGS,
        ready_for_latex_export=(
            prose_contract.ready_for_polished_prose and bool(paper_skeleton.sections)
        ),
    )
