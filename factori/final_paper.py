"""Local final-paper assembly, verification, and bounded release packaging (M106)."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.hashing import canonical_json, sha256_file, sha256_text
from factori.latex_render import LatexRenderer, build_latex_compile_check_report
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.production_mode import evaluate_production_mode, stage_backend_record
from factori.protocols import PROTOCOL_VERSION
from factori.reports import render_paper_skeleton_markdown
from factori.rerun_policy import validate_ledger_tip
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BackendKind,
    BlockedClaim,
    Claim,
    ClaimArtifactBinding,
    ClaimTable,
    ControllerActionType,
    CrossPackageAdjudicationReport,
    DraftClaimPlaceholder,
    DraftSkeleton,
    EvidenceCitationBinding,
    EvidencePackageExecutionReport,
    EvidencePackageExecutionResult,
    FinalPaperAppendixRecord,
    FinalPaperArtifactBinding,
    FinalPaperAssemblyConfig,
    FinalPaperAssemblyReport,
    FinalPaperFigureRecord,
    FinalPaperInspectionReport,
    FinalPaperManifest,
    FinalPaperOpenObligation,
    FinalPaperRenderReport,
    FinalPaperSectionRecord,
    FinalPaperTableRecord,
    FinalPaperVerificationFinding,
    FinalPaperVerificationReport,
    LatexRenderConfig,
    LedgerCommit,
    ManuscriptPlan,
    NucleusManuscriptDraft,
    NucleusManuscriptStatus,
    NucleusManuscriptSynthesisReport,
    PaperAppendix,
    PaperAssemblyReport,
    PaperSection,
    PaperSkeleton,
    ProductionModePolicy,
    ResearchObject,
    RetrievalContext,
    ScientificStageKind,
    StageBackendRecord,
    VerificationLabel,
)

_SYNTHESIS_RE = re.compile(r"^nucleus-manuscript-synthesis-report-(\d{4})\.json$")
_ASSEMBLY_RE = re.compile(r"^final-paper-assembly-report-(\d{4})\.json$")
_VERIFICATION_RE = re.compile(r"^final-paper-verification-report-(\d{4})\.json$")
_RENDER_RE = re.compile(r"^final-paper-render-report-(\d{4})\.json$")
_METRIC_SOURCE_RE = re.compile(
    r"^runs/[^/]+/experiments/[^/#]+\.json#metrics\.[A-Za-z0-9][A-Za-z0-9_.-]*$"
)
_MARKDOWN_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
_LATEX_IMAGE_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
_HEADING_RE = re.compile(r"^(#{2,6})\s+(.+?)\s*$")
_FORBIDDEN_PATTERNS = {
    "publication_ready=true": "publication readiness assertion",
    "publication ready": "publication readiness assertion",
    "ready for publication": "publication readiness assertion",
    "novelty proven": "novelty assertion",
    "novelty is proven": "novelty assertion",
    "underuse proven": "underuse assertion",
    "underuse is proven": "underuse assertion",
    "we prove": "proof assertion without checker evidence",
    "theorem proved": "proof assertion without checker evidence",
    "verified theorem": "proof assertion without checker evidence",
    "real-world validation": "real-world validation assertion",
    "real world validation": "real-world validation assertion",
}
_SECRET_RE = re.compile(
    r"(?:api[_-]?key|authorization\s*:\s*bearer|openai_api_key|secret(?:_key)?|token)"
    r"\s*(?:=|:|\s)\s*[^\s]{8,}",
    re.IGNORECASE,
)
_FIGURE_SUFFIXES = {".png", ".jpg", ".jpeg", ".pdf"}
_METRIC_RESULT_STATUSES = {"completed", "negative_result"}
_MAX_PAPER_TABLE_ROWS = 12
_MAX_PAPER_FIGURES = 2


class PaperAssemblyError(RuntimeError):
    """Raised when legacy deterministic paper-skeleton inputs are missing."""


@dataclass(frozen=True)
class PaperAssemblyInputs:
    """Ledger-loaded inputs for the legacy deterministic paper skeleton."""

    manuscript_plan: ManuscriptPlan
    draft_skeleton: DraftSkeleton
    claim_table: ClaimTable
    blocked_claims: list[BlockedClaim]
    research_object: ResearchObject


@dataclass(frozen=True)
class PaperAssemblyResult:
    """Result of legacy deterministic paper-skeleton assembly."""

    run_id: str
    manuscript_plan: ManuscriptPlan
    draft_skeleton: DraftSkeleton
    claim_table: ClaimTable
    blocked_claims: list[BlockedClaim]
    research_object: ResearchObject
    paper_skeleton: PaperSkeleton
    assembly_report: PaperAssemblyReport
    paper_json_artifact: ArtifactRef
    paper_markdown_artifact: ArtifactRef
    assembly_report_artifact: ArtifactRef


def load_paper_assembly_inputs(run_id: str, ledger: ResearchLedger) -> PaperAssemblyInputs:
    """Load deterministic paper-skeleton inputs from the append-only ledger."""
    commits = ledger.list_commits(run_id)
    research_commit = _latest_research_object_commit(commits)
    if research_commit is None:
        raise PaperAssemblyError(
            "Research object artifacts not found; run factori package-research-object first"
        )
    manuscript_commit = _require_commit(
        commits,
        ControllerActionType.MANUSCRIPT_PLAN_BUILT,
        "Manuscript plan not found; run factori plan-manuscript first",
    )
    draft_commit = _require_commit(
        commits,
        ControllerActionType.DRAFT_SKELETON_BUILT,
        "Draft skeleton not found; run factori build-draft-skeleton first",
    )
    claim_table_commit = _require_commit(
        commits,
        ControllerActionType.CLAIM_TABLE_BUILT,
        "Claim table not found; run factori plan-manuscript first",
    )
    blocked_claims_commit = _require_commit(
        commits,
        ControllerActionType.BLOCKED_CLAIMS_IDENTIFIED,
        "Blocked claims not found; run factori plan-manuscript first",
    )
    return PaperAssemblyInputs(
        manuscript_plan=ManuscriptPlan.model_validate(manuscript_commit.payload),
        draft_skeleton=DraftSkeleton.model_validate(draft_commit.payload),
        claim_table=ClaimTable.model_validate(claim_table_commit.payload),
        blocked_claims=[
            BlockedClaim.model_validate(item)
            for item in blocked_claims_commit.payload.get("blocked_claims", [])
        ],
        research_object=ResearchObject.model_validate(research_commit.payload),
    )


def assemble_paper_skeleton(
    run_id: str,
    manuscript_plan: ManuscriptPlan,
    draft_skeleton: DraftSkeleton,
    claim_table: ClaimTable,
    research_object: ResearchObject,
    blocked_claims: list[BlockedClaim] | None = None,
) -> PaperSkeleton:
    """Assemble the existing deterministic paper-shaped scaffold without adding claims."""
    blocked_claims = blocked_claims or []
    blocked_ids = {claim.claim_id for claim in blocked_claims} | set(
        manuscript_plan.blocked_claim_ids
    )
    claim_by_id = {claim.claim_id: claim for claim in claim_table.claims}
    placeholders_by_id = {
        placeholder.claim_id: placeholder for placeholder in draft_skeleton.claim_placeholders
    }
    sections = [
        _paper_section(section, claim_by_id, placeholders_by_id, blocked_ids)
        for section in manuscript_plan.sections
        if section.title not in {"Title", "Appendix"}
    ]
    included_placeholders = [
        placeholder for section in sections for placeholder in section.claim_placeholders
    ]
    return PaperSkeleton(
        paper_id=f"paper-skeleton-{research_object.final_nucleus.id}",
        run_id=run_id,
        title=manuscript_plan.title,
        abstract_scaffold=draft_skeleton.abstract_stub,
        sections=sections,
        appendices=_appendices(
            claim_table=claim_table,
            blocked_claims=blocked_claims,
            research_object=research_object,
        ),
        claim_placeholders=sorted(included_placeholders, key=lambda item: item.claim_id),
        provenance_refs=_provenance_refs(research_object),
    )


def build_paper_assembly_report(
    *,
    paper_skeleton: PaperSkeleton,
    manuscript_plan: ManuscriptPlan,
    draft_skeleton: DraftSkeleton,
    claim_table: ClaimTable,
    blocked_claims: list[BlockedClaim],
    research_object: ResearchObject,
) -> PaperAssemblyReport:
    """Compute the legacy deterministic paper-skeleton readiness view."""
    warnings: list[str] = []
    included_claim_ids = {placeholder.claim_id for placeholder in paper_skeleton.claim_placeholders}
    included_claims = [
        claim for claim in claim_table.claims if claim.claim_id in included_claim_ids
    ]
    blocked_ids = {claim.claim_id for claim in blocked_claims} | set(
        manuscript_plan.blocked_claim_ids
    )
    planned_main_claims = [
        claim
        for claim in claim_table.claims
        if claim.claim_id in manuscript_plan.allowed_claim_ids
        and claim.claim_id not in blocked_ids
        and claim.allowed_in_main_text
    ]
    for claim in included_claims:
        if claim.allowed_in_main_text and not claim.evidence_artifact_ids:
            warnings.append(f"{claim.claim_id}: missing evidence links for main claim")
        if _has_synthetic_real_world_inflation(claim):
            warnings.append(f"{claim.claim_id}: synthetic evidence framed as real-world validation")
        if _uses_presentation_evidence(claim):
            warnings.append(f"{claim.claim_id}: presentation artifact listed as evidence")
    for claim in planned_main_claims:
        if claim.claim_label == VerificationLabel.UNSUPPORTED:
            warnings.append(f"{claim.claim_id}: unsupported claim appears in main text")
    if not claim_table.claims:
        warnings.append("claim table is empty")
    if not draft_skeleton.section_stubs:
        warnings.append("draft skeleton has no sections")
    if not research_object.run_id:
        warnings.append("research object is missing")
    return PaperAssemblyReport(
        sections_count=len(paper_skeleton.sections),
        claims_included=len(included_claims),
        claims_blocked=len(blocked_claims),
        evidence_links_count=sum(len(claim.evidence_artifact_ids) for claim in included_claims),
        warnings=sorted(set(warnings)),
        ready_for_polished_prose=not warnings,
    )


def run_paper_assembly(
    *, run_id: str, store: ArtifactStore, ledger: ResearchLedger
) -> PaperAssemblyResult:
    """Run the legacy deterministic paper skeleton with its established ledger sequence."""
    store.init_run(run_id)
    inputs = load_paper_assembly_inputs(run_id, ledger)
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.PAPER_ASSEMBLY_STARTED,
        payload={
            "run_id": run_id,
            "research_object": inputs.research_object.run_id,
            "manuscript_plan": inputs.manuscript_plan.plan_id,
        },
    )
    paper_skeleton = assemble_paper_skeleton(
        run_id,
        inputs.manuscript_plan,
        inputs.draft_skeleton,
        inputs.claim_table,
        inputs.research_object,
        inputs.blocked_claims,
    )
    assembly_report = build_paper_assembly_report(
        paper_skeleton=paper_skeleton,
        manuscript_plan=inputs.manuscript_plan,
        draft_skeleton=inputs.draft_skeleton,
        claim_table=inputs.claim_table,
        blocked_claims=inputs.blocked_claims,
        research_object=inputs.research_object,
    )
    paper_json_artifact = _write_research_object_json(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id="paper-skeleton",
        payload=paper_skeleton,
        action_type=ControllerActionType.PAPER_SKELETON_WRITTEN,
        metadata={"package_part": "paper_skeleton"},
    )
    paper_markdown_artifact = _write_research_object_markdown(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id="paper-skeleton",
        markdown=render_paper_skeleton_markdown(paper_skeleton=paper_skeleton),
        action_type=ControllerActionType.PAPER_SKELETON_WRITTEN,
        metadata={"package_part": "paper_skeleton_markdown"},
    )
    assembly_report_artifact = _write_research_object_json(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_id="paper-assembly-report",
        payload=assembly_report,
        action_type=ControllerActionType.PAPER_ASSEMBLY_REPORT_WRITTEN,
        metadata={"package_part": "paper_assembly_report"},
    )
    return PaperAssemblyResult(
        run_id=run_id,
        manuscript_plan=inputs.manuscript_plan,
        draft_skeleton=inputs.draft_skeleton,
        claim_table=inputs.claim_table,
        blocked_claims=inputs.blocked_claims,
        research_object=inputs.research_object,
        paper_skeleton=paper_skeleton,
        assembly_report=assembly_report,
        paper_json_artifact=paper_json_artifact,
        paper_markdown_artifact=paper_markdown_artifact,
        assembly_report_artifact=assembly_report_artifact,
    )


def _paper_section(
    section: Any,
    claim_by_id: dict[str, Claim],
    placeholders_by_id: dict[str, DraftClaimPlaceholder],
    blocked_ids: set[str],
) -> PaperSection:
    placeholders: list[DraftClaimPlaceholder] = []
    warnings: list[str] = []
    for claim_id in section.allowed_claim_ids:
        claim = claim_by_id.get(claim_id)
        if claim is None or claim.claim_id in blocked_ids:
            continue
        if not _claim_allowed_in_section(claim, section.title):
            if claim.claim_label == VerificationLabel.UNSUPPORTED:
                warnings.append(f"{claim.claim_id}: unsupported claim excluded from main results")
            continue
        placeholders.append(
            placeholders_by_id.get(claim_id) or _placeholder_from_claim(claim)
        )
    evidence_ids = sorted(
        {
            evidence_id
            for placeholder in placeholders
            for evidence_id in placeholder.evidence_artifact_ids
        }
    )
    return PaperSection(
        section_id=section.section_id,
        title=section.title,
        purpose=" ".join(section.bullets),
        claim_placeholders=sorted(placeholders, key=lambda item: item.claim_id),
        evidence_artifact_ids=evidence_ids,
        warnings=warnings,
    )


def _claim_allowed_in_section(claim: Claim, section_title: str) -> bool:
    if claim.claim_label == VerificationLabel.UNSUPPORTED:
        return False
    if claim.claim_label == VerificationLabel.CONJECTURE:
        return section_title in {"Theory or Synthetic Experiments", "Appendix"}
    if claim.claim_label == VerificationLabel.NEGATIVE_RESULT:
        return section_title in {"Negative Results or Boundary Cases", "Results", "Limitations"}
    if claim.claim_label == VerificationLabel.LIMITATION:
        return section_title == "Limitations"
    if claim.claim_label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
        return section_title in {"Theory or Synthetic Experiments", "Results"}
    return True


def _appendices(
    *, claim_table: ClaimTable, blocked_claims: list[BlockedClaim], research_object: ResearchObject
) -> list[PaperAppendix]:
    claim_lines = [
        f"{claim.claim_id}: label={claim.claim_label.value}; candidate_id={claim.candidate_id}; "
        f"evidence={','.join(claim.evidence_artifact_ids) or 'none'}; "
        f"allowed_section={claim.allowed_section}"
        for claim in sorted(claim_table.claims, key=lambda item: item.claim_id)
    ] or ["no claims available"]
    blocked_lines = [
        f"{claim.claim_id}: label={claim.claim_label.value}; candidate_id={claim.candidate_id}; "
        f"reason={claim.blocked_reason}"
        for claim in sorted(blocked_claims, key=lambda item: item.claim_id)
    ] or ["none"]
    provenance_lines = [
        f"ledger_summary_ref={_artifact_path(research_object.ledger_summary_ref)}",
        f"artifact_manifest_ref={_artifact_path(research_object.artifact_manifest_ref)}",
        "reproducibility_manifest_ref="
        f"{_artifact_path(research_object.reproducibility_manifest_ref)}",
        "The ledger remains the source of truth; this paper skeleton is not provenance.",
    ]
    failed_lines = [
        f"branch_outcomes_ref={_artifact_path(research_object.branch_outcomes_ref)}",
        "Deferred, failed, rejected, and pruned branches remain in branch-outcomes.json.",
    ]
    return [
        PaperAppendix(
            appendix_id="appendix-a-claim-evidence",
            title="Appendix A: Claim/Evidence Table",
            content_lines=claim_lines,
            claim_ids=[claim.claim_id for claim in claim_table.claims],
        ),
        PaperAppendix(
            appendix_id="appendix-b-blocked-claims",
            title="Appendix B: Blocked or Downgraded Claims",
            content_lines=blocked_lines,
            claim_ids=[claim.claim_id for claim in blocked_claims],
        ),
        PaperAppendix(
            appendix_id="appendix-c-provenance",
            title="Appendix C: Provenance and Reproducibility",
            content_lines=provenance_lines,
            artifact_ref_ids=[
                ref.id
                for ref in [
                    research_object.ledger_summary_ref,
                    research_object.artifact_manifest_ref,
                    research_object.reproducibility_manifest_ref,
                ]
                if ref is not None
            ],
        ),
        PaperAppendix(
            appendix_id="appendix-d-branch-outcomes",
            title="Appendix D: Failed, Deferred, and Pruned Branches",
            content_lines=failed_lines,
            artifact_ref_ids=[
                research_object.branch_outcomes_ref.id
                if research_object.branch_outcomes_ref is not None
                else "missing"
            ],
        ),
    ]


def _placeholder_from_claim(claim: Claim) -> DraftClaimPlaceholder:
    evidence = ", ".join(claim.evidence_artifact_ids) or "no evidence"
    return DraftClaimPlaceholder(
        claim_id=claim.claim_id,
        candidate_id=claim.candidate_id,
        claim_label=claim.claim_label,
        placeholder_text=(
            f"[{claim.claim_label.value} claim placeholder: claim_id={claim.claim_id}; "
            f"evidence={evidence}]"
        ),
        evidence_artifact_ids=claim.evidence_artifact_ids,
        allowed_section=claim.allowed_section,
        warnings=[],
    )


def _provenance_refs(research_object: ResearchObject) -> dict[str, ArtifactRef]:
    refs = {
        "manuscript_plan": research_object.manuscript_plan_ref,
        "draft_skeleton": research_object.draft_skeleton_ref,
        "claim_table": research_object.claim_table_ref,
        "blocked_claims": research_object.blocked_claims_ref,
        "checklist": research_object.checklist_ref,
    }
    optional = {
        "artifact_manifest": research_object.artifact_manifest_ref,
        "ledger_summary": research_object.ledger_summary_ref,
        "branch_outcomes": research_object.branch_outcomes_ref,
        "reproducibility_manifest": research_object.reproducibility_manifest_ref,
    }
    refs.update({key: value for key, value in optional.items() if value is not None})
    return refs


def _has_synthetic_real_world_inflation(claim: Claim) -> bool:
    return claim.claim_label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED and any(
        marker in claim.claim_text.lower()
        for marker in [
            "real-world",
            "real world",
            "real markets",
            "real mobility",
            "public data",
            "user data",
        ]
    )


def _uses_presentation_evidence(claim: Claim) -> bool:
    return bool(
        {item.lower() for item in claim.evidence_types} & {"latex", "markdown", "presentation"}
    )


def _artifact_path(artifact: ArtifactRef | None) -> str:
    return artifact.path if artifact is not None else "missing"


def _write_research_object_json(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    artifact_id: str,
    payload: Any,
    action_type: ControllerActionType,
    metadata: dict[str, Any],
) -> ArtifactRef:
    path = _research_object_path(store, run_id, artifact_id, "json")
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    artifact = _artifact_ref(store, artifact_id, path, {"format": "json", **metadata})
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=action_type,
        payload=_payload_for_commit(payload),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _write_research_object_markdown(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    artifact_id: str,
    markdown: str,
    action_type: ControllerActionType,
    metadata: dict[str, Any],
) -> ArtifactRef:
    path = _research_object_path(store, run_id, artifact_id, "md")
    path.write_text(markdown, encoding="utf-8")
    artifact = _artifact_ref(store, artifact_id, path, {"format": "markdown", **metadata})
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=action_type,
        payload={"artifact_id": artifact_id, "format": "markdown", **metadata},
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def _research_object_path(
    store: ArtifactStore, run_id: str, artifact_id: str, extension: str
) -> Path:
    directory = store.run_path(run_id) / "research_object"
    directory.mkdir(parents=True, exist_ok=True)
    return directory / f"{artifact_id}.{extension}"


def _artifact_ref(
    store: ArtifactStore, artifact_id: str, path: Path, metadata: dict[str, Any]
) -> ArtifactRef:
    return ArtifactRef(
        id=artifact_id,
        type=ArtifactType.REPORT,
        path=path.relative_to(store.root).as_posix(),
        content_hash=sha256_file(path),
        metadata={"stage": "paper_assembly", "fake": True, **metadata},
    )


def _payload_for_commit(payload: Any) -> dict[str, Any]:
    if hasattr(payload, "model_dump"):
        return payload.model_dump(mode="json")
    if isinstance(payload, dict):
        return payload
    return {"payload": payload}


def _latest_research_object_commit(commits: list[LedgerCommit]) -> LedgerCommit | None:
    return next(
        (
            commit
            for commit in reversed(commits)
            if commit.action_type == ControllerActionType.RESEARCH_OBJECT_WRITTEN
            and "final_nucleus" in commit.payload
        ),
        None,
    )


def _require_commit(
    commits: list[LedgerCommit], action_type: ControllerActionType, message: str
) -> LedgerCommit:
    commit = next((item for item in reversed(commits) if item.action_type == action_type), None)
    if commit is None:
        raise PaperAssemblyError(message)
    return commit


class FinalPaperError(RuntimeError):
    """Raised only for corrupt M106 inputs that cannot produce a bounded report."""


@dataclass(frozen=True)
class FinalPaperResult:
    """One persisted M106 assembly, verification, or bundle operation."""

    run_id: str
    report: FinalPaperAssemblyReport | FinalPaperVerificationReport | FinalPaperRenderReport
    manifest_optional: FinalPaperManifest | None
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


@dataclass(frozen=True)
class _Inputs:
    root: Path
    run_id: str
    synthesis_path: Path | None
    synthesis: NucleusManuscriptSynthesisReport | None
    revised_draft: NucleusManuscriptDraft | None
    adjudication: CrossPackageAdjudicationReport | None
    execution: EvidencePackageExecutionReport | None
    retrieval_contexts: list[RetrievalContext]


@dataclass(frozen=True)
class _GeneratedFigureAsset:
    artifact_id: str
    filename_stem: str
    content: bytes


def assemble_final_paper(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    config: FinalPaperAssemblyConfig,
) -> FinalPaperResult:
    """Assemble local final-paper assets from the latest valid M105 revision.

    This operation does not author science. It copies the revised manuscript, reconstructs tables
    from execution outputs, adds provenance/open-obligation appendices, and persists a machine
    manifest. Missing required sources produce an append-only deferred assembly report.
    """
    _validate_config(run_id, config)
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    number = _next_number(reports, _ASSEMBLY_RE)
    report_id = f"final-paper-assembly-report-{number:04d}"
    inputs = _load_inputs(root_path, run_id)
    blockers = _assembly_prerequisites(inputs)
    if blockers:
        return _persist_assembly_report(
            report=_deferred_assembly_report(
                run_id=run_id,
                report_id=report_id,
                inputs=inputs,
                blockers=blockers,
            ),
            store=store,
            ledger=ledger,
        )

    assert inputs.synthesis is not None
    assert inputs.synthesis_path is not None
    assert inputs.revised_draft is not None
    assert inputs.adjudication is not None
    assert inputs.execution is not None

    bindings, binding_blockers = _resolve_artifact_bindings(inputs)
    tables, table_blockers = _build_tables(inputs, bindings)
    figures, figure_blockers, figure_assets = _build_figures(
        inputs,
        bindings,
        figure_prefix=f"final-paper-{number:04d}",
    )
    citations, citation_blockers = _resolve_citations(inputs)
    obligations = _build_open_obligations(inputs)
    manuscript_metric_blockers = _manuscript_metric_mismatch_reasons(
        inputs.revised_draft, tables
    )
    source_blockers = [
        *binding_blockers,
        *table_blockers,
        *figure_blockers,
        *citation_blockers,
        *manuscript_metric_blockers,
    ]
    if source_blockers:
        return _persist_assembly_report(
            report=_deferred_assembly_report(
                run_id=run_id,
                report_id=report_id,
                inputs=inputs,
                blockers=source_blockers,
                bindings=bindings,
                obligations=obligations,
            ),
            store=store,
            ledger=ledger,
        )

    backend_records = [
        *inputs.adjudication.backend_records,
        *inputs.synthesis.backend_records,
        *inputs.execution.backend_records,
        *_retrieval_backend_records(report_id, inputs.retrieval_contexts),
        _assembly_backend_record(report_id, [item.binding_id for item in bindings]),
    ]
    production = _production_report(
        run_id=run_id,
        records=backend_records,
        config=config,
        report_id=report_id,
        requires_metrics=bool(tables),
        requires_retrieval=any(
            item.source_type == "retrieval_source"
            for item in inputs.synthesis.evidence_citation_bindings
        ),
    )
    if config.require_non_fake_backends and production.blocking_violation_count:
        return _persist_assembly_report(
            report=_deferred_assembly_report(
                run_id=run_id,
                report_id=report_id,
                inputs=inputs,
                blockers=_production_blockers(production),
                bindings=bindings,
                obligations=obligations,
                backend_records=backend_records,
            ),
            store=store,
            ledger=ledger,
        )

    section_records = _section_records(
        inputs.revised_draft, inputs.synthesis.claim_artifact_bindings
    )
    appendices = _appendix_records(inputs, obligations, config)
    markdown = _assemble_markdown(
        draft=inputs.revised_draft,
        tables=tables,
        figures=figures,
        appendices=appendices,
        obligations=obligations,
        citation_bindings=citations,
        retrieval_contexts=inputs.retrieval_contexts,
        config=config,
    )
    latex = _assemble_latex(
        draft=inputs.revised_draft,
        tables=tables,
        figures=figures,
        appendices=appendices,
        obligations=obligations,
        citation_bindings=citations,
        retrieval_contexts=inputs.retrieval_contexts,
    )
    final_blockers = [
        *_forbidden_claim_reasons(markdown, latex),
        *_scope_qualification_reasons(markdown, inputs.synthesis.claim_artifact_bindings),
    ]
    if final_blockers:
        return _persist_assembly_report(
            report=_deferred_assembly_report(
                run_id=run_id,
                report_id=report_id,
                inputs=inputs,
                blockers=final_blockers,
                bindings=bindings,
                obligations=obligations,
                backend_records=backend_records,
            ),
            store=store,
            ledger=ledger,
        )

    final_id = f"final-paper-{number:04d}"
    manifest_id = f"final-paper-manifest-{number:04d}"
    claim_map_id = f"final-paper-claim-artifact-map-{number:04d}"
    citation_id = f"final-paper-evidence-citation-bindings-{number:04d}"
    provenance_id = f"final-paper-provenance-manifest-{number:04d}"
    bibliography = _build_bibliography(citations, inputs.retrieval_contexts)
    final_status = _assembled_status(inputs.synthesis.manuscript_status, obligations)
    manifest = FinalPaperManifest(
        manifest_id=manifest_id,
        run_id=run_id,
        source_manuscript_revision_id=inputs.revised_draft.draft_id,
        paper_nucleus_selection_id=inputs.synthesis.paper_nucleus_selection_id_optional
        or inputs.adjudication.report_id,
        title=inputs.revised_draft.title,
        paper_type=inputs.synthesis.manuscript_plan_optional.paper_type,
        manuscript_status=inputs.synthesis.manuscript_status,
        main_markdown_path=f"runs/{run_id}/reports/{final_id}.md",
        main_latex_path=f"runs/{run_id}/latex/{final_id}.tex",
        bibliography_path_optional=(
            f"runs/{run_id}/reports/{final_id}-references.bib" if bibliography else None
        ),
        section_records=section_records,
        artifact_bindings=bindings,
        figure_records=figures,
        table_records=tables,
        appendix_records=appendices,
        claim_artifact_map_path=f"runs/{run_id}/reports/{claim_map_id}.json",
        evidence_citation_bindings_path=f"runs/{run_id}/reports/{citation_id}.json",
        provenance_manifest_path=f"runs/{run_id}/reports/{provenance_id}.json",
        open_obligations=obligations,
        warnings=_unique(
            [
                *inputs.synthesis.unresolved_obligations,
                *(
                    ["No retrieved-source bibliography entries were required by this nucleus."]
                    if not bibliography
                    else []
                ),
            ]
        ),
    )
    provenance = _build_provenance_manifest(
        inputs=inputs,
        manifest=manifest,
        backend_records=backend_records,
        config=config,
    )
    report = FinalPaperAssemblyReport(
        run_id=run_id,
        report_id=report_id,
        operation="assembly",
        assembly_status="assembled",
        final_paper_status=final_status,
        source_manuscript_synthesis_report_path_optional=_relative(
            root_path, inputs.synthesis_path
        ),
        source_manuscript_revision_id_optional=inputs.revised_draft.draft_id,
        paper_nucleus_selection_id_optional=inputs.synthesis.paper_nucleus_selection_id_optional,
        manifest_path_optional=f"runs/{run_id}/reports/{manifest_id}.json",
        final_markdown_path_optional=manifest.main_markdown_path,
        final_latex_path_optional=manifest.main_latex_path,
        bibliography_path_optional=manifest.bibliography_path_optional,
        provenance_manifest_path_optional=manifest.provenance_manifest_path,
        section_count=len(section_records),
        figure_count=len(figures),
        table_count=len(tables),
        appendix_count=len(appendices),
        claim_artifact_binding_count=len(inputs.synthesis.claim_artifact_bindings),
        resolved_claim_artifact_binding_count=_resolved_claim_count(
            inputs.synthesis.claim_artifact_bindings, bindings
        ),
        evidence_citation_binding_count=len(inputs.synthesis.evidence_citation_bindings),
        resolved_evidence_citation_binding_count=len(citations),
        open_obligations=obligations,
        warnings=manifest.warnings,
        backend_records=backend_records,
        production_ready=(
            config.require_non_fake_backends and not production.blocking_violation_count
        ),
    )
    return _persist_assembly_report(
        report=report,
        manifest=manifest,
        final_markdown=markdown,
        final_latex=latex,
        bibliography=bibliography,
        claim_bindings=inputs.synthesis.claim_artifact_bindings,
        citation_bindings=citations,
        provenance=provenance,
        tables=tables,
        figure_assets=figure_assets,
        store=store,
        ledger=ledger,
    )


def verify_final_paper(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    require_non_fake_backends: bool = False,
) -> FinalPaperResult:
    """Verify an assembled final paper from immutable source paths and manifest hashes."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    number = _next_number(reports, _VERIFICATION_RE)
    report_id = f"final-paper-verification-report-{number:04d}"
    assembly_path, assembly = _latest_assembly_report(reports, operation="assembly")
    verification_record = _verification_backend_record(report_id, [])
    if assembly_path is None or assembly is None:
        return _persist_verification_report(
            report=_deferred_verification_report(
                run_id=run_id,
                report_id=report_id,
                source_assembly_path=None,
                reason="No assembled final paper is available; run assemble-final-paper first.",
                backend_records=[verification_record],
            ),
            store=store,
            ledger=ledger,
        )
    if assembly.assembly_status != "assembled" or not assembly.manifest_path_optional:
        return _persist_verification_report(
            report=_deferred_verification_report(
                run_id=run_id,
                report_id=report_id,
                source_assembly_path=_relative(root_path, assembly_path),
                reason="Latest final-paper assembly is deferred or does not contain a manifest.",
                backend_records=[*assembly.backend_records, verification_record],
            ),
            store=store,
            ledger=ledger,
        )

    manifest_path = _path_from_relative(root_path, assembly.manifest_path_optional)
    if not manifest_path.is_file():
        return _persist_verification_report(
            report=_deferred_verification_report(
                run_id=run_id,
                report_id=report_id,
                source_assembly_path=_relative(root_path, assembly_path),
                reason="The final-paper manifest referenced by the assembly is missing.",
                backend_records=[*assembly.backend_records, verification_record],
            ),
            store=store,
            ledger=ledger,
        )
    manifest = _read_model(manifest_path, FinalPaperManifest)
    findings: list[FinalPaperVerificationFinding] = []
    _verify_required_paths(root_path, manifest, findings)
    _verify_artifact_hashes(root_path, manifest, findings)
    _verify_claim_bindings(root_path, manifest, findings)
    _verify_metric_tables(root_path, manifest, findings)
    _verify_figures(root_path, manifest, findings)
    _verify_citations(root_path, manifest, findings)
    _verify_markdown_boundaries(root_path, manifest, findings)
    _verify_open_obligations(manifest, findings)
    records = [*assembly.backend_records, verification_record]
    production = _production_report(
        run_id=run_id,
        records=records,
        config=FinalPaperAssemblyConfig(
            run_id=run_id,
            require_non_fake_backends=require_non_fake_backends,
        ),
        report_id=report_id,
        requires_metrics=bool(manifest.table_records),
        requires_retrieval=bool(manifest.bibliography_path_optional),
        includes_verification=True,
    )
    for message in _production_blockers(production):
        findings.append(
            _finding(
                len(findings) + 1,
                "backend_provenance_gap",
                message,
                blocking=True,
            )
        )
    blocked = [item for item in findings if item.blocking]
    warned = [item for item in findings if not item.blocking]
    if blocked:
        status = "failed"
    elif warned:
        status = "verified_with_warnings"
    else:
        status = "verified"
    final_status = _verified_status(assembly.final_paper_status, status)
    claims = _load_claim_bindings(root_path, manifest)
    citations = _load_citation_bindings(root_path, manifest)
    report = FinalPaperVerificationReport(
        run_id=run_id,
        report_id=report_id,
        source_assembly_report_path=_relative(root_path, assembly_path),
        source_manifest_path_optional=assembly.manifest_path_optional,
        verification_status=status,
        final_paper_status=final_status,
        checks_run=8,
        checks_passed=max(0, 8 - len({item.finding_type for item in findings})),
        checks_failed=len(blocked),
        checks_warned=len(warned),
        claim_artifact_binding_count=len(claims),
        resolved_claim_artifact_binding_count=_resolved_claim_count(
            claims, manifest.artifact_bindings
        ),
        evidence_citation_binding_count=len(citations),
        resolved_evidence_citation_binding_count=len(citations),
        figure_count=len(manifest.figure_records),
        table_count=len(manifest.table_records),
        hash_mismatch_count=sum(item.finding_type == "hash_mismatch" for item in findings),
        missing_required_artifact_count=sum(
            item.finding_type == "missing_artifact" for item in findings
        ),
        findings=findings,
        open_obligations=manifest.open_obligations,
        backend_records=records,
        production_ready=(require_non_fake_backends and not production.blocking_violation_count),
    )
    return _persist_verification_report(report=report, store=store, ledger=ledger)


