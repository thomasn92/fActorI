"""Markdown report rendering for deterministic fake stages."""

from __future__ import annotations

from factori.budget import stage_c_cost_aware_score
from factori.dedup import DuplicateDecision
from factori.final_selection import StageCResultItem
from factori.schemas import (
    AbstractionAttackReport,
    AbstractionReport,
    BaselineReport,
    BridgeReport,
    Candidate,
    FinalNucleus,
    RedTeamReport,
    ScoreVector,
    StageCRedTeamSelectionReport,
    StageCVerificationRecord,
    UncertaintyEstimate,
    VerificationLabel,
)
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


def render_stage_c_selection_report(
    *,
    run_id: str,
    stage_b_survivors: list[Candidate],
    selected_candidates: list[Candidate],
    rejected_redteam: list[Candidate],
    pruned_uncertain: list[Candidate],
    insufficient_retrieval: list[Candidate],
    deferred_data: list[Candidate],
    budget_deferred: list[Candidate],
    redteam_reports: dict[str, StageCRedTeamSelectionReport],
    uncertainty_estimates: dict[str, UncertaintyEstimate],
    scores: dict[str, ScoreVector],
) -> str:
    """Render the deterministic pre-Stage-C selection report."""
    lines = [
        "# Stage C Selection Report",
        "",
        f"Run: `{run_id}`",
        "",
        "## Summary",
        "",
        f"- Stage B survivors: {len(stage_b_survivors)}",
        f"- Rejected by red-team threshold: {len(rejected_redteam)}",
        f"- Pruned as uncertain: {len(pruned_uncertain)}",
        f"- Insufficient retrieval adequacy: {len(insufficient_retrieval)}",
        f"- Deferred by data gate: {len(deferred_data)}",
        f"- Deferred by budget: {len(budget_deferred)}",
        f"- Stage C ready: {len(selected_candidates)}",
        "",
        "## Selected For Stage C",
        "",
        "| Rank | Candidate | Data | RT | S Lower | Cost-Aware Score |",
        "| ---: | --- | --- | ---: | ---: | ---: |",
    ]
    if selected_candidates:
        for rank, candidate in enumerate(selected_candidates, start=1):
            redteam = redteam_reports[candidate.id]
            uncertainty = uncertainty_estimates[candidate.id]
            score = scores[candidate.id]
            lines.append(
                "| "
                f"{rank} | {candidate.id} | {candidate.data_requirement.value} | "
                f"{redteam.rt_total:.3f} | {uncertainty.s_lower:.3f} | "
                f"{stage_c_cost_aware_score(candidate, score):.3f} |"
            )
    else:
        lines.append("|  | none |  |  |  |  |")

    lines.extend(["", "## Rejections And Deferrals", ""])
    for heading, candidates in [
        ("RejectedRedTeam", rejected_redteam),
        ("PrunedUncertain", pruned_uncertain),
        ("InsufficientRetrievalAdequacy", insufficient_retrieval),
        ("DeferredData", deferred_data),
        ("BudgetDeferred", budget_deferred),
    ]:
        lines.append(f"### {heading}")
        if candidates:
            lines.extend(f"- {candidate.id}: {candidate.status.value}" for candidate in candidates)
        else:
            lines.append("- none")
        lines.append("")

    lines.extend(
        [
            "## Readiness Thresholds",
            "",
            "- Red-team aggregate: RT(c) >= 0.75",
            "- Retrieval adequacy: rho_adequacy >= tau_adequacy",
            "- Conservative lower bound: S_lower >= tau_S",
            "- MVP data gate: NoData or SyntheticOnly",
        ]
    )
    return "\n".join(lines) + "\n"


