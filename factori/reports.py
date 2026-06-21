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
    CrossRunComparisonReport,
    DiagnosticReport,
    DraftSkeleton,
    ExportReadinessReport,
    FinalAuditReport,
    FinalNucleus,
    HygieneRemediationPlan,
    LedgerSummary,
    ManuscriptChecklist,
    ManuscriptPlan,
    OutputHygieneReport,
    PaperSkeleton,
    PipelineRunReport,
    RedTeamReport,
    ReleaseGateDecision,
    ReplayVerificationReport,
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
    adapter_metadata: dict[str, object] | None = None,
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
        f"- Candidate adapter: {(adapter_metadata or {}).get('backend', 'fake')}",
        f"- Candidate adapter model: {(adapter_metadata or {}).get('model', 'not_applicable')}",
        "- Candidate proposals are not verification evidence.",
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
    retrieval_adapter_metadata: dict[str, object] | None = None,
    reviewer_adapter_metadata: dict[str, object] | None = None,
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
        f"- Retrieval adapter: {(retrieval_adapter_metadata or {}).get('backend', 'fake')}",
        f"- Reviewer adapter: {(reviewer_adapter_metadata or {}).get('backend', 'fake')}",
        f"- Reviewer model: {(reviewer_adapter_metadata or {}).get('model') or 'none'}",
        "- Reviewer output is structural critique only and has no verification authority.",
        "- Retrieval adequacy is bounded context, not proof of novelty or literature coverage.",
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


