"""Verification evidence-boundary helpers."""

from __future__ import annotations

from collections.abc import Iterable

from factori.schemas import ArtifactRef, ArtifactType, VerificationLabel

PROOF_EVIDENCE_ROLE = "fake_proof"
REAL_PROOF_EVIDENCE_ROLE = "proof"
SYNTHETIC_EXPERIMENT_EVIDENCE_ROLE = "fake_synthetic_experiment"
REAL_SYNTHETIC_EXPERIMENT_EVIDENCE_ROLE = "synthetic_experiment"
REAL_DATA_EXPERIMENT_EVIDENCE_ROLE = "real_data_experiment"


def claim_label_allowed(
    label: VerificationLabel | str,
    evidence_artifacts: Iterable[ArtifactRef],
) -> bool:
    """Return whether artifacts justify the requested verification label."""
    try:
        verification_label = VerificationLabel(label)
    except ValueError:
        return False

    artifacts = list(evidence_artifacts)
    if verification_label == VerificationLabel.LEAN_VERIFIED:
        return any(is_proof_evidence(artifact) for artifact in artifacts)
    if verification_label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
        return any(is_synthetic_experiment_evidence(artifact) for artifact in artifacts)
    if verification_label == VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED:
        return False
    if verification_label == VerificationLabel.EXPERIMENT_VERIFIED:
        return False
    return verification_label in {
        VerificationLabel.CONJECTURE,
        VerificationLabel.NEGATIVE_RESULT,
        VerificationLabel.LIMITATION,
        VerificationLabel.UNSUPPORTED,
    }


def is_proof_evidence(artifact: ArtifactRef) -> bool:
    """Return whether an artifact is proof evidence for this MVP."""
    return (
        artifact.type == ArtifactType.LEAN
        and artifact.metadata.get("evidence_role")
        in {PROOF_EVIDENCE_ROLE, REAL_PROOF_EVIDENCE_ROLE}
        and _artifact_is_linked_evidence(artifact)
    )


def is_synthetic_experiment_evidence(artifact: ArtifactRef) -> bool:
    """Return whether an artifact is synthetic experiment evidence for this MVP."""
    return (
        artifact.type == ArtifactType.EXPERIMENT
        and artifact.metadata.get("evidence_role")
        in {SYNTHETIC_EXPERIMENT_EVIDENCE_ROLE, REAL_SYNTHETIC_EXPERIMENT_EVIDENCE_ROLE}
        and _artifact_is_linked_evidence(artifact)
    )


def _artifact_is_linked_evidence(artifact: ArtifactRef) -> bool:
    if artifact.producing_commit_hash is None:
        return False
    return artifact.is_mvp_verification_evidence()
