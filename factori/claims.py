"""Deterministic claim and evidence planning helpers."""

from __future__ import annotations

from factori.evidence import (
    claim_label_allowed,
    is_proof_evidence,
)
from factori.final_selection import StageCResultItem
from factori.schemas import (
    ArtifactRef,
    Claim,
    ClaimEvidenceLink,
    ClaimTable,
    FinalNucleus,
    VerificationLabel,
)

ALLOWED_SECTIONS = [
    "Abstract",
    "Introduction",
    "Related Work",
    "Model",
    "Theory",
    "Synthetic Experiments",
    "Results",
    "Negative Results",
    "Limitations",
    "Future Work",
    "Appendix",
]

MAIN_TEXT_SECTIONS = {
    "Abstract",
    "Introduction",
    "Model",
    "Theory",
    "Synthetic Experiments",
    "Results",
    "Negative Results",
    "Limitations",
}


def build_claim_table(
    final_nucleus: FinalNucleus,
    stage_c_results: list[StageCResultItem],
    artifact_index: dict[str, ArtifactRef],
) -> ClaimTable:
    """Build a deterministic claim/evidence table for a final nucleus."""
    supporting_ids = set(final_nucleus.supporting_candidate_ids)
    selected_results = [
        item
        for item in sorted(stage_c_results, key=lambda result: result.candidate.id)
        if item.candidate.id in supporting_ids
    ]
    claims: list[Claim] = []
    links: list[ClaimEvidenceLink] = []
    for item in selected_results:
        claim = _claim_from_stage_c_result(final_nucleus, item, artifact_index)
        claims.append(claim)
        links.extend(_evidence_links(claim, artifact_index))
    return ClaimTable(
        final_nucleus_id=final_nucleus.id,
        claims=claims,
        evidence_links=links,
    )


def is_claim_admissible(
    claim: Claim,
    evidence_artifacts: list[ArtifactRef],
) -> bool:
    """Return whether a claim may appear in its requested manuscript section."""
    if claim.allowed_section not in ALLOWED_SECTIONS:
        return False
    if claim.claim_label == VerificationLabel.LEAN_VERIFIED:
        return _has_exact_proof_evidence(claim, evidence_artifacts)
    if claim.claim_label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
        return (
            claim.allowed_section in {"Synthetic Experiments", "Results", "Abstract"}
            and _is_synthetic_claim_text(claim.claim_text)
            and claim_label_allowed(claim.claim_label, evidence_artifacts)
        )
    if claim.claim_label == VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED:
        return False
    if claim.claim_label == VerificationLabel.EXPERIMENT_VERIFIED:
        return False
    if claim.claim_label == VerificationLabel.CONJECTURE:
        return claim.allowed_section in {"Theory", "Future Work", "Appendix"}
    if claim.claim_label == VerificationLabel.NEGATIVE_RESULT:
        return claim.allowed_section in {"Negative Results", "Results", "Limitations"}
    if claim.claim_label == VerificationLabel.LIMITATION:
        return claim.allowed_section == "Limitations"
    if claim.claim_label == VerificationLabel.UNSUPPORTED:
        return claim.allowed_section == "Future Work" and not claim.allowed_in_main_text
    return False


def _claim_from_stage_c_result(
    final_nucleus: FinalNucleus,
    item: StageCResultItem,
    artifact_index: dict[str, ArtifactRef],
) -> Claim:
    record = item.verification_record
    evidence_artifact_ids = [artifact.id for artifact in record.evidence_artifacts]
    evidence_artifacts = [
        artifact_index[artifact_id]
        for artifact_id in evidence_artifact_ids
        if artifact_id in artifact_index
    ]
    evidence_types = sorted({artifact.type.value for artifact in evidence_artifacts})
    allowed_section = _allowed_section(record.label, item)
    claim = Claim(
        claim_id=f"claim-{item.candidate.id}",
        claim_text=_claim_text(final_nucleus, item),
        claim_label=record.label,
        candidate_id=item.candidate.id,
        evidence_artifact_ids=evidence_artifact_ids,
        evidence_types=evidence_types,
        allowed_in_main_text=allowed_section in MAIN_TEXT_SECTIONS
        and record.label != VerificationLabel.UNSUPPORTED,
        allowed_section=allowed_section,
        reason=_claim_reason(record.label),
    )
    admitted = is_claim_admissible(claim, evidence_artifacts)
    if admitted == claim.allowed_in_main_text or claim.claim_label == VerificationLabel.UNSUPPORTED:
        return claim
    return claim.model_copy(update={"allowed_in_main_text": admitted})


