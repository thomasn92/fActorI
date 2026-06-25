"""Deterministic narrative manuscript contract construction."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from factori.draft_skeleton import DraftSkeletonError, load_manuscript_planning_artifacts
from factori.hashing import sha256_json
from factori.manuscript_plan import ManuscriptPlanError, load_final_nucleus
from factori.schemas import (
    Claim,
    ClaimTable,
    FinalNucleus,
    ManuscriptPlan,
    NarrativeManuscriptContract,
    NarrativeSectionRole,
    VerificationLabel,
)


class NarrativeContractError(RuntimeError):
    """Raised when narrative-contract prerequisites are missing."""


@dataclass(frozen=True)
class NarrativeInputs:
    """Ledger-loaded inputs for deterministic narrative checks."""

    final_nucleus: FinalNucleus
    manuscript_plan: ManuscriptPlan
    claim_table: ClaimTable


def load_narrative_inputs(run_id: str, ledger) -> NarrativeInputs:
    """Load manuscript planning inputs from the ledger."""
    try:
        final_nucleus = load_final_nucleus(run_id, ledger)
        manuscript_plan, claim_table, _blocked_claims = load_manuscript_planning_artifacts(
            run_id,
            ledger,
        )
    except (DraftSkeletonError, ManuscriptPlanError) as exc:
        raise NarrativeContractError(
            "Manuscript planning artifacts not found; run factori plan-manuscript first"
        ) from exc
    return NarrativeInputs(
        final_nucleus=final_nucleus,
        manuscript_plan=manuscript_plan,
        claim_table=claim_table,
    )


def build_narrative_contract(
    manuscript_plan: ManuscriptPlan,
    final_nucleus: FinalNucleus,
    claim_table: ClaimTable,
    retrieval_context: dict[str, Any] | None = None,
    *,
    run_id: str = "unknown",
) -> NarrativeManuscriptContract:
    """Build a deterministic narrative contract from existing manuscript artifacts."""
    main_claim = _select_main_claim(claim_table)
    central_message = _central_message(final_nucleus, main_claim)
    section_plan = [_section_contract(section) for section in manuscript_plan.sections]
    blocked_or_missing = _blocked_or_missing_items(
        manuscript_plan=manuscript_plan,
        claim_table=claim_table,
        main_claim=main_claim,
    )
    has_synthetic = any(
        claim.claim_label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
        for claim in claim_table.claims
    )
    has_empirical_section = any(
        "empirical" in section.title.lower() for section in manuscript_plan.sections
    )
    identity = {
        "run_id": run_id,
        "final_nucleus_id": final_nucleus.id,
        "plan_id": manuscript_plan.plan_id,
        "claim_ids": sorted(claim.claim_id for claim in claim_table.claims),
    }
    return NarrativeManuscriptContract(
        contract_id=f"narrative-contract-{sha256_json(identity)[:12]}",
        run_id=run_id,
        final_nucleus_id=final_nucleus.id,
        central_message=central_message,
        problem_statement=_problem_statement(manuscript_plan, final_nucleus),
        why_interesting=_why_interesting(final_nucleus, main_claim),
        literature_gap=_literature_gap(retrieval_context),
        novelty_claim=_bounded_novelty_claim(final_nucleus, main_claim),
        model_frame=_model_frame(manuscript_plan),
        notation_policy=(
            "Use the smallest object and assumption set needed for the main result; "
            "move auxiliary notation to the appendix."
        ),
        main_result_id=main_claim.claim_id if main_claim is not None else None,
        main_result_in_words=main_claim.claim_text if main_claim is not None else "",
        main_result_formal_pointer=(
            f"claim_id={main_claim.claim_id}" if main_claim is not None else None
        ),
        derivatives_or_corollaries=_derivative_claim_ids(claim_table, main_claim),
        numerical_study_purpose=_numerical_purpose(manuscript_plan, has_synthetic),
        synthetic_study_boundary=(
            "Synthetic evidence supports only controlled synthetic assumptions."
            if has_synthetic
            else ""
        ),
        empirical_study_boundary=(
            "No real-data empirical validation is claimed in this deterministic MVP."
            if has_empirical_section
            else ""
        ),
        appendix_policy=(
            "Technical lemmas, proof details, robustness checks, and unsupported extensions "
            "belong in the appendix unless they are the single main result."
        ),
        section_plan=section_plan,
        blocked_or_missing_items=blocked_or_missing,
    )


def infer_narrative_roles(section_title: str) -> list[NarrativeSectionRole]:
    """Infer deterministic narrative roles from a section title."""
    lowered = section_title.lower()
    roles: list[NarrativeSectionRole] = []
    if "abstract" in lowered:
        roles.append(NarrativeSectionRole.CENTRAL_MESSAGE)
    if "introduction" in lowered:
        roles.extend(
            [
                NarrativeSectionRole.PROBLEM_FRAMING,
                NarrativeSectionRole.BACKGROUND_LITERATURE_POSITIONING,
            ]
        )
    if "model" in lowered or "setup" in lowered or "method" in lowered:
        roles.append(NarrativeSectionRole.MODEL_FRAME)
    if "main result" in lowered:
        roles.append(NarrativeSectionRole.MAIN_BODY_RESULT)
    if "theory" in lowered or "result" in lowered:
        roles.append(NarrativeSectionRole.MAIN_BODY_RESULT)
    if "corollary" in lowered or "derivative" in lowered:
        roles.append(NarrativeSectionRole.DERIVATIVE_COROLLARY)
    if "lemma" in lowered or "proof" in lowered:
        roles.append(NarrativeSectionRole.TECHNICAL_LEMMA)
    if "numerical" in lowered or "synthetic experiment" in lowered:
        roles.append(NarrativeSectionRole.NUMERICAL_VALIDATION)
    if "empirical" in lowered or "discussion" in lowered:
        roles.append(NarrativeSectionRole.EMPIRICAL_DISCUSSION)
    if "negative" in lowered or "boundary" in lowered:
        roles.append(NarrativeSectionRole.SYNTHETIC_BOUNDARY)
    if "limitation" in lowered or "conclusion" in lowered:
        roles.append(NarrativeSectionRole.LIMITATIONS_DISCUSSION)
    if "appendix" in lowered:
        roles.append(NarrativeSectionRole.APPENDIX_ONLY_PROOF)
    return list(dict.fromkeys(roles))


def _section_contract(section) -> dict[str, Any]:
    roles = section.narrative_roles or infer_narrative_roles(section.title)
    return {
        "section_id": section.section_id,
        "title": section.title,
        "roles": [role.value for role in roles],
        "allowed_claim_ids": list(section.allowed_claim_ids),
    }


def _select_main_claim(claim_table: ClaimTable) -> Claim | None:
    priority = {
        VerificationLabel.LEAN_VERIFIED: 0,
        VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED: 1,
        VerificationLabel.NEGATIVE_RESULT: 2,
        VerificationLabel.CONJECTURE: 3,
        VerificationLabel.LIMITATION: 4,
        VerificationLabel.UNSUPPORTED: 5,
    }
    allowed = [claim for claim in claim_table.claims if claim.allowed_in_main_text]
    candidates = allowed or list(claim_table.claims)
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda claim: (
            priority.get(claim.claim_label, 9),
            claim.allowed_section,
            claim.claim_id,
        ),
    )[0]


def _central_message(final_nucleus: FinalNucleus, claim: Claim | None) -> str:
    if claim is not None:
        return f"{claim.claim_text} ({claim.claim_label.value})."
    return final_nucleus.reason


def _problem_statement(manuscript_plan: ManuscriptPlan, final_nucleus: FinalNucleus) -> str:
    return (
        f"The paper frames {manuscript_plan.title} around the selected nucleus "
        f"{final_nucleus.id}."
    )


def _why_interesting(final_nucleus: FinalNucleus, claim: Claim | None) -> str:
    if claim is not None:
        return (
            "The result clarifies which labeled claim survives deterministic evidence "
            f"boundaries: {claim.claim_id}."
        )
    return f"The nucleus was selected because: {final_nucleus.reason}"


def _literature_gap(retrieval_context: dict[str, Any] | None) -> str:
    if retrieval_context:
        source_count = retrieval_context.get("source_count", "unknown")
        return (
            "Novelty is positioned against bounded retrieval context "
            f"with source_count={source_count}; complete coverage is not claimed."
        )
    return "Novelty is bounded by available retrieval adequacy; complete coverage is not claimed."


def _bounded_novelty_claim(final_nucleus: FinalNucleus, claim: Claim | None) -> str:
    if claim is None:
        return "No positive novelty claim is made without an admitted main claim."
    return (
        f"The novelty claim is limited to the labeled contribution in {claim.claim_id}; "
        f"the final nucleus remains {final_nucleus.synthesis_label}."
    )


def _model_frame(manuscript_plan: ManuscriptPlan) -> str:
    titles = {section.title for section in manuscript_plan.sections}
    if "General Model" in titles:
        return "Use a general model before instantiations and special cases."
    if "Problem Setup" in titles:
        return "Use a focused problem setup before method and results."
    return "Define objects, assumptions, and outputs before stating results."


def _derivative_claim_ids(claim_table: ClaimTable, main_claim: Claim | None) -> list[str]:
    main_id = main_claim.claim_id if main_claim is not None else None
    return sorted(
        claim.claim_id
        for claim in claim_table.claims
        if claim.claim_id != main_id and claim.allowed_in_main_text
    )


def _numerical_purpose(manuscript_plan: ManuscriptPlan, has_synthetic: bool) -> str:
    numerics = any(
        "numerical" in section.title.lower()
        or "synthetic experiment" in section.title.lower()
        for section in manuscript_plan.sections
    )
    if numerics or has_synthetic:
        return (
            "Numerical or synthetic studies illustrate controlled assumptions, baselines, "
            "and boundary behavior; they are not real-world validation."
        )
    return ""


def _blocked_or_missing_items(
    *,
    manuscript_plan: ManuscriptPlan,
    claim_table: ClaimTable,
    main_claim: Claim | None,
) -> list[str]:
    items: list[str] = []
    if main_claim is None:
        items.append("missing admitted main result claim")
    if not any(section.title == "Introduction" for section in manuscript_plan.sections):
        items.append("missing introduction/problem framing section")
    if not any("Appendix" in section.title for section in manuscript_plan.sections):
        items.append("missing appendix allocation")
    if not claim_table.claims:
        items.append("missing claim table entries")
    return sorted(set(items))


__all__ = [
    "NarrativeContractError",
    "NarrativeInputs",
    "build_narrative_contract",
    "infer_narrative_roles",
    "load_narrative_inputs",
]
