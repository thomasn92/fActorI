from __future__ import annotations

import json

from typer.testing import CliRunner

from factori.cli import app
from factori.narrative_contract import build_narrative_contract
from factori.paper_shape import critique_paper_shape
from factori.schemas import (
    Claim,
    ClaimTable,
    FinalNucleus,
    FinalNucleusType,
    MainMessageAssessment,
    ManuscriptPlan,
    ManuscriptSectionPlan,
    NarrativeSectionRole,
    PaperShapeCritique,
    PaperShapeScore,
    PaperShapeStatus,
    VerificationLabel,
)


def test_paper_shape_critic_models_are_importable() -> None:
    assert PaperShapeCritique.__name__ == "PaperShapeCritique"
    assert PaperShapeScore.__name__ == "PaperShapeScore"
    assert MainMessageAssessment.__name__ == "MainMessageAssessment"


def test_paper_shape_score_is_deterministic() -> None:
    contract = _contract()

    first = critique_paper_shape(contract, _plan())
    second = critique_paper_shape(contract, _plan())

    assert first == second
    assert first.status in {
        PaperShapeStatus.PAPER_SHAPED,
        PaperShapeStatus.PAPER_SHAPED_WITH_WARNINGS,
    }


def test_central_message_missing_lowers_score() -> None:
    strong = critique_paper_shape(_contract(), _plan())
    weak = critique_paper_shape(
        _contract().model_copy(update={"central_message": ""}),
        _plan(),
    )

    assert weak.score.central_message < strong.score.central_message
    assert "central message is missing" in weak.warnings


def test_missing_problem_statement_lowers_score() -> None:
    strong = critique_paper_shape(_contract(), _plan())
    weak = critique_paper_shape(
        _contract().model_copy(update={"problem_statement": ""}),
        _plan(),
    )

    assert weak.score.problem_framing < strong.score.problem_framing
    assert "missing problem statement" in weak.missing_items


def test_missing_literature_positioning_lowers_score() -> None:
    weak = critique_paper_shape(
        _contract().model_copy(update={"literature_gap": ""}),
        _plan(),
    )

    assert weak.score.literature_positioning < 1.0
    assert "literature gap or positioning is missing" in weak.warnings


def test_missing_main_result_in_words_lowers_score() -> None:
    weak = critique_paper_shape(
        _contract().model_copy(update={"main_result_in_words": ""}),
        _plan(),
    )

    assert weak.score.main_result_focus < 1.0
    assert "main result is not stated in prose" in weak.warnings


def test_multiple_primary_main_results_lower_score() -> None:
    plan = _plan(
        main_claim_ids=["claim-1", "claim-2"],
    )
    critique = critique_paper_shape(_contract(plan=plan), plan)

    assert critique.main_result_assessment.primary_main_results == 2
    assert "multiple primary main results appear in the main body" in critique.warnings


def test_technical_lemmas_in_main_body_trigger_appendix_warning() -> None:
    plan = _plan(include_main_lemma=True)
    critique = critique_paper_shape(_contract(plan=plan), plan)

    assert critique.appendix_allocation_assessment.technical_lemmas_in_main_body == 1
    assert "technical material should move from main body to appendix" in critique.warnings


def test_numerics_without_purpose_trigger_warning() -> None:
    critique = critique_paper_shape(
        _contract().model_copy(update={"numerical_study_purpose": ""}),
        _plan(),
    )

    assert "numerical or synthetic study section lacks a declared purpose" in critique.warnings


def test_synthetic_claims_are_not_treated_as_empirical_validation() -> None:
    plan = _plan()
    claim_table = ClaimTable(
        final_nucleus_id="nucleus-1",
        claims=[_claim("claim-1", VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED)],
    )
    contract = build_narrative_contract(plan, _final_nucleus(), claim_table, run_id="run-1")
    critique = critique_paper_shape(contract, plan)

    assert not any("real-world validation" in warning for warning in critique.warnings)
    assert "Synthetic evidence supports only controlled" in contract.synthetic_study_boundary


def test_empirical_section_without_boundary_triggers_warning() -> None:
    critique = critique_paper_shape(
        _contract().model_copy(update={"empirical_study_boundary": ""}),
        _plan(),
    )

    assert "empirical section lacks an empirical boundary statement" in critique.warnings


def test_strong_narrative_score_does_not_create_scientific_validation() -> None:
    critique = critique_paper_shape(_contract(), _plan())

    assert critique.creates_scientific_validation is False
    assert critique.is_verification_evidence is False


def test_narrative_score_cannot_create_proof_or_experiment_evidence() -> None:
    critique = critique_paper_shape(_contract(), _plan())

    dumped = json.dumps(critique.model_dump(mode="json"), sort_keys=True)
    assert "LeanVerified" not in dumped
    assert "SyntheticExperimentVerified" not in dumped
    assert critique.is_verification_evidence is False


