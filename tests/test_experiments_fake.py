from __future__ import annotations

from factori.experiments_fake import PREDECLARED_DELTA, run_fake_synthetic_experiment
from factori.schemas import Candidate, DataRequirement, VerificationLabel


def test_fake_synthetic_experiment_is_deterministic() -> None:
    candidate = _synthetic_candidate("candidate-synthetic")

    first = run_fake_synthetic_experiment(candidate)
    second = run_fake_synthetic_experiment(candidate)

    assert first == second


def test_synthetic_experiment_verified_requires_all_checks() -> None:
    result = run_fake_synthetic_experiment(_synthetic_candidate("candidate-synthetic"))

    assert result.delta >= PREDECLARED_DELTA
    assert result.lcb_95 >= 0
    assert result.ablation_passed
    assert result.baseline_strong
    assert result.label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED


def test_failed_synthetic_experiment_becomes_negative_result_or_limitation() -> None:
    negative = run_fake_synthetic_experiment(_synthetic_candidate("candidate-negative"))
    limitation = run_fake_synthetic_experiment(_synthetic_candidate("candidate-weak-baseline"))
    ablation = run_fake_synthetic_experiment(_synthetic_candidate("candidate-ablation-fail"))

    assert negative.label == VerificationLabel.NEGATIVE_RESULT
    assert limitation.label == VerificationLabel.LIMITATION
    assert ablation.label == VerificationLabel.LIMITATION


def test_real_data_experiment_verified_is_never_produced_in_mvp() -> None:
    result = run_fake_synthetic_experiment(
        Candidate(
            id="candidate-public-data",
            question="Does this require public data?",
            data_requirement=DataRequirement.PUBLIC_DOWNLOAD,
        )
    )

    assert result.label != VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED


def _synthetic_candidate(candidate_id: str) -> Candidate:
    return Candidate(
        id=candidate_id,
        question="Can a synthetic empirical contract validate this branch?",
        experiment="Deterministic synthetic contract",
        data_requirement=DataRequirement.SYNTHETIC_ONLY,
    )
