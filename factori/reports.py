"""Markdown report rendering for deterministic fake stages."""

from __future__ import annotations

from factori.dedup import DuplicateDecision
from factori.schemas import Candidate, ScoreVector
from factori.scoring import cost_aware_score


def render_opportunity_report(
    *,
    domain: str,
    primitives: list[str],
    opportunities: list[dict[str, object]],
    promoted_methods: list[str],
) -> str:
    """Render the fake Stage 0 opportunity report."""
    lines = [
        "# Stage 0 Opportunity Discovery",
        "",
        f"Domain: `{domain}`",
        "",
        "## Fake Primitives",
        "",
        *[f"- {primitive}" for primitive in primitives],
        "",
        "## Domain-Method Scores",
        "",
        "| Method | Opportunity Score | Promoted |",
        "| --- | ---: | --- |",
    ]
    for opportunity in opportunities:
        method = str(opportunity["method"])
        score = float(opportunity["opportunity_score"])
        promoted = "yes" if method in promoted_methods else "no"
        lines.append(f"| {method} | {score:.2f} | {promoted} |")
    return "\n".join(lines) + "\n"


def render_stage_a_report(
    *,
    run_id: str,
    generated_candidates: list[Candidate],
    deferred_candidates: list[Candidate],
    duplicate_decisions: list[DuplicateDecision],
    gate_pruned_candidates: list[Candidate],
    passing_candidates: list[Candidate],
    survivors: list[Candidate],
    scores: dict[str, ScoreVector],
) -> str:
    """Render the ranked Stage A report."""
    lines = [
        "# Stage A Report",
        "",
        f"Run: `{run_id}`",
        "",
        "## Summary",
        "",
        f"- Generated candidates: {len(generated_candidates)}",
        f"- Deferred by data gate: {len(deferred_candidates)}",
        f"- Pruned as duplicate: {len(duplicate_decisions)}",
        f"- Pruned by Stage A gate: {len(gate_pruned_candidates)}",
        f"- Passing Stage A gate: {len(passing_candidates)}",
        f"- Kept for Stage B: {len(survivors)}",
        "",
        "## Ranked Survivors",
        "",
        "| Rank | Candidate | Data | Base Score | Cost-Aware Score |",
        "| ---: | --- | --- | ---: | ---: |",
    ]

    for rank, candidate in enumerate(survivors, start=1):
        score = scores[candidate.id]
        lines.append(
            "| "
            f"{rank} | {candidate.id} | {candidate.data_requirement.value} | "
            f"{score.base_score():.3f} | {cost_aware_score(candidate, score):.3f} |"
        )

    lines.extend(["", "## Deferred Candidates", ""])
    if deferred_candidates:
        lines.extend(
            f"- {candidate.id}: {candidate.data_requirement.value} -> {candidate.status.value}"
            for candidate in deferred_candidates
        )
    else:
        lines.append("- none")

    lines.extend(["", "## Duplicate Pruning", ""])
    if duplicate_decisions:
        lines.extend(
            f"- {decision.candidate_id} duplicates {decision.duplicate_of} "
            f"(distance {decision.distance:.3f})"
            for decision in duplicate_decisions
        )
    else:
        lines.append("- none")

    return "\n".join(lines) + "\n"
