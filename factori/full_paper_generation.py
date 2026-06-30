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
    build_claim_support_audit,
    repair_missing_required_citations_with_accepted_sources,
    validate_citation_usage,
    write_citation_registry_reports,
)
from factori.claim_adjudication import ClaimAdjudicator
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
    ClaimSupportAuditReport,
    ControllerActionType,
    FullPaperArtifactBundle,
    FullPaperGenerationConfig,
    FullPaperGenerationReport,
    FullPaperGenerationResult,
    FullPaperGenerationStatus,
    FullPaperGenerationStep,
    FullPaperGenerationStepStatus,
    FullPaperReleaseReport,
    HumanReviewArtifact,
    PaperCriticReport,
    QualityRepairReport,
    RerunPolicy,
    RetrievalQualityReport,
    ReviewerBundleSummary,
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
QUALITY_SECTION_DEPTH_TARGETS: dict[str, dict[str, int]] = {
    "Abstract": {"min_words": 130, "max_words": 190},
    "Introduction and Problem Framing": {"min_words": 220, "max_words": 320},
    "Method and Model": {"min_words": 220, "max_words": 320},
    "Claim and Evidence Boundaries": {"min_words": 160, "max_words": 240},
    "Demonstration Status": {"min_words": 150, "max_words": 230},
    "Limitations": {"min_words": 180, "max_words": 260},
    "Conclusion": {"min_words": 150, "max_words": 220},
}
_APPENDIX_HEADING_FRAGMENTS = (
    "appendix",
    "bibliography",
    "references",
)
_CONCRETE_LIMITATION_PATTERNS = (
    "accepted registry source",
    "accepted source",
    "accepted_source_count",
    "rejected source",
    "hard-rejected source",
    "hard rejected source",
    "no proof artifact",
    "proof artifact",
    "no experiment artifact",
    "experiment artifact",
    "human validation",
    "human-review artifact",
    "bounded retrieval",
    "retrieval is bounded",
    "publication_ready=false",
)
_IRREDUCIBLE_QUALITY_WARNING_PREFIXES = (
    "Some retrieved sources were rejected by bounded quality filtering.",
    "Retrieval adequacy is bounded background context only, not validation.",
    "Appendices increase the total heading count but do not fragment the main body.",
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
    quality_repair_report_artifact: ArtifactRef | None = None
    claim_adjudicator: ClaimAdjudicator | None = None


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
    claim_adjudicator: ClaimAdjudicator | None = None,
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
    quality_repair_report: QualityRepairReport | None = None
    quality_repaired_markdown: str | None = None
    claim_support_audit: ClaimSupportAuditReport | None = None

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
                claim_adjudicator=claim_adjudicator,
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

    quality_repair_report, quality_repaired_markdown, claim_support_audit = (
        _run_quality_repair_if_requested(
            run_id=run_id,
            root=root,
            ledger=ledger,
            config=config,
            revision_result=revision_result,
            claim_adjudicator=claim_adjudicator,
        )
    )
    if quality_repair_report is not None:
        if (
            quality_repaired_markdown is not None
            and revision_result is not None
            and revision_result.revision_result is not None
        ):
            revision_result = _revision_result_with_markdown(
                revision_result,
                quality_repaired_markdown,
            )
        steps.append(
            _step(
                "quality-repair",
                _quality_repair_step_status(quality_repair_report),
                "Bounded deterministic quality repair was evaluated.",
                {},
                _quality_repair_step_warnings(quality_repair_report),
                (
                    "Quality repair could not complete safely."
                    if quality_repair_report.quality_repair_status
                    in {"blocked", "failed"}
                    else None
                ),
            )
        )
    else:
        steps.append(
            _step(
                "quality-repair",
                FullPaperGenerationStepStatus.SKIPPED,
                "Quality repair was skipped by configuration.",
                {},
            )
        )

    reexport_requested = (
        config.reexport_latex_after_revision
        or enable_safe_repair
        or (config.export_latex and quality_repaired_markdown is not None)
    )
    if reexport_requested:
        if (
            quality_repaired_markdown is None
            and (revision_result is None or revision_result.revision_result is None)
        ):
            raise FullPaperGenerationError(
                "LaTeX re-export after revision requires --apply-safe-fake-revision."
            )
        try:
            revised_export = _export_revised_markdown_to_latex(
                run_id=run_id,
                root=root,
                ledger=ledger,
                revision_result=revision_result,
                revised_markdown=quality_repaired_markdown,
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

    if claim_support_audit is None:
        claim_support_audit = _build_claim_support_audit_for_generation(
            run_id=run_id,
            root=root,
            ledger=ledger,
            revision_result=revision_result,
            claim_adjudicator=claim_adjudicator,
        )
    claim_support_warnings = _claim_support_warnings(claim_support_audit)
    steps.append(
        _step(
            "claim-support-audit",
            _status_from_warnings(claim_support_warnings),
            "Claim-to-source support and citation placement were audited.",
            {},
            claim_support_warnings,
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
            claim_adjudicator=claim_adjudicator,
        )

    persistence = _write_full_paper_generation_artifacts(
        run_id=run_id,
        store=store,
        ledger=ledger,
        report=report,
        bundle=bundle,
        revised_export=revised_export,
        claim_support_audit=claim_support_audit,
        quality_repair_report=quality_repair_report,
        quality_repaired_markdown=quality_repaired_markdown,
    )
    return _with_persisted_generation_artifacts(
        run_id=run_id,
        report=report,
        bundle=bundle,
        drafting_result=drafting_result,
        latex_result=latex_result,
        critic_result=critic_result,
        revision_result=revision_result,
        claim_adjudicator=claim_adjudicator,
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
    quality_repair_report = _read_quality_repair_report(
        paths["quality_repair_report"]
    )
    human_review_artifact = _read_human_review_artifact(paths["human_review_artifact"])
    reviewer_bundle_summary = _read_preferred_reviewer_bundle_summary(paths)
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
    retrieval_quality_report = _read_retrieval_quality_report(
        paths["retrieval_quality_report"]
    )
    marker_keys = sorted(set(CITATION_MARKER_RE.findall(markdown)))
    if citation_registry is not None:
        citation_safety = validate_citation_usage(markdown, citation_registry)
        unregistered_citation_keys = citation_safety.unregistered_citation_keys
        registry_backed_citation_count = citation_safety.registry_backed_citation_count
        bibliography_registry_backed = citation_safety.bibliography_registry_backed
        citation_policy = citation_registry.citation_policy
        registry_source_count = len(citation_registry.citations)
        citation_registry_sources_all_accepted = all(
            record.accepted_for_registry for record in citation_registry.citations
        )
    else:
        unregistered_citation_keys = marker_keys
        registry_backed_citation_count = 0
        bibliography_registry_backed = False
        citation_policy = "none"
        registry_source_count = 0
        citation_registry_sources_all_accepted = True
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
    claim_support_audit = _read_claim_support_audit(paths["claim_support_audit"])
    claim_support_counts = (
        claim_support_audit.summary_counts if claim_support_audit is not None else {}
    )
    claim_support_placement = (
        claim_support_audit.citation_placement_violations
        if claim_support_audit is not None
        else []
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
        "quality_repair_report_exists": paths["quality_repair_report"].is_file(),
        "quality_repair_report_present": quality_repair_report is not None,
        "human_review_artifact_exists": paths["human_review_artifact"].is_file(),
        "human_review_artifact_present": human_review_artifact is not None,
        "human_review_status": (
            human_review_artifact.review_status if human_review_artifact is not None else None
        ),
        "human_review_artifact_path": (
            paths["human_review_artifact"].relative_to(root_path).as_posix()
            if human_review_artifact is not None
            else None
        ),
        "human_review_blocking_concern_count": (
            len(human_review_artifact.blocking_concerns)
            if human_review_artifact is not None
            else 0
        ),
        "human_review_requested_change_count": (
            len(human_review_artifact.requested_changes)
            if human_review_artifact is not None
            else 0
        ),
        "human_review_recommended_next_action": (
            human_review_artifact.recommended_next_action
            if human_review_artifact is not None
            else None
        ),
        "reviewer_bundle_summary_exists": (
            paths["reviewer_bundle_summary_json"].is_file()
        ),
        "reviewer_bundle_summary_after_human_review_exists": (
            paths["reviewer_bundle_summary_after_human_review_json"].is_file()
        ),
        "reviewer_bundle_summary_markdown_exists": (
            paths["reviewer_bundle_summary_markdown"].is_file()
        ),
        "reviewer_bundle_summary_present": reviewer_bundle_summary is not None,
        "reviewer_summary_status": (
            "present" if reviewer_bundle_summary is not None else "absent"
        ),
        "reviewer_summary_evidence_gap_count": (
            len(reviewer_bundle_summary.evidence_gaps)
            if reviewer_bundle_summary is not None
            else 0
        ),
        "reviewer_summary_human_checklist_count": (
            len(reviewer_bundle_summary.human_review_checklist)
            if reviewer_bundle_summary is not None
            else 0
        ),
        "reviewer_summary_recommended_action_count": (
            len(reviewer_bundle_summary.recommended_next_actions)
            if reviewer_bundle_summary is not None
            else 0
        ),
        "retrieval_quality_report_exists": paths["retrieval_quality_report"].is_file(),
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
        "quality_repair_backend": (
            quality_repair_report.quality_repair_backend
            if quality_repair_report is not None
            else "off"
        ),
        "quality_repair_status": (
            quality_repair_report.quality_repair_status
            if quality_repair_report is not None
            else "disabled"
        ),
        "quality_repaired_section_count": (
            len(quality_repair_report.sections_repaired)
            if quality_repair_report is not None
            else 0
        ),
        "quality_status_before_repair": (
            quality_repair_report.before_quality_status
            if quality_repair_report is not None
            else None
        ),
        "quality_status_after_repair": (
            quality_repair_report.after_quality_status
            if quality_repair_report is not None
            else None
        ),
        "section_depth_targets_present": bool(
            quality_repair_report and quality_repair_report.section_depth_targets
        ),
        "section_depth_targets": (
            quality_repair_report.section_depth_targets
            if quality_repair_report is not None
            else {}
        ),
        "section_depth_target_total": (
            len(quality_repair_report.section_depth_targets)
            if quality_repair_report is not None
            else 0
        ),
        "section_depth_target_met_count": (
            len(quality_repair_report.section_depth_targets)
            - len(quality_repair_report.sections_below_target_after)
            if quality_repair_report is not None
            else 0
        ),
        "sections_below_depth_target": (
            quality_repair_report.sections_below_target_after
            if quality_repair_report is not None
            else []
        ),
        "placeholder_sections_after_quality_repair": (
            quality_repair_report.placeholder_like_sections_after
            if quality_repair_report is not None
            else []
        ),
        "warnings_reduced_count": (
            quality_repair_report.warnings_reduced_count
            if quality_repair_report is not None
            else 0
        ),
        "irreducible_quality_warnings": (
            quality_repair_report.irreducible_warnings
            if quality_repair_report is not None
            else []
        ),
        "claim_support_rechecked_after_quality_repair": bool(
            quality_repair_report
            and quality_repair_report.claim_support_rechecked_after_repair
        ),
        "citation_safety_rechecked_after_quality_repair": bool(
            quality_repair_report
            and quality_repair_report.citation_safety_rechecked_after_repair
        ),
        "citation_registry_present": paths["citation_registry"].is_file(),
        "citation_registry_source_count": registry_source_count,
        "citation_registry_sources_all_accepted": (
            citation_registry_sources_all_accepted
        ),
        "registry_backed_citation_count": registry_backed_citation_count,
        "unregistered_citation_keys": unregistered_citation_keys,
        "bibliography_registry_backed": bibliography_registry_backed,
        "bibliography_status": bibliography_status,
        "citation_policy": citation_policy,
        "retrieval_quality_report_present": (
            retrieval_quality_report is not None
        ),
        "retrieved_source_count": (
            retrieval_quality_report.total_retrieved_sources
            if retrieval_quality_report is not None
            else 0
        ),
        "accepted_source_count": (
            retrieval_quality_report.accepted_source_count
            if retrieval_quality_report is not None
            else 0
        ),
        "rejected_source_count": (
            retrieval_quality_report.rejected_source_count
            if retrieval_quality_report is not None
            else 0
        ),
        "retrieval_adequacy_status": (
            retrieval_quality_report.adequacy_status
            if retrieval_quality_report is not None
            else "not_evaluated"
        ),
        "retrieval_quality_duplicate_count": (
            retrieval_quality_report.duplicate_count
            if retrieval_quality_report is not None
            else 0
        ),
        "retrieval_quality_low_relevance_count": (
            retrieval_quality_report.low_relevance_count
            if retrieval_quality_report is not None
            else 0
        ),
        "retrieval_quality_metadata_incomplete_count": (
            retrieval_quality_report.metadata_incomplete_count
            if retrieval_quality_report is not None
            else 0
        ),
        "source_relevance_adjudication_enabled": bool(
            retrieval_quality_report
            and retrieval_quality_report.source_relevance_adjudication_enabled
        ),
        "source_relevance_adjudicator_backend": (
            retrieval_quality_report.source_relevance_adjudicator_backend
            if retrieval_quality_report is not None
            else "off"
        ),
        "source_relevance_adjudicator_model": (
            retrieval_quality_report.source_relevance_adjudicator_model
            if retrieval_quality_report is not None
            else None
        ),
        "source_relevance_adjudication_calls": (
            retrieval_quality_report.source_relevance_adjudication_calls
            if retrieval_quality_report is not None
            else 0
        ),
        "source_relevance_adjudicated_count": (
            retrieval_quality_report.adjudicated_source_count
            if retrieval_quality_report is not None
            else 0
        ),
        "source_relevance_llm_accepted_count": (
            retrieval_quality_report.llm_accepted_count
            if retrieval_quality_report is not None
            else 0
        ),
        "source_relevance_llm_rejected_count": (
            retrieval_quality_report.llm_rejected_count
            if retrieval_quality_report is not None
            else 0
        ),
        "source_relevance_hard_reject_count": (
            retrieval_quality_report.hard_reject_count
            if retrieval_quality_report is not None
            else 0
        ),
        "claim_support_audit_present": paths["claim_support_audit"].is_file(),
        "claim_support_total_sentences": int(
            claim_support_counts.get("total_sentences", 0)
        ),
        "claim_support_registry_supported_count": int(
            claim_support_counts.get("registry_supported", 0)
        ),
        "claim_support_scaffold_not_required_count": int(
            claim_support_counts.get("scaffold_not_required", 0)
        ),
        "claim_support_missing_required_citation_count": int(
            claim_support_counts.get("missing_required_citation", 0)
        ),
        "claim_support_scope_mismatch_count": int(
            claim_support_counts.get("scope_mismatch", 0)
        ),
        "claim_support_forbidden_claim_count": int(
            claim_support_counts.get("forbidden_claim", 0)
        ),
        "citation_placement_violations": list(claim_support_placement),
        "citation_as_validation_misuse_count": int(
            claim_support_counts.get("citation_as_validation_misuse", 0)
        ),
        "claim_adjudication_enabled": bool(
            claim_support_audit and claim_support_audit.claim_adjudication_enabled
        ),
        "claim_adjudicator_backend": (
            claim_support_audit.claim_adjudicator_backend
            if claim_support_audit is not None
            else "off"
        ),
        "claim_adjudication_calls": (
            claim_support_audit.claim_adjudication_calls
            if claim_support_audit is not None
            else 0
        ),
        "adjudicated_sentence_count": (
            claim_support_audit.adjudicated_sentence_count
            if claim_support_audit is not None
            else 0
        ),
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
    _markdown_override: str | None = None,
    _claim_support_audit_override: ClaimSupportAuditReport | None = None,
    _citation_safety_override: Any | None = None,
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
    if _markdown_override is not None:
        markdown = _markdown_override
    elif isinstance(primary, str) and primary.endswith(".md"):
        primary_path = root_path / primary
        if primary_path.is_file():
            markdown = primary_path.read_text(encoding="utf-8")
    if _markdown_override is not None:
        override_stats = _manuscript_stats(markdown)
        override_sections = _markdown_sections(markdown)
        override_accounting = _section_accounting(
            override_sections,
            override_stats.get("title_detected"),
        )
        bundle.update(override_stats)
        bundle.update(_public_section_accounting(override_accounting))
        bundle["unregistered_citation_keys"] = sorted(
            set(CITATION_MARKER_RE.findall(markdown))
        )
        if _citation_safety_override is not None:
            bundle["unregistered_citation_keys"] = list(
                _citation_safety_override.unregistered_citation_keys
            )
            bundle["registry_backed_citation_count"] = int(
                _citation_safety_override.registry_backed_citation_count
            )
            bundle["bibliography_registry_backed"] = bool(
                _citation_safety_override.bibliography_registry_backed
            )
            bibliography_present = bool(
                re.search(
                    r"^#{1,6}\s+(bibliography|references)\s*$",
                    markdown,
                    re.I | re.M,
                )
            )
            bundle["bibliography_status"] = (
                "registry-backed"
                if bibliography_present
                and _citation_safety_override.bibliography_registry_backed
                else "unsafe"
                if bibliography_present
                else "absent"
            )
        if _claim_support_audit_override is not None:
            counts = _claim_support_audit_override.summary_counts
            bundle["claim_support_audit_present"] = True
            bundle["claim_support_total_sentences"] = int(
                counts.get("total_sentences", 0)
            )
            bundle["claim_support_registry_supported_count"] = int(
                counts.get("registry_supported", 0)
            )
            bundle["claim_support_scaffold_not_required_count"] = int(
                counts.get("scaffold_not_required", 0)
            )
            bundle["claim_support_missing_required_citation_count"] = int(
                counts.get("missing_required_citation", 0)
            )
            bundle["claim_support_scope_mismatch_count"] = int(
                counts.get("scope_mismatch", 0)
            )
            bundle["claim_support_forbidden_claim_count"] = int(
                counts.get("forbidden_claim", 0)
            )
            bundle["citation_placement_violations"] = list(
                _claim_support_audit_override.citation_placement_violations
            )
            bundle["citation_as_validation_misuse_count"] = int(
                counts.get("citation_as_validation_misuse", 0)
            )
            bundle["claim_adjudication_enabled"] = bool(
                _claim_support_audit_override.claim_adjudication_enabled
            )
            bundle["claim_adjudicator_backend"] = (
                _claim_support_audit_override.claim_adjudicator_backend
            )
            bundle["claim_adjudication_calls"] = (
                _claim_support_audit_override.claim_adjudication_calls
            )
            bundle["adjudicated_sentence_count"] = (
                _claim_support_audit_override.adjudicated_sentence_count
            )

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
    quality_repair_report_present = bool(
        bundle.get("quality_repair_report_present")
    )
    quality_repair_backend = str(bundle.get("quality_repair_backend") or "off")
    quality_repair_status = str(bundle.get("quality_repair_status") or "disabled")
    quality_repaired_section_count = int(
        bundle.get("quality_repaired_section_count") or 0
    )
    quality_status_before_repair = bundle.get("quality_status_before_repair")
    quality_status_after_repair = bundle.get("quality_status_after_repair")
    claim_support_rechecked_after_quality_repair = bool(
        bundle.get("claim_support_rechecked_after_quality_repair")
    )
    citation_safety_rechecked_after_quality_repair = bool(
        bundle.get("citation_safety_rechecked_after_quality_repair")
    )
    citation_registry_present = bool(bundle.get("citation_registry_present"))
    citation_registry_source_count = int(
        bundle.get("citation_registry_source_count") or 0
    )
    citation_registry_sources_all_accepted = bool(
        bundle.get("citation_registry_sources_all_accepted", True)
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
    retrieval_quality_report_present = bool(
        bundle.get("retrieval_quality_report_present")
    )
    retrieved_source_count = int(bundle.get("retrieved_source_count") or 0)
    accepted_source_count = int(bundle.get("accepted_source_count") or 0)
    rejected_source_count = int(bundle.get("rejected_source_count") or 0)
    retrieval_adequacy_status = str(
        bundle.get("retrieval_adequacy_status") or "not_evaluated"
    )
    source_relevance_adjudication_enabled = bool(
        bundle.get("source_relevance_adjudication_enabled")
    )
    source_relevance_adjudicator_backend = str(
        bundle.get("source_relevance_adjudicator_backend") or "off"
    )
    source_relevance_adjudicated_count = int(
        bundle.get("source_relevance_adjudicated_count") or 0
    )
    source_relevance_llm_accepted_count = int(
        bundle.get("source_relevance_llm_accepted_count") or 0
    )
    source_relevance_llm_rejected_count = int(
        bundle.get("source_relevance_llm_rejected_count") or 0
    )
    source_relevance_hard_reject_count = int(
        bundle.get("source_relevance_hard_reject_count") or 0
    )
    claim_support_audit_present = bool(bundle.get("claim_support_audit_present"))
    claim_support_total_sentences = int(
        bundle.get("claim_support_total_sentences") or 0
    )
    claim_support_registry_supported_count = int(
        bundle.get("claim_support_registry_supported_count") or 0
    )
    claim_support_scaffold_not_required_count = int(
        bundle.get("claim_support_scaffold_not_required_count") or 0
    )
    claim_support_missing_required_citation_count = int(
        bundle.get("claim_support_missing_required_citation_count") or 0
    )
    claim_support_scope_mismatch_count = int(
        bundle.get("claim_support_scope_mismatch_count") or 0
    )
    claim_support_forbidden_claim_count = int(
        bundle.get("claim_support_forbidden_claim_count") or 0
    )
    citation_placement_violations = list(
        bundle.get("citation_placement_violations") or []
    )
    citation_as_validation_misuse_count = int(
        bundle.get("citation_as_validation_misuse_count") or 0
    )
    claim_adjudication_enabled = bool(bundle.get("claim_adjudication_enabled"))
    claim_adjudicator_backend = str(bundle.get("claim_adjudicator_backend") or "off")
    claim_adjudication_calls = int(bundle.get("claim_adjudication_calls") or 0)
    adjudicated_sentence_count = int(bundle.get("adjudicated_sentence_count") or 0)
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
    section_word_counts = _canonical_section_word_counts(body_sections)
    depth_summary = _quality_depth_target_summary(section_word_counts)
    sections_below_depth_target = list(depth_summary["sections_below_depth_target"])
    sections_too_short = [
        {"heading": section["heading"], "word_count": section["word_count"]}
        for section in body_sections
        if section["word_count"] < min_avg_words_per_section
    ]
    empty_or_placeholder_sections = [
        {"heading": section["heading"], "word_count": section["word_count"]}
        for section in body_sections
        if _section_is_placeholder_like(section)
    ]
    placeholder_like_section_headings = [
        str(section["heading"]) for section in empty_or_placeholder_sections
    ]
    limitations_concrete_constraint_count = max(
        (
            _concrete_limitation_constraint_count(str(section["body"]))
            for section in body_sections
            if _canonical_main_body_heading(str(section["heading"])) == "Limitations"
        ),
        default=0,
    )
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
        and _section_is_placeholder_like(conclusion_section)
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
    if citation_registry_present and not citation_registry_sources_all_accepted:
        failure_reasons.append(
            "Citation registry includes a source rejected by retrieval quality filtering."
        )
    if (
        retrieval_quality_report_present
        and citation_policy == "registry-only"
        and accepted_source_count == 0
    ):
        failure_reasons.append(
            "Registry-only citation policy has no retrieval-quality accepted sources."
        )
    if retrieval_quality_report_present and rejected_source_count:
        development_warnings.append(
            "Some retrieved sources were rejected by bounded quality filtering."
        )
    if retrieval_quality_report_present and retrieval_adequacy_status in {
        "insufficient_sources",
        "bounded_context_only",
    }:
        development_warnings.append(
            "Retrieval adequacy is bounded background context only, not validation."
        )
    if citation_registry_present and not claim_support_audit_present:
        development_warnings.append(
            "Citation registry is present but no claim-support audit was found."
        )
    if claim_support_missing_required_citation_count:
        failure_reasons.append(
            "Claim-support audit found external/source claims without local citations."
        )
    if claim_support_scope_mismatch_count:
        failure_reasons.append(
            "Claim-support audit found citation scope mismatches."
        )
    if claim_support_forbidden_claim_count:
        failure_reasons.append(
            "Claim-support audit found forbidden proof, experiment, novelty, or "
            "publication-readiness claims."
        )
    if citation_as_validation_misuse_count:
        failure_reasons.append(
            "Claim-support audit found citations used as validation or proof."
        )
    if citation_placement_violations:
        failure_reasons.append(
            "Citation placement violations are present."
        )
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
    if sections_too_short or (
        quality_repair_report_present and sections_below_depth_target
    ):
        development_warnings.append("One or more sections may be underdeveloped.")
    if placeholder_sections_detected:
        development_warnings.append("One or more sections are empty or placeholder-like.")
    if mostly_placeholder_sections:
        failure_reasons.append("Most body sections are empty or placeholder text.")
    if too_many_sections_for_length:
        development_warnings.append("Too many headings for the amount of content.")
    elif (
        appendix_section_count
        and total_heading_count > max_section_count_for_short_draft
        and word_count < min_words
    ):
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
    irreducible_quality_warnings = _irreducible_quality_warnings(development_warnings)
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
        "section_depth_targets_present": True,
        "section_depth_targets": QUALITY_SECTION_DEPTH_TARGETS,
        "section_word_counts": section_word_counts,
        "section_depth_target_total": depth_summary[
            "section_depth_target_total"
        ],
        "section_depth_target_met_count": depth_summary[
            "section_depth_target_met_count"
        ],
        "sections_below_depth_target": sections_below_depth_target,
        "placeholder_sections_after_quality_repair": (
            placeholder_like_section_headings
        ),
        "warnings_reduced_count": int(bundle.get("warnings_reduced_count") or 0),
        "irreducible_quality_warnings": irreducible_quality_warnings,
        "limitations_concrete_constraint_count": limitations_concrete_constraint_count,
        "title_detected": title_text or None,
        "title_is_placeholder": title_is_placeholder,
        "abstract_present": abstract_present,
        "citation_marker_count": citation_marker_count,
        "citations_present": citations_present,
        "quality_repair_report_present": quality_repair_report_present,
        "quality_repair_backend": quality_repair_backend,
        "quality_repair_status": quality_repair_status,
        "quality_repaired_section_count": quality_repaired_section_count,
        "quality_status_before_repair": quality_status_before_repair,
        "quality_status_after_repair": quality_status_after_repair,
        "quality_repair_warnings_reduced_count": int(
            bundle.get("warnings_reduced_count") or 0
        ),
        "quality_repair_irreducible_warnings": list(
            bundle.get("irreducible_quality_warnings") or []
        ),
        "claim_support_rechecked_after_quality_repair": (
            claim_support_rechecked_after_quality_repair
        ),
        "citation_safety_rechecked_after_quality_repair": (
            citation_safety_rechecked_after_quality_repair
        ),
        "reviewer_bundle_summary_present": bool(
            bundle.get("reviewer_bundle_summary_present")
        ),
        "reviewer_summary_evidence_gap_count": int(
            bundle.get("reviewer_summary_evidence_gap_count") or 0
        ),
        "reviewer_summary_human_checklist_count": int(
            bundle.get("reviewer_summary_human_checklist_count") or 0
        ),
        "reviewer_summary_recommended_action_count": int(
            bundle.get("reviewer_summary_recommended_action_count") or 0
        ),
        "human_review_artifact_present": bool(
            bundle.get("human_review_artifact_present")
        ),
        "human_review_status": bundle.get("human_review_status"),
        "human_review_blocking_concern_count": int(
            bundle.get("human_review_blocking_concern_count") or 0
        ),
        "human_review_requested_change_count": int(
            bundle.get("human_review_requested_change_count") or 0
        ),
        "human_review_recommended_next_action": bundle.get(
            "human_review_recommended_next_action"
        ),
        "citation_registry_present": citation_registry_present,
        "citation_registry_source_count": citation_registry_source_count,
        "citation_registry_sources_all_accepted": (
            citation_registry_sources_all_accepted
        ),
        "registry_backed_citation_count": registry_backed_citation_count,
        "unregistered_citation_keys": unregistered_citation_keys,
        "bibliography_registry_backed": bibliography_registry_backed,
        "bibliography_status": bundle.get("bibliography_status", "absent"),
        "citation_policy": citation_policy,
        "retrieval_quality_report_present": retrieval_quality_report_present,
        "retrieved_source_count": retrieved_source_count,
        "accepted_source_count": accepted_source_count,
        "rejected_source_count": rejected_source_count,
        "retrieval_adequacy_status": retrieval_adequacy_status,
        "source_relevance_adjudication_enabled": (
            source_relevance_adjudication_enabled
        ),
        "source_relevance_adjudicator_backend": (
            source_relevance_adjudicator_backend
        ),
        "source_relevance_adjudicated_count": (
            source_relevance_adjudicated_count
        ),
        "source_relevance_llm_accepted_count": (
            source_relevance_llm_accepted_count
        ),
        "source_relevance_llm_rejected_count": (
            source_relevance_llm_rejected_count
        ),
        "source_relevance_hard_reject_count": (
            source_relevance_hard_reject_count
        ),
        "claim_support_audit_present": claim_support_audit_present,
        "claim_support_total_sentences": claim_support_total_sentences,
        "claim_support_registry_supported_count": (
            claim_support_registry_supported_count
        ),
        "claim_support_scaffold_not_required_count": (
            claim_support_scaffold_not_required_count
        ),
        "claim_support_missing_required_citation_count": (
            claim_support_missing_required_citation_count
        ),
        "claim_support_scope_mismatch_count": claim_support_scope_mismatch_count,
        "claim_support_forbidden_claim_count": claim_support_forbidden_claim_count,
        "citation_placement_violations": citation_placement_violations,
        "citation_as_validation_misuse_count": citation_as_validation_misuse_count,
        "claim_adjudication_enabled": claim_adjudication_enabled,
        "claim_adjudicator_backend": claim_adjudicator_backend,
        "claim_adjudication_calls": claim_adjudication_calls,
        "adjudicated_sentence_count": adjudicated_sentence_count,
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
        "quality_repair_report": run_path / "reports" / "quality-repair-report.json",
        "human_review_artifact": run_path / "reports" / "human-review-artifact.json",
        "human_review_summary": run_path / "reports" / "human-review-summary.md",
        "reviewer_bundle_summary_json": run_path
        / "reports"
        / "reviewer-bundle-summary.json",
        "reviewer_bundle_summary_markdown": run_path
        / "reports"
        / "reviewer-bundle-summary.md",
        "reviewer_bundle_summary_after_human_review_json": run_path
        / "reports"
        / "reviewer-bundle-summary-after-human-review.json",
        "reviewer_bundle_summary_after_human_review_markdown": run_path
        / "reports"
        / "reviewer-bundle-summary-after-human-review.md",
        "retrieval_report": run_path / "reports" / "retrieval-report.json",
        "retrieval_quality_report": run_path
        / "reports"
        / "retrieval-quality-report.json",
        "citation_registry": run_path / "reports" / "citation-registry.json",
        "claim_support_audit": run_path / "reports" / "claim-support-audit.json",
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


def _read_claim_support_audit(path: Path) -> ClaimSupportAuditReport | None:
    if not path.is_file():
        return None
    try:
        return ClaimSupportAuditReport.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _read_retrieval_quality_report(path: Path) -> RetrievalQualityReport | None:
    if not path.is_file():
        return None
    try:
        return RetrievalQualityReport.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _read_quality_repair_report(path: Path) -> QualityRepairReport | None:
    if not path.is_file():
        return None
    try:
        return QualityRepairReport.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _read_human_review_artifact(path: Path) -> HumanReviewArtifact | None:
    if not path.is_file():
        return None
    try:
        return HumanReviewArtifact.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _read_reviewer_bundle_summary(path: Path) -> ReviewerBundleSummary | None:
    if not path.is_file():
        return None
    try:
        return ReviewerBundleSummary.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _read_preferred_reviewer_bundle_summary(
    paths: dict[str, Path],
) -> ReviewerBundleSummary | None:
    return _read_reviewer_bundle_summary(
        paths["reviewer_bundle_summary_after_human_review_json"]
    ) or _read_reviewer_bundle_summary(paths["reviewer_bundle_summary_json"])


def build_reviewer_bundle_summary(
    *,
    run_id: str,
    root: str | Path = ".",
    release_report: FullPaperReleaseReport | None = None,
    human_review_artifact: HumanReviewArtifact | None = None,
) -> ReviewerBundleSummary:
    """Build a deterministic reviewer-facing summary from final paper reports."""
    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    paths = _paper_bundle_paths(run_path)
    bundle = inspect_paper_bundle_summary(run_id=run_id, root=root_path)
    lint = lint_paper_bundle_summary(run_id=run_id, root=root_path)
    human_review = human_review_artifact or _read_human_review_artifact(
        paths["human_review_artifact"]
    )
    human_review_present = _human_review_counts_as_present(human_review)
    release_status = (
        release_report.decision.status.value
        if release_report is not None
        else str(bundle.get("release_status") or lint.get("paper_release_status") or "unknown")
    )
    release_warnings = (
        list(release_report.decision.warnings) if release_report is not None else []
    )
    release_blocking = (
        list(release_report.decision.blocking_reasons)
        if release_report is not None
        else []
    )
    warning_summary = _reviewer_remaining_warnings(
        lint=lint,
        release_warnings=release_warnings,
        human_review=human_review,
    )
    blocking_issues = sorted(
        set(
            release_blocking
            + list(lint.get("quality_failure_reasons") or [])
            + (list(human_review.blocking_concerns) if human_review else [])
        )
    )
    paper_paths = _reviewer_paper_artifact_paths(bundle)
    audit_paths = _reviewer_audit_artifact_paths(bundle, paths, root_path)
    claim_support_status = _reviewer_claim_support_status(lint)
    citation_status = _reviewer_citation_status(lint)
    safety_status = (
        "safe"
        if not blocking_issues
        and claim_support_status == "clean"
        and citation_status in {"registry-backed", "no-citations-required"}
        else "needs_review"
    )
    source_relevance_status = _reviewer_source_relevance_status(lint)
    return ReviewerBundleSummary(
        run_id=run_id,
        release_status=release_status,
        publication_ready=False,
        safety_status=safety_status,
        quality_status=str(lint.get("quality_status") or "unknown"),
        claim_support_status=claim_support_status,
        citation_status=citation_status,
        retrieval_quality_status=str(
            lint.get("retrieval_adequacy_status") or "not_evaluated"
        ),
        source_relevance_status=source_relevance_status,
        quality_repair_status=str(lint.get("quality_repair_status") or "disabled"),
        paper_artifact_paths=paper_paths,
        audit_artifact_paths=audit_paths,
        remaining_warnings=warning_summary,
        blocking_issues=blocking_issues,
        evidence_boundaries=_reviewer_evidence_boundaries(human_review_present),
        evidence_gaps=_reviewer_evidence_gaps(human_review_present),
        source_limitations=_reviewer_source_limitations(lint),
        claim_support_summary=_reviewer_claim_support_summary(lint),
        citation_summary=_reviewer_citation_summary(lint),
        retrieval_summary=_reviewer_retrieval_summary(lint),
        quality_summary=_reviewer_quality_summary(lint),
        human_review_artifact_present=human_review_present,
        human_review_status=human_review.review_status if human_review else None,
        human_review_artifact_path=(
            paths["human_review_artifact"].relative_to(root_path).as_posix()
            if human_review
            else None
        ),
        human_review_blocking_concern_count=(
            len(human_review.blocking_concerns) if human_review else 0
        ),
        human_review_requested_change_count=(
            len(human_review.requested_changes) if human_review else 0
        ),
        human_review_recommended_next_action=(
            human_review.recommended_next_action if human_review else None
        ),
        human_review_checklist=_reviewer_human_review_checklist(),
        recommended_next_actions=_reviewer_recommended_next_actions(human_review),
        creates_scientific_validation=False,
        implies_publication_readiness=False,
        is_verification_evidence=False,
    )


def render_reviewer_bundle_summary_markdown(
    summary: ReviewerBundleSummary,
) -> str:
    """Render the reviewer bundle summary as deterministic Markdown."""
    lines = [
        "# Reviewer Bundle Summary",
        "",
        f"Run ID: `{summary.run_id}`",
        f"Release: `{summary.release_status}`",
        f"Publication ready: `{str(summary.publication_ready).lower()}`",
        f"Safety: `{summary.safety_status}`",
        f"Quality: `{summary.quality_status}`",
        f"Claim support: `{summary.claim_support_status}`",
        f"Citation status: `{summary.citation_status}`",
        f"Retrieval quality: `{summary.retrieval_quality_status}`",
        f"Source relevance: `{summary.source_relevance_status}`",
        f"Quality repair: `{summary.quality_repair_status}`",
        (
            "Human review: "
            f"`{'present' if summary.human_review_artifact_present else 'absent'}`"
        ),
        f"Human review status: `{summary.human_review_status or 'not_available'}`",
        (
            "Blocking human-review concerns: "
            f"`{summary.human_review_blocking_concern_count}`"
        ),
        (
            "Requested human-review changes: "
            f"`{summary.human_review_requested_change_count}`"
        ),
        (
            "Human-review recommended next action: "
            f"`{summary.human_review_recommended_next_action or 'none'}`"
        ),
        "",
        "## Remaining Warnings",
    ]
    for category, warnings in summary.remaining_warnings.items():
        lines.append(f"### {category.replace('_', ' ').title()}")
        if warnings:
            lines.extend(f"- {warning}" for warning in warnings)
        else:
            lines.append("- none")
    lines.extend(["", "## Blocking Issues"])
    if summary.blocking_issues:
        lines.extend(f"- {issue}" for issue in summary.blocking_issues)
    else:
        lines.append("- none")
    lines.extend(["", "## Evidence Boundaries"])
    for category, items in summary.evidence_boundaries.items():
        lines.append(f"### {category.replace('_', ' ').title()}")
        lines.extend(f"- {item}" for item in items)
    lines.extend(["", "## Evidence Gaps"])
    lines.extend(f"- {gap}" for gap in summary.evidence_gaps)
    lines.extend(["", "## Source Limitations"])
    lines.extend(f"- {item}" for item in summary.source_limitations)
    lines.extend(["", "## Human Review Checklist"])
    lines.extend(f"- {item}" for item in summary.human_review_checklist)
    lines.extend(["", "## Recommended Next Actions"])
    lines.extend(f"- {item}" for item in summary.recommended_next_actions)
    lines.extend(
        [
            "",
            "## Non-Evidence Flags",
            "- creates_scientific_validation: false",
            "- implies_publication_readiness: false",
            "- is_verification_evidence: false",
            "",
        ]
    )
    return "\n".join(lines)


def inspect_reviewer_bundle_summary(
    *,
    run_id: str,
    root: str | Path = ".",
) -> dict[str, Any]:
    """Inspect a persisted reviewer bundle summary without mutating the run."""
    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    if not run_path.is_dir():
        raise PaperBundleInspectionError(f"No run directory found for run_id={run_id}.")
    paths = _paper_bundle_paths(run_path)
    summary = _read_preferred_reviewer_bundle_summary(paths)
    if summary is None:
        raise PaperBundleInspectionError(
            f"No reviewer bundle summary found for run_id={run_id}."
        )
    payload = summary.model_dump(mode="json")
    summary_path = (
        paths["reviewer_bundle_summary_after_human_review_json"]
        if paths["reviewer_bundle_summary_after_human_review_json"].is_file()
        else paths["reviewer_bundle_summary_json"]
    )
    markdown_path = (
        paths["reviewer_bundle_summary_after_human_review_markdown"]
        if paths["reviewer_bundle_summary_after_human_review_markdown"].is_file()
        else paths["reviewer_bundle_summary_markdown"]
    )
    payload["summary_path"] = summary_path.relative_to(root_path).as_posix()
    payload["markdown_summary_path"] = markdown_path.relative_to(root_path).as_posix()
    return payload


def _reviewer_remaining_warnings(
    *,
    lint: dict[str, Any],
    release_warnings: list[str],
    human_review: HumanReviewArtifact | None,
) -> dict[str, list[str]]:
    all_warnings = sorted(
        set(list(lint.get("quality_warning_reasons") or []) + release_warnings)
    )
    retrieval_warnings = [
        warning for warning in all_warnings if _reviewer_warning_is_retrieval(warning)
    ]
    quality_warnings = [
        warning
        for warning in all_warnings
        if warning not in retrieval_warnings
        and not _reviewer_warning_is_release(warning)
    ]
    claim_warnings: list[str] = []
    if int(lint.get("claim_support_missing_required_citation_count") or 0):
        claim_warnings.append("Some citation-required claims remain unresolved.")
    if int(lint.get("claim_support_forbidden_claim_count") or 0):
        claim_warnings.append("Forbidden proof, experiment, novelty, or readiness claims remain.")
    if int(lint.get("claim_support_scope_mismatch_count") or 0):
        claim_warnings.append("Some citations do not match claim-support scope.")
    citation_warnings: list[str] = []
    if list(lint.get("unregistered_citation_keys") or []):
        citation_warnings.append("Unregistered citation keys are present.")
    if int(lint.get("citation_as_validation_misuse_count") or 0):
        citation_warnings.append("Some citation usage is framed as proof or validation.")
    if not bool(lint.get("citation_registry_sources_all_accepted", True)):
        citation_warnings.append("Citation registry includes non-accepted sources.")
    release_warnings_only = [
        warning
        for warning in all_warnings
        if _reviewer_warning_is_release(warning)
        and warning not in retrieval_warnings
    ]
    release_warnings_only.append("Publication ready is false.")
    human_review_warnings: list[str] = []
    if human_review and human_review.blocking_concerns:
        human_review_warnings.append("Human review recorded blocking concerns.")
    if human_review and human_review.requested_changes:
        human_review_warnings.append("Human review requested manuscript or evidence changes.")
    return {
        "retrieval_source_boundary_warnings": retrieval_warnings,
        "quality_depth_warnings": quality_warnings,
        "claim_support_warnings": sorted(set(claim_warnings)),
        "citation_warnings": sorted(set(citation_warnings)),
        "human_review_warnings": sorted(set(human_review_warnings)),
        "release_warnings": sorted(set(release_warnings_only)),
    }


def _reviewer_warning_is_retrieval(warning: str) -> bool:
    key = warning.casefold()
    return any(
        phrase in key
        for phrase in (
            "retrieved sources",
            "retrieval adequacy",
            "retrieval quality",
            "bounded background context",
            "source relevance",
            "source filtering",
        )
    )


def _reviewer_warning_is_release(warning: str) -> bool:
    key = warning.casefold()
    return "human-review readiness" in key or "publication ready" in key


def _reviewer_paper_artifact_paths(bundle: dict[str, Any]) -> dict[str, str]:
    artifacts = dict(bundle.get("artifacts") or {})
    keys = (
        "complete_manuscript_draft",
        "revised_manuscript_draft",
        "paper",
        "revised_paper",
        "latex_source_map",
        "revised_latex_source_map",
        "references",
        "revised_references",
    )
    paths = {key: str(artifacts[key]) for key in keys if artifacts.get(key)}
    if bundle.get("primary_artifact_to_read"):
        paths["primary_draft"] = str(bundle["primary_artifact_to_read"])
    if bundle.get("primary_latex_to_read"):
        paths["primary_latex"] = str(bundle["primary_latex_to_read"])
    return paths


def _reviewer_audit_artifact_paths(
    bundle: dict[str, Any],
    paths: dict[str, Path],
    root_path: Path,
) -> dict[str, str]:
    artifacts = dict(bundle.get("artifacts") or {})
    keys = (
        "generation_report",
        "release_report",
        "safe_repair_report",
        "quality_repair_report",
        "retrieval_report",
        "retrieval_quality_report",
        "citation_registry",
        "claim_support_audit",
        "human_review_artifact",
        "human_review_summary",
    )
    result: dict[str, str] = {}
    for key in keys:
        if artifacts.get(key):
            result[key] = str(artifacts[key])
        elif key in paths:
            result[key] = paths[key].relative_to(root_path).as_posix()
    return result


def _reviewer_claim_support_status(lint: dict[str, Any]) -> str:
    unresolved = sum(
        int(lint.get(key) or 0)
        for key in (
            "claim_support_missing_required_citation_count",
            "claim_support_scope_mismatch_count",
            "claim_support_forbidden_claim_count",
            "citation_as_validation_misuse_count",
        )
    )
    if unresolved:
        return "needs_review"
    if bool(lint.get("claim_support_audit_present")):
        return "clean"
    return "not_available"


def _reviewer_citation_status(lint: dict[str, Any]) -> str:
    if list(lint.get("unregistered_citation_keys") or []):
        return "needs_review"
    if not bool(lint.get("citation_registry_sources_all_accepted", True)):
        return "needs_review"
    if bool(lint.get("bibliography_registry_backed")):
        return "registry-backed"
    if not bool(lint.get("citations_present")):
        return "no-citations-required"
    return "needs_review"


def _reviewer_source_relevance_status(lint: dict[str, Any]) -> str:
    backend = str(lint.get("source_relevance_adjudicator_backend") or "off")
    if backend == "off":
        return "off"
    return (
        f"{backend}: {int(lint.get('source_relevance_adjudicated_count') or 0)} "
        "adjudicated, "
        f"{int(lint.get('source_relevance_llm_accepted_count') or 0)} accepted, "
        f"{int(lint.get('source_relevance_llm_rejected_count') or 0)} rejected, "
        f"{int(lint.get('source_relevance_hard_reject_count') or 0)} hard rejected"
    )


def _human_review_counts_as_present(review: HumanReviewArtifact | None) -> bool:
    return bool(
        review
        and review.reviewer_is_human
        and not review.llm_generated
        and review.review_status != "not_reviewed"
    )


def _reviewer_evidence_boundaries(
    human_review_present: bool,
) -> dict[str, list[str]]:
    human_review_evidence_line = (
        "Human-review artifact is evidence that human review occurred only."
        if human_review_present
        else "No human-review artifact is present in this bundle."
    )
    return {
        "artifacts_that_are_evidence": [
            "No linked proof artifact is present in this bundle.",
            "No linked experiment artifact is present in this bundle.",
            human_review_evidence_line,
            "Retrieval/source records are bounded background context only.",
        ],
        "artifacts_that_are_not_evidence": [
            "LLM prose is not evidence.",
            "LLM reviews are not evidence.",
            "Source relevance adjudication is not evidence.",
            "Retrieval adequacy scores are not evidence.",
            "Citation registry is not evidence.",
            "Claim-support audit is not evidence.",
            "Quality repair is not evidence.",
            "Release status is not evidence.",
            "LaTeX/PDF export is not evidence.",
            (
                "Human review does not establish proof, experiment validation, "
                "novelty, correctness, or publication readiness."
            ),
        ],
    }


def _reviewer_evidence_gaps(human_review_present: bool) -> list[str]:
    gaps = [
        (
            "No proof artifact is linked; theorem-style claims need proof work before "
            "stronger wording."
        ),
        (
            "No experiment artifact is linked; empirical claims need experiment work "
            "before stronger wording."
        ),
    ]
    if not human_review_present:
        gaps.append("No human-review artifact is linked; this summary is not human review.")
    return gaps


def _reviewer_source_limitations(lint: dict[str, Any]) -> list[str]:
    limitations = [
        (
            "accepted_source_count="
            f"{int(lint.get('accepted_source_count') or 0)}; "
            "rejected_source_count="
            f"{int(lint.get('rejected_source_count') or 0)}."
        ),
        (
            "retrieval_adequacy_status="
            f"{lint.get('retrieval_adequacy_status') or 'not_evaluated'}; "
            "retrieval remains bounded background context only."
        ),
        "Rejected and hard-rejected sources cannot support manuscript claims.",
    ]
    if bool(lint.get("source_relevance_adjudication_enabled")):
        limitations.append("Source relevance adjudication is non-evidential.")
    return limitations


def _reviewer_claim_support_summary(lint: dict[str, Any]) -> dict[str, Any]:
    return {
        "claim_support_audit_present": bool(lint.get("claim_support_audit_present")),
        "total_sentences": int(lint.get("claim_support_total_sentences") or 0),
        "registry_supported": int(
            lint.get("claim_support_registry_supported_count") or 0
        ),
        "scaffold_not_required": int(
            lint.get("claim_support_scaffold_not_required_count") or 0
        ),
        "missing_required_citation": int(
            lint.get("claim_support_missing_required_citation_count") or 0
        ),
        "scope_mismatch": int(lint.get("claim_support_scope_mismatch_count") or 0),
        "forbidden_claim": int(lint.get("claim_support_forbidden_claim_count") or 0),
        "citation_as_validation_misuse": int(
            lint.get("citation_as_validation_misuse_count") or 0
        ),
        "claim_adjudicator_backend": str(lint.get("claim_adjudicator_backend") or "off"),
        "adjudicated_sentence_count": int(lint.get("adjudicated_sentence_count") or 0),
    }


def _reviewer_citation_summary(lint: dict[str, Any]) -> dict[str, Any]:
    return {
        "citation_registry_present": bool(lint.get("citation_registry_present")),
        "citation_registry_source_count": int(
            lint.get("citation_registry_source_count") or 0
        ),
        "citation_registry_sources_all_accepted": bool(
            lint.get("citation_registry_sources_all_accepted", True)
        ),
        "registry_backed_citation_count": int(
            lint.get("registry_backed_citation_count") or 0
        ),
        "unregistered_citation_keys": list(lint.get("unregistered_citation_keys") or []),
        "bibliography_status": str(lint.get("bibliography_status") or "absent"),
        "citation_policy": str(lint.get("citation_policy") or "none"),
    }


def _reviewer_retrieval_summary(lint: dict[str, Any]) -> dict[str, Any]:
    return {
        "retrieval_quality_report_present": bool(
            lint.get("retrieval_quality_report_present")
        ),
        "retrieved_source_count": int(lint.get("retrieved_source_count") or 0),
        "accepted_source_count": int(lint.get("accepted_source_count") or 0),
        "rejected_source_count": int(lint.get("rejected_source_count") or 0),
        "retrieval_adequacy_status": str(
            lint.get("retrieval_adequacy_status") or "not_evaluated"
        ),
        "source_relevance_adjudication_enabled": bool(
            lint.get("source_relevance_adjudication_enabled")
        ),
        "source_relevance_adjudicator_backend": str(
            lint.get("source_relevance_adjudicator_backend") or "off"
        ),
        "source_relevance_adjudicated_count": int(
            lint.get("source_relevance_adjudicated_count") or 0
        ),
        "source_relevance_llm_accepted_count": int(
            lint.get("source_relevance_llm_accepted_count") or 0
        ),
        "source_relevance_llm_rejected_count": int(
            lint.get("source_relevance_llm_rejected_count") or 0
        ),
        "source_relevance_hard_reject_count": int(
            lint.get("source_relevance_hard_reject_count") or 0
        ),
    }


def _reviewer_quality_summary(lint: dict[str, Any]) -> dict[str, Any]:
    return {
        "quality_status": str(lint.get("quality_status") or "unknown"),
        "quality_repair_report_present": bool(
            lint.get("quality_repair_report_present")
        ),
        "quality_repair_backend": str(lint.get("quality_repair_backend") or "off"),
        "quality_repair_status": str(
            lint.get("quality_repair_status") or "disabled"
        ),
        "quality_repaired_section_count": int(
            lint.get("quality_repaired_section_count") or 0
        ),
        "section_depth_target_met_count": int(
            lint.get("section_depth_target_met_count") or 0
        ),
        "section_depth_target_total": int(
            lint.get("section_depth_target_total") or 0
        ),
        "sections_below_depth_target": list(
            lint.get("sections_below_depth_target") or []
        ),
        "warnings_reduced_count": int(lint.get("warnings_reduced_count") or 0),
        "irreducible_quality_warnings": list(
            lint.get("irreducible_quality_warnings") or []
        ),
    }


def _reviewer_human_review_checklist() -> list[str]:
    return [
        "Read the revised manuscript draft.",
        "Verify that the problem framing matches the intended research question.",
        "Check whether accepted sources are appropriate for bounded background context.",
        "Check whether rejected or hard-rejected sources should be replaced by better retrieval.",
        "Confirm that no proof or experiment claims are made without linked artifacts.",
        "Decide whether to request real proof, experiment, or retrieval expansion.",
        "Decide whether the draft should proceed to evidence generation.",
    ]


def _reviewer_recommended_next_actions(
    human_review: HumanReviewArtifact | None,
) -> list[str]:
    actions: list[str] = []
    if human_review and human_review.blocking_concerns:
        actions.append("Address blocking human-review concerns before evidence generation.")
    elif _human_review_counts_as_present(human_review):
        actions.append("Follow the human-review recommended next action as a separate step.")
    else:
        actions.append("Perform human review and record the result as a separate artifact.")
    actions.extend(
        [
        "Expand real retrieval before making broader literature-context claims.",
        "Add a proof artifact if theorem claims are desired.",
        "Add an experiment artifact if empirical claims are desired.",
        "Run a LaTeX/PDF export check if presentation fidelity is needed.",
        ]
    )
    return actions


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


def _canonical_section_word_counts(
    sections: list[dict[str, Any]],
) -> dict[str, int]:
    counts = {heading: 0 for heading in CANONICAL_MAIN_SECTIONS}
    for section in sections:
        canonical = _canonical_main_body_heading(str(section["heading"]))
        if canonical in counts:
            counts[canonical] = max(counts[canonical], int(section["word_count"]))
    return counts


def _sections_below_depth_target(
    word_counts: dict[str, int],
) -> list[str]:
    return [
        heading
        for heading, target in QUALITY_SECTION_DEPTH_TARGETS.items()
        if int(word_counts.get(heading, 0)) < int(target["min_words"])
    ]


def _quality_depth_target_summary(
    word_counts: dict[str, int],
) -> dict[str, int | list[str] | bool]:
    below = _sections_below_depth_target(word_counts)
    total = len(QUALITY_SECTION_DEPTH_TARGETS)
    return {
        "section_depth_targets_present": True,
        "section_depth_target_total": total,
        "section_depth_target_met_count": total - len(below),
        "sections_below_depth_target": below,
    }


def _irreducible_quality_warnings(warnings: list[str]) -> list[str]:
    return [
        warning
        for warning in warnings
        if any(
            warning.startswith(prefix)
            for prefix in _IRREDUCIBLE_QUALITY_WARNING_PREFIXES
        )
    ]


def _warnings_reduced_count(before: list[str], after: list[str]) -> int:
    return max(0, len(set(before) - set(after)))


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


def _concrete_limitation_constraint_count(text: str) -> int:
    text_key = text.casefold()
    return sum(1 for pattern in _CONCRETE_LIMITATION_PATTERNS if pattern in text_key)


def _is_concrete_limitations_section(section: dict[str, Any]) -> bool:
    heading = str(section["heading"])
    return (
        _canonical_main_body_heading(heading) == "Limitations"
        and _concrete_limitation_constraint_count(str(section["body"])) >= 2
    )


def _section_is_placeholder_like(section: dict[str, Any]) -> bool:
    if int(section["word_count"]) == 0:
        return True
    if _is_concrete_limitations_section(section):
        return False
    return _contains_placeholder_text(str(section["body"]))


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
            "placeholder_like": _section_is_placeholder_like(section),
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


def _build_claim_support_audit_for_generation(
    *,
    run_id: str,
    root: str | Path,
    ledger: ResearchLedger,
    revision_result: PaperRevisionRunResult | None,
    claim_adjudicator: ClaimAdjudicator | None,
) -> ClaimSupportAuditReport:
    markdown = _primary_markdown_for_claim_support(
        run_id=run_id,
        root=root,
        revision_result=revision_result,
    )
    registry = _read_citation_registry(
        Path(root) / "runs" / run_id / "reports" / "citation-registry.json"
    )
    if registry is None:
        registry = build_citation_registry_from_ledger(run_id, ledger)
    return build_claim_support_audit(
        run_id=run_id,
        markdown=markdown,
        citation_registry=registry,
        claim_adjudicator=claim_adjudicator,
        available_evidence_artifacts=_available_evidence_artifacts(ledger, run_id),
    )


def _run_quality_repair_if_requested(
    *,
    run_id: str,
    root: str | Path,
    ledger: ResearchLedger,
    config: FullPaperGenerationConfig,
    revision_result: PaperRevisionRunResult | None,
    claim_adjudicator: ClaimAdjudicator | None,
) -> tuple[QualityRepairReport | None, str | None, ClaimSupportAuditReport | None]:
    backend = config.quality_repair_backend
    if backend == "off":
        return None, None, None
    if backend == "openai":
        if not config.allow_external_calls:
            raise FullPaperGenerationError(
                "OpenAI quality repair requires --allow-external-calls."
            )
        raise FullPaperGenerationError(
            "OpenAI quality repair is gated but not implemented for M57; "
            "use --quality-repair-backend deterministic."
        )
    if backend not in {"deterministic", "fake"}:
        raise FullPaperGenerationError(f"Unsupported quality repair backend: {backend}")

    markdown = _primary_markdown_for_claim_support(
        run_id=run_id,
        root=root,
        revision_result=revision_result,
    )
    before = lint_paper_bundle_summary(run_id=run_id, root=root)
    repaired_markdown, repair_state = _deterministic_quality_repair_markdown(
        markdown,
        before,
    )
    registry = _read_citation_registry(
        Path(root) / "runs" / run_id / "reports" / "citation-registry.json"
    )
    if registry is None:
        registry = build_citation_registry_from_ledger(run_id, ledger)
    claim_support_audit = build_claim_support_audit(
        run_id=run_id,
        markdown=repaired_markdown,
        citation_registry=registry,
        claim_adjudicator=claim_adjudicator,
        available_evidence_artifacts=_available_evidence_artifacts(ledger, run_id),
    )
    source_repair = repair_missing_required_citations_with_accepted_sources(
        repaired_markdown,
        claim_support_audit,
        registry,
    )
    if source_repair.revised_markdown != repaired_markdown:
        repaired_markdown = source_repair.revised_markdown
        claim_support_audit = build_claim_support_audit(
            run_id=run_id,
            markdown=repaired_markdown,
            citation_registry=registry,
            claim_adjudicator=claim_adjudicator,
            available_evidence_artifacts=_available_evidence_artifacts(ledger, run_id),
        )
    citation_safety = validate_citation_usage(repaired_markdown, registry)
    after = lint_paper_bundle_summary(
        run_id=run_id,
        root=root,
        _markdown_override=repaired_markdown,
        _claim_support_audit_override=claim_support_audit,
        _citation_safety_override=citation_safety,
    )
    bibliography_present = bool(
        re.search(
            r"^#{1,6}\s+(bibliography|references)\s*$",
            repaired_markdown,
            re.I | re.M,
        )
    )
    citation_violation = bool(citation_safety.unregistered_citation_keys) or (
        bibliography_present and not citation_safety.bibliography_registry_backed
    )
    sections_repaired = sorted(set(repair_state["sections_repaired"]))
    quality_warnings_before = list(before.get("quality_warning_reasons") or [])
    quality_warnings_after = list(after.get("quality_warning_reasons") or [])
    placeholder_like_sections_before = [
        str(item.get("heading", ""))
        for item in before.get("empty_or_placeholder_sections") or []
        if isinstance(item, dict) and item.get("heading")
    ]
    placeholder_like_sections_after = [
        str(item.get("heading", ""))
        for item in after.get("empty_or_placeholder_sections") or []
        if isinstance(item, dict) and item.get("heading")
    ]
    status = (
        "blocked"
        if citation_violation
        or int(claim_support_audit.summary_counts.get("missing_required_citation", 0))
        or int(claim_support_audit.summary_counts.get("forbidden_claim", 0))
        or int(claim_support_audit.summary_counts.get("citation_as_validation_misuse", 0))
        else "repaired"
        if repaired_markdown != markdown
        else "no_action_needed"
    )
    report = QualityRepairReport(
        run_id=run_id,
        quality_repair_enabled=True,
        quality_repair_backend=backend,
        quality_repair_status=status,
        before_quality_status=str(before.get("quality_status") or ""),
        after_quality_status=str(after.get("quality_status") or ""),
        quality_failures_before=list(before.get("quality_failure_reasons") or []),
        quality_failures_after=list(after.get("quality_failure_reasons") or []),
        quality_warnings_before=quality_warnings_before,
        quality_warnings_after=quality_warnings_after,
        sections_repaired=sections_repaired,
        section_depth_targets=QUALITY_SECTION_DEPTH_TARGETS,
        section_word_counts_before=dict(before.get("section_word_counts") or {}),
        section_word_counts_after=dict(after.get("section_word_counts") or {}),
        sections_below_target_before=list(
            before.get("sections_below_depth_target") or []
        ),
        sections_below_target_after=list(
            after.get("sections_below_depth_target") or []
        ),
        placeholder_like_sections_before=placeholder_like_sections_before,
        placeholder_like_sections_after=placeholder_like_sections_after,
        warnings_reduced_count=_warnings_reduced_count(
            quality_warnings_before,
            quality_warnings_after,
        ),
        irreducible_warnings=_irreducible_quality_warnings(quality_warnings_after),
        abstract_repaired=bool(repair_state["abstract_repaired"]),
        problem_statement_repaired=bool(repair_state["problem_statement_repaired"]),
        method_summary_repaired=bool(repair_state["method_summary_repaired"]),
        limitations_repaired=bool(repair_state["limitations_repaired"]),
        conclusion_repaired=bool(repair_state["conclusion_repaired"]),
        placeholder_sections_repaired=int(
            repair_state["placeholder_sections_repaired"]
        ),
        underdeveloped_sections_repaired=int(
            repair_state["underdeveloped_sections_repaired"]
        ),
        claim_support_rechecked_after_repair=True,
        citation_safety_rechecked_after_repair=True,
    )
    return report, repaired_markdown, claim_support_audit


def _deterministic_quality_repair_markdown(
    markdown: str,
    before_lint: dict[str, Any],
) -> tuple[str, dict[str, Any]]:
    sections = _markdown_sections(markdown)
    state: dict[str, Any] = {
        "sections_repaired": [],
        "abstract_repaired": False,
        "problem_statement_repaired": False,
        "method_summary_repaired": False,
        "limitations_repaired": False,
        "conclusion_repaired": False,
        "placeholder_sections_repaired": 0,
        "underdeveloped_sections_repaired": 0,
    }
    revised = markdown
    short_headings = {
        str(item.get("heading", ""))
        for item in before_lint.get("sections_too_short") or []
        if isinstance(item, dict)
    }
    placeholder_headings = {
        str(item.get("heading", ""))
        for item in before_lint.get("empty_or_placeholder_sections") or []
        if isinstance(item, dict)
    }

    def needs(heading: str, min_words: int) -> bool:
        section = _find_markdown_section(sections, heading)
        if section is None:
            return True
        return (
            int(section["word_count"]) < min_words
            or heading in short_headings
            or heading in placeholder_headings
            or _contains_placeholder_text(str(section["body"]))
        )

    if needs("Abstract", QUALITY_SECTION_DEPTH_TARGETS["Abstract"]["min_words"]):
        revised = _replace_or_insert_markdown_section(
            revised,
            "Abstract",
            _quality_repair_abstract(),
        )
        state["sections_repaired"].append("Abstract")
        state["abstract_repaired"] = True
        state["underdeveloped_sections_repaired"] += 1
        if "Abstract" in placeholder_headings:
            state["placeholder_sections_repaired"] += 1

    semantic = dict(before_lint.get("semantic_checks") or {})
    if needs(
        "Introduction and Problem Framing",
        QUALITY_SECTION_DEPTH_TARGETS["Introduction and Problem Framing"][
            "min_words"
        ],
    ):
        revised = _replace_or_insert_markdown_section(
            revised,
            "Introduction and Problem Framing",
            _quality_repair_introduction(),
        )
        state["sections_repaired"].append("Introduction and Problem Framing")
        state["problem_statement_repaired"] = True
        state["underdeveloped_sections_repaired"] += 1
        if "Introduction and Problem Framing" in placeholder_headings:
            state["placeholder_sections_repaired"] += 1
    elif not semantic.get("problem_statement_present"):
        revised = _prepend_to_markdown_section(
            revised,
            "Introduction and Problem Framing",
            _quality_repair_problem_statement(),
        )
        state["sections_repaired"].append("Introduction and Problem Framing")
        state["problem_statement_repaired"] = True

    if (
        needs(
            "Method and Model",
            QUALITY_SECTION_DEPTH_TARGETS["Method and Model"]["min_words"],
        )
        or not semantic.get("method_summary_present")
    ):
        revised = _replace_or_insert_markdown_section(
            revised,
            "Method and Model",
            _quality_repair_method_summary(),
        )
        state["sections_repaired"].append("Method and Model")
        state["method_summary_repaired"] = True
        state["underdeveloped_sections_repaired"] += 1
        if "Method and Model" in placeholder_headings:
            state["placeholder_sections_repaired"] += 1

    if needs(
        "Claim and Evidence Boundaries",
        QUALITY_SECTION_DEPTH_TARGETS["Claim and Evidence Boundaries"]["min_words"],
    ):
        revised = _replace_or_insert_markdown_section(
            revised,
            "Claim and Evidence Boundaries",
            _quality_repair_claim_boundaries(),
        )
        state["sections_repaired"].append("Claim and Evidence Boundaries")
        state["underdeveloped_sections_repaired"] += 1
        if "Claim and Evidence Boundaries" in placeholder_headings:
            state["placeholder_sections_repaired"] += 1

    if needs(
        "Demonstration Status",
        QUALITY_SECTION_DEPTH_TARGETS["Demonstration Status"]["min_words"],
    ):
        revised = _replace_or_insert_markdown_section(
            revised,
            "Demonstration Status",
            _quality_repair_demonstration_status(),
        )
        state["sections_repaired"].append("Demonstration Status")
        state["underdeveloped_sections_repaired"] += 1
        if "Demonstration Status" in placeholder_headings:
            state["placeholder_sections_repaired"] += 1

    if (
        needs(
            "Limitations",
            QUALITY_SECTION_DEPTH_TARGETS["Limitations"]["min_words"],
        )
        or not semantic.get("limitations_present")
    ):
        revised = _replace_or_insert_markdown_section(
            revised,
            "Limitations",
            _quality_repair_limitations(),
        )
        state["sections_repaired"].append("Limitations")
        state["limitations_repaired"] = True
        state["underdeveloped_sections_repaired"] += 1
        if "Limitations" in placeholder_headings:
            state["placeholder_sections_repaired"] += 1

    if (
        needs(
            "Conclusion",
            QUALITY_SECTION_DEPTH_TARGETS["Conclusion"]["min_words"],
        )
        or before_lint.get("conclusion_placeholder_like")
    ):
        revised = _replace_or_insert_markdown_section(
            revised,
            "Conclusion",
            _quality_repair_conclusion(),
        )
        state["sections_repaired"].append("Conclusion")
        state["conclusion_repaired"] = True
        state["underdeveloped_sections_repaired"] += 1
        if "Conclusion" in placeholder_headings:
            state["placeholder_sections_repaired"] += 1

    return revised.strip() + "\n", state


def _quality_repair_abstract() -> str:
    return (
        "This draft frames a bounded optimal-transport approach for organizing a "
        "manuscript-level description of heterogeneous spatial structure in human "
        "geography. The contribution is deliberately narrow: it is an audited paper "
        "package that makes the candidate framing, source boundaries, citation "
        "registry, claim-support audit, source-aware citation repair, quality repair, "
        "and release gate readable for human review. The method described here is a "
        "deterministic pipeline for assembling and checking a draft, not a validated "
        "theorem, experiment, novelty result, or correctness argument. Accepted "
        "registry sources are used only as bounded background context; rejected and "
        "hard-rejected sources cannot support claims. Source relevance and retrieval "
        "adequacy remain non-evidential, and citations do not prove validation or "
        "publication readiness. The current run therefore supports a cautious "
        "human-review handoff with warnings while reserving stronger scientific "
        "claims for later proof, experiment, and human-review artifacts."
    )


def _quality_repair_problem_statement() -> str:
    return (
        "Problem statement: this draft asks how a bounded optimal-transport framing "
        "can organize heterogeneous spatial structure in human geography without "
        "treating the draft as proof, validation, or novelty. The central contribution "
        "of this draft is a traceable manuscript package whose source and citation "
        "boundaries are explicit."
    )


def _quality_repair_introduction() -> str:
    return (
        "Problem statement: this draft asks how a bounded optimal-transport framing "
        "can organize a structured description of heterogeneous spatial relations in "
        "human geography without treating the description as proof, validation, or "
        "novelty. The representation problem is difficult in the manuscript because "
        "the candidate framing must handle uneven scales, overlapping boundaries, and "
        "multiple relation types while staying inside the evidence actually recorded "
        "by the run. The current draft does not try to settle that scientific problem. "
        "It instead presents a controlled research scaffold whose central contribution "
        "is traceability: the paper package states what was assembled, which sources "
        "were accepted for bounded background context, which sources were rejected or "
        "hard-rejected, and which claim-support checks remain binding. Accepted "
        "registry sources, when present, may situate the topic locally, but they do "
        "not map the literature as a whole and cannot support proof, experiment, "
        "validation, or publication-readiness language. The introduction therefore "
        "sets expectations for a cautious human-review handoff. It makes the candidate "
        "idea legible, names the source and citation constraints, and leaves stronger "
        "scientific conclusions to later admissible evidence artifacts. That stance "
        "also shapes the prose: claims about sources must either carry accepted local "
        "citations or be rewritten as boundary language, and claims about proof, "
        "experiments, validation, novelty, or publication readiness are excluded from "
        "the draft unless the run contains the corresponding evidence. The section is "
        "therefore an orientation to the package rather than an argument that the "
        "candidate approach has already succeeded. The terminology is intentionally "
        "modest: candidate, scaffold, boundary, and handoff language help the reader "
        "separate manuscript organization from evidence production. It also keeps "
        "review questions explicit and auditable."
    )


def _quality_repair_method_summary() -> str:
    return (
        "Method summary: the run begins with a candidate framing and then assembles a "
        "paper-shaped manuscript from deterministic artifacts already present in the "
        "run. Retrieval records are normalized and filtered before any source can "
        "enter the citation registry. Source relevance adjudication, when enabled, "
        "may judge whether an ambiguous source is useful as bounded background "
        "context, but deterministic checks still control metadata completeness, "
        "duplicate status, hard rejection, registry inclusion, and citation key "
        "provenance. The accepted-source registry then becomes the only source of "
        "allowed citation keys. The drafting layer uses that registry as context, "
        "after which the claim-support audit classifies manuscript sentences, checks "
        "whether citation-required claims have local registry citations, and rejects "
        "citation use that implies proof, experiment evidence, novelty evidence, "
        "validation, or publication readiness. Source-aware repair can add compatible "
        "accepted citations or downgrade unsupported source claims to boundary "
        "language. Quality repair then expands underdeveloped sections using only "
        "deterministic, non-evidence wording. The release gate evaluates the final "
        "bundle for human-review handoff only. These steps describe provenance, "
        "traceability, and safety controls; they do not establish correctness or "
        "scientific value. The method is intentionally conservative about local "
        "artifacts: it reads reports from the run, rewrites only manuscript text, "
        "reruns citation and claim-support checks after repair, and carries forward "
        "the same non-evidence flags. A section can become clearer or deeper through "
        "this pass, but it cannot gain a stronger verification label or a new source "
        "outside the registry. The method also records section-depth targets, "
        "before-and-after warning counts, empty-section checks, and post-repair safety "
        "checks so that readability improvements remain auditable and repeatable."
    )


def _quality_repair_claim_boundaries() -> str:
    return (
        "The claim and evidence boundary is explicit. This manuscript does not change "
        "the claim table, mutate evidence links, or upgrade verification labels. "
        "Markdown prose, LaTeX exports, citation registries, retrieval reports, source "
        "relevance decisions, quality repair reports, and release reports are context "
        "artifacts only. They can describe how the run was assembled and where its "
        "boundaries are, but they cannot provide scientific validation, empirical "
        "confirmation, novelty, correctness, human approval, or publication readiness. "
        "The absence of proof evidence means proof-oriented language must stay outside "
        "this manuscript unless a verified proof artifact is later linked through the "
        "ledger. The absence of experiment evidence means experiment-oriented language "
        "must also stay outside the manuscript unless a validated experiment artifact "
        "with the correct data-regime limits is later linked. "
        "Citations can support bounded background or source-context statements when "
        "they are registry-backed and accepted; they cannot support theorem, "
        "experiment, validation, novelty, or publication-readiness claims. Any "
        "stronger manuscript sentence must wait for the corresponding admissible "
        "evidence artifact, and deterministic repair is allowed only to clarify this "
        "boundary."
    )


def _quality_repair_demonstration_status() -> str:
    return (
        "Demonstration status: the current run demonstrates orchestration, artifact "
        "persistence, bounded retrieval filtering, citation safety, claim-support "
        "checks, source-aware repair, deterministic quality repair, and release "
        "gating. In fake or local mode, adapter outputs are deterministic stand-ins "
        "for workflow validation and are not scientific evidence. In live mode, LLM "
        "prose, source relevance judgments, and claim adjudication records remain "
        "context and audit artifacts rather than proof, experiment evidence, or human "
        "approval. The demonstrated result is therefore a safer manuscript package, "
        "not a validated scientific finding. The run can show that rejected sources "
        "stay out of the citation registry, that accepted citations are registry "
        "backed, and that release status is limited to human-review handoff. It does "
        "not show that the candidate model is correct, novel, empirically confirmed, "
        "or approved by a human reviewer. The value of the demonstration is "
        "operational: it shows that the manuscript can be made more readable after "
        "safety-clean generation while the same citation and evidence boundaries "
        "remain in force."
    )


def _quality_repair_limitations() -> str:
    return (
        "Limitations: retrieval is bounded and cannot be read as a map of the "
        "literature as a whole. The accepted source count is a local registry count, "
        "not a measure of disciplinary agreement, and sparse accepted sources should "
        "be read as a constraint on background context. Rejected sources and "
        "hard-rejected sources are excluded from citation support even when their "
        "titles look relevant. Source relevance adjudication does not create proof, "
        "experiment evidence, novelty evidence, empirical validation, correctness, or "
        "human approval. This run has no proof artifact unless the ledger links a "
        "verified proof result, and it has no experiment artifact unless the ledger "
        "links a validated experiment result with the proper data-regime boundary. "
        "Human validation is also absent unless an explicit human-review artifact is "
        "present. The release status means only that configured internal checks are "
        "suitable for a human-review handoff. The manuscript remains a draft with "
        "`publication_ready=false`, and stronger claims require additional admissible "
        "evidence outside deterministic quality repair. These constraints are "
        "concrete rather than decorative: accepted_source_count, rejected source "
        "counts, hard-rejection decisions, the absence of proof artifacts, the "
        "absence of experiment artifacts, and the absence of human-review artifacts "
        "all limit what the manuscript can responsibly say."
    )


def _quality_repair_conclusion() -> str:
    return (
        "The resulting artifact is suitable for human review with warnings when the "
        "release gate reports that status. The useful result is the package discipline: "
        "source filtering is recorded, accepted citations remain registry-backed, "
        "claim-support checks are clean, and evidence boundaries are visible in the "
        "manuscript rather than hidden in auxiliary reports. Retrieval remains bounded "
        "background context, so clean citation safety does not become proof, empirical "
        "confirmation, novelty, or publication readiness. The draft should therefore "
        "be read as a traceable handoff artifact for reviewers who need to inspect the "
        "candidate framing and the supporting audit trail. The absence of proof, "
        "experiment, and human-review evidence keeps stronger future claims outside "
        "this draft. Future validation, correctness, novelty, or "
        "publication conclusions must wait for separate admissible artifacts before "
        "the manuscript can state them in a stronger form. The next useful step is "
        "therefore not a stronger claim in prose, but a review or evidence-producing "
        "artifact that can be audited by the same safety stack."
    )


def _find_markdown_section(
    sections: list[dict[str, Any]],
    heading: str,
) -> dict[str, Any] | None:
    target = heading.strip().casefold()
    return next(
        (section for section in sections if str(section["heading"]).casefold() == target),
        None,
    )


def _replace_or_insert_markdown_section(markdown: str, heading: str, body: str) -> str:
    if re.search(rf"^##\s+{re.escape(heading)}\s*$", markdown, re.M):
        return _replace_markdown_section(markdown, heading, body)
    insertion = f"\n## {heading}\n\n{body.strip()}\n"
    title_match = re.search(r"^#\s+.+$", markdown, re.M)
    if title_match:
        insert_at = title_match.end()
        return markdown[:insert_at] + "\n" + insertion + markdown[insert_at:]
    return f"# Generated Paper Draft\n{insertion}\n{markdown}"


def _replace_markdown_section(markdown: str, heading: str, body: str) -> str:
    pattern = re.compile(
        rf"(^##\s+{re.escape(heading)}\s*\n)(.*?)(?=^##\s+|\Z)",
        re.M | re.S,
    )
    return pattern.sub(rf"\1\n{body.strip()}\n\n", markdown, count=1)


def _prepend_to_markdown_section(markdown: str, heading: str, paragraph: str) -> str:
    if re.search(rf"^##\s+{re.escape(heading)}\s*$", markdown, re.M):
        pattern = re.compile(rf"(^##\s+{re.escape(heading)}\s*\n)", re.M)
        return pattern.sub(rf"\1\n{paragraph.strip()}\n\n", markdown, count=1)
    return _replace_or_insert_markdown_section(markdown, heading, paragraph)


def _revision_result_with_markdown(
    result: PaperRevisionRunResult,
    revised_markdown: str,
) -> PaperRevisionRunResult:
    if result.revision_result is None:
        return result
    return PaperRevisionRunResult(
        run_id=result.run_id,
        critic_report=result.critic_report,
        revision_plan=result.revision_plan,
        revision_result=result.revision_result.model_copy(
            update={"revised_markdown": revised_markdown}
        ),
        critic_report_artifact=result.critic_report_artifact,
        revision_plan_artifact=result.revision_plan_artifact,
        revision_safety_artifact=result.revision_safety_artifact,
        revised_markdown_artifact=result.revised_markdown_artifact,
        safe_repair_report_artifact=result.safe_repair_report_artifact,
        commit_hash=result.commit_hash,
    )


def _quality_repair_step_status(
    report: QualityRepairReport,
) -> FullPaperGenerationStepStatus:
    if report.quality_repair_status in {"blocked", "failed"}:
        return FullPaperGenerationStepStatus.BLOCKED
    if report.quality_failures_after or report.quality_warnings_after:
        return FullPaperGenerationStepStatus.SUCCEEDED_WITH_WARNINGS
    return FullPaperGenerationStepStatus.SUCCEEDED


def _quality_repair_step_warnings(report: QualityRepairReport) -> list[str]:
    warnings: list[str] = []
    if report.quality_repair_status == "no_action_needed":
        warnings.append("Quality repair found no eligible deterministic section edits.")
    if report.quality_failures_after:
        warnings.extend(report.quality_failures_after)
    warnings.extend(report.quality_warnings_after)
    warnings.append(
        "Quality repair is manuscript polish only and cannot create evidence, "
        "validation, novelty, or publication readiness."
    )
    return sorted(set(warnings))


def _available_evidence_artifacts(
    ledger: ResearchLedger,
    run_id: str,
) -> dict[str, bool]:
    refs = [
        artifact
        for commit in ledger.list_commits(run_id)
        for artifact in commit.artifact_refs
    ]
    return {
        "proof": any(
            artifact.type == ArtifactType.LEAN
            and bool(artifact.metadata.get("is_verification_evidence"))
            for artifact in refs
        ),
        "experiment": any(
            artifact.type == ArtifactType.EXPERIMENT
            and bool(artifact.metadata.get("is_verification_evidence"))
            for artifact in refs
        ),
        "human_review": False,
        "publication_ready": False,
    }


def _primary_markdown_for_claim_support(
    *,
    run_id: str,
    root: str | Path,
    revision_result: PaperRevisionRunResult | None,
) -> str:
    if revision_result is not None and revision_result.revision_result is not None:
        return revision_result.revision_result.revised_markdown
    run_path = Path(root) / "runs" / run_id
    for relative in (
        "reports/revised-manuscript-draft.md",
        "reports/complete-manuscript-draft.md",
    ):
        path = run_path / relative
        if path.is_file():
            return path.read_text(encoding="utf-8")
    return ""


def _claim_support_warnings(audit: ClaimSupportAuditReport) -> list[str]:
    warnings: list[str] = []
    if audit.citation_registry_present and not audit.claim_support_items:
        warnings.append("Claim-support audit found no manuscript sentences to classify.")
    missing = audit.summary_counts.get("missing_required_citation", 0)
    mismatch = audit.summary_counts.get("scope_mismatch", 0)
    forbidden = audit.summary_counts.get("forbidden_claim", 0)
    misuse = audit.summary_counts.get("citation_as_validation_misuse", 0)
    if missing:
        warnings.append(f"Claim-support audit found {missing} missing local citations.")
    if mismatch:
        warnings.append(f"Claim-support audit found {mismatch} citation scope mismatches.")
    if forbidden:
        warnings.append(f"Claim-support audit found {forbidden} forbidden evidence claims.")
    if misuse:
        warnings.append(f"Claim-support audit found {misuse} citation-as-validation misuses.")
    if audit.citation_placement_violations:
        warnings.append(
            "Claim-support audit found citation placement issues: "
            + str(len(audit.citation_placement_violations))
        )
    if audit.citation_registry_present:
        warnings.append(
            "Retrieval metadata is bounded literature context, not proof of novelty, "
            "validation, or publication readiness."
        )
    return warnings


def _export_revised_markdown_to_latex(
    *,
    run_id: str,
    root: str | Path,
    ledger: ResearchLedger,
    revision_result: PaperRevisionRunResult | None,
    revised_markdown: str | None = None,
):
    if revised_markdown is None:
        if revision_result is None or revision_result.revision_result is None:
            raise LatexExportError("revised manuscript draft is missing")
        revised_markdown = revision_result.revision_result.revised_markdown
    if not revised_markdown:
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
        draft_markdown=revised_markdown,
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
    claim_support_audit: ClaimSupportAuditReport,
    quality_repair_report: QualityRepairReport | None,
    quality_repaired_markdown: str | None,
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
            "claim_support_audit_artifact_id": "claim-support-audit",
            "quality_repair_report_artifact_id": (
                "quality-repair-report"
                if quality_repair_report is not None
                else None
            ),
            "revised_manuscript_draft_artifact_id": (
                "revised-manuscript-draft"
                if quality_repaired_markdown is not None
                else bundle.revised_manuscript_draft_artifact_id
            ),
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
        ArtifactWriteSpec(
            artifact_id="claim-support-audit",
            artifact_type=ArtifactType.REPORT,
            payload=claim_support_audit,
            artifact_format="json",
            metadata={
                **metadata,
                "artifact_role": "claim_support_citation_discipline_context",
            },
        ),
    ]
    if quality_repair_report is not None:
        specs.append(
            ArtifactWriteSpec(
                artifact_id="quality-repair-report",
                artifact_type=ArtifactType.REPORT,
                payload=quality_repair_report,
                artifact_format="json",
                metadata={
                    **metadata,
                    "artifact_role": "quality_repair_context",
                },
            )
        )
    if quality_repaired_markdown is not None:
        specs.append(
            ArtifactWriteSpec(
                artifact_id="revised-manuscript-draft",
                artifact_type=ArtifactType.REPORT,
                payload=quality_repaired_markdown,
                artifact_format="markdown",
                metadata={
                    **metadata,
                    "artifact_role": "quality_repaired_manuscript_context",
                },
            )
        )
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
            "quality_repair_applied": quality_repair_report is not None
            and quality_repair_report.quality_repair_status
            in {"repaired", "no_action_needed"},
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
    claim_adjudicator: ClaimAdjudicator | None,
    persistence: PersistenceResult,
) -> FullPaperGenerationRunResult:
    refs = {artifact.id: artifact for artifact in persistence.artifacts}
    bundle = bundle.model_copy(
        update={
            "full_paper_generation_report_artifact_id": "full-paper-generation-report",
            "full_paper_artifact_bundle_artifact_id": "full-paper-artifact-bundle",
            "claim_support_audit_artifact_id": _id_if_present(
                refs, "claim-support-audit"
            ),
            "quality_repair_report_artifact_id": _id_if_present(
                refs, "quality-repair-report"
            )
            or bundle.quality_repair_report_artifact_id,
            "revised_manuscript_draft_artifact_id": _id_if_present(
                refs, "revised-manuscript-draft"
            )
            or bundle.revised_manuscript_draft_artifact_id,
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
        claim_adjudicator=claim_adjudicator,
        persistence=persistence,
        report_artifact=refs.get("full-paper-generation-report"),
        bundle_artifact=refs.get("full-paper-artifact-bundle"),
        revised_latex_artifact=refs.get("revised-paper"),
        revised_references_artifact=refs.get("revised-references"),
        revised_source_map_artifact=refs.get("revised-latex-source-map"),
        revised_export_report_artifact=refs.get("revised-latex-export-report"),
        revised_safety_report_artifact=refs.get("revised-latex-safety-report"),
        quality_repair_report_artifact=refs.get("quality-repair-report"),
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
    generation = _latest_refs(ledger, run_id, ControllerActionType.FULL_PAPER_GENERATION_WRITTEN)
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
        claim_support_audit_artifact_id=_id_if_present(
            generation,
            "claim-support-audit",
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
            generation, "revised-manuscript-draft"
        )
        or _id_if_present(
            revision,
            "revised-manuscript-draft",
        ),
        paper_revision_result_artifact_id=_id_if_present(
            revision, "paper-revision-result"
        ),
        quality_repair_report_artifact_id=_id_if_present(
            generation,
            "quality-repair-report",
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
    "build_reviewer_bundle_summary",
    "full_paper_generation_result_model",
    "generate_full_paper",
    "inspect_paper_bundle_summary",
    "inspect_reviewer_bundle_summary",
    "lint_paper_bundle_summary",
    "render_reviewer_bundle_summary_markdown",
]
