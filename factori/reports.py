"""Markdown report rendering for deterministic fake stages."""

from __future__ import annotations

from factori.budget import stage_c_cost_aware_score
from factori.dedup import DuplicateDecision
from factori.final_selection import StageCResultItem
from factori.schemas import (
    AbstractionAttackReport,
    AbstractionReport,
    ArtifactManifest,
    BaselineReport,
    BlockedClaim,
    BranchOutcomeSummary,
    BridgeReport,
    Candidate,
    ClaimTable,
    DraftSkeleton,
    FinalNucleus,
    LedgerSummary,
    ManuscriptChecklist,
    ManuscriptPlan,
    RedTeamReport,
    ReproducibilityManifest,
    ResearchObject,
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


def render_manuscript_plan_report(
    *,
    run_id: str,
    final_nucleus: FinalNucleus,
    claim_table: ClaimTable,
    blocked_claims: list[BlockedClaim],
    manuscript_plan: ManuscriptPlan,
) -> str:
    """Render the deterministic manuscript planning report."""
    allowed_claims = [
        claim for claim in claim_table.claims if claim.claim_id in manuscript_plan.allowed_claim_ids
    ]
    lines = [
        "# Manuscript Plan",
        "",
        "Deterministic MVP planning only; this is not a full manuscript or LaTeX paper.",
        "",
        f"Run: `{run_id}`",
        "",
        "## Summary",
        "",
        f"- Final nucleus type: {final_nucleus.nucleus_type.value}",
        f"- Final nucleus id: {final_nucleus.id}",
        f"- Claims total: {len(claim_table.claims)}",
        f"- Claims allowed: {len(allowed_claims)}",
        f"- Claims blocked: {len(blocked_claims)}",
        "",
        "## Section Plan",
        "",
        "| Section | Allowed Claims |",
        "| --- | --- |",
    ]
    for section in manuscript_plan.sections:
        allowed = ", ".join(section.allowed_claim_ids) if section.allowed_claim_ids else "none"
        lines.append(f"| {section.title} | {allowed} |")

    lines.extend(
        [
            "",
            "## Claim Evidence Table",
            "",
            "| Claim | Label | Candidate | Section | Evidence Types | Main Text |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for claim in claim_table.claims:
        evidence = ", ".join(claim.evidence_types) if claim.evidence_types else "none"
        lines.append(
            f"| {claim.claim_id} | {claim.claim_label.value} | {claim.candidate_id} | "
            f"{claim.allowed_section} | {evidence} | "
            f"{'yes' if claim.allowed_in_main_text else 'no'} |"
        )

    lines.extend(["", "## Blocked Or Downgraded Claims", ""])
    if blocked_claims:
        lines.extend(
            f"- {claim.claim_id}: {claim.blocked_reason}" for claim in blocked_claims
        )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Planning Invariants",
            "",
            "- Synthetic evidence remains synthetic-only.",
            "- Conjectures are not promoted to theorems.",
            "- Negative results remain negative or boundary findings.",
            "- Unsupported claims are excluded from the manuscript body.",
            "- Presentation artifacts are not verification evidence.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_draft_skeleton_markdown(
    *,
    run_id: str,
    draft_skeleton: DraftSkeleton,
    claim_table: ClaimTable,
    blocked_claims: list[BlockedClaim],
) -> str:
    """Render a deterministic draft skeleton Markdown scaffold."""
    del claim_table
    lines = [
        "# Title Stub",
        "",
        draft_skeleton.title,
        "",
        "## Abstract Stub",
        "",
        draft_skeleton.abstract_stub,
        "",
    ]
    for section in draft_skeleton.section_stubs:
        if section.section_title == "Title":
            continue
        lines.extend(
            [
                f"## {section.section_title}",
                "",
                f"Purpose: {section.section_purpose}",
                "",
                "Allowed claims: "
                + (", ".join(section.allowed_claim_ids) if section.allowed_claim_ids else "none"),
                "",
            ]
        )
        for placeholder in section.paragraph_placeholders:
            lines.extend([placeholder, ""])
        if section.warnings:
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in section.warnings)
            lines.append("")

    lines.extend(
        [
            "## Claim/Evidence Checklist",
            "",
        ]
    )
    if draft_skeleton.checklist is None:
        lines.append("- checklist not generated")
    else:
        for item in draft_skeleton.checklist.items:
            status = "PASS" if item.passed else "FAIL"
            lines.append(f"- [{status}] {item.category.value}: {item.description}")
    lines.extend(["", "## Blocked Claims", ""])
    if blocked_claims:
        lines.extend(
            f"- {blocked.claim_id}: {blocked.blocked_reason}" for blocked in blocked_claims
        )
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Draft Invariants",
            "",
            f"- Run: `{run_id}`",
            "- This is a scaffold, not polished prose.",
            "- This is not a final LaTeX paper.",
            "- Claim labels are copied from the claim table.",
            "- Draft artifacts are not verification evidence.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_manuscript_checklist_markdown(
    *,
    run_id: str,
    checklist: ManuscriptChecklist,
) -> str:
    """Render the deterministic manuscript checklist."""
    lines = [
        "# Manuscript Checklist",
        "",
        f"Run: `{run_id}`",
        "",
        f"Failures: {checklist.failures_count}",
        "",
        "| Category | Status | Item | Reason |",
        "| --- | --- | --- | --- |",
    ]
    for item in checklist.items:
        status = "PASS" if item.passed else "FAIL"
        lines.append(
            f"| {item.category.value} | {status} | {item.description} | {item.reason} |"
        )
    return "\n".join(lines) + "\n"


