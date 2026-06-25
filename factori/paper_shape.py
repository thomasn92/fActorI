"""Deterministic paper-shape critic for manuscript-quality diagnostics."""

from __future__ import annotations

from dataclasses import dataclass

from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.reports import render_paper_shape_critique_markdown
from factori.schemas import (
    AppendixAllocationAssessment,
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    EmpiricalBoundaryAssessment,
    LiteraturePositioningAssessment,
    MainMessageAssessment,
    MainResultAssessment,
    ManuscriptPlan,
    ModelNotationAssessment,
    NarrativeManuscriptContract,
    NarrativeSectionRole,
    NumericalStudyAssessment,
    PaperShapeCritique,
    PaperShapeScore,
    PaperShapeStatus,
)


@dataclass(frozen=True)
class PaperShapeReportArtifacts:
    """Artifacts written by an explicit paper-shape critique report request."""

    narrative_contract_artifact: ArtifactRef
    critique_json_artifact: ArtifactRef
    critique_markdown_artifact: ArtifactRef


def critique_paper_shape(
    contract: NarrativeManuscriptContract,
    manuscript_plan: ManuscriptPlan,
    draft_or_paper=None,
) -> PaperShapeCritique:
    """Critique whether the planned manuscript is shaped like a focused paper."""
    del draft_or_paper
    central = _central_message_assessment(contract)
    problem_framing = _problem_framing_score(contract)
    literature = _literature_assessment(contract)
    model = _model_assessment(contract)
    main_result = _main_result_assessment(contract, manuscript_plan)
    numerical = _numerical_assessment(contract, manuscript_plan)
    empirical = _empirical_assessment(contract, manuscript_plan)
    appendix = _appendix_assessment(contract, manuscript_plan)
    score = PaperShapeScore(
        central_message=central.score,
        problem_framing=problem_framing,
        literature_positioning=literature.score,
        model_clarity=model.score,
        main_result_focus=main_result.score,
        numerics_purpose=numerical.score,
        empirical_boundary=empirical.score,
        appendix_allocation=appendix.score,
        total=round(
            0.20 * central.score
            + 0.15 * problem_framing
            + 0.15 * literature.score
            + 0.15 * model.score
            + 0.15 * main_result.score
            + 0.10 * numerical.score
            + 0.05 * empirical.score
            + 0.05 * appendix.score,
            6,
        ),
    )
    missing_items = sorted(set(_missing_items(contract, manuscript_plan)))
    warnings = sorted(
        set(
            [
                *central.warnings,
                *literature.warnings,
                *model.warnings,
                *main_result.warnings,
                *numerical.warnings,
                *empirical.warnings,
                *appendix.warnings,
            ]
        )
    )
    return PaperShapeCritique(
        critique_id=f"paper-shape-critique-{contract.contract_id}",
        run_id=contract.run_id,
        contract_id=contract.contract_id,
        status=_status(score.total, warnings, missing_items),
        score=score,
        central_message_assessment=central,
        literature_positioning_assessment=literature,
        model_notation_assessment=model,
        main_result_assessment=main_result,
        numerical_study_assessment=numerical,
        empirical_boundary_assessment=empirical,
        appendix_allocation_assessment=appendix,
        missing_items=missing_items,
        warnings=warnings,
        recommended_structural_edits=_recommended_edits(missing_items, warnings),
    )


def write_paper_shape_reports(
    *,
    run_id: str,
    contract: NarrativeManuscriptContract,
    critique: PaperShapeCritique,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> PaperShapeReportArtifacts:
    """Write non-evidence manuscript-quality reports through artifact provenance."""
    store.init_run(run_id)
    contract_ref = store.write_json(
        run_id=run_id,
        artifact_id="narrative-contract",
        artifact_type=ArtifactType.REPORT,
        data=contract,
        metadata={
            "stage": "paper_shape_critique",
            "artifact_role": "manuscript_quality_context",
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
        },
    )
    contract_commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.NARRATIVE_CONTRACT_WRITTEN,
        payload=contract.model_dump(mode="json"),
        artifact_refs=[contract_ref],
    )
    contract_ref = store.link_artifact_to_commit(contract_ref, contract_commit.commit_hash)
    critique_json_ref = store.write_json(
        run_id=run_id,
        artifact_id="paper-shape-critique",
        artifact_type=ArtifactType.REPORT,
        data=critique,
        metadata={
            "stage": "paper_shape_critique",
            "artifact_role": "manuscript_quality_context",
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
        },
    )
    critique_markdown_ref = store.write_markdown(
        run_id=run_id,
        artifact_id="paper-shape-critique",
        artifact_type=ArtifactType.REPORT,
        markdown=render_paper_shape_critique_markdown(contract, critique),
        metadata={
            "stage": "paper_shape_critique",
            "artifact_role": "manuscript_quality_context",
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
        },
    )
    critique_commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.PAPER_SHAPE_CRITIQUE_WRITTEN,
        payload={
            "critique_id": critique.critique_id,
            "status": critique.status.value,
            "score": critique.score.total,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
        },
        artifact_refs=[critique_json_ref, critique_markdown_ref],
    )
    critique_json_ref = store.link_artifact_to_commit(
        critique_json_ref,
        critique_commit.commit_hash,
    )
    critique_markdown_ref = store.link_artifact_to_commit(
        critique_markdown_ref,
        critique_commit.commit_hash,
    )
    return PaperShapeReportArtifacts(
        narrative_contract_artifact=contract_ref,
        critique_json_artifact=critique_json_ref,
        critique_markdown_artifact=critique_markdown_ref,
    )


