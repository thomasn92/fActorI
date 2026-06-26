from __future__ import annotations

import pytest
from pydantic import ValidationError

from factori.adapters.experiment_contracts import (
    build_experiment_run_contract,
    experiment_input_spec,
)
from factori.schemas import Candidate, DataRequirement, ExperimentKind


def test_experiment_contract_construction_is_deterministic() -> None:
    candidate = _candidate()

    first = build_experiment_run_contract(candidate, backend="local_synthetic")
    second = build_experiment_run_contract(candidate, backend="local_synthetic")

    assert first == second
    assert first.candidate_id == candidate.id
    assert first.claim_id == f"claim-{candidate.id}"
    assert first.experiment_kind == ExperimentKind.SYNTHETIC_SIMULATION
    assert first.data_regime == DataRequirement.SYNTHETIC_ONLY
    assert first.synthetic_data_spec["external_data"] is False
    assert first.allow_external_tools is True


def test_experiment_input_spec_is_deterministic() -> None:
    contract = build_experiment_run_contract(_candidate(), backend="local_synthetic")

    assert experiment_input_spec(contract) == experiment_input_spec(contract)
    assert experiment_input_spec(contract)["data_regime"] == "SyntheticOnly"
    assert experiment_input_spec(contract)["random_seed"] == contract.random_seed


def test_no_data_contract_uses_sanity_check_kind() -> None:
    candidate = _candidate(data_requirement=DataRequirement.NO_DATA)

    contract = build_experiment_run_contract(candidate)

    assert contract.experiment_kind == ExperimentKind.NO_DATA_SANITY_CHECK
    assert contract.data_regime == DataRequirement.NO_DATA


def test_contract_schema_enforces_bounds() -> None:
    candidate = _candidate()

    with pytest.raises(ValidationError):
        build_experiment_run_contract(candidate, timeout_seconds=0)

    with pytest.raises(ValidationError):
        build_experiment_run_contract(candidate, replications=101)


def _candidate(data_requirement: DataRequirement = DataRequirement.SYNTHETIC_ONLY) -> Candidate:
    return Candidate(
        id="candidate-experiment",
        question="Can a synthetic runner validate controlled behavior?",
        hypothesis="Synthetic-only behavior improves the declared metric.",
        data_requirement=data_requirement,
        method="synthetic simulation",
    )