def render_final_paper(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    config: LatexRenderConfig,
    renderer: LatexRenderer | None = None,
) -> FinalPaperResult:
    """Render the latest verified final-paper LaTeX as a persisted presentation PDF."""
    if config.run_id != run_id:
        raise FinalPaperError("LatexRenderConfig.run_id must match run_id.")
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    number = _next_number(reports, _RENDER_RE)
    report_id = f"final-paper-render-report-{number:04d}"
    assembly_path, assembly = _latest_assembly_report(reports, operation="assembly")
    verification_path, verification = _latest_verification_report(reports)
    if (
        assembly_path is None
        or assembly is None
        or verification_path is None
        or verification is None
        or verification.verification_status not in {"verified", "verified_with_warnings"}
        or not assembly.final_latex_path_optional
    ):
        report = FinalPaperRenderReport(
            report_id=report_id,
            run_id=run_id,
            render_status="deferred",
            source_assembly_report_path_optional=(
                _relative(root_path, assembly_path) if assembly_path else None
            ),
            source_verification_report_path_optional=(
                _relative(root_path, verification_path) if verification_path else None
            ),
            blocking_findings=[
                "A verified final-paper assembly is required before rendering a PDF."
            ],
        )
        return _persist_render_report(
            report=report,
            standalone_latex=None,
            pdf_bytes=None,
            store=store,
            ledger=ledger,
        )

    source_path = _path_from_relative(root_path, assembly.final_latex_path_optional)
    if not source_path.is_file():
        raise FinalPaperError(f"Final-paper LaTeX source is missing: {source_path}")
    standalone = _standalone_final_latex(_read_text(source_path))
    standalone = _resolve_latex_graphics(standalone, root_path)
    rendered = (renderer or LatexRenderer()).render_document(standalone, config)
    pdf_id = f"final-paper-render-{number:04d}-pdf"
    render_result = rendered.result.model_copy(
        update={
            "rendered_pdf_artifact_id": pdf_id if rendered.pdf_bytes is not None else None
        }
    )
    compile_check = build_latex_compile_check_report(
        config=config,
        render_result=render_result,
    )
    standalone_id = f"final-paper-render-{number:04d}"
    report = FinalPaperRenderReport(
        report_id=report_id,
        run_id=run_id,
        render_status="rendered" if compile_check.passed and rendered.pdf_bytes else "failed",
        source_assembly_report_path_optional=_relative(root_path, assembly_path),
        source_verification_report_path_optional=_relative(root_path, verification_path),
        source_latex_path_optional=assembly.final_latex_path_optional,
        standalone_latex_path_optional=f"runs/{run_id}/latex/{standalone_id}.tex",
        rendered_pdf_path_optional=(
            f"runs/{run_id}/latex/{standalone_id}.pdf" if rendered.pdf_bytes else None
        ),
        compile_check_report=compile_check,
        blocking_findings=[] if compile_check.passed else [render_result.reason],
        warnings=list(compile_check.warnings),
        production_ready=bool(compile_check.passed and rendered.pdf_bytes),
    )
    return _persist_render_report(
        report=report,
        standalone_latex=standalone,
        pdf_bytes=rendered.pdf_bytes,
        store=store,
        ledger=ledger,
    )