def _evidence_links(
    claim: Claim,
    artifact_index: dict[str, ArtifactRef],
) -> list[ClaimEvidenceLink]:
    links: list[ClaimEvidenceLink] = []
    for artifact_id in claim.evidence_artifact_ids:
        artifact = artifact_index.get(artifact_id)
        if artifact is None:
            continue
        links.append(
            ClaimEvidenceLink(
                claim_id=claim.claim_id,
                artifact_id=artifact.id,
                artifact_type=artifact.type,
                evidence_role=artifact.metadata.get("evidence_role"),
                supports_label=claim_label_allowed(claim.claim_label, [artifact]),
            )
        )
    return links


def _has_exact_proof_evidence(
    claim: Claim,
    evidence_artifacts: list[ArtifactRef],
) -> bool:
    return any(
        is_proof_evidence(artifact)
        and (claim.candidate_id in artifact.id or claim.candidate_id in artifact.path)
        for artifact in evidence_artifacts
    )


def _allowed_section(label: VerificationLabel, item: StageCResultItem) -> str:
    if label == VerificationLabel.LEAN_VERIFIED:
        return "Theory"
    if label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
        return "Synthetic Experiments"
    if label == VerificationLabel.REAL_DATA_EXPERIMENT_VERIFIED:
        return "Future Work"
    if label == VerificationLabel.CONJECTURE:
        return "Theory"
    if label == VerificationLabel.NEGATIVE_RESULT:
        return "Negative Results"
    if label == VerificationLabel.LIMITATION:
        return "Limitations"
    if label == VerificationLabel.UNSUPPORTED:
        return "Future Work"
    return "Appendix"


def _claim_text(final_nucleus: FinalNucleus, item: StageCResultItem) -> str:
    candidate = item.candidate
    label = item.verification_record.label
    nucleus_prefix = (
        "Within the abstract synthesis"
        if final_nucleus.abstract_model is not None
        else "For the selected branch"
    )
    if label == VerificationLabel.LEAN_VERIFIED:
        return (
            f"{nucleus_prefix}, candidate {candidate.id} has a fake Lean-verified "
            f"mathematical claim: {candidate.question}"
        )
    if label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
        return (
            f"{nucleus_prefix}, candidate {candidate.id} has synthetic simulation "
            f"evidence for: {candidate.question}"
        )
    if label == VerificationLabel.NEGATIVE_RESULT:
        return (
            f"{nucleus_prefix}, candidate {candidate.id} is a negative or boundary "
            f"finding: {candidate.question}"
        )
    if label == VerificationLabel.CONJECTURE:
        return (
            f"{nucleus_prefix}, candidate {candidate.id} remains a conjecture: "
            f"{candidate.question}"
        )
    if label == VerificationLabel.LIMITATION:
        return f"{nucleus_prefix}, candidate {candidate.id} is a limitation: {candidate.question}"
    return f"{nucleus_prefix}, candidate {candidate.id} is unsupported: {candidate.question}"


def _claim_reason(label: VerificationLabel) -> str:
    if label == VerificationLabel.LEAN_VERIFIED:
        return "allowed only with linked fake proof evidence"
    if label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
        return "allowed only as synthetic/simulation evidence"
    if label == VerificationLabel.NEGATIVE_RESULT:
        return "must remain framed as negative or boundary evidence"
    if label == VerificationLabel.CONJECTURE:
        return "must remain a conjecture"
    if label == VerificationLabel.LIMITATION:
        return "must remain a limitation"
    return "unsupported claims are restricted to future work or blocked"


def _is_synthetic_claim_text(text: str) -> bool:
    lowered = text.lower()
    if any(token in lowered for token in ["real-world", "real world", "field deployment"]):
        return False
    return "synthetic" in lowered or "simulation" in lowered