def render_stage_c_verification_report(
    *,
    run_id: str,
    stage_c_ready: list[Candidate],
    verification_records: dict[str, StageCVerificationRecord],
    proof_results: dict[str, object],
    experiment_results: dict[str, object],
) -> str:
    """Render the deterministic fake Stage C verification report."""
    labels = [record.label for record in verification_records.values()]
    lines = [
        "# Stage C Verification Report",
        "",
        "Deterministic MVP validation only; this is not real Lean or real experiment evidence.",
        "",
        f"Run: `{run_id}`",
        "",
        "## Summary",
        "",
        f"- Stage C ready candidates: {len(stage_c_ready)}",
        f"- Fake proof runs: {len(proof_results)}",
        f"- Fake synthetic experiments: {len(experiment_results)}",
        f"- LeanVerified: {labels.count(VerificationLabel.LEAN_VERIFIED)}",
        f"- SyntheticExperimentVerified: "
        f"{labels.count(VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED)}",
        f"- NegativeResult: {labels.count(VerificationLabel.NEGATIVE_RESULT)}",
        f"- Conjecture: {labels.count(VerificationLabel.CONJECTURE)}",
        f"- Limitation: {labels.count(VerificationLabel.LIMITATION)}",
        f"- Unsupported: {labels.count(VerificationLabel.UNSUPPORTED)}",
        "",
        "## Verification Decisions",
        "",
        "| Candidate | Branch Type | Label | Status | Evidence Artifacts |",
        "| --- | --- | --- | --- | ---: |",
    ]
    for candidate in stage_c_ready:
        record = verification_records[candidate.id]
        lines.append(
            f"| {candidate.id} | {record.branch_type.value} | {record.label.value} | "
            f"{record.status.value} | {len(record.evidence_artifacts)} |"
        )

    lines.extend(
        [
            "",
            "## Evidence Boundary",
            "",
            "- LeanVerified requires a linked fake proof artifact under `lean/`.",
            "- SyntheticExperimentVerified requires a linked fake synthetic experiment artifact.",
            "- RealDataExperimentVerified is never produced in the MVP.",
            "- LaTeX and Markdown presentation artifacts are not verification evidence.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_abstract_synthesis_report(
    *,
    run_id: str,
    stage_c_results: list[StageCResultItem],
    abstraction_reports: list[AbstractionReport],
    attack_reports: list[AbstractionAttackReport],
    passing_abstractions: list[AbstractionReport],
    final_nucleus: FinalNucleus,
) -> str:
    """Render the deterministic abstract synthesis report."""
    attacks_by_id = {
        attack.abstract_model_id: attack for attack in attack_reports
    }
    lines = [
        "# Abstract Synthesis Report",
        "",
        "Deterministic MVP synthesis only; this is not a manuscript.",
        "",
        f"Run: `{run_id}`",
        "",
        "## Summary",
        "",
        f"- Stage C results: {len(stage_c_results)}",
        f"- Abstract models proposed: {len(abstraction_reports)}",
        f"- Abstract models passed: {len(passing_abstractions)}",
        f"- Final nucleus type: {final_nucleus.nucleus_type.value}",
        f"- Final nucleus id: {final_nucleus.id}",
        "",
        "## Stage C Inputs",
        "",
        "| Candidate | Label | Evidence Artifacts |",
        "| --- | --- | ---: |",
    ]
    for item in stage_c_results:
        record = item.verification_record
        lines.append(
            f"| {item.candidate.id} | {record.label.value} | "
            f"{len(record.evidence_artifacts)} |"
        )

    lines.extend(
        [
            "",
            "## Proposed Abstractions",
            "",
            "| Model | A(G,B) | Coverage | Coherence | Compression | Generativity | "
            "Verifiability | RT | Passed |",
            "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
        ]
    )
    if abstraction_reports:
        for report in abstraction_reports:
            attack = attacks_by_id.get(report.abstract_model_id)
            rt_score = attack.rt_abstract if attack else 0.0
            passed = (
                report.accepted_by_score
                and attack is not None
                and attack.attack_passed
            )
            lines.append(
                f"| {report.abstract_model_id} | {report.total_score:.3f} | "
                f"{report.coverage:.3f} | {report.coherence:.3f} | "
                f"{report.compression:.3f} | {report.generativity:.3f} | "
                f"{report.verifiability:.3f} | {rt_score:.3f} | "
                f"{'yes' if passed else 'no'} |"
            )
    else:
        lines.append("| none |  |  |  |  |  |  |  |  |")

    lines.extend(
        [
            "",
            "## Final Nucleus",
            "",
            f"- Type: {final_nucleus.nucleus_type.value}",
            f"- ID: {final_nucleus.id}",
            f"- Supporting candidates: {', '.join(final_nucleus.supporting_candidate_ids)}",
            f"- Reason: {final_nucleus.reason}",
            "",
            "## Label Preservation",
            "",
            "- Abstract models are labeled AbstractSynthesis only.",
            "- Branch verification labels remain attached to their original branch claims.",
            "- Synthetic evidence remains synthetic-only evidence.",
            "- Negative results are boundary cases, not positive evidence.",
        ]
    )
    return "\n".join(lines) + "\n"