def build_final_paper_bundle(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    require_rendered_pdf: bool = False,
) -> FinalPaperResult:
    """Build a self-contained, hash-locked directory from a successfully verified final paper."""
    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    reports = run_path / "reports"
    number = _next_number(reports, _ASSEMBLY_RE)
    report_id = f"final-paper-assembly-report-{number:04d}"
    assembly_path, assembly = _latest_assembly_report(reports, operation="assembly")
    verification_path, verification = _latest_verification_report(reports)
    render_path, render = _latest_render_report(reports)
    record = _assembly_backend_record(report_id, [])
    if (
        assembly_path is None
        or assembly is None
        or verification_path is None
        or verification is None
        or verification.verification_status not in {"verified", "verified_with_warnings"}
        or (
            require_rendered_pdf
            and (
                render_path is None
                or render is None
                or render.render_status != "rendered"
                or not render.rendered_pdf_path_optional
            )
        )
        or not assembly.manifest_path_optional
    ):
        report = FinalPaperAssemblyReport(
            run_id=run_id,
            report_id=report_id,
            operation="bundle",
            assembly_status="deferred",
            final_paper_status="deferred",
            blocking_findings=[
                "A verified final paper"
                + (" with a successful PDF render" if require_rendered_pdf else "")
                + " is required before building a release bundle."
            ],
            backend_records=[record],
        )
        return _persist_assembly_report(report=report, store=store, ledger=ledger)

    manifest_path = _path_from_relative(root_path, assembly.manifest_path_optional)
    manifest = _read_model(manifest_path, FinalPaperManifest)
    source_files, blockers = _bundle_sources(
        root_path=root_path,
        manifest=manifest,
        assembly_path=assembly_path,
        verification_path=verification_path,
        verification=verification,
        render_path=render_path,
        render=render,
    )
    if blockers:
        report = FinalPaperAssemblyReport(
            run_id=run_id,
            report_id=report_id,
            operation="bundle",
            assembly_status="deferred",
            final_paper_status=assembly.final_paper_status,
            source_manuscript_synthesis_report_path_optional=assembly.source_manuscript_synthesis_report_path_optional,
            source_manuscript_revision_id_optional=assembly.source_manuscript_revision_id_optional,
            paper_nucleus_selection_id_optional=assembly.paper_nucleus_selection_id_optional,
            manifest_path_optional=assembly.manifest_path_optional,
            blocking_findings=blockers,
            backend_records=[*assembly.backend_records, record],
        )
        return _persist_assembly_report(report=report, store=store, ledger=ledger)

    bundle_id = f"final-paper-bundle-{number:04d}"
    bundle_dir = run_path / "final-paper-bundles" / bundle_id
    if bundle_dir.exists():
        raise FinalPaperError(f"Final paper bundle already exists: {bundle_dir}")
    staging_dir = bundle_dir.with_name(f".{bundle_id}.tmp")
    if staging_dir.exists():
        raise FinalPaperError(
            "A prior incomplete final-paper bundle staging directory exists: "
            f"{staging_dir}"
        )
    _write_bundle(
        bundle_dir=staging_dir,
        root_path=root_path,
        manifest=manifest,
        source_files=source_files,
        assembly=assembly,
        verification=verification,
    )
    staging_hashes_path = staging_dir / "reproducibility" / "hashes.sha256"
    _write_hash_lock(staging_dir, staging_hashes_path)
    os.replace(staging_dir, bundle_dir)
    hashes_path = bundle_dir / "reproducibility" / "hashes.sha256"
    report = FinalPaperAssemblyReport(
        run_id=run_id,
        report_id=report_id,
        operation="bundle",
        assembly_status="assembled",
        final_paper_status=verification.final_paper_status,
        source_manuscript_synthesis_report_path_optional=assembly.source_manuscript_synthesis_report_path_optional,
        source_manuscript_revision_id_optional=assembly.source_manuscript_revision_id_optional,
        paper_nucleus_selection_id_optional=assembly.paper_nucleus_selection_id_optional,
        manifest_path_optional=assembly.manifest_path_optional,
        final_markdown_path_optional=assembly.final_markdown_path_optional,
        final_latex_path_optional=assembly.final_latex_path_optional,
        bibliography_path_optional=assembly.bibliography_path_optional,
        provenance_manifest_path_optional=assembly.provenance_manifest_path_optional,
        bundle_path_optional=_relative(root_path, bundle_dir),
        bundle_hashes_path_optional=_relative(root_path, hashes_path),
        section_count=assembly.section_count,
        figure_count=assembly.figure_count,
        table_count=assembly.table_count,
        appendix_count=assembly.appendix_count,
        claim_artifact_binding_count=assembly.claim_artifact_binding_count,
        resolved_claim_artifact_binding_count=assembly.resolved_claim_artifact_binding_count,
        evidence_citation_binding_count=assembly.evidence_citation_binding_count,
        resolved_evidence_citation_binding_count=assembly.resolved_evidence_citation_binding_count,
        open_obligations=assembly.open_obligations,
        warnings=verification.findings
        and ["Bundle preserves final-paper verification warnings without changing their scope."]
        or [],
        backend_records=[*assembly.backend_records, record],
        production_ready=verification.production_ready,
    )
    return _persist_assembly_report(report=report, store=store, ledger=ledger)


def inspect_final_paper(*, run_id: str, root: str | Path = ".") -> FinalPaperInspectionReport:
    """Read the latest M106 assembly, verification, and bundle records without mutation."""
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    assembly_path, assembly = _latest_assembly_report(reports, operation="assembly")
    if assembly_path is None or assembly is None:
        return FinalPaperInspectionReport(run_id=run_id, final_paper_present=False)
    _, verification = _latest_verification_report(reports)
    _, render = _latest_render_report(reports)
    _, bundle = _latest_assembly_report(reports, operation="bundle")
    return FinalPaperInspectionReport(
        run_id=run_id,
        final_paper_present=assembly.assembly_status == "assembled",
        latest_assembly_report_id_optional=assembly.report_id,
        assembly_status_optional=assembly.assembly_status,
        final_paper_status_optional=(
            verification.final_paper_status if verification else assembly.final_paper_status
        ),
        final_markdown_path_optional=assembly.final_markdown_path_optional,
        final_latex_path_optional=assembly.final_latex_path_optional,
        manifest_path_optional=assembly.manifest_path_optional,
        verification_present=verification is not None,
        verification_status_optional=verification.verification_status if verification else None,
        render_present=render is not None and render.render_status == "rendered",
        render_status_optional=render.render_status if render else None,
        standalone_latex_path_optional=(
            render.standalone_latex_path_optional if render else None
        ),
        rendered_pdf_path_optional=render.rendered_pdf_path_optional if render else None,
        bundle_present=bundle is not None and bundle.assembly_status == "assembled",
        bundle_path_optional=bundle.bundle_path_optional if bundle else None,
        section_count=assembly.section_count,
        figure_count=assembly.figure_count,
        table_count=assembly.table_count,
        appendix_count=assembly.appendix_count,
        claim_artifact_binding_count=assembly.claim_artifact_binding_count,
        resolved_claim_artifact_binding_count=assembly.resolved_claim_artifact_binding_count,
        evidence_citation_binding_count=assembly.evidence_citation_binding_count,
        resolved_evidence_citation_binding_count=assembly.resolved_evidence_citation_binding_count,
        open_obligations=assembly.open_obligations,
        verification_findings=verification.findings if verification else [],
        backend_records=[
            *assembly.backend_records,
            *(verification.backend_records if verification else []),
        ],
        warnings=[
            *assembly.warnings,
            *(item.description for item in verification.findings if not item.blocking),
        ]
        if verification
        else list(assembly.warnings),
        production_ready=(
            verification.production_ready if verification else assembly.production_ready
        ),
    )


def render_final_paper_text(report: FinalPaperInspectionReport) -> str:
    """Render a concise human inspection view without treating it as scientific evidence."""
    return "\n".join(
        [
            "Final paper: " + ("present" if report.final_paper_present else "absent"),
            "Assembly status: " + (report.assembly_status_optional or "not available"),
            "Verification status: " + (report.verification_status_optional or "not available"),
            "Render status: " + (report.render_status_optional or "not available"),
            "Rendered PDF: " + (report.rendered_pdf_path_optional or "not available"),
            f"Sections/figures/tables/appendices: {report.section_count}/{report.figure_count}/"
            f"{report.table_count}/{report.appendix_count}",
            f"Resolved claim/citation bindings: {report.resolved_claim_artifact_binding_count}/"
            f"{report.resolved_evidence_citation_binding_count}",
            f"Open obligations: {len(report.open_obligations)}",
            f"Production ready: {str(report.production_ready).lower()}",
            "publication_ready=false",
        ]
    )


def render_final_paper_assembly_markdown(report: FinalPaperAssemblyReport) -> str:
    """Render a non-evidence local final-paper assembly report."""
    return (
        "\n".join(
            [
                "# Final Paper Assembly Report",
                "",
                f"- Operation: `{report.operation}`",
                f"- Assembly status: `{report.assembly_status}`",
                f"- Final paper status: `{report.final_paper_status}`",
                f"- Sections / figures / tables / appendices: `{report.section_count}` / "
                f"`{report.figure_count}` / `{report.table_count}` / `{report.appendix_count}`",
                f"- Resolved claim bindings: `{report.resolved_claim_artifact_binding_count}` / "
                f"`{report.claim_artifact_binding_count}`",
                "- Resolved citation bindings: "
                f"`{report.resolved_evidence_citation_binding_count}` / "
                f"`{report.evidence_citation_binding_count}`",
                f"- Bundle: `{report.bundle_path_optional or 'not built'}`",
                "",
                "This is local assembly and packaging context. It does not create scientific "
                "evidence, proof verification, novelty proof, real-world validation, or "
                "publication readiness.",
                "",
                "publication_ready=false",
                *(
                    [
                        "",
                        "## Blocking Findings",
                        *[f"- {item}" for item in report.blocking_findings],
                    ]
                    if report.blocking_findings
                    else []
                ),
                *(
                    [
                        "",
                        "## Open Obligations",
                        *[f"- {item.description}" for item in report.open_obligations],
                    ]
                    if report.open_obligations
                    else []
                ),
            ]
        )
        + "\n"
    )


def render_final_paper_verification_markdown(report: FinalPaperVerificationReport) -> str:
    """Render local final-paper verification findings."""
    return "\n".join(
        [
            "# Final Paper Verification Report",
            "",
            f"- Status: `{report.verification_status}`",
            f"- Final paper status: `{report.final_paper_status}`",
            f"- Checks passed / failed / warned: `{report.checks_passed}` / "
            f"`{report.checks_failed}` / `{report.checks_warned}`",
            f"- Hash mismatches: `{report.hash_mismatch_count}`",
            f"- Missing required artifacts: `{report.missing_required_artifact_count}`",
            "",
            "## Findings",
            *[
                f"- `{'blocking' if item.blocking else 'warning'}` `{item.finding_type}`: "
                f"{item.description}"
                for item in report.findings
            ],
            "",
            "Verification confirms only structural consistency of bounded presentation artifacts. "
            "It is not publication readiness, novelty proof, theorem verification, or real-world "
            "validation.",
            "",
            "publication_ready=false",
            "",
        ]
    )


def render_final_paper_render_markdown(report: FinalPaperRenderReport) -> str:
    """Render local PDF compilation context without scientific authority."""
    return "\n".join(
        [
            "# Final Paper Render Report",
            "",
            f"- Status: `{report.render_status}`",
            f"- Source LaTeX: `{report.source_latex_path_optional or 'not available'}`",
            f"- Standalone LaTeX: `{report.standalone_latex_path_optional or 'not written'}`",
            f"- Rendered PDF: `{report.rendered_pdf_path_optional or 'not written'}`",
            "",
            "This PDF is presentation context only. Compilation does not create scientific "
            "evidence, validation, human approval, or publication readiness.",
            "",
            "publication_ready=false",
            *(
                ["", "## Blocking Findings", *[f"- {item}" for item in report.blocking_findings]]
                if report.blocking_findings
                else []
            ),
        ]
    ) + "\n"


def _load_inputs(root: Path, run_id: str) -> _Inputs:
    reports = root / "runs" / run_id / "reports"
    synthesis_path, synthesis = _latest_valid_revision(reports)
    revised = synthesis.revised_draft_optional if synthesis is not None else None
    adjudication = None
    if synthesis is not None and synthesis.source_adjudication_report_path:
        path = _path_from_relative(root, synthesis.source_adjudication_report_path)
        if path.is_file():
            adjudication = _read_model(path, CrossPackageAdjudicationReport)
    execution_path = _latest_matching(
        reports, re.compile(r"^evidence-package-execution-report-(\d{4})\.json$")
    )
    execution = (
        _read_model(execution_path, EvidencePackageExecutionReport) if execution_path else None
    )
    retrieval = _load_retrieval_contexts(reports)
    return _Inputs(
        root=root,
        run_id=run_id,
        synthesis_path=synthesis_path,
        synthesis=synthesis,
        revised_draft=revised,
        adjudication=adjudication,
        execution=execution,
        retrieval_contexts=retrieval,
    )


def _assembly_prerequisites(inputs: _Inputs) -> list[str]:
    if inputs.synthesis is None or inputs.synthesis_path is None:
        return ["No valid revised nucleus manuscript exists; run revise-nucleus-manuscript first."]
    if inputs.synthesis.manuscript_status == NucleusManuscriptStatus.MANUSCRIPT_DEFERRED:
        return ["Latest nucleus manuscript is deferred and cannot be assembled."]
    if inputs.revised_draft is None:
        return ["Latest nucleus manuscript report does not contain a valid revised draft."]
    if inputs.synthesis.revision_report_optional is None or (
        inputs.synthesis.revision_report_optional.status != "revised"
    ):
        return ["Latest nucleus manuscript revision did not complete successfully."]
    if inputs.synthesis.blocking_reasons:
        return ["Latest nucleus manuscript retains blocking findings."]
    if not inputs.synthesis.claim_artifact_bindings:
        return ["Required claim-artifact bindings are missing."]
    if not inputs.synthesis.evidence_citation_bindings:
        return ["Required evidence-citation bindings are missing."]
    if inputs.synthesis.manuscript_plan_optional is None:
        return ["Latest revised manuscript has no persisted manuscript plan."]
    if inputs.adjudication is None or inputs.adjudication.paper_nucleus_selection_optional is None:
        return ["The source paper nucleus selection is missing."]
    if inputs.execution is None:
        return ["No hybrid evidence execution report is available for claim resolution."]
    return []


def _resolve_artifact_bindings(
    inputs: _Inputs,
) -> tuple[list[FinalPaperArtifactBinding], list[str]]:
    assert inputs.synthesis is not None
    assert inputs.execution is not None
    results = {item.result_id: item for item in inputs.execution.results}
    bindings: list[FinalPaperArtifactBinding] = []
    blockers: list[str] = []
    required_ids = {
        artifact_id
        for claim in inputs.synthesis.claim_artifact_bindings
        for artifact_id in claim.supporting_artifact_ids
    }
    for citation in inputs.synthesis.evidence_citation_bindings:
        if citation.source_type == "execution_artifact":
            required_ids.add(citation.artifact_id)
    for artifact_id in sorted(required_ids):
        result = results.get(artifact_id)
        path = _artifact_path_for_id(inputs.root, inputs.run_id, artifact_id)
        resolved = result is not None and path is not None and path.is_file()
        if not resolved:
            blockers.append(f"Required evidence artifact is unresolved: {artifact_id}.")
        claim_ids = [
            item.claim_id
            for item in inputs.synthesis.claim_artifact_bindings
            if artifact_id in item.supporting_artifact_ids
        ]
        binding = FinalPaperArtifactBinding(
            binding_id=f"final-paper-artifact-{_slug(artifact_id)}",
            artifact_id=artifact_id,
            artifact_type=(result.artifact_type.value if result is not None else "unknown"),
            source_stage="hybrid_evidence_execution",
            source_backend=_result_backend(inputs.execution, result),
            content_hash=sha256_file(path) if resolved and path is not None else _empty_hash(),
            manuscript_locations=["main_text"],
            claim_ids=claim_ids,
            evidence_label=result.evidence_label if result is not None else "InconclusiveResult",
            required=True,
            resolved=resolved,
        )
        bindings.append(binding)
    for citation in inputs.synthesis.evidence_citation_bindings:
        if citation.source_type != "retrieval_source":
            continue
        path = _artifact_path_for_id(inputs.root, inputs.run_id, citation.artifact_id)
        source = _retrieval_source_for_binding(citation, inputs.retrieval_contexts)
        resolved = path is not None and path.is_file() and source is not None
        if not resolved:
            blockers.append(
                "Retrieval citation binding cannot be resolved to a real retrieved source: "
                f"{citation.binding_id}."
            )
        bindings.append(
            FinalPaperArtifactBinding(
                binding_id=f"final-paper-artifact-{_slug(citation.binding_id)}",
                artifact_id=citation.artifact_id,
                artifact_type="retrieval_context",
                source_stage="literature_retrieval",
                source_backend="retrieval_real" if source is not None else "unknown",
                content_hash=sha256_file(path)
                if path is not None and path.is_file()
                else _empty_hash(),
                manuscript_locations=[citation.manuscript_location],
                claim_ids=citation.supports_claim_ids,
                evidence_label=citation.evidence_label,
                required=True,
                resolved=resolved,
            )
        )
    return bindings, _unique(blockers)


def _build_tables(
    inputs: _Inputs,
    bindings: list[FinalPaperArtifactBinding],
) -> tuple[list[FinalPaperTableRecord], list[str]]:
    assert inputs.execution is not None
    assert inputs.synthesis is not None
    required = {
        artifact_id
        for claim in inputs.synthesis.claim_artifact_bindings
        for artifact_id in claim.supporting_artifact_ids
    }
    by_id = {item.result_id: item for item in inputs.execution.results}
    rows: list[dict[str, Any]] = []
    blockers: list[str] = []
    token_bindings = {
        (str(item.get("artifact_id", "")), str(item.get("metric", ""))): item
        for item in inputs.revised_draft.metric_token_bindings
    }
    token_order = {
        key: index for index, key in enumerate(token_bindings)
    }
    for artifact_id in sorted(required):
        result = by_id.get(artifact_id)
        if result is None or result.status not in _METRIC_RESULT_STATUSES:
            continue
        if not result.metrics:
            continue
        for metric, value in sorted(result.metrics.items()):
            source = result.metric_sources.get(metric)
            source_path, source_error = _validated_execution_metric_source(
                root=inputs.root,
                run_id=inputs.run_id,
                execution=inputs.execution,
                source=source,
                metric=metric,
                value=value,
            )
            if source_error:
                blockers.append(
                    f"Metric {metric} for {artifact_id} lacks a validated execution output source: "
                    f"{source_error}"
                )
                continue
            assert source_path is not None
            output_metrics = _metrics_from_output(source_path)
            if output_metrics.get(metric) != value:
                blockers.append(
                    f"Metric {metric} for {artifact_id} differs from its execution output artifact."
                )
            rows.append(
                {
                    "artifact_id": artifact_id,
                    "metric": metric,
                    "display_label": str(
                        token_bindings.get((artifact_id, metric), {}).get(
                            "display_label", _paper_metric_label(metric)
                        )
                    ),
                    "value": value,
                    "metric_source": source,
                }
            )
    if not rows:
        return [], blockers
    if token_bindings:
        rows = [
            row
            for row in rows
            if (str(row["artifact_id"]), str(row["metric"])) in token_bindings
        ]
    rows = sorted(
        rows,
        key=lambda row: (
            token_order.get(
                (str(row["artifact_id"]), str(row["metric"])),
                _MAX_PAPER_TABLE_ROWS + 1,
            ),
            -_paper_metric_priority(str(row["metric"])),
            str(row["display_label"]),
        ),
    )[:_MAX_PAPER_TABLE_ROWS]
    if not rows:
        return [], blockers
    source_ids = sorted({str(row["artifact_id"]) for row in rows})
    claim_ids = [
        item.claim_id
        for item in inputs.synthesis.claim_artifact_bindings
        if any(value in item.supporting_artifact_ids for value in source_ids)
    ]
    table = FinalPaperTableRecord(
        table_id="final-paper-artifact-bound-metrics",
        source_metric_artifact_ids=source_ids,
        title="Primary quantitative results",
        columns=["display_label", "value"],
        rows=rows,
        referenced_in_sections=_result_section_ids(inputs.revised_draft),
        claim_ids_supported=claim_ids,
        content_hash=sha256_text(canonical_json(rows)),
        resolved=not blockers,
    )
    return [table], _unique(blockers)