def render_research_object_markdown(
    *,
    research_object: ResearchObject,
    artifact_manifest: ArtifactManifest,
    ledger_summary: LedgerSummary,
    branch_outcomes: list[BranchOutcomeSummary],
    reproducibility_manifest: ReproducibilityManifest,
) -> str:
    """Render the deterministic research object audit report."""
    evidence_artifacts = [
        artifact for artifact in artifact_manifest.artifacts if artifact.is_evidence
    ]
    presentation_artifacts = [
        artifact for artifact in artifact_manifest.artifacts if artifact.is_presentation
    ]
    verification_outcomes = [
        outcome for outcome in branch_outcomes if outcome.verification_label is not None
    ]
    blocked_outcomes = [
        outcome for outcome in branch_outcomes if outcome.outcome == "BlockedClaim"
    ]
    failed_or_deferred = [
        outcome
        for outcome in branch_outcomes
        if outcome.outcome
        in {
            "PrunedDuplicate",
            "RejectedRedTeam",
            "PrunedUncertain",
            "InsufficientRetrievalAdequacy",
            "DeferredRealDataCandidate",
            "RequiresRealData",
            "StagnationStop",
            "BudgetDeferred",
        }
    ]
    lines = [
        "# fActorI Research Object",
        "",
        "Deterministic MVP package only; the immutable ledger remains the source of truth.",
        "",
        f"Run: `{research_object.run_id}`",
        "",
        "## Final Nucleus",
        "",
        f"- Type: {research_object.final_nucleus.nucleus_type.value}",
        f"- ID: {research_object.final_nucleus.id}",
        "- Supporting candidates: "
        + (
            ", ".join(research_object.final_nucleus.supporting_candidate_ids)
            if research_object.final_nucleus.supporting_candidate_ids
            else "none"
        ),
        f"- Reason: {research_object.final_nucleus.reason}",
        "",
        "## Manuscript Plan",
        "",
        f"- Plan artifact: `{research_object.manuscript_plan_ref.path}`",
        f"- Draft skeleton: `{research_object.draft_skeleton_ref.path}`",
        f"- Claim table: `{research_object.claim_table_ref.path}`",
        f"- Checklist: `{research_object.checklist_ref.path}`",
        "",
        "## Claim/Evidence Summary",
        "",
        f"- Evidence artifacts: {artifact_manifest.evidence_artifact_count}",
        f"- Presentation artifacts: {artifact_manifest.presentation_artifact_count}",
        f"- Blocked claims: {len(blocked_outcomes)}",
        "",
        "## Verification Labels",
        "",
    ]
    if verification_outcomes:
        for outcome in verification_outcomes:
            lines.append(
                f"- {outcome.candidate_id}: {outcome.verification_label.value}"
            )
    else:
        lines.append("- none")

    lines.extend(["", "## Blocked Claims", ""])
    if blocked_outcomes:
        lines.extend(
            f"- {outcome.candidate_id}: {outcome.reason}" for outcome in blocked_outcomes
        )
    else:
        lines.append("- none")

    lines.extend(["", "## Failed, Deferred, and Pruned Branches", ""])
    if failed_or_deferred:
        lines.extend(
            f"- {outcome.candidate_id}: {outcome.outcome} ({outcome.reason})"
            for outcome in failed_or_deferred
        )
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Evidence Artifacts",
            "",
            "| Artifact | Type | Path | Producing Commit |",
            "| --- | --- | --- | --- |",
        ]
    )
    if evidence_artifacts:
        for artifact in evidence_artifacts:
            lines.append(
                f"| {artifact.artifact_id} | {artifact.artifact_type.value} | "
                f"`{artifact.path}` | {artifact.producing_commit_hash or 'missing'} |"
            )
    else:
        lines.append("| none |  |  |  |")

    lines.extend(
        [
            "",
            "## Presentation Artifacts",
            "",
            "| Artifact | Type | Path |",
            "| --- | --- | --- |",
        ]
    )
    if presentation_artifacts:
        for artifact in presentation_artifacts:
            lines.append(
                f"| {artifact.artifact_id} | {artifact.artifact_type.value} | "
                f"`{artifact.path}` |"
            )
    else:
        lines.append("| none |  |  |")

    lines.extend(
        [
            "",
            "## Ledger Summary",
            "",
            f"- Commits: {ledger_summary.commit_count}",
            f"- Root commit: {ledger_summary.root_commit_hash or 'missing'}",
            f"- Latest commit: {ledger_summary.latest_commit_hash or 'missing'}",
            f"- Candidates: {ledger_summary.candidate_count}",
            f"- Artifacts: {ledger_summary.artifact_count}",
            "",
            "## Reproducibility Status",
            "",
            f"- Reproducible: {str(reproducibility_manifest.reproducible).lower()}",
            "- Blocking issues: "
            + (
                ", ".join(reproducibility_manifest.blocking_issues)
                if reproducibility_manifest.blocking_issues
                else "none"
            ),
            "",
            "## Warnings",
            "",
        ]
    )
    if reproducibility_manifest.warnings:
        lines.extend(f"- {warning}" for warning in reproducibility_manifest.warnings)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"
