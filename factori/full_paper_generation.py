"""End-to-end non-evidence full-paper generation orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from factori.artifacts import ArtifactStore
from factori.citations import (
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
    ControllerActionType,
    FullPaperArtifactBundle,
    FullPaperGenerationConfig,
    FullPaperGenerationReport,
    FullPaperGenerationResult,
    FullPaperGenerationStatus,
    FullPaperGenerationStep,
    FullPaperGenerationStepStatus,
    PaperCriticReport,
    RerunPolicy,
)


class FullPaperGenerationError(RuntimeError):
    """Raised when full-paper generation is blocked or fails closed."""


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
    max_words: int = 160,
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


def _validate_upstream_prerequisites(run_id: str, ledger: ResearchLedger) -> None:
    try:
        load_manuscript_drafting_inputs(run_id, ledger)
    except ManuscriptDraftingError as exc:
        raise FullPaperGenerationError(_clear_manuscript_error(str(exc))) from exc


def _ensure_citation_artifacts(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    include_citations: bool,
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
    registry = build_citation_registry_from_ledger(run_id, ledger)
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
    bundle = FullPaperArtifactBundle(
        run_id=run_id,
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
    "full_paper_generation_result_model",
    "generate_full_paper",
]
