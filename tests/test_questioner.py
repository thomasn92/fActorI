from __future__ import annotations

from typer.testing import CliRunner

from factori.cli import app
from factori.questioner import route_questions_to_action, routed_action, select_questions
from factori.schemas import (
    AutonomyContext,
    Candidate,
    ControllerDecisionAction,
    DataRequirement,
    LiteratureState,
    QuestionCategory,
    ScoreVector,
    StagnationState,
    VerificationState,
)


def test_question_selection_is_stage_dependent() -> None:
    candidate, score, literature, verification = _control_inputs()

    stage_a = select_questions("stage_a", candidate, score, literature, verification)
    stage_c = select_questions("stage_c", candidate, score, literature, verification)

    assert {question.category for question in stage_a} != {
        question.category for question in stage_c
    }
    assert QuestionCategory.DATA_SUFFICIENCY in {question.category for question in stage_a}
    assert QuestionCategory.VERIFICATION_READINESS in {
        question.category for question in stage_c
    }


def test_micro_checks_do_not_include_full_question_bank() -> None:
    candidate, score, literature, verification = _control_inputs()

    questions = select_questions("transition", candidate, score, literature, verification)

    assert len(questions) == 2
    assert {question.category for question in questions} == {QuestionCategory.MICRO_CHECK}


def test_triggered_questions_fire_only_when_present() -> None:
    candidate, score, literature, verification = _control_inputs()

    without_trigger = select_questions("transition", candidate, score, literature, verification)
    with_trigger = select_questions(
        "transition",
        candidate,
        score,
        literature,
        verification,
        triggers={"weak_baseline"},
    )

    assert "trigger-baseline" not in {question.id for question in without_trigger}
    assert "trigger-baseline" in {question.id for question in with_trigger}


def test_weak_data_routes_to_add_synthetic_data_not_human() -> None:
    candidate, score, literature, verification = _control_inputs(
        data_requirement=DataRequirement.SYNTHETIC_ONLY
    )
    questions = select_questions(
        "transition",
        candidate,
        score,
        literature,
        verification,
        triggers={"weak_data"},
    )

    action = route_questions_to_action(questions, candidate, score, literature, verification)

    assert routed_action(action) == ControllerDecisionAction.ADD_SYNTHETIC_DATA


def test_weak_baseline_routes_to_strengthen_baseline_not_human() -> None:
    candidate, score, literature, verification = _control_inputs(reviewer=0.30)
    questions = select_questions(
        "transition",
        candidate,
        score,
        literature,
        verification,
        triggers={"weak_baseline"},
    )

    action = route_questions_to_action(questions, candidate, score, literature, verification)

    assert routed_action(action) == ControllerDecisionAction.STRENGTHEN_BASELINE


def test_overcomplicated_branch_routes_to_simplify_not_human() -> None:
    candidate, score, literature, verification = _control_inputs(difficulty=0.80)
    questions = select_questions(
        "transition",
        candidate,
        score,
        literature,
        verification,
        triggers={"high_complexity"},
    )

    action = route_questions_to_action(questions, candidate, score, literature, verification)

    assert routed_action(action) == ControllerDecisionAction.SIMPLIFY


def test_low_score_and_stagnation_does_not_ask_human_by_default() -> None:
    candidate, score, literature, verification = _control_inputs(
        novelty=0.20,
        feasibility=0.25,
        verifiability=0.20,
    )
    questions = select_questions(
        "transition",
        candidate,
        score,
        literature,
        verification,
        triggers={"stagnation"},
    )
    stagnation_state = StagnationState(
        stagnation_count=3,
        stagnant=True,
        forced_actions=[
            ControllerDecisionAction.SIMPLIFY,
            ControllerDecisionAction.DOWNGRADE_CLAIM,
        ],
    )

    action = route_questions_to_action(
        questions,
        candidate,
        score,
        literature,
        verification,
        stagnation_state=stagnation_state,
    )

    assert routed_action(action) in {
        ControllerDecisionAction.SIMPLIFY,
        ControllerDecisionAction.DOWNGRADE_CLAIM,
        ControllerDecisionAction.CONVERT_TO_NEGATIVE_RESULT,
        ControllerDecisionAction.STOP_FAILURE,
    }


def test_high_uncertainty_high_value_routes_to_human() -> None:
    candidate, score, literature, verification = _control_inputs()
    questions = select_questions("transition", candidate, score, literature, verification)

    action = route_questions_to_action(
        questions,
        candidate,
        score,
        literature,
        verification,
        autonomy_context=AutonomyContext(
            decision_uncertainty=0.90,
            candidate_value=0.90,
        ),
    )

    assert routed_action(action) == ControllerDecisionAction.ASK_HUMAN


def test_external_data_requirement_routes_to_human_or_downgrade() -> None:
    user_candidate, score, literature, verification = _control_inputs(
        data_requirement=DataRequirement.USER_PROVIDED
    )
    public_candidate, public_score, public_literature, public_verification = _control_inputs(
        data_requirement=DataRequirement.PUBLIC_DOWNLOAD
    )
    user_questions = select_questions(
        "transition",
        user_candidate,
        score,
        literature,
        verification,
        triggers={"weak_data"},
    )
    public_questions = select_questions(
        "transition",
        public_candidate,
        public_score,
        public_literature,
        public_verification,
        triggers={"weak_data"},
    )

    user_action = route_questions_to_action(
        user_questions,
        user_candidate,
        score,
        literature,
        verification,
    )
    public_action = route_questions_to_action(
        public_questions,
        public_candidate,
        public_score,
        public_literature,
        public_verification,
    )

    assert routed_action(user_action) == ControllerDecisionAction.ASK_HUMAN
    assert routed_action(public_action) == ControllerDecisionAction.DOWNGRADE_CLAIM


def test_cli_demo_commands_run_successfully(tmp_path) -> None:
    runner = CliRunner()

    questioner = runner.invoke(
        app,
        [
            "questioner-check",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--candidate-id",
            "candidate-1",
        ],
    )
    retrieval = runner.invoke(app, ["retrieval-adequacy-demo"])
    stagnation = runner.invoke(app, ["stagnation-demo"])

    assert questioner.exit_code == 0
    assert "routed_action=" in questioner.output
    assert retrieval.exit_code == 0
    assert "rho_adequacy" in retrieval.output
    assert stagnation.exit_code == 0
    assert "forced_action=" in stagnation.output


def _control_inputs(
    *,
    data_requirement: DataRequirement = DataRequirement.NO_DATA,
    novelty: float = 0.55,
    feasibility: float = 0.65,
    verifiability: float = 0.60,
    reviewer: float = 0.60,
    difficulty: float = 0.45,
) -> tuple[Candidate, ScoreVector, LiteratureState, VerificationState]:
    candidate = Candidate(
        id="candidate-control",
        domain="demo",
        method="demo-method",
        question="Can the control layer route deterministically?",
        data_requirement=data_requirement,
    )
    score = ScoreVector(
        novelty=novelty,
        feasibility=feasibility,
        verifiability=verifiability,
        reviewer=reviewer,
        difficulty=difficulty,
        diversity=0.40,
        uncertainty=0.10,
    )
    literature = LiteratureState(
        semantic=0.60,
        keyword=0.60,
        citation=0.60,
        diversity=0.60,
        adversarial=0.60,
    )
    verification = VerificationState()
    return candidate, score, literature, verification
