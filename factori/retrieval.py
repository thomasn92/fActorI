"""Deterministic retrieval adequacy certificate skeleton."""

from __future__ import annotations

from factori.schemas import BranchStatus, LiteratureState, RetrievalAdequacyCertificate

TAU_ADEQUACY = 0.80
DEFAULT_RETRIEVAL_WEIGHTS = {
    "semantic": 0.20,
    "keyword": 0.20,
    "citation": 0.20,
    "diversity": 0.20,
    "adversarial": 0.20,
}


def compute_retrieval_adequacy(
    literature_state: LiteratureState,
    *,
    tau_adequacy: float = TAU_ADEQUACY,
    weights: dict[str, float] | None = None,
) -> RetrievalAdequacyCertificate:
    """Compute a deterministic placeholder retrieval adequacy certificate."""
    weights = weights or DEFAULT_RETRIEVAL_WEIGHTS
    rho_adequacy = round(
        weights["semantic"] * literature_state.semantic
        + weights["keyword"] * literature_state.keyword
        + weights["citation"] * literature_state.citation
        + weights["diversity"] * literature_state.diversity
        + weights["adversarial"] * literature_state.adversarial,
        6,
    )
    passed = rho_adequacy >= tau_adequacy
    return RetrievalAdequacyCertificate(
        semantic=literature_state.semantic,
        keyword=literature_state.keyword,
        citation=literature_state.citation,
        diversity=literature_state.diversity,
        adversarial=literature_state.adversarial,
        weights=dict(weights),
        rho_adequacy=rho_adequacy,
        tau_adequacy=tau_adequacy,
        passed=passed,
        status=BranchStatus.ACTIVE
        if passed
        else BranchStatus.INSUFFICIENT_RETRIEVAL_ADEQUACY,
    )
