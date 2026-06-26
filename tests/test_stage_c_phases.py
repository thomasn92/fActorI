from __future__ import annotations

from dataclasses import dataclass

from factori.adapters.experiment_real import (
    ExperimentToolRunResult,
    LocalSyntheticExperimentRunner,
)
from factori.adapters.proof_contracts import build_proof_verification_contract
from factori.adapters.proof_real import LeanProofVerifier, ProofToolRunResult
from factori.artifacts import ArtifactStore
from factori.hashing import sha256_text
from factori.ledger import ResearchLedger
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BranchVerificationType,
    Candidate,
    ConstraintSet,
    ControllerActionType,
    DataRequirement,
    ProofVerificationResult,
    VerificationLabel,
)
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_phases import (
    StageCBranchPhaseResult,
    StageCExperimentPhaseResult,
    StageCInputBundle,
    StageCProofPhaseResult,
    build_stage_c_verification_summary,
    classify_stage_c_evidence,
    load_stage_c_inputs,
    persist_stage_c_verification_decision,
    process_stage_c_candidate,
    run_stage_c_experiment_phase,
    run_stage_c_proof_phase,
)
from factori.stage_c_selection import run_stage_c_selection

HASH = "a" * 64


@dataclass
class RecordingProofRunner:
    calls: int = 0

    def run(self, **kwargs) -> ProofToolRunResult:
        self.calls += 1
        assert kwargs["executable"] == "lean"
        return ProofToolRunResult(
            exit_code=0,
            stdout="proof accepted",
            stderr="",
            elapsed_ms=5,
            tool_version="lean-test-version",
        )


@dataclass
class RecordingExperimentRunner:
    calls: int = 0

    def run(self, **kwargs) -> ExperimentToolRunResult:
        self.calls += 1
        assert kwargs["executable"] == "local-runner"
        metrics = {"delta": 0.08, "lcb_95": 0.02}
        return ExperimentToolRunResult(
            exit_code=0,
            stdout='{"metrics":{"delta":0.08,"lcb_95":0.02}}',
            stderr="",
            output_payload={"metrics": metrics, "synthetic_only": True},
            metrics=metrics,
            elapsed_ms=7,
            runner_version="local-test-version",
        )


def test_stage_c_phase_module_imports() -> None:
    assert StageCInputBundle
    assert StageCProofPhaseResult
    assert StageCExperimentPhaseResult
    assert StageCBranchPhaseResult


def test_input_loading_phase_returns_selected_candidates(tmp_path) -> None:
    store, ledger = _run_stage_c_selection_pipeline(tmp_path)

    bundle = load_stage_c_inputs("run-1", ledger)

    assert bundle.run_id == "run-1"
    assert len(bundle.stage_c_ready_candidates) == 1
    assert bundle.stage_c_ready_candidates[0].id
    assert store.run_path("run-1").is_dir()


def test_proof_phase_preserves_fake_deterministic_default(tmp_path) -> None:
    store, ledger = _empty_run(tmp_path)
    candidate = _proof_candidate()

    phase = run_stage_c_proof_phase(
        run_id="run-1",
        candidate=candidate,
        branch_type=BranchVerificationType.MATHEMATICAL,
        proof_verifier=None,
        store=store,
        ledger=ledger,
    )

    assert phase.record.label == VerificationLabel.LEAN_VERIFIED
    assert phase.record.evidence_artifacts
    assert phase.proof_result.fake is True
    assert [artifact.id for artifact in phase.artifacts] == [f"fake-proof-{candidate.id}"]


def test_process_candidate_routes_to_expected_phase(tmp_path) -> None:
    store, ledger = _empty_run(tmp_path)
    candidate = _proof_candidate()

    branch = process_stage_c_candidate(
        run_id="run-1",
        candidate=candidate,
        store=store,
        ledger=ledger,
    )

    assert branch.record.branch_type == BranchVerificationType.MATHEMATICAL
    assert branch.proof_result is not None
    assert branch.experiment_result is None


def test_proof_phase_uses_injected_real_runner_without_external_execution(tmp_path) -> None:
    store, ledger = _empty_run(tmp_path)
    runner = RecordingProofRunner()
    verifier = LeanProofVerifier(
        proof_executable="lean",
        runner=runner,
        allow_external_tools=True,
    )
    candidate = _proof_candidate()

    phase = run_stage_c_proof_phase(
        run_id="run-1",
        candidate=candidate,
        branch_type=BranchVerificationType.MATHEMATICAL,
        proof_verifier=verifier,
        store=store,
        ledger=ledger,
    )

    assert runner.calls == 1
    assert phase.record.label == VerificationLabel.LEAN_VERIFIED
    assert {artifact.id for artifact in phase.artifacts} == {
        f"proof-contract-{candidate.id}",
        f"proof-payload-{candidate.id}",
        f"proof-trace-{candidate.id}",
        f"proof-result-{candidate.id}",
        f"proof-safety-{candidate.id}",
    }


