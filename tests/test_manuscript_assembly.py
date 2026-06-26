from __future__ import annotations

from factori.manuscript_assembly import assemble_complete_markdown_draft
from factori.paper_shape import critique_paper_shape
from factori.schemas import (
    ArtifactType,
    Claim,
    ClaimEvidenceLink,
    ClaimTable,
    CompleteMarkdownDraft,
    GeneratedSectionDraft,
    ManuscriptDraftStatus,
    ManuscriptPlan,
    ManuscriptSectionPlan,
    NarrativeManuscriptContract,
    NarrativeSectionRole,
    ProseSafetyReport,
    SectionDraftingResult,
    VerificationLabel,
)


def test_assembled_markdown_includes_expected_section_headings() -> None:
    draft, report = _assemble(_section_results())

    assert report.draft_status == ManuscriptDraftStatus.DRAFT_COMPLETE_WITH_WARNINGS
    for heading in (
        "## Abstract",
        "## Introduction",
        "## Model and Preliminary Results",
        "## Main Result and Derivatives",
        "## Numerical Study",
        "## Empirical Results and Discussion",
        "## Limitations",
        "## Conclusion",
        "## Appendix",
    ):
        assert heading in draft.markdown


def test_assembled_markdown_includes_claim_evidence_and_provenance_appendices() -> None:
    draft, _report = _assemble(_section_results())

    assert "## Claim/Evidence Appendix" in draft.markdown
    assert "`claim-main` (Conjecture): evidence artifacts: evidence-a" in draft.markdown
    assert "## Provenance Appendix" in draft.markdown
    assert "- Draft artifacts are presentation/context only." in draft.markdown


def test_empirical_section_is_marked_unavailable_when_empirical_evidence_absent() -> None:
    draft, report = _assemble(_section_results())

    assert "Empirical results are unavailable" in draft.markdown
    assert any("Empirical results are unavailable" in warning for warning in report.warnings)


def test_unsafe_section_is_omitted_or_marked_in_markdown() -> None:
    results = [
        _section_result(
            section_id="introduction",
            title="Introduction",
            safe=False,
            reasons=["unknown or disallowed claim IDs: claim-x"],
        )
    ]

    draft, report = _assemble(results)

    assert report.draft_status == ManuscriptDraftStatus.DRAFT_INCOMPLETE_UNSAFE_SECTIONS
    assert report.unsafe_section_ids == ["introduction"]
    assert "[UNSAFE SECTION OMITTED]" in draft.markdown
    assert "claim-x" in draft.markdown


def test_complete_markdown_draft_is_not_verification_evidence() -> None:
    draft, report = _assemble(_section_results())

    assert isinstance(draft, CompleteMarkdownDraft)
    assert not draft.is_verification_evidence
    assert not draft.creates_scientific_validation
    assert not report.is_verification_evidence
    assert not report.creates_scientific_validation


def test_manuscript_assembly_does_not_mutate_claim_table() -> None:
    claim_table = _claim_table()
    before = claim_table.model_dump(mode="json")

    assemble_complete_markdown_draft(
        run_id="run-1",
        manuscript_plan=_manuscript_plan(),
        narrative_contract=_narrative_contract(),
        paper_shape_critique=critique_paper_shape(
            _narrative_contract(),
            _manuscript_plan(),
        ),
        claim_table=claim_table,
        section_results=_section_results(),
    )

    assert claim_table.model_dump(mode="json") == before


def test_manuscript_assembly_is_deterministic() -> None:
    first = _assemble(_section_results())
    second = _assemble(_section_results())

    assert first == second


def test_draft_status_fails_when_no_sections_are_available() -> None:
    draft, report = _assemble([])

    assert report.draft_status == ManuscriptDraftStatus.DRAFT_FAILED
    assert draft.unsafe_section_ids == []


def _assemble(
    results: list[SectionDraftingResult],
) -> tuple[CompleteMarkdownDraft, object]:
    narrative = _narrative_contract()
    plan = _manuscript_plan()
    return assemble_complete_markdown_draft(
        run_id="run-1",
        manuscript_plan=plan,
        narrative_contract=narrative,
        paper_shape_critique=critique_paper_shape(narrative, plan),
        claim_table=_claim_table(),
        section_results=results,
    )


def _section_results() -> list[SectionDraftingResult]:
    return [
        _section_result("introduction", "Introduction"),
        _section_result("problem-setup", "Problem Setup"),
        _section_result("results", "Results"),
        _section_result("limitations", "Limitations"),
        _section_result("conclusion", "Conclusion"),
        _section_result("appendix", "Appendix"),
    ]


