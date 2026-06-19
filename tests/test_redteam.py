from __future__ import annotations

from factori.redteam import run_redteam_checks, theorem_significance
from factori.schemas import BranchStatus, Candidate, LiteratureState, ScoreVector


def test_triviality_failure_downgrades_or_rejects_theorem_candidate() -> None:
    candidate = Candidate(
        id="candidate-trivial-theorem",
        question="Trivial theorem?",
        theory="Theorem-style formulation",
        literature=_adequate_literature(),
        variant_type="theorem_or_conjecture_form",
        symbolic_state={"variant_type": "theorem_or_conjecture_form"},
    )
    score = _score()

    report = run_redteam_checks(candidate, score)

    assert theorem_significance(candidate, score) < 0.70
    assert not report.triviality_passed
    assert report.status == BranchStatus.TRIVIAL_THEOREM_CANDIDATE


def test_insufficient_retrieval_blocks_stage_c_readiness() -> None:
    candidate = Candidate(
        id="candidate-low-retrieval",
        question="Weak retrieval?",
        literature=LiteratureState(
            semantic=0.40,
            keyword=0.40,
            citation=0.40,
            diversity=0.40,
            adversarial=0.40,
        ),
    )

    report = run_redteam_checks(candidate, _score())

    assert report.status == BranchStatus.INSUFFICIENT_RETRIEVAL_ADEQUACY
    assert not report.stage_c_ready


def test_redteam_passes_with_adequate_retrieval_and_nontrivial_claim() -> None:
    candidate = Candidate(
        id="candidate-nontrivial-theorem",
        question="Nontrivial theorem?",
        theory="Theorem-style formulation",
        literature=_adequate_literature(),
        variant_type="theorem_or_conjecture_form",
        symbolic_state={"variant_type": "theorem_or_conjecture_form"},
    )

    report = run_redteam_checks(candidate, _score(novelty=0.85, verifiability=0.85))

    assert report.stage_c_ready
    assert report.status == BranchStatus.ACTIVE


def _adequate_literature() -> LiteratureState:
    return LiteratureState(
        semantic=0.85,
        keyword=0.82,
        citation=0.81,
        diversity=0.80,
        adversarial=0.80,
    )


def _score(novelty: float = 0.65, verifiability: float = 0.72) -> ScoreVector:
    return ScoreVector(
        novelty=novelty,
        feasibility=0.80,
        verifiability=verifiability,
        reviewer=0.70,
        difficulty=0.40,
        diversity=0.50,
    )