def test_proof_classification_rejects_reviewer_artifacts_as_proof_evidence() -> None:
    candidate = _proof_candidate()
    contract = build_proof_verification_contract(
        candidate,
        backend="lean",
        tool_name="lean",
    )
    result = _real_proof_result(candidate)
    reviewer_artifact = ArtifactRef(
        id="llm-reviewer-output",
        type=ArtifactType.REPORT,
        path="runs/run-1/reports/llm-reviewer-output.json",
        content_hash=HASH,
        producing_commit_hash=HASH,
        metadata={
            "evidence_role": "llm_reviewer",
            "is_verification_evidence": True,
        },
    )

    classification = classify_stage_c_evidence(
        candidate=candidate,
        branch_type=BranchVerificationType.MATHEMATICAL,
        result=result,
        evidence_artifacts=[reviewer_artifact],
        proof_contract=contract,
    )

    assert classification.label == VerificationLabel.CONJECTURE
    assert classification.evidence_artifacts == []


def test_experiment_phase_preserves_fake_deterministic_default(tmp_path) -> None:
    store, ledger = _empty_run(tmp_path)
    candidate = _experiment_candidate()

    phase = run_stage_c_experiment_phase(
        run_id="run-1",
        candidate=candidate,
        branch_type=BranchVerificationType.SYNTHETIC_EMPIRICAL,
        experiment_runner=None,
        store=store,
        ledger=ledger,
    )

    assert phase.record.label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
    assert phase.experiment_result.fake is True
    assert [artifact.id for artifact in phase.artifacts] == [
        f"fake-synthetic-experiment-{candidate.id}"
    ]


def test_experiment_phase_uses_injected_runner_without_external_execution(tmp_path) -> None:
    store, ledger = _empty_run(tmp_path)
    runner = RecordingExperimentRunner()
    adapter = LocalSyntheticExperimentRunner(
        runner_name="local-runner",
        runner=runner,
        allow_external_tools=True,
    )
    candidate = _experiment_candidate()

    phase = run_stage_c_experiment_phase(
        run_id="run-1",
        candidate=candidate,
        branch_type=BranchVerificationType.SYNTHETIC_EMPIRICAL,
        experiment_runner=adapter,
        store=store,
        ledger=ledger,
    )

    assert runner.calls == 1
    assert phase.record.label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
    assert phase.record.label != VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED
    assert {artifact.id for artifact in phase.artifacts} == {
        f"experiment-contract-{candidate.id}",
        f"experiment-input-{candidate.id}",
        f"experiment-trace-{candidate.id}",
        f"experiment-output-{candidate.id}",
        f"experiment-result-{candidate.id}",
        f"experiment-safety-{candidate.id}",
    }


def test_experiment_classification_rejects_retrieval_artifacts_as_experiment_evidence(
    tmp_path,
) -> None:
    store, ledger = _empty_run(tmp_path)
    candidate = _experiment_candidate()
    adapter = LocalSyntheticExperimentRunner(
        runner_name="local-runner",
        runner=RecordingExperimentRunner(),
        allow_external_tools=True,
    )
    phase = run_stage_c_experiment_phase(
        run_id="run-1",
        candidate=candidate,
        branch_type=BranchVerificationType.SYNTHETIC_EMPIRICAL,
        experiment_runner=adapter,
        store=store,
        ledger=ledger,
    )
    retrieval_artifact = ArtifactRef(
        id="retrieval-result",
        type=ArtifactType.LITERATURE,
        path="runs/run-1/reports/retrieval-result.json",
        content_hash=HASH,
        producing_commit_hash=HASH,
        metadata={
            "evidence_role": "retrieval_evidence",
            "is_verification_evidence": True,
        },
    )

    classification = classify_stage_c_evidence(
        candidate=candidate,
        branch_type=BranchVerificationType.SYNTHETIC_EMPIRICAL,
        result=phase.experiment_result,
        evidence_artifacts=[retrieval_artifact],
        experiment_contract=phase.experiment_contract,
    )

    assert classification.label == VerificationLabel.UNSUPPORTED
    assert classification.evidence_artifacts == []