def _build_figures(
    inputs: _Inputs,
    bindings: list[FinalPaperArtifactBinding],
    *,
    figure_prefix: str,
) -> tuple[
    list[FinalPaperFigureRecord],
    list[str],
    list[_GeneratedFigureAsset],
]:
    assert inputs.revised_draft is not None
    records: list[FinalPaperFigureRecord] = []
    blockers: list[str] = []
    generated_assets: list[_GeneratedFigureAsset] = []
    markdown_refs = _MARKDOWN_IMAGE_RE.findall(inputs.revised_draft.markdown)
    latex_refs = _LATEX_IMAGE_RE.findall(inputs.revised_draft.latex)
    reference_paths = _unique(
        [value.strip() for _, value in markdown_refs]
        + [value.strip() for value in latex_refs]
    )
    if len(reference_paths) > _MAX_PAPER_FIGURES:
        blockers.append(
            f"Manuscript references {len(reference_paths)} figures; "
            f"the paper-facing limit is {_MAX_PAPER_FIGURES}."
        )
    captions = {
        value.strip(): caption.strip() for caption, value in markdown_refs if caption.strip()
    }
    for index, value in enumerate(reference_paths[:_MAX_PAPER_FIGURES], start=1):
        path = _resolve_figure_reference(inputs.root, inputs.run_id, value)
        if path is None or not path.is_file() or path.suffix.lower() not in _FIGURE_SUFFIXES:
            blockers.append(f"Referenced figure is missing or unsupported: {value}.")
            continue
        if not _is_sandbox_generated_figure(inputs, path):
            blockers.append(
                "Referenced figure is not a declared artifact from a successful local sandbox "
                f"execution: {value}."
            )
            continue
        source_artifact = _figure_source_artifact_id(path)
        caption = captions.get(value) or _figure_caption_from_output(path)
        if not caption:
            blockers.append(f"Referenced figure lacks a persisted caption: {value}.")
            continue
        records.append(
            FinalPaperFigureRecord(
                figure_id=f"final-paper-figure-{index:03d}",
                source_artifact_id=source_artifact,
                file_path=_relative(inputs.root, path),
                caption=caption,
                referenced_in_sections=_result_section_ids(inputs.revised_draft),
                claim_ids_supported=[
                    claim_id
                    for binding in bindings
                    for claim_id in binding.claim_ids
                    if binding.artifact_id == source_artifact
                ],
                generation_backend="local_execution",
                content_hash=sha256_file(path),
                resolved=True,
            )
        )
    # Final assembly must preserve the accepted manuscript's scientific presentation. Derived
    # figures remain available to upstream authoring, but are not inserted unless the manuscript
    # explicitly selected and referenced them.
    return records[:_MAX_PAPER_FIGURES], _unique(blockers), generated_assets


def _generate_paired_difference_figure(
    inputs: _Inputs,
    bindings: list[FinalPaperArtifactBinding],
    *,
    artifact_id: str,
) -> tuple[FinalPaperFigureRecord, _GeneratedFigureAsset] | None:
    return _generate_metric_figure(
        inputs,
        bindings,
        artifact_id=artifact_id,
        finder=_find_paired_difference_series,
        renderer=_render_paired_difference_png,
        caption=(
            "Paired differences from the declared baseline across experimental conditions. "
            "Points are persisted means and error bars reproduce the persisted interval fields."
        ),
    )


def _generate_reliability_figure(
    inputs: _Inputs,
    bindings: list[FinalPaperArtifactBinding],
    *,
    artifact_id: str,
) -> tuple[FinalPaperFigureRecord, _GeneratedFigureAsset] | None:
    assert inputs.execution is not None
    for result in inputs.execution.results:
        if result.status not in _METRIC_RESULT_STATUSES:
            continue
        sources = _unique(
            source.split("#", 1)[0] for source in result.metric_sources.values() if source
        )
        for source in sources:
            path = _path_from_relative(inputs.root, source)
            if not path.is_file():
                continue
            payload = _read_json(path)
            curves = _find_reliability_curves(payload)
            if curves is None:
                continue
            content = _render_reliability_png(curves)
            record = FinalPaperFigureRecord(
                figure_id=artifact_id,
                source_artifact_id=result.result_id,
                file_path=f"runs/{inputs.run_id}/reports/{artifact_id}.png",
                caption=(
                    "Reliability curves reconstructed from the validated experiment output. "
                    "The diagonal denotes exact calibration."
                ),
                referenced_in_sections=_result_section_ids(inputs.revised_draft),
                claim_ids_supported=_unique(
                    claim_id
                    for binding in bindings
                    for claim_id in binding.claim_ids
                    if binding.artifact_id == result.result_id
                ),
                generation_backend="deterministic_presentation_renderer",
                content_hash=hashlib.sha256(content).hexdigest(),
                resolved=True,
            )
            return record, _GeneratedFigureAsset(
                artifact_id=artifact_id,
                filename_stem=artifact_id,
                content=content,
            )
    return None


def _generate_interval_summary_figure(
    inputs: _Inputs,
    bindings: list[FinalPaperArtifactBinding],
    *,
    artifact_id: str,
) -> tuple[FinalPaperFigureRecord, _GeneratedFigureAsset] | None:
    return _generate_metric_figure(
        inputs,
        bindings,
        artifact_id=artifact_id,
        finder=_find_reliability_interval_summaries,
        renderer=_render_interval_summary_png,
        caption=(
            "Reliability-deviation summaries reconstructed from the validated experiment output. "
            "Bars are persisted means and error bars reproduce the persisted interval fields."
        ),
    )


def _generate_metric_figure(
    inputs: _Inputs,
    bindings: list[FinalPaperArtifactBinding],
    *,
    artifact_id: str,
    finder: Any,
    renderer: Any,
    caption: str,
) -> tuple[FinalPaperFigureRecord, _GeneratedFigureAsset] | None:
    assert inputs.execution is not None
    for result in inputs.execution.results:
        if result.status not in _METRIC_RESULT_STATUSES:
            continue
        sources = _unique(
            source.split("#", 1)[0] for source in result.metric_sources.values() if source
        )
        for source in sources:
            path = _path_from_relative(inputs.root, source)
            if not path.is_file():
                continue
            data = finder(_read_json(path))
            if data is None:
                continue
            content = renderer(data)
            record = FinalPaperFigureRecord(
                figure_id=artifact_id,
                source_artifact_id=result.result_id,
                file_path=f"runs/{inputs.run_id}/reports/{artifact_id}.png",
                caption=caption,
                referenced_in_sections=_result_section_ids(inputs.revised_draft),
                claim_ids_supported=_unique(
                    claim_id
                    for binding in bindings
                    for claim_id in binding.claim_ids
                    if binding.artifact_id == result.result_id
                ),
                generation_backend="deterministic_presentation_renderer",
                content_hash=hashlib.sha256(content).hexdigest(),
                resolved=True,
            )
            return record, _GeneratedFigureAsset(
                artifact_id=artifact_id,
                filename_stem=artifact_id,
                content=content,
            )
    return None


