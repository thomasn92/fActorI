from __future__ import annotations

from factori.citations import (
    build_citation_registry,
    build_claim_support_audit,
    repair_confirmed_claim_support_violations,
)
from factori.claim_adjudication import (
    ClaimAdjudicationRequest,
    FakeClaimAdjudicator,
    citation_requirement_for_sentence,
    deterministic_semantic_adjudication,
)


def _request(sentence: str, preliminary: str = "proof_claim") -> ClaimAdjudicationRequest:
    return ClaimAdjudicationRequest(
        sentence_id="limitations-p0-s0",
        section_name="Limitations",
        sentence=sentence,
        preliminary_claim_class=preliminary,
        citation_keys_present=[],
        registry_source_summaries=[],
        available_evidence_artifacts={
            "proof": False,
            "experiment": False,
            "human_review": False,
            "publication_ready": False,
        },
    )


def test_fake_adjudicator_treats_no_proof_as_boundary() -> None:
    result = FakeClaimAdjudicator().adjudicate(
        [_request("This manuscript is not a proof and provides no validation.")]
    )[0]

    assert result.adjudicated_claim_class == "limitation_statement"
    assert result.forbidden_claim_detected is False


def test_fake_adjudicator_treats_not_an_experiment_as_boundary() -> None:
    result = FakeClaimAdjudicator().adjudicate(
        [_request("This demonstration is not an experiment result.", "experiment_claim")]
    )[0]

    assert result.adjudicated_claim_class == "evidence_boundary_statement"


def test_fake_adjudicator_treats_withheld_validation_as_limitation() -> None:
    sentence = (
        "All validation language is intentionally withheld until a real proof or study "
        "is available."
    )
    result = FakeClaimAdjudicator().adjudicate([_request(sentence)])[0]

    assert result.adjudicated_claim_class == "limitation_statement"
    assert result.forbidden_claim_detected is False


def test_fake_adjudicator_keeps_positive_proof_claim_forbidden() -> None:
    result = FakeClaimAdjudicator().adjudicate(
        [_request("We prove that the proposed construction is correct.")]
    )[0]

    assert result.adjudicated_claim_class == "proof_claim"
    assert result.forbidden_claim_detected is True


def test_fake_adjudicator_keeps_positive_experiment_claim_forbidden() -> None:
    result = FakeClaimAdjudicator().adjudicate(
        [_request("Experiments show that the method is empirically valid.", "experiment_claim")]
    )[0]

    assert result.adjudicated_claim_class == "experiment_claim"
    assert result.forbidden_claim_detected is True


def test_fake_adjudicator_detects_citation_as_validation_misuse() -> None:
    request = _request(
        "The cited sources validate the result [@Fixture2024].",
        "source_context_claim",
    )
    request = ClaimAdjudicationRequest(
        **{
            **request.__dict__,
            "citation_keys_present": ["Fixture2024"],
        }
    )

    result = FakeClaimAdjudicator().adjudicate([request])[0]

    assert result.citation_use == "misused_as_validation"
    assert result.citation_as_validation_misuse is True


def test_fake_adjudicator_treats_sources_do_not_validate_as_boundary() -> None:
    result = FakeClaimAdjudicator().adjudicate(
        [_request("These sources do not validate the current result.", "source_context_claim")]
    )[0]

    assert result.adjudicated_claim_class == "evidence_boundary_statement"
    assert result.citation_as_validation_misuse is False


def test_claim_support_audit_uses_adjudicated_class() -> None:
    audit = build_claim_support_audit(
        run_id="run-adjudication",
        markdown=(
            "# Draft\n\n## Limitations\n\n"
            "This is not a proof, experiment result, or empirical validation.\n"
        ),
        citation_registry=None,
        claim_adjudicator=FakeClaimAdjudicator(),
    )

    assert audit.claim_adjudication_enabled is True
    assert audit.claim_adjudicator_backend == "fake"
    assert audit.adjudicated_sentence_count == 1
    assert audit.summary_counts["forbidden_claim"] == 0
    assert audit.claim_support_items[0].claim_class == "evidence_boundary_statement"


def test_deterministic_fallback_has_negation_guardrail() -> None:
    result = deterministic_semantic_adjudication(
        _request("No proof, experiments, validation, or publication readiness is claimed.")
    )

    assert result.adjudicator_backend == "deterministic_fallback"
    assert result.adjudicated_claim_class == "limitation_statement"
    assert result.forbidden_claim_detected is False


def test_claim_support_repair_removes_only_confirmed_positive_violation() -> None:
    safe_boundary = "This draft is not a proof and provides no empirical validation."
    positive_claim = "We prove that the proposed construction is correct."
    markdown = f"# Draft\n\n## Limitations\n\n{safe_boundary} {positive_claim}\n"
    audit = build_claim_support_audit(
        run_id="run-repair",
        markdown=markdown,
        citation_registry=None,
        claim_adjudicator=FakeClaimAdjudicator(),
    )

    repaired, removed = repair_confirmed_claim_support_violations(markdown, audit)

    assert safe_boundary in repaired
    assert positive_claim not in repaired
    assert len(removed) == 1


def test_literature_positioning_scaffold_does_not_require_citation() -> None:
    sentence = (
        "Accordingly, the section should be treated as a problem-framing and "
        "literature-positioning scaffold."
    )

    result = FakeClaimAdjudicator().adjudicate(
        [_request(sentence, "literature_background_claim")]
    )[0]

    assert result.requires_citation is False
    assert result.requires_citation_reason == "scaffold_role_no_citation_required"


