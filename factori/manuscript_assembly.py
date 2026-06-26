"""Deterministic assembly of safe section drafts into a Markdown manuscript."""

from __future__ import annotations

from collections.abc import Iterable

from factori.schemas import (
    ClaimTable,
    CompleteMarkdownDraft,
    ManuscriptAssemblyReport,
    ManuscriptDraftStatus,
    ManuscriptPlan,
    NarrativeManuscriptContract,
    NarrativeSectionRole,
    PaperShapeCritique,
    SectionDraftingResult,
)

CANONICAL_MAIN_SECTIONS = [
    "Abstract",
    "Introduction",
    "Model and Preliminary Results",
    "Main Result and Derivatives",
    "Numerical Study",
    "Empirical Results and Discussion",
    "Limitations",
    "Conclusion",
    "Appendix",
]


def assemble_complete_markdown_draft(
    *,
    run_id: str,
    manuscript_plan: ManuscriptPlan,
    narrative_contract: NarrativeManuscriptContract,
    paper_shape_critique: PaperShapeCritique,
    claim_table: ClaimTable,
    section_results: list[SectionDraftingResult],
) -> tuple[CompleteMarkdownDraft, ManuscriptAssemblyReport]:
    """Assemble a full Markdown manuscript from safety-checked section drafts."""
    results_by_id = {result.section_id: result for result in section_results}
    safe_results = [result for result in section_results if result.safe and not result.rejected]
    unsafe_results = [result for result in section_results if not result.safe or result.rejected]
    unsafe_ids = sorted(result.section_id for result in unsafe_results)
    warnings = sorted(
        set(
            [
                *paper_shape_critique.warnings,
                *[
                    warning
                    for result in section_results
                    for warning in result.warnings
                ],
                *[
                    reason
                    for result in unsafe_results
                    for reason in result.safety_reasons
                ],
            ]
        )
    )
    if not _has_empirical_evidence(claim_table):
        warnings.append(
            "Empirical results are unavailable in this MVP draft; no real-world "
            "validation is claimed."
        )

    claim_appendix = _claim_evidence_appendix(claim_table)
    provenance_appendix = _provenance_appendix(
        run_id=run_id,
        manuscript_plan=manuscript_plan,
        narrative_contract=narrative_contract,
        paper_shape_critique=paper_shape_critique,
        section_results=section_results,
    )
    markdown = _render_markdown(
        run_id=run_id,
        manuscript_plan=manuscript_plan,
        narrative_contract=narrative_contract,
        claim_table=claim_table,
        results_by_id=results_by_id,
        claim_appendix=claim_appendix,
        provenance_appendix=provenance_appendix,
    )
    status = _draft_status(
        section_results=section_results,
        unsafe_section_ids=unsafe_ids,
        warnings=warnings,
    )
    complete = CompleteMarkdownDraft(
        run_id=run_id,
        title=manuscript_plan.title,
        markdown=markdown,
        section_ids=[section.section_id for section in manuscript_plan.sections],
        unsafe_section_ids=unsafe_ids,
        claim_evidence_appendix=claim_appendix,
        provenance_appendix=provenance_appendix,
        warnings=warnings,
    )
    report = ManuscriptAssemblyReport(
        run_id=run_id,
        assembled_sections=len(safe_results),
        omitted_sections=unsafe_ids,
        unsafe_section_ids=unsafe_ids,
        warnings=warnings,
        draft_status=status,
        complete_markdown_artifact_id="complete-manuscript-draft",
    )
    return complete, report


