from __future__ import annotations

from factori.adapters.llm_prompts import build_stage_a_candidate_prompt
from factori.schemas import ConstraintSet, DataRequirement


def test_stage_a_prompt_contract_is_deterministic() -> None:
    constraints = ConstraintSet(domain="human geography", method="optimal transport")

    first = build_stage_a_candidate_prompt(
        "human geography", "optimal transport", constraints, 4
    )
    second = build_stage_a_candidate_prompt(
        "human geography", "optimal transport", constraints, 4
    )

    assert first == second
    assert first.prompt_text == second.prompt_text


def test_prompt_includes_mvp_data_gate_and_required_schema() -> None:
    contract = build_stage_a_candidate_prompt(
        "machine learning",
        None,
        ConstraintSet(domain="machine learning"),
        3,
    )

    assert contract.mvp_data_gate["allowed"] == [
        DataRequirement.NO_DATA,
        DataRequirement.SYNTHETIC_ONLY,
    ]
    assert contract.mvp_data_gate["deferred"] == [
        DataRequirement.PUBLIC_DOWNLOAD,
        DataRequirement.USER_PROVIDED,
    ]
    candidate_schema = contract.requested_output_schema["properties"]["candidates"][
        "items"
    ]
    assert "data_requirement" in candidate_schema["required"]
    assert contract.max_candidates == 3


def test_prompt_forbids_evidence_and_verification_claims() -> None:
    contract = build_stage_a_candidate_prompt(
        "robust finance",
        "wasserstein robustness",
        ConstraintSet(domain="robust finance", method="wasserstein robustness"),
        2,
    )
    combined = " ".join(
        [
            contract.prompt_text,
            *contract.forbidden_claims,
            *contract.evidence_boundary_instructions,
        ]
    )

    assert "Do not claim LeanVerified" in combined
    assert "Do not invent evidence" in combined
    assert "not verification evidence" in combined
    assert "structured JSON" in combined