def _find_paired_difference_series(
    value: Any,
) -> dict[str, list[tuple[str, float, float, float]]] | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if "paired" in str(key).casefold() and "difference" in str(key).casefold():
                parsed = _condition_interval_series(child)
                if parsed is not None:
                    return parsed
        for child in value.values():
            found = _find_paired_difference_series(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_paired_difference_series(child)
            if found is not None:
                return found
    return None


def _condition_interval_series(
    value: Any,
) -> dict[str, list[tuple[str, float, float, float]]] | None:
    if not isinstance(value, dict):
        return None
    series: dict[str, list[tuple[str, float, float, float]]] = {}
    for label, conditions in value.items():
        if not isinstance(conditions, dict):
            continue
        points: list[tuple[str, float, float, float]] = []
        for condition, payload in sorted(conditions.items(), key=lambda item: str(item[0])):
            summary = _find_interval_summary(payload)
            if summary is None:
                continue
            mean, low, high = summary
            points.append((str(condition), mean, low, high))
        if len(points) >= 2 and any(abs(point[1]) > 1e-15 for point in points):
            series[str(label)] = points
    return dict(list(sorted(series.items()))[:4]) if len(series) >= 1 else None


def _find_reliability_interval_summaries(
    value: Any,
) -> dict[str, tuple[float, float, float]] | None:
    if isinstance(value, dict):
        for key, child in value.items():
            if "reliability" in str(key).casefold():
                parsed = _named_interval_summaries(child)
                if parsed is not None:
                    return parsed
        for child in value.values():
            found = _find_reliability_interval_summaries(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_reliability_interval_summaries(child)
            if found is not None:
                return found
    return None


def _named_interval_summaries(
    value: Any,
) -> dict[str, tuple[float, float, float]] | None:
    if not isinstance(value, dict):
        return None
    summaries = {
        str(label): summary
        for label, payload in value.items()
        if (summary := _find_interval_summary(payload)) is not None
    }
    return dict(list(sorted(summaries.items()))[:6]) if len(summaries) >= 2 else None


def _find_interval_summary(value: Any) -> tuple[float, float, float] | None:
    if not isinstance(value, dict):
        return None
    mean = value.get("mean")
    interval = value.get("interval_95")
    if (
        isinstance(mean, (int, float))
        and isinstance(interval, list)
        and len(interval) == 2
        and all(isinstance(item, (int, float)) for item in interval)
        and all(math.isfinite(float(item)) for item in (mean, *interval))
    ):
        return float(mean), float(interval[0]), float(interval[1])
    matches = [
        found
        for child in value.values()
        if (found := _find_interval_summary(child)) is not None
    ]
    return matches[0] if len(matches) == 1 else None


def _find_reliability_curves(
    value: Any,
) -> dict[str, list[tuple[float, float]]] | None:
    if isinstance(value, dict):
        curves: dict[str, list[tuple[float, float]]] = {}
        for label, points in value.items():
            parsed = _reliability_points(points)
            if parsed:
                curves[str(label)] = parsed
        if len(curves) >= 2:
            return dict(list(sorted(curves.items()))[:4])
        for child in value.values():
            found = _find_reliability_curves(child)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = _find_reliability_curves(child)
            if found is not None:
                return found
    return None


def _reliability_points(value: Any) -> list[tuple[float, float]]:
    if not isinstance(value, list) or len(value) < 3:
        return []
    points: list[tuple[float, float]] = []
    for item in value:
        if not isinstance(item, dict) or "predicted" not in item:
            return []
        target = next(
            (item[key] for key in ("eta", "clean_label", "observed") if key in item),
            None,
        )
        predicted = item.get("predicted")
        if not isinstance(predicted, (int, float)) or not isinstance(target, (int, float)):
            return []
        if not math.isfinite(float(predicted)) or not math.isfinite(float(target)):
            return []
        points.append((float(predicted), float(target)))
    return sorted(points)


def _render_reliability_png(curves: dict[str, list[tuple[float, float]]]) -> bytes:
    matplotlib_cache = Path(os.environ.setdefault("MPLCONFIGDIR", "/tmp/factori-matplotlib"))
    matplotlib_cache.mkdir(parents=True, exist_ok=True)
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(6.2, 4.2), dpi=160, facecolor="white")
    canvas = FigureCanvasAgg(figure)
    axes = figure.add_subplot(1, 1, 1)
    axes.plot([0, 1], [0, 1], color="#555555", linestyle="--", linewidth=1.2, label="Ideal")
    colors = ("#0072B2", "#D55E00", "#009E73", "#CC79A7")
    for color, (label, points) in zip(colors, sorted(curves.items()), strict=False):
        axes.plot(
            [point[0] for point in points],
            [point[1] for point in points],
            color=color,
            linewidth=2.0,
            marker="o",
            markersize=3.0,
            label=label.replace("_", " ").title(),
        )
    axes.set(xlim=(0, 1), ylim=(0, 1), xlabel="Mean predicted probability", ylabel="Clean rate")
    axes.grid(color="#dddddd", linewidth=0.6)
    axes.spines[["top", "right"]].set_visible(False)
    axes.legend(frameon=False, fontsize=8, loc="upper left")
    figure.tight_layout()
    buffer = BytesIO()
    canvas.print_png(buffer, metadata={"Software": "factori"})
    return buffer.getvalue()


def _render_paired_difference_png(
    series: dict[str, list[tuple[str, float, float, float]]],
) -> bytes:
    matplotlib_cache = Path(os.environ.setdefault("MPLCONFIGDIR", "/tmp/factori-matplotlib"))
    matplotlib_cache.mkdir(parents=True, exist_ok=True)
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    figure = Figure(figsize=(6.5, 4.2), dpi=160, facecolor="white")
    canvas = FigureCanvasAgg(figure)
    axes = figure.add_subplot(1, 1, 1)
    colors = ("#0072B2", "#D55E00", "#009E73", "#CC79A7")
    width = 0.18
    for index, (label, points) in enumerate(sorted(series.items())):
        offset = (index - (len(series) - 1) / 2) * width
        x_values = [position + offset for position in range(len(points))]
        means = [point[1] for point in points]
        errors = [
            [max(0.0, point[1] - point[2]) for point in points],
            [max(0.0, point[3] - point[1]) for point in points],
        ]
        axes.errorbar(
            x_values,
            means,
            yerr=errors,
            color=colors[index % len(colors)],
            linewidth=1.5,
            marker="o",
            markersize=4.0,
            capsize=3.0,
            label=label.replace("_", " ").title(),
        )
    first_points = next(iter(series.values()))
    axes.set_xticks(range(len(first_points)), [f"Cell {point[0]}" for point in first_points])
    axes.axhline(0.0, color="#555555", linestyle="--", linewidth=1.0)
    axes.set_ylabel("Paired difference from baseline")
    axes.grid(axis="y", color="#dddddd", linewidth=0.6)
    axes.spines[["top", "right"]].set_visible(False)
    axes.legend(frameon=False, fontsize=8)
    figure.tight_layout()
    buffer = BytesIO()
    canvas.print_png(buffer, metadata={"Software": "factori"})
    return buffer.getvalue()


def _render_interval_summary_png(
    summaries: dict[str, tuple[float, float, float]],
) -> bytes:
    matplotlib_cache = Path(os.environ.setdefault("MPLCONFIGDIR", "/tmp/factori-matplotlib"))
    matplotlib_cache.mkdir(parents=True, exist_ok=True)
    from matplotlib.backends.backend_agg import FigureCanvasAgg
    from matplotlib.figure import Figure

    labels = list(sorted(summaries))
    means = [summaries[label][0] for label in labels]
    errors = [
        [max(0.0, summaries[label][0] - summaries[label][1]) for label in labels],
        [max(0.0, summaries[label][2] - summaries[label][0]) for label in labels],
    ]
    figure = Figure(figsize=(6.5, 4.2), dpi=160, facecolor="white")
    canvas = FigureCanvasAgg(figure)
    axes = figure.add_subplot(1, 1, 1)
    positions = list(range(len(labels)))
    axes.bar(
        positions,
        means,
        yerr=errors,
        capsize=4.0,
        color=("#0072B2", "#D55E00", "#009E73", "#CC79A7")[: len(labels)],
        edgecolor="white",
        linewidth=0.8,
    )
    axes.set_xticks(
        positions,
        [label.replace("_", " ").title() for label in labels],
        rotation=15,
        ha="right",
    )
    axes.set_ylabel("Reliability-curve deviation")
    axes.grid(axis="y", color="#dddddd", linewidth=0.6)
    axes.spines[["top", "right"]].set_visible(False)
    figure.tight_layout()
    buffer = BytesIO()
    canvas.print_png(buffer, metadata={"Software": "factori"})
    return buffer.getvalue()


def _paper_metric_priority(metric: str) -> int:
    value = metric.casefold()
    score = 80 if value.endswith((".mean", "_mean")) else 0
    if "paired" in value or "difference" in value or "contrast" in value:
        score += 25
    if value.endswith((".count", "_count", ".0", ".1")):
        score -= 70
    if any(
        marker in value
        for marker in ("failure", "warning", "diagnostic", "runtime", "seed")
    ):
        score -= 30
    return score


def _paper_metric_label(metric: str) -> str:
    parts = [part for part in metric.split(".") if part != "mean"]
    return ": ".join(part.replace("_", " ") for part in parts[-3:])[:120]


def _resolve_citations(inputs: _Inputs) -> tuple[list[EvidenceCitationBinding], list[str]]:
    assert inputs.synthesis is not None
    resolved: list[EvidenceCitationBinding] = []
    blockers: list[str] = []
    for binding in inputs.synthesis.evidence_citation_bindings:
        if binding.source_type == "retrieval_source":
            source = _retrieval_source_for_binding(binding, inputs.retrieval_contexts)
            if source is None:
                blockers.append(
                    f"Missing retrieved source for citation binding {binding.binding_id}."
                )
                continue
        elif binding.source_type == "execution_artifact":
            path = _artifact_path_for_id(inputs.root, inputs.run_id, binding.artifact_id)
            if path is None or not path.is_file():
                blockers.append(
                    f"Missing execution artifact for citation binding {binding.binding_id}."
                )
                continue
        resolved.append(binding)
    return resolved, _unique(blockers)


def _build_open_obligations(inputs: _Inputs) -> list[FinalPaperOpenObligation]:
    assert inputs.synthesis is not None
    values = [
        *inputs.synthesis.unresolved_obligations,
        *(
            inputs.synthesis.manuscript_plan_optional.required_repairs
            if inputs.synthesis.manuscript_plan_optional
            else []
        ),
    ]
    if inputs.execution is not None:
        values.extend(
            obligation
            for result in inputs.execution.results
            for obligation in result.unresolved_obligations
        )
        values.extend(
            "Negative control did not pass: " + result.result_id
            for result in inputs.execution.results
            if any("Negative controls did not pass" in warning for warning in result.warnings)
        )
    return [
        FinalPaperOpenObligation(
            obligation_id=f"final-paper-obligation-{index:03d}",
            obligation_type=_obligation_type(value),
            description=value,
            source_artifact_id=inputs.synthesis.report_id,
            affected_claim_ids=[item.claim_id for item in inputs.synthesis.claim_artifact_bindings],
            blocking_for_publication=True,
        )
        for index, value in enumerate(_unique(values), start=1)
    ]


def _section_records(
    draft: NucleusManuscriptDraft,
    bindings: list[ClaimArtifactBinding],
) -> list[FinalPaperSectionRecord]:
    records: list[FinalPaperSectionRecord] = []
    for index, (title, body) in enumerate(_markdown_sections(draft.markdown), start=1):
        lower = f"{title}\n{body}".lower()
        claim_ids = [
            item.claim_id
            for item in bindings
            if any(token in lower for token in _claim_tokens(item.claim_text))
        ]
        records.append(
            FinalPaperSectionRecord(
                section_id=f"final-paper-section-{index:03d}",
                title=title,
                manuscript_location=_slug(title),
                claim_ids=claim_ids,
                source_artifact_ids=[
                    artifact
                    for item in bindings
                    if item.claim_id in claim_ids
                    for artifact in item.supporting_artifact_ids
                ],
                content_hash=sha256_text(body),
            )
        )
    return records


def _appendix_records(
    inputs: _Inputs,
    obligations: list[FinalPaperOpenObligation],
    config: FinalPaperAssemblyConfig,
) -> list[FinalPaperAppendixRecord]:
    assert inputs.synthesis is not None
    assert inputs.adjudication is not None
    nucleus = inputs.adjudication.paper_nucleus_selection_optional
    records: list[FinalPaperAppendixRecord] = []
    if nucleus and nucleus.appendix_package_ids:
        records.append(
            FinalPaperAppendixRecord(
                appendix_id="appendix-secondary-packages",
                title="Secondary and Exploratory Packages",
                role="appendix_package_context",
                source_package_ids=nucleus.appendix_package_ids,
            )
        )
    if config.include_negative_results_appendix and nucleus and nucleus.negative_package_ids:
        records.append(
            FinalPaperAppendixRecord(
                appendix_id="appendix-negative-results",
                title="Negative Results and Failed Branches",
                role="negative_result_context",
                source_package_ids=nucleus.negative_package_ids,
                contains_negative_results=True,
                contains_failed_branches=True,
            )
        )
    if obligations:
        records.append(
            FinalPaperAppendixRecord(
                appendix_id="appendix-open-obligations",
                title="Open Obligations and Scope Boundaries",
                role="open_obligation_context",
                source_artifact_ids=[inputs.synthesis.report_id],
                contains_open_obligations=True,
            )
        )
    if config.include_provenance_appendix:
        records.append(
            FinalPaperAppendixRecord(
                appendix_id="appendix-provenance",
                title="Provenance and Reproducibility Context",
                role="provenance_context",
                source_artifact_ids=[inputs.synthesis.report_id],
                contains_provenance=True,
            )
        )
    return records


def _assemble_markdown(
    *,
    draft: NucleusManuscriptDraft,
    tables: list[FinalPaperTableRecord],
    figures: list[FinalPaperFigureRecord],
    appendices: list[FinalPaperAppendixRecord],
    obligations: list[FinalPaperOpenObligation],
    citation_bindings: list[EvidenceCitationBinding],
    retrieval_contexts: list[RetrievalContext],
    config: FinalPaperAssemblyConfig,
) -> str:
    result_lines: list[str] = []
    for table in tables:
        if _table_values_present(draft.markdown, table):
            continue
        result_lines.extend([f"### {table.title}", "", "| Measure | Value |", "|---|---:|"])
        result_lines.extend(
            "| {display_label} | {value} |".format(**row)
            for row in table.rows
        )
        result_lines.append("")
    if figures:
        result_lines.extend(["### Figures", ""])
        result_lines.extend(
            f"![{item.caption}]({item.file_path})" for item in figures
        )
    manuscript = _insert_markdown_result_material(
        draft.markdown.rstrip(),
        "\n".join(result_lines).rstrip(),
    )
    lines = [manuscript]
    if appendices:
        lines.extend(["", "## Assembly Appendices", ""])
        for appendix in appendices:
            lines.append(f"### {appendix.title}")
            if appendix.contains_open_obligations:
                lines.extend(f"- {item.description}" for item in obligations)
            elif appendix.source_package_ids:
                lines.extend(f"- Package context: `{item}`" for item in appendix.source_package_ids)
            elif appendix.contains_provenance:
                lines.append(
                    "- Artifact bindings, execution records, and reproduction context are listed "
                    "in the paper manifest."
                )
            lines.append("")
    references = _reference_lines(citation_bindings, retrieval_contexts)
    if references and not re.search(r"(?mi)^#{1,6}\s+references\b", draft.markdown):
        lines.extend(["## References", "", *references])
    return "\n".join(lines).rstrip() + "\n"


def _table_values_present(source: str, table: FinalPaperTableRecord) -> bool:
    """Return whether the manuscript already presents every validated table value."""
    return bool(table.rows) and all(str(row["value"]) in source for row in table.rows)


def _assemble_latex(
    *,
    draft: NucleusManuscriptDraft,
    tables: list[FinalPaperTableRecord],
    figures: list[FinalPaperFigureRecord],
    appendices: list[FinalPaperAppendixRecord],
    obligations: list[FinalPaperOpenObligation],
    citation_bindings: list[EvidenceCitationBinding],
    retrieval_contexts: list[RetrievalContext],
) -> str:
    base_latex = draft.latex.rstrip()
    if r"\documentclass" in base_latex:
        required_packages = (
            ("longtable", r"\usepackage{longtable}"),
            ("graphicx", r"\usepackage{graphicx}"),
            ("float", r"\usepackage{float}"),
            ("xurl", r"\usepackage{xurl}"),
        )
        missing_packages = [
            declaration
            for package, declaration in required_packages
            if rf"\usepackage{{{package}}}" not in base_latex
        ]
        if missing_packages:
            base_latex = base_latex.replace(
                r"\begin{document}",
                "\n".join([*missing_packages, r"\begin{document}"]),
                1,
            )
    document_suffix = ""
    if r"\end{document}" in base_latex:
        base_latex, _, suffix = base_latex.rpartition(r"\end{document}")
        document_suffix = r"\end{document}" + suffix
        base_latex = base_latex.rstrip()
    result_lines: list[str] = []
    for table in tables:
        if _table_values_present(draft.latex, table):
            continue
        result_lines.extend(
            [
                "",
                rf"\subsection*{{{_latex_escape(table.title)}}}",
                r"\small",
                r"\begin{longtable}{p{0.75\textwidth}r}",
                r"Measure & Value \\",
                r"\hline",
                *[
                    " & ".join(
                        [
                            _latex_escape(str(row["display_label"])),
                            _latex_escape(str(row["value"])),
                        ]
                    )
                    + " \\\\"
                    for row in table.rows
                ],
                r"\end{longtable}",
                r"\normalsize",
            ]
        )
    for figure in figures:
        result_lines.extend(
            [
                r"\begin{figure}[H]",
                r"\centering",
                rf"\includegraphics[width=0.9\linewidth]{{{_latex_escape(figure.file_path)}}}",
                rf"\caption{{{_latex_escape(figure.caption)}}}",
                r"\end{figure}",
            ]
        )
    base_latex = _insert_latex_result_material(
        base_latex,
        "\n".join(result_lines).strip(),
    )
    lines = [base_latex]
    reference_lines = _latex_reference_lines(citation_bindings, retrieval_contexts)
    if reference_lines and r"\begin{thebibliography}" not in base_latex:
        lines.extend(
            [
                "",
                r"\section*{References}",
                r"\begin{thebibliography}{99}",
                *reference_lines,
                r"\end{thebibliography}",
            ]
        )
    if appendices:
        lines.extend(["", r"\appendix", r"\section*{Assembly Appendices}"])
        for appendix in appendices:
            lines.append(rf"\subsection*{{{_latex_escape(appendix.title)}}}")
            if appendix.contains_open_obligations:
                lines.append(r"\begin{itemize}")
                lines.extend(rf"\item {_latex_escape(item.description)}" for item in obligations)
                lines.append(r"\end{itemize}")
    if document_suffix:
        lines.extend(["", document_suffix])
    return "\n".join(lines).rstrip() + "\n"


def _insert_markdown_result_material(source: str, material: str) -> str:
    if not material:
        return source
    result_heading = re.search(
        r"(?mi)^(?P<marks>#{1,6})[ \t]+[^\n]*\bresults?\b[^\n]*$",
        source,
    )
    if result_heading is not None:
        level = len(result_heading.group("marks"))
        next_heading = re.search(
            rf"(?m)^#{{1,{level}}}[ \t]+",
            source[result_heading.end() :],
        )
        insertion = (
            result_heading.end() + next_heading.start()
            if next_heading is not None
            else len(source)
        )
        return (
            source[:insertion].rstrip()
            + "\n\n"
            + material
            + "\n\n"
            + source[insertion:].lstrip()
        ).rstrip()
    fallback = re.search(
        r"(?mi)^#{1,6}[ \t]+[^\n]*\b(?:limitations?|discussion|conclusion|appendix|references)\b",
        source,
    )
    block = "## Results Summary\n\n" + material
    if fallback is None:
        return source.rstrip() + "\n\n" + block
    return (
        source[: fallback.start()].rstrip()
        + "\n\n"
        + block
        + "\n\n"
        + source[fallback.start() :].lstrip()
    ).rstrip()


def _insert_latex_result_material(source: str, material: str) -> str:
    if not material:
        return source
    result_heading = re.search(
        r"(?mi)^\\section\*?\{[^}\n]*\bresults?\b[^}\n]*\}",
        source,
    )
    if result_heading is not None:
        next_heading = re.search(
            r"(?m)^\\section\*?\{",
            source[result_heading.end() :],
        )
        insertion = (
            result_heading.end() + next_heading.start()
            if next_heading is not None
            else len(source)
        )
        return (
            source[:insertion].rstrip()
            + "\n\n"
            + material
            + "\n\n"
            + source[insertion:].lstrip()
        ).rstrip()
    fallback = re.search(
        r"(?mi)^\\section\*?\{[^}\n]*\b(?:limitations?|discussion|conclusion|appendix|references)\b",
        source,
    )
    block = r"\section*{Results Summary}" + "\n" + material
    if fallback is None:
        return source.rstrip() + "\n\n" + block
    return (
        source[: fallback.start()].rstrip()
        + "\n\n"
        + block
        + "\n\n"
        + source[fallback.start() :].lstrip()
    ).rstrip()


def _standalone_final_latex(source: str) -> str:
    """Turn an M106 fragment into a deterministic, compile-ready paper document."""
    normalized = source.replace("\r\n", "\n").replace("\r", "\n").strip()
    normalized = _strip_reconstructed_result_tables(normalized)
    normalized = _strip_presentation_audit_sections(normalized)
    if r"\documentclass" in normalized:
        normalized = _ensure_latex_preamble_line(normalized, r"\usepackage[T1]{fontenc}")
        normalized = _ensure_latex_preamble_line(normalized, r"\usepackage[utf8]{inputenc}")
        normalized = _ensure_latex_preamble_line(normalized, r"\usepackage{lmodern}")
        normalized = _ensure_latex_preamble_line(normalized, r"\usepackage[margin=1in]{geometry}")
        normalized = _ensure_latex_package(normalized, "adjustbox")
        normalized = _ensure_latex_package(normalized, "microtype")
        normalized = _fit_simple_longtables(normalized)
        normalized = _fit_table_tabulars(normalized)
        normalized = _format_document_tabular_numbers(normalized)
        normalized = _format_document_longtable_numbers(normalized)
        normalized = re.sub(
            r"(?<!\\protect)\\url\{",
            r"\\protect\\url{",
            normalized,
        )
        normalized = _ensure_latex_preamble_line(
            normalized, r"\setlength{\emergencystretch}{4em}"
        )
        normalized = _ensure_latex_preamble_line(
            normalized, r"\setlength{\parindent}{1.25em}"
        )
        normalized = _ensure_latex_preamble_line(normalized, r"\setlength{\parskip}{0pt}")
        if not re.search(r"(?m)^\s*\\author\{", normalized):
            normalized = _ensure_latex_preamble_line(normalized, r"\author{}")
        if not re.search(r"(?m)^\s*\\date\{", normalized):
            normalized = _ensure_latex_preamble_line(normalized, r"\date{}")
        return normalized + "\n"
    normalized = re.sub(r"(?<!\\)#", r"\\#", normalized)
    normalized = re.sub(
        r"\\texttt\{([^{}]*)\}",
        lambda match: (
            _latex_path(_latex_unescape_text(match.group(1)))
            if "/" in match.group(1) or len(match.group(1)) > 40
            else match.group(0)
        ),
        normalized,
    )
    title, normalized = _paperize_final_fragment(normalized)
    return "\n".join(
        [
            r"\documentclass[11pt]{article}",
            r"\usepackage[T1]{fontenc}",
            r"\usepackage[utf8]{inputenc}",
            r"\usepackage{lmodern}",
            r"\usepackage{amsmath,amssymb}",
            r"\usepackage{graphicx}",
            r"\usepackage{float}",
            r"\usepackage{array,longtable}",
            r"\usepackage{adjustbox}",
            r"\usepackage{booktabs}",
            r"\usepackage{microtype}",
            r"\usepackage{xurl}",
            r"\usepackage[hidelinks]{hyperref}",
            r"\usepackage[margin=1in]{geometry}",
            r"\setlength{\parindent}{1.25em}",
            r"\setlength{\parskip}{0pt}",
            rf"\title{{{title}}}",
            r"\author{}",
            r"\date{}",
            r"\setlength{\emergencystretch}{3em}",
            r"\begin{document}",
            r"\maketitle",
            r"\vspace{-3em}",
            normalized,
            r"\end{document}",
            "",
        ]
    )


def _resolve_latex_graphics(source: str, root: Path) -> str:
    pattern = re.compile(
        r"(?P<prefix>\\includegraphics(?:\[[^\]]*\])?\{)"
        r"(?P<path>runs/[^}]+)"
        r"(?P<suffix>\})"
    )
    def resolve(match: re.Match[str]) -> str:
        relative = _latex_unescape_text(match.group("path"))
        absolute = (root / relative).resolve().as_posix()
        return (
            match.group("prefix")
            + r"\detokenize{"
            + absolute
            + "}"
            + match.group("suffix")
        )

    return pattern.sub(resolve, source)


def _ensure_latex_package(source: str, package: str) -> str:
    if re.search(rf"\\usepackage(?:\[[^]]*\])?\{{[^}}]*\b{re.escape(package)}\b[^}}]*\}}", source):
        return source
    return source.replace(
        r"\begin{document}",
        rf"\usepackage{{{package}}}" + "\n" + r"\begin{document}",
        1,
    )


def _ensure_latex_preamble_line(source: str, line: str) -> str:
    if line in source:
        return source
    return source.replace(r"\begin{document}", line + "\n" + r"\begin{document}", 1)


def _strip_reconstructed_result_tables(source: str) -> str:
    """Keep exact metric dumps in the bundle, not in the presentation PDF."""
    marker = r"\section*{Reconstructed Result Tables}"
    start = source.find(marker)
    if start < 0:
        return source
    end_candidates = [
        position
        for token in (
            r"\appendix",
            r"\section*{Assembly Appendices}",
            r"\end{document}",
        )
        if (position := source.find(token, start + len(marker))) >= 0
    ]
    end = min(end_candidates, default=len(source))
    return source[:start].rstrip() + "\n\n" + source[end:].lstrip()


def _strip_presentation_audit_sections(source: str) -> str:
    """Keep machine-readable audit material in the bundle, not the paper PDF."""
    claim_map = re.compile(
        r"\n?\\subsection\*\{Machine-readable claim map\}.*?"
        r"(?=\n\\appendix|\n\\section\*?\{|\n\\end\{document\})",
        re.DOTALL,
    )
    normalized = claim_map.sub("", source)
    assembly_appendix = re.compile(
        r"\n?\\appendix\s*\n\\section\*\{Assembly Appendices\}.*?"
        r"(?=\n\\end\{document\})",
        re.DOTALL,
    )
    normalized = assembly_appendix.sub("", normalized)
    normalized = re.sub(
        r"Reader-facing citations use this short identifier;[^.]*"
        r"machine-readable provenance note below\.",
        "Full provenance details are retained in the accompanying machine-readable bundle.",
        normalized,
    )
    normalized = normalized.replace(
        r"\section*{Provenance note and references}",
        r"\section*{References}",
    )
    normalized = re.sub(
        r"\\section\*\{References\}\s*(?=\\begin\{thebibliography\})",
        "",
        normalized,
    )
    return normalized


def _format_document_tabular_numbers(source: str) -> str:
    pattern = re.compile(
        r"(?P<open>\\begin\{tabular\}\{[^\n]*\}\s*)"
        r"(?P<body>.*?)"
        r"(?P<close>\\end\{tabular\})",
        re.DOTALL,
    )
    return pattern.sub(
        lambda match: (
            match.group("open")
            + _format_table_numbers(match.group("body"))
            + match.group("close")
        ),
        source,
    )


def _format_document_longtable_numbers(source: str) -> str:
    pattern = re.compile(
        r"(?P<open>\\begin\{longtable\}\{[^\n]*\}\s*)"
        r"(?P<body>.*?)"
        r"(?P<close>\\end\{longtable\})",
        re.DOTALL,
    )
    return pattern.sub(
        lambda match: (
            match.group("open")
            + _format_table_numbers(match.group("body"))
            + match.group("close")
        ),
        source,
    )


def _fit_simple_longtables(source: str) -> str:
    pattern = re.compile(
        r"\\begin\{longtable\}\{(?P<spec>[^{}\n]+)\}\s*"
        r"(?P<body>.*?)\\end\{longtable\}",
        re.DOTALL,
    )

    def replace(match: re.Match[str]) -> str:
        spec = match.group("spec")
        body = match.group("body").strip()
        if any(token in spec for token in ("p{", "m{", "b{", "X")):
            return match.group(0)
        if body.count(r"\\") > 30:
            return match.group(0)
        lines = body.splitlines()
        caption = ""
        if lines and lines[0].lstrip().startswith(r"\caption{"):
            caption = lines.pop(0).rstrip()
            if caption.endswith(r"\\"):
                caption = caption[:-2].rstrip()
        tabular = "\n".join(
            [
                r"\begin{adjustbox}{max width=\textwidth}",
                rf"\begin{{tabular}}{{{spec}}}",
                *lines,
                r"\end{tabular}",
                r"\end{adjustbox}",
            ]
        )
        if caption:
            return "\n".join(
                [r"\begin{table}[H]", r"\centering", caption, tabular, r"\end{table}"]
            )
        return "\n".join([r"\begin{center}", tabular, r"\end{center}"])

    return pattern.sub(replace, source)


def _fit_table_tabulars(source: str) -> str:
    """Constrain ordinary manuscript tables without changing their scientific content."""
    pattern = re.compile(
        r"(?P<open>\\begin\{table\}(?:\[[^]]*\])?)"
        r"(?P<body>.*?)"
        r"(?P<close>\\end\{table\})",
        re.DOTALL,
    )

    def replace(match: re.Match[str]) -> str:
        body = match.group("body")
        if r"\begin{adjustbox}" in body or r"\begin{tabular}" not in body:
            return match.group(0)
        body = body.replace(
            r"\begin{tabular}",
            r"\begin{adjustbox}{max width=\linewidth}" + "\n" + r"\begin{tabular}",
            1,
        )
        tabular_end = body.rfind(r"\end{tabular}")
        if tabular_end < 0:
            return match.group(0)
        tabular_end += len(r"\end{tabular}")
        body = body[:tabular_end] + "\n" + r"\end{adjustbox}" + body[tabular_end:]
        return match.group("open") + body + match.group("close")

    return pattern.sub(replace, source)


def _paperize_final_fragment(source: str) -> tuple[str, str]:
    """Separate the human-facing paper from bundle-only audit material."""
    title_match = re.match(r"\\section\*\{([^{}]+)\}\s*", source)
    title = "Generated Research Paper"
    if title_match is not None:
        title = re.sub(
            r":\s*an artifact-summary report\s*$",
            "",
            title_match.group(1),
            flags=re.IGNORECASE,
        )
        source = source[title_match.end() :]

    source = re.split(r"\\section\*\{Reconstructed Result Tables\}", source, maxsplit=1)[0]
    source = re.split(
        r"\\subsubsection\*\{Unresolved obligations retained\}", source, maxsplit=1
    )[0]
    source = re.sub(
        r"\\subsection\*\{Abstract\}\s*(.*?)(?=\\subsection\{)",
        lambda match: "\\begin{abstract}\n"
        + match.group(1).strip()
        + "\n\\end{abstract}\n\n",
        source,
        count=1,
        flags=re.DOTALL,
    )
    source = source.replace(r"\subsubsection*{", r"\FACTORIESUBSECTIONSTAR{")
    source = source.replace(r"\subsection{", r"\section{")
    source = source.replace(r"\FACTORIESUBSECTIONSTAR{", r"\subsection*{")
    source = source.replace("This artifact-summary report describes", "This study describes")
    source = source.replace("This is an artifact-summary report of", "This study reports")
    source = source.replace("This artifact-summary report records", "This study records")
    source = re.sub(
        r"\s*\[Artifact citation.*?\]\.?",
        "",
        source,
        flags=re.DOTALL,
    )
    source = re.sub(
        r"The table is supported by .*?under formal binding .*?\.\s*",
        "",
        source,
        count=1,
    )
    source = _format_presentation_tables(source)
    source = (
        source.rstrip()
        + "\n\n"
        + r"\medskip\noindent\textit{Artifacts and exact metrics are available in the "
        r"accompanying final-paper bundle.}"
    )
    return title, source


def _format_presentation_tables(source: str) -> str:
    pattern = re.compile(
        r"\\begin\{center\}\s*\\begin\{tabular\}\{([^}]*)\}"
        r"(.*?)\\end\{tabular\}\s*\\end\{center\}",
        flags=re.DOTALL,
    )

    def replace_table(match: re.Match[str]) -> str:
        columns = match.group(1).replace("|", "")
        body = _format_table_numbers(match.group(2).strip())
        body = body.replace(r"\\ \hline", "\\\\\n\\midrule")
        if not body.rstrip().endswith(r"\\"):
            body = body.rstrip() + r"\\"
        return "\n".join(
            [
                r"\begin{table}[H]",
                r"\centering",
                r"\small",
                r"\begin{adjustbox}{max width=\linewidth}",
                rf"\begin{{tabular}}{{{columns}}}",
                r"\toprule",
                body,
                r"\bottomrule",
                r"\end{tabular}",
                r"\end{adjustbox}",
                r"\end{table}",
            ]
        )

    return pattern.sub(replace_table, source)


def _format_table_numbers(body: str) -> str:
    number_pattern = re.compile(
        r"(?<![A-Za-z0-9_.])[-+]?(?:\d+\.\d+(?:[eE][-+]?\d+)?|\d+[eE][-+]?\d+)"
        r"(?![A-Za-z0-9_.])"
    )

    def compact(match: re.Match[str]) -> str:
        value = float(match.group(0))
        return "0" if value == 0 else f"{value:.4g}"

    return number_pattern.sub(compact, body)


def _legacy_metric_longtable(match: re.Match[str]) -> str:
    """Make the prior M106 four-column metric table printable across pages."""
    rows: list[str] = []
    for line in match.group(1).splitlines():
        stripped = line.strip()
        if stripped.startswith("Artifact &"):
            rows.append(r"Metric & Value & Provenance \\")
            continue
        if " & " not in stripped:
            rows.append(line)
            continue
        suffix = r" \\" if stripped.endswith(r"\\") else ""
        content = stripped[: -len(r"\\")].rstrip() if suffix else stripped
        fields = content.split(" & ")
        if len(fields) != 4:
            rows.append(line)
            continue
        artifact, metric, value, source = fields
        rows.append(
            " & ".join(
                [
                    _latex_path(_latex_unescape_text(metric)),
                    value,
                    _metric_provenance_cell(
                        _latex_unescape_text(artifact),
                        _latex_unescape_text(source),
                    ),
                ]
            )
            + suffix
        )
    body = "\n".join(rows)
    return (
        r"\scriptsize"
        "\n"
        r"\begin{longtable}{>{\raggedright\arraybackslash}p{0.37\textwidth}"
        r">{\raggedleft\arraybackslash}p{0.14\textwidth}"
        r">{\raggedright\arraybackslash}p{0.41\textwidth}}"
        + body
        + "\n"
        + r"\end{longtable}"
        + "\n"
        + r"\normalsize"
    )


def _metric_provenance_cell(artifact_id: str, metric_source: str) -> str:
    return " ".join(
        [
            r"\textbf{Artifact:}",
            _latex_path(artifact_id),
            r"\newline\textbf{Source:}",
            _latex_path(metric_source),
        ]
    )


def _build_bibliography(
    citations: list[EvidenceCitationBinding], contexts: list[RetrievalContext]
) -> str | None:
    values: list[str] = []
    seen: set[str] = set()
    for binding in citations:
        if binding.source_type != "retrieval_source":
            continue
        source = _retrieval_source_for_binding(binding, contexts)
        if source is None:
            continue
        key = _citation_key(source.source_id)
        if key in seen:
            continue
        seen.add(key)
        fields = [("title", source.title)]
        if source.authors:
            fields.append(("author", " and ".join(source.authors)))
        if source.year is not None:
            fields.append(("year", str(source.year)))
        if source.venue:
            fields.append(("howpublished", source.venue))
        if source.doi:
            fields.append(("doi", source.doi))
        values.append(
            "@misc{"
            + key
            + ",\n"
            + ",\n".join(f"  {name} = {{{_bib_escape(value)}}}" for name, value in fields)
            + "\n}\n"
        )
    return "\n".join(values) if values else None


def _reference_lines(
    citations: list[EvidenceCitationBinding], contexts: list[RetrievalContext]
) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for binding in citations:
        if binding.source_type != "retrieval_source":
            continue
        source = _retrieval_source_for_binding(binding, contexts)
        if source is None or source.source_id in seen:
            continue
        seen.add(source.source_id)
        authors = ", ".join(source.authors) if source.authors else "Unknown author"
        year = str(source.year) if source.year is not None else "n.d."
        identifier = source.doi or source.source_id
        key = _citation_key(source.source_id)
        lines.append(
            f'- <a id="{key}"></a>{authors} ({year}). {source.title}. `{identifier}`'
        )
    return lines


def _latex_reference_lines(
    citations: list[EvidenceCitationBinding], contexts: list[RetrievalContext]
) -> list[str]:
    lines: list[str] = []
    seen: set[str] = set()
    for binding in citations:
        if binding.source_type != "retrieval_source":
            continue
        source = _retrieval_source_for_binding(binding, contexts)
        if source is None or source.source_id in seen:
            continue
        seen.add(source.source_id)
        authors = ", ".join(source.authors) if source.authors else "Unknown author"
        details = [authors]
        if source.year is not None:
            details.append(str(source.year))
        details.append(rf"\emph{{{_latex_escape(source.title)}}}")
        if source.venue:
            details.append(_latex_escape(source.venue))
        identifier = source.doi or source.source_id
        details.append(rf"\url{{{_latex_escape(identifier)}}}")
        lines.append(
            rf"\bibitem{{{_citation_key(source.source_id)}}} " + ". ".join(details) + "."
        )
    return lines


def _build_provenance_manifest(
    *,
    inputs: _Inputs,
    manifest: FinalPaperManifest,
    backend_records: list[StageBackendRecord],
    config: FinalPaperAssemblyConfig,
) -> dict[str, Any]:
    ledger_tip = validate_ledger_tip(inputs.run_id, root=inputs.root)
    return {
        "run_id": inputs.run_id,
        "manifest_id": manifest.manifest_id,
        "source_manuscript_revision_id": manifest.source_manuscript_revision_id,
        "protocol_version": PROTOCOL_VERSION,
        "backend_records": [item.model_dump(mode="json") for item in backend_records],
        "claim_artifact_map_path": manifest.claim_artifact_map_path,
        "evidence_citation_bindings_path": manifest.evidence_citation_bindings_path,
        "execution_metric_sources": [
            source
            for item in manifest.table_records
            for row in item.rows
            for source in [str(row["metric_source"])]
        ],
        "random_seeds": [
            item.random_seed
            for item in (inputs.execution.code_artifacts if inputs.execution else [])
        ],
        "sandbox_execution_records": (
            [item.model_dump(mode="json") for item in inputs.execution.sandbox_executions]
            if inputs.execution
            else []
        ),
        "ledger_tip_validation": ledger_tip.model_dump(mode="json"),
        "reproduction_instructions": _reproduction_instructions(inputs.run_id, manifest),
        "external_llm_or_retrieval_may_be_required": True,
        "full_scientific_reproducibility_guaranteed": False,
        "include_raw_llm_provenance": config.include_raw_llm_provenance,
        "publication_ready": False,
        "creates_scientific_validation": False,
        "is_verification_evidence": False,
    }


def _persist_assembly_report(
    *,
    report: FinalPaperAssemblyReport,
    store: ArtifactStore,
    ledger: ResearchLedger,
    manifest: FinalPaperManifest | None = None,
    final_markdown: str | None = None,
    final_latex: str | None = None,
    bibliography: str | None = None,
    claim_bindings: list[ClaimArtifactBinding] | None = None,
    citation_bindings: list[EvidenceCitationBinding] | None = None,
    provenance: dict[str, Any] | None = None,
    tables: list[FinalPaperTableRecord] | None = None,
    figure_assets: list[_GeneratedFigureAsset] | None = None,
) -> FinalPaperResult:
    metadata = _metadata("final_paper_assembly")
    specs: list[ArtifactWriteSpec] = [
        ArtifactWriteSpec(report.report_id, ArtifactType.REPORT, report, "json", metadata),
        ArtifactWriteSpec(
            f"{report.report_id}-markdown",
            ArtifactType.REPORT,
            render_final_paper_assembly_markdown(report),
            "markdown",
            metadata,
            filename_stem=report.report_id,
        ),
    ]
    if manifest is not None and final_markdown is not None and final_latex is not None:
        final_id = Path(manifest.main_markdown_path).stem
        specs.extend(
            [
                ArtifactWriteSpec(
                    final_id, ArtifactType.REPORT, final_markdown, "markdown", metadata
                ),
                ArtifactWriteSpec(
                    f"{final_id}-latex",
                    ArtifactType.LATEX,
                    final_latex,
                    "latex",
                    metadata,
                    extension="tex",
                    filename_stem=final_id,
                ),
                ArtifactWriteSpec(
                    manifest.manifest_id, ArtifactType.REPORT, manifest, "json", metadata
                ),
                ArtifactWriteSpec(
                    Path(manifest.claim_artifact_map_path).stem,
                    ArtifactType.REPORT,
                    [item.model_dump(mode="json") for item in claim_bindings or []],
                    "json",
                    metadata,
                ),
                ArtifactWriteSpec(
                    Path(manifest.evidence_citation_bindings_path).stem,
                    ArtifactType.REPORT,
                    [item.model_dump(mode="json") for item in citation_bindings or []],
                    "json",
                    metadata,
                ),
                ArtifactWriteSpec(
                    Path(manifest.provenance_manifest_path).stem,
                    ArtifactType.REPORT,
                    provenance or {},
                    "json",
                    metadata,
                ),
            ]
        )
        if bibliography:
            specs.append(
                ArtifactWriteSpec(
                    f"{final_id}-references",
                    ArtifactType.REPORT,
                    bibliography,
                    "bib",
                    metadata,
                    filename_stem=f"{final_id}-references",
                )
            )
        specs.extend(
            ArtifactWriteSpec(
                f"{final_id}-table-{index:03d}",
                ArtifactType.REPORT,
                table,
                "json",
                metadata,
            )
            for index, table in enumerate(tables or [], start=1)
        )
        specs.extend(
            ArtifactWriteSpec(
                asset.artifact_id,
                ArtifactType.REPORT,
                asset.content,
                "binary",
                _metadata("final_paper_figure"),
                extension="png",
                format_label="png",
                filename_stem=asset.filename_stem,
            )
            for asset in figure_assets or []
        )
    persistence = persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=(
            ControllerActionType.FINAL_PAPER_ASSEMBLED
            if report.operation == "assembly"
            else ControllerActionType.FINAL_PAPER_BUNDLE_BUILT
        ),
        commit_payload={
            "run_id": report.run_id,
            "report_id": report.report_id,
            "operation": report.operation,
            "assembly_status": report.assembly_status,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {item.id: item for item in persistence.artifacts}
    return FinalPaperResult(
        run_id=report.run_id,
        report=report,
        manifest_optional=manifest,
        persistence=persistence,
        report_artifact=by_id[report.report_id],
        markdown_artifact=by_id[f"{report.report_id}-markdown"],
    )


def _persist_verification_report(
    *,
    report: FinalPaperVerificationReport,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> FinalPaperResult:
    metadata = _metadata("final_paper_verification")
    persistence = persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(report.report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report.report_id}-markdown",
                ArtifactType.REPORT,
                render_final_paper_verification_markdown(report),
                "markdown",
                metadata,
                filename_stem=report.report_id,
            ),
        ],
        action_type=ControllerActionType.FINAL_PAPER_VERIFIED,
        commit_payload={
            "run_id": report.run_id,
            "report_id": report.report_id,
            "verification_status": report.verification_status,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {item.id: item for item in persistence.artifacts}
    return FinalPaperResult(
        run_id=report.run_id,
        report=report,
        manifest_optional=None,
        persistence=persistence,
        report_artifact=by_id[report.report_id],
        markdown_artifact=by_id[f"{report.report_id}-markdown"],
    )


def _persist_render_report(
    *,
    report: FinalPaperRenderReport,
    standalone_latex: str | None,
    pdf_bytes: bytes | None,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> FinalPaperResult:
    metadata = _metadata("final_paper_render")
    specs = [
        ArtifactWriteSpec(report.report_id, ArtifactType.REPORT, report, "json", metadata),
        ArtifactWriteSpec(
            f"{report.report_id}-markdown",
            ArtifactType.REPORT,
            render_final_paper_render_markdown(report),
            "markdown",
            metadata,
            filename_stem=report.report_id,
        ),
    ]
    render_number = int(report.report_id.rsplit("-", 1)[1])
    final_id = f"final-paper-render-{render_number:04d}"
    if standalone_latex is not None:
        specs.append(
            ArtifactWriteSpec(
                f"{final_id}-latex",
                ArtifactType.LATEX,
                standalone_latex,
                "latex",
                metadata,
                filename_stem=final_id,
            )
        )
    if pdf_bytes is not None:
        specs.append(
            ArtifactWriteSpec(
                f"{final_id}-pdf",
                ArtifactType.LATEX,
                pdf_bytes,
                "binary",
                metadata,
                extension="pdf",
                format_label="pdf",
                filename_stem=final_id,
            )
        )
    persistence = persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=ControllerActionType.FINAL_PAPER_RENDERED,
        commit_payload={
            "run_id": report.run_id,
            "report_id": report.report_id,
            "render_status": report.render_status,
            "rendered_pdf_path": report.rendered_pdf_path_optional,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {item.id: item for item in persistence.artifacts}
    return FinalPaperResult(
        run_id=report.run_id,
        report=report,
        manifest_optional=None,
        persistence=persistence,
        report_artifact=by_id[report.report_id],
        markdown_artifact=by_id[f"{report.report_id}-markdown"],
    )


def _deferred_assembly_report(
    *,
    run_id: str,
    report_id: str,
    inputs: _Inputs,
    blockers: list[str],
    bindings: list[FinalPaperArtifactBinding] | None = None,
    obligations: list[FinalPaperOpenObligation] | None = None,
    backend_records: list[StageBackendRecord] | None = None,
) -> FinalPaperAssemblyReport:
    return FinalPaperAssemblyReport(
        run_id=run_id,
        report_id=report_id,
        assembly_status="deferred",
        final_paper_status="deferred",
        source_manuscript_synthesis_report_path_optional=(
            _relative(inputs.root, inputs.synthesis_path) if inputs.synthesis_path else None
        ),
        source_manuscript_revision_id_optional=(
            inputs.revised_draft.draft_id if inputs.revised_draft else None
        ),
        paper_nucleus_selection_id_optional=(
            inputs.synthesis.paper_nucleus_selection_id_optional if inputs.synthesis else None
        ),
        claim_artifact_binding_count=(
            len(inputs.synthesis.claim_artifact_bindings) if inputs.synthesis else 0
        ),
        resolved_claim_artifact_binding_count=(
            _resolved_claim_count(inputs.synthesis.claim_artifact_bindings, bindings or [])
            if inputs.synthesis
            else 0
        ),
        evidence_citation_binding_count=(
            len(inputs.synthesis.evidence_citation_bindings) if inputs.synthesis else 0
        ),
        resolved_evidence_citation_binding_count=(
            len([item for item in bindings or [] if item.resolved])
        ),
        open_obligations=obligations or [],
        blocking_findings=_unique(blockers),
        backend_records=backend_records or [],
    )


def _deferred_verification_report(
    *,
    run_id: str,
    report_id: str,
    source_assembly_path: str | None,
    reason: str,
    backend_records: list[StageBackendRecord],
) -> FinalPaperVerificationReport:
    return FinalPaperVerificationReport(
        run_id=run_id,
        report_id=report_id,
        source_assembly_report_path=source_assembly_path or "not_available",
        verification_status="deferred",
        final_paper_status="deferred",
        checks_run=0,
        checks_passed=0,
        checks_failed=0,
        checks_warned=0,
        claim_artifact_binding_count=0,
        resolved_claim_artifact_binding_count=0,
        evidence_citation_binding_count=0,
        resolved_evidence_citation_binding_count=0,
        figure_count=0,
        table_count=0,
        hash_mismatch_count=0,
        missing_required_artifact_count=0,
        findings=[_finding(1, "missing_artifact", reason, blocking=True)],
        backend_records=backend_records,
    )


def _verify_required_paths(
    root: Path, manifest: FinalPaperManifest, findings: list[FinalPaperVerificationFinding]
) -> None:
    values = [
        manifest.main_markdown_path,
        manifest.main_latex_path,
        manifest.claim_artifact_map_path,
        manifest.evidence_citation_bindings_path,
        manifest.provenance_manifest_path,
        *([manifest.bibliography_path_optional] if manifest.bibliography_path_optional else []),
    ]
    for value in values:
        if value is None:
            continue
        path = _path_from_relative(root, value)
        if not path.is_file():
            findings.append(
                _finding(
                    len(findings) + 1,
                    "missing_artifact",
                    f"Required final-paper artifact is missing: {value}.",
                    artifact_ids=[value],
                    blocking=True,
                )
            )


def _verify_artifact_hashes(
    root: Path, manifest: FinalPaperManifest, findings: list[FinalPaperVerificationFinding]
) -> None:
    for binding in manifest.artifact_bindings:
        path = _artifact_path_for_id(root, manifest.run_id, binding.artifact_id)
        if path is None or not path.is_file():
            continue
        if sha256_file(path) != binding.content_hash:
            findings.append(
                _finding(
                    len(findings) + 1,
                    "hash_mismatch",
                    "Artifact hash no longer matches the final-paper manifest: "
                    f"{binding.artifact_id}.",
                    artifact_ids=[binding.artifact_id],
                    claim_ids=binding.claim_ids,
                    blocking=True,
                )
            )


def _verify_claim_bindings(
    root: Path, manifest: FinalPaperManifest, findings: list[FinalPaperVerificationFinding]
) -> None:
    claims = _load_claim_bindings(root, manifest)
    by_artifact = {item.artifact_id: item for item in manifest.artifact_bindings}
    markdown_path = _path_from_relative(root, manifest.main_markdown_path)
    markdown = _read_text(markdown_path) if markdown_path.is_file() else ""
    for claim in claims:
        if not claim.supporting_artifact_ids:
            findings.append(
                _finding(
                    len(findings) + 1,
                    "unsupported_claim",
                    f"Claim {claim.claim_id} has no supporting artifact IDs.",
                    claim_ids=[claim.claim_id],
                    blocking=True,
                )
            )
            continue
        missing = [
            artifact_id
            for artifact_id in claim.supporting_artifact_ids
            if artifact_id not in by_artifact or not by_artifact[artifact_id].resolved
        ]
        if missing:
            findings.append(
                _finding(
                    len(findings) + 1,
                    "unsupported_claim",
                    f"Claim {claim.claim_id} has unresolved supporting artifacts.",
                    artifact_ids=missing,
                    claim_ids=[claim.claim_id],
                    blocking=True,
                )
            )
        if claim.requires_qualification and not _has_scope_qualification(markdown):
            findings.append(
                _finding(
                    len(findings) + 1,
                    "missing_scope_qualification",
                    f"Claim {claim.claim_id} requires a visible scope qualification.",
                    claim_ids=[claim.claim_id],
                    blocking=True,
                )
            )


def _verify_metric_tables(
    root: Path, manifest: FinalPaperManifest, findings: list[FinalPaperVerificationFinding]
) -> None:
    markdown_path = _path_from_relative(root, manifest.main_markdown_path)
    markdown = _read_text(markdown_path) if markdown_path.is_file() else ""
    execution = _latest_execution_report(root, manifest.run_id)
    for table in manifest.table_records:
        if not table.resolved:
            findings.append(
                _finding(
                    len(findings) + 1,
                    "unresolved_table",
                    f"Table {table.table_id} was not resolved during assembly.",
                    artifact_ids=table.source_metric_artifact_ids,
                    claim_ids=table.claim_ids_supported,
                    blocking=True,
                )
            )
            continue
        if sha256_text(canonical_json(table.rows)) != table.content_hash:
            findings.append(
                _finding(
                    len(findings) + 1,
                    "hash_mismatch",
                    f"Table {table.table_id} content hash is inconsistent.",
                    artifact_ids=table.source_metric_artifact_ids,
                    blocking=True,
                )
            )
        for row in table.rows:
            source = str(row.get("metric_source", ""))
            metric = str(row.get("metric", ""))
            value = row.get("value")
            source_path, source_error = _validated_execution_metric_source(
                root=root,
                run_id=manifest.run_id,
                execution=execution,
                source=source,
                metric=metric,
                value=value,
            )
            if source_error:
                finding_type = (
                    "metric_mismatch"
                    if "value does not match" in source_error
                    else "unresolved_table"
                )
                findings.append(
                    _finding(
                        len(findings) + 1,
                        finding_type,
                        f"Table {table.table_id} metric source is not a validated sandbox "
                        f"output: {source}. {source_error}",
                        artifact_ids=table.source_metric_artifact_ids,
                        blocking=True,
                    )
                )
                continue
            assert source_path is not None
            actual = _metrics_from_output(source_path).get(metric)
            if actual != value:
                findings.append(
                    _finding(
                        len(findings) + 1,
                        "metric_mismatch",
                        f"Table {table.table_id} value for {metric} differs from its output "
                        "artifact.",
                        artifact_ids=table.source_metric_artifact_ids,
                        claim_ids=table.claim_ids_supported,
                        blocking=True,
                    )
                )
            expected_line = f"| {row['display_label']} | {value} |"
            if expected_line not in markdown and str(value) not in markdown:
                findings.append(
                    _finding(
                        len(findings) + 1,
                        "metric_mismatch",
                        "Final Markdown contains neither the manuscript-presented value nor "
                        f"the deterministically assembled {metric} row.",
                        artifact_ids=table.source_metric_artifact_ids,
                        claim_ids=table.claim_ids_supported,
                        blocking=True,
                    )
                )


def _verify_figures(
    root: Path, manifest: FinalPaperManifest, findings: list[FinalPaperVerificationFinding]
) -> None:
    markdown_path = _path_from_relative(root, manifest.main_markdown_path)
    markdown = _read_text(markdown_path) if markdown_path.is_file() else ""
    for figure in manifest.figure_records:
        path = _path_from_relative(root, figure.file_path)
        if not path.is_file() or not figure.resolved:
            findings.append(
                _finding(
                    len(findings) + 1,
                    "unresolved_figure",
                    f"Figure {figure.figure_id} is missing or unresolved.",
                    artifact_ids=[figure.source_artifact_id],
                    claim_ids=figure.claim_ids_supported,
                    blocking=True,
                )
            )
            continue
        if sha256_file(path) != figure.content_hash:
            findings.append(
                _finding(
                    len(findings) + 1,
                    "hash_mismatch",
                    f"Figure {figure.figure_id} hash no longer matches the manifest.",
                    artifact_ids=[figure.source_artifact_id],
                    blocking=True,
                )
            )
        if figure.file_path not in markdown:
            findings.append(
                _finding(
                    len(findings) + 1,
                    "unresolved_figure",
                    f"Figure {figure.figure_id} is not referenced in final Markdown.",
                    artifact_ids=[figure.source_artifact_id],
                    blocking=True,
                )
            )


def _verify_citations(
    root: Path, manifest: FinalPaperManifest, findings: list[FinalPaperVerificationFinding]
) -> None:
    citations = _load_citation_bindings(root, manifest)
    if not citations:
        findings.append(
            _finding(
                len(findings) + 1,
                "missing_citation",
                "No evidence-citation bindings are available for final-paper verification.",
                blocking=True,
            )
        )
        return
    retrieval = [item for item in citations if item.source_type == "retrieval_source"]
    if retrieval:
        contexts = _load_retrieval_contexts(root / "runs" / manifest.run_id / "reports")
        if not manifest.bibliography_path_optional:
            findings.append(
                _finding(
                    len(findings) + 1,
                    "missing_citation",
                    "Retrieval-supported statements require a generated bibliography.",
                    blocking=True,
                )
            )
            return
        bibliography_path = _path_from_relative(root, manifest.bibliography_path_optional)
        if not bibliography_path.is_file():
            findings.append(
                _finding(
                    len(findings) + 1,
                    "missing_citation",
                    "Retrieval-supported statements require a readable generated bibliography.",
                    blocking=True,
                )
            )
            return
        bibliography = _read_text(bibliography_path)
        for binding in retrieval:
            if _retrieval_source_for_binding(binding, contexts) is None:
                findings.append(
                    _finding(
                        len(findings) + 1,
                        "missing_citation",
                        "Citation binding no longer resolves to a real retrieval context: "
                        f"{binding.binding_id}.",
                        artifact_ids=[binding.artifact_id],
                        claim_ids=binding.supports_claim_ids,
                        blocking=True,
                    )
                )
                continue
            identifier = binding.citation_or_reference_id
            if identifier not in bibliography:
                findings.append(
                    _finding(
                        len(findings) + 1,
                        "missing_citation",
                        f"Bibliography does not resolve retrieval citation {identifier}.",
                        artifact_ids=[binding.artifact_id],
                        claim_ids=binding.supports_claim_ids,
                        blocking=True,
                    )
                )


def _verify_markdown_boundaries(
    root: Path, manifest: FinalPaperManifest, findings: list[FinalPaperVerificationFinding]
) -> None:
    markdown_path = _path_from_relative(root, manifest.main_markdown_path)
    latex_path = _path_from_relative(root, manifest.main_latex_path)
    if not markdown_path.is_file() or not latex_path.is_file():
        return
    markdown = _read_text(markdown_path)
    latex = _read_text(latex_path)
    for reason in _forbidden_claim_reasons(markdown, latex):
        findings.append(_finding(len(findings) + 1, "forbidden_claim", reason, blocking=True))


def _verify_open_obligations(
    manifest: FinalPaperManifest, findings: list[FinalPaperVerificationFinding]
) -> None:
    for obligation in manifest.open_obligations:
        if obligation.blocking_for_publication:
            findings.append(
                _finding(
                    len(findings) + 1,
                    "unresolved_blocking_obligation",
                    obligation.description,
                    artifact_ids=[obligation.source_artifact_id],
                    claim_ids=obligation.affected_claim_ids,
                    blocking=False,
                )
            )


def _bundle_sources(
    *,
    root_path: Path,
    manifest: FinalPaperManifest,
    assembly_path: Path,
    verification_path: Path,
    verification: FinalPaperVerificationReport,
    render_path: Path | None,
    render: FinalPaperRenderReport | None,
) -> tuple[dict[str, Path | str], list[str]]:
    values: dict[str, Path | str] = {
        "paper_markdown": _path_from_relative(root_path, manifest.main_markdown_path),
        "paper_latex": _path_from_relative(root_path, manifest.main_latex_path),
        "manifest": _path_from_relative(
            root_path, _relative(root_path, assembly_path.parent / f"{manifest.manifest_id}.json")
        ),
        "claim_map": _path_from_relative(root_path, manifest.claim_artifact_map_path),
        "citation_bindings": _path_from_relative(
            root_path, manifest.evidence_citation_bindings_path
        ),
        "provenance": _path_from_relative(root_path, manifest.provenance_manifest_path),
        "verification": verification_path,
    }
    if render_path is not None and render is not None and render.render_status == "rendered":
        values.update(
            {
                "render_report": render_path,
                "standalone_latex": _path_from_relative(
                    root_path, render.standalone_latex_path_optional
                ),
                "rendered_pdf": _path_from_relative(
                    root_path, render.rendered_pdf_path_optional
                ),
            }
        )
    if manifest.bibliography_path_optional:
        values["bibliography"] = _path_from_relative(root_path, manifest.bibliography_path_optional)
    blockers: list[str] = []
    for name, value in values.items():
        if not isinstance(value, Path) or not value.is_file():
            blockers.append(f"Bundle source is missing: {name}.")
            continue
        if _contains_secret(value.read_bytes()):
            blockers.append(
                f"Bundle source contains a credential-like value and is excluded: {name}."
            )
    for index, figure in enumerate(manifest.figure_records, start=1):
        path = _path_from_relative(root_path, figure.file_path)
        if not path.is_file() or _contains_secret(path.read_bytes()):
            blockers.append(f"Figure source is unavailable or unsafe: {figure.figure_id}.")
        else:
            values[f"figure:{index:03d}"] = path
    for binding in manifest.artifact_bindings:
        path = _artifact_path_for_id(root_path, manifest.run_id, binding.artifact_id)
        if path is None or not path.is_file() or _contains_secret(path.read_bytes()):
            blockers.append(f"Evidence artifact is unavailable or unsafe: {binding.artifact_id}.")
        else:
            values[f"evidence:{binding.artifact_id}"] = path
    for table in manifest.table_records:
        for row in table.rows:
            source = str(row["metric_source"])
            path = _path_from_relative(root_path, source)
            if not path.is_file() or _contains_secret(path.read_bytes()):
                blockers.append(f"Metric output is unavailable or unsafe: {source}.")
            else:
                values[f"metric:{_slug(source)}"] = path
    if verification.verification_status not in {"verified", "verified_with_warnings"}:
        blockers.append("Final-paper verification is not successful.")
    if render is not None and render.render_status != "rendered":
        blockers.append("Final-paper PDF rendering is not successful.")
    return values, _unique(blockers)


def _write_bundle(
    *,
    bundle_dir: Path,
    root_path: Path,
    manifest: FinalPaperManifest,
    source_files: dict[str, Path | str],
    assembly: FinalPaperAssemblyReport,
    verification: FinalPaperVerificationReport,
) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=False)
    _copy_bundle_file(source_files["paper_markdown"], bundle_dir / "paper" / "final-paper.md")
    if "standalone_latex" in source_files and "rendered_pdf" in source_files:
        _copy_bundle_file(
            source_files["paper_latex"], bundle_dir / "paper" / "final-paper-fragment.tex"
        )
        _copy_bundle_file(
            source_files["standalone_latex"], bundle_dir / "paper" / "final-paper.tex"
        )
        _copy_bundle_file(
            source_files["rendered_pdf"], bundle_dir / "paper" / "final-paper.pdf"
        )
    else:
        _copy_bundle_file(source_files["paper_latex"], bundle_dir / "paper" / "final-paper.tex")
    if "bibliography" in source_files:
        _copy_bundle_file(source_files["bibliography"], bundle_dir / "paper" / "references.bib")
    _copy_bundle_file(
        source_files["manifest"], bundle_dir / "reports" / "final-paper-manifest.json"
    )
    _copy_bundle_file(source_files["claim_map"], bundle_dir / "reports" / "claim-artifact-map.json")
    _copy_bundle_file(
        source_files["citation_bindings"],
        bundle_dir / "reports" / "evidence-citation-bindings.json",
    )
    _copy_bundle_file(
        source_files["verification"], bundle_dir / "reports" / "verification-report.json"
    )
    if "render_report" in source_files:
        _copy_bundle_file(
            source_files["render_report"], bundle_dir / "reports" / "render-report.json"
        )
    _copy_bundle_file(
        source_files["provenance"], bundle_dir / "provenance" / "provenance-manifest.json"
    )
    for key, path in source_files.items():
        if key.startswith("figure:"):
            _copy_bundle_file(path, bundle_dir / "figures" / Path(path).name)
        elif key.startswith("evidence:"):
            _copy_bundle_file(path, bundle_dir / "evidence" / Path(path).name)
        elif key.startswith("metric:"):
            _copy_bundle_file(path, bundle_dir / "evidence" / "metrics" / Path(path).name)
    for table in manifest.table_records:
        _atomic_write_text(
            bundle_dir / "tables" / f"{table.table_id}.json",
            canonical_json(table) + "\n",
        )
    _atomic_write_text(
        bundle_dir / "appendices" / "appendix-records.json",
        canonical_json(manifest.appendix_records) + "\n",
    )
    _atomic_write_text(
        bundle_dir / "provenance" / "open-obligations.json",
        canonical_json(manifest.open_obligations) + "\n",
    )
    _atomic_write_text(
        bundle_dir / "provenance" / "backend-records.json",
        canonical_json(assembly.backend_records) + "\n",
    )
    _atomic_write_text(
        bundle_dir / "reproduction" / "README.md",
        _bundle_reproduction_instructions(manifest, verification),
    )
    _atomic_write_text(
        bundle_dir / "reproducibility" / "bundle-manifest.json",
        canonical_json(
            {
                "bundle_id": bundle_dir.name,
                "run_id": manifest.run_id,
                "source_manifest_id": manifest.manifest_id,
                "publication_ready": False,
                "creates_scientific_validation": False,
                "is_verification_evidence": False,
            }
        )
        + "\n",
    )


def _write_hash_lock(bundle_dir: Path, hashes_path: Path) -> None:
    paths = sorted(path for path in bundle_dir.rglob("*") if path.is_file() and path != hashes_path)
    lines = [f"{sha256_file(path)}  {path.relative_to(bundle_dir).as_posix()}" for path in paths]
    _atomic_write_text(hashes_path, "\n".join(lines) + "\n")


def _reproduction_instructions(run_id: str, manifest: FinalPaperManifest) -> list[str]:
    return [
        f"uv run factori inspect-final-paper --run-id {run_id} --json",
        f"uv run factori verify-final-paper --run-id {run_id}",
        f"uv run factori render-final-paper --run-id {run_id} --allow-external-tools "
        "--latex-executable pdflatex",
        f"uv run factori build-final-paper-bundle --run-id {run_id}",
        f"Inspect metric sources listed in {manifest.provenance_manifest_path}.",
        "The bundle does not guarantee full scientific reproducibility when upstream LLM or "
        "retrieval calls are required.",
    ]


def _bundle_reproduction_instructions(
    manifest: FinalPaperManifest, verification: FinalPaperVerificationReport
) -> str:
    return "\n".join(
        [
            "# Reproduction Context",
            "",
            *[f"- `{item}`" for item in _reproduction_instructions(manifest.run_id, manifest)],
            "",
            f"Verification status at bundle creation: `{verification.verification_status}`.",
            "No claim of full scientific reproducibility, publication readiness, or external "
            "validation is made.",
            "",
        ]
    )


def _production_report(
    *,
    run_id: str,
    records: list[StageBackendRecord],
    config: FinalPaperAssemblyConfig,
    report_id: str,
    requires_metrics: bool,
    requires_retrieval: bool,
    includes_verification: bool = False,
):
    expected = [
        ScientificStageKind.ADJUDICATION,
        ScientificStageKind.MANUSCRIPT_PLANNING,
        ScientificStageKind.MANUSCRIPT_SYNTHESIS,
        ScientificStageKind.CRITIC_REVIEW,
        ScientificStageKind.CLAIM_AUDIT,
        ScientificStageKind.MANUSCRIPT_ARTIFACT_ASSEMBLY,
    ]
    if requires_metrics:
        expected.extend(
            [ScientificStageKind.EXPERIMENT_EXECUTION, ScientificStageKind.METRIC_COMPUTATION]
        )
    if requires_retrieval:
        expected.append(ScientificStageKind.LITERATURE_RETRIEVAL)
    if includes_verification:
        expected.append(ScientificStageKind.BUNDLE_VERIFICATION)
    return evaluate_production_mode(
        run_id=run_id,
        records=records,
        policy=ProductionModePolicy(require_non_fake_backends=config.require_non_fake_backends),
        expected_stage_kinds=expected,
        report_id=f"{report_id}-production-evaluation",
    )


def _assembly_backend_record(report_id: str, artifact_ids: list[str]) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-assembly",
        stage_kind=ScientificStageKind.MANUSCRIPT_ARTIFACT_ASSEMBLY,
        backend_kind=BackendKind.LOCAL_EXECUTION,
        backend_name="final_paper_assembler",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=True,
        allowed_in_production=True,
        reason=(
            "Local assembly resolves immutable paper assets without generating scientific prose."
        ),
        artifact_ids=artifact_ids,
    )


def _retrieval_backend_records(
    report_id: str,
    contexts: list[RetrievalContext],
) -> list[StageBackendRecord]:
    grouped: dict[str, list[str]] = {}
    for context in contexts:
        if context.retrieval_mode != "real_retrieval" or not any(
            not source.fake_or_mocked for source in context.sources
        ):
            continue
        grouped.setdefault(context.backend_name, []).append(context.context_id)
    return [
        stage_backend_record(
            stage_id=f"{report_id}-literature-retrieval-{index:03d}",
            stage_kind=ScientificStageKind.LITERATURE_RETRIEVAL,
            backend_kind=BackendKind.RETRIEVAL_REAL,
            backend_name=backend_name,
            is_scientific_generation=False,
            is_scientific_judgment=False,
            is_execution_or_verification=True,
            allowed_in_production=True,
            reason=(
                "Persisted real retrieval supplies bounded metadata and abstract context; it "
                "does not establish novelty or complete literature coverage."
            ),
            artifact_ids=sorted(context_ids),
            fallback_used=False,
            fallback_disclosed=True,
        )
        for index, (backend_name, context_ids) in enumerate(sorted(grouped.items()), start=1)
    ]


def _verification_backend_record(report_id: str, artifact_ids: list[str]) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-verification",
        stage_kind=ScientificStageKind.BUNDLE_VERIFICATION,
        backend_kind=BackendKind.LOCAL_EXECUTION,
        backend_name="final_paper_verifier",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=True,
        allowed_in_production=True,
        reason=(
            "Local verification checks final-paper structure, bindings, hashes, and scope "
            "boundaries."
        ),
        artifact_ids=artifact_ids,
    )


def _latest_valid_revision(
    reports: Path,
) -> tuple[Path | None, NucleusManuscriptSynthesisReport | None]:
    for path in _matching_desc(reports, _SYNTHESIS_RE):
        report = _read_model(path, NucleusManuscriptSynthesisReport)
        revision = report.revision_report_optional
        if (
            report.phase == "revision"
            and report.revised_draft_optional is not None
            and report.manuscript_status
            in {
                NucleusManuscriptStatus.BOUNDED_DRAFT,
                NucleusManuscriptStatus.SCIENTIFIC_DRAFT_WITH_OPEN_OBLIGATIONS,
            }
            and revision is not None
            and revision.status == "revised"
            and not report.blocking_reasons
        ):
            return path, report
    return None, None


def _latest_assembly_report(
    reports: Path, *, operation: str
) -> tuple[Path | None, FinalPaperAssemblyReport | None]:
    for path in _matching_desc(reports, _ASSEMBLY_RE):
        report = _read_model(path, FinalPaperAssemblyReport)
        if report.operation == operation:
            return path, report
    return None, None


def _latest_verification_report(
    reports: Path,
) -> tuple[Path | None, FinalPaperVerificationReport | None]:
    paths = _matching_desc(reports, _VERIFICATION_RE)
    if not paths:
        return None, None
    return paths[0], _read_model(paths[0], FinalPaperVerificationReport)


def _latest_render_report(
    reports: Path,
) -> tuple[Path | None, FinalPaperRenderReport | None]:
    paths = _matching_desc(reports, _RENDER_RE)
    if not paths:
        return None, None
    return paths[0], _read_model(paths[0], FinalPaperRenderReport)


def _load_claim_bindings(root: Path, manifest: FinalPaperManifest) -> list[ClaimArtifactBinding]:
    path = _path_from_relative(root, manifest.claim_artifact_map_path)
    if not path.is_file():
        return []
    payload = _read_json(path)
    return (
        [ClaimArtifactBinding.model_validate(item) for item in payload]
        if isinstance(payload, list)
        else []
    )


def _load_citation_bindings(
    root: Path, manifest: FinalPaperManifest
) -> list[EvidenceCitationBinding]:
    path = _path_from_relative(root, manifest.evidence_citation_bindings_path)
    if not path.is_file():
        return []
    payload = _read_json(path)
    return (
        [EvidenceCitationBinding.model_validate(item) for item in payload]
        if isinstance(payload, list)
        else []
    )


def _resolved_claim_count(
    claims: list[ClaimArtifactBinding], bindings: list[FinalPaperArtifactBinding]
) -> int:
    by_id = {item.artifact_id: item for item in bindings}
    return sum(
        bool(claim.supporting_artifact_ids)
        and all(by_id.get(item) and by_id[item].resolved for item in claim.supporting_artifact_ids)
        for claim in claims
    )


def _result_backend(
    execution: EvidencePackageExecutionReport,
    result: EvidencePackageExecutionResult | None,
) -> str:
    if result is None:
        return "unknown"
    if result.metrics:
        return BackendKind.LOCAL_EXECUTION.value
    if result.artifact_type.value in {"symbolic_reduction", "symbolic_derivation", "proof_plan"}:
        return BackendKind.LLM_OPENAI.value
    return next(
        (
            item.backend_kind.value
            for item in execution.backend_records
            if item.stage_kind == ScientificStageKind.EXPERIMENT_EXECUTION
        ),
        BackendKind.LOCAL_EXECUTION.value,
    )


def _result_section_ids(draft: NucleusManuscriptDraft | None) -> list[str]:
    if draft is None:
        return []
    return [
        _slug(title)
        for title, _ in _markdown_sections(draft.markdown)
        if "result" in title.lower() or "benchmark" in title.lower()
    ] or ["results"]


def _metrics_from_output(path: Path) -> dict[str, float | int]:
    payload = _read_json(path)
    if not isinstance(payload, dict):
        return {}
    raw = payload.get("metrics", payload)
    if not isinstance(raw, dict):
        return {}
    return _flatten_output_metrics(raw)


def _flatten_output_metrics(
    value: dict[str, Any] | list[Any],
    *,
    prefix: str = "",
) -> dict[str, float | int]:
    flattened: dict[str, float | int] = {}
    items = value.items() if isinstance(value, dict) else enumerate(value)
    for key, item in items:
        metric_name = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(item, bool):
            continue
        if isinstance(item, (int, float)):
            flattened[metric_name] = item
        elif isinstance(item, (dict, list)):
            flattened.update(_flatten_output_metrics(item, prefix=metric_name))
    return flattened


def _latest_execution_report(root: Path, run_id: str) -> EvidencePackageExecutionReport | None:
    reports = root / "runs" / run_id / "reports"
    path = _latest_matching(
        reports, re.compile(r"^evidence-package-execution-report-(\d{4})\.json$")
    )
    return _read_model(path, EvidencePackageExecutionReport) if path else None


def _validated_execution_metric_source(
    *,
    root: Path,
    run_id: str,
    execution: EvidencePackageExecutionReport | None,
    source: str | None,
    metric: str,
    value: object,
) -> tuple[Path | None, str | None]:
    """Resolve a metric only when M103 recorded it from a completed sandbox output.

    The JSON-pointer suffix is part of the provenance contract. A JSON file that only resembles
    an output artifact is insufficient: it must be the declared output of a completed sandbox
    execution and agree with the corresponding schema-valid metric extraction record.
    """
    if execution is None:
        return None, "No hybrid evidence execution report is available."
    if not source or not _METRIC_SOURCE_RE.fullmatch(source):
        return None, "Metric source does not use the required sandbox output JSON-pointer format."
    source_path_text, pointer = source.split("#", 1)
    if pointer != f"metrics.{metric}":
        return None, "Metric source JSON pointer does not match the declared metric name."
    expected_prefix = f"runs/{run_id}/experiments/"
    if not source_path_text.startswith(expected_prefix):
        return None, "Metric source is outside this run's sandbox experiment directory."
    source_path = _path_from_relative(root, source)
    if not source_path.is_file():
        return None, "Sandbox output JSON is missing."

    completed = {
        item.execution_id: item
        for item in execution.sandbox_executions
        if item.status == "completed" and item.output_json_path
    }
    matching_execution = next(
        (
            item
            for item in completed.values()
            if _path_from_relative(root, item.output_json_path) == source_path
        ),
        None,
    )
    if matching_execution is None:
        return None, "Metric source is not a completed sandbox execution output."
    extraction = next(
        (
            item
            for item in execution.metric_extractions
            if item.execution_id == matching_execution.execution_id
        ),
        None,
    )
    if extraction is None or not extraction.metrics_extracted or not extraction.schema_valid:
        return None, "No successful schema-valid metric extraction exists for the sandbox output."
    if extraction.metric_sources.get(metric) != source:
        return None, "Metric extraction provenance does not match the declared metric source."
    if extraction.metrics.get(metric) != value:
        return None, "Metric extraction value does not match the declared result value."
    if _metrics_from_output(source_path).get(metric) != value:
        return None, "Sandbox output value does not match the declared result value."
    return source_path, None


def _is_sandbox_generated_figure(inputs: _Inputs, path: Path) -> bool:
    if inputs.execution is None:
        return False
    for execution in inputs.execution.sandbox_executions:
        if execution.status != "completed":
            continue
        for artifact_path in execution.artifact_paths:
            if _path_from_relative(inputs.root, artifact_path) == path:
                return True
    return False


def _manuscript_metric_mismatch_reasons(
    draft: NucleusManuscriptDraft,
    tables: list[FinalPaperTableRecord],
) -> list[str]:
    """Check that metric rows retained from M105 equal execution-derived values."""
    blockers: list[str] = []
    rows_by_key = {
        (str(row["artifact_id"]), str(row["metric"])): row
        for table in tables
        for row in table.rows
    }
    for binding in draft.metric_token_bindings:
        key = (str(binding.get("artifact_id", "")), str(binding.get("metric", "")))
        row = rows_by_key.get(key)
        if row is None:
            blockers.append(
                "A manuscript metric token is absent from the validated final-paper table: "
                f"{key[0]}/{key[1]}."
            )
        elif row["value"] != binding.get("value"):
            blockers.append(
                "A manuscript metric token differs from the validated execution artifact: "
                f"{key[0]}/{key[1]}."
            )
    source = f"{draft.markdown}\n{draft.latex}"
    for table in tables:
        for row in table.rows:
            artifact_id = str(row["artifact_id"])
            metric = str(row["metric"])
            expected = str(row["value"])
            markdown_pattern = re.compile(
                rf"\|\s*{re.escape(artifact_id)}\s*\|\s*{re.escape(metric)}\s*\|\s*"
                r"([^|]+?)\s*\|"
            )
            latex_pattern = re.compile(
                rf"{re.escape(_latex_escape(artifact_id))}\s*&\s*"
                rf"{re.escape(_latex_escape(metric))}\s*&\s*([^\\\s]+)\s*\\\\"
            )
            observed = [match.group(1).strip() for match in markdown_pattern.finditer(source)]
            observed.extend(match.group(1).strip() for match in latex_pattern.finditer(source))
            if any(item != expected for item in observed):
                blockers.append(
                    "Manuscript metric value differs from the validated execution artifact: "
                    f"{artifact_id}/{metric}."
                )
    return _unique(blockers)


def _retrieval_source_for_binding(
    binding: EvidenceCitationBinding, contexts: list[RetrievalContext]
):
    for context in contexts:
        if context.retrieval_mode != "real_retrieval":
            continue
        for source in context.sources:
            if source.fake_or_mocked:
                continue
            if binding.citation_or_reference_id in {source.source_id, source.doi}:
                return source
    return None


def _artifact_path_for_id(root: Path, run_id: str, artifact_id: str) -> Path | None:
    run = root / "runs" / run_id
    candidates = [
        run / "reports" / f"{artifact_id}.json",
        run / "reports" / f"{artifact_id}.md",
        run / "experiments" / f"{artifact_id}.json",
        run / "experiments" / f"{artifact_id}.py",
        run / "latex" / f"{artifact_id}.tex",
    ]
    return next((path for path in candidates if path.is_file()), None)


def _path_from_relative(root: Path, value: str | None) -> Path:
    if not value:
        return root / "__missing__"
    # Metric sources use a JSON-pointer suffix (for example,
    # ``...output.json#metrics.held_out_mae``).  The suffix identifies the
    # value inside the artifact and is not part of the filesystem path.
    path = Path(value.split("#", 1)[0])
    if path.is_absolute() or ".." in path.parts:
        return root / "__unsafe__"
    return root / path


def _resolve_figure_reference(root: Path, run_id: str, value: str) -> Path | None:
    raw = Path(value)
    if raw.is_absolute():
        return None
    candidates = [
        _path_from_relative(root, value),
        root / "runs" / run_id / "reports" / raw,
        root / "runs" / run_id / "experiments" / raw.name,
    ]
    return next((path for path in candidates if path.is_file()), None)


def _figure_source_artifact_id(path: Path) -> str:
    return path.stem.removesuffix("-output")


def _figure_caption_from_output(path: Path) -> str | None:
    del path
    return None


def _markdown_sections(markdown: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, list[str]]] = []
    current: tuple[str, list[str]] | None = None
    for line in markdown.splitlines():
        match = _HEADING_RE.match(line)
        if match:
            if current is not None:
                sections.append(current)
            current = (match.group(2).strip(), [])
        elif current is not None:
            current[1].append(line)
    if current is not None:
        sections.append(current)
    if not sections:
        return [("Manuscript", markdown)]
    return [(title, "\n".join(body).strip()) for title, body in sections]


