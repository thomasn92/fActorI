"""Gated local proof-tool adapter with injectable execution."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from factori.adapters.errors import (
    AdapterExternalCallsDisabled,
    AdapterTransportError,
)
from factori.adapters.proof_contracts import (
    build_proof_verification_contract,
    proof_payload_text,
)
from factori.adapters.proof_safety import (
    ProofContractValidationResult,
    ProofResultValidationResult,
    validate_proof_contract,
    validate_proof_result,
)
from factori.hashing import sha256_text
from factori.schemas import (
    Candidate,
    ProofVerificationContract,
    ProofVerificationResult,
    VerificationLabel,
)


@dataclass(frozen=True)
class ProofToolRunResult:
    """Captured output from an injected proof-tool runner."""

    exit_code: int
    stdout: str
    stderr: str
    elapsed_ms: int = 0
    tool_version: str | None = None


class ProofToolRunner(Protocol):
    """Minimal proof-tool runner seam used by tests and future local tools."""

    def run(
        self,
        *,
        executable: str,
        args: Sequence[str],
        input_text: str,
        timeout_seconds: int,
    ) -> ProofToolRunResult: ...


class SubprocessProofToolRunner:
    """Local subprocess runner. It is only invoked after explicit external-tool opt-in."""

    def run(
        self,
        *,
        executable: str,
        args: Sequence[str],
        input_text: str,
        timeout_seconds: int,
    ) -> ProofToolRunResult:
        started = time.monotonic()
        try:
            completed = subprocess.run(
                [executable, *args],
                input=input_text,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise AdapterTransportError(
                backend="lean",
                provider="lean",
                operation="verify_proof",
                message=str(exc),
                cause=exc,
            ) from exc
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return ProofToolRunResult(
            exit_code=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
            elapsed_ms=elapsed_ms,
        )


@dataclass(frozen=True)
class ProofAdapterRun:
    """Complete adapter output before Stage C persists artifacts."""

    contract: ProofVerificationContract
    result: ProofVerificationResult
    trace: dict[str, Any]
    contract_validation: ProofContractValidationResult
    result_validation: ProofResultValidationResult


@dataclass(frozen=True)
class LeanProofVerifier:
    """Gated Lean proof verifier adapter with no default execution in fake runs."""

    proof_executable: str
    runner: ProofToolRunner | None = None
    timeout_seconds: int = 10
    allow_external_tools: bool = False
    backend_name: str = "lean"
    provider_name: str = "lean"
    is_fake: bool = False
    external_calls_enabled: bool = False

    def verify_proof(
        self,
        candidate: Candidate,
        proof_payload: Mapping[str, Any],
    ) -> ProofVerificationResult:
        """Protocol-compatible proof verification entry point."""
        contract = build_proof_verification_contract(
            candidate,
            backend=self.backend_name,
            proof_payload_text=_payload_text_from_mapping(proof_payload),
            proof_payload_path=_optional_text(proof_payload.get("proof_payload_path")),
            timeout_seconds=int(proof_payload.get("timeout_seconds", self.timeout_seconds)),
            tool_name=self.proof_executable,
        )
        return self.verify_contract(contract).result

    def verify_contract(self, contract: ProofVerificationContract) -> ProofAdapterRun:
        """Validate, run the injected tool, and parse a proof result deterministically."""
        if not self.allow_external_tools:
            raise AdapterExternalCallsDisabled(
                "External proof tools are disabled. Set allow_external_tools=true to use "
                "real proof adapters."
            )
        prepared_contract = contract.model_copy(
            update={
                "backend": self.backend_name,
                "tool_name": contract.tool_name or self.proof_executable,
                "allow_external_tools": True,
                "fake_default": False,
                "is_verification_evidence": False,
            }
        )
        contract_validation = validate_proof_contract(prepared_contract)
        payload_text = proof_payload_text(prepared_contract)
        if contract_validation.valid:
            run_result = (self.runner or SubprocessProofToolRunner()).run(
                executable=self.proof_executable,
                args=(),
                input_text=payload_text,
                timeout_seconds=prepared_contract.timeout_seconds,
            )
        else:
            run_result = ProofToolRunResult(
                exit_code=1,
                stdout="",
                stderr="; ".join(contract_validation.reasons),
            )
        result = parse_proof_tool_run(
            contract=prepared_contract,
            run_result=run_result,
            forbidden_tokens_present=contract_validation.forbidden_tokens_present,
        ).model_copy(
            update={
                "raw_trace_artifact_id": f"proof-trace-{prepared_contract.candidate_id}",
                "safety_report_artifact_id": f"proof-safety-{prepared_contract.candidate_id}",
            }
        )
        result_validation = validate_proof_result(result, prepared_contract)
        trace = {
            "backend": self.backend_name,
            "provider": self.provider_name,
            "tool_name": self.proof_executable,
            "exit_code": run_result.exit_code,
            "stdout": run_result.stdout,
            "stderr": run_result.stderr,
            "elapsed_ms": run_result.elapsed_ms,
            "tool_version": run_result.tool_version,
            "fake": False,
            "is_verification_evidence": False,
        }
        return ProofAdapterRun(
            contract=prepared_contract,
            result=result,
            trace=trace,
            contract_validation=contract_validation,
            result_validation=result_validation,
        )


def parse_proof_tool_run(
    *,
    contract: ProofVerificationContract,
    run_result: ProofToolRunResult,
    forbidden_tokens_present: bool,
) -> ProofVerificationResult:
    """Convert runner output into a provider-neutral proof result."""
    payload_text = proof_payload_text(contract)
    verified = run_result.exit_code == 0 and not forbidden_tokens_present
    label = VerificationLabel.LEAN_VERIFIED if verified else VerificationLabel.CONJECTURE
    reason = (
        "real proof backend reported success and safety checks can be applied"
        if verified
        else "proof backend did not produce a validated proof"
    )
    return ProofVerificationResult(
        candidate_id=contract.candidate_id,
        claim_id=contract.claim_id,
        backend=contract.backend,
        provider="lean",
        proof_language=contract.proof_language,
        tool_name=contract.tool_name or "lean",
        tool_version=run_result.tool_version,
        exit_code=run_result.exit_code,
        stdout_hash=sha256_text(run_result.stdout),
        stderr_hash=sha256_text(run_result.stderr),
        proof_payload_hash=sha256_text(payload_text),
        forbidden_tokens_present=forbidden_tokens_present,
        verified=verified,
        label=label,
        reason=reason,
        elapsed_ms=run_result.elapsed_ms,
    )


def _payload_text_from_mapping(payload: Mapping[str, Any]) -> str | None:
    for key in ("proof_payload_text", "text", "proof"):
        value = payload.get(key)
        if value is not None:
            return str(value)
    return None


def _optional_text(value: Any) -> str | None:
    return None if value is None else str(value)


__all__ = [
    "LeanProofVerifier",
    "ProofAdapterRun",
    "ProofToolRunResult",
    "ProofToolRunner",
    "SubprocessProofToolRunner",
    "parse_proof_tool_run",
]
