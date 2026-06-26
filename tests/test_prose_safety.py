from __future__ import annotations

from factori.adapters.prose_safety import (
    parse_prose_generation_response,
    validate_generated_section,
)
from factori.schemas import (
    Claim,
    ClaimTable,
    GeneratedSectionDraft,
    ProseSectionContract,
    VerificationLabel,
)


def test_parser_accepts_valid_structured_prose_response() -> None:
    parsed = parse_prose_generation_response(_raw_response())

    assert not parsed.rejected
    assert parsed.section_draft is not None
    assert parsed.section_draft.section_id == "introduction"
    assert parsed.section_draft.used_claim_ids == ["claim-main"]
    assert not parsed.section_draft.is_verification_evidence


def test_parser_rejects_malformed_response() -> None:
    parsed = parse_prose_generation_response("not-json")

    assert parsed.rejected
    assert parsed.section_draft is None
    assert "not valid JSON" in parsed.reasons[0]


def test_parser_rejects_missing_required_fields() -> None:
    parsed = parse_prose_generation_response({"section_id": "introduction"})

    assert parsed.rejected
    assert "missing required prose response fields" in parsed.reasons[0]


def test_safety_accepts_grounded_placeholder_prose() -> None:
    safety = validate_generated_section(
        _draft(),
        _section_contract(),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.safe
    assert not safety.rejected
    assert not safety.is_verification_evidence
    assert not safety.creates_scientific_validation


def test_safety_rejects_unknown_claim_ids() -> None:
    safety = validate_generated_section(
        _draft(claim_ids=["claim-unknown"]),
        _section_contract(),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.rejected
    assert any("claim IDs" in reason for reason in safety.reasons)


def test_safety_rejects_unknown_evidence_artifact_ids() -> None:
    safety = validate_generated_section(
        _draft(evidence_ids=["evidence-unknown"]),
        _section_contract(),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.rejected
    assert any("evidence artifact IDs" in reason for reason in safety.reasons)


def test_safety_rejects_invented_citation_ids() -> None:
    safety = validate_generated_section(
        _draft(citation_ids=["source-invented"]),
        _section_contract(),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.rejected
    assert any("citation IDs" in reason for reason in safety.reasons)


def test_safety_rejects_lean_verified_text_when_claim_is_not_lean_verified() -> None:
    safety = validate_generated_section(
        _draft(content="This section says the claim is LeanVerified."),
        _section_contract(forbidden_labels=[VerificationLabel.LEAN_VERIFIED]),
        _claim_table(label=VerificationLabel.CONJECTURE),
        _evidence_map(),
    )

    assert safety.rejected
    assert safety.created_or_upgraded_labels
    assert any("LeanVerified" in reason for reason in safety.reasons)


def test_safety_rejects_real_data_experiment_verified_in_mvp() -> None:
    safety = validate_generated_section(
        _draft(content="This section claims RealDataExperimentVerified status."),
        _section_contract(),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.rejected
    assert safety.created_or_upgraded_labels
    assert any("RealDataExperimentVerified" in reason for reason in safety.reasons)


def test_safety_rejects_empirical_validation_from_synthetic_only_evidence() -> None:
    safety = validate_generated_section(
        _draft(content="SyntheticExperimentVerified evidence gives real-world validation."),
        _section_contract(),
        _claim_table(label=VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED),
        _evidence_map(),
    )

    assert safety.rejected
    assert any("real-world validation" in reason for reason in safety.reasons)


def test_safety_rejects_invented_theorem_numbering() -> None:
    safety = validate_generated_section(
        _draft(content="Theorem 1 proves the example statement."),
        _section_contract(),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.rejected
    assert any("numbering" in reason for reason in safety.reasons)


def test_safety_warns_on_word_limit_without_turning_prose_into_evidence() -> None:
    safety = validate_generated_section(
        _draft(content="one two three four five"),
        _section_contract(max_words=3),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.safe
    assert not safety.rejected
    assert any("word limit exceeded" in warning for warning in safety.warnings)
    assert not safety.is_verification_evidence


def _raw_response() -> dict[str, object]:
    return {
        "section_id": "introduction",
        "title": "Introduction",
        "draft_markdown": "This section references claim-main using evidence-a.",
        "used_claim_ids": ["claim-main"],
        "used_evidence_artifact_ids": ["evidence-a"],
        "used_citation_ids": ["source-a"],
        "unsupported_sentences": [],
        "warnings": [],
    }


def _draft(
    *,
    content: str = "This section references claim-main using evidence-a.",
    claim_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    citation_ids: list[str] | None = None,
) -> GeneratedSectionDraft:
    used_claim_ids = claim_ids or ["claim-main"]
    return GeneratedSectionDraft(
        section_id="introduction",
        title="Introduction",
        content=content,
        claim_ids=used_claim_ids,
        used_claim_ids=used_claim_ids,
        used_evidence_artifact_ids=evidence_ids or ["evidence-a"],
        used_citation_ids=citation_ids or ["source-a"],
        unsupported_sentences=[],
        warnings=[],
        fake=False,
        is_verification_evidence=False,
    )


def _section_contract(
    *,
    forbidden_labels: list[VerificationLabel] | None = None,
    max_words: int = 120,
) -> ProseSectionContract:
    return ProseSectionContract(
        run_id="run-1",
        section_id="introduction",
        section_title="Introduction",
        section_role="Introduction",
        allowed_claim_ids=["claim-main"],
        allowed_evidence_artifact_ids=["evidence-a"],
        allowed_citation_ids=["source-a"],
        forbidden_labels=forbidden_labels
        or [VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED],
        evidence_boundary_instructions=["Generated prose is not evidence."],
        style_instructions=["Use placeholder-grade prose."],
        max_words=max_words,
        source_contract_hashes={"claim_table": "0" * 64},
    )


def _claim_table(label: VerificationLabel = VerificationLabel.CONJECTURE) -> ClaimTable:
    return ClaimTable(
        final_nucleus_id="final",
        claims=[
            Claim(
                claim_id="claim-main",
                claim_text="The example remains bounded by its label.",
                claim_label=label,
                candidate_id="candidate-a",
                evidence_artifact_ids=["evidence-a"],
                evidence_types=["proof"],
                allowed_in_main_text=True,
                allowed_section="Introduction",
                reason="test",
            )
        ],
    )


def _evidence_map() -> dict[str, dict[str, object]]:
    return {
        "evidence-a": {
            "artifact_id": "evidence-a",
            "claim_id": "claim-main",
            "is_verification_evidence": False,
        }
    }
