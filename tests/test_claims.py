from __future__ import annotations

from factori.claims import build_claim_table, is_claim_admissible
from factori.final_selection import StageCResultItem
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BranchStatus,
    BranchVerificationType,
    Candidate,
    Claim,
    FinalNucleus,
    FinalNucleusType,
    StageCVerificationRecord,
    VerificationLabel,
)

HASH = "a" * 64


def test_claim_table_creation_is_deterministic() -> None:
    final_nucleus = _final_nucleus("candidate-a")
    item = _stage_c_item("candidate-a", VerificationLabel.LEAN_VERIFIED, [_proof("candidate-a")])
    artifact_index = {
        artifact.id: artifact for artifact in item.verification_record.evidence_artifacts
    }

    first = build_claim_table(final_nucleus, [item], artifact_index)
    second = build_claim_table(final_nucleus, [item], artifact_index)

    assert first == second
    assert first.claims[0].claim_label == VerificationLabel.LEAN_VERIFIED
    assert first.claims[0].allowed_section == "Theory"


def test_lean_verified_claims_require_exact_proof_evidence() -> None:
    claim = _claim(
        label=VerificationLabel.LEAN_VERIFIED,
        candidate_id="candidate-a",
        section="Theory",
    )

    assert is_claim_admissible(claim, [_proof("candidate-a")])
    assert not is_claim_admissible(claim, [])
    assert not is_claim_admissible(claim, [_proof("candidate-b")])


def test_synthetic_experiment_claims_require_synthetic_experiment_evidence() -> None:
    claim = _claim(
        label=VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
        text="Synthetic simulation evidence supports candidate-a.",
        section="Synthetic Experiments",
    )

    assert is_claim_admissible(claim, [_synthetic_experiment("candidate-a")])
    assert not is_claim_admissible(claim, [])


def test_real_data_experiment_verified_is_rejected_in_mvp() -> None:
    claim = _claim(
        label=VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED,
        text="Real-world performance is verified.",
        section="Results",
    )

    assert not is_claim_admissible(claim, [_synthetic_experiment("candidate-a")])


def test_conjecture_is_not_allowed_as_theorem_or_main_result() -> None:
    claim = _claim(
        label=VerificationLabel.CONJECTURE,
        text="Theorem: this unresolved branch is proven.",
        section="Results",
    )

    assert not is_claim_admissible(claim, [])


def test_unsupported_claims_are_blocked_from_main_text() -> None:
    main = _claim(label=VerificationLabel.UNSUPPORTED, section="Results", allowed=True)
    future = _claim(label=VerificationLabel.UNSUPPORTED, section="Future Work", allowed=False)

    assert not is_claim_admissible(main, [])
    assert is_claim_admissible(future, [])


def test_latex_artifacts_cannot_support_claims() -> None:
    claim = _claim(label=VerificationLabel.LEAN_VERIFIED, section="Theory")

    assert not is_claim_admissible(claim, [_latex()])


def _claim(
    *,
    label: VerificationLabel,
    candidate_id: str = "candidate-a",
    text: str = "Candidate candidate-a has an admissible claim.",
    section: str = "Theory",
    allowed: bool = True,
) -> Claim:
    return Claim(
        claim_id=f"claim-{candidate_id}",
        claim_text=text,
        claim_label=label,
        candidate_id=candidate_id,
        evidence_artifact_ids=[],
        evidence_types=[],
        allowed_in_main_text=allowed,
        allowed_section=section,
        reason="test claim",
    )


def _final_nucleus(candidate_id: str) -> FinalNucleus:
    return FinalNucleus(
        id=f"final-{candidate_id}",
        nucleus_type=FinalNucleusType.BRANCH_NUCLEUS,
        candidate_id=candidate_id,
        supporting_candidate_ids=[candidate_id],
        labels_by_candidate={candidate_id: VerificationLabel.LEAN_VERIFIED},
        evidence_artifacts=[_proof(candidate_id)],
        reason="test final nucleus",
    )


def _stage_c_item(
    candidate_id: str,
    label: VerificationLabel,
    evidence: list[ArtifactRef],
) -> StageCResultItem:
    return StageCResultItem(
        candidate=Candidate(
            id=candidate_id,
            question="Can this branch support a manuscript claim?",
            theory="Theorem-style claim",
        ),
        verification_record=StageCVerificationRecord(
            candidate_id=candidate_id,
            branch_type=BranchVerificationType.MATHEMATICAL,
            label=label,
            status=BranchStatus.STOP_SUCCESS,
            evidence_artifacts=evidence,
            reason="test record",
        ),
    )


def _proof(candidate_id: str) -> ArtifactRef:
    return ArtifactRef(
        id=f"fake-proof-{candidate_id}",
        type=ArtifactType.LEAN,
        path=f"runs/run-1/lean/fake-proof-{candidate_id}.json",
        content_hash=HASH,
        producing_commit_hash=HASH,
        metadata={"evidence_role": "fake_proof"},
    )


def _synthetic_experiment(candidate_id: str) -> ArtifactRef:
    return ArtifactRef(
        id=f"fake-synthetic-experiment-{candidate_id}",
        type=ArtifactType.EXPERIMENT,
        path=f"runs/run-1/experiments/fake-synthetic-experiment-{candidate_id}.json",
        content_hash=HASH,
        producing_commit_hash=HASH,
        metadata={"evidence_role": "fake_synthetic_experiment"},
    )


def _latex() -> ArtifactRef:
    return ArtifactRef(
        id="paper",
        type=ArtifactType.LATEX,
        path="runs/run-1/latex/paper.tex",
        content_hash=HASH,
        producing_commit_hash=HASH,
        metadata={"evidence_role": "fake_proof"},
    )