def _claim_tokens(value: str) -> list[str]:
    tokens = [item.lower() for item in re.findall(r"[A-Za-z]{5,}", value)]
    return tokens[:3] or [value.lower()]


def _scope_qualification_reasons(markdown: str, claims: list[ClaimArtifactBinding]) -> list[str]:
    return [
        f"Claim {claim.claim_id} requires a visible scope qualification."
        for claim in claims
        if claim.requires_qualification and not _has_scope_qualification(markdown)
    ]


def _has_scope_qualification(markdown: str) -> bool:
    text = markdown.lower()
    return any(
        value in text
        for value in (
            "bounded",
            "synthetic",
            "declared setting",
            "under the declared",
            "scope",
            "limitation",
            "not real-world",
        )
    )


def _forbidden_claim_reasons(markdown: str, latex: str) -> list[str]:
    text = f"{markdown}\n{latex}".lower()
    reasons = [
        message
        for phrase, message in _FORBIDDEN_PATTERNS.items()
        if phrase not in {"real-world validation", "real world validation"}
        and phrase in text
    ]
    if _contains_affirmative_real_world_validation(text):
        reasons.append("real-world validation assertion")
    return _unique(reasons)


def _contains_affirmative_real_world_validation(text: str) -> bool:
    for sentence in re.split(r"(?<=[.!?;])\s+|\n+", text.casefold()):
        for match in re.finditer(r"\breal[- ]world validation\b", sentence):
            before = sentence[: match.start()]
            after = sentence[match.end() :]
            if re.search(
                r"\b(?:not|no|never|without|avoid|avoids|cannot|can't|doesn't|"
                r"does not|do not|don't|isn't|is not|unverified|unproven|"
                r"unsupported|unresolved|forbid|forbids|must not|should not)\b",
                before,
            ):
                continue
            if re.match(
                r"\s*(?:is|are|was|were|has been|have been|remains?)?\s*"
                r"(?:not|never|unverified|unproven|unsupported|unresolved|forbidden|"
                r"disallowed|absent|outside|out of scope|must not|should not|cannot)\b",
                after,
            ):
                continue
            return True
    return False


