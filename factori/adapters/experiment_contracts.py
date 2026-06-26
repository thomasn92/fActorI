"""Deterministic synthetic experiment contract construction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from factori.config import (
    DEFAULT_EXPERIMENT_REPLICATIONS,
    DEFAULT_EXPERIMENT_TIMEOUT_SECONDS,
)
from factori.experiments_fake import PREDECLARED_DELTA
from factori.schemas import (
    Candidate,
    DataRequirement,
    ExperimentKind,
    ExperimentRunContract,
)

DEFAULT_EXPERIMENT_KIND = ExperimentKind.SYNTHETIC_SIMULATION
DEFAULT_EXPECTED_OUTPUT_TYPE = "synthetic_experiment_result"
DEFAULT_FORBIDDEN_EXTERNAL_INPUTS = (
    "PublicDownload",
    "UserProvided",
    "RealWorldData",
    "network",
    "absolute_path",
)


def build_experiment_run_contract(
    candidate: Candidate,
    *,
    backend: str = "fake",
    experiment_kind: ExperimentKind | None = None,
    data_regime: DataRequirement | None = None,
    synthetic_data_spec: Mapping[str, Any] | None = None,
    model_spec: Mapping[str, Any] | None = None,
    algorithm_spec: Mapping[str, Any] | None = None,
    metrics: Sequence[str] = ("delta", "lcb_95"),
    acceptance_criteria: Mapping[str, Any] | None = None,
    random_seed: int | None = None,
    replications: int = DEFAULT_EXPERIMENT_REPLICATIONS,
    timeout_seconds: int = DEFAULT_EXPERIMENT_TIMEOUT_SECONDS,
    runner_name: str | None = None,
    forbidden_external_inputs: Sequence[str] = DEFAULT_FORBIDDEN_EXTERNAL_INPUTS,
) -> ExperimentRunContract:
    """Build a deterministic contract for a candidate's synthetic claim."""
    regime = data_regime or candidate.data_requirement
    kind = experiment_kind or (
        ExperimentKind.NO_DATA_SANITY_CHECK
        if regime == DataRequirement.NO_DATA
        else DEFAULT_EXPERIMENT_KIND
    )
    seed = random_seed if random_seed is not None else _deterministic_seed(candidate.id)
    return ExperimentRunContract(
        candidate_id=candidate.id,
        claim_id=f"claim-{candidate.id}",
        experiment_id=f"synthetic-experiment-{candidate.id}",
        experiment_kind=kind,
        data_regime=regime,
        synthetic_data_spec=dict(
            synthetic_data_spec
            if synthetic_data_spec is not None
            else _default_synthetic_data_spec(candidate, seed)
        ),
        model_spec=dict(
            model_spec
            if model_spec is not None
            else {
                "candidate_method": candidate.method or "unspecified",
                "claim_text": candidate.hypothesis or candidate.question,
            }
        ),
        algorithm_spec=dict(
            algorithm_spec
            if algorithm_spec is not None
            else {
                "runner": "deterministic-local-synthetic",
                "no_external_inputs": True,
            }
        ),
        metrics=list(metrics),
        acceptance_criteria=dict(
            acceptance_criteria
            if acceptance_criteria is not None
            else {
                "delta": {"min": PREDECLARED_DELTA},
                "lcb_95": {"min": 0.0},
            }
        ),
        random_seed=seed,
        replications=replications,
        timeout_seconds=timeout_seconds,
        backend=backend,
        runner_name=runner_name,
        forbidden_external_inputs=list(forbidden_external_inputs),
        expected_output_type=DEFAULT_EXPECTED_OUTPUT_TYPE,
        allow_external_tools=backend != "fake",
        fake_default=backend == "fake",
        is_verification_evidence=False,
    )


def experiment_input_spec(contract: ExperimentRunContract) -> dict[str, Any]:
    """Return the deterministic input payload handed to local synthetic runners."""
    return {
        "candidate_id": contract.candidate_id,
        "claim_id": contract.claim_id,
        "experiment_id": contract.experiment_id,
        "experiment_kind": contract.experiment_kind.value,
        "data_regime": contract.data_regime.value,
        "synthetic_data_spec": contract.synthetic_data_spec,
        "model_spec": contract.model_spec,
        "algorithm_spec": contract.algorithm_spec,
        "metrics": contract.metrics,
        "acceptance_criteria": contract.acceptance_criteria,
        "random_seed": contract.random_seed,
        "replications": contract.replications,
    }


def _default_synthetic_data_spec(candidate: Candidate, seed: int) -> dict[str, Any]:
    if candidate.data_requirement == DataRequirement.NO_DATA:
        return {}
    return {
        "regime": DataRequirement.SYNTHETIC_ONLY.value,
        "samples": 256,
        "noise": 0.05,
        "seed": seed,
        "external_data": False,
    }


def _deterministic_seed(candidate_id: str) -> int:
    return sum((index + 1) * ord(char) for index, char in enumerate(candidate_id)) % 10_000


__all__ = [
    "DEFAULT_EXPECTED_OUTPUT_TYPE",
    "DEFAULT_EXPERIMENT_KIND",
    "DEFAULT_FORBIDDEN_EXTERNAL_INPUTS",
    "build_experiment_run_contract",
    "experiment_input_spec",
]
