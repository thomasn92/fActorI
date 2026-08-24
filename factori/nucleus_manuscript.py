"""Bounded LLM manuscript synthesis around an adjudicated paper nucleus (M105)."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

from factori.adapters.errors import AdapterError
from factori.adapters.nucleus_manuscript import NucleusManuscriptClient
from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.production_mode import evaluate_production_mode, stage_backend_record
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BackendKind,
    ClaimArtifactBinding,
    ControllerActionType,
    CrossPackageAdjudicationReport,
    EvidenceArtifactType,
    EvidenceCitationBinding,
    EvidencePackageExecutionReport,
    EvidencePackageExecutionResult,
    HybridEvidencePackageCandidate,
    HybridEvidencePackageReport,
    ManuscriptCriticReview,
    ManuscriptCriticRole,
    ManuscriptRevisionReport,
    ManuscriptSectionPlan,
    NucleusManuscriptConfig,
    NucleusManuscriptDraft,
    NucleusManuscriptInspectionReport,
    NucleusManuscriptPlan,
    NucleusManuscriptRawArtifact,
    NucleusManuscriptStatus,
    NucleusManuscriptSynthesisReport,
    PaperNucleusSelection,
    ProductionModePolicy,
    RetrievalContext,
    RetrievedSourceSummary,
    ScientificStageKind,
    StageBackendRecord,
)

_ADJUDICATION_RE = re.compile(r"^cross-package-adjudication-report-(\d{4})\.json$")
_PACKAGE_RE = re.compile(r"^hybrid-evidence-package-report-(\d{4})\.json$")
_EXECUTION_RE = re.compile(r"^evidence-package-execution-report-(\d{4})\.json$")
_REPORT_RE = re.compile(r"^nucleus-manuscript-synthesis-report-(\d{4})\.json$")
_RAW_RE = re.compile(r"^nucleus-manuscript-raw-(\d{4})\.json$")
_RETRIEVAL_RE = re.compile(r"^retrieval-context-(\d{4})\.json$")
_ARTIFACT_TOKEN_RE = re.compile(r"[a-z0-9]+")
_DECIMAL_LITERAL_RE = re.compile(
    r"(?<![A-Za-z0-9_])[-+]?(?:\d+\.\d+|\.\d+)(?:[eE][-+]?\d+)?"
    r"(?![A-Za-z0-9_]|\.\d)"
)

_REVIEW_ROLES = tuple(ManuscriptCriticRole)
_MAIN_RESULT_STATUSES = {"completed", "negative_result", "draft_created"}
_FORBIDDEN_PHRASES = {
    "publication_ready=true": "publication readiness assertion",
    "publication ready": "publication readiness assertion",
    "novelty proven": "novelty assertion",
    "novelty is proven": "novelty assertion",
    "underuse proven": "underuse assertion",
    "underuse is proven": "underuse assertion",
    "we prove": "proof assertion without checker evidence",
    "theorem proved": "proof assertion without checker evidence",
    "real-world validation": "real-world validation assertion",
    "real world validation": "real-world validation assertion",
}
_MANDATORY_FORBIDDEN = [
    "real-world validation",
    "verified theorem",
    "novelty proven",
    "underuse proven",
    "publication ready",
    "general domain truth",
]


class NucleusManuscriptError(RuntimeError):
    """Raised only when an M105 operation cannot persist a bounded result safely."""


@dataclass(frozen=True)
class NucleusManuscriptResult:
    run_id: str
    report: NucleusManuscriptSynthesisReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


def plan_nucleus_manuscript(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    planner: NucleusManuscriptClient,
    config: NucleusManuscriptConfig,
) -> NucleusManuscriptResult:
    """Produce an artifact-bound LLM paper plan or a persisted deferred report."""
    _validate_client(planner)
    _validate_config(run_id, config)
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    report_number = _next_number(reports, _REPORT_RE)
    raw_number = _next_number(reports, _RAW_RE)
    report_id = f"nucleus-manuscript-synthesis-report-{report_number:04d}"
    try:
        inputs = _load_inputs(root_path, run_id)
        blockers = _prerequisite_blockers(
            inputs,
            require_literature_context=config.require_non_fake_backends,
        )
        if blockers:
            return _persist_deferred(
                run_id=run_id,
                root_path=root_path,
                store=store,
                ledger=ledger,
                report_id=report_id,
                phase="planning",
                source_adjudication_path=inputs.adjudication_path,
                blockers=blockers,
                unresolved=_unresolved_obligations(inputs),
                backend_records=[],
            )
        evidence = _build_evidence_context(inputs)
        plan_id = f"nucleus-manuscript-plan-{report_number:04d}"
        response = planner.plan_manuscript(
            prompt_id=f"{report_id}-planning-prompt-001",
            nucleus_payload=inputs.nucleus.model_dump(mode="json"),
            evidence_payload=evidence,
        )
        raw = _raw_artifact(
            raw_id=f"nucleus-manuscript-raw-{raw_number:04d}",
            run_id=run_id,
            operation="planning",
            client=planner,
            response=response,
            accepted_id=plan_id if response.accepted and not response.rejection_reasons else None,
        )
        if response.accepted is None or response.rejection_reasons:
            return _persist_deferred(
                run_id=run_id,
                root_path=root_path,
                store=store,
                ledger=ledger,
                report_id=report_id,
                phase="planning",
                source_adjudication_path=inputs.adjudication_path,
                blockers=[
                    "LLM manuscript plan was rejected: " + "; ".join(response.rejection_reasons)
                ],
                unresolved=_unresolved_obligations(inputs),
                backend_records=[_planning_record(report_id, planner, [raw.raw_artifact_id])],
                raw_artifacts=[raw],
            )
        plan = _materialize_plan(
            plan_id=plan_id,
            run_id=run_id,
            nucleus=inputs.nucleus,
            selection_id=inputs.adjudication.report_id,
            proposal=response.accepted,
            bindings=inputs.claim_bindings,
            citations=inputs.citation_bindings,
        )
        validation = _validate_plan(plan, inputs)
        if validation:
            return _persist_deferred(
                run_id=run_id,
                root_path=root_path,
                store=store,
                ledger=ledger,
                report_id=report_id,
                phase="planning",
                source_adjudication_path=inputs.adjudication_path,
                blockers=validation,
                unresolved=_unresolved_obligations(inputs),
                backend_records=[_planning_record(report_id, planner, [raw.raw_artifact_id])],
                raw_artifacts=[raw],
            )
        backend_records = [
            _planning_record(report_id, planner, [raw.raw_artifact_id, plan.plan_id]),
            _claim_audit_record(report_id, [item.claim_id for item in inputs.claim_bindings]),
            _assembly_record(report_id, [item.binding_id for item in inputs.citation_bindings]),
        ]
        production = _production_report(
            run_id=run_id,
            inputs=inputs,
            records=backend_records,
            config=config,
            expected=[
                ScientificStageKind.ADJUDICATION,
                ScientificStageKind.MANUSCRIPT_PLANNING,
                ScientificStageKind.CLAIM_AUDIT,
                ScientificStageKind.MANUSCRIPT_ARTIFACT_ASSEMBLY,
            ],
            report_id=report_id,
        )
        if config.require_non_fake_backends and production.blocking_violation_count:
            raise NucleusManuscriptError(
                _production_error("Strict manuscript planning", production)
            )
        report = NucleusManuscriptSynthesisReport(
            run_id=run_id,
            report_id=report_id,
            phase="planning",
            manuscript_status=NucleusManuscriptStatus.SCIENTIFIC_DRAFT_WITH_OPEN_OBLIGATIONS,
            source_adjudication_report_path=_relative(root_path, inputs.adjudication_path),
            paper_nucleus_selection_id_optional=inputs.adjudication.report_id,
            plan_path_optional=f"runs/{run_id}/reports/{plan_id}.json",
            claim_artifact_map_path_optional=f"runs/{run_id}/reports/claim-artifact-map-{report_number:04d}.json",
            evidence_citation_bindings_path_optional=(
                f"runs/{run_id}/reports/evidence-citation-bindings-{report_number:04d}.json"
            ),
            manuscript_plan_optional=plan,
            claim_artifact_bindings=inputs.claim_bindings,
            evidence_citation_bindings=inputs.citation_bindings,
            unresolved_obligations=_unresolved_obligations(inputs),
            backend_records=backend_records,
            raw_artifact_paths=[f"runs/{run_id}/reports/{raw.raw_artifact_id}.json"],
            production_ready=(
                config.require_non_fake_backends and not production.blocking_violation_count
            ),
        )
        return _persist_report(
            report=report,
            raw_artifacts=[raw],
            store=store,
            ledger=ledger,
            action=ControllerActionType.NUCLEUS_MANUSCRIPT_PLANNED,
        )
    except NucleusManuscriptError:
        raise
    except (AdapterError, ValueError) as exc:
        raise NucleusManuscriptError(f"Nucleus manuscript planning failed: {exc}") from exc


def synthesize_nucleus_manuscript(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    planner: NucleusManuscriptClient,
    config: NucleusManuscriptConfig,
) -> NucleusManuscriptResult:
    """Draft Markdown and LaTeX from a valid M105 plan, or persist a deferral."""
    _validate_client(planner)
    _validate_config(run_id, config)
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    report_number = _next_number(reports, _REPORT_RE)
    raw_number = _next_number(reports, _RAW_RE)
    report_id = f"nucleus-manuscript-synthesis-report-{report_number:04d}"
    inputs = _load_inputs(root_path, run_id)
    planning = _latest_phase_report(reports, "planning")
    if planning is None or planning.manuscript_plan_optional is None:
        return _persist_deferred(
            run_id=run_id,
            root_path=root_path,
            store=store,
            ledger=ledger,
            report_id=report_id,
            phase="synthesis",
            source_adjudication_path=inputs.adjudication_path,
            blockers=["No valid nucleus manuscript plan found; run plan-nucleus-manuscript first."],
            unresolved=_unresolved_obligations(inputs),
            backend_records=[],
        )
    blockers = _prerequisite_blockers(
        inputs,
        require_literature_context=config.require_non_fake_backends,
    ) + _validate_plan(
        planning.manuscript_plan_optional, inputs
    )
    if blockers:
        return _persist_deferred(
            run_id=run_id,
            root_path=root_path,
            store=store,
            ledger=ledger,
            report_id=report_id,
            phase="synthesis",
            source_adjudication_path=inputs.adjudication_path,
            blockers=blockers,
            unresolved=_unresolved_obligations(inputs),
            backend_records=[],
        )
    plan = planning.manuscript_plan_optional
    evidence = _build_evidence_context(inputs)
    response = planner.synthesize_manuscript(
        prompt_id=f"{report_id}-synthesis-prompt-001",
        plan_payload=plan.model_dump(mode="json"),
        evidence_payload=evidence,
    )
    draft_id = f"nucleus-manuscript-draft-{report_number:04d}"
    raw = _raw_artifact(
        raw_id=f"nucleus-manuscript-raw-{raw_number:04d}",
        run_id=run_id,
        operation="synthesis",
        client=planner,
        response=response,
        accepted_id=draft_id if response.accepted and not response.rejection_reasons else None,
    )
    if response.accepted is None or response.rejection_reasons:
        return _persist_deferred(
            run_id=run_id,
            root_path=root_path,
            store=store,
            ledger=ledger,
            report_id=report_id,
            phase="synthesis",
            source_adjudication_path=inputs.adjudication_path,
            blockers=[
                "LLM manuscript draft was rejected: " + "; ".join(response.rejection_reasons)
            ],
            unresolved=_unresolved_obligations(inputs),
            backend_records=[_synthesis_record(report_id, planner, [raw.raw_artifact_id])],
            raw_artifacts=[raw],
        )
    draft, validation = _materialize_draft(
        draft_id=draft_id,
        run_id=run_id,
        plan=plan,
        proposal=response.accepted,
        inputs=inputs,
    )
    if validation:
        return _persist_deferred(
            run_id=run_id,
            root_path=root_path,
            store=store,
            ledger=ledger,
            report_id=report_id,
            phase="synthesis",
            source_adjudication_path=inputs.adjudication_path,
            blockers=validation,
            unresolved=_unresolved_obligations(inputs),
            backend_records=[_synthesis_record(report_id, planner, [raw.raw_artifact_id])],
            raw_artifacts=[raw],
        )
    backend_records = [
        _synthesis_record(report_id, planner, [raw.raw_artifact_id, draft.draft_id]),
        _claim_audit_record(report_id, [item.claim_id for item in inputs.claim_bindings]),
        _assembly_record(report_id, [item.binding_id for item in inputs.citation_bindings]),
    ]
    all_backend_records = [*planning.backend_records, *backend_records]
    production = _production_report(
        run_id=run_id,
        inputs=inputs,
        records=all_backend_records,
        config=config,
        expected=[
            ScientificStageKind.ADJUDICATION,
            ScientificStageKind.MANUSCRIPT_PLANNING,
            ScientificStageKind.MANUSCRIPT_SYNTHESIS,
            ScientificStageKind.CLAIM_AUDIT,
            ScientificStageKind.MANUSCRIPT_ARTIFACT_ASSEMBLY,
        ],
        report_id=report_id,
    )
    if config.require_non_fake_backends and production.blocking_violation_count:
        raise NucleusManuscriptError(_production_error("Strict manuscript synthesis", production))
    report = NucleusManuscriptSynthesisReport(
        run_id=run_id,
        report_id=report_id,
        phase="synthesis",
        manuscript_status=_draft_status(inputs),
        source_adjudication_report_path=_relative(root_path, inputs.adjudication_path),
        paper_nucleus_selection_id_optional=inputs.adjudication.report_id,
        plan_path_optional=planning.plan_path_optional,
        draft_markdown_path_optional=f"runs/{run_id}/reports/{draft_id}.md",
        draft_latex_path_optional=f"runs/{run_id}/latex/{draft_id}.tex",
        claim_artifact_map_path_optional=planning.claim_artifact_map_path_optional,
        evidence_citation_bindings_path_optional=planning.evidence_citation_bindings_path_optional,
        manuscript_plan_optional=plan,
        draft_optional=draft,
        claim_artifact_bindings=inputs.claim_bindings,
        evidence_citation_bindings=inputs.citation_bindings,
        unresolved_obligations=_unresolved_obligations(inputs),
        backend_records=all_backend_records,
        raw_artifact_paths=[f"runs/{run_id}/reports/{raw.raw_artifact_id}.json"],
        production_ready=(
            config.require_non_fake_backends and not production.blocking_violation_count
        ),
    )
    return _persist_report(
        report=report,
        raw_artifacts=[raw],
        draft=draft,
        store=store,
        ledger=ledger,
        action=ControllerActionType.NUCLEUS_MANUSCRIPT_SYNTHESIZED,
    )


def revise_nucleus_manuscript(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    planner: NucleusManuscriptClient,
    config: NucleusManuscriptConfig,
) -> NucleusManuscriptResult:
    """Run an LLM manuscript critic ensemble, revise once, then revalidate and re-critique."""
    _validate_client(planner)
    _validate_config(run_id, config)
    root_path = Path(root)
    reports = root_path / "runs" / run_id / "reports"
    report_number = _next_number(reports, _REPORT_RE)
    raw_number = _next_number(reports, _RAW_RE)
    report_id = f"nucleus-manuscript-synthesis-report-{report_number:04d}"
    inputs = _load_inputs(root_path, run_id)
    synthesis = _latest_phase_report(reports, "synthesis")
    if (
        synthesis is None
        or synthesis.draft_optional is None
        or synthesis.manuscript_plan_optional is None
    ):
        return _persist_deferred(
            run_id=run_id,
            root_path=root_path,
            store=store,
            ledger=ledger,
            report_id=report_id,
            phase="revision",
            source_adjudication_path=inputs.adjudication_path,
            blockers=[
                "No valid nucleus manuscript draft found; run synthesize-nucleus-manuscript first."
            ],
            unresolved=_unresolved_obligations(inputs),
            backend_records=[],
        )
    plan = synthesis.manuscript_plan_optional
    draft = synthesis.draft_optional
    evidence = _build_evidence_context(inputs)
    first_reviews, first_raws = _run_critics(
        run_id=run_id,
        report_id=report_id,
        raw_start=raw_number,
        planner=planner,
        draft=draft,
        evidence=evidence,
    )
    if _blocking_reviews(first_reviews):
        # The revision call may repair bounded presentation defects, but a fresh review decides.
        pass
    revision_response = planner.revise_manuscript(
        prompt_id=f"{report_id}-revision-prompt-001",
        draft_payload=draft.model_dump(mode="json"),
        critic_reviews_payload=[item.model_dump(mode="json") for item in first_reviews],
        evidence_payload=evidence,
    )
    revised_id = f"nucleus-manuscript-revised-{report_number:04d}"
    revision_raw = _raw_artifact(
        raw_id=f"nucleus-manuscript-raw-{raw_number + len(first_raws):04d}",
        run_id=run_id,
        operation="revision",
        client=planner,
        response=revision_response,
        accepted_id=revised_id
        if revision_response.accepted and not revision_response.rejection_reasons
        else None,
    )
    if revision_response.accepted is None or revision_response.rejection_reasons:
        return _persist_revision_deferred(
            run_id=run_id,
            root_path=root_path,
            store=store,
            ledger=ledger,
            report_id=report_id,
            inputs=inputs,
            plan=plan,
            draft=draft,
            reviews=first_reviews,
            raw_artifacts=[*first_raws, revision_raw],
            blockers=[
                "LLM manuscript revision was rejected: "
                + "; ".join(revision_response.rejection_reasons)
            ],
            applied=[],
            backend_records=[
                _critic_record(report_id, planner, [item.raw_artifact_id for item in first_raws]),
                _synthesis_record(report_id, planner, [revision_raw.raw_artifact_id]),
            ],
        )
    revised, validation = _materialize_draft(
        draft_id=revised_id,
        run_id=run_id,
        plan=plan,
        proposal=revision_response.accepted,
        inputs=inputs,
        source_draft_id=draft.draft_id,
    )
    if validation:
        return _persist_revision_deferred(
            run_id=run_id,
            root_path=root_path,
            store=store,
            ledger=ledger,
            report_id=report_id,
            inputs=inputs,
            plan=plan,
            draft=draft,
            reviews=first_reviews,
            raw_artifacts=[*first_raws, revision_raw],
            blockers=validation,
            applied=list(revision_response.accepted.applied_recommendations),
            backend_records=[
                _critic_record(report_id, planner, [item.raw_artifact_id for item in first_raws]),
                _synthesis_record(report_id, planner, [revision_raw.raw_artifact_id]),
            ],
        )
    second_reviews, second_raws = _run_critics(
        run_id=run_id,
        report_id=f"{report_id}-post-revision",
        raw_start=raw_number + len(first_raws) + 1,
        planner=planner,
        draft=revised,
        evidence=evidence,
    )
    remaining = _blocking_reviews(second_reviews)
    revision = ManuscriptRevisionReport(
        revision_id=f"manuscript-revision-report-{report_number:04d}",
        run_id=run_id,
        source_draft_id=draft.draft_id,
        revised_draft_id_optional=revised.draft_id if not remaining else None,
        revision_attempt=1,
        status="revised" if not remaining else "manuscript_deferred",
        applied_recommendations=list(revision_response.accepted.applied_recommendations),
        remaining_blocking_findings=remaining,
        claim_artifact_validation_passed=not validation,
    )
    backend_records = [
        _critic_record(
            report_id, planner, [item.raw_artifact_id for item in [*first_raws, *second_raws]]
        ),
        _synthesis_record(report_id, planner, [revision_raw.raw_artifact_id, revised.draft_id]),
        _claim_audit_record(report_id, [item.claim_id for item in inputs.claim_bindings]),
        _assembly_record(report_id, [item.binding_id for item in inputs.citation_bindings]),
    ]
    all_backend_records = [*synthesis.backend_records, *backend_records]
    production = _production_report(
        run_id=run_id,
        inputs=inputs,
        records=all_backend_records,
        config=config,
        expected=[
            ScientificStageKind.ADJUDICATION,
            ScientificStageKind.MANUSCRIPT_SYNTHESIS,
            ScientificStageKind.CRITIC_REVIEW,
            ScientificStageKind.CLAIM_AUDIT,
            ScientificStageKind.MANUSCRIPT_ARTIFACT_ASSEMBLY,
        ],
        report_id=report_id,
    )
    if config.require_non_fake_backends and production.blocking_violation_count:
        raise NucleusManuscriptError(_production_error("Strict manuscript revision", production))
    report = NucleusManuscriptSynthesisReport(
        run_id=run_id,
        report_id=report_id,
        phase="revision",
        manuscript_status=(
            NucleusManuscriptStatus.MANUSCRIPT_DEFERRED if remaining else _draft_status(inputs)
        ),
        source_adjudication_report_path=_relative(root_path, inputs.adjudication_path),
        paper_nucleus_selection_id_optional=inputs.adjudication.report_id,
        plan_path_optional=synthesis.plan_path_optional,
        draft_markdown_path_optional=synthesis.draft_markdown_path_optional,
        draft_latex_path_optional=synthesis.draft_latex_path_optional,
        revised_markdown_path_optional=(
            f"runs/{run_id}/reports/{revised_id}.md" if not remaining else None
        ),
        revised_latex_path_optional=(
            f"runs/{run_id}/latex/{revised_id}.tex" if not remaining else None
        ),
        claim_artifact_map_path_optional=synthesis.claim_artifact_map_path_optional,
        evidence_citation_bindings_path_optional=synthesis.evidence_citation_bindings_path_optional,
        manuscript_plan_optional=plan,
        draft_optional=draft,
        revised_draft_optional=revised if not remaining else None,
        claim_artifact_bindings=inputs.claim_bindings,
        evidence_citation_bindings=inputs.citation_bindings,
        critic_reviews=[*first_reviews, *second_reviews],
        revision_report_optional=revision,
        blocking_reasons=remaining,
        unresolved_obligations=_unresolved_obligations(inputs),
        backend_records=all_backend_records,
        raw_artifact_paths=[
            *[f"runs/{run_id}/reports/{item.raw_artifact_id}.json" for item in first_raws],
            f"runs/{run_id}/reports/{revision_raw.raw_artifact_id}.json",
            *[f"runs/{run_id}/reports/{item.raw_artifact_id}.json" for item in second_raws],
        ],
        production_ready=(
            config.require_non_fake_backends and not production.blocking_violation_count
        ),
    )
    return _persist_report(
        report=report,
        raw_artifacts=[*first_raws, revision_raw, *second_raws],
        revised_draft=revised if not remaining else None,
        revision=revision,
        store=store,
        ledger=ledger,
        action=ControllerActionType.NUCLEUS_MANUSCRIPT_REVISED,
    )


def inspect_nucleus_manuscript(
    *, run_id: str, root: str | Path = "."
) -> NucleusManuscriptInspectionReport:
    """Read the latest M105 synthesis state without mutating the run."""
    reports = Path(root) / "runs" / run_id / "reports"
    path = _latest_matching(reports, _REPORT_RE)
    if path is None:
        return NucleusManuscriptInspectionReport(run_id=run_id, nucleus_manuscript_present=False)
    report = _load_model(path, NucleusManuscriptSynthesisReport)
    plan = report.manuscript_plan_optional
    return NucleusManuscriptInspectionReport(
        run_id=run_id,
        nucleus_manuscript_present=True,
        latest_report_id_optional=report.report_id,
        manuscript_status_optional=report.manuscript_status,
        paper_nucleus_present=report.paper_nucleus_selection_id_optional is not None,
        manuscript_plan_present=plan is not None,
        draft_present=report.draft_optional is not None,
        revised_draft_present=report.revised_draft_optional is not None,
        paper_type_optional=plan.paper_type if plan else None,
        primary_package_id_optional=plan.primary_package_id if plan else None,
        claim_artifact_binding_count=len(report.claim_artifact_bindings),
        evidence_citation_binding_count=len(report.evidence_citation_bindings),
        blocking_manuscript_findings=report.blocking_reasons,
        unresolved_obligations=report.unresolved_obligations,
        critic_reviews=report.critic_reviews,
        backend_records=report.backend_records,
        warnings=report.blocking_reasons,
        production_ready=report.production_ready,
    )


def render_nucleus_manuscript_text(report: NucleusManuscriptInspectionReport) -> str:
    return "\n".join(
        [
            "Nucleus manuscript: " + ("present" if report.nucleus_manuscript_present else "absent"),
            "Status: "
            + (
                report.manuscript_status_optional.value
                if report.manuscript_status_optional
                else "not available"
            ),
            f"Nucleus/plan/draft/revised: {str(report.paper_nucleus_present).lower()}/"
            f"{str(report.manuscript_plan_present).lower()}/{str(report.draft_present).lower()}/"
            f"{str(report.revised_draft_present).lower()}",
            f"Claim/citation bindings: {report.claim_artifact_binding_count}/"
            f"{report.evidence_citation_binding_count}",
            f"Blocking findings: {len(report.blocking_manuscript_findings)}",
            f"Production ready: {str(report.production_ready).lower()}",
            "publication_ready=false",
        ]
    )


def render_nucleus_manuscript_markdown(report: NucleusManuscriptSynthesisReport) -> str:
    plan = report.manuscript_plan_optional
    lines = [
        "# Nucleus-Centered Manuscript Synthesis",
        "",
        f"- Phase: `{report.phase}`",
        f"- Status: `{report.manuscript_status.value}`",
        f"- Plan: `{plan.plan_id if plan else 'not available'}`",
        f"- Claim bindings: `{len(report.claim_artifact_bindings)}`",
        f"- Citation bindings: `{len(report.evidence_citation_bindings)}`",
        "",
        "The manuscript presentation is bounded by the persisted artifact bindings. It does not "
        "create proof, novelty, real-world validation, scientific validation, or publication "
        "readiness.",
        "",
        "publication_ready=false",
    ]
    if report.blocking_reasons:
        lines.extend(
            ["", "## Blocking Reasons", *[f"- {item}" for item in report.blocking_reasons]]
        )
    return "\n".join(lines)


@dataclass(frozen=True)
class _Inputs:
    adjudication_path: Path
    adjudication: CrossPackageAdjudicationReport
    nucleus: PaperNucleusSelection | None
    packages: HybridEvidencePackageReport | None
    execution: EvidencePackageExecutionReport | None
    primary_package: HybridEvidencePackageCandidate | None
    primary_results: list[EvidencePackageExecutionResult]
    claim_bindings: list[ClaimArtifactBinding]
    citation_bindings: list[EvidenceCitationBinding]
    retrieval_contexts: list[RetrievalContext]


def _load_inputs(root: Path, run_id: str) -> _Inputs:
    reports = root / "runs" / run_id / "reports"
    adjudication_path = _latest_matching(reports, _ADJUDICATION_RE)
    if adjudication_path is None:
        raise NucleusManuscriptError(
            "No cross-package adjudication report found; run adjudicate-evidence-packages first."
        )
    adjudication = _load_model(adjudication_path, CrossPackageAdjudicationReport)
    nucleus = adjudication.paper_nucleus_selection_optional
    package_path = _latest_matching(reports, _PACKAGE_RE)
    execution_path = _latest_matching(reports, _EXECUTION_RE)
    packages = _load_model(package_path, HybridEvidencePackageReport) if package_path else None
    execution = (
        _load_model(execution_path, EvidencePackageExecutionReport) if execution_path else None
    )
    primary = None
    results: list[EvidencePackageExecutionResult] = []
    if nucleus and packages:
        primary = next(
            (item for item in packages.packages if item.package_id == nucleus.primary_package_id),
            None,
        )
    if nucleus and execution:
        results = [
            item for item in execution.results if item.package_id == nucleus.primary_package_id
        ]
    retrieval = _load_retrieval_contexts(reports)
    claims, citations = _build_bindings(
        nucleus=nucleus, primary=primary, results=results, retrieval=retrieval
    )
    return _Inputs(
        adjudication_path=adjudication_path,
        adjudication=adjudication,
        nucleus=nucleus,
        packages=packages,
        execution=execution,
        primary_package=primary,
        primary_results=results,
        claim_bindings=claims,
        citation_bindings=citations,
        retrieval_contexts=retrieval,
    )


def _prerequisite_blockers(
    inputs: _Inputs,
    *,
    require_literature_context: bool = False,
) -> list[str]:
    blockers: list[str] = []
    if inputs.nucleus is None:
        blockers.append("No primary paper nucleus was selected by cross-package adjudication.")
        return blockers
    if inputs.primary_package is None:
        blockers.append("The primary package referenced by the nucleus is missing.")
    if inputs.execution is None:
        blockers.append("No hybrid evidence execution report is available for the primary package.")
    primary_decision = next(
        (
            item
            for item in inputs.adjudication.decisions
            if item.package_id == inputs.nucleus.primary_package_id
        ),
        None,
    )
    if primary_decision is None:
        blockers.append("No package adjudication decision exists for the selected primary nucleus.")
    elif primary_decision.blocking_findings:
        blockers.append("Primary nucleus has unresolved blocking adjudication findings.")
    for review in inputs.adjudication.reviews:
        if review.package_id == inputs.nucleus.primary_package_id and any(
            finding.blocking for finding in review.findings
        ):
            blockers.append("Primary nucleus has unresolved blocking scientific critic findings.")
            break
    if inputs.primary_package is not None:
        completed_plan_ids = {
            item.artifact_plan_id
            for item in inputs.primary_results
            if item.status in _MAIN_RESULT_STATUSES
        }
        required_plan_ids = _required_artifact_plan_ids(inputs.primary_package)
        missing_plan_ids = [
            plan_id for plan_id in required_plan_ids if plan_id not in completed_plan_ids
        ]
        if missing_plan_ids:
            blockers.append(
                "Primary package is missing required evidence artifact plans: "
                + ", ".join(missing_plan_ids)
            )
        if not inputs.claim_bindings:
            blockers.append(
                "Claim-artifact bindings could not be constructed for the primary nucleus."
            )
        if _requires_retrieval(inputs.primary_package) and not any(
            context.retrieval_mode == "real_retrieval" and context.sources
            for context in inputs.retrieval_contexts
        ):
            blockers.append(
                "Primary package requires retrieval citations but no real retrieval context is "
                "available."
            )
        if any(
            metric
            for item in inputs.primary_results
            if item.metrics
            for metric, source in item.metric_sources.items()
            if not source
        ):
            blockers.append(
                "Primary package contains metrics without persisted execution metric sources."
            )
    if require_literature_context and not any(
        context.retrieval_mode == "real_retrieval"
        and any(not source.fake_or_mocked for source in context.sources)
        for context in inputs.retrieval_contexts
    ):
        blockers.append(
            "A strict manuscript requires accepted real-retrieval sources for a bounded "
            "literature section and bibliography."
        )
    return _unique(blockers)


def _required_artifact_plan_ids(package: HybridEvidencePackageCandidate) -> list[str]:
    """Resolve free-form minimum labels to the package's executable plan IDs."""
    declared = {_artifact_token(item) for item in package.minimum_required_artifacts}
    always_required_types = {
        EvidenceArtifactType.NEGATIVE_CONTROL,
        EvidenceArtifactType.ROBUSTNESS_SWEEP,
    }
    matched = [
        plan.artifact_plan_id
        for plan in package.artifact_plans
        if _artifact_token(plan.artifact_type.value) in declared
        or _artifact_token(plan.artifact_plan_id) in declared
        or plan.artifact_type in always_required_types
    ]
    return matched or [plan.artifact_plan_id for plan in package.artifact_plans]


