from __future__ import annotations

from factori.final_selection import StageCResultItem, final_branch_score, select_final_nucleus
from factori.schemas import (
    AbstractionAttackReport,
    AbstractionReport,
    AbstractModel,
    ArtifactRef,
    ArtifactType,
    BranchStatus,
    BranchVerificationType,
    Candidate,
    FinalNucleusType,
    InstantiationMap,
    ScoreVector,
    StageCVerificationRecord,
    VerificationLabel,
)

HASH = "a" * 64


def test_final_nucleus_is_abstract_when_abstraction_passes() -> None:
    branches = [_item("candidate-a"), _item("candidate-b")]
    report = _passing_abstraction_report(branches)
    attack = AbstractionAttackReport(
        abstract_model_id=report.abstract_model_id,
        rt_abstract=0.90,
        tau_abstract_redteam=0.75,
        attack_passed=True,
    )

    nucleus = select_final_nucleus(branches, [report], [attack])

    assert nucleus.nucleus_type == FinalNucleusType.ABSTRACT_NUCLEUS
    assert nucleus.abstract_model is not None
    assert nucleus.abstract_model.synthesis_label == "AbstractSynthesis"
    assert all(
        label == VerificationLabel.LEAN_VERIFIED
        for label in nucleus.labels_by_candidate.values()
    )


def test_final_nucleus_falls_back_to_branch_when_no_abstraction_passes() -> None:
    branches = [
        _item("candidate-a", label=VerificationLabel.CONJECTURE),
        _item("candidate-b", label=VerificationLabel.LEAN_VERIFIED),
    ]

    nucleus = select_final_nucleus(branches, [])

    assert nucleus.nucleus_type == FinalNucleusType.BRANCH_NUCLEUS
    assert nucleus.candidate_id == "candidate-b"
    assert nucleus.labels_by_candidate == {"candidate-b": VerificationLabel.LEAN_VERIFIED}


def test_branch_nucleus_selection_is_deterministic() -> None:
    branches = [
        _item("candidate-b", label=VerificationLabel.CONJECTURE),
        _item("candidate-a", label=VerificationLabel.CONJECTURE),
    ]

    first = select_final_nucleus(branches, [])
    second = select_final_nucleus(list(reversed(branches)), [])

    assert first == second
    assert first.candidate_id == "candidate-a"


def test_final_branch_score_prefers_verified_and_informative_results() -> None:
    lean = _item("candidate-lean", label=VerificationLabel.LEAN_VERIFIED)
    synthetic = _item(
        "candidate-synthetic",
        label=VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
    )
    negative = _item("candidate-negative", label=VerificationLabel.NEGATIVE_RESULT)
    conjecture = _item("candidate-conjecture", label=VerificationLabel.CONJECTURE)

    assert final_branch_score(lean) > final_branch_score(synthetic)
    assert final_branch_score(synthetic) > final_branch_score(negative)
    assert final_branch_score(negative) > final_branch_score(conjecture)


def _passing_abstraction_report(branches: list[StageCResultItem]) -> AbstractionReport:
    maps = [
        InstantiationMap(
            id=f"abstract-demo-to-{branch.candidate.id}",
            abstract_model_id="abstract-demo",
            candidate_id=branch.candidate.id,
            coherent=True,
            coherence_score=0.90,
            role="instance",
            branch_label=branch.verification_record.label,
            label_preserved=True,
            reason="test map",
        )
        for branch in branches
    ]
    model = AbstractModel(
        id="abstract-demo",
        objects=["calibration"],
        assumptions=["instances keep their original verification labels"],
        mechanism="shared deterministic mechanism",
        claim_family="AbstractSynthesis preserving LeanVerified",
        instantiation_maps=maps,
    )
    return AbstractionReport(
        abstract_model_id=model.id,
        model=model,
        coverage=1.0,
        coherence=0.90,
        compression=0.88,
        generativity=0.80,
        verifiability=1.0,
        total_score=0.92,
        tau_a=0.75,
        accepted_by_score=True,
        branch_ids=[branch.candidate.id for branch in branches],
    )


def _item(
    candidate_id: str,
    *,
    label: VerificationLabel = VerificationLabel.LEAN_VERIFIED,
) -> StageCResultItem:
    evidence = [_evidence()] if label in {
        VerificationLabel.LEAN_VERIFIED,
        VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
        VerificationLabel.NEGATIVE_RESULT,
    } else []
    return StageCResultItem(
        candidate=Candidate(
            id=candidate_id,
            domain="machine learning",
            method="calibration",
            question="Can calibration branches be unified?",
            theory="Theorem-style calibration claim",
        ),
        verification_record=StageCVerificationRecord(
            candidate_id=candidate_id,
            branch_type=BranchVerificationType.MATHEMATICAL,
            label=label,
            status=BranchStatus.STOP_SUCCESS,
            evidence_artifacts=evidence,
            reason="test record",
        ),
        stage_c_score=ScoreVector(
            novelty=0.80,
            feasibility=0.82,
            verifiability=0.86,
            reviewer=0.80,
            difficulty=0.30,
            diversity=0.60,
            uncertainty=0.04,
        ),
    )


def _evidence() -> ArtifactRef:
    return ArtifactRef(
        id="proof",
        type=ArtifactType.LEAN,
        path="runs/run-1/lean/fake-proof-candidate.json",
        content_hash=HASH,
        producing_commit_hash=HASH,
        metadata={"evidence_role": "fake_proof"},
    )
