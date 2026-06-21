from factori.adapters.reviewer_prompts import build_stage_b_reviewer_prompt
from factori.schemas import Candidate, ConstraintSet, DataRequirement


def test_reviewer_prompt_is_deterministic_and_bounded() -> None:
    candidate = _candidate()
    rubric = {"novelty": "Assess novelty risk", "clarity": "Assess precision"}
    context = {"source_count": 3, "is_exhaustive_literature_coverage": False}

    first = build_stage_b_reviewer_prompt(candidate, rubric, context, 4)
    second = build_stage_b_reviewer_prompt(candidate, rubric, context, 4)

    assert first == second
    assert first.candidate_id == candidate.id
    assert first.data_requirement == DataRequirement.SYNTHETIC_ONLY
    assert first.max_objections == 4
    assert "LeanVerified" in first.forbidden_outputs
    assert "RealDataExperimentVerified" in first.forbidden_outputs
    assert "not proof" in first.evidence_boundary_instructions[1]
    assert "requested_output_schema" in first.prompt_text
    assert "exhaustive literature coverage" in first.prompt_text


def _candidate() -> Candidate:
    constraints = ConstraintSet(
        domain="machine learning",
        method="calibration",
        question="Can calibration remain stable under a seeded synthetic shift?",
        data_requirement=DataRequirement.SYNTHETIC_ONLY,
    )
    return Candidate(
        id="candidate-1",
        constraints=constraints,
        domain=constraints.domain,
        method=constraints.method,
        question=constraints.question or "Synthetic calibration question",
        data_requirement=constraints.data_requirement,
    )