def _artifact_token(value: str) -> str:
    return "_".join(_ARTIFACT_TOKEN_RE.findall(value.casefold()))


def _build_bindings(
    *,
    nucleus: PaperNucleusSelection | None,
    primary: HybridEvidencePackageCandidate | None,
    results: list[EvidencePackageExecutionResult],
    retrieval: list[RetrievalContext],
) -> tuple[list[ClaimArtifactBinding], list[EvidenceCitationBinding]]:
    if nucleus is None or primary is None:
        return [], []
    valid = [item for item in results if item.status in _MAIN_RESULT_STATUSES]
    artifact_ids = [item.result_id for item in valid]
    labels = [item.evidence_label for item in valid]
    claim_id = f"claim-primary-{_slug(primary.package_id)}"
    unsupported = [
        obligation
        for item in results
        if item.status not in _MAIN_RESULT_STATUSES
        for obligation in [item.failure_reason_optional or item.status]
    ]
    binding = ClaimArtifactBinding(
        claim_id=claim_id,
        claim_text=nucleus.central_claim_draft,
        claim_scope=nucleus.allowed_claim_scope,
        supporting_artifact_ids=artifact_ids,
        supporting_evidence_labels=labels,
        unsupported_components=_unique(unsupported),
        allowed_in_main_text=bool(artifact_ids),
        requires_qualification=True,
    )
    citations: list[EvidenceCitationBinding] = []
    citation_index = 0
    for result in valid:
        sources = _unique(
            source.split("#", 1)[0]
            for source in result.metric_sources.values()
            if source
        ) or [result.result_id]
        for source in sources:
            citation_index += 1
            citations.append(
                EvidenceCitationBinding(
                    binding_id=(
                        f"evidence-citation-{_slug(primary.package_id)}-"
                        f"{citation_index:03d}"
                    ),
                    manuscript_location="results_or_appendix",
                    artifact_id=result.result_id,
                    source_type="execution_artifact",
                    evidence_label=result.evidence_label,
                    citation_or_reference_id=source,
                    supports_claim_ids=[claim_id],
                )
            )
    seen_retrieval_sources: set[str] = set()
    retrieval_supports_claim = _requires_retrieval(primary)
    for context in retrieval:
        if context.retrieval_mode != "real_retrieval":
            continue
        for source in context.sources:
            if source.fake_or_mocked:
                continue
            source_identity = (source.doi or source.source_id).casefold()
            if source_identity in seen_retrieval_sources:
                continue
            seen_retrieval_sources.add(source_identity)
            citation_index += 1
            citations.append(
                EvidenceCitationBinding(
                    binding_id=(
                        f"retrieval-citation-{_slug(primary.package_id)}-"
                        f"{citation_index:03d}"
                    ),
                    manuscript_location="related_work_or_limitations",
                    artifact_id=context.context_id,
                    source_type="retrieval_source",
                    evidence_label=(
                        "RetrievalNoveltyAssessment"
                        if retrieval_supports_claim
                        else "BoundedLiteratureContext"
                    ),
                    citation_or_reference_id=source.doi or source.source_id,
                    supports_claim_ids=[claim_id] if retrieval_supports_claim else [],
                )
            )
    return [binding], citations


