"""Deterministic fake synthetic experiment validator for Stage C."""

from __future__ import annotations

from factori.schemas import Candidate, DataRequirement, FakeExperimentResult, VerificationLabel

PREDECLARED_DELTA = 0.05


def run_fake_synthetic_experiment(candidate: Candidate) -> FakeExperimentResult:
    """Return a deterministic fake synthetic experiment result without running Docker."""
    metric_value, baseline_value, ablation_passed, baseline_strong = _fake_metrics(candidate)
    delta = round(metric_value - baseline_value, 6)
    lcb_95 = round(delta - 0.03, 6)

    if candidate.data_requirement != DataRequirement.SYNTHETIC_ONLY:
        label = VerificationLabel.LIMITATION
        reason = "candidate is not a SyntheticOnly empirical branch"
    elif (
        delta >= PREDECLARED_DELTA
        and lcb_95 >= 0
        and ablation_passed
        and baseline_strong
    ):
        label = VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
        reason = "fake synthetic contract met delta, LCB, ablation, and baseline checks"
    elif not ablation_passed or not baseline_strong:
        label = VerificationLabel.LIMITATION
        reason = "fake synthetic contract has weak baseline or failed ablation"
    elif baseline_strong:
        label = VerificationLabel.NEGATIVE_RESULT
        reason = "fake synthetic contract failed the predeclared performance threshold"
    else:
        label = VerificationLabel.LIMITATION
        reason = "fake synthetic contract has weak baseline or failed ablation"

    return FakeExperimentResult(
        candidate_id=candidate.id,
        experiment_id=f"fake-synthetic-experiment-{candidate.id}",
        generator_name="deterministic_synthetic_contract_v1",
        generator_parameters={
            "n": 256,
            "noise": 0.05,
            "regime": "mvp_synthetic_only",
        },
        seed=_deterministic_seed(candidate.id),
        metric_name="fake_regret_reduction",
        metric_value=metric_value,
        baseline_value=baseline_value,
        delta=delta,
        predeclared_delta=PREDECLARED_DELTA,
        lcb_95=lcb_95,
        ablation_passed=ablation_passed,
        baseline_strong=baseline_strong,
        label=label,
        reason=reason,
    )


def _fake_metrics(candidate: Candidate) -> tuple[float, float, bool, bool]:
    text = _candidate_text(candidate)
    if "weak-baseline" in text:
        return 0.68, 0.62, True, False
    if "ablation-fail" in text:
        return 0.72, 0.64, False, True
    if "negative" in text or "experiment-fail" in text:
        return 0.62, 0.63, True, True
    offset = (_deterministic_seed(candidate.id) % 7) / 1000.0
    return round(0.73 + offset, 6), 0.64, True, True


def _deterministic_seed(candidate_id: str) -> int:
    return sum((index + 1) * ord(char) for index, char in enumerate(candidate_id)) % 10_000


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