def test_critique_paper_shape_cli_works(tmp_path) -> None:
    runner = CliRunner()
    setup = runner.invoke(
        app,
        [
            "run-all",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--domain",
            "human geography",
            "--stop-after",
            "plan-manuscript",
        ],
    )
    assert setup.exit_code == 0, setup.output

    result = runner.invoke(
        app,
        [
            "critique-paper-shape",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "paper_shape_status=" in result.output
    assert "is_verification_evidence=false" in result.output


def test_critique_paper_shape_json_and_write_report(tmp_path) -> None:
    runner = CliRunner()
    setup = runner.invoke(
        app,
        [
            "run-all",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-2",
            "--domain",
            "human geography",
            "--stop-after",
            "plan-manuscript",
        ],
    )
    assert setup.exit_code == 0, setup.output

    result = runner.invoke(
        app,
        [
            "critique-paper-shape",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-2",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["critique"]["is_verification_evidence"] is False
    assert payload["critique"]["creates_scientific_validation"] is False
    assert payload["artifacts"]["narrative_contract"]["metadata"][
        "is_verification_evidence"
    ] is False
    assert payload["artifacts"]["paper_shape_critique"]["content_hash"]
    assert (tmp_path / "runs" / "run-2" / "reports" / "paper-shape-critique.md").exists()


def _contract(plan: ManuscriptPlan | None = None):
    plan = plan or _plan()
    return build_narrative_contract(
        plan,
        _final_nucleus(),
        _claim_table(plan),
        run_id="run-1",
    )


def _final_nucleus() -> FinalNucleus:
    return FinalNucleus(
        id="nucleus-1",
        nucleus_type=FinalNucleusType.BRANCH_NUCLEUS,
        candidate_id="candidate-1",
        supporting_candidate_ids=["candidate-1"],
        labels_by_candidate={"candidate-1": VerificationLabel.CONJECTURE},
        reason="The branch gives a focused manuscript nucleus.",
    )


def _claim(
    claim_id: str,
    label: VerificationLabel = VerificationLabel.CONJECTURE,
) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim_text=f"Claim {claim_id} is a bounded conjectural paper result.",
        claim_label=label,
        candidate_id="candidate-1",
        evidence_artifact_ids=["fake-evidence"],
        evidence_types=["lean" if label == VerificationLabel.CONJECTURE else "experiment"],
        allowed_in_main_text=True,
        allowed_section="Theory",
        reason="Allowed for narrative-shape tests.",
    )


def _claim_table(plan: ManuscriptPlan) -> ClaimTable:
    claim_ids = sorted(
        {claim_id for section in plan.sections for claim_id in section.allowed_claim_ids}
    )
    claims = [_claim(claim_id) for claim_id in claim_ids] or [_claim("claim-1")]
    return ClaimTable(final_nucleus_id="nucleus-1", claims=claims)


def _plan(
    *,
    main_claim_ids: list[str] | None = None,
    include_main_lemma: bool = False,
) -> ManuscriptPlan:
    main_claim_ids = main_claim_ids or ["claim-1"]
    sections = [
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
            section_id="model",
            title="Model and Preliminary Results",
            bullets=["Define model."],
            narrative_roles=[NarrativeSectionRole.MODEL_FRAME],
        ),
        ManuscriptSectionPlan(
            section_id="main-result",
            title="Main Result",
            bullets=["State one result."],
            allowed_claim_ids=main_claim_ids,
            narrative_roles=[NarrativeSectionRole.MAIN_BODY_RESULT],
        ),
        ManuscriptSectionPlan(
            section_id="numerical-study",
            title="Numerical Study",
            bullets=["Explain numerical purpose."],
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
            bullets=["Allocate proofs."],
            narrative_roles=[NarrativeSectionRole.APPENDIX_ONLY_PROOF],
        ),
    ]
    if include_main_lemma:
        sections.insert(
            4,
            ManuscriptSectionPlan(
                section_id="technical-lemma",
                title="Technical Lemma",
                bullets=["Too much technical detail."],
                allowed_claim_ids=["claim-lemma"],
                narrative_roles=[NarrativeSectionRole.TECHNICAL_LEMMA],
            ),
        )
    return ManuscriptPlan(
        plan_id="plan-1",
        final_nucleus_id="nucleus-1",
        nucleus_type=FinalNucleusType.BRANCH_NUCLEUS,
        title="Paper Shape Test Plan",
        sections=sections,
        allowed_claim_ids=main_claim_ids,
        blocked_claim_ids=[],
    )
