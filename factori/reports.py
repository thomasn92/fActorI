"""Markdown report rendering for deterministic fake stages."""

from __future__ import annotations

from factori.dedup import DuplicateDecision
from factori.schemas import BaselineReport, BridgeReport, Candidate, RedTeamReport, ScoreVector
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


def render_stage_b_report(
    *,
    run_id: str,
    stage_a_survivors: list[Candidate],
    children: list[Candidate],
    bridge_reports: dict[str, BridgeReport],
    baseline_reports: dict[str, BaselineReport],
    redteam_reports: dict[str, RedTeamReport],
    rejected_review: list[Candidate],
    gate_pruned: list[Candidate],
    survivors: list[Candidate],
    scores: dict[str, ScoreVector],
) -> str:
    """Render the ranked deterministic Stage B report."""
    rejected_bridge = [
        candidate
        for candidate in children
        if candidate.id in bridge_reports and not bridge_reports[candidate.id].survives
    ]
    rejected_baseline = [
        candidate
        for candidate in children
        if candidate.id in baseline_reports and not baseline_reports[candidate.id].baseline_valid
    ]
    insufficient_retrieval = [
        candidate
        for candidate in children
        if candidate.id in redteam_reports and not redteam_reports[candidate.id].stage_c_ready
    ]
    lines = [
        "# Stage B Report",
        "",
        f"Run: `{run_id}`",
        "",
        "## Summary",
        "",
        f"- Stage A survivors: {len(stage_a_survivors)}",
        f"- Stage B children: {len(children)}",
        f"- Rejected by bridge: {len(rejected_bridge)}",
        f"- Rejected by review: {len(rejected_review)}",
        f"- Rejected by baseline: {len(rejected_baseline)}",
        f"- Insufficient retrieval: {len(insufficient_retrieval)}",
        f"- Pruned by Stage B gate: {len(gate_pruned)}",
        f"- Passing Stage B: {len(survivors)}",
        "",
        "## Ranked Stage B Survivors",
        "",
        "| Rank | Candidate | Parent | Variant | Score |",
        "| ---: | --- | --- | --- | ---: |",
    ]
    for rank, candidate in enumerate(survivors, start=1):
        score = scores[candidate.id]
        lines.append(
            f"| {rank} | {candidate.id} | {candidate.parent_candidate_id or ''} | "
            f"{candidate.variant_type or ''} | {cost_aware_score(candidate, score):.3f} |"
        )

    lines.extend(["", "## Gate Pruned", ""])
    if gate_pruned:
        lines.extend(f"- {candidate.id}: {candidate.status.value}" for candidate in gate_pruned)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"
