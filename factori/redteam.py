"""Deterministic fake red-team checks for Stage B."""

from __future__ import annotations

from factori.retrieval import compute_retrieval_adequacy
from factori.schemas import BranchStatus, Candidate, RedTeamReport, ScoreVector

TRIVIALITY_THRESHOLD = 0.70


def run_redteam_checks(candidate: Candidate, score: ScoreVector) -> RedTeamReport:
    """Run deterministic novelty-risk, retrieval, and triviality checks."""
    retrieval_certificate = compute_retrieval_adequacy(candidate.literature)
    theorem_style = _is_theorem_style(candidate)
    triviality_score = theorem_significance(candidate, score) if theorem_style else None
    triviality_passed = triviality_score is None or triviality_score >= TRIVIALITY_THRESHOLD
    redteam_rejection = bool("redteam-reject" in candidate.id)

    if redteam_rejection:
        status = BranchStatus.REJECTED_RED_TEAM
    elif not retrieval_certificate.passed:
        status = BranchStatus.INSUFFICIENT_RETRIEVAL_ADEQUACY
    elif not triviality_passed:
        status = BranchStatus.TRIVIAL_THEOREM_CANDIDATE
    else:
        status = BranchStatus.ACTIVE

    return RedTeamReport(
        candidate_id=candidate.id,
        retrieval_certificate=retrieval_certificate,
        novelty_risk=candidate.literature.novelty_risk,
        triviality_score=triviality_score,
        triviality_passed=triviality_passed,
        redteam_rejection=redteam_rejection,
        stage_c_ready=retrieval_certificate.passed and not redteam_rejection and triviality_passed,
        status=status,
    )


def theorem_significance(candidate: Candidate, score: ScoreVector) -> float:
    """Return deterministic theorem significance T_sig."""
    variant_type = str(candidate.symbolic_state.get("variant_type", ""))
    base = 0.68 + (score.novelty - 0.45) * 0.30 + (score.verifiability - 0.60) * 0.20
    if variant_type == "theorem_or_conjecture_form":
        base += 0.08
    if candidate.id.startswith("candidate-trivial-theorem") or "trivial-candidate" in candidate.id:
        base = 0.42
    return round(min(1.0, max(0.0, base)), 6)


def _is_theorem_style(candidate: Candidate) -> bool:
    variant_type = str(candidate.symbolic_state.get("variant_type", ""))
    text = " ".join(
        [candidate.question, candidate.theory or "", candidate.hypothesis or ""]
    ).lower()
    return variant_type == "theorem_or_conjecture_form" or "theorem" in text or "conjecture" in text
