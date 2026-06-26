from __future__ import annotations

from factori.adapters.experiment_contracts import build_experiment_run_contract
from factori.adapters.experiment_real import ExperimentToolRunResult, parse_experiment_tool_run
from factori.adapters.experiment_safety import (
    experiment_label_allowed_by_result,
    validate_experiment_contract,
    validate_experiment_result,
)
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    Candidate,
    DataRequirement,
    ExperimentRunResult,
    VerificationLabel,
)

HASH = "0" * 64


def test_contract_validation_rejects_missing_claim_id() -> None:
    contract = build_experiment_run_contract(_candidate()).model_copy(
        update={"claim_id": ""}
    )

    validation = validate_experiment_contract(contract)

    assert validation.valid is False
    assert "claim_id is required" in validation.reasons


def test_contract_validation_rejects_missing_synthetic_data_spec() -> None:
    contract = build_experiment_run_contract(_candidate()).model_copy(
        update={"synthetic_data_spec": {}}
    )

    validation = validate_experiment_contract(contract)

    assert validation.valid is False
    assert "SyntheticOnly experiments require a synthetic_data_spec" in validation.reasons


def test_contract_validation_rejects_public_or_user_data_regimes() -> None:
    for regime in [DataRequirement.PUBLIC_DOWNLOAD, DataRequirement.USER_PROVIDED]:
        contract = build_experiment_run_contract(_candidate()).model_copy(
            update={"data_regime": regime}
        )

        validation = validate_experiment_contract(contract)

        assert validation.valid is False
        assert (
            "MVP experiments allow only NoData or SyntheticOnly data regimes"
            in validation.reasons
        )


def test_contract_validation_rejects_network_and_absolute_inputs() -> None:
    network_contract = build_experiment_run_contract(_candidate()).model_copy(
        update={"algorithm_spec": {"source": "https://example.invalid/data.json"}}
    )
    absolute_contract = build_experiment_run_contract(_candidate()).model_copy(
        update={"synthetic_data_spec": {"path": "/tmp/real.csv"}}
    )

    assert "network access" in " ".join(validate_experiment_contract(network_contract).reasons)
    assert "absolute external inputs" in " ".join(
        validate_experiment_contract(absolute_contract).reasons
    )


def test_result_validation_rejects_fake_masquerade_and_real_data_label() -> None:
    contract = build_experiment_run_contract(_candidate(), backend="fake")
    result = _verified_result(contract).model_copy(update={"backend": "fake"})

    validation = validate_experiment_result(result, contract)

    assert validation.valid is False
    assert "fake backend cannot masquerade as real experiment evidence" in validation.reasons

    real_label = result.model_copy(
        update={"label": VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED}
    )
    real_validation = validate_experiment_result(real_label, contract)
    assert (
        "synthetic experiments cannot produce RealDataExperimentVerified"
        in real_validation.reasons
    )


def test_result_validation_rejects_missing_hashes() -> None:
    contract = build_experiment_run_contract(_candidate(), backend="local_synthetic")
    result = _verified_result(contract).model_copy(update={"stdout_hash": ""})

    validation = validate_experiment_result(result, contract)

    assert validation.valid is False
    assert "stdout hash is required" in validation.reasons


def test_result_validation_rejects_llm_reviewer_retrieval_or_proof_evidence() -> None:
    contract = build_experiment_run_contract(_candidate(), backend="local_synthetic")
    result = _verified_result(contract)
    bad_artifacts = [
        ArtifactRef(
            id=f"artifact-{role}",
            type=ArtifactType.REPORT,
            path=f"runs/run-1/reports/{role}.json",
            content_hash=HASH,
            producing_commit_hash=HASH,
            metadata={"evidence_role": role, "is_verification_evidence": True},
        )
        for role in ["llm_reviewer", "retrieval_evidence", "proof"]
    ]

    validation = validate_experiment_result(result, contract, bad_artifacts)

    assert validation.valid is False
    assert any("cannot justify experiment labels" in reason for reason in validation.reasons)


def test_synthetic_experiment_label_requires_linked_evidence() -> None:
    contract = build_experiment_run_contract(_candidate(), backend="local_synthetic")
    result = _verified_result(contract)
    evidence = ArtifactRef(
        id="experiment-result-candidate-experiment",
        type=ArtifactType.EXPERIMENT,
        path="runs/run-1/experiments/experiment-result-candidate-experiment.json",
        content_hash=HASH,
        producing_commit_hash=HASH,
        metadata={
            "evidence_role": "synthetic_experiment",
            "is_verification_evidence": True,
        },
    )

    assert experiment_label_allowed_by_result(result, contract, [evidence]) is True
    assert experiment_label_allowed_by_result(result, contract, []) is False


def test_parse_experiment_tool_run_is_deterministic() -> None:
    contract = build_experiment_run_contract(_candidate(), backend="local_synthetic")
    run_result = ExperimentToolRunResult(
        exit_code=0,
        stdout='{"metrics":{"delta":0.08,"lcb_95":0.02}}',
        output_payload={"metrics": {"delta": 0.08, "lcb_95": 0.02}},
        metrics={"delta": 0.08, "lcb_95": 0.02},
    )
    input_spec = {"candidate_id": contract.candidate_id}

    first = parse_experiment_tool_run(
        contract=contract,
        run_result=run_result,
        input_spec=input_spec,
    )
    second = parse_experiment_tool_run(
        contract=contract,
        run_result=run_result,
        input_spec=input_spec,
    )

    assert first == second
    assert first.label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED


def _candidate() -> Candidate:
    return Candidate(
        id="candidate-experiment",
        question="Can a synthetic runner validate controlled behavior?",
        hypothesis="Synthetic-only behavior improves the declared metric.",
        data_requirement=DataRequirement.SYNTHETIC_ONLY,
        method="synthetic simulation",
    )


def _verified_result(contract) -> ExperimentRunResult:
    return ExperimentRunResult(
        candidate_id=contract.candidate_id,
        claim_id=contract.claim_id,
        experiment_id=contract.experiment_id,
        backend="local_synthetic",
        provider="local",
        experiment_kind=contract.experiment_kind,
        data_regime=contract.data_regime,
        runner_name="local_synthetic",
        exit_code=0,
        stdout_hash=HASH,
        stderr_hash=HASH,
        input_spec_hash=HASH,
        output_payload_hash=HASH,
        metrics={"delta": 0.08, "lcb_95": 0.02},
        acceptance_criteria=contract.acceptance_criteria,
        passed=True,
        label=VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
        reason="valid synthetic run",
        raw_trace_artifact_id="experiment-trace-candidate-experiment",
        safety_report_artifact_id="experiment-safety-candidate-experiment",
    )