def test_evidence_classification_is_deterministic_for_valid_proof() -> None:
    candidate = _proof_candidate()
    contract = build_proof_verification_contract(candidate, backend="lean", tool_name="lean")
    result = _real_proof_result(candidate)
    artifact = _proof_artifact(candidate)

    first = classify_stage_c_evidence(
        candidate=candidate,
        branch_type=BranchVerificationType.MATHEMATICAL,
        result=result,
        evidence_artifacts=[artifact],
        proof_contract=contract,
    )
    second = classify_stage_c_evidence(
        candidate=candidate,
        branch_type=BranchVerificationType.MATHEMATICAL,
        result=result,
        evidence_artifacts=[artifact],
        proof_contract=contract,
    )

    assert first == second
    assert first.label == VerificationLabel.LEAN_VERIFIED


def test_persistence_phase_writes_expected_decision_artifact_id(tmp_path) -> None:
    store, ledger = _empty_run(tmp_path)
    candidate = _proof_candidate()
    phase = run_stage_c_proof_phase(
        run_id="run-1",
        candidate=candidate,
        branch_type=BranchVerificationType.MATHEMATICAL,
        proof_verifier=None,
        store=store,
        ledger=ledger,
    )

    artifact = persist_stage_c_verification_decision(
        "run-1",
        phase.record,
        store,
        ledger,
    )

    assert artifact.id == f"stage-c-verification-{candidate.id}"
    assert artifact.producing_commit_hash


def test_public_run_stage_c_result_shape_and_summary_are_stable(tmp_path) -> None:
    store, ledger = _run_stage_c_selection_pipeline(tmp_path)

    result = run_stage_c(run_id="run-1", store=store, ledger=ledger)
    summary = build_stage_c_verification_summary(
        verification_records=result.verification_records,
        proof_results=result.proof_results,
        experiment_results=result.experiment_results,
    )

    assert result.stage_c_ready_candidates
    assert result.verified_candidates
    assert result.report_artifact.id == "stage-c-verification-report"
    assert summary.label_counts["lean_verified"] == 1


def _empty_run(tmp_path) -> tuple[ArtifactStore, ResearchLedger]:
    store = ArtifactStore(tmp_path)
    run_id = "run-1"
    store.init_run(run_id)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    ledger.append_commit(
        run_id=run_id,
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": run_id},
        timestamp="1970-01-01T00:00:00.000000Z",
    )
    return store, ledger


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


def _proof_candidate() -> Candidate:
    return Candidate(
        id="candidate-proof",
        question="Can a local theorem be checked?",
        theory="Theorem-style proof",
    )


def _experiment_candidate() -> Candidate:
    return Candidate(
        id="candidate-experiment",
        constraints=ConstraintSet(domain="synthetic methods"),
        domain="synthetic methods",
        method="synthetic simulation",
        question="Can controlled simulation improve the declared metric?",
        hypothesis="Synthetic-only behavior improves the declared metric.",
        experiment="Synthetic simulation with deterministic acceptance criteria.",
        data_requirement=DataRequirement.SYNTHETIC_ONLY,
    )


def _real_proof_result(candidate: Candidate) -> ProofVerificationResult:
    contract = build_proof_verification_contract(candidate, backend="lean", tool_name="lean")
    return ProofVerificationResult(
        candidate_id=candidate.id,
        claim_id=contract.claim_id,
        backend="lean",
        provider="lean",
        proof_language="Lean",
        tool_name="lean",
        exit_code=0,
        stdout_hash=sha256_text("proof accepted"),
        stderr_hash=sha256_text(""),
        proof_payload_hash=sha256_text(contract.proof_payload_text or ""),
        forbidden_tokens_present=False,
        verified=True,
        label=VerificationLabel.LEAN_VERIFIED,
        reason="real proof backend reported success and safety checks can be applied",
        raw_trace_artifact_id=f"proof-trace-{candidate.id}",
        safety_report_artifact_id=f"proof-safety-{candidate.id}",
    )


def _proof_artifact(candidate: Candidate) -> ArtifactRef:
    return ArtifactRef(
        id=f"proof-result-{candidate.id}",
        type=ArtifactType.LEAN,
        path=f"runs/run-1/lean/proof-result-{candidate.id}.json",
        content_hash=HASH,
        producing_commit_hash=HASH,
        metadata={
            "evidence_role": "proof",
            "is_verification_evidence": True,
        },
    )