def _obligation_type(value: str) -> str:
    lower = value.lower()
    if "proof" in lower:
        return "unresolved_proof"
    if "symbolic" in lower or "derivation" in lower:
        return "unresolved_symbolic_step"
    if "retrieval" in lower or "citation" in lower:
        return "retrieval_gap" if "retrieval" in lower else "missing_citation"
    if "novel" in lower or "overlap" in lower:
        return "novelty_gap"
    if "robust" in lower:
        return "missing_robustness"
    if "real data" in lower:
        return "missing_real_data"
    if "external validation" in lower:
        return "missing_external_validation"
    if "negative control" in lower:
        return "failed_negative_control"
    if "repair" in lower or "critic" in lower:
        return "critic_required_repair"
    if "figure" in lower:
        return "missing_figure"
    return "other"


def _assembled_status(
    status: NucleusManuscriptStatus, obligations: list[FinalPaperOpenObligation]
) -> str:
    if status == NucleusManuscriptStatus.BOUNDED_DRAFT and not obligations:
        return "bounded_draft"
    return "scientific_draft_with_open_obligations"


def _verified_status(assembly_status: str, verification_status: str) -> str:
    if assembly_status == "bounded_draft" and verification_status in {
        "verified",
        "verified_with_warnings",
    }:
        return "verified_bounded_draft"
    return assembly_status