def _build_evidence_context(inputs: _Inputs) -> dict[str, Any]:
    return {
        "claim_artifact_bindings": [item.model_dump(mode="json") for item in inputs.claim_bindings],
        "evidence_citation_bindings": [
            item.model_dump(mode="json") for item in inputs.citation_bindings
        ],
        "retrieved_literature_context": [
            {
                "context_id": context.context_id,
                "query": context.query,
                "retrieval_confidence": context.retrieval_confidence,
                "limitations": context.limitations,
                "sources": [
                    source.model_dump(mode="json")
                    for source in context.sources
                    if not source.fake_or_mocked
                ],
            }
            for context in inputs.retrieval_contexts
            if context.retrieval_mode == "real_retrieval"
        ],
        "primary_execution_results": [
            item.model_dump(mode="json") for item in inputs.primary_results
        ],
        "deterministic_metric_table": _metric_rows(inputs.primary_results),
        "allowed_claim_scope": inputs.nucleus.allowed_claim_scope if inputs.nucleus else "none",
        "forbidden_claims": inputs.nucleus.forbidden_claims
        if inputs.nucleus
        else _MANDATORY_FORBIDDEN,
        "unresolved_obligations": _unresolved_obligations(inputs),
    }


def _materialize_plan(
    *,
    plan_id: str,
    run_id: str,
    nucleus: PaperNucleusSelection,
    selection_id: str,
    proposal: Any,
    bindings: list[ClaimArtifactBinding],
    citations: list[EvidenceCitationBinding],
) -> NucleusManuscriptPlan:
    allowed_claim_ids = {item.claim_id for item in bindings}
    allowed_package_ids = {
        nucleus.primary_package_id,
        *nucleus.supporting_package_ids,
        *nucleus.appendix_package_ids,
        *nucleus.negative_package_ids,
    }
    sections = [
        ManuscriptSectionPlan(
            section_id=item.section_id,
            title=item.title,
            bullets=item.bullets,
            purpose=item.purpose,
            claim_ids=item.claim_ids,
            artifact_ids=item.artifact_ids,
            supporting_package_ids=[
                value
                for value in item.supporting_package_ids
                if value in allowed_package_ids
            ],
            required_citations=_normalize_citation_references(
                item.required_citations, citations
            ),
            scope_constraints=item.scope_constraints,
            allowed_claim_ids=[value for value in item.claim_ids if value in allowed_claim_ids],
        )
        for item in proposal.section_plans
    ]
    retrieval_citations = _retrieval_citation_ids(citations)
    if retrieval_citations:
        literature_sections = [
            section
            for section in sections
            if any(
                marker in f"{section.title} {section.purpose}".casefold()
                for marker in ("related work", "literature", "prior work")
            )
        ]
        if literature_sections:
            literature_section = literature_sections[0]
            sections[sections.index(literature_section)] = literature_section.model_copy(
                update={
                    "required_citations": _unique(
                        [*literature_section.required_citations, *sorted(retrieval_citations)]
                    )
                }
            )
        else:
            sections.insert(
                min(1, len(sections)),
                ManuscriptSectionPlan(
                    section_id="section-related-work",
                    title="Related Work and Literature Boundaries",
                    bullets=[
                        "Position the bounded question using only accepted retrieval metadata "
                        "and abstracts."
                    ],
                    purpose="Summarize related literature and delimit unsupported novelty claims.",
                    required_citations=sorted(retrieval_citations),
                    scope_constraints=[
                        "Do not claim exhaustive coverage, novelty, underuse, or scientific "
                        "validation from retrieval context."
                    ],
                ),
            )
    return NucleusManuscriptPlan(
        plan_id=plan_id,
        run_id=run_id,
        paper_nucleus_selection_id=selection_id,
        primary_package_id=nucleus.primary_package_id,
        working_title=proposal.working_title,
        paper_type=proposal.paper_type,
        central_question=proposal.central_question,
        central_claim=proposal.central_claim,
        allowed_claim_scope=nucleus.allowed_claim_scope,
        forbidden_claims=_merge_forbidden(nucleus.forbidden_claims),
        section_plans=sections,
        supporting_package_roles={
            package_id: role
            for package_id, role in proposal.supporting_package_roles.items()
            if package_id in nucleus.supporting_package_ids
        },
        appendix_package_roles={
            package_id: role
            for package_id, role in proposal.appendix_package_roles.items()
            if package_id in nucleus.appendix_package_ids
        },
        negative_result_roles={
            package_id: role
            for package_id, role in proposal.negative_result_roles.items()
            if package_id in nucleus.negative_package_ids
        },
        required_repairs=nucleus.required_repairs_before_manuscript,
        unresolved_obligations=nucleus.required_additional_checks,
    )


