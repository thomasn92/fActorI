"""End-to-end non-evidence full-paper generation orchestration."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.citations import (
    CITATION_MARKER_RE,
    build_citation_registry_from_ledger,
    validate_citation_usage,
    write_citation_registry_reports,
)
from factori.latex_export import (
    LatexExportError,
    LatexExportRunResult,
    build_latex_export_contract,
    export_latex_from_run,
    export_markdown_draft_to_latex,
    load_latex_export_inputs,
)
from factori.latex_render import LatexRenderer, LatexRenderError
from factori.ledger import ResearchLedger
from factori.literature_positioning import build_literature_positioning_report
from factori.manuscript_assembly import CANONICAL_MAIN_SECTIONS
from factori.manuscript_drafting import (
    ManuscriptDraftingError,
    ManuscriptDraftingRunResult,
    draft_manuscript,
    load_manuscript_drafting_inputs,
)
from factori.paper_critic import (
    PaperCriticError,
    PaperCriticRunResult,
    critique_paper_from_run,
)
from factori.paper_revision import PaperRevisionRunResult, revise_paper_from_run
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    CitationRegistry,
    ControllerActionType,
    FullPaperArtifactBundle,
    FullPaperGenerationConfig,
    FullPaperGenerationReport,
    FullPaperGenerationResult,
    FullPaperGenerationStatus,
    FullPaperGenerationStep,
    FullPaperGenerationStepStatus,
    FullPaperReleaseReport,
    PaperCriticReport,
    RerunPolicy,
)


class FullPaperGenerationError(RuntimeError):
    """Raised when full-paper generation is blocked or fails closed."""


class PaperBundleInspectionError(RuntimeError):
    """Raised when a generated paper bundle cannot be inspected read-only."""


_PLACEHOLDER_TITLE_PATTERNS = (
    "deterministic branch manuscript plan",
    "placeholder",
    "untitled",
    "draft",
)
_PLACEHOLDER_SECTION_PATTERNS = (
    "[fake prose draft]",
    "[fake",
    "placeholder",
    "todo",
    "tbd",
)
_GENERIC_HEADING_TITLES = frozenset(
    {
        "abstract",
        "introduction",
        "background",
        "related work",
        "model",
        "method",
        "methods",
        "results",
        "discussion",
        "limitations",
        "conclusion",
        "appendix",
        "references",
        "bibliography",
    }
)
_PROBLEM_LANGUAGE = (
    "problem framing",
    "problem statement",
    "research problem",
    "the problem",
)
_CONTRIBUTION_LANGUAGE = (
    "central contribution",
    "contribution of this draft",
    "this draft contributes",
)
_METHOD_LANGUAGE = (
    "method summary",
    "method",
    "model",
    "algorithm",
    "approach",
    "pipeline",
    "mechanically",
)
_EVIDENCE_BOUNDARY_LANGUAGE = (
    "evidence boundary",
    "evidence boundaries",
    "evidence-aware",
    "not proof evidence",
    "not verification evidence",
    "cannot create evidence",
    "does not create evidence",
    "does not provide proof",
    "does not provide empirical validation",
)
_LIMITATION_LANGUAGE = (
    "limitation",
    "not provide proof",
    "not provide empirical validation",
    "not publication readiness",
    "unavailable",
)
_PROVENANCE_LANGUAGE = (
    "provenance appendix",
    "run id",
    "artifact",
    "ledger",
    "audit context",
)
_FAKE_CITATION_PATTERNS = (
    "[@fake",
    "[@placeholder",
    "[@todo",
    "[@citation",
)
_FORBIDDEN_EMPIRICAL_PATTERNS = (
    "empirically validated",
    "provides empirical validation",
    "demonstrates empirical validation",
    "described as real-world empirical validation",
    "described as real world empirical validation",
    "real data validated",
    "field validated",
)
_UNSUPPORTED_EXTERNAL_FACT_PATTERNS = (
    "studies show",
    "prior work shows",
    "the literature shows",
    "field data show",
    "survey data show",
    "according to",
)
_MAIN_BODY_HEADING_ALIASES = {
    "abstract": "Abstract",
    "introduction": "Introduction and Problem Framing",
    "introduction and problem framing": "Introduction and Problem Framing",
    "problem framing": "Introduction and Problem Framing",
    "method": "Method and Model",
    "methods": "Method and Model",
    "method and model": "Method and Model",
    "method summary": "Method and Model",
    "model": "Method and Model",
    "central contribution": "Claim and Evidence Boundaries",
    "claim and evidence boundaries": "Claim and Evidence Boundaries",
    "main result and derivatives": "Claim and Evidence Boundaries",
    "results": "Claim and Evidence Boundaries",
    "demonstration status": "Demonstration Status",
    "limitations": "Limitations",
    "conclusion": "Conclusion",
}
_APPENDIX_HEADING_FRAGMENTS = (
    "appendix",
    "bibliography",
    "references",
)
_METADATA_HEADING_TITLES = frozenset(
    {
        "central message",
        "draft invariants",
        "source/citation status",
        "source and citation status",
        "source map notes",
    }
)


@dataclass(frozen=True)
class FullPaperGenerationRunResult:
    """Runtime result for full-paper generation orchestration."""

    run_id: str
    report: FullPaperGenerationReport
    artifact_bundle: FullPaperArtifactBundle
    drafting_result: ManuscriptDraftingRunResult | None = None
    latex_result: LatexExportRunResult | None = None
    critic_result: PaperCriticRunResult | None = None
    revision_result: PaperRevisionRunResult | None = None
    persistence: PersistenceResult | None = None
    report_artifact: ArtifactRef | None = None
    bundle_artifact: ArtifactRef | None = None
    revised_latex_artifact: ArtifactRef | None = None
    revised_references_artifact: ArtifactRef | None = None
    revised_source_map_artifact: ArtifactRef | None = None
    revised_export_report_artifact: ArtifactRef | None = None
    revised_safety_report_artifact: ArtifactRef | None = None


def generate_full_paper(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    prose_generator,
    config: FullPaperGenerationConfig,
    renderer: LatexRenderer | None = None,
    max_words: int = 260,
    enable_safe_repair: bool = False,
) -> FullPaperGenerationRunResult:
    """Generate a complete manuscript package from existing deterministic run artifacts."""
    if config.run_id != run_id:
        raise FullPaperGenerationError("full-paper generation config run_id does not match")
    if config.write_report:
        skipped = _handle_existing_generation_report(
            run_id=run_id,
            root=root,
            ledger=ledger,
            policy=config.rerun_policy,
            force=config.force,
        )
        if skipped is not None:
            return skipped

    _validate_upstream_prerequisites(run_id, ledger)
    steps: list[FullPaperGenerationStep] = []
    drafting_result = None
    latex_result = None
    critic_result = None
    revision_result = None
    revised_export = None

    draft_refs = _latest_refs(ledger, run_id, ControllerActionType.MANUSCRIPT_DRAFT_WRITTEN)
    if draft_refs:
        steps.append(
            _step(
                "draft-manuscript",
                FullPaperGenerationStepStatus.SKIPPED,
                "Existing manuscript draft artifacts were reused.",
                draft_refs,
            )
        )
    else:
        try:
            drafting_result = draft_manuscript(
                run_id=run_id,
                store=store,
                ledger=ledger,
                prose_generator=prose_generator,
                write_report=True,
                include_citations=config.include_citations,
                max_citation_sources=config.max_retrieval_sources,
                max_words=max_words,
            )
        except ManuscriptDraftingError as exc:
            raise FullPaperGenerationError(_clear_manuscript_error(str(exc))) from exc
        steps.append(
            _step(
                "draft-manuscript",
                _status_from_warnings(drafting_result.drafting_report.warnings),
                "Manuscript draft artifacts were generated.",
                _refs_from_drafting_result(drafting_result),
                drafting_result.drafting_report.warnings,
            )
        )

    citation_step = _ensure_citation_artifacts(
        run_id=run_id,
        store=store,
        ledger=ledger,
        include_citations=config.include_citations,
        max_retrieval_sources=config.max_retrieval_sources,
    )
    steps.insert(0, citation_step)

    if config.export_latex:
        latex_refs = _latest_refs(ledger, run_id, ControllerActionType.LATEX_EXPORT_WRITTEN)
        if latex_refs and not config.render_check:
            steps.append(
                _step(
                    "export-latex",
                    FullPaperGenerationStepStatus.SKIPPED,
                    "Existing LaTeX export artifacts were reused.",
                    latex_refs,
                )
            )
        else:
            try:
                latex_result = export_latex_from_run(
                    run_id=run_id,
                    store=store,
                    ledger=ledger,
                    root=root,
                    write_report=True,
                    render_check=config.render_check,
                    allow_external_tools=config.allow_external_tools,
                    latex_executable=config.latex_executable,
                    renderer=renderer,
                )
            except (LatexExportError, LatexRenderError) as exc:
                raise FullPaperGenerationError(str(exc)) from exc
            steps.append(
                _step(
                    "export-latex",
                    _status_from_warnings(latex_result.export_result.warnings),
                    "LaTeX export artifacts were generated.",
                    _refs_from_latex_result(latex_result),
                    latex_result.export_result.warnings,
                )
            )
    else:
        steps.append(
            _step(
                "export-latex",
                FullPaperGenerationStepStatus.SKIPPED,
                "LaTeX export was skipped by configuration.",
                {},
            )
        )

    if config.critique:
        critic_refs = _latest_refs(
            ledger,
            run_id,
            ControllerActionType.PAPER_CRITIC_REPORT_WRITTEN,
        )
        if critic_refs:
            steps.append(
                _step(
                    "critique-paper",
                    FullPaperGenerationStepStatus.SKIPPED,
                    "Existing paper critic report was reused.",
                    critic_refs,
                )
            )
        else:
            try:
                critic_result = critique_paper_from_run(
                    run_id=run_id,
                    root=root,
                    store=store,
                    ledger=ledger,
                    write_report=True,
                )
            except PaperCriticError as exc:
                raise FullPaperGenerationError(str(exc)) from exc
            warnings = _critic_warnings(critic_result)
            steps.append(
                _step(
                    "critique-paper",
                    _status_from_warnings(warnings),
                    "Paper critic report was generated.",
                    _refs_from_critic_result(critic_result),
                    warnings,
                )
            )
    else:
        steps.append(
            _step(
                "critique-paper",
                FullPaperGenerationStepStatus.SKIPPED,
                "Paper critique was skipped by configuration.",
                {},
            )
        )

    repair_requested = config.apply_safe_fake_revision or enable_safe_repair
    if config.revise or repair_requested:
        try:
            revision_result = revise_paper_from_run(
                run_id=run_id,
                root=root,
                store=store,
                ledger=ledger,
                apply_safe_fake_revision_flag=repair_requested,
                write_report=repair_requested,
                safe_repair_mode=enable_safe_repair,
            )
        except PaperCriticError as exc:
            raise FullPaperGenerationError(str(exc)) from exc
        warnings = _revision_warnings(
            revision_result,
            safe_repair_mode=enable_safe_repair,
        )
        steps.append(
            _step(
                "revise-paper",
                _status_from_warnings(warnings),
                (
                    "Safe fake paper revision was applied."
                    if repair_requested
                    else "Paper revision plan was built without applying changes."
                ),
                _refs_from_revision_result(revision_result),
                warnings,
            )
        )
    else:
        steps.append(
            _step(
                "revise-paper",
                FullPaperGenerationStepStatus.SKIPPED,
                "Paper revision was skipped; no revised draft was written.",
                {},
            )
        )

    reexport_requested = config.reexport_latex_after_revision or enable_safe_repair
    if reexport_requested:
        if revision_result is None or revision_result.revision_result is None:
            raise FullPaperGenerationError(
                "LaTeX re-export after revision requires --apply-safe-fake-revision."
            )
        try:
            revised_export = _export_revised_markdown_to_latex(
                run_id=run_id,
                root=root,
                ledger=ledger,
                revision_result=revision_result,
            )
        except LatexExportError as exc:
            raise FullPaperGenerationError(str(exc)) from exc
        steps.append(
            _step(
                "reexport-latex-after-revision",
                _status_from_warnings(revised_export.warnings),
                "Revised Markdown draft was converted to LaTeX in memory.",
                {},
                revised_export.warnings,
            )
        )
    else:
        steps.append(
            _step(
                "reexport-latex-after-revision",
                FullPaperGenerationStepStatus.SKIPPED,
                "LaTeX re-export after revision was skipped.",
                {},
            )
        )

    bundle = _collect_artifact_bundle(ledger, run_id)
    warnings = (
        _aggregate_post_repair_warnings(steps, revision_result)
        if enable_safe_repair
        and revision_result is not None
        and revision_result.revision_result is not None
        else _aggregate_warnings(steps)
    )
    status = _generation_status(steps, warnings)
    report = FullPaperGenerationReport(
        report_id=f"full-paper-generation-report-{run_id}",
        run_id=run_id,
        config=config,
        generation_status=status,
        steps=steps,
        artifact_bundle=bundle,
        warnings=warnings,
        blocking_issues=_blocking_issues(steps),
        revision_applied=repair_requested,
        render_check_requested=config.render_check,
        publication_ready=False,
    )
    if not config.write_report:
        return FullPaperGenerationRunResult(
            run_id=run_id,
            report=report,
            artifact_bundle=bundle,
            drafting_result=drafting_result,
            latex_result=latex_result,
            critic_result=critic_result,
            revision_result=revision_result,
        )

    persistence = _write_full_paper_generation_artifacts(
        run_id=run_id,
        store=store,
        ledger=ledger,
        report=report,
        bundle=bundle,
        revised_export=revised_export,
    )
    return _with_persisted_generation_artifacts(
        run_id=run_id,
        report=report,
        bundle=bundle,
        drafting_result=drafting_result,
        latex_result=latex_result,
        critic_result=critic_result,
        revision_result=revision_result,
        persistence=persistence,
    )


def inspect_paper_bundle_summary(
    *,
    run_id: str,
    root: str | Path = ".",
) -> dict[str, Any]:
    """Inspect generated paper bundle artifacts without mutating the run."""
    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    if not run_path.is_dir():
        raise PaperBundleInspectionError(f"No run directory found for run_id={run_id}.")
    paths = _paper_bundle_paths(run_path)
    existing = {
        name: path.relative_to(root_path).as_posix()
        for name, path in sorted(paths.items())
        if path.is_file()
    }
    primary_draft = (
        paths["revised_manuscript_draft"]
        if paths["revised_manuscript_draft"].is_file()
        else paths["complete_manuscript_draft"]
        if paths["complete_manuscript_draft"].is_file()
        else None
    )
    primary_latex = (
        paths["revised_paper"]
        if paths["revised_paper"].is_file()
        else paths["paper"]
        if paths["paper"].is_file()
        else None
    )
    primary_source_map = (
        paths["revised_latex_source_map"]
        if paths["revised_latex_source_map"].is_file()
        else paths["latex_source_map"]
        if paths["latex_source_map"].is_file()
        else None
    )
    manuscript_stats = (
        _manuscript_stats(primary_draft.read_text(encoding="utf-8"))
        if primary_draft is not None
        else _empty_manuscript_stats()
    )
    generation_report = _read_generation_report(paths["generation_report"])
    release_report = _read_release_report(paths["release_report"])
    safe_repair_report = _read_json_optional(paths["safe_repair_report"])
    markdown = (
        primary_draft.read_text(encoding="utf-8")
        if primary_draft is not None
        else ""
    )
    section_accounting = _section_accounting(
        _markdown_sections(markdown),
        manuscript_stats.get("title_detected"),
    )
    citation_registry = _read_citation_registry(paths["citation_registry"])
    marker_keys = sorted(set(CITATION_MARKER_RE.findall(markdown)))
    if citation_registry is not None:
        citation_safety = validate_citation_usage(markdown, citation_registry)
        unregistered_citation_keys = citation_safety.unregistered_citation_keys
        registry_backed_citation_count = citation_safety.registry_backed_citation_count
        bibliography_registry_backed = citation_safety.bibliography_registry_backed
        citation_policy = citation_registry.citation_policy
        registry_source_count = len(citation_registry.citations)
    else:
        unregistered_citation_keys = marker_keys
        registry_backed_citation_count = 0
        bibliography_registry_backed = False
        citation_policy = "none"
        registry_source_count = 0
    bibliography_present = bool(
        re.search(r"^#{1,6}\s+(bibliography|references)\s*$", markdown, re.I | re.M)
    )
    bibliography_status = (
        "registry-backed"
        if bibliography_present and bibliography_registry_backed
        else "unsafe"
        if bibliography_present
        else "absent"
    )
    generation_warning_count = (
        len(generation_report.warnings) if generation_report is not None else 0
    )
    release_warning_count = (
        len(release_report.decision.warnings) if release_report is not None else 0
    )
    generation_blocking_count = (
        len(generation_report.blocking_issues) if generation_report is not None else 0
    )
    release_blocking_count = (
        len(release_report.decision.blocking_reasons)
        if release_report is not None
        else 0
    )
    return {
        "run_id": run_id,
        "paper_exists": paths["paper"].is_file(),
        "revised_paper_exists": paths["revised_paper"].is_file(),
        "complete_manuscript_draft_exists": paths["complete_manuscript_draft"].is_file(),
        "revised_manuscript_draft_exists": paths["revised_manuscript_draft"].is_file(),
        "latex_exists": paths["paper"].is_file(),
        "revised_latex_exists": paths["revised_paper"].is_file(),
        "safe_repair_report_exists": paths["safe_repair_report"].is_file(),
        "release_report_exists": paths["release_report"].is_file(),
        "generation_report_exists": paths["generation_report"].is_file(),
        "primary_artifact_to_read": (
            primary_draft.relative_to(root_path).as_posix()
            if primary_draft is not None
            else primary_latex.relative_to(root_path).as_posix()
            if primary_latex is not None
            else None
        ),
        "primary_latex_to_read": (
            primary_latex.relative_to(root_path).as_posix()
            if primary_latex is not None
            else None
        ),
        "primary_source_map_to_read": (
            primary_source_map.relative_to(root_path).as_posix()
            if primary_source_map is not None
            else None
        ),
        **manuscript_stats,
        **_public_section_accounting(section_accounting),
        "warning_counts": {
            "generation": generation_warning_count,
            "release": release_warning_count,
            "total": generation_warning_count + release_warning_count,
        },
        "blocking_issue_counts": {
            "generation": generation_blocking_count,
            "release": release_blocking_count,
            "total": generation_blocking_count + release_blocking_count,
        },
        "warning_count": generation_warning_count + release_warning_count,
        "blocking_issue_count": generation_blocking_count + release_blocking_count,
        "release_status": (
            release_report.decision.status.value if release_report is not None else None
        ),
        "generation_status": (
            generation_report.generation_status.value
            if generation_report is not None
            else None
        ),
        "safe_repair_applied_count": (
            int(safe_repair_report.get("repairs_applied", 0))
            if isinstance(safe_repair_report, dict)
            else 0
        ),
        "citation_registry_present": paths["citation_registry"].is_file(),
        "citation_registry_source_count": registry_source_count,
        "registry_backed_citation_count": registry_backed_citation_count,
        "unregistered_citation_keys": unregistered_citation_keys,
        "bibliography_registry_backed": bibliography_registry_backed,
        "bibliography_status": bibliography_status,
        "citation_policy": citation_policy,
        "artifacts": existing,
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }


def lint_paper_bundle_summary(
    *,
    run_id: str,
    root: str | Path = ".",
    min_words: int = 1500,
    min_avg_words_per_section: float = 120.0,
    min_citation_markers: int = 1,
) -> dict[str, Any]:
    """Compute deterministic draft-quality diagnostics without mutating the run."""
    if min_words < 0:
        raise PaperBundleInspectionError("min_words must be non-negative.")
    if min_avg_words_per_section < 0:
        raise PaperBundleInspectionError(
            "min_avg_words_per_section must be non-negative."
        )
    if min_citation_markers < 0:
        raise PaperBundleInspectionError("min_citation_markers must be non-negative.")

    root_path = Path(root)
    bundle = inspect_paper_bundle_summary(run_id=run_id, root=root_path)
    primary = bundle.get("primary_artifact_to_read")
    markdown = ""
    if isinstance(primary, str) and primary.endswith(".md"):
        primary_path = root_path / primary
        if primary_path.is_file():
            markdown = primary_path.read_text(encoding="utf-8")

    headings = list(bundle.get("section_headings_detected") or [])
    word_count = int(bundle.get("word_count") or 0)
    section_count = int(bundle.get("section_count") or 0)
    average_words_per_section = (
        round(word_count / section_count, 1) if section_count else 0.0
    )
    title = bundle.get("title_detected")
    title_text = str(title) if title else ""
    title_is_placeholder = _title_is_placeholder(title_text)
    abstract_present = bool(bundle.get("abstract_detected"))
    citation_marker_count = int(bundle.get("citation_marker_count") or 0)
    citations_present = citation_marker_count > 0
    citation_registry_present = bool(bundle.get("citation_registry_present"))
    citation_registry_source_count = int(
        bundle.get("citation_registry_source_count") or 0
    )
    registry_backed_citation_count = int(
        bundle.get("registry_backed_citation_count") or 0
    )
    unregistered_citation_keys = list(
        bundle.get("unregistered_citation_keys") or []
    )
    bibliography_registry_backed = bool(
        bundle.get("bibliography_registry_backed")
    )
    citation_policy = str(bundle.get("citation_policy") or "none")
    sections = _markdown_sections(markdown)
    section_accounting = _section_accounting(sections, title_text)
    main_body_sections = section_accounting["main_body_sections"]
    appendix_sections = section_accounting["appendix_sections"]
    metadata_sections = section_accounting["metadata_sections"]
    unplanned_main_body_sections = section_accounting[
        "unplanned_main_body_sections"
    ]
    body_sections = [
        *main_body_sections,
        *unplanned_main_body_sections,
    ]
    main_body_section_count = len(main_body_sections)
    appendix_section_count = len(appendix_sections)
    metadata_section_count = len(metadata_sections)
    total_heading_count = int(section_accounting["total_heading_count"])
    main_body_word_count = sum(
        int(section["word_count"]) for section in main_body_sections
    )
    appendix_word_count = sum(
        int(section["word_count"]) for section in appendix_sections
    )
    main_body_avg_words_per_section = (
        round(main_body_word_count / main_body_section_count, 1)
        if main_body_section_count
        else 0.0
    )
    sections_too_short = [
        {"heading": section["heading"], "word_count": section["word_count"]}
        for section in body_sections
        if section["word_count"] < min_avg_words_per_section
    ]
    empty_or_placeholder_sections = [
        {"heading": section["heading"], "word_count": section["word_count"]}
        for section in body_sections
        if section["word_count"] == 0 or _contains_placeholder_text(section["body"])
    ]
    generic_heading_count = sum(
        1 for heading in headings if heading.casefold() in _GENERIC_HEADING_TITLES
    )
    lower_body_text = _markdown_body_text(markdown).casefold()
    lower_headings = [heading.casefold() for heading in headings]
    problem_statement_present = _contains_any(lower_body_text, _PROBLEM_LANGUAGE)
    central_contribution_present = _contains_any(
        lower_body_text,
        _CONTRIBUTION_LANGUAGE,
    )
    method_summary_present = _contains_any(lower_body_text, _METHOD_LANGUAGE)
    evidence_boundary_statement_present = _contains_any(
        lower_body_text,
        _EVIDENCE_BOUNDARY_LANGUAGE,
    )
    limitations_present = _contains_any(lower_body_text, _LIMITATION_LANGUAGE)
    provenance_present = _contains_any(lower_body_text, _PROVENANCE_LANGUAGE)
    unsupported_claims_absent = not _contains_any(
        lower_body_text,
        ("unsupported sentence", "unsupported assertion", "unsupported factual claim"),
    )
    fake_citations_absent = not _contains_any(lower_body_text, _FAKE_CITATION_PATTERNS)
    fake_empirical_claims_absent = not _contains_any(
        lower_body_text,
        _FORBIDDEN_EMPIRICAL_PATTERNS,
    )
    title_is_non_placeholder = bool(title_text) and not title_is_placeholder
    title_is_grammatical_enough = _title_is_grammatical_enough(title_text)
    missing_claim_evidence_appendix = not any(
        "claim/evidence" in heading or ("claim" in heading and "evidence" in heading)
        for heading in lower_headings
    )
    missing_provenance_appendix = not any(
        "provenance" in heading for heading in lower_headings
    )
    max_section_count_for_short_draft = 10
    paper_body_heading_count = (
        main_body_section_count + len(unplanned_main_body_sections)
    )
    too_many_sections_for_length = (
        word_count < min_words
        and paper_body_heading_count > max_section_count_for_short_draft
    )
    nested_main_body_heading_count = sum(
        1 for section in body_sections if section["level"] > 2
    )
    placeholder_main_body_sections = [
        section
        for section in main_body_sections
        if section["word_count"] == 0 or _contains_placeholder_text(section["body"])
    ]
    unplanned_main_body_headings = [
        str(section["heading"]) for section in unplanned_main_body_sections
    ]
    standalone_central_message_detected = any(
        str(section["heading"]).casefold() == "central message"
        for section in metadata_sections
    )
    central_message_merged = (
        not standalone_central_message_detected
        and _contains_any(lower_body_text, _CONTRIBUTION_LANGUAGE)
    )
    conclusion_section = next(
        (
            section
            for section in main_body_sections
            if _canonical_main_body_heading(str(section["heading"])) == "Conclusion"
        ),
        None,
    )
    conclusion_placeholder_like = bool(
        conclusion_section is not None
        and (
            int(conclusion_section["word_count"]) == 0
            or _contains_placeholder_text(str(conclusion_section["body"]))
        )
    )
    main_body_heading_fragmentation_detected = bool(
        unplanned_main_body_headings
        or nested_main_body_heading_count
        or main_body_section_count > len(CANONICAL_MAIN_SECTIONS) + 1
        or standalone_central_message_detected
    )
    severe_section_fragmentation = bool(
        unplanned_main_body_headings
        or nested_main_body_heading_count
        or main_body_section_count > len(CANONICAL_MAIN_SECTIONS) + 1
        or len(placeholder_main_body_sections) >= 2
    )
    heading_fragmentation_detected = main_body_heading_fragmentation_detected
    placeholder_sections_detected = bool(empty_or_placeholder_sections)
    mostly_placeholder_sections = len(empty_or_placeholder_sections) >= max(
        1,
        len(body_sections) // 2,
    )
    section_structure_coherent = not severe_section_fragmentation
    unsupported_external_claims_without_citations = (
        citation_marker_count == 0
        and _contains_any(lower_body_text, _UNSUPPORTED_EXTERNAL_FACT_PATTERNS)
    )
    semantic_checks = {
        "problem_statement_present": problem_statement_present,
        "central_contribution_present": central_contribution_present,
        "method_summary_present": method_summary_present,
        "evidence_boundary_statement_present": evidence_boundary_statement_present,
        "limitations_present": limitations_present,
        "provenance_present": provenance_present,
        "unsupported_claims_absent": unsupported_claims_absent,
        "fake_citations_absent": fake_citations_absent,
        "fake_empirical_claims_absent": fake_empirical_claims_absent,
        "title_is_non_placeholder": title_is_non_placeholder,
        "title_is_grammatical_enough": title_is_grammatical_enough,
        "section_structure_coherent": section_structure_coherent,
        "section_fragmentation_detected": heading_fragmentation_detected,
        "main_body_heading_fragmentation_detected": (
            main_body_heading_fragmentation_detected
        ),
        "placeholder_sections_detected": placeholder_sections_detected,
        "claim_evidence_appendix_present": not missing_claim_evidence_appendix,
        "provenance_appendix_present": not missing_provenance_appendix,
        "unsupported_external_claims_without_citations": (
            unsupported_external_claims_without_citations
        ),
    }
    semantic_section_audit = _semantic_section_audit(
        _body_sections(sections, title_text)
    )

    failure_reasons: list[str] = []
    development_warnings: list[str] = []
    if not markdown:
        failure_reasons.append("No Markdown manuscript draft artifact was found.")
    if not problem_statement_present:
        failure_reasons.append("Problem statement is missing or not explicit.")
    if not central_contribution_present:
        failure_reasons.append("Central contribution is missing or not explicit.")
    if not method_summary_present:
        failure_reasons.append("Method or model summary is missing.")
    if not evidence_boundary_statement_present:
        failure_reasons.append("Evidence boundary statement is missing.")
    if not limitations_present:
        failure_reasons.append("Limitations are missing.")
    if not provenance_present:
        failure_reasons.append("Provenance statement or appendix is missing.")
    if not unsupported_claims_absent:
        failure_reasons.append("Unsupported assertive claim language is present.")
    if not fake_citations_absent:
        failure_reasons.append("Fake or placeholder citation markers are present.")
    if not fake_empirical_claims_absent:
        failure_reasons.append("Fake empirical or real-world validation language is present.")
    if unsupported_external_claims_without_citations:
        failure_reasons.append("External factual claims appear without citation markers.")
    if unregistered_citation_keys:
        failure_reasons.append(
            "Unregistered citation keys are present: "
            + ", ".join(unregistered_citation_keys)
        )
    if bundle.get("bibliography_status") == "unsafe":
        failure_reasons.append("Bibliography is not fully backed by the citation registry.")
    if word_count < min_words:
        development_warnings.append("Draft may be skeletal: below proxy word-count target.")
    if average_words_per_section < min_avg_words_per_section:
        development_warnings.append("Sections may be underdeveloped by proxy word count.")
    if not title_text:
        failure_reasons.append("Title is missing.")
    elif title_is_placeholder:
        failure_reasons.append("Title appears to be a placeholder.")
    elif not title_is_grammatical_enough:
        development_warnings.append("Title may be grammatically weak.")
    if sections_too_short:
        development_warnings.append("One or more sections may be underdeveloped.")
    if placeholder_sections_detected:
        development_warnings.append("One or more sections are empty or placeholder-like.")
    if mostly_placeholder_sections:
        failure_reasons.append("Most body sections are empty or placeholder text.")
    if too_many_sections_for_length:
        development_warnings.append("Too many headings for the amount of content.")
    elif appendix_section_count and total_heading_count > max_section_count_for_short_draft:
        development_warnings.append(
            "Appendices increase the total heading count but do not fragment the main body."
        )
    if standalone_central_message_detected:
        development_warnings.append(
            "A standalone Central Message heading should be consolidated into a planned section."
        )
    if conclusion_placeholder_like:
        development_warnings.append(
            "The conclusion appears placeholder-like and should synthesize the "
            "bounded contribution."
        )
    if severe_section_fragmentation:
        failure_reasons.append("Severe section fragmentation is present.")
    if missing_claim_evidence_appendix:
        failure_reasons.append("Claim/evidence appendix is missing.")
    if missing_provenance_appendix:
        failure_reasons.append("Provenance appendix is missing.")
    if (
        citation_marker_count < min_citation_markers
        and not unsupported_external_claims_without_citations
    ):
        development_warnings.append(
            "Citation registry sources are available but no citation markers were used."
            if citation_registry_source_count
            else "No citation markers found."
        )

    issues = [*failure_reasons, *development_warnings]
    quality_status = (
        "DraftQualityFailed"
        if failure_reasons
        else "DraftQualityWarnings"
        if development_warnings
        else "DraftQualityPass"
    )
    quality_score = _quality_score(failure_reasons, development_warnings)
    return {
        "run_id": run_id,
        "quality_status": quality_status,
        "quality_score_optional": quality_score,
        "word_count": word_count,
        "section_count": section_count,
        "average_words_per_section": average_words_per_section,
        "main_body_section_count": main_body_section_count,
        "appendix_section_count": appendix_section_count,
        "metadata_section_count": metadata_section_count,
        "total_heading_count": total_heading_count,
        "main_body_word_count": main_body_word_count,
        "appendix_word_count": appendix_word_count,
        "main_body_avg_words_per_section": main_body_avg_words_per_section,
        "title_detected": title_text or None,
        "title_is_placeholder": title_is_placeholder,
        "abstract_present": abstract_present,
        "citation_marker_count": citation_marker_count,
        "citations_present": citations_present,
        "citation_registry_present": citation_registry_present,
        "citation_registry_source_count": citation_registry_source_count,
        "registry_backed_citation_count": registry_backed_citation_count,
        "unregistered_citation_keys": unregistered_citation_keys,
        "bibliography_registry_backed": bibliography_registry_backed,
        "bibliography_status": bundle.get("bibliography_status", "absent"),
        "citation_policy": citation_policy,
        "sections_too_short": sections_too_short,
        "empty_or_placeholder_sections": empty_or_placeholder_sections,
        "generic_heading_count": generic_heading_count,
        "missing_problem_framing": not problem_statement_present,
        "missing_method_summary": not method_summary_present,
        "missing_limitations": not limitations_present,
        "missing_claim_evidence_appendix": missing_claim_evidence_appendix,
        "missing_provenance_appendix": missing_provenance_appendix,
        "too_many_sections_for_length": too_many_sections_for_length,
        "heading_fragmentation_detected": heading_fragmentation_detected,
        "main_body_heading_fragmentation_detected": (
            main_body_heading_fragmentation_detected
        ),
        "appendix_headings_present": appendix_section_count > 0,
        "unplanned_main_body_headings": unplanned_main_body_headings,
        "standalone_central_message_detected": (
            standalone_central_message_detected
        ),
        "central_message_merged": central_message_merged,
        "conclusion_placeholder_like": conclusion_placeholder_like,
        "semantic_checks": semantic_checks,
        "semantic_section_audit": semantic_section_audit,
        "development_warnings": development_warnings,
        "quality_failure_reasons": failure_reasons,
        "quality_warning_reasons": development_warnings,
        "blocking_quality_issues": failure_reasons,
        "issues": issues,
        "warnings": development_warnings,
        "thresholds": {
            "min_words": min_words,
            "min_avg_words_per_section": min_avg_words_per_section,
            "min_citation_markers": min_citation_markers,
            "max_section_count_for_short_draft": max_section_count_for_short_draft,
            "placeholder_title_patterns": list(_PLACEHOLDER_TITLE_PATTERNS),
        },
        "primary_artifact_to_read": bundle.get("primary_artifact_to_read"),
        "paper_release_status": bundle.get("release_status"),
        "publication_ready": False,
        "safety_report_safe": None,
        "release_status_unchanged": True,
        "safety_status_unchanged": True,
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }


def _validate_upstream_prerequisites(run_id: str, ledger: ResearchLedger) -> None:
    try:
        load_manuscript_drafting_inputs(run_id, ledger)
    except ManuscriptDraftingError as exc:
        raise FullPaperGenerationError(_clear_manuscript_error(str(exc))) from exc


def _paper_bundle_paths(run_path: Path) -> dict[str, Path]:
    return {
        "complete_manuscript_draft": run_path
        / "reports"
        / "complete-manuscript-draft.md",
        "revised_manuscript_draft": run_path
        / "reports"
        / "revised-manuscript-draft.md",
        "paper": run_path / "latex" / "paper.tex",
        "revised_paper": run_path / "latex" / "revised-paper.tex",
        "latex_source_map": run_path / "latex" / "latex-source-map.json",
        "revised_latex_source_map": run_path
        / "latex"
        / "revised-latex-source-map.json",
        "generation_report": run_path / "reports" / "full-paper-generation-report.json",
        "release_report": run_path / "reports" / "full-paper-release-report.json",
        "safe_repair_report": run_path / "reports" / "safe-repair-report.json",
        "retrieval_report": run_path / "reports" / "retrieval-report.json",
        "citation_registry": run_path / "reports" / "citation-registry.json",
        "references": run_path / "latex" / "references.bib",
        "revised_references": run_path / "latex" / "revised-references.bib",
    }


def _read_citation_registry(path: Path) -> CitationRegistry | None:
    if not path.is_file():
        return None
    try:
        return CitationRegistry.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _manuscript_stats(markdown: str) -> dict[str, Any]:
    headings = _markdown_headings(markdown)
    title = _markdown_title(markdown)
    abstract_detected = any(
        heading.lower() == "abstract" for heading in headings
    )
    citation_marker_count = len(re.findall(r"\[@[A-Za-z0-9][A-Za-z0-9_.:-]*\]", markdown))
    return {
        "line_count": len(markdown.splitlines()),
        "word_count": len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'_-]*", markdown)),
        "section_headings_detected": headings,
        "section_count": len(headings),
        "title_detected": title,
        "abstract_detected": abstract_detected,
        "citations_present": citation_marker_count > 0,
        "citation_marker_count": citation_marker_count,
    }


def _empty_manuscript_stats() -> dict[str, Any]:
    return {
        "line_count": 0,
        "word_count": 0,
        "section_headings_detected": [],
        "section_count": 0,
        "title_detected": None,
        "abstract_detected": False,
        "citations_present": False,
        "citation_marker_count": 0,
    }


def _section_accounting(
    sections: list[dict[str, Any]],
    title: object,
) -> dict[str, Any]:
    title_text = str(title or "")
    body_sections = _body_sections(sections, title_text)
    main_body_sections: list[dict[str, Any]] = []
    appendix_sections: list[dict[str, Any]] = []
    metadata_sections: list[dict[str, Any]] = []
    unplanned_main_body_sections: list[dict[str, Any]] = []
    for section in body_sections:
        heading = str(section["heading"])
        heading_key = heading.casefold()
        if _canonical_main_body_heading(heading) is not None:
            main_body_sections.append(section)
        elif any(fragment in heading_key for fragment in _APPENDIX_HEADING_FRAGMENTS):
            appendix_sections.append(section)
        elif heading_key in _METADATA_HEADING_TITLES:
            metadata_sections.append(section)
        else:
            unplanned_main_body_sections.append(section)
    return {
        "main_body_sections": main_body_sections,
        "appendix_sections": appendix_sections,
        "metadata_sections": metadata_sections,
        "unplanned_main_body_sections": unplanned_main_body_sections,
        "main_body_section_count": len(main_body_sections),
        "appendix_section_count": len(appendix_sections),
        "metadata_section_count": len(metadata_sections),
        "total_heading_count": len(sections),
        "main_body_word_count": sum(
            int(section["word_count"]) for section in main_body_sections
        ),
        "appendix_word_count": sum(
            int(section["word_count"]) for section in appendix_sections
        ),
        "main_body_avg_words_per_section": (
            round(
                sum(int(section["word_count"]) for section in main_body_sections)
                / len(main_body_sections),
                1,
            )
            if main_body_sections
            else 0.0
        ),
    }


def _public_section_accounting(accounting: dict[str, Any]) -> dict[str, Any]:
    return {
        key: accounting[key]
        for key in (
            "main_body_section_count",
            "appendix_section_count",
            "metadata_section_count",
            "total_heading_count",
            "main_body_word_count",
            "appendix_word_count",
            "main_body_avg_words_per_section",
        )
    }


def _canonical_main_body_heading(heading: str) -> str | None:
    return _MAIN_BODY_HEADING_ALIASES.get(heading.strip().casefold())


def _markdown_headings(markdown: str) -> list[str]:
    headings = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if not stripped.startswith("#"):
            continue
        heading = stripped.lstrip("#").strip()
        if heading:
            headings.append(heading)
    return headings


def _markdown_title(markdown: str) -> str | None:
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip() or None
    return None


def _markdown_sections(markdown: str) -> list[dict[str, Any]]:
    sections: list[dict[str, Any]] = []
    current_heading: str | None = None
    current_level = 0
    current_lines: list[str] = []
    for line in markdown.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*$", line.strip())
        if match:
            if current_heading is not None:
                body = "\n".join(current_lines).strip()
                sections.append(
                    {
                        "heading": current_heading,
                        "level": current_level,
                        "body": body,
                        "word_count": _word_count(body),
                    }
                )
            current_heading = match.group(2).strip()
            current_level = len(match.group(1))
            current_lines = []
        else:
            current_lines.append(line)
    if current_heading is not None:
        body = "\n".join(current_lines).strip()
        sections.append(
            {
                "heading": current_heading,
                "level": current_level,
                "body": body,
                "word_count": _word_count(body),
            }
        )
    return sections


def _markdown_body_text(markdown: str) -> str:
    return "\n".join(
        line for line in markdown.splitlines() if not line.strip().startswith("#")
    )


def _body_sections(
    sections: list[dict[str, Any]],
    title: str,
) -> list[dict[str, Any]]:
    title_key = title.casefold()
    return [
        section
        for section in sections
        if not (
            section["level"] == 1
            and title_key
            and str(section["heading"]).casefold() == title_key
        )
    ]


def _major_body_sections(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    skipped_fragments = (
        "appendix",
        "bibliography",
        "references",
        "draft invariants",
    )
    return [
        section
        for section in sections
        if not any(
            fragment in str(section["heading"]).casefold()
            for fragment in skipped_fragments
        )
    ]


def _word_count(text: str) -> int:
    return len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'_-]*", text))


def _title_is_placeholder(title: str) -> bool:
    title_key = title.casefold()
    return any(pattern in title_key for pattern in _PLACEHOLDER_TITLE_PATTERNS)


def _contains_placeholder_text(text: str) -> bool:
    text_key = text.casefold()
    return any(pattern in text_key for pattern in _PLACEHOLDER_SECTION_PATTERNS)


def _title_is_grammatical_enough(title: str) -> bool:
    if not title:
        return False
    normalized = " ".join(title.split()).casefold()
    if title.strip().endswith("?"):
        return False
    weak_patterns = (
        " expose structure ",
        " expose ",
        " manuscript plan",
        " bounded study of selected branch",
    )
    return not any(pattern in f" {normalized} " for pattern in weak_patterns)


def _semantic_section_audit(sections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "section_name": str(section["heading"]),
            "word_count": section["word_count"],
            "contains_problem_language": _contains_any(
                str(section["body"]).casefold(),
                _PROBLEM_LANGUAGE,
            ),
            "contains_contribution_language": _contains_any(
                str(section["body"]).casefold(),
                _CONTRIBUTION_LANGUAGE,
            ),
            "contains_evidence_boundary_language": _contains_any(
                str(section["body"]).casefold(),
                _EVIDENCE_BOUNDARY_LANGUAGE,
            ),
            "contains_limitation_language": _contains_any(
                str(section["body"]).casefold(),
                _LIMITATION_LANGUAGE,
            ),
            "contains_provenance_language": _contains_any(
                str(section["body"]).casefold(),
                _PROVENANCE_LANGUAGE,
            ),
            "contains_forbidden_validation_language": _contains_any(
                str(section["body"]).casefold(),
                _FORBIDDEN_EMPIRICAL_PATTERNS,
            ),
            "contains_extra_headings": section["level"] > 2,
            "placeholder_like": section["word_count"] == 0
            or _contains_placeholder_text(str(section["body"])),
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
        }
        for section in sections
    ]


def _contains_any(text: str, phrases: tuple[str, ...]) -> bool:
    return any(phrase in text for phrase in phrases)


def _contains_heading_or_text(
    headings: list[str],
    text: str,
    phrase: str,
) -> bool:
    return any(phrase in heading for heading in headings) or phrase in text


def _quality_score(blocking_issues: list[str], warnings: list[str]) -> float:
    penalty = min(1.0, 0.12 * len(blocking_issues) + 0.05 * len(warnings))
    return round(max(0.0, 1.0 - penalty), 2)


def _read_generation_report(path: Path) -> FullPaperGenerationReport | None:
    if not path.is_file():
        return None
    return FullPaperGenerationReport.model_validate_json(path.read_text(encoding="utf-8"))


def _read_release_report(path: Path) -> FullPaperReleaseReport | None:
    if not path.is_file():
        return None
    return FullPaperReleaseReport.model_validate_json(path.read_text(encoding="utf-8"))


def _read_json_optional(path: Path) -> Any | None:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _ensure_citation_artifacts(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    include_citations: bool,
    max_retrieval_sources: int,
) -> FullPaperGenerationStep:
    if not include_citations:
        return _step(
            "citation-registry",
            FullPaperGenerationStepStatus.SKIPPED,
            "Citation registry generation was skipped by configuration.",
            {},
        )
    refs = _citation_refs(ledger, run_id)
    if refs:
        return _step(
            "citation-registry",
            FullPaperGenerationStepStatus.SKIPPED,
            "Existing citation and literature-positioning artifacts were reused.",
            refs,
        )
    try:
        inputs = load_manuscript_drafting_inputs(run_id, ledger)
    except ManuscriptDraftingError as exc:
        raise FullPaperGenerationError(_clear_manuscript_error(str(exc))) from exc
    registry = build_citation_registry_from_ledger(
        run_id,
        ledger,
        max_sources=max_retrieval_sources,
    )
    positioning = build_literature_positioning_report(
        run_id=run_id,
        citation_registry=registry,
        narrative_contract=inputs.narrative_contract,
    )
    safety = validate_citation_usage(positioning.markdown_intro_paragraph, registry)
    artifacts = write_citation_registry_reports(
        run_id=run_id,
        store=store,
        ledger=ledger,
        citation_registry=registry,
        literature_positioning_report=positioning,
        citation_safety_report=safety,
    )
    refs = {
        "citation-registry": artifacts.citation_registry_artifact,
        "literature-positioning-report": artifacts.literature_positioning_artifact,
        "citation-safety-report": artifacts.citation_safety_artifact,
    }
    warnings = sorted({*registry.warnings, *safety.warnings, *safety.reasons})
    return _step(
        "citation-registry",
        _status_from_warnings(warnings),
        "Citation registry and literature-positioning artifacts were generated.",
        refs,
        warnings,
    )


def _export_revised_markdown_to_latex(
    *,
    run_id: str,
    root: str | Path,
    ledger: ResearchLedger,
    revision_result: PaperRevisionRunResult,
):
    if revision_result.revision_result is None:
        raise LatexExportError("revised manuscript draft is missing")
    inputs = load_latex_export_inputs(run_id, root=root, ledger=ledger)
    contract = build_latex_export_contract(
        run_id=run_id,
        manuscript_draft_artifact_id="revised-manuscript-draft",
        drafting_plan=inputs.drafting_plan,
        drafting_report=inputs.drafting_report,
        citation_registry=inputs.citation_registry,
        citation_registry_artifact_id=(
            inputs.citation_registry_artifact.id
            if inputs.citation_registry_artifact is not None
            else None
        ),
        render_check_enabled=False,
    )
    return export_markdown_draft_to_latex(
        run_id=run_id,
        draft_markdown=revision_result.revision_result.revised_markdown,
        contract=contract,
        drafting_plan=inputs.drafting_plan,
        drafting_report=inputs.drafting_report,
        citation_registry=inputs.citation_registry,
    )


def _write_full_paper_generation_artifacts(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    report: FullPaperGenerationReport,
    bundle: FullPaperArtifactBundle,
    revised_export,
) -> PersistenceResult:
    metadata = {
        "stage": "full_paper_generation",
        "artifact_role": "full_paper_generation_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }
    bundle = bundle.model_copy(
        update={
            "full_paper_generation_report_artifact_id": "full-paper-generation-report",
            "full_paper_artifact_bundle_artifact_id": "full-paper-artifact-bundle",
            "revised_latex_artifact_id": (
                "revised-paper" if revised_export is not None else None
            ),
            "revised_references_artifact_id": (
                "revised-references" if revised_export is not None else None
            ),
            "revised_latex_source_map_artifact_id": (
                "revised-latex-source-map" if revised_export is not None else None
            ),
            "revised_latex_export_report_artifact_id": (
                "revised-latex-export-report" if revised_export is not None else None
            ),
            "revised_latex_safety_report_artifact_id": (
                "revised-latex-safety-report" if revised_export is not None else None
            ),
        }
    )
    bundle = _bundle_with_artifact_ids(bundle)
    specs = [
        ArtifactWriteSpec(
            artifact_id="full-paper-generation-report",
            artifact_type=ArtifactType.REPORT,
            payload=report.model_copy(update={"artifact_bundle": bundle}),
            artifact_format="json",
            metadata=metadata,
        ),
        ArtifactWriteSpec(
            artifact_id="full-paper-artifact-bundle",
            artifact_type=ArtifactType.REPORT,
            payload=bundle,
            artifact_format="json",
            metadata=metadata,
        ),
    ]
    if revised_export is not None:
        specs.extend(
            [
                ArtifactWriteSpec(
                    artifact_id="revised-paper",
                    artifact_type=ArtifactType.LATEX,
                    payload=revised_export.paper_tex,
                    artifact_format="latex",
                    metadata={**metadata, "artifact_role": "revised_latex_context"},
                ),
                ArtifactWriteSpec(
                    artifact_id="revised-references",
                    artifact_type=ArtifactType.LATEX,
                    payload=revised_export.references_bib,
                    artifact_format="bib",
                    metadata={**metadata, "artifact_role": "revised_latex_context"},
                ),
                ArtifactWriteSpec(
                    artifact_id="revised-latex-source-map",
                    artifact_type=ArtifactType.LATEX,
                    payload=revised_export.source_map,
                    artifact_format="json",
                    metadata={**metadata, "artifact_role": "revised_latex_context"},
                ),
                ArtifactWriteSpec(
                    artifact_id="revised-latex-export-report",
                    artifact_type=ArtifactType.LATEX,
                    payload=revised_export,
                    artifact_format="json",
                    metadata={**metadata, "artifact_role": "revised_latex_context"},
                ),
                ArtifactWriteSpec(
                    artifact_id="revised-latex-safety-report",
                    artifact_type=ArtifactType.LATEX,
                    payload=revised_export.safety_report,
                    artifact_format="json",
                    metadata={**metadata, "artifact_role": "revised_latex_context"},
                ),
            ]
        )
    return persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.FULL_PAPER_GENERATION_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "generation_status": report.generation_status.value,
            "steps": len(report.steps),
            "warnings": len(report.warnings),
            "revision_applied": report.revision_applied,
            "render_check_requested": report.render_check_requested,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
        },
    )


def _handle_existing_generation_report(
    *,
    run_id: str,
    root: str | Path,
    ledger: ResearchLedger,
    policy: RerunPolicy,
    force: bool,
) -> FullPaperGenerationRunResult | None:
    refs = _latest_refs(ledger, run_id, ControllerActionType.FULL_PAPER_GENERATION_WRITTEN)
    if not refs:
        return None
    if policy == RerunPolicy.SKIP_IF_COMPLETE:
        report_ref = refs.get("full-paper-generation-report")
        bundle_ref = refs.get("full-paper-artifact-bundle")
        if report_ref is None or bundle_ref is None:
            raise FullPaperGenerationError(
                "Existing full-paper generation commit is missing report artifacts."
            )
        report = FullPaperGenerationReport.model_validate_json(
            _read_text_artifact(root, report_ref)
        )
        bundle = FullPaperArtifactBundle.model_validate_json(
            _read_text_artifact(root, bundle_ref)
        )
        return FullPaperGenerationRunResult(
            run_id=run_id,
            report=report,
            artifact_bundle=bundle,
            report_artifact=report_ref,
            bundle_artifact=bundle_ref,
        )
    if policy == RerunPolicy.ALLOW_IF_FORCED and force:
        return None
    raise FullPaperGenerationError(
        "Full-paper generation report already exists. Use --rerun-policy "
        "skip-if-complete or --rerun-policy allow-if-forced --force."
    )


def _with_persisted_generation_artifacts(
    *,
    run_id: str,
    report: FullPaperGenerationReport,
    bundle: FullPaperArtifactBundle,
    drafting_result: ManuscriptDraftingRunResult | None,
    latex_result: LatexExportRunResult | None,
    critic_result: PaperCriticRunResult | None,
    revision_result: PaperRevisionRunResult | None,
    persistence: PersistenceResult,
) -> FullPaperGenerationRunResult:
    refs = {artifact.id: artifact for artifact in persistence.artifacts}
    bundle = bundle.model_copy(
        update={
            "full_paper_generation_report_artifact_id": "full-paper-generation-report",
            "full_paper_artifact_bundle_artifact_id": "full-paper-artifact-bundle",
            "revised_latex_artifact_id": _id_if_present(refs, "revised-paper"),
            "revised_references_artifact_id": _id_if_present(refs, "revised-references"),
            "revised_latex_source_map_artifact_id": _id_if_present(
                refs, "revised-latex-source-map"
            ),
            "revised_latex_export_report_artifact_id": _id_if_present(
                refs, "revised-latex-export-report"
            ),
            "revised_latex_safety_report_artifact_id": _id_if_present(
                refs, "revised-latex-safety-report"
            ),
        }
    )
    bundle = _bundle_with_artifact_ids(bundle)
    report = report.model_copy(update={"artifact_bundle": bundle})
    return FullPaperGenerationRunResult(
        run_id=run_id,
        report=report,
        artifact_bundle=bundle,
        drafting_result=drafting_result,
        latex_result=latex_result,
        critic_result=critic_result,
        revision_result=revision_result,
        persistence=persistence,
        report_artifact=refs.get("full-paper-generation-report"),
        bundle_artifact=refs.get("full-paper-artifact-bundle"),
        revised_latex_artifact=refs.get("revised-paper"),
        revised_references_artifact=refs.get("revised-references"),
        revised_source_map_artifact=refs.get("revised-latex-source-map"),
        revised_export_report_artifact=refs.get("revised-latex-export-report"),
        revised_safety_report_artifact=refs.get("revised-latex-safety-report"),
    )


def full_paper_generation_result_model(
    result: FullPaperGenerationRunResult,
) -> FullPaperGenerationResult:
    """Convert a runtime result to the exported protocol result model."""
    return FullPaperGenerationResult(
        run_id=result.run_id,
        generation_status=result.report.generation_status,
        report=result.report,
        artifact_bundle=result.artifact_bundle,
    )


def _collect_artifact_bundle(
    ledger: ResearchLedger,
    run_id: str,
) -> FullPaperArtifactBundle:
    draft = _latest_refs(ledger, run_id, ControllerActionType.MANUSCRIPT_DRAFT_WRITTEN)
    citation = _citation_refs(ledger, run_id)
    latex = _latest_refs(ledger, run_id, ControllerActionType.LATEX_EXPORT_WRITTEN)
    critic = _latest_refs(ledger, run_id, ControllerActionType.PAPER_CRITIC_REPORT_WRITTEN)
    revision = _latest_refs(ledger, run_id, ControllerActionType.PAPER_REVISION_WRITTEN)
    retrieval = _latest_refs(ledger, run_id, ControllerActionType.RETRIEVAL_RUN_RECORDED)
    bundle = FullPaperArtifactBundle(
        run_id=run_id,
        retrieval_report_artifact_id=_id_if_present(retrieval, "retrieval-report"),
        citation_registry_artifact_id=_id_if_present(citation, "citation-registry"),
        literature_positioning_report_artifact_id=_id_if_present(
            citation, "literature-positioning-report"
        ),
        citation_safety_report_artifact_id=_id_if_present(
            citation, "citation-safety-report"
        ),
        manuscript_drafting_plan_artifact_id=_id_if_present(
            draft, "manuscript-drafting-plan"
        ),
        manuscript_drafting_report_artifact_id=_id_if_present(
            draft, "manuscript-drafting-report"
        ),
        complete_manuscript_draft_artifact_id=_id_if_present(
            draft, "complete-manuscript-draft"
        ),
        manuscript_assembly_report_artifact_id=_id_if_present(
            draft, "manuscript-assembly-report"
        ),
        latex_artifact_id=_id_if_present(latex, "paper"),
        references_artifact_id=_id_if_present(latex, "references"),
        latex_source_map_artifact_id=_id_if_present(latex, "latex-source-map"),
        latex_export_report_artifact_id=_id_if_present(latex, "latex-export-report"),
        latex_safety_report_artifact_id=_id_if_present(latex, "latex-safety-report"),
        latex_compile_check_report_artifact_id=_id_if_present(
            latex, "latex-compile-check-report"
        ),
        paper_critic_report_artifact_id=_id_if_present(critic, "paper-critic-report"),
        paper_revision_plan_artifact_id=_id_if_present(revision, "paper-revision-plan"),
        revision_safety_report_artifact_id=_id_if_present(
            revision, "revision-safety-report"
        ),
        revised_manuscript_draft_artifact_id=_id_if_present(
            revision, "revised-manuscript-draft"
        ),
        paper_revision_result_artifact_id=_id_if_present(
            revision, "paper-revision-result"
        ),
    )
    return _bundle_with_artifact_ids(bundle)


def _bundle_with_artifact_ids(bundle: FullPaperArtifactBundle) -> FullPaperArtifactBundle:
    ids = sorted(
        {
            value
            for key, value in bundle.model_dump(mode="json").items()
            if key.endswith("_artifact_id") and isinstance(value, str)
        }
    )
    return bundle.model_copy(update={"artifact_ids": ids})


def _latest_refs(
    ledger: ResearchLedger,
    run_id: str,
    action_type: ControllerActionType,
) -> dict[str, ArtifactRef]:
    commits = [
        commit for commit in ledger.list_commits(run_id)
        if commit.action_type == action_type
    ]
    if not commits:
        return {}
    return {artifact.id: artifact for artifact in commits[-1].artifact_refs}


def _citation_refs(ledger: ResearchLedger, run_id: str) -> dict[str, ArtifactRef]:
    draft = _latest_refs(ledger, run_id, ControllerActionType.MANUSCRIPT_DRAFT_WRITTEN)
    refs = {
        key: artifact for key, artifact in draft.items()
        if key in {
            "citation-registry",
            "literature-positioning-report",
            "citation-safety-report",
        }
    }
    standalone = _latest_refs(ledger, run_id, ControllerActionType.CITATION_REGISTRY_WRITTEN)
    return {**standalone, **refs}


def _step(
    name: str,
    status: FullPaperGenerationStepStatus,
    summary: str,
    refs: dict[str, ArtifactRef],
    warnings: list[str] | None = None,
    error_message: str | None = None,
) -> FullPaperGenerationStep:
    return FullPaperGenerationStep(
        step_name=name,
        status=status,
        summary=summary,
        artifact_ids=sorted(refs),
        warnings=sorted(set(warnings or [])),
        error_message=error_message,
    )


def _status_from_warnings(warnings: list[str]) -> FullPaperGenerationStepStatus:
    return (
        FullPaperGenerationStepStatus.SUCCEEDED_WITH_WARNINGS
        if warnings
        else FullPaperGenerationStepStatus.SUCCEEDED
    )


def _generation_status(
    steps: list[FullPaperGenerationStep],
    warnings: list[str],
) -> FullPaperGenerationStatus:
    statuses = {step.status for step in steps}
    if FullPaperGenerationStepStatus.FAILED in statuses:
        return FullPaperGenerationStatus.PAPER_GENERATION_FAILED
    if FullPaperGenerationStepStatus.BLOCKED in statuses:
        return FullPaperGenerationStatus.PAPER_GENERATION_BLOCKED
    if warnings or FullPaperGenerationStepStatus.SUCCEEDED_WITH_WARNINGS in statuses:
        return FullPaperGenerationStatus.PAPER_GENERATION_SUCCEEDED_WITH_WARNINGS
    return FullPaperGenerationStatus.PAPER_GENERATION_SUCCEEDED


def _aggregate_warnings(steps: list[FullPaperGenerationStep]) -> list[str]:
    warnings = [warning for step in steps for warning in step.warnings]
    warnings.append(
        "Generated paper package artifacts are manuscript/presentation context only and "
        "cannot create evidence, upgrade labels, or imply publication readiness."
    )
    return sorted(set(warnings))


def _aggregate_post_repair_warnings(
    steps: list[FullPaperGenerationStep],
    revision_result: PaperRevisionRunResult,
) -> list[str]:
    warnings = _post_repair_warnings(revision_result)
    current_step_names = {"citation-registry", "reexport-latex-after-revision"}
    warnings.extend(
        warning
        for step in steps
        if step.step_name in current_step_names
        for warning in step.warnings
    )
    warnings.append(
        "Generated paper package artifacts are manuscript/presentation context only and "
        "cannot create evidence, upgrade labels, or imply publication readiness."
    )
    return sorted(set(warnings))


def _blocking_issues(steps: list[FullPaperGenerationStep]) -> list[str]:
    return [
        step.error_message or step.summary
        for step in steps
        if step.status in {
            FullPaperGenerationStepStatus.BLOCKED,
            FullPaperGenerationStepStatus.FAILED,
        }
    ]


def _revision_warnings(
    result: PaperRevisionRunResult,
    *,
    safe_repair_mode: bool,
) -> list[str]:
    if safe_repair_mode and result.revision_result is not None:
        return _post_repair_warnings(result)
    warnings = list(result.revision_plan.warnings)
    if result.revision_result is not None:
        warnings.extend(result.revision_result.safety_report.warnings)
        warnings.extend(result.revision_result.safety_report.reasons)
    return sorted(set(warnings))


def _post_repair_warnings(result: PaperRevisionRunResult) -> list[str]:
    warnings = _critic_report_warnings(result.critic_report)
    if result.revision_result is not None:
        warnings.extend(result.revision_result.safety_report.warnings)
        warnings.extend(result.revision_result.safety_report.reasons)
    return sorted(set(warnings))


def _critic_warnings(result: PaperCriticRunResult) -> list[str]:
    return _critic_report_warnings(result.critic_report)


def _critic_report_warnings(report: PaperCriticReport) -> list[str]:
    warnings = [
        finding.message for finding in report.findings
        if finding.severity.value in {"Warning", "Major", "Blocking"}
    ]
    return sorted(set(warnings))


def _refs_from_drafting_result(result: ManuscriptDraftingRunResult) -> dict[str, ArtifactRef]:
    refs = {
        "manuscript-drafting-plan": result.plan_artifact,
        "manuscript-drafting-report": result.drafting_report_artifact,
        "complete-manuscript-draft": result.markdown_artifact,
        "manuscript-assembly-report": result.assembly_report_artifact,
        "citation-registry": result.citation_registry_artifact,
        "literature-positioning-report": result.literature_positioning_artifact,
        "citation-safety-report": result.citation_safety_artifact,
    }
    return {key: artifact for key, artifact in refs.items() if artifact is not None}


def _refs_from_latex_result(result: LatexExportRunResult) -> dict[str, ArtifactRef]:
    refs = {
        "paper": result.paper_artifact,
        "references": result.bibliography_artifact,
        "latex-source-map": result.source_map_artifact,
        "latex-export-report": result.export_report_artifact,
        "latex-safety-report": result.safety_report_artifact,
        "latex-compile-check-report": result.compile_check_artifact,
    }
    return {key: artifact for key, artifact in refs.items() if artifact is not None}


def _refs_from_critic_result(result: PaperCriticRunResult) -> dict[str, ArtifactRef]:
    return (
        {"paper-critic-report": result.critic_report_artifact}
        if result.critic_report_artifact is not None
        else {}
    )


def _refs_from_revision_result(result: PaperRevisionRunResult) -> dict[str, ArtifactRef]:
    refs = {
        (
            result.critic_report_artifact.id
            if result.critic_report_artifact is not None
            else "paper-critic-report"
        ): result.critic_report_artifact,
        "paper-revision-plan": result.revision_plan_artifact,
        "revision-safety-report": result.revision_safety_artifact,
        "revised-manuscript-draft": result.revised_markdown_artifact,
        "safe-repair-report": result.safe_repair_report_artifact,
    }
    return {key: artifact for key, artifact in refs.items() if artifact is not None}


def _id_if_present(refs: dict[str, ArtifactRef], artifact_id: str) -> str | None:
    return artifact_id if artifact_id in refs else None


def _read_text_artifact(root: str | Path, artifact: ArtifactRef) -> str:
    return (Path(root) / artifact.path).read_text(encoding="utf-8")


def _clear_manuscript_error(message: str) -> str:
    if "Manuscript planning artifacts not found" in message:
        return "No manuscript plan found. Run plan-manuscript first."
    return message


__all__ = [
    "FullPaperGenerationError",
    "FullPaperGenerationRunResult",
    "PaperBundleInspectionError",
    "full_paper_generation_result_model",
    "generate_full_paper",
    "inspect_paper_bundle_summary",
    "lint_paper_bundle_summary",
]
