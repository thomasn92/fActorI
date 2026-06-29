from __future__ import annotations

from factori.citations import (
    build_claim_support_audit,
    repair_confirmed_claim_support_violations,
)
from factori.claim_adjudication import (
    ClaimAdjudicationRequest,
    FakeClaimAdjudicator,
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