def test_retrieval_limitations_do_not_require_citation() -> None:
    sentence = (
        "It motivates the bounded-structure analysis agenda, notes the retrieval "
        "limitations, and avoids any claim that the available metadata verifies the "
        "mathematical statement or resolves the broader literature landscape."
    )

    result = FakeClaimAdjudicator().adjudicate(
        [_request(sentence, "literature_background_claim")]
    )[0]

    assert result.requires_citation is False
    assert result.requires_citation_reason == "absence_of_evidence_no_citation_required"


def test_current_draft_lacks_literature_support_does_not_require_citation() -> None:
    sentence = (
        "The current draft also does not include retrieval-backed literature support, "
        "so it cannot make source-context claims about prior work beyond noting that "
        "such context is absent from the present run artifacts."
    )

    result = FakeClaimAdjudicator().adjudicate(
        [_request(sentence, "source_context_claim")]
    )[0]

    assert result.requires_citation is False
    assert result.requires_citation_reason == "absence_of_evidence_no_citation_required"


def test_available_metadata_does_not_verify_statement_does_not_require_citation() -> None:
    result = FakeClaimAdjudicator().adjudicate(
        [
            _request(
                "The available metadata does not verify the mathematical statement.",
                "source_context_claim",
            )
        ]
    )[0]

    assert result.adjudicated_claim_class == "evidence_boundary_statement"
    assert result.requires_citation is False


def test_positive_prior_work_claim_requires_citation() -> None:
    requires, reason = citation_requirement_for_sentence(
        "Prior work uses optimal transport to compare spatial distributions.",
        "literature_background_claim",
    )

    assert requires is True
    assert reason == "positive_literature_claim"


def test_positive_literature_support_claim_requires_citation() -> None:
    requires, reason = citation_requirement_for_sentence(
        "The literature supports this framing.",
        "literature_background_claim",
    )

    assert requires is True
    assert reason == "positive_literature_claim"


def test_positive_source_content_claim_requires_citation() -> None:
    requires, reason = citation_requirement_for_sentence(
        "Source X discusses migration flows using optimal transport.",
        "source_context_claim",
    )

    assert requires is True
    assert reason == "positive_source_context_claim"


def test_positive_external_factual_claim_requires_citation() -> None:
    requires, reason = citation_requirement_for_sentence(
        "Human geography commonly models spatial interaction through network flows.",
        "external_factual_claim",
    )

    assert requires is True
    assert reason == "positive_external_claim"


def test_claim_support_audit_ignores_current_run_limitation_missing_citations() -> None:
    registry = build_citation_registry("run-semantic", [_source("S1")])
    markdown = (
        "# Draft\n\n## Introduction\n\n"
        "Accordingly, the section should be treated as a problem-framing and "
        "literature-positioning scaffold. "
        "The current draft also does not include retrieval-backed literature support, "
        "so it cannot make source-context claims about prior work beyond noting that "
        "such context is absent from the present run artifacts.\n"
    )

    audit = build_claim_support_audit(
        run_id="run-semantic",
        markdown=markdown,
        citation_registry=registry,
        claim_adjudicator=FakeClaimAdjudicator(),
    )

    assert audit.summary_counts["missing_required_citation"] == 0
    assert audit.unsupported_items == []
    assert {
        item.requires_citation_reason
        for item in audit.claim_support_items
        if not item.requires_citation
    } >= {
        "scaffold_role_no_citation_required",
        "absence_of_evidence_no_citation_required",
    }


def test_claim_support_audit_still_flags_uncited_positive_external_claim() -> None:
    registry = build_citation_registry("run-semantic", [_source("S1")])

    audit = build_claim_support_audit(
        run_id="run-semantic",
        markdown="## Introduction\n\nPrior work uses optimal transport in geography.",
        citation_registry=registry,
        claim_adjudicator=FakeClaimAdjudicator(),
    )

    assert audit.summary_counts["missing_required_citation"] == 1
    assert audit.unsupported_items[0].requires_citation is True
    assert audit.unsupported_items[0].requires_citation_reason == (
        "positive_literature_claim"
    )


def test_deterministic_fallback_matches_observed_false_positive_semantics() -> None:
    registry = build_citation_registry("run-semantic", [_source("S1")])

    audit = build_claim_support_audit(
        run_id="run-semantic",
        markdown=(
            "## Introduction\n\n"
            "It motivates the bounded-structure analysis agenda, notes the retrieval "
            "limitations, and avoids any claim that the available metadata verifies the "
            "mathematical statement or resolves the broader literature landscape."
        ),
        citation_registry=registry,
        claim_adjudicator=None,
    )

    assert audit.claim_adjudication_enabled is False
    assert audit.summary_counts["missing_required_citation"] == 0
    assert audit.unsupported_items == []


def _source(source_id: str):
    from types import SimpleNamespace

    return SimpleNamespace(
        source_id=source_id,
        title=f"Fixture Source {source_id}",
        authors=["Fixture"],
        year=2024,
        venue="Fixture Venue",
        doi=None,
        url=None,
        provider="fake",
        retrieved_at="1970-01-01T00:00:00Z",
        raw_metadata_hash="0" * 64,
        abstract="Fixture source for citation-support tests.",
        snippet="Fixture source for citation-support tests.",
        query="human geography",
        rank=1,
        metadata={
            "backend": "fake",
            "source_status": "fixture",
            "source_type": "fixture",
            "trust_level": "fixture",
        },
    )
