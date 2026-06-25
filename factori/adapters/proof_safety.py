"""Safety checks for gated proof-verification adapters."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from factori.adapters.proof_contracts import proof_payload_text
from factori.evidence import is_proof_evidence
from factori.proof_fake import FORBIDDEN_PROOF_TOKENS
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ProofVerificationContract,
    ProofVerificationResult,
    VerificationLabel,
)

SUPPORTED_PROOF_LANGUAGES = frozenset({"lean"})
MAX_PROOF_TIMEOUT_SECONDS = 60
NETWORK_MARKERS = ("http://", "https://", "ftp://", "curl ", "wget ")
NON_PROOF_EVIDENCE_ROLES = frozenset(
    {
        "llm_prompt",
        "llm_response",
        "llm_parse_report",
        "llm_reviewer",
        "retrieval_evidence",
        "literature_evidence",
        "fake_synthetic_experiment",
        "real_data_experiment",
    }
)


@dataclass(frozen=True)
class ProofContractValidationResult:
    """Deterministic validation result for a proof contract."""

    valid: bool
    reasons: tuple[str, ...]
    forbidden_tokens_present: bool


@dataclass(frozen=True)
class ProofResultValidationResult:
    """Deterministic validation result for a proof backend result."""

    valid: bool
    reasons: tuple[str, ...]


def validate_proof_contract(
    contract: ProofVerificationContract,
) -> ProofContractValidationResult:
    """Validate a proof request before any external tool execution."""
    reasons: list[str] = []
    if not contract.candidate_id.strip():
        reasons.append("candidate_id is required")
    if not contract.claim_id.strip():
        reasons.append("claim_id is required")
    if not contract.claim_text.strip():
        reasons.append("claim_text is required")
    if contract.proof_language.strip().lower() not in SUPPORTED_PROOF_LANGUAGES:
        reasons.append(f"unsupported proof language: {contract.proof_language}")

    payload_text = proof_payload_text(contract)
    has_payload = bool(payload_text.strip() or contract.proof_payload_path)
    if not has_payload:
        reasons.append("proof payload is required")

    forbidden_tokens = tuple(contract.forbidden_tokens or FORBIDDEN_PROOF_TOKENS)
    payload_and_claim = f"{contract.claim_text}\n{payload_text}".lower()
    forbidden_present = any(token.lower() in payload_and_claim for token in forbidden_tokens)
    if forbidden_present:
        reasons.append("proof payload contains forbidden proof tokens")

    if contract.timeout_seconds < 1 or contract.timeout_seconds > MAX_PROOF_TIMEOUT_SECONDS:
        reasons.append("proof timeout must be between 1 and 60 seconds")
    for allowed_import in contract.allowed_imports:
        if allowed_import.startswith("/") or "://" in allowed_import:
            reasons.append("absolute or external imports are not allowed")
            break
    lowered_payload = payload_text.lower()
    if any(marker in lowered_payload for marker in NETWORK_MARKERS):
        reasons.append("proof payload must not depend on network access")

    return ProofContractValidationResult(
        valid=not reasons,
        reasons=tuple(reasons),
        forbidden_tokens_present=forbidden_present,
    )


def validate_proof_result(
    result: ProofVerificationResult,
    contract: ProofVerificationContract,
    evidence_artifacts: Iterable[ArtifactRef] = (),
) -> ProofResultValidationResult:
    """Validate backend output before it can support a proof label."""
    reasons: list[str] = []
    contract_validation = validate_proof_contract(contract)
    if not contract_validation.valid:
        reasons.extend(f"contract: {reason}" for reason in contract_validation.reasons)
    if result.candidate_id != contract.candidate_id:
        reasons.append("result candidate_id does not match contract")
    if result.claim_id != contract.claim_id:
        reasons.append("result claim_id does not match contract")
    if result.verified and result.exit_code != 0:
        reasons.append("verified=true requires exit_code == 0")
    if (
        result.exit_code == 0
        and not result.verified
        and result.label == VerificationLabel.LEAN_VERIFIED
    ):
        reasons.append("LeanVerified label requires verified=true")
    if not result.stdout_hash:
        reasons.append("stdout hash is required")
    if not result.stderr_hash:
        reasons.append("stderr hash is required")
    if not result.proof_payload_hash:
        reasons.append("proof payload hash is required")
    if result.forbidden_tokens_present:
        reasons.append("forbidden proof tokens are present")
    if result.backend == "fake" and result.label == VerificationLabel.LEAN_VERIFIED:
        reasons.append("fake backend cannot masquerade as real proof evidence")
    if result.label == VerificationLabel.LEAN_VERIFIED:
        if result.backend == "fake":
            reasons.append("LeanVerified requires a real proof backend")
        if not contract.allow_external_tools:
            reasons.append("LeanVerified requires allow_external_tools=true")
        if result.exit_code != 0:
            reasons.append("LeanVerified requires exit_code == 0")
        if not result.verified:
            reasons.append("LeanVerified requires verified=true")
        if not result.raw_trace_artifact_id:
            reasons.append("LeanVerified requires a raw proof trace artifact")
        if not result.safety_report_artifact_id:
            reasons.append("LeanVerified requires a safety validation artifact")
    artifact_reasons = _proof_artifact_reasons(list(evidence_artifacts))
    reasons.extend(artifact_reasons)
    return ProofResultValidationResult(valid=not reasons, reasons=tuple(reasons))


def proof_label_allowed_by_result(
    result: ProofVerificationResult,
    contract: ProofVerificationContract,
    evidence_artifacts: Iterable[ArtifactRef],
) -> bool:
    """Return whether result plus linked artifacts can justify LeanVerified."""
    validation = validate_proof_result(result, contract, evidence_artifacts)
    return (
        validation.valid
        and result.label == VerificationLabel.LEAN_VERIFIED
        and any(is_proof_evidence(artifact) for artifact in evidence_artifacts)
    )


def _proof_artifact_reasons(artifacts: list[ArtifactRef]) -> list[str]:
    reasons: list[str] = []
    for artifact in artifacts:
        suffix = artifact.path.rsplit(".", maxsplit=1)[-1].lower() if "." in artifact.path else ""
        evidence_role = str(artifact.metadata.get("evidence_role", ""))
        if artifact.type in {ArtifactType.LATEX} or suffix in {"md", "markdown", "tex", "pdf"}:
            reasons.append("presentation artifacts cannot justify proof labels")
        if evidence_role in NON_PROOF_EVIDENCE_ROLES:
            reasons.append(f"{evidence_role} artifacts cannot justify proof labels")
    return reasons


__all__ = [
    "MAX_PROOF_TIMEOUT_SECONDS",
    "ProofContractValidationResult",
    "ProofResultValidationResult",
    "SUPPORTED_PROOF_LANGUAGES",
    "proof_label_allowed_by_result",
    "validate_proof_contract",
    "validate_proof_result",
]
