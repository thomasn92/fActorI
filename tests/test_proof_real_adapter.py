from __future__ import annotations

from dataclasses import dataclass

import pytest

from factori.adapters.config import AdapterConfig
from factori.adapters.errors import (
    AdapterConfigurationError,
    AdapterExternalCallsDisabled,
)
from factori.adapters.proof_contracts import build_proof_verification_contract
from factori.adapters.proof_real import (
    LeanProofVerifier,
    ProofToolRunResult,
    parse_proof_tool_run,
)
from factori.adapters.registry import get_adapter_registry
from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.schemas import (
    ArtifactType,
    Candidate,
    ConstraintSet,
    ControllerActionType,
    VerificationLabel,
)
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_selection import run_stage_c_selection


@dataclass
class RecordingProofRunner:
    exit_code: int = 0
    stdout: str = "proof accepted"
    stderr: str = ""
    calls: int = 0

    def run(self, **kwargs) -> ProofToolRunResult:
        self.calls += 1
        assert kwargs["executable"] == "lean"
        assert kwargs["timeout_seconds"] >= 1
        return ProofToolRunResult(
            exit_code=self.exit_code,
            stdout=self.stdout,
            stderr=self.stderr,
            elapsed_ms=7,
            tool_version="lean-test-version",
        )


def test_fake_proof_backend_remains_default() -> None:
    registry = get_adapter_registry()

    assert registry.config.proof_backend == "fake"
    assert registry.config.allow_external_tools is False
    assert registry.class_names()["proof_verifier"] == "FakeProofVerifier"


def test_real_proof_backend_fails_if_external_tools_disabled() -> None:
    with pytest.raises(AdapterExternalCallsDisabled, match="External proof tools are disabled"):
        get_adapter_registry(
            AdapterConfig(
                proof_backend="lean",
                allow_external_tools=False,
                proof_executable="lean",
            ),
            proof_runner=RecordingProofRunner(),
        )


def test_real_proof_backend_fails_if_executable_missing() -> None:
    with pytest.raises(AdapterConfigurationError, match="proof executable"):
        get_adapter_registry(
            AdapterConfig(
                proof_backend="lean",
                allow_external_tools=True,
                proof_executable="definitely-not-a-proof-tool",
            )
        )


def test_real_proof_backend_uses_injected_runner_without_external_binary() -> None:
    runner = RecordingProofRunner()
    registry = get_adapter_registry(
        AdapterConfig(
            proof_backend="lean",
            allow_external_tools=True,
            proof_executable="lean",
        ),
        proof_runner=runner,
    )
    verifier = registry.proof_verifier
    candidate = _candidate()
    contract = build_proof_verification_contract(candidate, backend="lean", tool_name="lean")

    adapter_run = verifier.verify_contract(contract)

    assert isinstance(verifier, LeanProofVerifier)
    assert runner.calls == 1
    assert adapter_run.result.label == VerificationLabel.LEAN_VERIFIED
    assert adapter_run.result.stdout_hash
    assert adapter_run.result.stderr_hash
    assert adapter_run.result.proof_payload_hash
    assert adapter_run.contract_validation.valid is True
    assert adapter_run.result_validation.valid is True


def test_failed_real_proof_becomes_conjecture() -> None:
    verifier = LeanProofVerifier(
        proof_executable="lean",
        runner=RecordingProofRunner(exit_code=1, stderr="proof failed"),
        allow_external_tools=True,
    )
    adapter_run = verifier.verify_contract(
        build_proof_verification_contract(_candidate(), backend="lean", tool_name="lean")
    )

    assert adapter_run.result.label == VerificationLabel.CONJECTURE
    assert adapter_run.result.verified is False


def test_parse_proof_tool_run_is_deterministic() -> None:
    contract = build_proof_verification_contract(_candidate(), backend="lean", tool_name="lean")
    run_result = ProofToolRunResult(exit_code=0, stdout="ok", stderr="", elapsed_ms=3)

    first = parse_proof_tool_run(
        contract=contract,
        run_result=run_result,
        forbidden_tokens_present=False,
    )
    second = parse_proof_tool_run(
        contract=contract,
        run_result=run_result,
        forbidden_tokens_present=False,
    )

    assert first == second
    assert first.label == VerificationLabel.LEAN_VERIFIED


def test_stage_c_with_injected_proof_runner_writes_real_proof_artifacts(tmp_path) -> None:
    store, ledger = _run_stage_c_selection_pipeline(tmp_path)
    verifier = LeanProofVerifier(
        proof_executable="lean",
        runner=RecordingProofRunner(),
        allow_external_tools=True,
    )

    result = run_stage_c(
        run_id="run-1",
        store=store,
        ledger=ledger,
        proof_verifier=verifier,
    )

    assert result.proof_backend_metadata["backend"] == "lean"
    labels = [record.label for record in result.verification_records.values()]
    assert labels == [VerificationLabel.LEAN_VERIFIED]
    artifacts = [artifact for values in result.artifacts.values() for artifact in values]
    proof_artifact_ids = {
        artifact.id for artifact in artifacts if artifact.type == ArtifactType.LEAN
    }
    candidate_id = result.stage_c_ready_candidates[0].id
    assert {
        f"proof-contract-{candidate_id}",
        f"proof-payload-{candidate_id}",
        f"proof-trace-{candidate_id}",
        f"proof-result-{candidate_id}",
        f"proof-safety-{candidate_id}",
    } <= proof_artifact_ids
    proof_evidence = [
        artifact
        for artifact in artifacts
        if artifact.id == f"proof-result-{candidate_id}"
    ][0]
    assert proof_evidence.metadata["evidence_role"] == "proof"
    assert proof_evidence.producing_commit_hash
    assert len(proof_evidence.content_hash) == 64

    proof_commits = [
        commit
        for commit in ledger.list_commits("run-1")
        if commit.action_type == ControllerActionType.STAGE_C_PROOF_VALIDATED
    ]
    assert proof_commits[-1].payload["proof_backend"] == "lean"
    assert len(proof_commits[-1].artifact_refs) == 5


def _candidate() -> Candidate:
    return Candidate(
        id="candidate-proof",
        question="Can a local theorem be checked?",
        theory="Theorem-style proof",
    )


def _run_stage_c_selection_pipeline(tmp_path) -> tuple[ArtifactStore, ResearchLedger]:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )
    run_stage_b(run_id="run-1", store=store, ledger=ledger)
    run_stage_c_selection(run_id="run-1", store=store, ledger=ledger)
    return store, ledger