def _validate_plan(plan: NucleusManuscriptPlan, inputs: _Inputs) -> list[str]:
    blockers = _forbidden_reasons(
        {
            "working_title": plan.working_title,
            "central_question": plan.central_question,
            "central_claim": plan.central_claim,
            "sections": [
                {
                    "title": item.title,
                    "purpose": item.purpose,
                    "bullets": item.bullets,
                    "scope_constraints": item.scope_constraints,
                }
                for item in plan.section_plans
            ],
        }
    )
    known_claims = {item.claim_id for item in inputs.claim_bindings}
    known_citations = {item.binding_id for item in inputs.citation_bindings}
    known_packages = set(
        ([inputs.nucleus.primary_package_id] if inputs.nucleus else [])
        + (inputs.nucleus.supporting_package_ids if inputs.nucleus else [])
        + (inputs.nucleus.appendix_package_ids if inputs.nucleus else [])
        + (inputs.nucleus.negative_package_ids if inputs.nucleus else [])
    )
    if not plan.section_plans:
        blockers.append("Manuscript plan lacks section plans.")
    for section in plan.section_plans:
        if not section.purpose.strip() or not section.scope_constraints:
            blockers.append(f"Section {section.section_id} lacks purpose or scope constraints.")
        if set(section.claim_ids) - known_claims:
            blockers.append(f"Section {section.section_id} references unknown claim IDs.")
        if set(section.required_citations) - known_citations:
            blockers.append(f"Section {section.section_id} references unknown citation bindings.")
        if set(section.supporting_package_ids) - known_packages:
            blockers.append(
                f"Section {section.section_id} references package IDs outside adjudication roles."
            )
    corpus = " ".join(f"{item.title} {item.purpose}" for item in plan.section_plans).casefold()
    required_roles = {
        "question or introduction": ("question", "introduction", "motivation", "problem", "scope"),
        "methods or design": ("method", "design", "data-generating", "estimand", "procedure"),
        "results": ("result", "performance", "finding", "contrast", "outcome"),
        "interpretation or limitations": (
            "interpretation",
            "limitation",
            "conclusion",
            "discussion",
        ),
    }
    for role, markers in required_roles.items():
        if not any(marker in corpus for marker in markers):
            blockers.append(f"Manuscript plan lacks a {role}-oriented section.")
    if inputs.nucleus and (
        inputs.nucleus.appendix_package_ids or inputs.nucleus.negative_package_ids
    ) and "append" not in corpus:
        blockers.append("Manuscript plan lacks an appendix-oriented section.")
    retrieval_citations = _retrieval_citation_ids(inputs.citation_bindings)
    if retrieval_citations:
        literature_sections = [
            section
            for section in plan.section_plans
            if any(
                marker in f"{section.title} {section.purpose}".casefold()
                for marker in ("related work", "literature", "prior work")
            )
        ]
        if not literature_sections:
            blockers.append("Manuscript plan lacks a source-grounded literature section.")
        elif not retrieval_citations.issubset(
            {
                citation
                for section in literature_sections
                for citation in section.required_citations
            }
        ):
            blockers.append(
                "The literature section does not bind every accepted retrieval source."
            )
    return _unique(blockers)


