"""Deterministic fake scoring for the Stage A skeleton."""

from __future__ import annotations

from factori.schemas import Candidate, DataRequirement, ScoreVector

STAGE_A_NOVELTY_MIN = 0.35
STAGE_A_FEASIBILITY_MIN = 0.60
STAGE_A_VERIFIABILITY_MIN = 0.50

METHOD_PROFILES: dict[str, tuple[float, float, float, float, float, float]] = {
    "optimal transport": (0.74, 0.68, 0.64, 0.58, 0.54, 0.66),
    "graph curvature": (0.67, 0.61, 0.58, 0.55, 0.58, 0.69),
    "spatial statistics": (0.58, 0.78, 0.72, 0.60, 0.43, 0.54),
    "wasserstein robustness": (0.72, 0.66, 0.62, 0.57, 0.56, 0.64),
    "model dispersion": (0.63, 0.62, 0.57, 0.54, 0.50, 0.58),
    "synthetic stress testing": (0.60, 0.74, 0.76, 0.61, 0.45, 0.52),
    "calibration": (0.55, 0.76, 0.74, 0.58, 0.42, 0.50),
    "distribution shift": (0.68, 0.64, 0.61, 0.57, 0.55, 0.63),
    "uncertainty quantification": (0.64, 0.70, 0.69, 0.56, 0.48, 0.57),
}

DEFAULT_PROFILE = (0.50, 0.63, 0.55, 0.50, 0.50, 0.50)


def score_candidate(candidate: Candidate) -> ScoreVector:
    """Return a deterministic fake score vector for a candidate."""
    profile = METHOD_PROFILES.get((candidate.method or "").lower(), DEFAULT_PROFILE)
    novelty, feasibility, verifiability, reviewer, difficulty, diversity = profile

    if candidate.data_requirement == DataRequirement.NO_DATA:
        feasibility += 0.04
        verifiability += 0.03
        difficulty -= 0.04
    elif candidate.data_requirement == DataRequirement.SYNTHETIC_ONLY:
        feasibility += 0.02
        verifiability += 0.08
        difficulty += 0.02
    elif candidate.data_requirement == DataRequirement.PUBLIC_DOWNLOAD:
        feasibility -= 0.20
        verifiability -= 0.10
        difficulty += 0.12
    elif candidate.data_requirement == DataRequirement.USER_PROVIDED:
        feasibility -= 0.30
        verifiability -= 0.15
        difficulty += 0.18

    if "duplicate" in candidate.id:
        reviewer -= 0.02

    return ScoreVector(
        novelty=_clamp(novelty),
        feasibility=_clamp(feasibility),
        verifiability=_clamp(verifiability),
        reviewer=_clamp(reviewer),
        difficulty=_clamp(difficulty),
        diversity=_clamp(diversity),
        uncertainty=0.10,
    )


def stage_a_cost(candidate: Candidate) -> float:
    """Return a deterministic fake normalized cost."""
    cost_by_data = {
        DataRequirement.NO_DATA: 0.50,
        DataRequirement.SYNTHETIC_ONLY: 1.00,
        DataRequirement.PUBLIC_DOWNLOAD: 2.50,
        DataRequirement.USER_PROVIDED: 3.00,
    }
    return cost_by_data[candidate.data_requirement]


def cost_aware_score(candidate: Candidate, score: ScoreVector) -> float:
    """Return the Stage A ranking score with the MVP cost penalty."""
    return score.base_score() / (1.0 + 0.05 * stage_a_cost(candidate))


def passes_stage_a_gate(score: ScoreVector) -> bool:
    """Return whether a score vector passes the Stage A gate."""
    return (
        score.novelty >= STAGE_A_NOVELTY_MIN
        and score.feasibility >= STAGE_A_FEASIBILITY_MIN
        and score.verifiability >= STAGE_A_VERIFIABILITY_MIN
    )


def score_payload(candidate: Candidate, score: ScoreVector) -> dict[str, object]:
    """Return the JSON payload stored for score artifacts."""
    return {
        "candidate_id": candidate.id,
        "score": score.model_dump(mode="json"),
        "base_score": round(score.base_score(), 6),
        "cost": stage_a_cost(candidate),
        "cost_aware_score": round(cost_aware_score(candidate, score), 6),
        "fake": True,
    }


def _clamp(value: float) -> float:
    return round(min(1.0, max(0.0, value)), 4)