def _render_markdown(
    *,
    run_id: str,
    manuscript_plan: ManuscriptPlan,
    narrative_contract: NarrativeManuscriptContract,
    claim_table: ClaimTable,
    results_by_id: dict[str, SectionDraftingResult],
    claim_appendix: str,
    provenance_appendix: str,
) -> str:
    buckets = _bucket_safe_results(manuscript_plan.sections, results_by_id)
    lines = [
        f"# {manuscript_plan.title}",
        "",
        "> Deterministic Markdown manuscript draft. This presentation artifact is not "
        "proof evidence, experiment evidence, retrieval evidence, human approval, or "
        "scientific validation.",
        "",
        "## Central Message",
        "",
        narrative_contract.central_message or "Central message unavailable in the contract.",
        "",
    ]
    for heading in CANONICAL_MAIN_SECTIONS:
        lines.extend([f"## {heading}", ""])
        if heading == "Empirical Results and Discussion" and not _has_empirical_evidence(
            claim_table
        ):
            lines.extend(
                [
                    "Empirical results are unavailable and out of scope for this MVP run. "
                    "No real-world validation is claimed.",
                    "",
                ]
            )
            continue
        section_texts = buckets.get(heading, [])
        if section_texts:
            for text in section_texts:
                lines.extend([text, ""])
        else:
            lines.extend([_unavailable_text(heading), ""])

    lines.extend(
        [
            "## Claim/Evidence Appendix",
            "",
            claim_appendix,
            "",
            "## Provenance Appendix",
            "",
            provenance_appendix,
            "",
            "## Draft Invariants",
            "",
            f"- Run: `{run_id}`",
            "- Markdown drafting cannot create or upgrade scientific labels.",
            "- Markdown drafting cannot create proof, experiment, retrieval, or "
            "human-review evidence.",
            "- Citations and empirical results are not invented by this draft engine.",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def _bucket_safe_results(
    sections: Iterable,
    results_by_id: dict[str, SectionDraftingResult],
) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {heading: [] for heading in CANONICAL_MAIN_SECTIONS}
    for section in sections:
        result = results_by_id.get(section.section_id)
        if result is None:
            continue
        heading = _canonical_heading(section.title, section.narrative_roles)
        if heading == "Title":
            continue
        if result.safe and not result.rejected:
            buckets.setdefault(heading, []).append(result.draft_markdown)
        else:
            buckets.setdefault(heading, []).append(
                "[UNSAFE SECTION OMITTED] "
                + "; ".join(result.safety_reasons or ["section failed safety checks"])
            )
    return buckets


def _canonical_heading(title: str, roles: list[NarrativeSectionRole]) -> str:
    lowered = title.lower()
    role_set = set(roles)
    if lowered == "title":
        return "Title"
    if "abstract" in lowered:
        return "Abstract"
    if "introduction" in lowered:
        return "Introduction"
    if (
        NarrativeSectionRole.MODEL_FRAME in role_set
        or "model" in lowered
        or "setup" in lowered
        or "method" in lowered
    ):
        return "Model and Preliminary Results"
    if "synthetic" in lowered or "numerical" in lowered:
        return "Numerical Study"
    if NarrativeSectionRole.MAIN_BODY_RESULT in role_set or "result" in lowered:
        return "Main Result and Derivatives"
    if "empirical" in lowered or "discussion" in lowered:
        return "Empirical Results and Discussion"
    if "limitation" in lowered or "negative" in lowered or "boundary" in lowered:
        return "Limitations"
    if "conclusion" in lowered:
        return "Conclusion"
    if "appendix" in lowered:
        return "Appendix"
    return "Appendix"


def _claim_evidence_appendix(claim_table: ClaimTable) -> str:
    if not claim_table.claims:
        return "- No claims are present in the claim table."
    lines = []
    for claim in sorted(claim_table.claims, key=lambda item: item.claim_id):
        evidence = ", ".join(claim.evidence_artifact_ids) or "none"
        lines.append(
            f"- `{claim.claim_id}` ({claim.claim_label.value}): evidence artifacts: {evidence}"
        )
    return "\n".join(lines)


def _provenance_appendix(
    *,
    run_id: str,
    manuscript_plan: ManuscriptPlan,
    narrative_contract: NarrativeManuscriptContract,
    paper_shape_critique: PaperShapeCritique,
    section_results: list[SectionDraftingResult],
) -> str:
    lines = [
        f"- Run ID: `{run_id}`",
        f"- Manuscript plan: `{manuscript_plan.plan_id}`",
        f"- Narrative contract: `{narrative_contract.contract_id}`",
        f"- Paper-shape critique: `{paper_shape_critique.critique_id}`",
        f"- Drafted sections: {len(section_results)}",
        "- Draft artifacts are presentation/context only.",
    ]
    return "\n".join(lines)


def _unavailable_text(heading: str) -> str:
    return (
        f"[{heading} placeholder] No safety-accepted section draft was available for "
        "this role. The engine does not invent manuscript content."
    )


def _has_empirical_evidence(claim_table: ClaimTable) -> bool:
    return any(
        "real" in evidence_type.lower()
        for claim in claim_table.claims
        for evidence_type in claim.evidence_types
    )


def _draft_status(
    *,
    section_results: list[SectionDraftingResult],
    unsafe_section_ids: list[str],
    warnings: list[str],
) -> ManuscriptDraftStatus:
    if not section_results:
        return ManuscriptDraftStatus.DRAFT_FAILED
    if unsafe_section_ids:
        return ManuscriptDraftStatus.DRAFT_INCOMPLETE_UNSAFE_SECTIONS
    if warnings:
        return ManuscriptDraftStatus.DRAFT_COMPLETE_WITH_WARNINGS
    return ManuscriptDraftStatus.DRAFT_COMPLETE


__all__ = [
    "CANONICAL_MAIN_SECTIONS",
    "assemble_complete_markdown_draft",
]
