from __future__ import annotations

from factori.proof_fake import run_fake_proof_validation
from factori.schemas import ArtifactType, Candidate, LiteratureState, VerificationLabel


def test_fake_proof_validator_is_deterministic() -> None:
    candidate = _theorem_candidate("candidate-proof")

    first = run_fake_proof_validation(candidate)
    second = run_fake_proof_validation(candidate)

    assert first == second


def test_fake_proof_success_can_produce_lean_verified() -> None:
    result = run_fake_proof_validation(_theorem_candidate("candidate-proof"))

    assert result.lean_exit_code_fake == 0
    assert not result.forbidden_tokens_present
    assert result.proof_score >= 0.85
    assert result.label == VerificationLabel.LEAN_VERIFIED
    assert result.evidence_artifact_type == ArtifactType.LEAN


def test_forbidden_tokens_prevent_lean_verified() -> None:
    candidate = _theorem_candidate("candidate-forbidden", theory="Theorem with sorry")

    result = run_fake_proof_validation(candidate)

    assert result.forbidden_tokens_present
    assert result.label != VerificationLabel.LEAN_VERIFIED


def test_failed_proof_becomes_conjecture_or_unsupported() -> None:
    candidate = _theorem_candidate("candidate-lean-fail")

    result = run_fake_proof_validation(candidate)

    assert result.lean_exit_code_fake != 0
    assert result.label in {VerificationLabel.CONJECTURE, VerificationLabel.UNSUPPORTED}


def _theorem_candidate(candidate_id: str, theory: str = "Theorem-style proof") -> Candidate:
    return Candidate(
        id=candidate_id,
        question="Can the fake theorem be validated?",
        theory=theory,
        literature=LiteratureState(
            semantic=0.85,
            keyword=0.82,
            citation=0.81,
            diversity=0.80,
            adversarial=0.80,
        ),
        variant_type="theorem_or_conjecture_form",
    )
