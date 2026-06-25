from __future__ import annotations

import json

import pytest

from factori.adapters.llm_safety import (
    LLMCandidateResponseError,
    parse_llm_candidate_response,
    parse_llm_candidate_response_with_report,
    validate_llm_candidate,
)
from factori.schemas import BranchStatus, DataRequirement
from factori.stage_a import apply_mvp_data_gate


def test_parser_accepts_valid_structured_candidate_response() -> None:
    raw = {"candidates": [_candidate()]}

    first = parse_llm_candidate_response(raw)
    second = parse_llm_candidate_response(json.dumps(raw))

    assert first == second
    assert len(first) == 1
    assert first[0].data_requirement == DataRequirement.NO_DATA
    assert first[0].symbolic_state["llm_proposed"] is True
    assert first[0].symbolic_state["fake"] is False


def test_parser_records_passed_backend_and_provider_metadata() -> None:
    candidate = parse_llm_candidate_response(
        {"candidates": [_candidate()]},
        backend="provider-neutral-backend",
        provider="provider-neutral-provider",
    )[0]

    assert candidate.symbolic_state["adapter_backend"] == "provider-neutral-backend"
    assert candidate.symbolic_state["adapter_provider"] == "provider-neutral-provider"


def test_parser_rejects_malformed_response() -> None:
    with pytest.raises(LLMCandidateResponseError, match="not valid JSON"):
        parse_llm_candidate_response("not-json")

    with pytest.raises(LLMCandidateResponseError, match="candidates list"):
        parse_llm_candidate_response({"ideas": []})


def test_parser_rejects_candidate_missing_data_requirement() -> None:
    candidate = _candidate()
    del candidate["data_requirement"]

    parsed, report = parse_llm_candidate_response_with_report(
        {"candidates": [candidate]}
    )

    assert parsed == []
    assert "data_requirement" in report.rejected_candidates[0]["reasons"][0]


def test_parser_rejects_verification_label_inflation() -> None:
    candidate = _candidate(claim_type="LeanVerified theorem")

    parsed, report = parse_llm_candidate_response_with_report(
        {"candidates": [candidate]}
    )

    assert parsed == []
    assert "verification-label inflation" in report.rejected_candidates[0]["reasons"][0]


def test_parser_rejects_synthetic_to_real_world_validation() -> None:
    candidate = _candidate(
        data_requirement="SyntheticOnly",
        hypothesis="This gives real-world validation for deployed systems.",
    )

    parsed, report = parse_llm_candidate_response_with_report(
        {"candidates": [candidate]}
    )

    assert parsed == []
    assert "real-world validation" in report.rejected_candidates[0]["reasons"][0]


@pytest.mark.parametrize(
    ("data_requirement", "expected_status"),
    [
        ("PublicDownload", BranchStatus.DEFERRED_REAL_DATA_CANDIDATE),
        ("UserProvided", BranchStatus.REQUIRES_REAL_DATA),
    ],
)
def test_real_data_candidates_are_parsed_then_deferred_by_existing_gate(
    data_requirement: str,
    expected_status: BranchStatus,
) -> None:
    candidate = parse_llm_candidate_response(
        {"candidates": [_candidate(data_requirement=data_requirement)]}
    )[0]
    validation = validate_llm_candidate(candidate)

    assert validation.valid is True
    assert validation.deferred_by_mvp_data_gate is True
    assert apply_mvp_data_gate(candidate).status == expected_status


def test_candidate_validation_is_deterministic() -> None:
    candidate = parse_llm_candidate_response({"candidates": [_candidate()]})[0]

    assert validate_llm_candidate(candidate) == validate_llm_candidate(candidate)


def _candidate(**updates: object) -> dict[str, object]:
    candidate: dict[str, object] = {
        "title": "Calibration under controlled shifts",
        "domain": "machine learning",
        "method": "calibration",
        "claim_type": "methodological proposition",
        "question": "When does calibration remain stable under controlled shifts?",
        "hypothesis": "A constrained calibration map is stable under declared assumptions.",
        "assumptions": ["The shift family is bounded and declared."],
        "primitives": ["calibration map", "shift family"],
        "data_requirement": "NoData",
        "possible_synthetic_experiment": None,
        "baseline": "Compare with an uncalibrated score.",
        "risks": ["The assumptions may be too restrictive."],
    }
    candidate.update(updates)
    return candidate