def render_paper_skeleton_markdown(*, paper_skeleton: PaperSkeleton) -> str:
    """Render the deterministic assembled paper skeleton."""
    lines = [
        f"# {paper_skeleton.title}",
        "",
        "Deterministic paper skeleton only; this is not polished prose or verification evidence.",
        "",
    ]
    for section in paper_skeleton.sections:
        lines.extend([f"## {section.title}", "", f"Purpose: {section.purpose}", ""])
        if section.title == "Abstract":
            lines.extend([paper_skeleton.abstract_scaffold, ""])
        if section.claim_placeholders:
            lines.append("Claim placeholders:")
            for placeholder in section.claim_placeholders:
                evidence = (
                    ", ".join(placeholder.evidence_artifact_ids)
                    if placeholder.evidence_artifact_ids
                    else "none"
                )
                lines.append(
                    f"- claim_id={placeholder.claim_id}; "
                    f"label={placeholder.claim_label.value}; "
                    f"candidate_id={placeholder.candidate_id}; "
                    f"evidence_artifact_ids={evidence}; "
                    f"allowed_section={placeholder.allowed_section}"
                )
            lines.append("")
        else:
            lines.extend(["Claim placeholders: none", ""])
        if section.warnings:
            lines.append("Warnings:")
            lines.extend(f"- {warning}" for warning in section.warnings)
            lines.append("")

    for appendix in paper_skeleton.appendices:
        lines.extend([f"# {appendix.title}", ""])
        lines.extend(f"- {line}" for line in appendix.content_lines)
        lines.append("")

    lines.extend(
        [
            "# Assembly Invariants",
            "",
            "- No new scientific claims are introduced.",
            "- Claim labels are copied from the claim table.",
            "- Synthetic evidence remains synthetic-only.",
            "- Conjectures remain conjectures.",
            "- Negative results remain negative or boundary-labeled.",
            "- LaTeX and Markdown artifacts are presentation artifacts, not verification evidence.",
            "- The ledger remains the source of truth for provenance.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_final_audit_report_markdown(*, audit_report: FinalAuditReport) -> str:
    """Render a deterministic final audit report."""
    lines = [
        "# Final Audit Report",
        "",
        "Deterministic internal-consistency audit only; this does not certify scientific validity.",
        "",
        f"Run: `{audit_report.run_id}`",
        "",
        "## Summary",
        "",
        f"- Audit checks: {len(audit_report.checks)}",
        f"- Passes: {audit_report.passes_count}",
        f"- Warnings: {audit_report.warnings_count}",
        f"- Failures: {audit_report.failures_count}",
        f"- Blocking failures: {audit_report.blocking_failures_count}",
        "",
        "## Checks",
        "",
        "| Check | Category | Status | Severity | Message |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in audit_report.checks:
        lines.append(
            f"| {check.check_id} | {check.category.value} | {check.status.value} | "
            f"{check.severity.value} | {check.message} |"
        )
    lines.extend(
        [
            "",
            "## Boundary",
            "",
            "- Paper skeletons, Markdown, LaTeX, and research-object reports are "
            "presentation artifacts.",
            "- The audit validates deterministic consistency only.",
            "- The immutable ledger remains the source of truth.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_release_gate_decision_markdown(*, decision: ReleaseGateDecision) -> str:
    """Render a deterministic release gate decision."""
    lines = [
        "# Release Gate Decision",
        "",
        "Deterministic release gate only; this is not a scientific-validity certificate.",
        "",
        f"Run: `{decision.run_id}`",
        "",
        f"- Status: {decision.status.value}",
        f"- Ready for polished prose: {str(decision.ready_for_polished_prose).lower()}",
        f"- Ready for LaTeX export: {str(decision.ready_for_latex_export).lower()}",
        f"- Ready for external review: {str(decision.ready_for_external_review).lower()}",
        f"- Audit checks: {decision.audit_checks}",
        "",
        "## Blocking Reasons",
        "",
    ]
    if decision.blocking_reasons:
        lines.extend(f"- {reason}" for reason in decision.blocking_reasons)
    else:
        lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    if decision.warnings:
        lines.extend(f"- {warning}" for warning in decision.warnings)
    else:
        lines.append("- none")
    return "\n".join(lines) + "\n"


def render_export_readiness_report_markdown(
    *,
    readiness_report: ExportReadinessReport,
) -> str:
    """Render deterministic export-readiness report."""
    lines = [
        "# Export Readiness Report",
        "",
        "Deterministic export preparation only; no polished prose or LaTeX is generated.",
        "",
        f"Run: `{readiness_report.run_id}`",
        "",
        f"- Ready for polished prose: {str(readiness_report.ready_for_polished_prose).lower()}",
        f"- Ready for LaTeX export: {str(readiness_report.ready_for_latex_export).lower()}",
        f"- Ready for external review: {str(readiness_report.ready_for_external_review).lower()}",
        f"- Export blocked: {str(readiness_report.export_blocked).lower()}",
        f"- Export allowed claims: {readiness_report.export_allowed_claims}",
        f"- Export blocked claims: {readiness_report.export_blocked_claims}",
        "",
        "## Blocking Reasons",
        "",
    ]
    if readiness_report.blocking_reasons:
        lines.extend(f"- {reason}" for reason in readiness_report.blocking_reasons)
    else:
        lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    if readiness_report.warnings:
        lines.extend(f"- {warning}" for warning in readiness_report.warnings)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Export Boundary",
            "",
            "- Export plans are not verification evidence.",
            "- Markdown and LaTeX-related files remain presentation artifacts.",
            "- Future exporters must preserve claim labels and evidence links.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_replay_verification_report_markdown(
    *,
    replay_report: ReplayVerificationReport,
) -> str:
    """Render a read-only deterministic replay report."""
    lines = [
        "# Replay Verification Report",
        "",
        "Read-only deterministic replay only; this is not provenance or scientific validation.",
        "",
        f"Run: `{replay_report.run_id}`",
        "",
        "## Summary",
        "",
        f"- Replay status: {replay_report.replay_status.value}",
        f"- Ledger commits checked: {replay_report.ledger_commits_checked}",
        f"- Artifacts checked: {replay_report.artifacts_checked}",
        f"- Hashes verified: {replay_report.hashes_verified}",
        f"- Evidence artifacts checked: {replay_report.evidence_artifacts_checked}",
        f"- Presentation artifacts checked: {replay_report.presentation_artifacts_checked}",
        f"- Stage outputs checked: {replay_report.stage_outputs_checked}",
        f"- Warnings: {replay_report.warnings_count}",
        f"- Blocking failures: {replay_report.blocking_failures_count}",
        f"- Ledger mutated: {str(replay_report.ledger_mutated).lower()}",
        f"- Artifact manifest mutated: "
        f"{str(replay_report.artifact_manifest_mutated).lower()}",
        "",
        "## Checks",
        "",
        "| Check | Category | Status | Severity | Message |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in replay_report.checks:
        lines.append(
            f"| {check.check_id} | {check.category.value} | {check.status.value} | "
            f"{check.severity.value} | {check.message} |"
        )
    lines.extend(
        [
            "",
            "## Replay Boundary",
            "",
            "- Replay reports are not provenance.",
            "- Replay reports are not verification evidence.",
            "- Replay reports are not ledgered.",
            "- The immutable ledger remains the source of truth.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_diagnostic_report_markdown(
    *,
    diagnostic_report: DiagnosticReport,
) -> str:
    """Render a read-only deterministic provenance diagnostic report."""
    lines = [
        "# Provenance Diagnostic Report",
        "",
        "Read-only deterministic diagnosis only; this is not provenance or scientific validation.",
        "",
        f"Run: `{diagnostic_report.run_id}`",
        "",
        "## Summary",
        "",
        f"- Diagnostic status: {diagnostic_report.diagnostic_status.value}",
        f"- Root causes: {len(diagnostic_report.root_causes)}",
        f"- Blocking causes: {diagnostic_report.blocking_causes_count}",
        f"- Warning causes: {diagnostic_report.warning_causes_count}",
        f"- Recommended steps: {len(diagnostic_report.recommended_steps)}",
        f"- Ledger mutated: {str(diagnostic_report.ledger_mutated).lower()}",
        "- Artifact manifest mutated: "
        f"{str(diagnostic_report.artifact_manifest_mutated).lower()}",
        "",
        "## Root Causes",
        "",
        "| Category | Severity | Summary | Source |",
        "| --- | --- | --- | --- |",
    ]
    if diagnostic_report.root_causes:
        for cause in diagnostic_report.root_causes:
            lines.append(
                f"| {cause.category.value} | {cause.severity.value} | "
                f"{cause.summary} | {cause.source} |"
            )
    else:
        lines.append("| none |  |  |  |")
    lines.extend(
        [
            "",
            "## Recommended Rerun Steps",
            "",
        ]
    )
    if diagnostic_report.recommended_steps:
        for step in diagnostic_report.recommended_steps:
            command = step.command or "manual inspection required"
            lines.append(f"{step.order + 1}. `{command}` - {step.reason}")
    else:
        lines.append("- none")
    lines.extend(["", "## Warnings", ""])
    if diagnostic_report.warnings:
        lines.extend(f"- {warning}" for warning in diagnostic_report.warnings)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Diagnostic Boundary",
            "",
            "- Diagnostics does not repair or rerun any stage.",
            "- Diagnostic reports are not provenance.",
            "- Diagnostic reports are not verification evidence.",
            "- Diagnostic reports are not ledgered.",
            "- The immutable ledger remains the source of truth.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_cross_run_comparison_markdown(
    *,
    comparison_report: CrossRunComparisonReport,
) -> str:
    """Render a read-only deterministic cross-run comparison report."""
    blocking = sum(
        finding.severity.value == "Blocking"
        for finding in comparison_report.regression_findings
    )
    warnings = sum(
        finding.severity.value == "Warning"
        for finding in comparison_report.regression_findings
    )
    lines = [
        "# Cross-Run Comparison Report",
        "",
        "Read-only deterministic comparison only; this is not provenance or scientific validation.",
        "",
        f"Baseline run: `{comparison_report.baseline_run_id}`",
        f"Candidate run: `{comparison_report.candidate_run_id}`",
        "",
        "## Summary",
        "",
        f"- Regression status: {comparison_report.regression_status.value}",
        f"- Differences: {len(comparison_report.differences)}",
        f"- Blocking regressions: {blocking}",
        f"- Warning regressions: {warnings}",
        f"- Ledger mutated: {str(comparison_report.ledger_mutated).lower()}",
        "- Artifact manifest mutated: "
        f"{str(comparison_report.artifact_manifest_mutated).lower()}",
        "",
        "## Differences",
        "",
        "| Field | Category | Severity | Baseline | Candidate |",
        "| --- | --- | --- | --- | --- |",
    ]
    if comparison_report.differences:
        for difference in comparison_report.differences:
            lines.append(
                f"| {difference.field} | {difference.category.value} | "
                f"{difference.severity.value} | {difference.baseline_value} | "
                f"{difference.candidate_value} |"
            )
    else:
        lines.append("| none |  |  |  |  |")
    lines.extend(["", "## Regression Findings", ""])
    if comparison_report.regression_findings:
        lines.extend(
            f"- [{finding.severity.value}] {finding.category.value}: {finding.summary}"
            for finding in comparison_report.regression_findings
        )
    else:
        lines.append("- none")
    lines.extend(["", "## Comparison Errors", ""])
    if comparison_report.comparison_errors:
        lines.extend(f"- {error}" for error in comparison_report.comparison_errors)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Comparison Boundary",
            "",
            "- Comparison does not repair or rerun either run.",
            "- Comparison reports are not provenance.",
            "- Comparison reports are not verification evidence.",
            "- Comparison reports are not ledgered.",
            "- Each immutable ledger remains the source of truth for its run.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_output_hygiene_report_markdown(
    *,
    hygiene_report: OutputHygieneReport,
) -> str:
    """Render a read-only deterministic output hygiene report."""
    lines = [
        "# Run Output Hygiene Report",
        "",
        "Read-only filesystem inspection only; this is not provenance or evidence.",
        "",
        f"Run: `{hygiene_report.run_id}`",
        "",
        "## Summary",
        "",
        f"- Hygiene status: {hygiene_report.hygiene_status.value}",
        f"- Files scanned: {hygiene_report.files_scanned}",
        f"- Manifest entries: {hygiene_report.manifest_entries}",
        f"- Orphaned files: {hygiene_report.orphaned_files}",
        f"- Missing manifest files: {hygiene_report.missing_manifest_files}",
        f"- Hash mismatches: {hygiene_report.hash_mismatches}",
        f"- Duplicate outputs: {hygiene_report.duplicate_outputs}",
        f"- Non-provenance files: {hygiene_report.non_provenance_files}",
        f"- Unexpected files: {hygiene_report.unexpected_files}",
        f"- Warnings: {hygiene_report.warnings_count}",
        f"- Blocking findings: {hygiene_report.blocking_findings_count}",
        f"- Ledger mutated: {str(hygiene_report.ledger_mutated).lower()}",
        "- Artifact manifest mutated: "
        f"{str(hygiene_report.artifact_manifest_mutated).lower()}",
        "",
        "## Findings",
        "",
        "| Category | Severity | Paths | Message |",
        "| --- | --- | --- | --- |",
    ]
    if hygiene_report.findings:
        for finding in hygiene_report.findings:
            paths = ", ".join(f"`{path}`" for path in finding.paths) or "none"
            lines.append(
                f"| {finding.category.value} | {finding.severity.value} | "
                f"{paths} | {finding.message} |"
            )
    else:
        lines.append("| none |  |  |  |")
    lines.extend(
        [
            "",
            "## Inspection Boundary",
            "",
            "- Hygiene inspection does not delete, repair, rewrite, or rehash stored metadata.",
            "- Hygiene reports are not provenance or verification evidence.",
            "- Hygiene reports are not ledgered or included in normal artifact manifests.",
            "- The append-only ledger remains the source of truth.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_hygiene_remediation_plan_markdown(
    *,
    remediation_plan: HygieneRemediationPlan,
) -> str:
    """Render a non-executing deterministic hygiene remediation plan."""
    lines = [
        "# Hygiene Remediation Plan",
        "",
        "Recommendations only; this plan does not execute cleanup, repair, deletion, or reruns.",
        "",
        f"Run: `{remediation_plan.run_id}`",
        "",
        "## Summary",
        "",
        f"- Plan status: {remediation_plan.plan_status.value}",
        f"- Source hygiene status: {remediation_plan.source_hygiene_status.value}",
        f"- Actions: {len(remediation_plan.actions)}",
        f"- Execution performed: {str(remediation_plan.execution_performed).lower()}",
        f"- Ledger mutated: {str(remediation_plan.ledger_mutated).lower()}",
        "- Artifact manifest mutated: "
        f"{str(remediation_plan.artifact_manifest_mutated).lower()}",
        "",
        "## Recommended Actions",
        "",
        "| Kind | Risk | Status | Stage | Paths | Reason |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    if remediation_plan.actions:
        for action in remediation_plan.actions:
            stage = action.recommended_stage or "none"
            paths = ", ".join(f"`{path}`" for path in action.paths) or "none"
            lines.append(
                f"| {action.kind.value} | {action.risk.value} | {action.status.value} | "
                f"{stage} | {paths} | {action.reason} |"
            )
            if action.recommended_command:
                lines.append(f"  Suggested command: `{action.recommended_command}`")
    else:
        lines.append("| NoActionNeeded | Low | NotRequired | none | none | Run is clean. |")
    lines.extend(["", "## Warnings", ""])
    if remediation_plan.warnings:
        lines.extend(f"- {warning}" for warning in remediation_plan.warnings)
    else:
        lines.append("- none")
    lines.extend(
        [
            "",
            "## Planning Boundary",
            "",
            "- No remediation action was executed.",
            "- This plan does not delete, quarantine, restore, rerun, or rewrite anything.",
            "- Remediation plans are not provenance or verification evidence.",
            "- Remediation plans are not ledgered or included in normal artifact manifests.",
            "- The append-only ledger remains the source of truth.",
        ]
    )
    return "\n".join(lines) + "\n"


def render_pipeline_run_report_markdown(
    *,
    pipeline_report: PipelineRunReport,
) -> str:
    """Render the ledgered deterministic one-command pipeline report."""
    lines = [
        "# Deterministic Pipeline Run Report",
        "",
        "This report describes orchestration only; it is not verification evidence.",
        "",
        f"Run: `{pipeline_report.run_id}`",
        f"Domain: `{pipeline_report.domain}`",
        f"Method: `{pipeline_report.method or 'automatic deterministic discovery'}`",
        "",
        "## Summary",
        "",
        f"- Pipeline status: {pipeline_report.pipeline_status.value}",
        f"- Failure policy: {pipeline_report.failure_policy.value}",
        f"- Stages run: {len(pipeline_report.stage_results)}",
        "- Blocking stage: "
        + (
            pipeline_report.blocking_stage.value
            if pipeline_report.blocking_stage is not None
            else "none"
        ),
        "- Release status: "
        + (
            pipeline_report.release_status.value
            if pipeline_report.release_status is not None
            else "not run"
        ),
        "- Replay status: "
        + (
            pipeline_report.replay_status.value
            if pipeline_report.replay_status is not None
            else "skipped"
        ),
        "- Diagnostic status: "
        + (
            pipeline_report.diagnostic_status.value
            if pipeline_report.diagnostic_status is not None
            else "skipped"
        ),
        "",
        "## Stage Results",
        "",
        "| Stage | Status | Artifacts | Error |",
        "| --- | --- | ---: | --- |",
    ]
    for result in pipeline_report.stage_results:
        lines.append(
            f"| {result.stage_name.value} | {result.status.value} | "
            f"{len(result.created_artifacts)} | {result.error_message or ''} |"
        )

    lines.extend(["", "## Final Outputs", ""])
    if pipeline_report.final_outputs:
        lines.extend(
            f"- {name}: `{path}`"
            for name, path in sorted(pipeline_report.final_outputs.items())
        )
    else:
        lines.append("- none")

    lines.extend(["", "## Warnings", ""])
    if pipeline_report.warnings:
        lines.extend(f"- {warning}" for warning in pipeline_report.warnings)
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "## Boundaries",
            "",
            "- Validators in this MVP are deterministic fakes, not scientific truth.",
            "- Markdown, LaTeX, paper skeletons, and export plans are not evidence.",
            "- Replay and diagnostics remain read-only and outside provenance.",
            "- The append-only ledger remains the source of truth.",
        ]
    )
    return "\n".join(lines) + "\n"
