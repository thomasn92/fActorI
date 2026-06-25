"""Deterministic proof-verification contract construction."""

from __future__ import annotations

import re
from collections.abc import Sequence

from factori.config import DEFAULT_PROOF_TIMEOUT_SECONDS
from factori.proof_fake import FORBIDDEN_PROOF_TOKENS
from factori.schemas import Candidate, ProofVerificationContract

DEFAULT_PROOF_LANGUAGE = "Lean"
DEFAULT_EXPECTED_OUTPUT_TYPE = "proof_transcript"


def build_proof_verification_contract(
    candidate: Candidate,
    *,
    backend: str = "fake",
    proof_language: str = DEFAULT_PROOF_LANGUAGE,
    proof_payload_text: str | None = None,
    proof_payload_path: str | None = None,
    allowed_imports: Sequence[str] = (),
    forbidden_tokens: Sequence[str] = FORBIDDEN_PROOF_TOKENS,
    timeout_seconds: int = DEFAULT_PROOF_TIMEOUT_SECONDS,
    tool_name: str | None = None,
) -> ProofVerificationContract:
    """Build a deterministic contract for the exact candidate claim."""
    claim_text = _claim_text(candidate)
    payload_text = proof_payload_text or _default_lean_payload(candidate, claim_text)
    return ProofVerificationContract(
        candidate_id=candidate.id,
        claim_id=f"claim-{candidate.id}",
        claim_text=claim_text,
        proof_language=proof_language,
        proof_payload_path=proof_payload_path,
        proof_payload_text=payload_text,
        proof_payload={"text": payload_text},
        allowed_imports=list(allowed_imports),
        forbidden_tokens=list(forbidden_tokens),
        timeout_seconds=timeout_seconds,
        expected_output_type=DEFAULT_EXPECTED_OUTPUT_TYPE,
        backend=backend,
        tool_name=tool_name,
        allow_external_tools=backend != "fake",
        fake_default=backend == "fake",
        is_verification_evidence=False,
    )


def proof_payload_text(contract: ProofVerificationContract) -> str:
    """Return the text payload used by local proof-tool runners."""
    if contract.proof_payload_text is not None:
        return contract.proof_payload_text
    text = contract.proof_payload.get("text")
    return str(text) if text is not None else ""


def _claim_text(candidate: Candidate) -> str:
    return (
        candidate.theory
        or candidate.hypothesis
        or candidate.question
        or f"Claim for candidate {candidate.id}"
    )


def _default_lean_payload(candidate: Candidate, claim_text: str) -> str:
    safe_id = re.sub(r"[^A-Za-z0-9_]", "_", candidate.id)
    claim_comment = " ".join(claim_text.split())
    return (
        f"-- fActorI deterministic proof payload for {candidate.id}\n"
        f"-- claim: {claim_comment}\n"
        f"theorem factori_{safe_id} : True := by\n"
        "  trivial\n"
    )


__all__ = [
    "DEFAULT_EXPECTED_OUTPUT_TYPE",
    "DEFAULT_PROOF_LANGUAGE",
    "build_proof_verification_contract",
    "proof_payload_text",
]
