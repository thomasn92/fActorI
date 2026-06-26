"""Deterministic Markdown-to-LaTeX export with source maps."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.citations import CITATION_MARKER_RE
from factori.hashing import sha256_json
from factori.latex_render import (
    LatexRenderer,
    build_latex_compile_check_report,
)
from factori.latex_safety import validate_latex_export
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    CitationRegistry,
    ControllerActionType,
    LatexCompileCheckReport,
    LatexExportContract,
    LatexExportResult,
    LatexRenderConfig,
    LatexRenderResult,
    LatexSourceMap,
    LatexSourceMapEntry,
    ManuscriptDraftingPlan,
    ManuscriptDraftingReport,
    NarrativeSectionRole,
    VerificationLabel,
)

DEFAULT_LATEX_PACKAGES = ["amsmath", "amssymb", "hyperref", "url"]
SOURCE_MAP_POLICY = (
    "Each generated LaTeX section is mapped to manuscript section IDs, allowed "
    "claim IDs, evidence artifact IDs, citation keys, and source contract hashes."
)
LATEX_PRESENTATION_WARNING = (
    "LaTeX export is presentation/context only and cannot create proof, experiment, "
    "retrieval, human-review, publication-readiness, or scientific-validation evidence."
)

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_NUMBERED_RE = re.compile(r"^\d+\.\s+(.+)$")
_INLINE_MATH_RE = re.compile(r"(\$[^$\n]+\$)")


class LatexExportError(RuntimeError):
    """Raised when LaTeX export prerequisites or gates fail."""


@dataclass(frozen=True)
class LatexExportInputs:
    """Disk-loaded inputs for LaTeX export."""

    draft_markdown: str
    draft_artifact: ArtifactRef
    drafting_plan: ManuscriptDraftingPlan
    drafting_report: ManuscriptDraftingReport
    citation_registry: CitationRegistry | None = None
    citation_registry_artifact: ArtifactRef | None = None


@dataclass(frozen=True)
class LatexExportRunResult:
    """Full LaTeX export result plus optional persisted artifact references."""

    run_id: str
    inputs: LatexExportInputs
    export_result: LatexExportResult
    paper_artifact: ArtifactRef | None = None
    bibliography_artifact: ArtifactRef | None = None
    source_map_artifact: ArtifactRef | None = None
    export_report_artifact: ArtifactRef | None = None
    safety_report_artifact: ArtifactRef | None = None
    compile_check_artifact: ArtifactRef | None = None
    commit_hash: str | None = None


@dataclass(frozen=True)
class _LatexConversion:
    paper_tex: str
    section_latex_ranges: dict[str, list[int]]
    section_markdown_ranges: dict[str, list[int]]


def load_latex_export_inputs(
    run_id: str,
    *,
    root: str | Path,
    ledger: ResearchLedger,
) -> LatexExportInputs:
    """Load a persisted Markdown manuscript draft and optional citation registry."""
    commits = ledger.list_commits(run_id)
    draft_commit = _latest_commit(commits, ControllerActionType.MANUSCRIPT_DRAFT_WRITTEN)
    if draft_commit is None:
        raise LatexExportError(
            "Complete Markdown manuscript draft not found; run factori "
            "draft-manuscript --write-report first"
        )
    refs = {artifact.id: artifact for artifact in draft_commit.artifact_refs}
    try:
        draft_ref = refs["complete-manuscript-draft"]
        plan_ref = refs["manuscript-drafting-plan"]
        report_ref = refs["manuscript-drafting-report"]
    except KeyError as exc:
        raise LatexExportError(
            "Manuscript draft artifacts are incomplete; rerun factori "
            "draft-manuscript --write-report"
        ) from exc

    root_path = Path(root)
    draft_markdown = _read_text_artifact(root_path, draft_ref)
    drafting_plan = ManuscriptDraftingPlan.model_validate_json(
        _read_text_artifact(root_path, plan_ref)
    )
    drafting_report = ManuscriptDraftingReport.model_validate_json(
        _read_text_artifact(root_path, report_ref)
    )
    citation_ref = refs.get("citation-registry")
    if citation_ref is None:
        citation_commit = _latest_commit(commits, ControllerActionType.CITATION_REGISTRY_WRITTEN)
        if citation_commit is not None:
            citation_ref = {
                artifact.id: artifact for artifact in citation_commit.artifact_refs
            }.get("citation-registry")
    citation_registry = (
        CitationRegistry.model_validate_json(_read_text_artifact(root_path, citation_ref))
        if citation_ref is not None
        else None
    )
    return LatexExportInputs(
        draft_markdown=draft_markdown,
        draft_artifact=draft_ref,
        drafting_plan=drafting_plan,
        drafting_report=drafting_report,
        citation_registry=citation_registry,
        citation_registry_artifact=citation_ref,
    )


def build_latex_export_contract(
    *,
    run_id: str,
    manuscript_draft_artifact_id: str,
    drafting_plan: ManuscriptDraftingPlan,
    drafting_report: ManuscriptDraftingReport,
    citation_registry: CitationRegistry | None = None,
    citation_registry_artifact_id: str | None = None,
    render_check_enabled: bool = False,
    document_class: str = "article",
    bibliography_style: str = "plain",
) -> LatexExportContract:
    """Build a deterministic contract for LaTeX export."""
    allowed_claim_ids = sorted(
        {
            claim_id
            for task in drafting_plan.tasks
            for claim_id in task.allowed_claim_ids
        }
        | {
            claim_id
            for summary in drafting_report.section_summaries
            for claim_id in summary.used_claim_ids
        }
    )
    allowed_evidence = sorted(
        {
            artifact_id
            for task in drafting_plan.tasks
            for artifact_id in task.allowed_evidence_artifact_ids
        }
        | {
            artifact_id
            for summary in drafting_report.section_summaries
            for artifact_id in summary.used_evidence_artifact_ids
        }
    )
    allowed_citations = sorted(
        {
            key
            for task in drafting_plan.tasks
            for key in task.allowed_citation_keys
        }
        | {
            key
            for summary in drafting_report.section_summaries
            for key in summary.used_citation_keys
        }
        | (
            {record.citation_key for record in citation_registry.citations}
            if citation_registry is not None
            else set()
        )
    )
    return LatexExportContract(
        run_id=run_id,
        manuscript_draft_artifact_id=manuscript_draft_artifact_id,
        citation_registry_artifact_id=citation_registry_artifact_id,
        bibliography_style=bibliography_style,
        document_class=document_class,
        packages=DEFAULT_LATEX_PACKAGES,
        section_order=[
            heading
            for heading in _unique_in_order(
                _canonical_heading(task.section_title, task.narrative_role)
                for task in drafting_plan.tasks
            )
            if heading != "Title"
        ],
        source_map_policy=SOURCE_MAP_POLICY,
        allowed_citation_keys=allowed_citations,
        allowed_claim_ids=allowed_claim_ids,
        allowed_evidence_artifact_ids=allowed_evidence,
        forbidden_labels=[VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED],
        render_check_enabled=render_check_enabled,
    )


def export_markdown_draft_to_latex(
    *,
    run_id: str,
    draft_markdown: str,
    contract: LatexExportContract,
    drafting_plan: ManuscriptDraftingPlan,
    drafting_report: ManuscriptDraftingReport,
    citation_registry: CitationRegistry | None = None,
    render_result: LatexRenderResult | None = None,
    compile_check_report: LatexCompileCheckReport | None = None,
) -> LatexExportResult:
    """Convert controlled Markdown to LaTeX and validate the export."""
    conversion = _markdown_to_latex(
        markdown=draft_markdown,
        contract=contract,
        title=_title_from_markdown(draft_markdown),
    )
    source_map = _build_source_map(
        run_id=run_id,
        conversion=conversion,
        contract=contract,
        drafting_plan=drafting_plan,
        drafting_report=drafting_report,
        draft_markdown=draft_markdown,
    )
    references_bib, bib_warnings = build_references_bib(citation_registry)
    safety = validate_latex_export(
        contract=contract,
        paper_tex=conversion.paper_tex,
        source_map=source_map,
        citation_registry=citation_registry,
    )
    warnings = sorted({LATEX_PRESENTATION_WARNING, *bib_warnings, *safety.warnings})
    return LatexExportResult(
        run_id=run_id,
        contract=contract,
        paper_tex=conversion.paper_tex,
        references_bib=references_bib,
        source_map=source_map,
        safety_report=safety,
        render_result=render_result,
        compile_check_report=compile_check_report,
        warnings=warnings,
    )


def export_latex_from_run(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    root: str | Path,
    write_report: bool = False,
    render_check: bool = False,
    allow_external_tools: bool = False,
    latex_executable: str | None = None,
    renderer: LatexRenderer | None = None,
) -> LatexExportRunResult:
    """Load a Markdown draft, export LaTeX, optionally render/check, and optionally persist."""
    inputs = load_latex_export_inputs(run_id, root=root, ledger=ledger)
    contract = build_latex_export_contract(
        run_id=run_id,
        manuscript_draft_artifact_id=inputs.draft_artifact.id,
        drafting_plan=inputs.drafting_plan,
        drafting_report=inputs.drafting_report,
        citation_registry=inputs.citation_registry,
        citation_registry_artifact_id=(
            inputs.citation_registry_artifact.id
            if inputs.citation_registry_artifact is not None
            else None
        ),
        render_check_enabled=render_check,
    )
    base_result = export_markdown_draft_to_latex(
        run_id=run_id,
        draft_markdown=inputs.draft_markdown,
        contract=contract,
        drafting_plan=inputs.drafting_plan,
        drafting_report=inputs.drafting_report,
        citation_registry=inputs.citation_registry,
    )
    render_result = None
    compile_check = None
    if render_check:
        config = LatexRenderConfig(
            run_id=run_id,
            render_check_enabled=True,
            allow_external_tools=allow_external_tools,
            latex_executable=latex_executable,
        )
        render_result = (renderer or LatexRenderer()).render(base_result.paper_tex, config)
        compile_check = build_latex_compile_check_report(
            config=config,
            render_result=render_result,
        )
        base_result = base_result.model_copy(
            update={
                "render_result": render_result,
                "compile_check_report": compile_check,
                "warnings": sorted(
                    {
                        *base_result.warnings,
                        *(compile_check.warnings if compile_check is not None else []),
                    }
                ),
            }
        )
    run_result = LatexExportRunResult(
        run_id=run_id,
        inputs=inputs,
        export_result=base_result,
    )
    if not write_report:
        return run_result
    return _with_persisted_artifacts(
        run_result,
        write_latex_export_artifacts(
            run_id=run_id,
            store=store,
            ledger=ledger,
            result=base_result,
        ),
    )


def write_latex_export_artifacts(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    result: LatexExportResult,
) -> PersistenceResult:
    """Persist LaTeX export artifacts as ledgered presentation/export context."""
    metadata = {
        "stage": "latex_export",
        "artifact_role": "latex_export_presentation_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }
    result = result.model_copy(
        update={
            "latex_artifact_id": "paper",
            "bibliography_artifact_id": "references",
            "source_map_artifact_id": "latex-source-map",
            "export_report_artifact_id": "latex-export-report",
            "safety_report_artifact_id": "latex-safety-report",
        }
    )
    specs = [
        ArtifactWriteSpec(
            artifact_id="paper",
            artifact_type=ArtifactType.LATEX,
            payload=result.paper_tex,
            artifact_format="latex",
            metadata={**metadata, "format": "latex"},
        ),
        ArtifactWriteSpec(
            artifact_id="references",
            artifact_type=ArtifactType.LATEX,
            payload=result.references_bib,
            artifact_format="bib",
            metadata={**metadata, "format": "bibtex"},
        ),
        ArtifactWriteSpec(
            artifact_id="latex-source-map",
            artifact_type=ArtifactType.LATEX,
            payload=result.source_map,
            artifact_format="json",
            metadata=metadata,
        ),
        ArtifactWriteSpec(
            artifact_id="latex-export-report",
            artifact_type=ArtifactType.LATEX,
            payload=result,
            artifact_format="json",
            metadata=metadata,
        ),
        ArtifactWriteSpec(
            artifact_id="latex-safety-report",
            artifact_type=ArtifactType.LATEX,
            payload=result.safety_report,
            artifact_format="json",
            metadata=metadata,
        ),
    ]
    if result.compile_check_report is not None:
        specs.append(
            ArtifactWriteSpec(
                artifact_id="latex-compile-check-report",
                artifact_type=ArtifactType.LATEX,
                payload=result.compile_check_report,
                artifact_format="json",
                metadata=metadata,
            )
        )
    return persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.LATEX_EXPORT_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "latex_artifact_id": "paper",
            "bibliography_artifact_id": "references",
            "source_map_artifact_id": "latex-source-map",
            "safety_report_artifact_id": "latex-safety-report",
            "safe": result.safety_report.safe,
            "render_check_enabled": result.contract.render_check_enabled,
            "render_passed": (
                result.render_result.passed if result.render_result is not None else None
            ),
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
        },
    )


def build_references_bib(
    citation_registry: CitationRegistry | None,
) -> tuple[str, list[str]]:
    """Generate deterministic BibTeX placeholders only from CitationRecords."""
    if citation_registry is None or not citation_registry.citations:
        return "% No retrieval-backed citations available for this draft.\n", []
    warnings = []
    entries = []
    for record in sorted(citation_registry.citations, key=lambda item: item.citation_key):
        fields = [
            f"  title = {{{_bib_escape(record.title)}}}",
            f"  note = {{Source: {record.source_id}; provider: {record.provider}}}",
        ]
        if record.authors:
            fields.append(f"  author = {{{_bib_escape(' and '.join(record.authors))}}}")
        else:
            warnings.append(f"{record.citation_key}: missing author metadata")
        if record.year is not None:
            fields.append(f"  year = {{{record.year}}}")
        else:
            warnings.append(f"{record.citation_key}: missing year metadata")
        if record.venue:
            fields.append(f"  journal = {{{_bib_escape(record.venue)}}}")
        if record.doi:
            fields.append(f"  doi = {{{_bib_escape(record.doi)}}}")
        if record.url:
            fields.append(f"  url = {{{_bib_escape(record.url)}}}")
        entries.append(
            "@misc{"
            + record.citation_key
            + ",\n"
            + ",\n".join(fields)
            + "\n}"
        )
    return "\n\n".join(entries) + "\n", sorted(warnings)


def _markdown_to_latex(
    *,
    markdown: str,
    contract: LatexExportContract,
    title: str,
) -> _LatexConversion:
    lines = markdown.splitlines()
    section_markdown_ranges = _markdown_section_ranges(lines)
    latex_lines: list[str] = [
        f"\\documentclass{{{contract.document_class}}}",
        *[f"\\usepackage{{{package}}}" for package in contract.packages],
        f"\\bibliographystyle{{{contract.bibliography_style}}}",
        f"\\title{{{_convert_inline(title)}}}",
        "\\begin{document}",
        "\\maketitle",
        "",
    ]
    section_latex_starts: dict[str, int] = {}
    appendix_started = False
    i = 0
    while i < len(lines):
        raw = lines[i]
        stripped = raw.strip()
        heading = _HEADING_RE.match(stripped)
        if heading is not None:
            level = len(heading.group(1))
            heading_title = heading.group(2).strip()
            if level == 1:
                i += 1
                continue
            if _is_appendix_heading(heading_title) and not appendix_started:
                latex_lines.append("\\appendix")
                appendix_started = True
            command = "section" if level == 2 else "subsection" if level == 3 else "subsubsection"
            section_latex_starts.setdefault(heading_title, len(latex_lines) + 1)
            latex_lines.append(f"\\{command}{{{_convert_inline(heading_title)}}}")
            latex_lines.append("")
            i += 1
            continue
        if stripped.startswith("```"):
            block: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                block.append(lines[i])
                i += 1
            i += 1 if i < len(lines) else 0
            latex_lines.extend(["\\begin{verbatim}", *block, "\\end{verbatim}", ""])
            continue
        if stripped.startswith("$$") and stripped.endswith("$$") and len(stripped) > 4:
            latex_lines.extend(["\\[", stripped[2:-2].strip(), "\\]", ""])
            i += 1
            continue
        if stripped == "$$":
            block = []
            i += 1
            while i < len(lines) and lines[i].strip() != "$$":
                block.append(lines[i])
                i += 1
            i += 1 if i < len(lines) else 0
            latex_lines.extend(["\\[", *block, "\\]", ""])
            continue
        if stripped.startswith("- "):
            items = []
            while i < len(lines) and lines[i].strip().startswith("- "):
                items.append(lines[i].strip()[2:])
                i += 1
            latex_lines.append("\\begin{itemize}")
            latex_lines.extend(f"\\item {_convert_inline(item)}" for item in items)
            latex_lines.extend(["\\end{itemize}", ""])
            continue
        numbered = _NUMBERED_RE.match(stripped)
        if numbered is not None:
            items = []
            while i < len(lines):
                match = _NUMBERED_RE.match(lines[i].strip())
                if match is None:
                    break
                items.append(match.group(1))
                i += 1
            latex_lines.append("\\begin{enumerate}")
            latex_lines.extend(f"\\item {_convert_inline(item)}" for item in items)
            latex_lines.extend(["\\end{enumerate}", ""])
            continue
        if stripped:
            paragraph = [stripped]
            i += 1
            while i < len(lines) and lines[i].strip():
                if _HEADING_RE.match(lines[i].strip()) or lines[i].strip().startswith(
                    ("```", "- ")
                ):
                    break
                if lines[i].strip() == "$$" or _NUMBERED_RE.match(lines[i].strip()):
                    break
                paragraph.append(lines[i].strip())
                i += 1
            latex_lines.extend([_convert_inline(" ".join(paragraph)), ""])
            continue
        latex_lines.append("")
        i += 1

    if contract.allowed_citation_keys:
        latex_lines.extend(["\\bibliography{references}", ""])
    latex_lines.append("\\end{document}")
    section_latex_ranges = _latex_section_ranges(section_latex_starts, len(latex_lines))
    return _LatexConversion(
        paper_tex="\n".join(latex_lines).rstrip() + "\n",
        section_latex_ranges=section_latex_ranges,
        section_markdown_ranges=section_markdown_ranges,
    )


def _build_source_map(
    *,
    run_id: str,
    conversion: _LatexConversion,
    contract: LatexExportContract,
    drafting_plan: ManuscriptDraftingPlan,
    drafting_report: ManuscriptDraftingReport,
    draft_markdown: str,
) -> LatexSourceMap:
    metadata = _source_metadata_by_heading(drafting_plan, drafting_report)
    entries = []
    for index, title in enumerate(conversion.section_markdown_ranges, start=1):
        data = metadata.get(title, _fallback_source_metadata(title, draft_markdown))
        entries.append(
            LatexSourceMapEntry(
                latex_block_id=f"latex-block-{index:03d}",
                section_id=data["section_id"],
                section_title=title,
                claim_ids=sorted(data["claim_ids"]),
                evidence_artifact_ids=sorted(data["evidence_artifact_ids"]),
                citation_keys=sorted(
                    set(data["citation_keys"])
                    | set(_citation_keys_for_section(title, draft_markdown))
                ),
                markdown_line_range=conversion.section_markdown_ranges.get(title, []),
                latex_line_range=conversion.section_latex_ranges.get(title, []),
                source_contract_hashes=data["source_contract_hashes"],
            )
        )
    mapped_titles = {entry.section_title for entry in entries}
    missing = sorted(title for title in contract.section_order if title not in mapped_titles)
    return LatexSourceMap(
        run_id=run_id,
        entries=entries,
        source_map_policy=contract.source_map_policy,
        covers_all_major_sections=not missing,
        missing_sections=missing,
    )


def _source_metadata_by_heading(
    drafting_plan: ManuscriptDraftingPlan,
    drafting_report: ManuscriptDraftingReport,
) -> dict[str, dict[str, Any]]:
    summaries = {
        summary.section_id: summary for summary in drafting_report.section_summaries
    }
    by_heading: dict[str, dict[str, Any]] = {}
    for task in drafting_plan.tasks:
        heading = _canonical_heading(task.section_title, task.narrative_role)
        summary = summaries.get(task.section_id)
        data = by_heading.setdefault(
            heading,
            {
                "section_id": task.section_id,
                "claim_ids": set(),
                "evidence_artifact_ids": set(),
                "citation_keys": set(),
                "source_contract_hashes": {},
            },
        )
        data["claim_ids"].update(task.allowed_claim_ids)
        data["evidence_artifact_ids"].update(task.allowed_evidence_artifact_ids)
        data["citation_keys"].update(task.allowed_citation_keys)
        data["source_contract_hashes"].update(task.source_contract_hashes)
        if summary is not None:
            data["claim_ids"].update(summary.used_claim_ids)
            data["evidence_artifact_ids"].update(summary.used_evidence_artifact_ids)
            data["citation_keys"].update(summary.used_citation_keys)
    return by_heading


def _fallback_source_metadata(title: str, markdown: str) -> dict[str, Any]:
    source_hash = sha256_json([title, markdown])
    return {
        "section_id": _slugify(title),
        "claim_ids": set(),
        "evidence_artifact_ids": set(),
        "citation_keys": set(),
        "source_contract_hashes": {"markdown_section": source_hash},
    }


def _markdown_section_ranges(lines: list[str]) -> dict[str, list[int]]:
    headings: list[tuple[str, int]] = []
    for index, line in enumerate(lines, start=1):
        match = _HEADING_RE.match(line.strip())
        if match is not None and len(match.group(1)) == 2:
            headings.append((match.group(2).strip(), index))
    ranges: dict[str, list[int]] = {}
    for idx, (title, start) in enumerate(headings):
        end = headings[idx + 1][1] - 1 if idx + 1 < len(headings) else len(lines)
        ranges[title] = [start, end]
    return ranges


def _latex_section_ranges(starts: dict[str, int], total_lines: int) -> dict[str, list[int]]:
    ordered = sorted(starts.items(), key=lambda item: item[1])
    ranges = {}
    for index, (title, start) in enumerate(ordered):
        end = ordered[index + 1][1] - 1 if index + 1 < len(ordered) else total_lines
        ranges[title] = [start, end]
    return ranges


def _citation_keys_for_section(title: str, markdown: str) -> list[str]:
    ranges = _markdown_section_ranges(markdown.splitlines())
    line_range = ranges.get(title)
    if line_range is None:
        return []
    lines = markdown.splitlines()[line_range[0] - 1 : line_range[1]]
    return sorted(set(CITATION_MARKER_RE.findall("\n".join(lines))))


def _convert_inline(text: str) -> str:
    parts = _INLINE_MATH_RE.split(text)
    converted = []
    for part in parts:
        if not part:
            continue
        if part.startswith("$") and part.endswith("$"):
            converted.append(part)
        else:
            converted.append(_convert_non_math_inline(part))
    return "".join(converted)


def _convert_non_math_inline(text: str) -> str:
    citations: list[str] = []

    def stash_citation(match: re.Match[str]) -> str:
        citations.append(match.group(1))
        return f"@@FACTORI_CITE_{len(citations) - 1}@@"

    text = CITATION_MARKER_RE.sub(stash_citation, text)
    escaped = _escape_latex(text)
    for index, key in enumerate(citations):
        escaped = escaped.replace(
            _escape_latex(f"@@FACTORI_CITE_{index}@@"),
            f"\\cite{{{key}}}",
        )
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"\\textbf{\1}", escaped)
    escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\\emph{\1}", escaped)
    return escaped


def _escape_latex(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "%": r"\%",
        "&": r"\&",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(character, character) for character in text)


def _bib_escape(text: str) -> str:
    return text.replace("{", "").replace("}", "").replace("\n", " ").strip()


def _title_from_markdown(markdown: str) -> str:
    for line in markdown.splitlines():
        match = re.match(r"^#\s+(.+)$", line.strip())
        if match is not None:
            return match.group(1).strip()
    return "fActorI Manuscript Draft"


def _canonical_heading(title: str, roles: list[NarrativeSectionRole]) -> str:
    lowered = title.lower()
    role_set = set(roles)
    if lowered == "title":
        return "Title"
    if "abstract" in lowered:
        return "Abstract"
    if "introduction" in lowered:
        return "Introduction"
    if NarrativeSectionRole.MODEL_FRAME in role_set or "model" in lowered:
        return "Model and Preliminary Results"
    if (
        NarrativeSectionRole.MAIN_BODY_RESULT in role_set
        or NarrativeSectionRole.DERIVATIVE_COROLLARY in role_set
        or "result" in lowered
    ):
        return "Main Result and Derivatives"
    if NarrativeSectionRole.NUMERICAL_VALIDATION in role_set or "numerical" in lowered:
        return "Numerical Study"
    if NarrativeSectionRole.EMPIRICAL_DISCUSSION in role_set or "empirical" in lowered:
        return "Empirical Results and Discussion"
    if NarrativeSectionRole.LIMITATIONS_DISCUSSION in role_set or "limitation" in lowered:
        return "Limitations"
    if "conclusion" in lowered:
        return "Conclusion"
    if "appendix" in lowered:
        return "Appendix"
    return title


def _is_appendix_heading(title: str) -> bool:
    lowered = title.lower()
    return "appendix" in lowered or lowered in {"bibliography", "draft invariants"}


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug or "section"


def _unique_in_order(values) -> list[str]:
    seen = set()
    ordered = []
    for value in values:
        if value not in seen:
            seen.add(value)
            ordered.append(value)
    return ordered


def _read_text_artifact(root: Path, artifact: ArtifactRef) -> str:
    path = root / artifact.path
    if not path.is_file():
        raise LatexExportError(f"Referenced artifact is missing: {artifact.path}")
    return path.read_text(encoding="utf-8")


def _latest_commit(commits, action_type: ControllerActionType):
    for commit in reversed(commits):
        if commit.action_type == action_type:
            return commit
    return None


def _with_persisted_artifacts(
    result: LatexExportRunResult,
    persistence: PersistenceResult,
) -> LatexExportRunResult:
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    export_result = result.export_result.model_copy(
        update={
            "latex_artifact_id": "paper",
            "bibliography_artifact_id": "references",
            "source_map_artifact_id": "latex-source-map",
            "export_report_artifact_id": "latex-export-report",
            "safety_report_artifact_id": "latex-safety-report",
        }
    )
    return LatexExportRunResult(
        run_id=result.run_id,
        inputs=result.inputs,
        export_result=export_result,
        paper_artifact=by_id.get("paper"),
        bibliography_artifact=by_id.get("references"),
        source_map_artifact=by_id.get("latex-source-map"),
        export_report_artifact=by_id.get("latex-export-report"),
        safety_report_artifact=by_id.get("latex-safety-report"),
        compile_check_artifact=by_id.get("latex-compile-check-report"),
        commit_hash=persistence.commit.commit_hash,
    )


__all__ = [
    "DEFAULT_LATEX_PACKAGES",
    "LATEX_PRESENTATION_WARNING",
    "LatexExportError",
    "LatexExportInputs",
    "LatexExportRunResult",
    "SOURCE_MAP_POLICY",
    "build_latex_export_contract",
    "build_references_bib",
    "export_latex_from_run",
    "export_markdown_draft_to_latex",
    "load_latex_export_inputs",
    "write_latex_export_artifacts",
]