def _central_message_assessment(contract: NarrativeManuscriptContract) -> MainMessageAssessment:
    warnings: list[str] = []
    findings: list[str] = []
    message = contract.central_message.strip()
    if not message:
        warnings.append("central message is missing")
        score = 0.0
    elif len(message.split()) > 45:
        warnings.append("central message is too long to function as one paper message")
        score = 0.65
    else:
        findings.append("central message is present and bounded")
        score = 1.0
    return MainMessageAssessment(
        score=score,
        passed=score >= 0.75,
        findings=findings,
        warnings=warnings,
    )


def _problem_framing_score(contract: NarrativeManuscriptContract) -> float:
    present = [
        bool(contract.problem_statement.strip()),
        bool(contract.why_interesting.strip()),
    ]
    return round(sum(present) / 2.0, 6)


def _literature_assessment(
    contract: NarrativeManuscriptContract,
) -> LiteraturePositioningAssessment:
    warnings: list[str] = []
    score = 1.0
    if not contract.literature_gap.strip():
        warnings.append("literature gap or positioning is missing")
        score -= 0.5
    if not contract.novelty_claim.strip():
        warnings.append("bounded novelty claim is missing")
        score -= 0.5
    return LiteraturePositioningAssessment(
        score=round(max(0.0, score), 6),
        passed=score >= 0.75,
        findings=[] if warnings else ["bounded literature positioning is present"],
        warnings=warnings,
    )


def _model_assessment(contract: NarrativeManuscriptContract) -> ModelNotationAssessment:
    warnings: list[str] = []
    score = 1.0
    if not contract.model_frame.strip():
        warnings.append("simple model frame is missing")
        score -= 0.5
    if not contract.notation_policy.strip():
        warnings.append("notation policy is missing")
        score -= 0.5
    return ModelNotationAssessment(
        score=round(max(0.0, score), 6),
        passed=score >= 0.75,
        findings=[] if warnings else ["model frame and notation policy are present"],
        warnings=warnings,
    )


def _main_result_assessment(
    contract: NarrativeManuscriptContract,
    manuscript_plan: ManuscriptPlan,
) -> MainResultAssessment:
    primary_count = _primary_main_result_count(manuscript_plan)
    warnings: list[str] = []
    score = 1.0
    if not contract.main_result_in_words.strip():
        warnings.append("main result is not stated in prose")
        score -= 0.45
    if primary_count == 0:
        warnings.append("no primary main-result slot is present in the main body")
        score -= 0.25
    if primary_count > 1:
        warnings.append("multiple primary main results appear in the main body")
        score -= 0.45
    if _technical_lemmas_in_main_body(manuscript_plan):
        warnings.append("technical lemmas appear in the main body")
        score -= 0.20
    return MainResultAssessment(
        score=round(max(0.0, score), 6),
        passed=score >= 0.75,
        primary_main_results=primary_count,
        findings=[] if warnings else ["one main result is visible in the main body"],
        warnings=warnings,
    )


def _numerical_assessment(
    contract: NarrativeManuscriptContract,
    manuscript_plan: ManuscriptPlan,
) -> NumericalStudyAssessment:
    numerics_present = _has_role(manuscript_plan, NarrativeSectionRole.NUMERICAL_VALIDATION)
    warnings: list[str] = []
    if numerics_present and not contract.numerical_study_purpose.strip():
        warnings.append("numerical or synthetic study section lacks a declared purpose")
        score = 0.0
    else:
        score = 1.0
    return NumericalStudyAssessment(
        score=score,
        passed=score >= 0.75,
        numerics_present=numerics_present,
        findings=[] if warnings else ["numerical study purpose is adequate or not applicable"],
        warnings=warnings,
    )


def _empirical_assessment(
    contract: NarrativeManuscriptContract,
    manuscript_plan: ManuscriptPlan,
) -> EmpiricalBoundaryAssessment:
    empirical_present = _has_role(manuscript_plan, NarrativeSectionRole.EMPIRICAL_DISCUSSION)
    warnings: list[str] = []
    score = 1.0
    if empirical_present and not contract.empirical_study_boundary.strip():
        warnings.append("empirical section lacks an empirical boundary statement")
        score -= 0.7
    lowered = " ".join(
        [
            contract.synthetic_study_boundary,
            contract.empirical_study_boundary,
            contract.novelty_claim,
        ]
    ).lower()
    if "synthetic" in lowered and (
        "real-world validation" in lowered or "real world validation" in lowered
    ):
        warnings.append("synthetic evidence is framed as real-world validation")
        score = 0.0
    return EmpiricalBoundaryAssessment(
        score=round(max(0.0, score), 6),
        passed=score >= 0.75,
        empirical_section_present=empirical_present,
        findings=[] if warnings else ["synthetic and empirical boundaries are preserved"],
        warnings=warnings,
    )


