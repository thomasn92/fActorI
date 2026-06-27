"""Deterministic paper critic over generated Markdown and LaTeX artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from factori.artifacts import ArtifactStore
from factori.citations import CITATION_MARKER_RE, validate_citation_usage
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    CitationRegistry,
    ControllerActionType,
    LatexExportResult,
    LatexSafetyReport,
    LatexSourceMap,
    PaperCriticFinding,
    PaperCriticFindingSeverity,
    PaperCriticFindingType,
    PaperCriticReport,
    PaperReleaseReadinessPreview,
    PaperRevisionActionKind,
    PaperRevisionPlan,
    PaperShapeCritique,
    PaperShapeStatus,
    SectionRevisionPlan,
)

RETRIEVAL_NOVELTY_CLAIMS = (
    "retrieval proves novelty",
    "citations prove novelty",
    "novelty is proven by retrieval",
    "novelty is proven",
    "retrieval as proof",
)
SYNTHETIC_AS_REAL_CLAIMS = (
    "empirically validated",
    "real-world validation",
    "real world validation",
    "real-world validated",
    "validated on real data",
)
NON_EXHAUSTIVE_TERMS = (
    "non-exhaustive",
    "not exhaustive",
    "bounded literature",
    "bounded positioning",
)


class PaperCriticError(RuntimeError):
    """Raised when paper critique prerequisites are missing or malformed."""


@dataclass(frozen=True)
class PaperCriticInputs:
    """Disk-loaded generated-paper artifacts for critique."""

    markdown: str
    manuscript_draft_artifact: ArtifactRef
    citation_registry: CitationRegistry | None = None
    citation_registry_artifact: ArtifactRef | None = None
    latex_result: LatexExportResult | None = None
    latex_artifact: ArtifactRef | None = None
    source_map: LatexSourceMap | None = None
    source_map_artifact: ArtifactRef | None = None
    latex_safety_report: LatexSafetyReport | None = None


@dataclass(frozen=True)
class PaperCriticRunResult:
    """Paper critique result with optional persisted artifact references."""

    run_id: str
    inputs: PaperCriticInputs
    critic_report: PaperCriticReport
    critic_report_artifact: ArtifactRef | None = None
    commit_hash: str | None = None


def critique_generated_paper(
    *,
    run_id: str,
    markdown: str,
    citation_registry: CitationRegistry | None = None,
    latex_result: LatexExportResult | None = None,
    source_map: LatexSourceMap | None = None,
    latex_safety_report: LatexSafetyReport | None = None,
    paper_shape_critique: PaperShapeCritique | None = None,
    manuscript_draft_artifact_id: str | None = None,
    latex_artifact_id: str | None = None,
    source_map_artifact_id: str | None = None,
) -> PaperCriticReport:
    """Critique generated paper artifacts without creating evidence or validation."""
    source_map = source_map or (latex_result.source_map if latex_result is not None else None)
    latex_safety_report = latex_safety_report or (
        latex_result.safety_report if latex_result is not None else None
    )
    findings: list[PaperCriticFinding] = []
    findings.extend(_narrative_findings(markdown, paper_shape_critique))
    findings.extend(_citation_findings(markdown, citation_registry))
    findings.extend(_evidence_boundary_findings(markdown))
    findings.extend(_latex_findings(latex_result, latex_safety_report))
    findings.extend(_source_map_findings(source_map))
    findings.extend(_section_findings(markdown))
    findings = _with_finding_ids(findings)

    blocking = _count_severity(findings, PaperCriticFindingSeverity.BLOCKING)
    major = _count_severity(findings, PaperCriticFindingSeverity.MAJOR)
    warnings = _count_severity(findings, PaperCriticFindingSeverity.WARNING)
    info = _count_severity(findings, PaperCriticFindingSeverity.INFO)
    preview = PaperReleaseReadinessPreview(
        run_id=run_id,
        ready_for_revision_review=blocking == 0,
        blocking_findings=blocking,
        major_findings=major,
        warning_findings=warnings,
        publication_ready=False,
        reasons=_preview_reasons(blocking, major, warnings),
    )
    return PaperCriticReport(
        report_id=f"paper-critic-report-{run_id}",
        run_id=run_id,
        manuscript_draft_artifact_id=manuscript_draft_artifact_id,
        latex_artifact_id=latex_artifact_id,
        source_map_artifact_id=source_map_artifact_id,
        findings=findings,
        findings_count=len(findings),
        blocking_findings=blocking,
        major_findings=major,
        warning_findings=warnings,
        info_findings=info,
        paper_shape_status=paper_shape_critique.status if paper_shape_critique else None,
        citation_safe=(
            validate_citation_usage(markdown, citation_registry).safe
            if citation_registry is not None
            else None
        ),
        latex_safe=latex_safety_report.safe if latex_safety_report is not None else None,
        source_map_covered=source_map.covers_all_major_sections if source_map is not None else None,
        release_readiness_preview=preview,
    )


def build_paper_revision_plan(report: PaperCriticReport) -> PaperRevisionPlan:
    """Map critic findings to conservative deterministic revision actions."""
    actionable = [
        finding for finding in report.findings
        if finding.recommended_action != PaperRevisionActionKind.NO_ACTION_NEEDED
    ]
    actions = _unique_actions(finding.recommended_action for finding in actionable)
    if not actions:
        actions = [PaperRevisionActionKind.NO_ACTION_NEEDED]
    section_buckets: dict[str, list[PaperCriticFinding]] = {}
    for finding in actionable:
        section_key = finding.section_id or "global"
        section_buckets.setdefault(section_key, []).append(finding)
    section_plans = [
        SectionRevisionPlan(
            section_id=section_id,
            section_title=_section_title(section_id, findings),
            actions=_unique_actions(finding.recommended_action for finding in findings),
            finding_ids=[finding.finding_id for finding in findings],
            safe_to_apply=True,
            notes=[finding.message for finding in findings],
        )
        for section_id, findings in sorted(section_buckets.items())
    ]
    return PaperRevisionPlan(
        plan_id=f"paper-revision-plan-{report.report_id}",
        run_id=report.run_id,
        critic_report_id=report.report_id,
        actions=actions,
        section_plans=section_plans,
        blocking_actions=_unique_actions(
            finding.recommended_action
            for finding in report.findings
            if finding.severity == PaperCriticFindingSeverity.BLOCKING
            and finding.recommended_action != PaperRevisionActionKind.NO_ACTION_NEEDED
        ),
        safe_to_apply=True,
        warnings=[
            "Revision plan is manuscript-quality context only and cannot imply "
            "publication readiness."
        ],
    )


def critique_paper_from_run(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    write_report: bool = False,
) -> PaperCriticRunResult:
    """Load latest generated-paper artifacts, critique them, and optionally persist."""
    inputs = load_paper_critic_inputs(run_id=run_id, root=root, ledger=ledger)
    report = critique_generated_paper(
        run_id=run_id,
        markdown=inputs.markdown,
        citation_registry=inputs.citation_registry,
        latex_result=inputs.latex_result,
        source_map=inputs.source_map,
        latex_safety_report=inputs.latex_safety_report,
        manuscript_draft_artifact_id=inputs.manuscript_draft_artifact.id,
        latex_artifact_id=inputs.latex_artifact.id if inputs.latex_artifact else None,
        source_map_artifact_id=(
            inputs.source_map_artifact.id if inputs.source_map_artifact else None
        ),
    )
    result = PaperCriticRunResult(run_id=run_id, inputs=inputs, critic_report=report)
    if not write_report:
        return result
    persistence = write_paper_critic_report(
        run_id=run_id,
        store=store,
        ledger=ledger,
        report=report,
    )
    artifact = persistence.artifacts[0]
    return PaperCriticRunResult(
        run_id=run_id,
        inputs=inputs,
        critic_report=report.model_copy(update={"report_id": report.report_id}),
        critic_report_artifact=artifact,
        commit_hash=persistence.commit.commit_hash,
    )


def load_paper_critic_inputs(
    *,
    run_id: str,
    root: str | Path,
    ledger: ResearchLedger,
) -> PaperCriticInputs:
    """Load the latest Markdown manuscript draft and optional LaTeX/citation artifacts."""
    commits = ledger.list_commits(run_id)
    draft_commit = _latest_commit(commits, ControllerActionType.MANUSCRIPT_DRAFT_WRITTEN)
    if draft_commit is None:
        raise PaperCriticError(
            "Complete Markdown manuscript draft not found; run factori "
            "draft-manuscript --write-report first"
        )
    refs = {artifact.id: artifact for artifact in draft_commit.artifact_refs}
    draft_artifact = refs.get("complete-manuscript-draft")
    if draft_artifact is None:
        raise PaperCriticError("Manuscript draft commit is missing complete-manuscript-draft")
    root_path = Path(root)
    markdown = _read_text_artifact(root_path, draft_artifact)
    citation_artifact = refs.get("citation-registry")
    if citation_artifact is None:
        citation_commit = _latest_commit(commits, ControllerActionType.CITATION_REGISTRY_WRITTEN)
        if citation_commit is not None:
            citation_artifact = {
                artifact.id: artifact for artifact in citation_commit.artifact_refs
            }.get("citation-registry")
    citation_registry = (
        CitationRegistry.model_validate_json(_read_text_artifact(root_path, citation_artifact))
        if citation_artifact is not None
        else None
    )

    latex_commit = _latest_commit(commits, ControllerActionType.LATEX_EXPORT_WRITTEN)
    latex_result = None
    latex_artifact = None
    source_map = None
    source_map_artifact = None
    latex_safety_report = None
    if latex_commit is not None:
        latex_refs = {artifact.id: artifact for artifact in latex_commit.artifact_refs}
        latex_artifact = latex_refs.get("paper")
        source_map_artifact = latex_refs.get("latex-source-map")
        export_artifact = latex_refs.get("latex-export-report")
        safety_artifact = latex_refs.get("latex-safety-report")
        if export_artifact is not None:
            latex_result = LatexExportResult.model_validate_json(
                _read_text_artifact(root_path, export_artifact)
            )
        if source_map_artifact is not None:
            source_map = LatexSourceMap.model_validate_json(
                _read_text_artifact(root_path, source_map_artifact)
            )
        if safety_artifact is not None:
            latex_safety_report = LatexSafetyReport.model_validate_json(
                _read_text_artifact(root_path, safety_artifact)
            )
    return PaperCriticInputs(
        markdown=markdown,
        manuscript_draft_artifact=draft_artifact,
        citation_registry=citation_registry,
        citation_registry_artifact=citation_artifact,
        latex_result=latex_result,
        latex_artifact=latex_artifact,
        source_map=source_map,
        source_map_artifact=source_map_artifact,
        latex_safety_report=latex_safety_report,
    )


def write_paper_critic_report(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    report: PaperCriticReport,
) -> PersistenceResult:
    """Persist a paper critic report as non-evidence manuscript context."""
    metadata = _paper_context_metadata("paper_critic")
    return persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                artifact_id="paper-critic-report",
                artifact_type=ArtifactType.REPORT,
                payload=report,
                artifact_format="json",
                metadata=metadata,
            )
        ],
        action_type=ControllerActionType.PAPER_CRITIC_REPORT_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "findings": report.findings_count,
            "blocking_findings": report.blocking_findings,
            "major_findings": report.major_findings,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
        },
    )


def _narrative_findings(
    markdown: str,
    paper_shape_critique: PaperShapeCritique | None,
) -> list[PaperCriticFinding]:
    lower = _normalized(markdown)
    findings: list[PaperCriticFinding] = []
    if "## central message" not in lower or "central message unavailable" in lower:
        findings.append(
            _finding(
                PaperCriticFindingType.NARRATIVE_SHAPE_FINDING,
                PaperCriticFindingSeverity.MAJOR,
                "central message is missing or unavailable",
                PaperRevisionActionKind.ADD_CENTRAL_MESSAGE,
                "markdown",
                section_id="central-message",
                section_title="Central Message",
            )
        )
    if "problem" not in lower:
        findings.append(
            _finding(
                PaperCriticFindingType.NARRATIVE_SHAPE_FINDING,
                PaperCriticFindingSeverity.WARNING,
                "problem framing is not explicit in the draft",
                PaperRevisionActionKind.CLARIFY_PROBLEM_STATEMENT,
                "markdown",
                section_id="introduction",
                section_title="Introduction",
            )
        )
    if not any(term in lower for term in NON_EXHAUSTIVE_TERMS):
        findings.append(
            _finding(
                PaperCriticFindingType.NARRATIVE_SHAPE_FINDING,
                PaperCriticFindingSeverity.WARNING,
                "bounded non-exhaustive literature positioning disclaimer is missing",
                PaperRevisionActionKind.ADD_BOUNDED_LITERATURE_GAP,
                "markdown",
                section_id="introduction",
                section_title="Introduction",
            )
        )
    if "main result" not in lower:
        findings.append(
            _finding(
                PaperCriticFindingType.NARRATIVE_SHAPE_FINDING,
                PaperCriticFindingSeverity.WARNING,
                "main result is not stated in prose",
                PaperRevisionActionKind.DOWNGRADE_UNSUPPORTED_CLAIM_LANGUAGE,
                "markdown",
                section_id="main-result",
                section_title="Main Result and Derivatives",
            )
        )
    if _main_result_mentions_before_appendix(markdown) > 1:
        findings.append(
            _finding(
                PaperCriticFindingType.NARRATIVE_SHAPE_FINDING,
                PaperCriticFindingSeverity.MAJOR,
                "multiple primary main-result statements appear before the appendix",
                PaperRevisionActionKind.DOWNGRADE_UNSUPPORTED_CLAIM_LANGUAGE,
                "markdown",
                section_id="main-result",
                section_title="Main Result and Derivatives",
            )
        )
    if paper_shape_critique is not None and paper_shape_critique.status in {
        PaperShapeStatus.PAPER_SHAPE_WEAK,
        PaperShapeStatus.NOT_PAPER_SHAPED,
    }:
        findings.append(
            _finding(
                PaperCriticFindingType.NARRATIVE_SHAPE_FINDING,
                PaperCriticFindingSeverity.MAJOR,
                f"paper-shape critique status is {paper_shape_critique.status.value}",
                PaperRevisionActionKind.ADD_MISSING_LIMITATION,
                "paper_shape_critique",
            )
        )
    return findings


def _citation_findings(
    markdown: str,
    citation_registry: CitationRegistry | None,
) -> list[PaperCriticFinding]:
    findings: list[PaperCriticFinding] = []
    markers = sorted(set(CITATION_MARKER_RE.findall(markdown)))
    if citation_registry is None:
        if markers:
            findings.append(
                _finding(
                    PaperCriticFindingType.CITATION_SAFETY_FINDING,
                    PaperCriticFindingSeverity.BLOCKING,
                    "citation markers appear but no citation registry is available",
                    PaperRevisionActionKind.REMOVE_INVENTED_CITATION,
                    "citation_safety",
                    blocking=True,
                )
            )
        return findings
    safety = validate_citation_usage(markdown, citation_registry)
    for key in safety.unknown_citation_keys:
        findings.append(
            _finding(
                PaperCriticFindingType.CITATION_SAFETY_FINDING,
                PaperCriticFindingSeverity.BLOCKING,
                f"unknown or invented citation key: {key}",
                PaperRevisionActionKind.REMOVE_INVENTED_CITATION,
                "citation_safety",
                blocking=True,
            )
        )
    missing = [
        entry.citation_key
        for entry in citation_registry.bibliography
        if not entry.has_source_provenance
    ]
    for key in sorted(missing):
        findings.append(
            _finding(
                PaperCriticFindingType.CITATION_SAFETY_FINDING,
                PaperCriticFindingSeverity.BLOCKING,
                f"bibliography entry lacks source provenance: {key}",
                PaperRevisionActionKind.ADD_CITATION_LIMITATION,
                "citation_registry",
                blocking=True,
            )
        )
    return findings


def _evidence_boundary_findings(markdown: str) -> list[PaperCriticFinding]:
    lower = _normalized(markdown)
    findings: list[PaperCriticFinding] = []
    if any(phrase in lower for phrase in RETRIEVAL_NOVELTY_CLAIMS):
        findings.append(
            _finding(
                PaperCriticFindingType.EVIDENCE_BOUNDARY_FINDING,
                PaperCriticFindingSeverity.BLOCKING,
                "retrieval or citations are described as novelty/proof evidence",
                PaperRevisionActionKind.DOWNGRADE_UNSUPPORTED_CLAIM_LANGUAGE,
                "evidence_boundary",
                blocking=True,
            )
        )
    if any(phrase in lower for phrase in SYNTHETIC_AS_REAL_CLAIMS):
        findings.append(
            _finding(
                PaperCriticFindingType.EMPIRICAL_BOUNDARY_FINDING,
                PaperCriticFindingSeverity.BLOCKING,
                "synthetic or MVP evidence is described as real-world empirical validation",
                PaperRevisionActionKind.CLARIFY_SYNTHETIC_ONLY_BOUNDARY,
                "evidence_boundary",
                section_id="empirical-results",
                section_title="Empirical Results and Discussion",
                blocking=True,
            )
        )
    if "leanverified" in lower:
        findings.append(
            _finding(
                PaperCriticFindingType.EVIDENCE_BOUNDARY_FINDING,
                PaperCriticFindingSeverity.BLOCKING,
                "LeanVerified language appears without local proof-evidence validation "
                "in the critic",
                PaperRevisionActionKind.DOWNGRADE_UNSUPPORTED_CLAIM_LANGUAGE,
                "evidence_boundary",
                blocking=True,
            )
        )
    if "syntheticexperimentverified" in lower:
        findings.append(
            _finding(
                PaperCriticFindingType.EVIDENCE_BOUNDARY_FINDING,
                PaperCriticFindingSeverity.BLOCKING,
                "SyntheticExperimentVerified language appears without linked synthetic "
                "evidence in the critic",
                PaperRevisionActionKind.DOWNGRADE_UNSUPPORTED_CLAIM_LANGUAGE,
                "evidence_boundary",
                blocking=True,
            )
        )
    if "realdataexperimentverified" in lower:
        findings.append(
            _finding(
                PaperCriticFindingType.EVIDENCE_BOUNDARY_FINDING,
                PaperCriticFindingSeverity.BLOCKING,
                "RealDataExperimentVerified is not allowed in the MVP manuscript",
                PaperRevisionActionKind.DOWNGRADE_UNSUPPORTED_CLAIM_LANGUAGE,
                "evidence_boundary",
                blocking=True,
            )
        )
    return findings


def _latex_findings(
    latex_result: LatexExportResult | None,
    latex_safety_report: LatexSafetyReport | None,
) -> list[PaperCriticFinding]:
    findings: list[PaperCriticFinding] = []
    if latex_result is not None and (
        latex_result.is_verification_evidence
        or latex_result.creates_scientific_validation
        or latex_result.implies_publication_readiness
    ):
        findings.append(
            _finding(
                PaperCriticFindingType.LATEX_SAFETY_FINDING,
                PaperCriticFindingSeverity.BLOCKING,
                "LaTeX export result is marked as evidence or publication readiness",
                PaperRevisionActionKind.DOWNGRADE_UNSUPPORTED_CLAIM_LANGUAGE,
                "latex_export",
                blocking=True,
            )
        )
    if latex_safety_report is not None and not latex_safety_report.safe:
        for reason in latex_safety_report.reasons:
            findings.append(
                _finding(
                    PaperCriticFindingType.LATEX_SAFETY_FINDING,
                    PaperCriticFindingSeverity.BLOCKING,
                    reason,
                    PaperRevisionActionKind.DOWNGRADE_UNSUPPORTED_CLAIM_LANGUAGE,
                    "latex_safety",
                    blocking=True,
                )
            )
    return findings


def _source_map_findings(source_map: LatexSourceMap | None) -> list[PaperCriticFinding]:
    if source_map is None:
        return [
            _finding(
                PaperCriticFindingType.SOURCE_MAP_FINDING,
                PaperCriticFindingSeverity.INFO,
                "LaTeX source map is not available; run export-latex --write-report if needed",
                PaperRevisionActionKind.ADD_SOURCE_MAP_WARNING,
                "source_map",
            )
        ]
    if source_map.covers_all_major_sections:
        return []
    return [
        _finding(
            PaperCriticFindingType.SOURCE_MAP_FINDING,
            PaperCriticFindingSeverity.MAJOR,
            "LaTeX source map is missing major sections: "
            + ", ".join(source_map.missing_sections),
            PaperRevisionActionKind.ADD_SOURCE_MAP_WARNING,
            "source_map",
        )
    ]


def _section_findings(markdown: str) -> list[PaperCriticFinding]:
    lower = _normalized(markdown)
    findings: list[PaperCriticFinding] = []
    if "## claim/evidence appendix" not in lower:
        findings.append(
            _finding(
                PaperCriticFindingType.APPENDIX_ALLOCATION_FINDING,
                PaperCriticFindingSeverity.MAJOR,
                "claim/evidence appendix is missing",
                PaperRevisionActionKind.ADD_MISSING_LIMITATION,
                "markdown",
                section_id="claim-evidence-appendix",
                section_title="Claim/Evidence Appendix",
            )
        )
    if "## provenance appendix" not in lower:
        findings.append(
            _finding(
                PaperCriticFindingType.APPENDIX_ALLOCATION_FINDING,
                PaperCriticFindingSeverity.MAJOR,
                "provenance appendix is missing",
                PaperRevisionActionKind.ADD_MISSING_LIMITATION,
                "markdown",
                section_id="provenance-appendix",
                section_title="Provenance Appendix",
            )
        )
    if "limitation" not in lower:
        findings.append(
            _finding(
                PaperCriticFindingType.SECTION_COHERENCE_FINDING,
                PaperCriticFindingSeverity.WARNING,
                "limitations are not explicit",
                PaperRevisionActionKind.ADD_MISSING_LIMITATION,
                "markdown",
                section_id="limitations",
                section_title="Limitations",
            )
        )
    before_appendix = markdown.split("## Appendix", maxsplit=1)[0].lower()
    if "technical lemma" in before_appendix:
        findings.append(
            _finding(
                PaperCriticFindingType.APPENDIX_ALLOCATION_FINDING,
                PaperCriticFindingSeverity.WARNING,
                "technical lemmas appear in the main body",
                PaperRevisionActionKind.MOVE_TECHNICAL_LEMMA_TO_APPENDIX,
                "markdown",
                section_id="appendix",
                section_title="Appendix",
            )
        )
    return findings


def _finding(
    finding_type: PaperCriticFindingType,
    severity: PaperCriticFindingSeverity,
    message: str,
    action: PaperRevisionActionKind,
    source: str,
    *,
    section_id: str | None = None,
    section_title: str | None = None,
    blocking: bool = False,
) -> PaperCriticFinding:
    return PaperCriticFinding(
        finding_id="pending",
        finding_type=finding_type,
        severity=severity,
        section_id=section_id,
        section_title=section_title,
        message=message,
        recommended_action=action,
        source=source,
        blocking=blocking or severity == PaperCriticFindingSeverity.BLOCKING,
    )


def _with_finding_ids(findings: list[PaperCriticFinding]) -> list[PaperCriticFinding]:
    ordered = sorted(
        findings,
        key=lambda finding: (
            _severity_order(finding.severity),
            finding.finding_type.value,
            finding.section_id or "",
            finding.message,
        ),
    )
    return [
        finding.model_copy(update={"finding_id": f"paper-finding-{index:03d}"})
        for index, finding in enumerate(ordered, start=1)
    ]


def _severity_order(severity: PaperCriticFindingSeverity) -> int:
    return {
        PaperCriticFindingSeverity.BLOCKING: 0,
        PaperCriticFindingSeverity.MAJOR: 1,
        PaperCriticFindingSeverity.WARNING: 2,
        PaperCriticFindingSeverity.INFO: 3,
    }[severity]


def _count_severity(
    findings: list[PaperCriticFinding],
    severity: PaperCriticFindingSeverity,
) -> int:
    return sum(1 for finding in findings if finding.severity == severity)


def _preview_reasons(blocking: int, major: int, warnings: int) -> list[str]:
    reasons = ["Paper critic preview is not publication readiness."]
    if blocking:
        reasons.append(f"{blocking} blocking findings require deterministic revision.")
    if major:
        reasons.append(f"{major} major manuscript-quality findings remain.")
    if warnings:
        reasons.append(f"{warnings} warning findings remain.")
    return reasons


def _unique_actions(actions) -> list[PaperRevisionActionKind]:
    return sorted(set(actions), key=lambda action: action.value)


def _section_title(section_id: str, findings: list[PaperCriticFinding]) -> str:
    for finding in findings:
        if finding.section_title:
            return finding.section_title
    return "Global" if section_id == "global" else section_id.replace("-", " ").title()


def _main_result_mentions_before_appendix(markdown: str) -> int:
    before_appendix = markdown.split("## Appendix", maxsplit=1)[0].lower()
    return before_appendix.count("main result")


def _normalized(text: str) -> str:
    return " ".join(text.lower().split())


def _latest_commit(commits, action_type: ControllerActionType):
    for commit in reversed(commits):
        if commit.action_type == action_type:
            return commit
    return None


def _read_text_artifact(root: Path, artifact: ArtifactRef) -> str:
    path = root / artifact.path
    if not path.is_file():
        raise PaperCriticError(f"Referenced artifact is missing: {artifact.path}")
    return path.read_text(encoding="utf-8")


def _paper_context_metadata(stage: str) -> dict[str, object]:
    return {
        "stage": stage,
        "artifact_role": "paper_revision_manuscript_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
    }


__all__ = [
    "NON_EXHAUSTIVE_TERMS",
    "PaperCriticError",
    "PaperCriticInputs",
    "PaperCriticRunResult",
    "RETRIEVAL_NOVELTY_CLAIMS",
    "SYNTHETIC_AS_REAL_CLAIMS",
    "build_paper_revision_plan",
    "critique_generated_paper",
    "critique_paper_from_run",
    "load_paper_critic_inputs",
    "write_paper_critic_report",
]
