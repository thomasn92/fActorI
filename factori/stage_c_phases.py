"""Internal deterministic Stage C verification phases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from factori.adapters.experiment_contracts import build_experiment_run_contract
from factori.adapters.experiment_real import LocalSyntheticExperimentRunner
from factori.adapters.experiment_safety import (
    experiment_label_allowed_by_result,
    validate_experiment_result,
)
from factori.adapters.proof_contracts import build_proof_verification_contract
from factori.adapters.proof_real import LeanProofVerifier
from factori.adapters.proof_safety import proof_label_allowed_by_result, validate_proof_result
from factori.artifacts import ArtifactStore
from factori.evidence import (
    PROOF_EVIDENCE_ROLE,
    REAL_PROOF_EVIDENCE_ROLE,
    REAL_SYNTHETIC_EXPERIMENT_EVIDENCE_ROLE,
    SYNTHETIC_EXPERIMENT_EVIDENCE_ROLE,
    claim_label_allowed,
    is_proof_evidence,
    is_synthetic_experiment_evidence,
)
from factori.experiments_fake import run_fake_synthetic_experiment
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, persist_artifacts_with_commit
from factori.proof_fake import run_fake_proof_validation
from factori.reports import render_stage_c_verification_report
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BranchStatus,
    BranchVerificationType,
    Candidate,
    ControllerActionType,
    DataRequirement,
    ExperimentRunContract,
    ExperimentRunResult,
    FakeExperimentResult,
    FakeProofResult,
    ProofVerificationContract,
    ProofVerificationResult,
    StageCVerificationRecord,
    VerificationLabel,
    VerificationState,
)

if TYPE_CHECKING:
    from factori.adapters.base import ExperimentRunner, ProofVerifier


class StageCError(RuntimeError):
    """Raised when Stage C verification prerequisites are missing."""


@dataclass(frozen=True)
class StageCInputBundle:
    """Loaded Stage C inputs."""

    run_id: str
    stage_c_ready_candidates: list[Candidate]


@dataclass(frozen=True)
class StageCEvidenceClassificationResult:
    """Evidence-boundary classification for one Stage C result."""

    label: VerificationLabel
    status: BranchStatus
    evidence_artifacts: list[ArtifactRef]
    reason: str
    fake: bool


@dataclass(frozen=True)
class StageCProofPhaseResult:
    """Output of the proof-verification phase for one candidate."""

    record: StageCVerificationRecord
    proof_result: FakeProofResult | ProofVerificationResult
    artifacts: list[ArtifactRef]
    evidence_artifacts: list[ArtifactRef]
    proof_contract: ProofVerificationContract | None = None


@dataclass(frozen=True)
class StageCExperimentPhaseResult:
    """Output of the synthetic-experiment phase for one candidate."""

    record: StageCVerificationRecord
    experiment_result: FakeExperimentResult | ExperimentRunResult
    artifacts: list[ArtifactRef]
    evidence_artifacts: list[ArtifactRef]
    experiment_contract: ExperimentRunContract | None = None


@dataclass(frozen=True)
class StageCBranchPhaseResult:
    """Complete deterministic processing result for one Stage C candidate."""

    candidate: Candidate
    record: StageCVerificationRecord
    artifacts: list[ArtifactRef]
    proof_result: FakeProofResult | ProofVerificationResult | None = None
    experiment_result: FakeExperimentResult | ExperimentRunResult | None = None


@dataclass(frozen=True)
class StageCVerificationSummary:
    """Summary payload used by the Stage C report persistence phase."""

    label_counts: dict[str, int]
    fake_proof_runs: int
    real_proof_runs: int
    fake_synthetic_experiments: int
    real_synthetic_experiments: int


def load_stage_c_inputs(run_id: str, ledger: ResearchLedger) -> StageCInputBundle:
    """Load the selected Stage C candidate bundle."""
    return StageCInputBundle(
        run_id=run_id,
        stage_c_ready_candidates=load_stage_c_ready_candidates(run_id, ledger),
    )


def load_stage_c_ready_candidates(run_id: str, ledger: ResearchLedger) -> list[Candidate]:
    """Load candidates selected by the latest Stage C selection budget commit."""
    commits = ledger.list_commits(run_id)
    selection_commit = next(
        (
            commit
            for commit in reversed(commits)
            if commit.action_type == ControllerActionType.STAGE_C_BUDGET_SELECTED
        ),
        None,
    )
    if selection_commit is None:
        raise StageCError("Stage C-ready candidates not found; run factori select-stage-c first")

    selected_ids = list(selection_commit.payload.get("selected_candidate_ids", []))
    if not selected_ids:
        raise StageCError("No Stage C-ready candidates found; run factori select-stage-c first")

    candidates: dict[str, Candidate] = {}
    for commit in commits:
        if commit.action_type != ControllerActionType.STAGE_B_CHILD_GENERATED:
            continue
        payload = commit.payload.get("candidate")
        if payload is None:
            continue
        candidate = Candidate.model_validate(payload)
        candidates[candidate.id] = candidate

    missing = [candidate_id for candidate_id in selected_ids if candidate_id not in candidates]
    if missing:
        raise StageCError(f"Stage C-ready candidate payloads missing: {', '.join(missing)}")

    return [
        candidates[candidate_id].model_copy(update={"status": BranchStatus.STAGE_C_READY})
        for candidate_id in selected_ids
    ]


def classify_stage_c_branch(candidate: Candidate) -> BranchVerificationType:
    """Classify a Stage C branch into a deterministic fake verification route."""
    text = _candidate_text(candidate)
    if any(token in text for token in ["theorem", "conjecture", "proof", "lemma"]):
        return BranchVerificationType.MATHEMATICAL
    if candidate.data_requirement == DataRequirement.SYNTHETIC_ONLY:
        return BranchVerificationType.SYNTHETIC_EMPIRICAL
    if candidate.data_requirement == DataRequirement.NO_DATA:
        return BranchVerificationType.NO_DATA_METHODOLOGICAL
    return BranchVerificationType.UNSUPPORTED


def process_stage_c_candidate(
    *,
    run_id: str,
    candidate: Candidate,
    store: ArtifactStore,
    ledger: ResearchLedger,
    proof_verifier: ProofVerifier | None = None,
    experiment_runner: ExperimentRunner | None = None,
) -> StageCBranchPhaseResult:
    """Run the deterministic Stage C phases for one candidate."""
    branch_type = classify_stage_c_branch(candidate)

    if branch_type == BranchVerificationType.MATHEMATICAL:
        proof_phase = run_stage_c_proof_phase(
            run_id=run_id,
            candidate=candidate,
            branch_type=branch_type,
            proof_verifier=proof_verifier,
            store=store,
            ledger=ledger,
        )
        return StageCBranchPhaseResult(
            candidate=candidate,
            record=proof_phase.record,
            artifacts=list(proof_phase.artifacts),
            proof_result=proof_phase.proof_result,
        )

    if branch_type == BranchVerificationType.SYNTHETIC_EMPIRICAL:
        experiment_phase = run_stage_c_experiment_phase(
            run_id=run_id,
            candidate=candidate,
            branch_type=branch_type,
            experiment_runner=experiment_runner,
            store=store,
            ledger=ledger,
        )
        return StageCBranchPhaseResult(
            candidate=candidate,
            record=experiment_phase.record,
            artifacts=list(experiment_phase.artifacts),
            experiment_result=experiment_phase.experiment_result,
        )

    if branch_type == BranchVerificationType.NO_DATA_METHODOLOGICAL:
        record = run_fake_no_data_methodological_validation(candidate)
        commit_no_data_validation(run_id, record, ledger)
    else:
        record = StageCVerificationRecord(
            candidate_id=candidate.id,
            branch_type=branch_type,
            label=VerificationLabel.UNSUPPORTED,
            status=BranchStatus.STOP_FAILURE,
            reason="branch type is unsupported by the deterministic MVP verifier",
        )

    return StageCBranchPhaseResult(
        candidate=candidate,
        record=record,
        artifacts=[],
    )


def run_stage_c_proof_phase(
    *,
    run_id: str,
    candidate: Candidate,
    branch_type: BranchVerificationType,
    proof_verifier: ProofVerifier | None,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> StageCProofPhaseResult:
    """Run fake or gated real proof verification for one mathematical branch."""
    if isinstance(proof_verifier, LeanProofVerifier):
        proof_result, artifacts, evidence_artifacts, proof_contract = run_real_proof_validation(
            run_id=run_id,
            candidate=candidate,
            proof_verifier=proof_verifier,
            store=store,
            ledger=ledger,
        )
        classification = classify_stage_c_evidence(
            candidate=candidate,
            branch_type=branch_type,
            result=proof_result,
            evidence_artifacts=evidence_artifacts,
            proof_contract=proof_contract,
        )
        record = record_from_evidence_classification(
            candidate=candidate,
            branch_type=branch_type,
            classification=classification,
            proof_result=proof_result,
        )
        return StageCProofPhaseResult(
            record=record,
            proof_result=proof_result,
            artifacts=artifacts,
            evidence_artifacts=evidence_artifacts,
            proof_contract=proof_contract,
        )

    proof_result = run_fake_proof_validation(candidate)
    proof_artifact = write_fake_proof_artifact(run_id, proof_result, store, ledger)
    classification = classify_stage_c_evidence(
        candidate=candidate,
        branch_type=branch_type,
        result=proof_result,
        evidence_artifacts=[proof_artifact],
    )
    record = record_from_evidence_classification(
        candidate=candidate,
        branch_type=branch_type,
        classification=classification,
        proof_result=proof_result,
    )
    return StageCProofPhaseResult(
        record=record,
        proof_result=proof_result,
        artifacts=[proof_artifact],
        evidence_artifacts=[proof_artifact],
    )


def run_stage_c_experiment_phase(
    *,
    run_id: str,
    candidate: Candidate,
    branch_type: BranchVerificationType,
    experiment_runner: ExperimentRunner | None,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> StageCExperimentPhaseResult:
    """Run fake or gated local synthetic experiment verification for one branch."""
    if isinstance(experiment_runner, LocalSyntheticExperimentRunner):
        experiment_result, artifacts, evidence_artifacts, contract = (
            run_real_experiment_validation(
                run_id=run_id,
                candidate=candidate,
                experiment_runner=experiment_runner,
                store=store,
                ledger=ledger,
            )
        )
        classification = classify_stage_c_evidence(
            candidate=candidate,
            branch_type=branch_type,
            result=experiment_result,
            evidence_artifacts=evidence_artifacts,
            experiment_contract=contract,
        )
        record = record_from_evidence_classification(
            candidate=candidate,
            branch_type=branch_type,
            classification=classification,
            experiment_result=experiment_result,
        )
        return StageCExperimentPhaseResult(
            record=record,
            experiment_result=experiment_result,
            artifacts=artifacts,
            evidence_artifacts=evidence_artifacts,
            experiment_contract=contract,
        )

    experiment_result = run_fake_synthetic_experiment(candidate)
    experiment_artifact = write_fake_experiment_artifact(
        run_id,
        experiment_result,
        store,
        ledger,
    )
    classification = classify_stage_c_evidence(
        candidate=candidate,
        branch_type=branch_type,
        result=experiment_result,
        evidence_artifacts=[experiment_artifact],
    )
    record = record_from_evidence_classification(
        candidate=candidate,
        branch_type=branch_type,
        classification=classification,
        experiment_result=experiment_result,
    )
    return StageCExperimentPhaseResult(
        record=record,
        experiment_result=experiment_result,
        artifacts=[experiment_artifact],
        evidence_artifacts=[experiment_artifact],
    )


def classify_stage_c_evidence(
    *,
    candidate: Candidate,
    branch_type: BranchVerificationType,
    result: FakeProofResult
    | ProofVerificationResult
    | FakeExperimentResult
    | ExperimentRunResult,
    evidence_artifacts: list[ArtifactRef],
    proof_contract: ProofVerificationContract | None = None,
    experiment_contract: ExperimentRunContract | None = None,
) -> StageCEvidenceClassificationResult:
    """Apply Stage C evidence-boundary rules without mutating provenance."""
    if isinstance(result, FakeProofResult):
        label = (
            result.label
            if claim_label_allowed(result.label, evidence_artifacts)
            else VerificationLabel.UNSUPPORTED
        )
        return StageCEvidenceClassificationResult(
            label=label,
            status=status_for_label(label),
            evidence_artifacts=evidence_artifacts,
            reason=result.reason if label == result.label else "proof evidence missing",
            fake=True,
        )

    if isinstance(result, ProofVerificationResult):
        if proof_contract is None:
            raise StageCError("proof contract is required for real proof evidence classification")
        allowed = proof_label_allowed_by_result(
            result,
            proof_contract,
            evidence_artifacts,
        )
        label = (
            result.label
            if allowed
            else VerificationLabel.CONJECTURE
            if result.label == VerificationLabel.LEAN_VERIFIED
            else result.label
        )
        return StageCEvidenceClassificationResult(
            label=label,
            status=status_for_label(label),
            evidence_artifacts=evidence_artifacts if label == result.label else [],
            reason=result.reason
            if label == result.label
            else "real proof evidence failed safety validation",
            fake=False,
        )

    if isinstance(result, FakeExperimentResult):
        label = (
            result.label
            if claim_label_allowed(result.label, evidence_artifacts)
            else VerificationLabel.UNSUPPORTED
        )
        return StageCEvidenceClassificationResult(
            label=label,
            status=status_for_label(label),
            evidence_artifacts=evidence_artifacts,
            reason=result.reason if label == result.label else "experiment evidence missing",
            fake=True,
        )

    if experiment_contract is None:
        raise StageCError(
            "experiment contract is required for real synthetic experiment evidence "
            "classification"
        )
    allowed = experiment_label_allowed_by_result(
        result,
        experiment_contract,
        evidence_artifacts,
    )
    label = (
        result.label
        if allowed
        else VerificationLabel.UNSUPPORTED
        if result.label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
        else result.label
    )
    return StageCEvidenceClassificationResult(
        label=label,
        status=status_for_label(label),
        evidence_artifacts=evidence_artifacts if label == result.label else [],
        reason=result.reason
        if label == result.label
        else "real synthetic experiment evidence failed safety validation",
        fake=False,
    )


def record_from_evidence_classification(
    *,
    candidate: Candidate,
    branch_type: BranchVerificationType,
    classification: StageCEvidenceClassificationResult,
    proof_result: FakeProofResult | ProofVerificationResult | None = None,
    experiment_result: FakeExperimentResult | ExperimentRunResult | None = None,
) -> StageCVerificationRecord:
    """Build a Stage C record from an evidence classification."""
    return StageCVerificationRecord(
        candidate_id=candidate.id,
        branch_type=branch_type,
        label=classification.label,
        status=classification.status,
        evidence_artifacts=classification.evidence_artifacts,
        proof_result=proof_result,
        experiment_result=experiment_result,
        reason=classification.reason,
        fake=classification.fake,
    )


def run_fake_no_data_methodological_validation(candidate: Candidate) -> StageCVerificationRecord:
    """Assign a conservative fake label to non-mathematical NoData branches."""
    text = _candidate_text(candidate)
    if "unsupported" in text or "vague" in text:
        label = VerificationLabel.UNSUPPORTED
        status = BranchStatus.STOP_FAILURE
        reason = "fake no-data methodological branch is too vague to support"
    elif "limitation" in text:
        label = VerificationLabel.LIMITATION
        status = BranchStatus.ACTIVE
        reason = "fake no-data methodological branch is retained only as a limitation"
    else:
        label = VerificationLabel.CONJECTURE
        status = BranchStatus.ACTIVE
        reason = "fake no-data methodological branch has no proof or experiment evidence"
    return StageCVerificationRecord(
        candidate_id=candidate.id,
        branch_type=BranchVerificationType.NO_DATA_METHODOLOGICAL,
        label=label,
        status=status,
        reason=reason,
    )


def run_real_proof_validation(
    *,
    run_id: str,
    candidate: Candidate,
    proof_verifier: LeanProofVerifier,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> tuple[
    ProofVerificationResult,
    list[ArtifactRef],
    list[ArtifactRef],
    ProofVerificationContract,
]:
    """Run the gated proof adapter and persist its proof artifacts."""
    contract = build_proof_verification_contract(
        candidate,
        backend=proof_verifier.backend_name,
        timeout_seconds=proof_verifier.timeout_seconds,
        tool_name=proof_verifier.proof_executable,
    )
    adapter_run = proof_verifier.verify_contract(contract)
    contract = adapter_run.contract
    candidate_id = candidate.id
    contract_id = f"proof-contract-{candidate_id}"
    payload_id = f"proof-payload-{candidate_id}"
    trace_id = f"proof-trace-{candidate_id}"
    result_id = f"proof-result-{candidate_id}"
    safety_id = f"proof-safety-{candidate_id}"
    result = adapter_run.result.model_copy(
        update={
            "raw_trace_artifact_id": trace_id,
            "safety_report_artifact_id": safety_id,
        }
    )
    safety = validate_proof_result(result, contract)
    proof_evidence_enabled = safety.valid and result.label == VerificationLabel.LEAN_VERIFIED
    evidence_metadata = (
        {
            "stage": "stage_c",
            "backend": result.backend,
            "provider": result.provider,
            "evidence_role": REAL_PROOF_EVIDENCE_ROLE,
            "is_verification_evidence": True,
            "fake": False,
        }
        if proof_evidence_enabled
        else {
            "stage": "stage_c",
            "backend": result.backend,
            "provider": result.provider,
            "is_verification_evidence": False,
            "fake": False,
        }
    )
    result_payload = result.model_dump(mode="json")
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                artifact_id=contract_id,
                artifact_type=ArtifactType.LEAN,
                payload=contract,
                artifact_format="json",
                metadata={
                    "stage": "stage_c",
                    "backend": contract.backend,
                    "provider": "lean",
                    "is_verification_evidence": False,
                    "fake": False,
                },
            ),
            ArtifactWriteSpec(
                artifact_id=payload_id,
                artifact_type=ArtifactType.LEAN,
                payload={
                    "candidate_id": candidate_id,
                    "claim_id": contract.claim_id,
                    "proof_language": contract.proof_language,
                    "proof_payload_text": contract.proof_payload_text,
                    "is_verification_evidence": False,
                },
                artifact_format="json",
                metadata={
                    "stage": "stage_c",
                    "backend": contract.backend,
                    "provider": "lean",
                    "is_verification_evidence": False,
                    "fake": False,
                },
            ),
            ArtifactWriteSpec(
                artifact_id=trace_id,
                artifact_type=ArtifactType.LEAN,
                payload=adapter_run.trace,
                artifact_format="json",
                metadata=evidence_metadata,
            ),
            ArtifactWriteSpec(
                artifact_id=result_id,
                artifact_type=ArtifactType.LEAN,
                payload=result,
                artifact_format="json",
                metadata=evidence_metadata,
            ),
            ArtifactWriteSpec(
                artifact_id=safety_id,
                artifact_type=ArtifactType.LEAN,
                payload={
                    "candidate_id": candidate_id,
                    "claim_id": contract.claim_id,
                    "contract_valid": adapter_run.contract_validation.valid,
                    "contract_reasons": list(adapter_run.contract_validation.reasons),
                    "result_valid": safety.valid,
                    "result_reasons": list(safety.reasons),
                    "is_verification_evidence": False,
                    "fake": False,
                },
                artifact_format="json",
                metadata={
                    "stage": "stage_c",
                    "backend": result.backend,
                    "provider": result.provider,
                    "is_verification_evidence": False,
                    "fake": False,
                },
            ),
        ],
        action_type=ControllerActionType.STAGE_C_PROOF_VALIDATED,
        commit_payload={
            **result_payload,
            "proof_backend": result.backend,
            "proof_provider": result.provider,
            "proof_contract_id": contract_id,
            "proof_result_id": result_id,
        },
        candidate_id=candidate_id,
    )
    evidence_artifacts = [
        artifact for artifact in persistence.artifacts if is_proof_evidence(artifact)
    ]
    return result, persistence.artifacts, evidence_artifacts, contract


def run_real_experiment_validation(
    *,
    run_id: str,
    candidate: Candidate,
    experiment_runner: LocalSyntheticExperimentRunner,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> tuple[
    ExperimentRunResult,
    list[ArtifactRef],
    list[ArtifactRef],
    ExperimentRunContract,
]:
    """Run the gated local synthetic experiment adapter and persist its artifacts."""
    contract = build_experiment_run_contract(
        candidate,
        backend=experiment_runner.backend_name,
        timeout_seconds=experiment_runner.timeout_seconds,
        replications=experiment_runner.replications,
        runner_name=experiment_runner.runner_name,
    )
    adapter_run = experiment_runner.run_contract(contract)
    contract = adapter_run.contract
    candidate_id = candidate.id
    contract_id = f"experiment-contract-{candidate_id}"
    input_id = f"experiment-input-{candidate_id}"
    trace_id = f"experiment-trace-{candidate_id}"
    output_id = f"experiment-output-{candidate_id}"
    result_id = f"experiment-result-{candidate_id}"
    safety_id = f"experiment-safety-{candidate_id}"
    result = adapter_run.result.model_copy(
        update={
            "raw_trace_artifact_id": trace_id,
            "safety_report_artifact_id": safety_id,
        }
    )
    safety = validate_experiment_result(result, contract)
    experiment_evidence_enabled = (
        safety.valid
        and result.label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
    )
    evidence_metadata = (
        {
            "stage": "stage_c",
            "backend": result.backend,
            "provider": result.provider,
            "evidence_role": REAL_SYNTHETIC_EXPERIMENT_EVIDENCE_ROLE,
            "is_verification_evidence": True,
            "fake": False,
        }
        if experiment_evidence_enabled
        else {
            "stage": "stage_c",
            "backend": result.backend,
            "provider": result.provider,
            "is_verification_evidence": False,
            "fake": False,
        }
    )
    result_payload = result.model_dump(mode="json")
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                artifact_id=contract_id,
                artifact_type=ArtifactType.EXPERIMENT,
                payload=contract,
                artifact_format="json",
                metadata={
                    "stage": "stage_c",
                    "backend": contract.backend,
                    "provider": "local",
                    "is_verification_evidence": False,
                    "fake": False,
                },
            ),
            ArtifactWriteSpec(
                artifact_id=input_id,
                artifact_type=ArtifactType.EXPERIMENT,
                payload=adapter_run.input_spec,
                artifact_format="json",
                metadata={
                    "stage": "stage_c",
                    "backend": contract.backend,
                    "provider": "local",
                    "is_verification_evidence": False,
                    "fake": False,
                },
            ),
            ArtifactWriteSpec(
                artifact_id=trace_id,
                artifact_type=ArtifactType.EXPERIMENT,
                payload=adapter_run.trace,
                artifact_format="json",
                metadata=evidence_metadata,
            ),
            ArtifactWriteSpec(
                artifact_id=output_id,
                artifact_type=ArtifactType.EXPERIMENT,
                payload=adapter_run.output_payload,
                artifact_format="json",
                metadata=evidence_metadata,
            ),
            ArtifactWriteSpec(
                artifact_id=result_id,
                artifact_type=ArtifactType.EXPERIMENT,
                payload=result,
                artifact_format="json",
                metadata=evidence_metadata,
            ),
            ArtifactWriteSpec(
                artifact_id=safety_id,
                artifact_type=ArtifactType.EXPERIMENT,
                payload={
                    "candidate_id": candidate_id,
                    "claim_id": contract.claim_id,
                    "contract_valid": adapter_run.contract_validation.valid,
                    "contract_reasons": list(adapter_run.contract_validation.reasons),
                    "result_valid": safety.valid,
                    "result_reasons": list(safety.reasons),
                    "is_verification_evidence": False,
                    "fake": False,
                },
                artifact_format="json",
                metadata={
                    "stage": "stage_c",
                    "backend": result.backend,
                    "provider": result.provider,
                    "is_verification_evidence": False,
                    "fake": False,
                },
            ),
        ],
        action_type=ControllerActionType.STAGE_C_SYNTHETIC_EXPERIMENT_RUN,
        commit_payload={
            **result_payload,
            "experiment_backend": result.backend,
            "experiment_provider": result.provider,
            "experiment_contract_id": contract_id,
            "experiment_result_id": result_id,
        },
        candidate_id=candidate_id,
    )
    evidence_artifacts = [
        artifact for artifact in persistence.artifacts if is_synthetic_experiment_evidence(artifact)
    ]
    return result, persistence.artifacts, evidence_artifacts, contract


def write_fake_proof_artifact(
    run_id: str,
    proof_result: FakeProofResult,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    """Persist a deterministic fake proof result artifact."""
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"fake-proof-{proof_result.candidate_id}",
        artifact_type=ArtifactType.LEAN,
        data=proof_result,
        metadata={
            "stage": "stage_c",
            "fake": True,
            "evidence_role": PROOF_EVIDENCE_ROLE,
        },
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=proof_result.candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_PROOF_VALIDATED,
        payload=proof_result.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def write_fake_experiment_artifact(
    run_id: str,
    experiment_result: FakeExperimentResult,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    """Persist a deterministic fake synthetic experiment result artifact."""
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"fake-synthetic-experiment-{experiment_result.candidate_id}",
        artifact_type=ArtifactType.EXPERIMENT,
        data=experiment_result,
        metadata={
            "stage": "stage_c",
            "fake": True,
            "evidence_role": SYNTHETIC_EXPERIMENT_EVIDENCE_ROLE,
        },
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=experiment_result.candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_SYNTHETIC_EXPERIMENT_RUN,
        payload=experiment_result.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def commit_no_data_validation(
    run_id: str,
    record: StageCVerificationRecord,
    ledger: ResearchLedger,
) -> None:
    """Append the no-data validation ledger commit."""
    ledger.append_commit(
        run_id=run_id,
        candidate_id=record.candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_NO_DATA_VALIDATED,
        payload=record.model_dump(mode="json"),
    )


def persist_stage_c_verification_decision(
    run_id: str,
    record: StageCVerificationRecord,
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> ArtifactRef:
    """Persist the per-candidate Stage C verification decision artifact."""
    artifact = store.write_json(
        run_id=run_id,
        artifact_id=f"stage-c-verification-{record.candidate_id}",
        artifact_type=ArtifactType.REPORT,
        data=record,
        metadata={
            "stage": "stage_c",
            "fake": record.fake,
            "report": "verification_decision",
        },
    )
    commit = ledger.append_commit(
        run_id=run_id,
        candidate_id=record.candidate_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_VERIFICATION_DECIDED,
        payload=record.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash)


def build_stage_c_verification_summary(
    *,
    verification_records: dict[str, StageCVerificationRecord],
    proof_results: dict[str, FakeProofResult | ProofVerificationResult],
    experiment_results: dict[str, FakeExperimentResult | ExperimentRunResult],
) -> StageCVerificationSummary:
    """Build deterministic Stage C verification summary counts."""
    return StageCVerificationSummary(
        label_counts=label_counts(verification_records.values()),
        fake_proof_runs=sum(
            1 for result in proof_results.values() if isinstance(result, FakeProofResult)
        ),
        real_proof_runs=sum(
            1
            for result in proof_results.values()
            if isinstance(result, ProofVerificationResult)
        ),
        fake_synthetic_experiments=sum(
            1
            for result in experiment_results.values()
            if isinstance(result, FakeExperimentResult)
        ),
        real_synthetic_experiments=sum(
            1
            for result in experiment_results.values()
            if isinstance(result, ExperimentRunResult)
        ),
    )


def persist_stage_c_report(
    *,
    run_id: str,
    stage_c_ready: list[Candidate],
    verification_records: dict[str, StageCVerificationRecord],
    proof_results: dict[str, FakeProofResult | ProofVerificationResult],
    experiment_results: dict[str, FakeExperimentResult | ExperimentRunResult],
    proof_backend_metadata: dict[str, object],
    experiment_backend_metadata: dict[str, object],
    store: ArtifactStore,
    ledger: ResearchLedger,
) -> tuple[ArtifactRef, str]:
    """Persist the Stage C Markdown report and report ledger commit."""
    markdown = render_stage_c_verification_report(
        run_id=run_id,
        stage_c_ready=stage_c_ready,
        verification_records=verification_records,
        proof_results=proof_results,
        experiment_results=experiment_results,
        proof_backend_metadata=proof_backend_metadata,
        experiment_backend_metadata=experiment_backend_metadata,
    )
    artifact = store.write_markdown(
        run_id=run_id,
        artifact_id="stage-c-verification-report",
        artifact_type=ArtifactType.REPORT,
        markdown=markdown,
        metadata={"stage": "stage_c", "fake": True},
    )
    summary = build_stage_c_verification_summary(
        verification_records=verification_records,
        proof_results=proof_results,
        experiment_results=experiment_results,
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_VERIFICATION_REPORT_WRITTEN,
        payload={
            "stage_c_ready": len(stage_c_ready),
            "fake_proof_runs": summary.fake_proof_runs,
            "real_proof_runs": summary.real_proof_runs,
            "fake_synthetic_experiments": summary.fake_synthetic_experiments,
            "real_synthetic_experiments": summary.real_synthetic_experiments,
            "proof_backend_metadata": proof_backend_metadata,
            "experiment_backend_metadata": experiment_backend_metadata,
            **summary.label_counts,
        },
        artifact_refs=[artifact],
    )
    return store.link_artifact_to_commit(artifact, commit.commit_hash), commit.commit_hash


def candidate_with_verification(
    candidate: Candidate,
    record: StageCVerificationRecord,
) -> Candidate:
    """Return a candidate copy with Stage C verification state attached."""
    return candidate.model_copy(
        update={
            "verification": VerificationState(
                labels=[record.label],
                evidence_artifacts=record.evidence_artifacts,
                notes=record.reason,
            ),
            "status": record.status,
        }
    )


def status_for_label(label: VerificationLabel) -> BranchStatus:
    """Map a Stage C verification label to branch status."""
    if label in {
        VerificationLabel.LEAN_VERIFIED,
        VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
    }:
        return BranchStatus.STOP_SUCCESS
    if label in {VerificationLabel.NEGATIVE_RESULT, VerificationLabel.UNSUPPORTED}:
        return BranchStatus.STOP_FAILURE
    return BranchStatus.ACTIVE


def label_counts(records) -> dict[str, int]:
    """Count Stage C verification labels for the report commit payload."""
    labels = [record.label for record in records]
    return {
        "lean_verified": labels.count(VerificationLabel.LEAN_VERIFIED),
        "synthetic_experiment_verified": labels.count(
            VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
        ),
        "negative_results": labels.count(VerificationLabel.NEGATIVE_RESULT),
        "conjectures": labels.count(VerificationLabel.CONJECTURE),
        "limitations": labels.count(VerificationLabel.LIMITATION),
        "unsupported": labels.count(VerificationLabel.UNSUPPORTED),
    }


def proof_backend_metadata(proof_verifier: ProofVerifier | None) -> dict[str, object]:
    """Return provider-neutral proof backend metadata for Stage C reports."""
    if isinstance(proof_verifier, LeanProofVerifier):
        return {
            "backend": proof_verifier.backend_name,
            "provider": proof_verifier.provider_name,
            "tool_name": proof_verifier.proof_executable,
            "allow_external_tools": proof_verifier.allow_external_tools,
            "fake": False,
        }
    return {
        "backend": "fake",
        "provider": "fake",
        "allow_external_tools": False,
        "fake": True,
    }


def experiment_backend_metadata(
    experiment_runner: ExperimentRunner | None,
) -> dict[str, object]:
    """Return provider-neutral experiment backend metadata for Stage C reports."""
    if isinstance(experiment_runner, LocalSyntheticExperimentRunner):
        return {
            "backend": experiment_runner.backend_name,
            "provider": experiment_runner.provider_name,
            "runner_name": experiment_runner.runner_name,
            "allow_external_tools": experiment_runner.allow_external_tools,
            "fake": False,
        }
    return {
        "backend": "fake",
        "provider": "fake",
        "allow_external_tools": False,
        "fake": True,
    }


def _candidate_text(candidate: Candidate) -> str:
    parts = [
        candidate.id,
        candidate.method or "",
        candidate.question,
        candidate.hypothesis or "",
        candidate.theory or "",
        candidate.experiment or "",
        candidate.variant_type or "",
        " ".join(str(value) for value in candidate.symbolic_state.values()),
    ]
    return " ".join(parts).lower()


__all__ = [
    "StageCBranchPhaseResult",
    "StageCError",
    "StageCEvidenceClassificationResult",
    "StageCExperimentPhaseResult",
    "StageCInputBundle",
    "StageCProofPhaseResult",
    "StageCVerificationSummary",
    "build_stage_c_verification_summary",
    "candidate_with_verification",
    "classify_stage_c_branch",
    "classify_stage_c_evidence",
    "commit_no_data_validation",
    "experiment_backend_metadata",
    "label_counts",
    "load_stage_c_inputs",
    "load_stage_c_ready_candidates",
    "persist_stage_c_report",
    "persist_stage_c_verification_decision",
    "process_stage_c_candidate",
    "proof_backend_metadata",
    "record_from_evidence_classification",
    "run_fake_no_data_methodological_validation",
    "run_real_experiment_validation",
    "run_real_proof_validation",
    "run_stage_c_experiment_phase",
    "run_stage_c_proof_phase",
    "status_for_label",
    "write_fake_experiment_artifact",
    "write_fake_proof_artifact",
]
