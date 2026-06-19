"""Deterministic candidate deduplication."""

from __future__ import annotations

import re
from dataclasses import dataclass

from factori.schemas import Candidate, ScoreVector
from factori.scoring import cost_aware_score

DUPLICATE_DISTANCE_THRESHOLD = 0.15
TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class DuplicateDecision:
    """A deterministic duplicate-pruning decision."""

    candidate_id: str
    duplicate_of: str
    distance: float


@dataclass(frozen=True)
class DedupResult:
    """Candidates retained and pruned by deduplication."""

    kept: list[Candidate]
    pruned: list[DuplicateDecision]


def candidate_distance(left: Candidate, right: Candidate) -> float:
    """Return a simple token Jaccard distance between two candidate signatures."""
    left_tokens = _candidate_tokens(left)
    right_tokens = _candidate_tokens(right)
    if not left_tokens and not right_tokens:
        return 0.0
    union = left_tokens | right_tokens
    intersection = left_tokens & right_tokens
    distance = 1.0 - (len(intersection) / len(union))
    if (left.method or "").lower() != (right.method or "").lower():
        distance = max(distance, 0.35)
    return round(distance, 6)


def deduplicate_candidates(
    candidates: list[Candidate],
    scores: dict[str, ScoreVector] | None = None,
    threshold: float = DUPLICATE_DISTANCE_THRESHOLD,
) -> DedupResult:
    """Prune candidates whose distance to a higher-ranked candidate is below threshold."""
    scores = scores or {}
    indexed = list(enumerate(candidates))
    ordered = sorted(
        indexed,
        key=lambda item: (
            -_rank_score(item[1], scores.get(item[1].id)),
            item[0],
        ),
    )

    kept_ranked: list[Candidate] = []
    pruned: list[DuplicateDecision] = []
    for _, candidate in ordered:
        duplicate = _nearest_duplicate(candidate, kept_ranked, threshold)
        if duplicate is None:
            kept_ranked.append(candidate)
        else:
            duplicate_of, distance = duplicate
            pruned.append(
                DuplicateDecision(
                    candidate_id=candidate.id,
                    duplicate_of=duplicate_of.id,
                    distance=distance,
                )
            )

    kept_ids = {candidate.id for candidate in kept_ranked}
    kept_in_original_order = [candidate for candidate in candidates if candidate.id in kept_ids]
    pruned_in_id_order = sorted(pruned, key=lambda decision: decision.candidate_id)
    return DedupResult(kept=kept_in_original_order, pruned=pruned_in_id_order)


def _nearest_duplicate(
    candidate: Candidate,
    kept: list[Candidate],
    threshold: float,
) -> tuple[Candidate, float] | None:
    decisions = [
        (kept_candidate, candidate_distance(candidate, kept_candidate))
        for kept_candidate in kept
    ]
    close = [decision for decision in decisions if decision[1] <= threshold]
    if not close:
        return None
    return min(close, key=lambda decision: (decision[1], decision[0].id))


def _rank_score(candidate: Candidate, score: ScoreVector | None) -> float:
    if score is None:
        return 0.0
    return cost_aware_score(candidate, score)


def _candidate_tokens(candidate: Candidate) -> set[str]:
    signature = " ".join(
        part
        for part in [
            candidate.domain,
            " ".join(candidate.primitives),
            candidate.method,
            candidate.question,
            candidate.hypothesis,
            candidate.theory,
            candidate.experiment,
            candidate.baseline,
            candidate.data_requirement.value,
        ]
        if part
    )
    return set(TOKEN_RE.findall(signature.lower()))