def _normalize_citation_references(
    values: list[str], citations: list[EvidenceCitationBinding]
) -> list[str]:
    aliases: dict[str, str] = {}
    source_prefixes: dict[str, str] = {}
    for citation in citations:
        aliases[citation.binding_id] = citation.binding_id
        aliases[citation.artifact_id] = citation.binding_id
        aliases[citation.citation_or_reference_id] = citation.binding_id
        source_prefixes[citation.citation_or_reference_id.split("#", 1)[0]] = (
            citation.binding_id
        )
    normalized: list[str] = []
    for value in values:
        resolved = aliases.get(value) or source_prefixes.get(value.split("#", 1)[0])
        if resolved is None:
            for alias in sorted(aliases, key=len, reverse=True):
                suffix = value.removeprefix(alias)
                if suffix != value and suffix.lstrip().startswith((";", ":", "-", "(")):
                    resolved = aliases[alias]
                    break
        candidate = resolved or value
        if candidate not in normalized:
            normalized.append(candidate)
    return normalized


def _materialize_draft(
    *,
    draft_id: str,
    run_id: str,
    plan: NucleusManuscriptPlan,
    proposal: Any,
    inputs: _Inputs,
    source_draft_id: str | None = None,
) -> tuple[NucleusManuscriptDraft, list[str]]:
    known_claims = {item.claim_id for item in inputs.claim_bindings}
    known_citations = {item.binding_id for item in inputs.citation_bindings}
    if source_draft_id is None:
        markdown = _append_metric_table(proposal.markdown, inputs.primary_results)
        latex = _append_latex_metric_table(proposal.latex, inputs.primary_results)
    else:
        markdown = proposal.markdown
        latex = proposal.latex
    retrieval_citations = _retrieval_citation_ids(inputs.citation_bindings)
    markdown, latex = _ensure_literature_content(
        markdown=markdown,
        latex=latex,
        inputs=inputs,
    )
    draft = NucleusManuscriptDraft(
        draft_id=draft_id,
        run_id=run_id,
        plan_id=plan.plan_id,
        title=proposal.title,
        abstract=proposal.abstract,
        markdown=markdown,
        latex=latex,
        claim_ids_used=proposal.claim_ids_used,
        citation_binding_ids=_unique(
            [*proposal.citation_binding_ids, *sorted(retrieval_citations)]
        ),
        source_draft_id_optional=source_draft_id,
    )
    blockers = _forbidden_reasons(
        {
            "title": draft.title,
            "abstract": draft.abstract,
            "markdown": draft.markdown,
            "latex": draft.latex,
        }
    )
    if not set(draft.claim_ids_used).issubset(known_claims):
        blockers.append("Draft references unknown claim IDs.")
    if not set(draft.citation_binding_ids).issubset(known_citations):
        blockers.append("Draft references unknown evidence citation binding IDs.")
    if not draft.claim_ids_used:
        blockers.append("Draft does not identify any artifact-bound substantive claims.")
    retrieval_citations = _retrieval_citation_ids(inputs.citation_bindings)
    if retrieval_citations:
        if not retrieval_citations.issubset(set(draft.citation_binding_ids)):
            blockers.append("Draft omits accepted retrieval-source citation bindings.")
        if not any(
            marker in draft.markdown.casefold()
            for marker in ("related work", "literature", "prior work")
        ):
            blockers.append("Draft lacks source-grounded literature content.")
    blockers.extend(
        _metric_literal_reasons(proposal.markdown, proposal.latex, inputs.primary_results)
    )
    requires_appendix = bool(
        inputs.nucleus
        and (
            inputs.nucleus.appendix_package_ids
            or inputs.nucleus.negative_package_ids
        )
    )
    blockers.extend(
        _required_draft_content_reasons(
            draft.markdown,
            require_appendix=requires_appendix,
        )
    )
    if inputs.nucleus and inputs.nucleus.rejected_package_ids:
        # Rejected packages cannot be cited as claim support even if their identifiers appear.
        rejected = set(inputs.nucleus.rejected_package_ids)
        if any(package_id in draft.markdown for package_id in rejected):
            blockers.append(
                "Draft presents a rejected package in manuscript body; retain it only in audit "
                "provenance."
            )
    return draft, _unique(blockers)