def _finding(
    index: int,
    finding_type: str,
    description: str,
    *,
    artifact_ids: list[str] | None = None,
    claim_ids: list[str] | None = None,
    blocking: bool,
) -> FinalPaperVerificationFinding:
    return FinalPaperVerificationFinding(
        finding_id=f"final-paper-verification-finding-{index:03d}",
        finding_type=finding_type,
        description=description,
        artifact_ids=artifact_ids or [],
        claim_ids=claim_ids or [],
        blocking=blocking,
    )


def _matching_desc(directory: Path, pattern: re.Pattern[str]) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        (path for path in directory.iterdir() if pattern.match(path.name)),
        key=lambda path: path.name,
        reverse=True,
    )


def _latest_matching(directory: Path, pattern: re.Pattern[str]) -> Path | None:
    paths = _matching_desc(directory, pattern)
    return paths[0] if paths else None


def _next_number(directory: Path, pattern: re.Pattern[str]) -> int:
    if not directory.is_dir():
        return 1
    numbers = [
        int(match.group(1)) for path in directory.iterdir() if (match := pattern.match(path.name))
    ]
    return max(numbers, default=0) + 1


def _load_retrieval_contexts(reports: Path) -> list[RetrievalContext]:
    paths = (
        sorted(reports.glob("retrieval-context-[0-9][0-9][0-9][0-9].json"))
        if reports.is_dir()
        else []
    )
    contexts: list[RetrievalContext] = []
    for path in paths:
        try:
            contexts.append(_read_model(path, RetrievalContext))
        except FinalPaperError:
            continue
    return contexts


def _read_model(path: Path, model_type: Any) -> Any:
    try:
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise FinalPaperError(f"Could not load {path}: {exc}") from exc


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _copy_bundle_file(source: Path | str, destination: Path) -> None:
    path = Path(source)
    _atomic_write_bytes(destination, path.read_bytes())


def _atomic_write_text(path: Path, text: str) -> None:
    _atomic_write_bytes(path, text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _contains_secret(payload: bytes) -> bool:
    try:
        return bool(_SECRET_RE.search(payload.decode("utf-8")))
    except UnicodeDecodeError:
        return False


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _metadata(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "publication_ready": False,
        "creates_scientific_validation": False,
        "is_verification_evidence": False,
    }


def _production_blockers(production: Any) -> list[str]:
    return [item.message for item in production.violations if item.blocking]


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


def _slug(value: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", value.lower())) or "item"


def _citation_key(value: str) -> str:
    return "Retrieved" + "".join(part.title() for part in re.findall(r"[A-Za-z0-9]+", value))


def _bib_escape(value: str) -> str:
    return value.replace("{", "\\{").replace("}", "\\}")


def _latex_escape(value: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "&": r"\&",
        "%": r"\%",
        "$": r"\$",
        "#": r"\#",
        "_": r"\_",
        "{": r"\{",
        "}": r"\}",
        "~": r"\textasciitilde{}",
        "^": r"\textasciicircum{}",
    }
    return "".join(replacements.get(character, character) for character in value)


def _latex_path(value: str) -> str:
    delimiter = next((item for item in ("|", "!", "+", ";") if item not in value), None)
    if delimiter is None:
        return _latex_escape(value)
    return rf"\path{delimiter}{value}{delimiter}"


def _latex_unescape_text(value: str) -> str:
    return (
        value.replace(r"\_", "_")
        .replace(r"\#", "#")
        .replace(r"\%", "%")
        .replace(r"\&", "&")
    )


def _empty_hash() -> str:
    return "0" * 64


def _validate_config(run_id: str, config: FinalPaperAssemblyConfig) -> None:
    if config.run_id != run_id:
        raise FinalPaperError("FinalPaperAssemblyConfig.run_id must match run_id.")
