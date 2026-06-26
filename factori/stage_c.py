"""Deterministic fake Stage C verification skeleton."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from factori.artifacts import ArtifactStore
from factori.ledger import ResearchLedger
from factori.schemas import (
    ArtifactRef,
    Candidate,
    ControllerActionType,
    ExperimentRunResult,
    FakeExperimentResult,
    FakeProofResult,
    ProofVerificationResult,
    StageCVerificationRecord,
)
from factori.stage_c_phases import (
    StageCError,
    candidate_with_verification,
    classify_stage_c_branch,
    experiment_backend_metadata,
    load_stage_c_inputs,
    load_stage_c_ready_candidates,
    persist_stage_c_report,
    persist_stage_c_verification_decision,
    process_stage_c_candidate,
    proof_backend_metadata,
    run_fake_no_data_methodological_validation,
)

if TYPE_CHECKING:
    from factori.adapters.base import ExperimentRunner, ProofVerifier


@dataclass(frozen=True)
class StageCResult:
    """Result of deterministic fake Stage C verification."""

    run_id: str
    stage_c_ready_candidates: list[Candidate]
    verified_candidates: list[Candidate]
    verification_records: dict[str, StageCVerificationRecord]
    proof_results: dict[str, FakeProofResult | ProofVerificationResult]
    experiment_results: dict[str, FakeExperimentResult | ExperimentRunResult]
    artifacts: dict[str, list[ArtifactRef]]
    report_artifact: ArtifactRef
    report_commit_hash: str
    proof_backend_metadata: dict[str, object]
    experiment_backend_metadata: dict[str, object]


def run_stage_c(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    proof_verifier: ProofVerifier | None = None,
    experiment_runner: ExperimentRunner | None = None,
) -> StageCResult:
    """Run deterministic fake Stage C verification for selected candidates."""
    store.init_run(run_id)
    input_bundle = load_stage_c_inputs(run_id, ledger)
    stage_c_ready = input_bundle.stage_c_ready_candidates
    proof_metadata = proof_backend_metadata(proof_verifier)
    experiment_metadata = experiment_backend_metadata(experiment_runner)
    ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.STAGE_C_VERIFICATION_STARTED,
        payload={"stage_c_ready_candidate_ids": [candidate.id for candidate in stage_c_ready]},
    )

    verified_candidates: list[Candidate] = []
    verification_records: dict[str, StageCVerificationRecord] = {}
    proof_results: dict[str, FakeProofResult | ProofVerificationResult] = {}
    experiment_results: dict[str, FakeExperimentResult | ExperimentRunResult] = {}
    artifacts: dict[str, list[ArtifactRef]] = {}

    for candidate in stage_c_ready:
        branch_result = process_stage_c_candidate(
            run_id=run_id,
            candidate=candidate,
            store=store,
            ledger=ledger,
            proof_verifier=proof_verifier,
            experiment_runner=experiment_runner,
        )
        verification_artifact = persist_stage_c_verification_decision(
            run_id,
            branch_result.record,
            store,
            ledger,
        )
        candidate_artifacts = [*branch_result.artifacts, verification_artifact]
        artifacts[candidate.id] = candidate_artifacts
        verification_records[candidate.id] = branch_result.record
        if branch_result.proof_result is not None:
            proof_results[candidate.id] = branch_result.proof_result
        if branch_result.experiment_result is not None:
            experiment_results[candidate.id] = branch_result.experiment_result
        verified_candidates.append(
            candidate_with_verification(candidate, branch_result.record)
        )

    report_artifact, report_commit_hash = persist_stage_c_report(
        run_id=run_id,
        stage_c_ready=stage_c_ready,
        verification_records=verification_records,
        proof_results=proof_results,
        experiment_results=experiment_results,
        proof_backend_metadata=proof_metadata,
        experiment_backend_metadata=experiment_metadata,
        store=store,
        ledger=ledger,
    )

    return StageCResult(
        run_id=run_id,
        stage_c_ready_candidates=stage_c_ready,
        verified_candidates=verified_candidates,
        verification_records=verification_records,
        proof_results=proof_results,
        experiment_results=experiment_results,
        artifacts=artifacts,
        report_artifact=report_artifact,
        report_commit_hash=report_commit_hash,
        proof_backend_metadata=proof_metadata,
        experiment_backend_metadata=experiment_metadata,
    )


__all__ = [
    "StageCError",
    "StageCResult",
    "classify_stage_c_branch",
    "load_stage_c_ready_candidates",
    "run_fake_no_data_methodological_validation",
    "run_stage_c",
]