def _appendix_assessment(
    contract: NarrativeManuscriptContract,
    manuscript_plan: ManuscriptPlan,
) -> AppendixAllocationAssessment:
    technical_count = _technical_lemmas_in_main_body(manuscript_plan)
    has_appendix = any(
        section.title.lower().startswith("appendix")
        for section in manuscript_plan.sections
    )
    warnings: list[str] = []
    score = 1.0
    if not has_appendix:
        warnings.append("appendix section is missing")
        score -= 0.5
    if not contract.appendix_policy.strip():
        warnings.append("appendix policy is missing")
        score -= 0.25
    if technical_count:
        warnings.append("technical material should move from main body to appendix")
        score -= 0.5
    return AppendixAllocationAssessment(
        score=round(max(0.0, score), 6),
        passed=score >= 0.75,
        technical_lemmas_in_main_body=technical_count,
        findings=[] if warnings else ["appendix allocation is explicit"],
        warnings=warnings,
    )


def _missing_items(
    contract: NarrativeManuscriptContract,
    manuscript_plan: ManuscriptPlan,
) -> list[str]:
    items = list(contract.blocked_or_missing_items)
    if not contract.problem_statement.strip():
        items.append("missing problem statement")
    if not contract.why_interesting.strip():
        items.append("missing why-interesting explanation")
    if not contract.literature_gap.strip():
        items.append("missing literature positioning")
    if not contract.main_result_in_words.strip():
        items.append("missing main result in words")
    if not _has_discussion_section(manuscript_plan):
        items.append("missing discussion section")
    return items


def _recommended_edits(missing_items: list[str], warnings: list[str]) -> list[str]:
    edits: list[str] = []
    for item in missing_items:
        if "problem" in item:
            edits.append("Add a problem-framing paragraph before technical material.")
        elif "literature" in item:
            edits.append("State the closest prior-work gap and bound the novelty claim.")
        elif "main result" in item:
            edits.append("Select one main result and state it in prose before details.")
        elif "discussion" in item:
            edits.append("Add a discussion or empirical-boundary paragraph.")
    for warning in warnings:
        if "technical" in warning or "appendix" in warning:
            edits.append(
                "Move technical lemmas, proof details, and secondary propositions to the appendix."
            )
        if "synthetic" in warning and "real-world" in warning:
            edits.append("Rewrite synthetic claims so they do not imply real-world validation.")
        if "multiple primary" in warning:
            edits.append(
                "Demote secondary results to derivatives, corollaries, or appendix material."
            )
    return sorted(set(edits))


def _status(
    total: float,
    warnings: list[str],
    missing_items: list[str],
) -> PaperShapeStatus:
    if total >= 0.85 and not warnings and not missing_items:
        return PaperShapeStatus.PAPER_SHAPED
    if total >= 0.70:
        return PaperShapeStatus.PAPER_SHAPED_WITH_WARNINGS
    if total >= 0.50:
        return PaperShapeStatus.PAPER_SHAPE_WEAK
    return PaperShapeStatus.NOT_PAPER_SHAPED


def _primary_main_result_count(manuscript_plan: ManuscriptPlan) -> int:
    count = 0
    for section in manuscript_plan.sections:
        roles = set(section.narrative_roles)
        title = section.title.lower()
        if NarrativeSectionRole.MAIN_BODY_RESULT in roles or "main result" in title:
            count += len(section.allowed_claim_ids) or 1
    return count


def _technical_lemmas_in_main_body(manuscript_plan: ManuscriptPlan) -> int:
    count = 0
    for section in manuscript_plan.sections:
        if section.title.lower().startswith("appendix"):
            continue
        roles = set(section.narrative_roles)
        title = section.title.lower()
        if NarrativeSectionRole.TECHNICAL_LEMMA in roles or "lemma" in title or "proof" in title:
            count += max(1, len(section.allowed_claim_ids))
    return count


def _has_role(manuscript_plan: ManuscriptPlan, role: NarrativeSectionRole) -> bool:
    return any(role in section.narrative_roles for section in manuscript_plan.sections)


def _has_discussion_section(manuscript_plan: ManuscriptPlan) -> bool:
    return any(
        "discussion" in section.title.lower() or section.title == "Limitations"
        for section in manuscript_plan.sections
    )


__all__ = [
    "PaperShapeReportArtifacts",
    "critique_paper_shape",
    "write_paper_shape_reports",
]
