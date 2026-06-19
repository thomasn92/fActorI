from __future__ import annotations

from factori.retrieval import compute_retrieval_adequacy
from factori.schemas import BranchStatus, LiteratureState


def test_retrieval_adequacy_is_deterministic() -> None:
    literature_state = LiteratureState(
        semantic=0.80,
        keyword=0.82,
        citation=0.78,
        diversity=0.84,
        adversarial=0.76,
    )

    first = compute_retrieval_adequacy(literature_state)
    second = compute_retrieval_adequacy(literature_state)

    assert first == second
    assert first.rho_adequacy == 0.8
    assert first.passed


def test_retrieval_failure_produces_insufficient_status() -> None:
    certificate = compute_retrieval_adequacy(
        LiteratureState(
            semantic=0.40,
            keyword=0.50,
            citation=0.45,
            diversity=0.35,
            adversarial=0.30,
        )
    )

    assert not certificate.passed
    assert certificate.status == BranchStatus.INSUFFICIENT_RETRIEVAL_ADEQUACY
