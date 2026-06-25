from __future__ import annotations

from factori.adapters.proof_contracts import (
    build_proof_verification_contract,
    proof_payload_text,
)
from factori.schemas import Candidate


def test_proof_contract_construction_is_deterministic() -> None:
    candidate = _candidate()

    first = build_proof_verification_contract(candidate, backend="lean", tool_name="lean")
    second = build_proof_verification_contract(candidate, backend="lean", tool_name="lean")

    assert first == second
    assert first.candidate_id == candidate.id
    assert first.claim_id == f"claim-{candidate.id}"
    assert first.proof_language == "Lean"
    assert first.backend == "lean"
    assert first.tool_name == "lean"
    assert first.allow_external_tools is True
    assert first.is_verification_evidence is False
    assert set(first.forbidden_tokens) >= {"sorry", "admit", "axiom", "unsafe"}


def test_proof_contract_uses_explicit_payload_when_supplied() -> None:
    contract = build_proof_verification_contract(
        _candidate(),
        backend="lean",
        proof_payload_text="theorem supplied : True := by\n  trivial\n",
    )

    assert proof_payload_text(contract) == "theorem supplied : True := by\n  trivial\n"


def test_fake_contract_remains_fake_and_does_not_allow_tools() -> None:
    contract = build_proof_verification_contract(_candidate())

    assert contract.backend == "fake"
    assert contract.allow_external_tools is False
    assert contract.fake_default is True


def _candidate() -> Candidate:
    return Candidate(
        id="candidate-proof",
        question="Can a local theorem be checked?",
        theory="Theorem-style claim",
    )