def _retrieval_citation_ids(
    citations: Iterable[EvidenceCitationBinding],
) -> set[str]:
    return {
        item.binding_id for item in citations if item.source_type == "retrieval_source"
    }


def _ensure_literature_content(
    *,
    markdown: str,
    latex: str,
    inputs: _Inputs,
) -> tuple[str, str]:
    citations = [
        item for item in inputs.citation_bindings if item.source_type == "retrieval_source"
    ]
    if not citations or any(
        marker in markdown.casefold()
        for marker in ("related work", "literature", "prior work")
    ):
        return markdown, latex
    markdown_lines = [
        markdown.rstrip(),
        "",
        "## Related Work and Literature Boundaries",
        "",
        (
            "The following accepted retrieval records provide bounded background context only; "
            "they do not establish novelty, completeness, or scientific validation."
        ),
        "",
    ]
    latex_lines = [
        latex.rstrip(),
        "",
        r"\section{Related Work and Literature Boundaries}",
        (
            "The accepted retrieval records provide bounded background context only; they do "
            "not establish novelty, completeness, or scientific validation."
        ),
        r"\begin{itemize}",
    ]
    for citation in citations:
        source = _retrieval_source_for_citation(citation, inputs.retrieval_contexts)
        if source is None:
            continue
        authors = ", ".join(source.authors) if source.authors else "Unknown author"
        year = str(source.year) if source.year is not None else "n.d."
        markdown_lines.append(
            f"- {authors} ({year}), *{source.title}* "
            f"[`{citation.binding_id}`]."
        )
        latex_lines.append(
            rf"\item {_latex_escape(authors)} ({_latex_escape(year)}), "
            rf"\emph{{{_latex_escape(source.title)}}}."
        )
    latex_lines.append(r"\end{itemize}")
    return "\n".join(markdown_lines).rstrip() + "\n", "\n".join(latex_lines).rstrip() + "\n"


def _retrieval_source_for_citation(
    citation: EvidenceCitationBinding,
    contexts: Iterable[RetrievalContext],
) -> RetrievedSourceSummary | None:
    for context in contexts:
        if context.context_id != citation.artifact_id:
            continue
        for source in context.sources:
            if source.fake_or_mocked:
                continue
            if citation.citation_or_reference_id in {source.source_id, source.doi}:
                return source
    return None


def _run_critics(
    *,
    run_id: str,
    report_id: str,
    raw_start: int,
    planner: NucleusManuscriptClient,
    draft: NucleusManuscriptDraft,
    evidence: dict[str, Any],
) -> tuple[list[ManuscriptCriticReview], list[NucleusManuscriptRawArtifact]]:
    reviews: list[ManuscriptCriticReview] = []
    raws: list[NucleusManuscriptRawArtifact] = []
    for index, role in enumerate(_REVIEW_ROLES, start=1):
        response = planner.critique_manuscript(
            prompt_id=f"{report_id}-critic-{index:03d}",
            critic_role=role,
            draft_payload=draft.model_dump(mode="json"),
            evidence_payload=evidence,
        )
        raw = _raw_artifact(
            raw_id=f"nucleus-manuscript-raw-{raw_start + index - 1:04d}",
            run_id=run_id,
            operation="critic",
            client=planner,
            response=response,
            accepted_id=(
                f"manuscript-critic-{_slug(draft.draft_id)}-{role.value}"
                if response.accepted and not response.rejection_reasons
                else None
            ),
        )
        raws.append(raw)
        if response.accepted is None or response.rejection_reasons:
            raise NucleusManuscriptError(
                f"Manuscript critic {role.value} was rejected: "
                + "; ".join(response.rejection_reasons)
            )
        reviews.append(
            ManuscriptCriticReview(
                review_id=f"manuscript-critic-{_slug(draft.draft_id)}-{role.value}",
                run_id=run_id,
                draft_id=draft.draft_id,
                critic_role=role,
                findings=response.accepted.findings,
                blocking_findings=response.accepted.blocking_findings,
                recommended_revisions=response.accepted.recommended_revisions,
                score=response.accepted.score,
            )
        )
    return reviews, raws


def _persist_deferred(
    *,
    run_id: str,
    root_path: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    report_id: str,
    phase: str,
    source_adjudication_path: Path,
    blockers: list[str],
    unresolved: list[str],
    backend_records: list[StageBackendRecord],
    raw_artifacts: list[NucleusManuscriptRawArtifact] | None = None,
) -> NucleusManuscriptResult:
    report = NucleusManuscriptSynthesisReport(
        run_id=run_id,
        report_id=report_id,
        phase=phase,
        manuscript_status=NucleusManuscriptStatus.MANUSCRIPT_DEFERRED,
        source_adjudication_report_path=_relative(root_path, source_adjudication_path),
        blocking_reasons=_unique(blockers),
        unresolved_obligations=_unique(unresolved),
        backend_records=backend_records,
        raw_artifact_paths=[
            f"runs/{run_id}/reports/{item.raw_artifact_id}.json" for item in raw_artifacts or []
        ],
        production_ready=False,
    )
    return _persist_report(
        report=report,
        raw_artifacts=raw_artifacts or [],
        store=store,
        ledger=ledger,
        action=(
            ControllerActionType.NUCLEUS_MANUSCRIPT_PLANNED
            if phase == "planning"
            else ControllerActionType.NUCLEUS_MANUSCRIPT_SYNTHESIZED
        ),
    )


def _persist_revision_deferred(
    *,
    run_id: str,
    root_path: Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    report_id: str,
    inputs: _Inputs,
    plan: NucleusManuscriptPlan,
    draft: NucleusManuscriptDraft,
    reviews: list[ManuscriptCriticReview],
    raw_artifacts: list[NucleusManuscriptRawArtifact],
    blockers: list[str],
    applied: list[str],
    backend_records: list[StageBackendRecord],
) -> NucleusManuscriptResult:
    revision = ManuscriptRevisionReport(
        revision_id=f"manuscript-revision-report-{_report_number(report_id):04d}",
        run_id=run_id,
        source_draft_id=draft.draft_id,
        revision_attempt=1,
        status="manuscript_deferred",
        applied_recommendations=applied,
        remaining_blocking_findings=_unique(blockers),
        claim_artifact_validation_passed=False,
    )
    report = NucleusManuscriptSynthesisReport(
        run_id=run_id,
        report_id=report_id,
        phase="revision",
        manuscript_status=NucleusManuscriptStatus.MANUSCRIPT_DEFERRED,
        source_adjudication_report_path=_relative(root_path, inputs.adjudication_path),
        paper_nucleus_selection_id_optional=inputs.adjudication.report_id,
        manuscript_plan_optional=plan,
        draft_optional=draft,
        claim_artifact_bindings=inputs.claim_bindings,
        evidence_citation_bindings=inputs.citation_bindings,
        critic_reviews=reviews,
        revision_report_optional=revision,
        blocking_reasons=_unique(blockers),
        unresolved_obligations=_unresolved_obligations(inputs),
        backend_records=backend_records,
        raw_artifact_paths=[
            f"runs/{run_id}/reports/{item.raw_artifact_id}.json" for item in raw_artifacts
        ],
        production_ready=False,
    )
    return _persist_report(
        report=report,
        raw_artifacts=raw_artifacts,
        revision=revision,
        store=store,
        ledger=ledger,
        action=ControllerActionType.NUCLEUS_MANUSCRIPT_REVISED,
    )


