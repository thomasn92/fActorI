"""Deterministic fake proof validator for Stage C."""

from __future__ import annotations

from factori.schemas import ArtifactType, Candidate, FakeProofResult, VerificationLabel

FORBIDDEN_PROOF_TOKENS = ("sorry", "admit", "axiom", "unsafe")


def run_fake_proof_validation(candidate: Candidate) -> FakeProofResult:
    """Return a deterministic fake proof result without calling Lean."""
    proof_text = _candidate_text(candidate)
    forbidden_tokens_present = any(token in proof_text for token in FORBIDDEN_PROOF_TOKENS)
    lean_exit_code_fake = 1 if forbidden_tokens_present or "lean-fail" in candidate.id else 0
    proof_score = _proof_score(candidate, forbidden_tokens_present, lean_exit_code_fake)

    if (
        lean_exit_code_fake == 0
        and not forbidden_tokens_present
        and proof_score >= 0.85
    ):
        label = VerificationLabel.LEAN_VERIFIED
        reason = "fake Lean compilation succeeded with sufficient proof score"
    elif proof_score >= 0.35:
        label = VerificationLabel.CONJECTURE
        reason = "fake proof attempt did not meet LeanVerified threshold"
    else:
        label = VerificationLabel.UNSUPPORTED
        reason = "fake proof attempt was structurally unsupported"

    return FakeProofResult(
        candidate_id=candidate.id,
        proof_attempt_id=f"fake-proof-{candidate.id}",
        lean_exit_code_fake=lean_exit_code_fake,
        forbidden_tokens_present=forbidden_tokens_present,
        proof_score=proof_score,
        label=label,
        evidence_artifact_type=ArtifactType.LEAN,
        reason=reason,
    )


def _proof_score(
    candidate: Candidate,
    forbidden_tokens_present: bool,
    lean_exit_code_fake: int,
) -> float:
    if forbidden_tokens_present:
        return 0.20
    if lean_exit_code_fake != 0:
        return 0.42
    text = _candidate_text(candidate)
    score = 0.55
    if any(token in text for token in ["theorem", "lemma", "proof"]):
        score += 0.35
    if "conjecture" in text:
        score += 0.25
    if candidate.literature.adequacy >= 0.75:
        score += 0.05
    if "weak-proof" in candidate.id:
        score -= 0.40
    return round(min(1.0, max(0.0, score)), 6)


def _candidate_text(candidate: Candidate) -> str:
    parts = [
        candidate.id,
        candidate.method or "",
        candidate.question,
        candidate.hypothesis or "",
        candidate.theory or "",
        candidate.variant_type or "",
        " ".join(str(value) for value in candidate.symbolic_state.values()),
    ]
    return " ".join(parts).lower()