def _section_result(
    section_id: str,
    title: str,
    *,
    safe: bool = True,
    reasons: list[str] | None = None,
) -> SectionDraftingResult:
    draft = GeneratedSectionDraft(
        section_id=section_id,
        title=title,
        content=f"[FAKE PROSE DRAFT] section_id={section_id}; No label is upgraded.",
        claim_ids=[],
        used_claim_ids=[],
        used_evidence_artifact_ids=[],
        used_citation_ids=[],
        warnings=["Fake prose draft is a deterministic placeholder."],
    )
    safety = ProseSafetyReport(
        section_id=section_id,
        safe=safe,
        rejected=not safe,
        reasons=reasons or [],
        warnings=draft.warnings,
        used_claim_ids=[],
        used_evidence_artifact_ids=[],
        used_citation_ids=[],
    )
    return SectionDraftingResult(
        section_id=section_id,
        section_title=title,
        section_role=title,
        narrative_role=[],
        draft_markdown=draft.content if safe else "",
        used_claim_ids=[],
        used_evidence_artifact_ids=[],
        used_citation_ids=[],
        safety_status="Safe" if safe else "Unsafe",
        warnings=draft.warnings,
        unsupported_sentences=[],
        source_contract_hashes={"claim_table": "0" * 64},
        safe=safe,
        rejected=not safe,
        safety_reasons=reasons or [],
        draft=draft,
        safety_report=safety,
    )


def _manuscript_plan() -> ManuscriptPlan:
    return ManuscriptPlan(
        plan_id="manuscript-plan-final",
        final_nucleus_id="final",
        nucleus_type="BranchNucleus",
        title="Deterministic Test Manuscript",
        sections=[
            ManuscriptSectionPlan(
                section_id="introduction",
                title="Introduction",
                bullets=["Frame the problem."],
                allowed_claim_ids=["claim-main"],
                narrative_roles=[
                    NarrativeSectionRole.PROBLEM_FRAMING,
                    NarrativeSectionRole.BACKGROUND_LITERATURE_POSITIONING,
                ],
            ),
            ManuscriptSectionPlan(
                section_id="problem-setup",
                title="Problem Setup",
                bullets=["Define objects."],
                narrative_roles=[NarrativeSectionRole.MODEL_FRAME],
            ),
            ManuscriptSectionPlan(
                section_id="results",
                title="Results",
                bullets=["State bounded results."],
                narrative_roles=[NarrativeSectionRole.MAIN_BODY_RESULT],
            ),
            ManuscriptSectionPlan(
                section_id="limitations",
                title="Limitations",
                bullets=["Preserve limits."],
                narrative_roles=[NarrativeSectionRole.LIMITATIONS_DISCUSSION],
            ),
            ManuscriptSectionPlan(
                section_id="conclusion",
                title="Conclusion",
                bullets=["Conclude."],
                narrative_roles=[NarrativeSectionRole.LIMITATIONS_DISCUSSION],
            ),
            ManuscriptSectionPlan(
                section_id="appendix",
                title="Appendix",
                bullets=["Appendix details."],
                narrative_roles=[NarrativeSectionRole.APPENDIX_ONLY_PROOF],
            ),
        ],
        allowed_claim_ids=["claim-main"],
        blocked_claim_ids=[],
    )


def _claim_table() -> ClaimTable:
    return ClaimTable(
        final_nucleus_id="final",
        claims=[
            Claim(
                claim_id="claim-main",
                claim_text="The example remains bounded by its label.",
                claim_label=VerificationLabel.CONJECTURE,
                candidate_id="candidate-a",
                evidence_artifact_ids=["evidence-a"],
                evidence_types=["proof"],
                allowed_in_main_text=True,
                allowed_section="Introduction",
                reason="test",
            )
        ],
        evidence_links=[
            ClaimEvidenceLink(
                claim_id="claim-main",
                artifact_id="evidence-a",
                artifact_type=ArtifactType.LEAN,
                evidence_role="proof",
                supports_label=False,
            )
        ],
    )


def _narrative_contract() -> NarrativeManuscriptContract:
    return NarrativeManuscriptContract(
        contract_id="narrative-contract",
        run_id="run-1",
        final_nucleus_id="final",
        central_message="A bounded deterministic example.",
        problem_statement="State the problem.",
        why_interesting="It clarifies deterministic boundaries.",
        literature_gap="Complete coverage is not claimed.",
        novelty_claim="Novelty is bounded by the claim table.",
        model_frame="Use a simple model.",
        notation_policy="Use minimal notation.",
        main_result_id="claim-main",
        main_result_in_words="The example remains bounded by its label.",
        appendix_policy="Move details to the appendix.",
    )
