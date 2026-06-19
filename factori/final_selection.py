"""Deterministic final nucleus selection."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from factori.schemas import (
    AbstractionAttackReport,
    AbstractionReport,
    ArtifactRef,
    Candidate,
    FinalNucleus,
    FinalNucleusType,
    ScoreVector,
    StageCVerificationRecord,
    VerificationLabel,
)


@dataclass(frozen=True)
class StageCResultItem:
    """Loaded Stage C result used by abstract synthesis."""

    candidate: Candidate
    verification_record: StageCVerificationRecord
    stage_c_score: ScoreVector | None = None


def select_final_nucleus(
    stage_c_results: list[StageCResultItem],
    abstraction_reports: list[AbstractionReport],
    attack_reports: list[AbstractionAttackReport] | None = None,
) -> FinalNucleus:
    """Select an abstract or branch final nucleus deterministically."""
    attacks_by_id = {
        attack.abstract_model_id: attack for attack in attack_reports or []
    }
    passing_reports = [
        report
        for report in abstraction_reports
        if report.accepted_by_score
        and attacks_by_id.get(report.abstract_model_id) is not None
        and attacks_by_id[report.abstract_model_id].attack_passed
        and len(report.model.instantiation_maps) >= 2
    ]
    if passing_reports:
        selected = sorted(
            passing_reports,
            key=lambda report: (-report.total_score, report.abstract_model_id),
        )[0]
        labels_by_candidate = _labels_by_candidate(stage_c_results)
        return FinalNucleus(
            id=f"final-{selected.abstract_model_id}",
            nucleus_type=FinalNucleusType.ABSTRACT_NUCLEUS,
            abstract_model=selected.model,
            candidate_id=None,
            supporting_candidate_ids=[
                mapping.candidate_id for mapping in selected.model.instantiation_maps
            ],
            labels_by_candidate=labels_by_candidate,
            evidence_artifacts=_evidence_for_candidates(
                stage_c_results,
                [mapping.candidate_id for mapping in selected.model.instantiation_maps],
            ),
            reason="accepted abstraction has the highest deterministic abstraction score",
        )

    if not stage_c_results:
        raise ValueError("cannot select final nucleus without Stage C results")

    selected_branch = sorted(
        stage_c_results,
        key=lambda item: (-final_branch_score(item), item.candidate.id),
    )[0]
    return FinalNucleus(
        id=f"final-{selected_branch.candidate.id}",
        nucleus_type=FinalNucleusType.BRANCH_NUCLEUS,
        abstract_model=None,
        candidate_id=selected_branch.candidate.id,
        supporting_candidate_ids=[selected_branch.candidate.id],
        labels_by_candidate={
            selected_branch.candidate.id: selected_branch.verification_record.label
        },
        evidence_artifacts=selected_branch.verification_record.evidence_artifacts,
        reason="no accepted abstraction survived; selected best labeled branch",
    )


def final_branch_score(item: StageCResultItem) -> float:
    """Score a Stage C branch for fallback nucleus selection."""
    label_score = _label_score(item.verification_record)
    score = item.stage_c_score
    stage_c_score = score.base_score() if score is not None else 0.50
    uncertainty_score = 1.0 - score.uncertainty if score is not None else 0.70
    evidence_score = _evidence_score(item.verification_record.evidence_artifacts)
    total = (
        0.40 * label_score
        + 0.25 * stage_c_score
        + 0.20 * uncertainty_score
        + 0.15 * evidence_score
    )
    return round(min(1.0, max(0.0, total)), 6)


def _label_score(record: StageCVerificationRecord) -> float:
    if record.label == VerificationLabel.LEAN_VERIFIED:
        return 1.00
    if record.label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
        return 0.92
    if record.label == VerificationLabel.NEGATIVE_RESULT:
        return 0.74 if record.evidence_artifacts else 0.62
    if record.label == VerificationLabel.CONJECTURE:
        return 0.55
    if record.label == VerificationLabel.LIMITATION:
        return 0.42
    return 0.12


def _evidence_score(artifacts: list[ArtifactRef]) -> float:
    if not artifacts:
        return 0.0
    ready = [
        artifact
        for artifact in artifacts
        if artifact.producing_commit_hash and artifact.is_mvp_verification_evidence()
    ]
    return min(1.0, len(ready) / max(1, len(artifacts)))


def _labels_by_candidate(
    stage_c_results: Iterable[StageCResultItem],
) -> dict[str, VerificationLabel]:
    return {
        item.candidate.id: item.verification_record.label
        for item in stage_c_results
    }


def _evidence_for_candidates(
    stage_c_results: Iterable[StageCResultItem],
    candidate_ids: list[str],
) -> list[ArtifactRef]:
    candidate_id_set = set(candidate_ids)
    artifacts: list[ArtifactRef] = []
    for item in stage_c_results:
        if item.candidate.id in candidate_id_set:
            artifacts.extend(item.verification_record.evidence_artifacts)
    return artifacts