def _persist_report(
    *,
    report: NucleusManuscriptSynthesisReport,
    raw_artifacts: list[NucleusManuscriptRawArtifact],
    store: ArtifactStore,
    ledger: ResearchLedger,
    action: ControllerActionType,
    draft: NucleusManuscriptDraft | None = None,
    revised_draft: NucleusManuscriptDraft | None = None,
    revision: ManuscriptRevisionReport | None = None,
) -> NucleusManuscriptResult:
    report_number = _report_number(report.report_id)
    specs: list[ArtifactWriteSpec] = [
        ArtifactWriteSpec(
            artifact_id=report.report_id,
            artifact_type=ArtifactType.REPORT,
            payload=report.model_dump(mode="json"),
            artifact_format="json",
            metadata=_metadata("nucleus_manuscript_synthesis"),
        ),
        ArtifactWriteSpec(
            artifact_id=f"{report.report_id}-markdown",
            artifact_type=ArtifactType.REPORT,
            payload=render_nucleus_manuscript_markdown(report),
            artifact_format="markdown",
            metadata=_metadata("nucleus_manuscript_synthesis"),
        ),
    ]
    if report.manuscript_plan_optional is not None:
        specs.extend(
            [
                ArtifactWriteSpec(
                    artifact_id=report.manuscript_plan_optional.plan_id,
                    artifact_type=ArtifactType.REPORT,
                    payload=report.manuscript_plan_optional.model_dump(mode="json"),
                    artifact_format="json",
                    metadata=_metadata("nucleus_manuscript_plan"),
                ),
                ArtifactWriteSpec(
                    artifact_id=f"claim-artifact-map-{report_number:04d}",
                    artifact_type=ArtifactType.REPORT,
                    payload=[
                        item.model_dump(mode="json") for item in report.claim_artifact_bindings
                    ],
                    artifact_format="json",
                    metadata=_metadata("claim_artifact_map"),
                ),
                ArtifactWriteSpec(
                    artifact_id=f"evidence-citation-bindings-{report_number:04d}",
                    artifact_type=ArtifactType.REPORT,
                    payload=[
                        item.model_dump(mode="json") for item in report.evidence_citation_bindings
                    ],
                    artifact_format="json",
                    metadata=_metadata("evidence_citation_bindings"),
                ),
            ]
        )
    if draft is not None:
        specs.extend(_draft_specs(draft))
    if revised_draft is not None:
        specs.extend(_draft_specs(revised_draft))
    if revision is not None:
        specs.append(
            ArtifactWriteSpec(
                artifact_id=revision.revision_id,
                artifact_type=ArtifactType.REPORT,
                payload=revision.model_dump(mode="json"),
                artifact_format="json",
                metadata=_metadata("nucleus_manuscript_revision"),
            )
        )
    specs.extend(
        ArtifactWriteSpec(
            artifact_id=item.raw_artifact_id,
            artifact_type=ArtifactType.REPORT,
            payload=item.model_dump(mode="json"),
            artifact_format="json",
            metadata=_metadata("nucleus_manuscript_raw"),
        )
        for item in raw_artifacts
    )
    persistence = persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=specs,
        action_type=action,
        commit_payload={
            "report_id": report.report_id,
            "phase": report.phase,
            "manuscript_status": report.manuscript_status.value,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {item.id: item for item in persistence.artifacts}
    return NucleusManuscriptResult(
        run_id=report.run_id,
        report=report,
        persistence=persistence,
        report_artifact=by_id[report.report_id],
        markdown_artifact=by_id[f"{report.report_id}-markdown"],
    )


def _draft_specs(draft: NucleusManuscriptDraft) -> list[ArtifactWriteSpec]:
    return [
        ArtifactWriteSpec(
            artifact_id=draft.draft_id,
            artifact_type=ArtifactType.REPORT,
            payload=draft.markdown,
            artifact_format="markdown",
            metadata=_metadata("nucleus_manuscript_draft"),
        ),
        ArtifactWriteSpec(
            artifact_id=f"{draft.draft_id}-latex",
            artifact_type=ArtifactType.LATEX,
            payload=draft.latex,
            artifact_format="latex",
            extension="tex",
            metadata=_metadata("nucleus_manuscript_draft"),
            filename_stem=draft.draft_id,
        ),
    ]


def _raw_artifact(
    *,
    raw_id: str,
    run_id: str,
    operation: str,
    client: NucleusManuscriptClient,
    response: Any,
    accepted_id: str | None,
) -> NucleusManuscriptRawArtifact:
    return NucleusManuscriptRawArtifact(
        raw_artifact_id=raw_id,
        run_id=run_id,
        operation=operation,
        backend_name=client.backend_name,
        model=client.model,
        prompt_text=response.prompt_text,
        requested_output_schema=response.requested_output_schema,
        raw_response=response.raw_response,
        accepted_id_optional=accepted_id,
        rejection_reasons=list(response.rejection_reasons),
        fallback_used=client.fallback_used,
    )


def _validate_client(client: NucleusManuscriptClient) -> None:
    if client.backend_kind not in {BackendKind.LLM_OPENAI, BackendKind.LLM_OTHER}:
        raise NucleusManuscriptError(
            "Nucleus manuscript operations require a non-fake LLM backend."
        )
    if client.fallback_used:
        raise NucleusManuscriptError(
            "Nucleus manuscript operations forbid deterministic prose fallback."
        )


def _validate_config(run_id: str, config: NucleusManuscriptConfig) -> None:
    if config.run_id != run_id:
        raise NucleusManuscriptError("Nucleus manuscript config run_id does not match run_id.")


def _production_report(
    *,
    run_id: str,
    inputs: _Inputs,
    records: list[StageBackendRecord],
    config: NucleusManuscriptConfig,
    expected: list[ScientificStageKind],
    report_id: str,
) -> Any:
    return evaluate_production_mode(
        run_id=run_id,
        records=[*inputs.adjudication.backend_records, *records],
        policy=ProductionModePolicy(
            require_non_fake_backends=config.require_non_fake_backends,
            fail_on_silent_fallback=config.fail_on_silent_fallback,
        ),
        expected_stage_kinds=expected,
        report_id=f"{report_id}-production-evaluation",
    )


def _planning_record(
    report_id: str, client: NucleusManuscriptClient, artifact_ids: list[str]
) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-planning",
        stage_kind=ScientificStageKind.MANUSCRIPT_PLANNING,
        backend_kind=client.backend_kind,
        backend_name=client.backend_name,
        is_scientific_generation=True,
        is_scientific_judgment=False,
        is_execution_or_verification=False,
        allowed_in_production=True,
        reason="Non-fake LLM organizes the bounded scientific narrative without creating evidence.",
        artifact_ids=artifact_ids,
        fallback_used=client.fallback_used,
        fallback_disclosed=client.fallback_disclosed,
    )


def _synthesis_record(
    report_id: str, client: NucleusManuscriptClient, artifact_ids: list[str]
) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-synthesis",
        stage_kind=ScientificStageKind.MANUSCRIPT_SYNTHESIS,
        backend_kind=client.backend_kind,
        backend_name=client.backend_name,
        is_scientific_generation=True,
        is_scientific_judgment=False,
        is_execution_or_verification=False,
        allowed_in_production=True,
        reason="Non-fake LLM drafts bounded prose from validated artifact bindings.",
        artifact_ids=artifact_ids,
        fallback_used=client.fallback_used,
        fallback_disclosed=client.fallback_disclosed,
    )


def _critic_record(
    report_id: str, client: NucleusManuscriptClient, artifact_ids: list[str]
) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-critic",
        stage_kind=ScientificStageKind.CRITIC_REVIEW,
        backend_kind=client.backend_kind,
        backend_name=client.backend_name,
        is_scientific_generation=False,
        is_scientific_judgment=True,
        is_execution_or_verification=False,
        allowed_in_production=True,
        reason=(
            "Non-fake LLM critic checks narrative coherence and scope against persisted bindings."
        ),
        artifact_ids=artifact_ids,
        fallback_used=client.fallback_used,
        fallback_disclosed=client.fallback_disclosed,
    )


def _claim_audit_record(report_id: str, artifact_ids: list[str]) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-claim-audit",
        stage_kind=ScientificStageKind.CLAIM_AUDIT,
        backend_kind=BackendKind.LOCAL_EXECUTION,
        backend_name="nucleus_claim_artifact_validator",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=True,
        allowed_in_production=True,
        reason=(
            "Local validation checks claim IDs, metric sources, citations, and forbidden claim "
            "boundaries."
        ),
        artifact_ids=artifact_ids,
    )


def _assembly_record(report_id: str, artifact_ids: list[str]) -> StageBackendRecord:
    return stage_backend_record(
        stage_id=f"{report_id}-artifact-assembly",
        stage_kind=ScientificStageKind.MANUSCRIPT_ARTIFACT_ASSEMBLY,
        backend_kind=BackendKind.LOCAL_EXECUTION,
        backend_name="nucleus_metric_table_assembler",
        is_scientific_generation=False,
        is_scientific_judgment=False,
        is_execution_or_verification=True,
        allowed_in_production=True,
        reason=(
            "Local assembly copies metric values and bindings from persisted execution artifacts."
        ),
        artifact_ids=artifact_ids,
    )


