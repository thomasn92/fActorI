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


def test_safety_rejects_unknown_citation_keys() -> None:
    safety = validate_generated_section(
        _draft(content="This section cites [@Invented2026].", citation_keys=["Invented2026"]),
        _section_contract(),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.rejected
    assert any("citation keys" in reason for reason in safety.reasons)


def test_safety_accepts_allowed_citation_keys() -> None:
    safety = validate_generated_section(
        _draft(content="This section cites bounded context [@SourceA2024]."),
        _section_contract(),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.safe
    assert safety.used_citation_keys == ["SourceA2024"]


def test_safety_rejects_invented_citations_from_markdown_markers() -> None:
    safety = validate_generated_section(
        _draft(content="This section cites [@MadeUp2026]."),
        _section_contract(),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.rejected
    assert any("citation keys" in reason for reason in safety.reasons)


def test_safety_rejects_exhaustive_literature_coverage_claims() -> None:
    safety = validate_generated_section(
        _draft(content="This section covers all prior work [@SourceA2024]."),
        _section_contract(),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.rejected
    assert any("exhaustive literature" in reason for reason in safety.reasons)


def test_safety_rejects_retrieval_as_novelty_proof() -> None:
    safety = validate_generated_section(
        _draft(content="Retrieval proves novelty for claim-main [@SourceA2024]."),
        _section_contract(),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.rejected
    assert any("retrieval or citations" in reason for reason in safety.reasons)


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


def test_safety_allows_limitations_as_scaffold_language() -> None:
    sentence = (
        "The limitations of this draft are the absence of retrieval-backed citations, "
        "proof artifacts, experiment artifacts, and human validation."
    )
    safety = validate_generated_section(
        _draft(
            content=sentence,
            claim_ids=[],
            evidence_ids=[],
            citation_ids=[],
            unsupported_sentences=[sentence],
        ),
        _section_contract(
            allowed_claim_ids=[],
            allowed_evidence_ids=[],
            allowed_citation_ids=[],
            allowed_statement_classes=[
                "limitation_statement",
                "missing_evidence_statement",
            ],
            forbidden_labels=[
                VerificationLabel.CONJECTURE,
                VerificationLabel.LEAN_VERIFIED,
                VerificationLabel.LIMITATION,
            ],
        ),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.safe
    assert not safety.rejected
    assert safety.allowed_statement_classes_used == [
        "limitation_statement",
        "missing_evidence_statement",
    ]
    assert safety.safe_scaffold_sentences_retained == [sentence]
    assert safety.unsafe_sentences_removed == []


def test_safety_rejects_conjecture_as_formal_label_without_evidence() -> None:
    safety = validate_generated_section(
        _draft(
            content="This section states a Conjecture.",
            claim_ids=[],
            evidence_ids=[],
            citation_ids=[],
        ),
        _section_contract(
            allowed_claim_ids=[],
            allowed_evidence_ids=[],
            allowed_citation_ids=[],
            forbidden_labels=[VerificationLabel.CONJECTURE],
        ),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.rejected
    assert "This section states a Conjecture." in safety.unsafe_sentences_removed
    assert any("Conjecture" in reason for reason in safety.reasons)


def test_safety_rejects_theorem_as_formal_label_without_evidence() -> None:
    safety = validate_generated_section(
        _draft(
            content="This draft presents a Theorem.",
            claim_ids=[],
            evidence_ids=[],
            citation_ids=[],
        ),
        _section_contract(
            allowed_claim_ids=[],
            allowed_evidence_ids=[],
            allowed_citation_ids=[],
        ),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.rejected
    assert any("formal result label" in reason for reason in safety.reasons)


def test_safety_rejects_empirical_validation_from_synthetic_only_evidence() -> None:
    safety = validate_generated_section(
        _draft(content="SyntheticExperimentVerified evidence gives real-world validation."),
        _section_contract(),
        _claim_table(label=VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED),
        _evidence_map(),
    )

    assert safety.rejected
    assert any("real-world validation" in reason for reason in safety.reasons)


def test_safety_allows_problem_framing_as_scaffold() -> None:
    sentence = (
        "The problem addressed by this draft is how to organize a human-geography "
        "research candidate into a manuscript-shaped artifact while preserving strict "
        "evidence boundaries."
    )
    safety = validate_generated_section(
        _draft(
            content=sentence,
            claim_ids=[],
            evidence_ids=[],
            citation_ids=[],
            unsupported_sentences=[sentence],
        ),
        _section_contract(
            allowed_claim_ids=[],
            allowed_evidence_ids=[],
            allowed_citation_ids=[],
            allowed_statement_classes=[
                "problem_framing",
                "evidence_boundary_statement",
            ],
        ),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.safe
    assert "problem_framing" in safety.allowed_statement_classes_used
    assert safety.sanitized_content == sentence


def test_safety_allows_method_description_as_scaffold() -> None:
    sentence = (
        "The method summarized here is a bounded generation pipeline that builds a "
        "manuscript plan, drafts sections under explicit evidence constraints, applies "
        "safety repair, and emits audit reports."
    )
    safety = validate_generated_section(
        _draft(
            content=sentence,
            claim_ids=[],
            evidence_ids=[],
            citation_ids=[],
            unsupported_sentences=[sentence],
        ),
        _section_contract(
            allowed_claim_ids=[],
            allowed_evidence_ids=[],
            allowed_citation_ids=[],
            allowed_statement_classes=[
                "method_description",
                "pipeline_description",
                "provenance_statement",
            ],
        ),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.safe
    assert "method_description" in safety.allowed_statement_classes_used
    assert "pipeline_description" in safety.allowed_statement_classes_used


def test_safety_sanitizes_one_unsafe_sentence_without_omitting_section() -> None:
    safe_sentence = (
        "The current artifact does not provide proof, empirical validation, retrieval "
        "grounding, or publication readiness."
    )
    unsafe_sentence = "We prove the result."
    safety = validate_generated_section(
        _draft(
            content=f"{safe_sentence} {unsafe_sentence}",
            claim_ids=[],
            evidence_ids=[],
            citation_ids=[],
            unsupported_sentences=[unsafe_sentence],
        ),
        _section_contract(
            allowed_claim_ids=[],
            allowed_evidence_ids=[],
            allowed_citation_ids=[],
            allowed_statement_classes=[
                "evidence_boundary_statement",
                "limitation_statement",
            ],
        ),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.safe
    assert safety.section_status == "partially_sanitized"
    assert safety.sanitized_content == safe_sentence
    assert safety.unsafe_sentences_removed == [unsafe_sentence]
    assert safety.removed_sentence_count == 1
    assert safety.retained_sentence_count == 1


def test_safety_rejects_publication_ready_language() -> None:
    safety = validate_generated_section(
        _draft(
            content="This manuscript is publication ready.",
            claim_ids=[],
            evidence_ids=[],
            citation_ids=[],
        ),
        _section_contract(
            allowed_claim_ids=[],
            allowed_evidence_ids=[],
            allowed_citation_ids=[],
        ),
        _claim_table(),
        _evidence_map(),
    )

    assert safety.rejected
    assert any("publication-readiness" in reason for reason in safety.reasons)


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
        "used_citation_keys": ["SourceA2024"],
        "unsupported_sentences": [],
        "warnings": [],
    }


def _draft(
    *,
    content: str = "This section references claim-main using evidence-a.",
    claim_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    citation_ids: list[str] | None = None,
    citation_keys: list[str] | None = None,
    unsupported_sentences: list[str] | None = None,
) -> GeneratedSectionDraft:
    used_claim_ids = ["claim-main"] if claim_ids is None else claim_ids
    return GeneratedSectionDraft(
        section_id="introduction",
        title="Introduction",
        content=content,
        claim_ids=used_claim_ids,
        used_claim_ids=used_claim_ids,
        used_evidence_artifact_ids=["evidence-a"] if evidence_ids is None else evidence_ids,
        used_citation_ids=["source-a"] if citation_ids is None else citation_ids,
        used_citation_keys=[] if citation_keys is None else citation_keys,
        unsupported_sentences=[] if unsupported_sentences is None else unsupported_sentences,
        warnings=[],
        fake=False,
        is_verification_evidence=False,
    )


def _section_contract(
    *,
    forbidden_labels: list[VerificationLabel] | None = None,
    max_words: int = 120,
    allowed_claim_ids: list[str] | None = None,
    allowed_evidence_ids: list[str] | None = None,
    allowed_citation_ids: list[str] | None = None,
    allowed_statement_classes: list[str] | None = None,
) -> ProseSectionContract:
    return ProseSectionContract(
        run_id="run-1",
        section_id="introduction",
        section_title="Introduction",
        section_role="Introduction",
        allowed_claim_ids=["claim-main"] if allowed_claim_ids is None else allowed_claim_ids,
        allowed_evidence_artifact_ids=(
            ["evidence-a"] if allowed_evidence_ids is None else allowed_evidence_ids
        ),
        allowed_citation_ids=["source-a"] if allowed_citation_ids is None else allowed_citation_ids,
        allowed_citation_keys=["SourceA2024"] if allowed_citation_ids is None else [],
        allowed_statement_classes=(
            [] if allowed_statement_classes is None else allowed_statement_classes
        ),
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
