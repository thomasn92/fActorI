from __future__ import annotations

from factori.evidence import (
    PROOF_EVIDENCE_ROLE,
    SYNTHETIC_EXPERIMENT_EVIDENCE_ROLE,
    claim_label_allowed,
)
from factori.schemas import ArtifactRef, ArtifactType, VerificationLabel

HASH = "a" * 64


def test_proof_evidence_required_for_lean_verified() -> None:
    proof = _artifact(
        artifact_type=ArtifactType.LEAN,
        path="runs/run-1/lean/fake-proof-candidate.json",
        metadata={"evidence_role": PROOF_EVIDENCE_ROLE},
    )
    unlinked_proof = proof.model_copy(update={"producing_commit_hash": None})

    assert claim_label_allowed(VerificationLabel.LEAN_VERIFIED, [proof])
    assert not claim_label_allowed(VerificationLabel.LEAN_VERIFIED, [unlinked_proof])
    assert not claim_label_allowed(VerificationLabel.LEAN_VERIFIED, [])


def test_synthetic_experiment_evidence_required_for_synthetic_verified() -> None:
    experiment = _artifact(
        artifact_type=ArtifactType.EXPERIMENT,
        path="runs/run-1/experiments/fake-synthetic-experiment-candidate.json",
        metadata={"evidence_role": SYNTHETIC_EXPERIMENT_EVIDENCE_ROLE},
    )

    assert claim_label_allowed(VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED, [experiment])
    assert not claim_label_allowed(VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED, [])


def test_latex_artifacts_cannot_support_verification_labels() -> None:
    latex = _artifact(
        artifact_type=ArtifactType.LATEX,
        path="runs/run-1/latex/paper.tex",
        metadata={"evidence_role": PROOF_EVIDENCE_ROLE},
    )

    assert not claim_label_allowed(VerificationLabel.LEAN_VERIFIED, [latex])
    assert not claim_label_allowed(VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED, [latex])


def test_real_data_and_generic_experiment_labels_are_rejected_in_mvp() -> None:
    experiment = _artifact(
        artifact_type=ArtifactType.EXPERIMENT,
        path="runs/run-1/experiments/real-data-experiment-candidate.json",
        metadata={"evidence_role": "real_data_experiment"},
    )

    assert not claim_label_allowed(VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED, [experiment])
    assert not claim_label_allowed(VerificationLabel.EXPERIMENT_VERIFIED, [experiment])


def test_weak_labels_are_allowed_but_unknown_labels_are_rejected() -> None:
    assert claim_label_allowed(VerificationLabel.CONJECTURE, [])
    assert claim_label_allowed(VerificationLabel.NEGATIVE_RESULT, [])
    assert claim_label_allowed(VerificationLabel.LIMITATION, [])
    assert claim_label_allowed(VerificationLabel.UNSUPPORTED, [])
    assert not claim_label_allowed("NotALabel", [])


def _artifact(
    *,
    artifact_type: ArtifactType,
    path: str,
    metadata: dict[str, str],
) -> ArtifactRef:
    return ArtifactRef(
        id="artifact-1",
        type=artifact_type,
        path=path,
        content_hash=HASH,
        producing_commit_hash=HASH,
        metadata=metadata,
    )
