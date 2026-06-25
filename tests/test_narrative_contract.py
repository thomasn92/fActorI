from __future__ import annotations

from factori.narrative_contract import build_narrative_contract, infer_narrative_roles
from factori.schemas import (
    Claim,
    ClaimTable,
    FinalNucleus,
    FinalNucleusType,
    ManuscriptPlan,
    ManuscriptSectionPlan,
    NarrativeManuscriptContract,
    NarrativeSectionRole,
    VerificationLabel,
)


def test_narrative_contract_models_are_importable() -> None:
    assert NarrativeManuscriptContract.__name__ == "NarrativeManuscriptContract"


def test_narrative_contract_can_be_built_from_simple_manuscript_plan() -> None:
    contract = build_narrative_contract(
        _plan(),
        _final_nucleus(),
        _claim_table(),
        run_id="run-1",
    )

    assert contract.run_id == "run-1"
    assert contract.final_nucleus_id == "nucleus-1"
    assert contract.central_message
    assert contract.problem_statement
    assert contract.literature_gap
    assert contract.main_result_id == "claim-1"
    assert contract.is_verification_evidence is False
    assert contract.creates_scientific_validation is False
    assert contract.section_plan[0]["roles"] == [NarrativeSectionRole.CENTRAL_MESSAGE.value]


def test_narrative_contract_is_deterministic() -> None:
    first = build_narrative_contract(_plan(), _final_nucleus(), _claim_table(), run_id="run-1")
    second = build_narrative_contract(_plan(), _final_nucleus(), _claim_table(), run_id="run-1")

    assert first == second


def test_section_role_inference_is_deterministic() -> None:
    assert infer_narrative_roles("Empirical Results and Discussion") == [
        NarrativeSectionRole.MAIN_BODY_RESULT,
        NarrativeSectionRole.EMPIRICAL_DISCUSSION,
    ]


def _final_nucleus() -> FinalNucleus:
    return FinalNucleus(
        id="nucleus-1",
        nucleus_type=FinalNucleusType.BRANCH_NUCLEUS,
        candidate_id="candidate-1",
        supporting_candidate_ids=["candidate-1"],
        labels_by_candidate={"candidate-1": VerificationLabel.CONJECTURE},
        reason="The branch gives the cleanest labeled manuscript nucleus.",
    )


def _claim_table() -> ClaimTable:
    return ClaimTable(
        final_nucleus_id="nucleus-1",
        claims=[
            Claim(
                claim_id="claim-1",
                claim_text="A bounded graph-curvature model yields a conjectural diagnostic.",
                claim_label=VerificationLabel.CONJECTURE,
                candidate_id="candidate-1",
                evidence_artifact_ids=["fake-proof-candidate-1"],
                evidence_types=["lean"],
                allowed_in_main_text=True,
                allowed_section="Theory",
                reason="Conjectures may appear in theory with explicit labels.",
            )
        ],
    )


def _plan() -> ManuscriptPlan:
    return ManuscriptPlan(
        plan_id="plan-1",
        final_nucleus_id="nucleus-1",
        nucleus_type=FinalNucleusType.BRANCH_NUCLEUS,
        title="Narrative Test Plan",
        allowed_claim_ids=["claim-1"],
        blocked_claim_ids=[],
        sections=[
            ManuscriptSectionPlan(
                section_id="abstract",
                title="Abstract",
                bullets=["State the central message."],
                narrative_roles=[NarrativeSectionRole.CENTRAL_MESSAGE],
            ),
            ManuscriptSectionPlan(
                section_id="introduction",
                title="Introduction",
                bullets=["Frame problem and gap."],
                narrative_roles=[
                    NarrativeSectionRole.PROBLEM_FRAMING,
                    NarrativeSectionRole.BACKGROUND_LITERATURE_POSITIONING,
                ],
            ),
            ManuscriptSectionPlan(
                section_id="problem-setup",
                title="Problem Setup",
                bullets=["Define model."],
                narrative_roles=[NarrativeSectionRole.MODEL_FRAME],
            ),
            ManuscriptSectionPlan(
                section_id="main-result",
                title="Main Result",
                bullets=["State one result."],
                allowed_claim_ids=["claim-1"],
                narrative_roles=[NarrativeSectionRole.MAIN_BODY_RESULT],
            ),
            ManuscriptSectionPlan(
                section_id="numerical-study",
                title="Numerical Study",
                bullets=["Illustrate boundary behavior."],
                narrative_roles=[NarrativeSectionRole.NUMERICAL_VALIDATION],
            ),
            ManuscriptSectionPlan(
                section_id="empirical-discussion",
                title="Empirical Results and Discussion",
                bullets=["Discuss empirical boundary."],
                narrative_roles=[NarrativeSectionRole.EMPIRICAL_DISCUSSION],
            ),
            ManuscriptSectionPlan(
                section_id="appendix",
                title="Appendix",
                bullets=["Proof details."],
                narrative_roles=[NarrativeSectionRole.APPENDIX_ONLY_PROOF],
            ),
        ],
    )