def _draft_status(inputs: _Inputs) -> NucleusManuscriptStatus:
    return (
        NucleusManuscriptStatus.SCIENTIFIC_DRAFT_WITH_OPEN_OBLIGATIONS
        if _unresolved_obligations(inputs)
        else NucleusManuscriptStatus.BOUNDED_DRAFT
    )


def _unresolved_obligations(inputs: _Inputs) -> list[str]:
    values = [
        *(inputs.nucleus.required_additional_checks if inputs.nucleus else []),
        *(inputs.primary_package.unresolved_obligations if inputs.primary_package else []),
        *(
            obligation
            for result in inputs.primary_results
            for obligation in result.unresolved_obligations
        ),
    ]
    return _unique(values)


def _requires_retrieval(package: HybridEvidencePackageCandidate) -> bool:
    return any(
        item.artifact_type == EvidenceArtifactType.LITERATURE_NOVELTY_CHECK
        for item in package.artifact_plans
    )


def _metric_rows(results: Iterable[EvidencePackageExecutionResult]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        for metric, value in result.metrics.items():
            rows.append(
                {
                    "artifact_id": result.result_id,
                    "metric": metric,
                    "value": value,
                    "source": result.metric_sources.get(metric, ""),
                }
            )
    return rows


def _append_metric_table(markdown: str, results: list[EvidencePackageExecutionResult]) -> str:
    rows = _metric_rows(results)
    if not rows:
        return markdown
    lines = [
        markdown.rstrip(),
        "",
        "## Artifact-Bound Metrics",
        "",
        "| Artifact | Metric | Value |",
        "|---|---|---:|",
    ]
    lines.extend(f"| {row['artifact_id']} | {row['metric']} | {row['value']} |" for row in rows)
    return "\n".join(lines) + "\n"


def _append_latex_metric_table(latex: str, results: list[EvidencePackageExecutionResult]) -> str:
    rows = _metric_rows(results)
    if not rows:
        return latex
    lines = [
        latex.rstrip(),
        "",
        r"\section*{Artifact-Bound Metrics}",
        r"\begin{tabular}{lll}",
        "Artifact & Metric & Value " + r"\\",
        r"\hline",
    ]
    lines.extend(
        f"{_latex_escape(str(row['artifact_id']))} & {_latex_escape(str(row['metric']))} "
        f"& {row['value']} " + r"\\"
        for row in rows
    )
    lines.append(r"\end{tabular}")
    return "\n".join(lines) + "\n"


def _metric_literal_reasons(
    markdown: str, latex: str, results: list[EvidencePackageExecutionResult]
) -> list[str]:
    allowed = {
        Decimal(str(value)) for result in results for value in result.metrics.values()
    }
    for result in results:
        for summary in (
            result.baseline_summary,
            result.control_summary,
            result.negative_control_summary,
        ):
            allowed.update(Decimal(value) for value in _DECIMAL_LITERAL_RE.findall(summary))
    markdown_without_heading_ordinals = re.sub(
        r"(?m)^(#{1,6}\s+)\d+(?:\.\d+)+(?=\s)", r"\1", markdown
    )
    latex_without_layout_dimensions = re.sub(
        r"(?<![\w.])[+-]?(?:\d+(?:\.\d*)?|\.\d+)"
        r"(?=\\(?:line|text|column|paper)width\b)",
        "",
        latex,
    )
    observed = set(
        _DECIMAL_LITERAL_RE.findall(
            f"{markdown_without_heading_ordinals}\n{latex_without_layout_dimensions}"
        )
    )
    unexpected = sorted(
        value for value in observed if not _decimal_literal_matches(value, allowed)
    )
    return (
        [
            "Draft contains decimal metric literals not present in execution artifacts: "
            + ", ".join(unexpected)
        ]
        if unexpected
        else []
    )


def _decimal_literal_matches(value: str, allowed: set[Decimal]) -> bool:
    observed = Decimal(value)
    if observed in allowed:
        return True
    displayed_quantum = Decimal(1).scaleb(observed.as_tuple().exponent)
    rounding_tolerance = abs(displayed_quantum) / 2
    return any(abs(observed - candidate) <= rounding_tolerance for candidate in allowed)


def _required_draft_content_reasons(
    markdown: str,
    *,
    require_appendix: bool = True,
) -> list[str]:
    corpus = markdown.casefold()
    required_content = {
        "introduction": (
            "introduction",
            "motivation",
            "background",
            "question",
            "problem",
            "study scope",
        ),
        "limitation": ("limitation", "bounded scope", "scope limit"),
        "conclusion": ("conclusion", "summary"),
    }
    if require_appendix:
        required_content["appendix"] = ("appendix",)
    return [
        f"Draft lacks required {label} content."
        for label, markers in required_content.items()
        if not any(marker in corpus for marker in markers)
    ]


def _forbidden_reasons(payload: Any) -> list[str]:
    text = (
        json.dumps(payload, sort_keys=True).lower()
        if not isinstance(payload, str)
        else payload.lower()
    )
    reasons = [
        message
        for phrase, message in _FORBIDDEN_PHRASES.items()
        if phrase not in {"real-world validation", "real world validation"}
        and phrase in text
    ]
    fragments = [payload] if isinstance(payload, str) else list(_text_fragments(payload))
    if any(_contains_affirmative_real_world_validation(item) for item in fragments):
        reasons.append("real-world validation assertion")
    return reasons


def _text_fragments(payload: Any) -> Iterable[str]:
    if isinstance(payload, str):
        yield payload
    elif isinstance(payload, dict):
        for value in payload.values():
            yield from _text_fragments(value)
    elif isinstance(payload, (list, tuple, set)):
        for value in payload:
            yield from _text_fragments(value)


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
            if re.search(
                r"\b(?:achieves?|claims?|confirms?|constitutes?|creates?|demonstrates?|"
                r"establishes?|provides?|proves?|represents?|supports?|validates?|verifies?|"
                r"is|was|becomes?)\s+$",
                before,
            ):
                return True
    return False


def _merge_forbidden(values: list[str]) -> list[str]:
    normalized = {value.strip().lower() for value in values}
    return [*values, *[value for value in _MANDATORY_FORBIDDEN if value not in normalized]]


def _blocking_reviews(reviews: list[ManuscriptCriticReview]) -> list[str]:
    return _unique(value for review in reviews for value in review.blocking_findings)


def _load_retrieval_contexts(reports: Path) -> list[RetrievalContext]:
    contexts: list[RetrievalContext] = []
    for path in reports.iterdir() if reports.is_dir() else []:
        if _RETRIEVAL_RE.match(path.name):
            try:
                contexts.append(_load_model(path, RetrievalContext))
            except NucleusManuscriptError:
                continue
    return contexts


def _latest_phase_report(reports: Path, phase: str) -> NucleusManuscriptSynthesisReport | None:
    paths = (
        sorted(
            (path for path in reports.iterdir() if _REPORT_RE.match(path.name)),
            key=lambda path: path.name,
            reverse=True,
        )
        if reports.is_dir()
        else []
    )
    for path in paths:
        report = _load_model(path, NucleusManuscriptSynthesisReport)
        if report.phase == phase:
            return report
    return None


def _latest_matching(directory: Path, pattern: re.Pattern[str]) -> Path | None:
    if not directory.is_dir():
        return None
    matches = [path for path in directory.iterdir() if pattern.match(path.name)]
    return max(matches, key=lambda path: path.name) if matches else None


def _next_number(directory: Path, pattern: re.Pattern[str]) -> int:
    if not directory.is_dir():
        return 1
    numbers = [
        int(match.group(1)) for path in directory.iterdir() if (match := pattern.match(path.name))
    ]
    return max(numbers, default=0) + 1


def _report_number(report_id: str) -> int:
    match = re.search(r"(\d{4})$", report_id)
    if match is None:
        raise NucleusManuscriptError(f"Could not determine report number from {report_id}.")
    return int(match.group(1))


def _load_model(path: Path, model_type: Any) -> Any:
    try:
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise NucleusManuscriptError(f"Could not load {path.name}: {exc}") from exc


def _relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _metadata(stage: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "publication_ready": False,
        "creates_scientific_validation": False,
        "is_verification_evidence": False,
    }


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(item for item in values if item))


def _slug(value: str) -> str:
    return "-".join(re.findall(r"[a-z0-9]+", value.lower())) or "item"


def _latex_escape(value: str) -> str:
    return value.replace("\\", r"\textbackslash{}").replace("_", r"\_").replace("&", r"\&")


def _production_error(prefix: str, report: Any) -> str:
    return prefix + " blocked: " + "; ".join(item.message for item in report.violations)


__all__ = [
    "NucleusManuscriptError",
    "NucleusManuscriptResult",
    "inspect_nucleus_manuscript",
    "plan_nucleus_manuscript",
    "render_nucleus_manuscript_markdown",
    "render_nucleus_manuscript_text",
    "revise_nucleus_manuscript",
    "synthesize_nucleus_manuscript",
]
