"""Deterministic prose-generation contracts for future exporters."""

from __future__ import annotations

from factori.schemas import (
    ClaimTable,
    FinalAuditReport,
    PaperSkeleton,
    ProseGenerationContract,
    ReleaseGateDecision,
    VerificationLabel,
)

STYLE_CONSTRAINTS = [
    "Preserve claim IDs, labels, and evidence links exactly.",
    "Use scaffold prose only; do not create new scientific claims.",
    "Keep unsupported directions outside normal body sections.",
    "Describe fake deterministic validators as fake MVP validators.",
]

FORBIDDEN_TRANSFORMATIONS = [
    "upgrade Conjecture to theorem",
    "upgrade SyntheticExperimentVerified to real-world validation",
    "upgrade NegativeResult to positive evidence",
    "omit evidence links for main claims",
    "omit blocked-claim appendix",
    "omit limitations",
    "use Markdown or LaTeX as verification evidence",
    "create new scientific claims",
]

BASE_REQUIRED_DISCLAIMERS = [
    "This MVP run used fake deterministic validators, not real Lean or real experiments.",
    "External review readiness is false until real adapters are implemented.",
]


def build_prose_generation_contract(
    run_id: str,
    paper_skeleton: PaperSkeleton,
    claim_table: ClaimTable,
    final_audit_report: FinalAuditReport,
    release_gate_decision: ReleaseGateDecision,
) -> ProseGenerationContract:
    """Build a label-preserving contract for future prose generation."""
    claim_by_id = {claim.claim_id: claim for claim in claim_table.claims}
    allowed_claims = sorted(
        {
            placeholder.claim_id
            for placeholder in paper_skeleton.claim_placeholders
            if placeholder.claim_label != VerificationLabel.UNSUPPORTED
        }
    )
    blocked_claims = sorted(_blocked_claims_from_appendix(paper_skeleton))
    claim_labels = {
        claim_id: claim_by_id[claim_id].claim_label
        for claim_id in sorted(claim_by_id)
    }
    claim_evidence_links = {
        claim_id: list(claim_by_id[claim_id].evidence_artifact_ids)
        for claim_id in sorted(claim_by_id)
    }
    return ProseGenerationContract(
        run_id=run_id,
        allowed_sections=[section.title for section in paper_skeleton.sections],
        allowed_claims=allowed_claims,
        blocked_claims=blocked_claims,
        claim_labels=claim_labels,
        claim_evidence_links=claim_evidence_links,
        style_constraints=STYLE_CONSTRAINTS,
        forbidden_transformations=FORBIDDEN_TRANSFORMATIONS,
        required_disclaimers=_required_disclaimers(claim_table),
        ready_for_polished_prose=(
            release_gate_decision.ready_for_polished_prose
            and final_audit_report.blocking_failures_count == 0
        ),
    )


def _required_disclaimers(claim_table: ClaimTable) -> list[str]:
    labels = {claim.claim_label for claim in claim_table.claims}
    disclaimers = list(BASE_REQUIRED_DISCLAIMERS)
    if VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED in labels:
        disclaimers.append(
            "Synthetic evidence supports only controlled synthetic assumptions."
        )
    if VerificationLabel.CONJECTURE in labels:
        disclaimers.append("Conjectural statements are not verified theorems.")
    if VerificationLabel.NEGATIVE_RESULT in labels:
        disclaimers.append("Negative results are boundary or failure findings.")
    return disclaimers


def _blocked_claims_from_appendix(paper_skeleton: PaperSkeleton) -> set[str]:
    blocked: set[str] = set()
    for appendix in paper_skeleton.appendices:
        if "Blocked" not in appendix.title:
            continue
        for line in appendix.content_lines:
            claim_id = line.split(":", maxsplit=1)[0].strip()
            if claim_id and claim_id != "none":
                blocked.add(claim_id)
    return blocked
