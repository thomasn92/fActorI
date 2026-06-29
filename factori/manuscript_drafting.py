"""Section-by-section deterministic manuscript drafting engine."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from factori.adapters.prose_prompts import build_prose_section_prompt
from factori.adapters.prose_real import OpenAIProseGenerator
from factori.adapters.prose_safety import validate_generated_section
from factori.artifacts import ArtifactStore
from factori.citations import build_citation_registry_from_ledger, validate_citation_usage
from factori.ledger import ResearchLedger
from factori.literature_positioning import build_literature_positioning_report
from factori.manuscript_assembly import assemble_complete_markdown_draft
from factori.narrative_contract import (
    NarrativeContractError,
    build_narrative_contract,
    load_narrative_inputs,
)
from factori.paper_shape import critique_paper_shape
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.prose_contract import build_prose_evidence_map, build_prose_section_contract
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    CitationRegistry,
    CitationSafetyReport,
    ClaimTable,
    CompleteMarkdownDraft,
    ControllerActionType,
    LiteraturePositioningReport,
    ManuscriptAssemblyReport,
    ManuscriptDraftingPlan,
    ManuscriptDraftingReport,
    ManuscriptPlan,
    NarrativeManuscriptContract,
    PaperShapeCritique,
    ProseGenerationParseResult,
    ProseSafetyReport,
    SectionDraftingResult,
    SectionDraftingTask,
    SectionDraftSafetySummary,
)


class ManuscriptDraftingError(RuntimeError):
    """Raised when manuscript drafting prerequisites or adapters fail."""


@dataclass(frozen=True)
class ManuscriptDraftingInputs:
    """Loaded deterministic inputs for manuscript drafting."""

    manuscript_plan: ManuscriptPlan
    claim_table: ClaimTable
    narrative_contract: NarrativeManuscriptContract
    paper_shape_critique: PaperShapeCritique
    citation_registry: CitationRegistry | None = None
    literature_positioning_report: LiteraturePositioningReport | None = None


@dataclass(frozen=True)
class ManuscriptDraftingRunResult:
    """Complete result of drafting and optional persistence."""

    run_id: str
    inputs: ManuscriptDraftingInputs
    drafting_plan: ManuscriptDraftingPlan
    section_results: list[SectionDraftingResult]
    complete_draft: CompleteMarkdownDraft
    drafting_report: ManuscriptDraftingReport
    assembly_report: ManuscriptAssemblyReport
    citation_registry: CitationRegistry | None = None
    literature_positioning_report: LiteraturePositioningReport | None = None
    citation_safety_report: CitationSafetyReport | None = None
    plan_artifact: ArtifactRef | None = None
    drafting_report_artifact: ArtifactRef | None = None
    markdown_artifact: ArtifactRef | None = None
    assembly_report_artifact: ArtifactRef | None = None
    citation_registry_artifact: ArtifactRef | None = None
    literature_positioning_artifact: ArtifactRef | None = None
    citation_safety_artifact: ArtifactRef | None = None
    commit_hash: str | None = None


def load_manuscript_drafting_inputs(
    run_id: str,
    ledger: ResearchLedger,
) -> ManuscriptDraftingInputs:
    """Load manuscript plan, claim table, narrative contract, and paper-shape critique."""
    try:
        inputs = load_narrative_inputs(run_id, ledger)
    except NarrativeContractError as exc:
        raise ManuscriptDraftingError(str(exc)) from exc
    narrative_contract = build_narrative_contract(
        inputs.manuscript_plan,
        inputs.final_nucleus,
        inputs.claim_table,
        run_id=run_id,
    )
    critique = critique_paper_shape(narrative_contract, inputs.manuscript_plan)
    return ManuscriptDraftingInputs(
        manuscript_plan=inputs.manuscript_plan,
        claim_table=inputs.claim_table,
        narrative_contract=narrative_contract,
        paper_shape_critique=critique,
    )


def build_manuscript_drafting_plan(
    *,
    run_id: str,
    manuscript_plan: ManuscriptPlan,
    claim_table: ClaimTable,
    narrative_contract: NarrativeManuscriptContract,
    paper_shape_critique: PaperShapeCritique,
    citation_registry: CitationRegistry | None = None,
    literature_positioning_report: LiteraturePositioningReport | None = None,
    prose_backend: str = "fake",
    max_words: int = 160,
) -> ManuscriptDraftingPlan:
    """Build deterministic section drafting tasks from a manuscript plan."""
    tasks: list[SectionDraftingTask] = []
    for section in manuscript_plan.sections:
        contract = build_prose_section_contract(
            run_id=run_id,
            section_id=section.section_id,
            manuscript_plan=manuscript_plan,
            claim_table=claim_table,
            narrative_contract=narrative_contract,
            citation_registry=citation_registry,
            literature_positioning_report=literature_positioning_report,
            max_words=max_words,
        )
        tasks.append(
            SectionDraftingTask(
                section_id=section.section_id,
                section_title=section.title,
                section_role=contract.section_role,
                narrative_role=contract.narrative_role,
                allowed_claim_ids=contract.allowed_claim_ids,
                allowed_evidence_artifact_ids=contract.allowed_evidence_artifact_ids,
                allowed_citation_ids=contract.allowed_citation_ids,
                allowed_citation_keys=contract.allowed_citation_keys,
                source_contract_hashes=contract.source_contract_hashes,
                prose_contract=contract,
            )
        )
    return ManuscriptDraftingPlan(
        run_id=run_id,
        plan_id=f"manuscript-drafting-plan-{manuscript_plan.final_nucleus_id}",
        manuscript_plan_id=manuscript_plan.plan_id,
        narrative_contract_id=narrative_contract.contract_id,
        paper_shape_critique_id=paper_shape_critique.critique_id,
        prose_backend=prose_backend,
        sections_count=len(tasks),
        tasks=tasks,
        warnings=list(paper_shape_critique.warnings),
    )


def draft_manuscript_sections(
    *,
    drafting_plan: ManuscriptDraftingPlan,
    claim_table: ClaimTable,
    narrative_contract: NarrativeManuscriptContract,
    prose_generator,
) -> list[SectionDraftingResult]:
    """Draft and validate every planned section using the configured prose adapter."""
    evidence_map = build_prose_evidence_map(claim_table)
    backend = getattr(prose_generator, "backend_name", "fake")
    provider = getattr(prose_generator, "provider_name", backend)
    results = []
    for task in drafting_plan.tasks:
        parse_result = _generate_section_parse_result(
            task=task,
            claim_table=claim_table,
            evidence_map=evidence_map,
            narrative_contract=narrative_contract,
            prose_generator=prose_generator,
            backend=backend,
            provider=provider,
        )
        results.append(
            _section_result_from_parse(
                task=task,
                parse_result=parse_result,
                claim_table=claim_table,
                evidence_map=evidence_map,
            )
        )
    return results


def draft_manuscript(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    prose_generator,
    write_report: bool = False,
    include_citations: bool = False,
    max_words: int = 160,
) -> ManuscriptDraftingRunResult:
    """Draft all manuscript sections and optionally persist presentation artifacts."""
    inputs = load_manuscript_drafting_inputs(run_id, ledger)
    citation_registry = None
    literature_positioning_report = None
    if include_citations:
        citation_registry = build_citation_registry_from_ledger(run_id, ledger)
        literature_positioning_report = build_literature_positioning_report(
            run_id=run_id,
            citation_registry=citation_registry,
            narrative_contract=inputs.narrative_contract,
        )
    prose_backend = getattr(prose_generator, "backend_name", "fake")
    drafting_plan = build_manuscript_drafting_plan(
        run_id=run_id,
        manuscript_plan=inputs.manuscript_plan,
        claim_table=inputs.claim_table,
        narrative_contract=inputs.narrative_contract,
        paper_shape_critique=inputs.paper_shape_critique,
        citation_registry=citation_registry,
        literature_positioning_report=literature_positioning_report,
        prose_backend=prose_backend,
        max_words=max_words,
    )
    section_results = draft_manuscript_sections(
        drafting_plan=drafting_plan,
        claim_table=inputs.claim_table,
        narrative_contract=inputs.narrative_contract,
        prose_generator=prose_generator,
    )
    complete_draft, assembly_report = assemble_complete_markdown_draft(
        run_id=run_id,
        manuscript_plan=inputs.manuscript_plan,
        narrative_contract=inputs.narrative_contract,
        paper_shape_critique=inputs.paper_shape_critique,
        claim_table=inputs.claim_table,
        section_results=section_results,
        citation_registry=citation_registry,
        literature_positioning_report=literature_positioning_report,
    )
    citation_safety_report = (
        validate_citation_usage(complete_draft, citation_registry)
        if citation_registry is not None
        else None
    )
    if citation_safety_report is not None and not citation_safety_report.safe:
        assembly_report = assembly_report.model_copy(
            update={
                "warnings": sorted(
                    set([*assembly_report.warnings, *citation_safety_report.reasons])
                )
            }
        )
        complete_draft = complete_draft.model_copy(
            update={
                "warnings": sorted(
                    set([*complete_draft.warnings, *citation_safety_report.reasons])
                )
            }
        )
    drafting_report = _build_drafting_report(
        run_id=run_id,
        drafting_plan=drafting_plan,
        section_results=section_results,
        assembly_report=assembly_report,
        citation_registry=citation_registry,
        literature_positioning_report=literature_positioning_report,
        citation_safety_report=citation_safety_report,
    )
    result = ManuscriptDraftingRunResult(
        run_id=run_id,
        inputs=inputs,
        drafting_plan=drafting_plan,
        section_results=section_results,
        complete_draft=complete_draft,
        drafting_report=drafting_report,
        assembly_report=assembly_report,
        citation_registry=citation_registry,
        literature_positioning_report=literature_positioning_report,
        citation_safety_report=citation_safety_report,
    )
    if not write_report:
        return result
    return _write_manuscript_draft_artifacts(
        result=result,
        store=store,
        ledger=ledger,
    )


def _generate_section_parse_result(
    *,
    task: SectionDraftingTask,
    claim_table: ClaimTable,
    evidence_map: dict[str, dict[str, Any]],
    narrative_contract: NarrativeManuscriptContract,
    prose_generator,
    backend: str,
    provider: str,
) -> ProseGenerationParseResult:
    if isinstance(prose_generator, OpenAIProseGenerator):
        prompt = build_prose_section_prompt(
            task.prose_contract,
            claim_table,
            evidence_map,
            narrative_contract,
            backend=backend,
            provider=provider,
        )
        return prose_generator.generate_section_from_prompt(prompt)
    draft = prose_generator.generate_section(task.prose_contract, claim_table)
    return ProseGenerationParseResult(
        section_draft=draft,
        raw_response_type=type(draft).__name__,
        fake=getattr(prose_generator, "is_fake", False),
    )


def _section_result_from_parse(
    *,
    task: SectionDraftingTask,
    parse_result: ProseGenerationParseResult,
    claim_table: ClaimTable,
    evidence_map: dict[str, dict[str, Any]],
) -> SectionDraftingResult:
    if parse_result.section_draft is None:
        safety = ProseSafetyReport(
            section_id=task.section_id,
            safe=False,
            rejected=True,
            reasons=parse_result.reasons or ["prose generation did not produce a section draft"],
            warnings=[],
        )
        return _build_section_result(task, None, safety)
    safety = validate_generated_section(
        parse_result.section_draft,
        task.prose_contract,
        claim_table,
        evidence_map,
    )
    return _build_section_result(task, parse_result.section_draft, safety)


def _build_section_result(
    task: SectionDraftingTask,
    draft,
    safety: ProseSafetyReport,
) -> SectionDraftingResult:
    safety_status = "Safe" if safety.safe and not safety.rejected else "Unsafe"
    return SectionDraftingResult(
        section_id=task.section_id,
        section_title=task.section_title,
        section_role=task.section_role,
        narrative_role=task.narrative_role,
        draft_markdown=(
            safety.sanitized_content
            if draft is not None and safety.safe and safety.sanitized_content is not None
            else draft.content
            if draft is not None and safety.safe
            else ""
        ),
        used_claim_ids=safety.used_claim_ids,
        used_evidence_artifact_ids=safety.used_evidence_artifact_ids,
        used_citation_ids=safety.used_citation_ids,
        used_citation_keys=safety.used_citation_keys,
        allowed_statement_classes_used=safety.allowed_statement_classes_used,
        safety_status=safety_status,
        warnings=safety.warnings,
        unsupported_sentences=draft.unsupported_sentences if draft is not None else [],
        unsafe_sentences_removed=safety.unsafe_sentences_removed,
        safe_scaffold_sentences_retained=safety.safe_scaffold_sentences_retained,
        original_sentence_count=safety.original_sentence_count,
        removed_sentence_count=safety.removed_sentence_count,
        retained_sentence_count=safety.retained_sentence_count,
        section_status=safety.section_status,
        removal_reasons=safety.removal_reasons,
        source_contract_hashes=task.source_contract_hashes,
        safe=safety.safe,
        rejected=safety.rejected,
        safety_reasons=safety.reasons,
        draft=draft,
        safety_report=safety,
        fake=draft.fake if draft is not None else True,
    )


def _build_drafting_report(
    *,
    run_id: str,
    drafting_plan: ManuscriptDraftingPlan,
    section_results: list[SectionDraftingResult],
    assembly_report: ManuscriptAssemblyReport,
    citation_registry: CitationRegistry | None = None,
    literature_positioning_report: LiteraturePositioningReport | None = None,
    citation_safety_report: CitationSafetyReport | None = None,
) -> ManuscriptDraftingReport:
    summaries = [
        SectionDraftSafetySummary(
            section_id=result.section_id,
            safety_status=result.safety_status,
            safe=result.safe,
            rejected=result.rejected,
            reasons=result.safety_reasons,
            warnings=result.warnings,
            used_claim_ids=result.used_claim_ids,
            used_evidence_artifact_ids=result.used_evidence_artifact_ids,
            used_citation_ids=result.used_citation_ids,
            used_citation_keys=result.used_citation_keys,
            allowed_statement_classes_used=result.allowed_statement_classes_used,
            safe_scaffold_sentences_retained=result.safe_scaffold_sentences_retained,
            unsafe_sentences_removed=result.unsafe_sentences_removed,
            original_sentence_count=result.original_sentence_count,
            removed_sentence_count=result.removed_sentence_count,
            retained_sentence_count=result.retained_sentence_count,
            section_status=result.section_status,
            removal_reasons=result.removal_reasons,
            unsupported_sentences=result.unsupported_sentences,
            created_or_upgraded_labels=result.safety_report.created_or_upgraded_labels,
        )
        for result in section_results
    ]
    sections_safe = sum(1 for result in section_results if result.safe and not result.rejected)
    return ManuscriptDraftingReport(
        run_id=run_id,
        drafting_plan_id=drafting_plan.plan_id,
        prose_backend=drafting_plan.prose_backend,
        sections_total=len(section_results),
        sections_safe=sections_safe,
        sections_unsafe=len(section_results) - sections_safe,
        draft_status=assembly_report.draft_status,
        section_summaries=summaries,
        warnings=sorted(
            set(
                [
                    *assembly_report.warnings,
                    *(
                        citation_safety_report.warnings
                        if citation_safety_report is not None
                        else []
                    ),
                ]
            )
        ),
        manuscript_draft_artifact_id="complete-manuscript-draft",
        assembly_report_artifact_id="manuscript-assembly-report",
        citation_registry_artifact_id=(
            "citation-registry" if citation_registry is not None else None
        ),
        literature_positioning_artifact_id=(
            "literature-positioning-report"
            if literature_positioning_report is not None
            else None
        ),
        citation_safety_artifact_id=(
            "citation-safety-report" if citation_safety_report is not None else None
        ),
    )


def _write_manuscript_draft_artifacts(
    *,
    result: ManuscriptDraftingRunResult,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ManuscriptDraftingRunResult:
    metadata = {
        "stage": "manuscript_drafting",
        "artifact_role": "manuscript_prose_presentation_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
    }
    artifact_specs = [
        ArtifactWriteSpec(
            artifact_id="manuscript-drafting-plan",
            artifact_type=ArtifactType.REPORT,
            payload=result.drafting_plan,
            artifact_format="json",
            metadata=metadata,
        ),
        ArtifactWriteSpec(
            artifact_id="manuscript-drafting-report",
            artifact_type=ArtifactType.REPORT,
            payload=result.drafting_report,
            artifact_format="json",
            metadata=metadata,
        ),
        ArtifactWriteSpec(
            artifact_id="complete-manuscript-draft",
            artifact_type=ArtifactType.REPORT,
            payload=result.complete_draft.markdown,
            artifact_format="markdown",
            metadata={**metadata, "artifact_role": "presentation_manuscript_draft"},
        ),
        ArtifactWriteSpec(
            artifact_id="manuscript-assembly-report",
            artifact_type=ArtifactType.REPORT,
            payload=result.assembly_report,
            artifact_format="json",
            metadata=metadata,
        ),
    ]
    if result.citation_registry is not None:
        citation_metadata = {
            **metadata,
            "artifact_role": "literature_positioning_context",
            "proves_novelty": False,
            "claims_literature_coverage": False,
        }
        artifact_specs.extend(
            [
                ArtifactWriteSpec(
                    artifact_id="citation-registry",
                    artifact_type=ArtifactType.REPORT,
                    payload=result.citation_registry,
                    artifact_format="json",
                    metadata=citation_metadata,
                ),
                ArtifactWriteSpec(
                    artifact_id="literature-positioning-report",
                    artifact_type=ArtifactType.REPORT,
                    payload=result.literature_positioning_report,
                    artifact_format="json",
                    metadata=citation_metadata,
                ),
                ArtifactWriteSpec(
                    artifact_id="citation-safety-report",
                    artifact_type=ArtifactType.REPORT,
                    payload=result.citation_safety_report,
                    artifact_format="json",
                    metadata=citation_metadata,
                ),
            ]
        )
    persistence = persist_artifacts_with_commit(
        run_id=result.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=artifact_specs,
        action_type=ControllerActionType.MANUSCRIPT_DRAFT_WRITTEN,
        commit_payload={
            "run_id": result.run_id,
            "sections_total": result.drafting_report.sections_total,
            "sections_safe": result.drafting_report.sections_safe,
            "sections_unsafe": result.drafting_report.sections_unsafe,
            "draft_status": result.drafting_report.draft_status.value,
            "include_citations": result.citation_registry is not None,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
        },
    )
    return _with_artifacts(result, persistence)


def _with_artifacts(
    result: ManuscriptDraftingRunResult,
    persistence: PersistenceResult,
) -> ManuscriptDraftingRunResult:
    artifact_by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    drafting_report = result.drafting_report.model_copy(
        update={
            "manuscript_draft_artifact_id": "complete-manuscript-draft",
            "assembly_report_artifact_id": "manuscript-assembly-report",
        }
    )
    assembly_report = result.assembly_report.model_copy(
        update={"complete_markdown_artifact_id": "complete-manuscript-draft"}
    )
    return ManuscriptDraftingRunResult(
        run_id=result.run_id,
        inputs=result.inputs,
        drafting_plan=result.drafting_plan,
        section_results=result.section_results,
        complete_draft=result.complete_draft,
        drafting_report=drafting_report,
        assembly_report=assembly_report,
        plan_artifact=artifact_by_id["manuscript-drafting-plan"],
        drafting_report_artifact=artifact_by_id["manuscript-drafting-report"],
        markdown_artifact=artifact_by_id["complete-manuscript-draft"],
        assembly_report_artifact=artifact_by_id["manuscript-assembly-report"],
        citation_registry=result.citation_registry,
        literature_positioning_report=result.literature_positioning_report,
        citation_safety_report=result.citation_safety_report,
        citation_registry_artifact=artifact_by_id.get("citation-registry"),
        literature_positioning_artifact=artifact_by_id.get("literature-positioning-report"),
        citation_safety_artifact=artifact_by_id.get("citation-safety-report"),
        commit_hash=persistence.commit.commit_hash,
    )


__all__ = [
    "ManuscriptDraftingError",
    "ManuscriptDraftingInputs",
    "ManuscriptDraftingRunResult",
    "build_manuscript_drafting_plan",
    "draft_manuscript",
    "draft_manuscript_sections",
    "load_manuscript_drafting_inputs",
]
