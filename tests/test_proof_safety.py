from __future__ import annotations

from factori.adapters.proof_contracts import build_proof_verification_contract
from factori.adapters.proof_safety import (
    validate_proof_contract,
    validate_proof_result,
)
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    Candidate,
    ProofVerificationContract,
    ProofVerificationResult,
    VerificationLabel,
)

HASH = "a" * 64


def test_proof_contract_validation_rejects_missing_claim_id() -> None:
    contract = build_proof_verification_contract(_candidate(), backend="lean").model_copy(
        update={"claim_id": ""}
    )

    result = validate_proof_contract(contract)

    assert not result.valid
    assert "claim_id is required" in result.reasons


def test_proof_contract_validation_rejects_missing_payload() -> None:
    contract = ProofVerificationContract(
        candidate_id="candidate-proof",
        claim_id="claim-candidate-proof",
        claim_text="Theorem-style claim",
        backend="lean",
        allow_external_tools=True,
    )

    result = validate_proof_contract(contract)

    assert not result.valid
    assert "proof payload is required" in result.reasons


def test_proof_contract_validation_rejects_forbidden_tokens_and_bad_timeout() -> None:
    contract = build_proof_verification_contract(
        _candidate(),
        backend="lean",
        proof_payload_text="theorem bad : True := by\n  sorry\n",
    ).model_copy(update={"timeout_seconds": 120})

    result = validate_proof_contract(contract)

    assert not result.valid
    assert result.forbidden_tokens_present is True
    assert "proof payload contains forbidden proof tokens" in result.reasons
    assert "proof timeout must be between 1 and 60 seconds" in result.reasons


def test_proof_contract_validation_rejects_external_imports_and_network_dependency() -> None:
    contract = build_proof_verification_contract(
        _candidate(),
        backend="lean",
        proof_payload_text="-- curl https://example.invalid\n theorem t : True := by trivial",
        allowed_imports=["/absolute/path"],
    )

    result = validate_proof_contract(contract)

    assert not result.valid
    assert "absolute or external imports are not allowed" in result.reasons
    assert "proof payload must not depend on network access" in result.reasons


def test_proof_result_validation_rejects_fake_backend_lean_verified() -> None:
    contract = build_proof_verification_contract(_candidate())
    result = _proof_result(
        backend="fake",
        label=VerificationLabel.LEAN_VERIFIED,
        verified=True,
        exit_code=0,
    )

    validation = validate_proof_result(result, contract)

    assert not validation.valid
    assert "fake backend cannot masquerade as real proof evidence" in validation.reasons


def test_proof_result_validation_rejects_inconsistent_exit_and_verified() -> None:
    contract = build_proof_verification_contract(_candidate(), backend="lean")
    result = _proof_result(verified=True, exit_code=1)

    validation = validate_proof_result(result, contract)

    assert not validation.valid
    assert "verified=true requires exit_code == 0" in validation.reasons


def test_latex_markdown_llm_reviewer_and_retrieval_artifacts_cannot_support_proof() -> None:
    contract = build_proof_verification_contract(_candidate(), backend="lean")
    result = _proof_result(label=VerificationLabel.LEAN_VERIFIED, verified=True, exit_code=0)
    artifacts = [
        _artifact(ArtifactType.LATEX, "runs/run-1/latex/paper.tex", "proof"),
        _artifact(ArtifactType.REPORT, "runs/run-1/reports/reviewer.json", "llm_reviewer"),
        _artifact(
            ArtifactType.LITERATURE,
            "runs/run-1/literature/source.json",
            "retrieval_evidence",
        ),
        _artifact(ArtifactType.REPORT, "runs/run-1/reports/paper.md", "proof"),
    ]

    validation = validate_proof_result(result, contract, artifacts)

    assert not validation.valid
    assert "presentation artifacts cannot justify proof labels" in validation.reasons
    assert "llm_reviewer artifacts cannot justify proof labels" in validation.reasons
    assert "retrieval_evidence artifacts cannot justify proof labels" in validation.reasons


def _candidate() -> Candidate:
    return Candidate(
        id="candidate-proof",
        question="Can a local theorem be checked?",
        theory="Theorem-style claim",
    )


def _proof_result(
    *,
    backend: str = "lean",
    label: VerificationLabel = VerificationLabel.CONJECTURE,
    verified: bool = False,
    exit_code: int = 1,
) -> ProofVerificationResult:
    return ProofVerificationResult(
        candidate_id="candidate-proof",
        claim_id="claim-candidate-proof",
        backend=backend,
        provider=backend,
        proof_language="Lean",
        tool_name="lean",
        exit_code=exit_code,
        stdout_hash=HASH,
        stderr_hash=HASH,
        proof_payload_hash=HASH,
        forbidden_tokens_present=False,
        verified=verified,
        label=label,
        reason="test result",
        raw_trace_artifact_id="proof-trace-candidate-proof",
        safety_report_artifact_id="proof-safety-candidate-proof",
    )


def _artifact(
    artifact_type: ArtifactType,
    path: str,
    evidence_role: str,
) -> ArtifactRef:
    return ArtifactRef(
        id=path.rsplit("/", maxsplit=1)[-1],
        type=artifact_type,
        path=path,
        content_hash=HASH,
        producing_commit_hash=HASH,
        metadata={"evidence_role": evidence_role, "is_verification_evidence": True},
    )
