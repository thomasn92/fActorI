"""Deterministic human-review readiness gate for generated paper bundles."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from factori.artifacts import ArtifactStore
from factori.citations import CITATION_MARKER_RE, validate_citation_usage
from factori.evidence import (
    claim_label_allowed,
    is_proof_evidence,
    is_synthetic_experiment_evidence,
)
from factori.hashing import sha256_file
from factori.latex_safety import validate_latex_export
from factori.ledger import ResearchLedger
from factori.paper_critic import critique_generated_paper
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.reports import render_full_paper_release_summary
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    CitationRegistry,
    ClaimTable,
    ControllerActionType,
    FullPaperArtifactBundle,
    FullPaperBundleCompletenessReport,
    FullPaperEvidenceBoundaryReport,
    FullPaperGenerationReport,
    FullPaperReadinessDecision,
    FullPaperReleaseCheck,
    FullPaperReleaseFinding,
    FullPaperReleaseFindingSeverity,
    FullPaperReleaseGateConfig,
    FullPaperReleaseReport,
    FullPaperReleaseStatus,
    LatexExportResult,
    LatexSafetyReport,
    LatexSourceMap,
    LiteraturePositioningReport,
    PaperCriticFindingSeverity,
    PaperCriticReport,
    PaperRevisionResult,
    RevisionSafetyReport,
    VerificationLabel,
)

_BIB_KEY_RE = re.compile(r"@[A-Za-z]+\s*\{\s*([^,\s]+)")
_PUBLICATION_READY_PHRASES = ("publication ready", "publication-ready")
_NEGATIONS = ("not ", "cannot ", "does not ", "do not ", "never ", "without ")


class FullPaperReleaseError(RuntimeError):
    """Raised when a release-gate request is malformed or cannot be persisted."""


@dataclass(frozen=True)
class FullPaperReleaseRunResult:
    """Runtime result and optional persisted readiness artifacts."""

    run_id: str
    report: FullPaperReleaseReport
    persistence: PersistenceResult | None = None
    report_artifact: ArtifactRef | None = None
    completeness_artifact: ArtifactRef | None = None
    evidence_boundary_artifact: ArtifactRef | None = None
    summary_artifact: ArtifactRef | None = None


def evaluate_full_paper_release(
    *,
    run_id: str,
    root: str | Path,
    ledger: ResearchLedger,
    config: FullPaperReleaseGateConfig,
) -> FullPaperReleaseReport:
    """Evaluate a generated paper bundle without mutating any run artifact."""
    if config.run_id != run_id:
        raise FullPaperReleaseError("full-paper release config run_id does not match")
    root_path = Path(root)
    refs = _artifact_index(ledger, run_id)
    generation_ref = refs.get("full-paper-generation-report")
    bundle_ref = refs.get("full-paper-artifact-bundle")
    generation = _load_model(root_path, generation_ref, FullPaperGenerationReport)
    bundle = _load_model(root_path, bundle_ref, FullPaperArtifactBundle)

    required = _required_artifact_ids(config, generation, bundle)
    completeness = _build_completeness(
        run_id,
        root_path,
        refs,
        required,
        bundle,
        {commit.commit_hash for commit in ledger.list_commits(run_id)},
    )
    checks: list[FullPaperReleaseCheck] = []
    findings: list[FullPaperReleaseFinding] = []
    _record(
        checks,
        findings,
        "required_artifacts",
        completeness.complete,
        FullPaperReleaseFindingSeverity.BLOCKING,
        (
            "All required generated-paper artifacts are present."
            if completeness.complete
            else "Required generated-paper artifacts are missing: "
            + ", ".join(completeness.missing_artifact_ids)
        ),
        "completeness",
        completeness.missing_artifact_ids,
    )
    provenance_ok = not (
        completeness.hash_mismatch_artifact_ids or completeness.missing_ledger_link_artifact_ids
    )
    _record(
        checks,
        findings,
        "artifact_provenance",
        provenance_ok,
        FullPaperReleaseFindingSeverity.BLOCKING,
        (
            "Generated-paper artifact hashes and ledger links are consistent."
            if provenance_ok
            else "Generated-paper artifact hashes or ledger links are inconsistent."
        ),
        "provenance",
        sorted(
            set(completeness.hash_mismatch_artifact_ids)
            | set(completeness.missing_ledger_link_artifact_ids)
        ),
    )
    generation_ok = bool(
        generation
        and not generation.blocking_issues
        and generation.generation_status.value
        in {"PaperGenerationSucceeded", "PaperGenerationSucceededWithWarnings"}
    )
    _record(
        checks,
        findings,
        "full_paper_generation_status",
        generation_ok,
        FullPaperReleaseFindingSeverity.BLOCKING,
        (
            "Full-paper generation completed without blocking issues."
            if generation_ok
            else "Full-paper generation report is missing, failed, or blocked."
        ),
        "generation",
        ["full-paper-generation-report"],
    )

    draft_ref = _selected_ref(
        refs,
        bundle,
        "revised_manuscript_draft_artifact_id",
        "complete_manuscript_draft_artifact_id",
    )
    latex_ref = _selected_ref(refs, bundle, "revised_latex_artifact_id", "latex_artifact_id")
    references_ref = _selected_ref(
        refs, bundle, "revised_references_artifact_id", "references_artifact_id"
    )
    source_map_ref = _selected_ref(
        refs, bundle, "revised_latex_source_map_artifact_id", "latex_source_map_artifact_id"
    )
    export_ref = _selected_ref(
        refs, bundle, "revised_latex_export_report_artifact_id", "latex_export_report_artifact_id"
    )
    safety_ref = _selected_ref(
        refs, bundle, "revised_latex_safety_report_artifact_id", "latex_safety_report_artifact_id"
    )
    markdown = _read_text(root_path, draft_ref)
    paper_tex = _read_text(root_path, latex_ref)
    references_bib = _read_text(root_path, references_ref)
    citation_registry = _load_model(root_path, refs.get("citation-registry"), CitationRegistry)
    literature = _load_model(
        root_path,
        refs.get("literature-positioning-report"),
        LiteraturePositioningReport,
    )
    source_map = _load_model(root_path, source_map_ref, LatexSourceMap)
    latex_export = _load_model(root_path, export_ref, LatexExportResult)
    persisted_latex_safety = _load_model(root_path, safety_ref, LatexSafetyReport)
    claim_table_ref = refs.get("claim-table")
    claim_table = _load_model(root_path, claim_table_ref, ClaimTable)

    citations_used = bool(CITATION_MARKER_RE.search(markdown))
    citations_required = (
        config.require_citations
        or citations_used
        or bool(generation and generation.config.include_citations)
    )
    citation_safe, citation_reasons, citation_warnings = _check_citations(
        markdown,
        references_bib,
        citation_registry,
        citations_required,
    )
    _record(
        checks,
        findings,
        "citation_safety",
        citation_safe,
        FullPaperReleaseFindingSeverity.BLOCKING,
        "Citation usage and bibliography provenance are safe."
        if citation_safe
        else "; ".join(citation_reasons),
        "citation",
        ["citation-registry", "references"],
    )

    literature_safe = _literature_positioning_safe(literature, citations_required)
    _record(
        checks,
        findings,
        "bounded_literature_positioning",
        literature_safe,
        FullPaperReleaseFindingSeverity.BLOCKING,
        (
            "Literature positioning is explicitly bounded and non-exhaustive."
            if literature_safe
            else "Literature positioning is missing its bounded non-exhaustiveness policy."
        ),
        "citation",
        ["literature-positioning-report"],
    )

    latex_safe, latex_reasons, latex_warnings = _check_latex(
        paper_tex,
        citation_registry,
        source_map,
        latex_export,
        persisted_latex_safety,
        required=config.require_latex_export,
    )
    _record(
        checks,
        findings,
        "latex_safety",
        latex_safe,
        FullPaperReleaseFindingSeverity.BLOCKING,
        "LaTeX export and source map pass safety checks."
        if latex_safe
        else "; ".join(latex_reasons),
        "latex",
        [artifact.id for artifact in (latex_ref, source_map_ref, safety_ref) if artifact],
    )

    evidence_boundary = _build_evidence_boundary_report(
        run_id=run_id,
        markdown=markdown,
        paper_tex=paper_tex,
        claim_table=claim_table,
        claim_table_ref=claim_table_ref,
        refs=refs,
        root=root_path,
    )
    _record(
        checks,
        findings,
        "evidence_boundaries",
        evidence_boundary.safe,
        FullPaperReleaseFindingSeverity.BLOCKING,
        "Generated paper text preserves evidence boundaries."
        if evidence_boundary.safe
        else "; ".join(evidence_boundary.reasons),
        "evidence_boundary",
        [draft_ref.id] if draft_ref else [],
    )

    appendix_reasons = _appendix_reasons(markdown)
    _record(
        checks,
        findings,
        "required_appendices",
        not appendix_reasons,
        FullPaperReleaseFindingSeverity.MAJOR,
        "Claim/evidence and provenance appendices are present."
        if not appendix_reasons
        else "; ".join(appendix_reasons),
        "manuscript",
        [draft_ref.id] if draft_ref else [],
    )

    critic_artifact = _load_model(root_path, refs.get("paper-critic-report"), PaperCriticReport)
    live_critic = _live_critic(
        run_id,
        markdown,
        citation_registry,
        latex_export,
        source_map,
        persisted_latex_safety,
        draft_ref,
        latex_ref,
        source_map_ref,
    )
    critic_exists = critic_artifact is not None
    critic_blocking = live_critic.blocking_findings if live_critic else 0
    critic_major = live_critic.major_findings if live_critic else 0
    critic_warnings = live_critic.warning_findings if live_critic else 0
    critic_ok = (
        critic_exists
        and live_critic is not None
        and critic_blocking == 0
        and critic_major <= config.max_major_findings
    )
    _record(
        checks,
        findings,
        "paper_critic",
        critic_ok,
        FullPaperReleaseFindingSeverity.BLOCKING,
        (
            "Current generated paper is below the configured critic threshold."
            if critic_ok
            else (
                "Paper critic report is missing or current findings exceed the "
                "configured threshold."
            )
        ),
        "critic",
        ["paper-critic-report"],
    )

    revision_status, revision_safe = _revision_status(root_path, refs)
    revision_ok = revision_safe and (
        not config.require_revision_status or revision_status != "NotApplied"
    )
    _record(
        checks,
        findings,
        "revision_status",
        revision_ok,
        FullPaperReleaseFindingSeverity.BLOCKING,
        f"Revision status: {revision_status}.",
        "revision",
        ["paper-revision-result"] if revision_status != "NotApplied" else [],
    )

    warning_messages = sorted(
        set(citation_warnings + latex_warnings + _critic_warning_messages(live_critic))
    )
    if not config.allow_warnings and warning_messages:
        _record(
            checks,
            findings,
            "warning_policy",
            False,
            FullPaperReleaseFindingSeverity.BLOCKING,
            "Warnings are disallowed by the release-gate policy.",
            "policy",
            [],
        )
    status = _decide_status(
        checks, completeness, citation_safe, latex_safe, evidence_boundary.safe, critic_ok
    )
    blocking = sorted(
        {
            finding.message
            for finding in findings
            if finding.severity == FullPaperReleaseFindingSeverity.BLOCKING
        }
    )
    major = [
        finding for finding in findings if finding.severity == FullPaperReleaseFindingSeverity.MAJOR
    ]
    if (
        major
        and len(major) > config.max_major_findings
        and status
        in {
            FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW,
            FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW_WITH_WARNINGS,
        }
    ):
        status = FullPaperReleaseStatus.BLOCKED_CRITIC_FINDINGS
        blocking.append("Major manuscript findings exceed the configured threshold.")
    ready = status in {
        FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW,
        FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW_WITH_WARNINGS,
    }
    if ready and (warning_messages or major or critic_warnings):
        status = FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW_WITH_WARNINGS
    decision = FullPaperReadinessDecision(
        run_id=run_id,
        status=status,
        ready_for_human_review=ready,
        blocking_reasons=blocking,
        warnings=warning_messages,
    )
    return FullPaperReleaseReport(
        report_id=f"full-paper-release-report-{run_id}",
        run_id=run_id,
        config=config,
        checks=checks,
        findings=findings,
        completeness=completeness,
        evidence_boundary=evidence_boundary,
        decision=decision,
        revision_status=revision_status,
        critic_blocking_findings=critic_blocking,
        critic_major_findings=critic_major,
        critic_warning_findings=critic_warnings,
    )


def run_full_paper_release_gate(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    config: FullPaperReleaseGateConfig,
) -> FullPaperReleaseRunResult:
    """Evaluate readiness and optionally persist non-evidence audit artifacts."""
    report = evaluate_full_paper_release(run_id=run_id, root=root, ledger=ledger, config=config)
    if not config.write_report:
        return FullPaperReleaseRunResult(run_id=run_id, report=report)
    if any(
        commit.action_type == ControllerActionType.FULL_PAPER_RELEASE_EVALUATED
        for commit in ledger.list_commits(run_id)
    ):
        raise FullPaperReleaseError("full-paper release report already exists for this run")
    metadata = {
        "stage": "full_paper_release",
        "artifact_role": "human_review_readiness_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                "full-paper-release-report", ArtifactType.REPORT, report, "json", metadata
            ),
            ArtifactWriteSpec(
                "full-paper-bundle-completeness",
                ArtifactType.REPORT,
                report.completeness,
                "json",
                metadata,
            ),
            ArtifactWriteSpec(
                "full-paper-evidence-boundary-report",
                ArtifactType.REPORT,
                report.evidence_boundary,
                "json",
                metadata,
            ),
            ArtifactWriteSpec(
                "full-paper-release-summary",
                ArtifactType.REPORT,
                render_full_paper_release_summary(report),
                "markdown",
                metadata,
            ),
        ],
        action_type=ControllerActionType.FULL_PAPER_RELEASE_EVALUATED,
        commit_payload={
            "run_id": run_id,
            "status": report.decision.status.value,
            "ready_for_human_review": report.decision.ready_for_human_review,
            "publication_ready": False,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return FullPaperReleaseRunResult(
        run_id=run_id,
        report=report,
        persistence=persistence,
        report_artifact=by_id["full-paper-release-report"],
        completeness_artifact=by_id["full-paper-bundle-completeness"],
        evidence_boundary_artifact=by_id["full-paper-evidence-boundary-report"],
        summary_artifact=by_id["full-paper-release-summary"],
    )


def _artifact_index(ledger: ResearchLedger, run_id: str) -> dict[str, ArtifactRef]:
    refs: dict[str, ArtifactRef] = {}
    for commit in ledger.list_commits(run_id):
        for artifact in commit.artifact_refs:
            refs[artifact.id] = artifact
    return refs


def _required_artifact_ids(config, generation, bundle) -> list[str]:
    required = {
        "full-paper-generation-report",
        "full-paper-artifact-bundle",
        "complete-manuscript-draft",
        "paper-critic-report",
    }
    if config.require_latex_export or (generation and generation.config.export_latex):
        required.update(
            {
                "paper",
                "references",
                "latex-source-map",
                "latex-export-report",
                "latex-safety-report",
            }
        )
    if config.require_citations or (generation and generation.config.include_citations):
        required.update(
            {"citation-registry", "citation-safety-report", "literature-positioning-report"}
        )
    if bundle is not None:
        required.update(bundle.artifact_ids)
    return sorted(required)


def _build_completeness(run_id, root, refs, required, bundle, commit_hashes):
    bundle_ids = bundle.artifact_ids if bundle is not None else []
    all_ids = sorted(set(required) | set(bundle_ids))
    present, missing, mismatches, unlinked = [], [], [], []
    for artifact_id in all_ids:
        ref = refs.get(artifact_id)
        if ref is None or not (root / ref.path).is_file():
            missing.append(artifact_id)
            continue
        present.append(artifact_id)
        if sha256_file(root / ref.path) != ref.content_hash:
            mismatches.append(artifact_id)
        if ref.producing_commit_hash is None or ref.producing_commit_hash not in commit_hashes:
            unlinked.append(artifact_id)
    return FullPaperBundleCompletenessReport(
        run_id=run_id,
        required_artifact_ids=all_ids,
        present_artifact_ids=present,
        missing_artifact_ids=missing,
        hash_mismatch_artifact_ids=mismatches,
        missing_ledger_link_artifact_ids=unlinked,
        complete=not missing,
    )


def _selected_ref(refs, bundle, preferred_field, fallback_field):
    if bundle is None:
        return None
    preferred = getattr(bundle, preferred_field)
    fallback = getattr(bundle, fallback_field)
    return refs.get(preferred or fallback) if preferred or fallback else None


def _load_model(root, ref, model_type):
    if ref is None or not (root / ref.path).is_file():
        return None
    try:
        return model_type.model_validate_json((root / ref.path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _read_text(root, ref) -> str:
    if ref is None or not (root / ref.path).is_file():
        return ""
    return (root / ref.path).read_text(encoding="utf-8")


def _check_citations(markdown, bibliography, registry, required):
    if registry is None:
        return (
            not required and not CITATION_MARKER_RE.search(markdown),
            ["Citation registry is missing."]
            if required or CITATION_MARKER_RE.search(markdown)
            else [],
            [],
        )
    report = validate_citation_usage(markdown, registry)
    known = {record.citation_key for record in registry.citations}
    bib_keys = set(_BIB_KEY_RE.findall(bibliography))
    invented = sorted(bib_keys - known)
    reasons = list(report.reasons)
    if invented:
        reasons.append("invented bibliography keys: " + ", ".join(invented))
    return not reasons, sorted(set(reasons)), report.warnings


def _literature_positioning_safe(report, required):
    if report is None:
        return not required
    contract = report.contract
    text = " ".join(
        [contract.non_exhaustiveness_disclaimer, *contract.coverage_limitations]
    ).lower()
    return ("not exhaustive" in text or "non-exhaustive" in text) and "not proof of novelty" in text


def _check_latex(paper_tex, registry, source_map, export, persisted_safety, *, required):
    if not required and not paper_tex:
        return True, [], []
    if not paper_tex or source_map is None or export is None or persisted_safety is None:
        return False, ["LaTeX export, source map, or safety report is missing."], []
    report = validate_latex_export(
        contract=export.contract,
        paper_tex=paper_tex,
        source_map=source_map,
        citation_registry=registry,
    )
    reasons = sorted(set(report.reasons + persisted_safety.reasons))
    warnings = sorted(set(report.warnings + persisted_safety.warnings))
    return report.safe and persisted_safety.safe and not reasons, reasons, warnings


def _build_evidence_boundary_report(
    *, run_id, markdown, paper_tex, claim_table, claim_table_ref, refs, root
):
    reasons, unsupported = [], []
    text = f"{markdown}\n{paper_tex}"
    claim_unchanged = bool(
        claim_table_ref
        and (root / claim_table_ref.path).is_file()
        and sha256_file(root / claim_table_ref.path) == claim_table_ref.content_hash
    )
    if not claim_unchanged:
        reasons.append("claim table is missing or differs from its ledgered content hash")
    evidence_refs = [
        ref
        for ref in refs.values()
        if is_proof_evidence(ref) or is_synthetic_experiment_evidence(ref)
    ]
    evidence_unchanged = all(
        (root / ref.path).is_file() and sha256_file(root / ref.path) == ref.content_hash
        for ref in evidence_refs
    )
    if not evidence_unchanged:
        reasons.append("verification evidence artifacts differ from ledgered content hashes")
    for label in (VerificationLabel.LEAN_VERIFIED, VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED):
        if label.value not in text:
            continue
        label_lines = [line.lower() for line in text.splitlines() if label.value in line]
        supported = False
        if claim_table is not None:
            for claim in claim_table.claims:
                claim_refs = [
                    refs[artifact_id]
                    for artifact_id in claim.evidence_artifact_ids
                    if artifact_id in refs
                ]
                if (
                    claim.claim_label == label
                    and any(claim.claim_text.lower() in line for line in label_lines)
                    and claim_label_allowed(label, claim_refs)
                ):
                    supported = True
                    break
        if not supported:
            unsupported.append(label.value)
            reasons.append(f"{label.value} appears without linked supporting evidence")
    if VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED.value in text:
        unsupported.append(VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED.value)
        reasons.append("RealDataExperimentVerified is unavailable in the MVP")
    normalized = " ".join(text.lower().split())
    if _contains_unnegated(
        normalized,
        ("retrieval proves novelty", "novelty is proven by retrieval", "citations prove novelty"),
    ):
        reasons.append("retrieval or citations are used as novelty proof")
    if _contains_unnegated(
        normalized, ("empirically validated", "real-world validation", "validated on real data")
    ):
        reasons.append("synthetic evidence is described as real-world empirical validation")
    if _contains_unnegated(normalized, _PUBLICATION_READY_PHRASES):
        reasons.append("generated paper text claims publication readiness")
    for ref in refs.values():
        if (
            ref.id
            in {
                "paper",
                "references",
                "latex-source-map",
                "complete-manuscript-draft",
                "revised-manuscript-draft",
            }
            and ref.is_mvp_verification_evidence()
        ):
            reasons.append(
                f"presentation artifact is classified as verification evidence: {ref.id}"
            )
    return FullPaperEvidenceBoundaryReport(
        run_id=run_id,
        safe=not reasons,
        unsupported_labels=sorted(set(unsupported)),
        reasons=sorted(set(reasons)),
        warnings=["Human-review readiness is not publication readiness or scientific validation."],
        claim_table_unchanged=claim_unchanged,
        evidence_classification_unchanged=evidence_unchanged,
        creates_or_upgrades_labels=False,
    )


def _appendix_reasons(markdown):
    lower = markdown.lower()
    reasons = []
    if "claim/evidence appendix" not in lower:
        reasons.append("claim/evidence appendix is missing")
    if "provenance appendix" not in lower:
        reasons.append("provenance appendix is missing")
    return reasons


def _live_critic(
    run_id, markdown, registry, export, source_map, safety, draft_ref, latex_ref, source_map_ref
):
    if not markdown:
        return None
    return critique_generated_paper(
        run_id=run_id,
        markdown=markdown,
        citation_registry=registry,
        latex_result=export,
        source_map=source_map,
        latex_safety_report=safety,
        manuscript_draft_artifact_id=draft_ref.id if draft_ref else None,
        latex_artifact_id=latex_ref.id if latex_ref else None,
        source_map_artifact_id=source_map_ref.id if source_map_ref else None,
    )


def _revision_status(root, refs):
    result = _load_model(root, refs.get("paper-revision-result"), PaperRevisionResult)
    safety = _load_model(root, refs.get("revision-safety-report"), RevisionSafetyReport)
    if result is None:
        return "NotApplied", True
    return result.revision_status.value, bool(safety and safety.safe)


def _critic_warning_messages(report):
    if report is None:
        return []
    return sorted(
        {
            finding.message
            for finding in report.findings
            if finding.severity == PaperCriticFindingSeverity.WARNING
        }
    )


def _record(checks, findings, check_id, passed, severity, message, category, artifact_ids):
    checks.append(
        FullPaperReleaseCheck(
            check_id=check_id,
            passed=passed,
            severity=severity,
            message=message,
            artifact_ids=sorted(set(artifact_ids)),
        )
    )
    if not passed:
        findings.append(
            FullPaperReleaseFinding(
                finding_id=f"release-{len(findings) + 1:03d}",
                category=category,
                severity=severity,
                message=message,
                artifact_ids=sorted(set(artifact_ids)),
            )
        )


def _decide_status(checks, completeness, citation_safe, latex_safe, evidence_safe, critic_ok):
    if completeness.missing_artifact_ids:
        return FullPaperReleaseStatus.BLOCKED_MISSING_ARTIFACTS
    if completeness.hash_mismatch_artifact_ids or completeness.missing_ledger_link_artifact_ids:
        return FullPaperReleaseStatus.BLOCKED_INCONSISTENT_PROVENANCE
    if not evidence_safe:
        return FullPaperReleaseStatus.BLOCKED_EVIDENCE_BOUNDARY_VIOLATION
    if not citation_safe:
        return FullPaperReleaseStatus.BLOCKED_CITATION_SAFETY_VIOLATION
    if not latex_safe:
        return FullPaperReleaseStatus.BLOCKED_LATEX_SAFETY_VIOLATION
    if not critic_ok:
        return FullPaperReleaseStatus.BLOCKED_CRITIC_FINDINGS
    if any(
        not check.passed and check.severity == FullPaperReleaseFindingSeverity.BLOCKING
        for check in checks
    ):
        return FullPaperReleaseStatus.RELEASE_GATE_FAILED
    return FullPaperReleaseStatus.READY_FOR_HUMAN_REVIEW


def _contains_unnegated(text, phrases):
    for phrase in phrases:
        start = text.find(phrase)
        while start >= 0:
            prefix = text[max(0, start - 24) : start]
            if not any(negation in prefix for negation in _NEGATIONS):
                return True
            start = text.find(phrase, start + len(phrase))
    return False


__all__ = [
    "FullPaperReleaseError",
    "FullPaperReleaseRunResult",
    "evaluate_full_paper_release",
    "run_full_paper_release_gate",
]
