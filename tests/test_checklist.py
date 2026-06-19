from __future__ import annotations

from factori.checklist import build_manuscript_checklist
from factori.schemas import (
    ArtifactType,
    ChecklistCategory,
    Claim,
    ClaimEvidenceLink,
    ClaimTable,
    DraftClaimPlaceholder,
    DraftSection,
    DraftSkeleton,
    VerificationLabel,
)


def test_checklist_generation_is_deterministic() -> None:
    skeleton = _skeleton()
    claim_table = _claim_table()

    first = build_manuscript_checklist(skeleton, claim_table, [])
    second = build_manuscript_checklist(skeleton, claim_table, [])

    assert first == second
    assert first.failures_count == 0


def test_checklist_detects_missing_evidence_links() -> None:
    skeleton = _skeleton(evidence_ids=[])
    claim_table = _claim_table(evidence_ids=[])

    checklist = build_manuscript_checklist(skeleton, claim_table, [])

    assert not _item(checklist, "evidence-main-claims").passed


def test_checklist_detects_unsupported_main_claims() -> None:
    skeleton = _skeleton(
        label=VerificationLabel.UNSUPPORTED,
        section="Results",
        evidence_ids=[],
    )
    claim_table = _claim_table(label=VerificationLabel.UNSUPPORTED, evidence_ids=[])

    checklist = build_manuscript_checklist(skeleton, claim_table, [])

    assert not _item(checklist, "unsupported-not-main-results").passed


def test_checklist_detects_synthetic_real_world_label_inflation() -> None:
    skeleton = _skeleton(
        label=VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
        text="[SyntheticExperimentVerified claim placeholder: real-world validation]",
        evidence_ids=["fake-synthetic-experiment-candidate-a"],
    )
    claim_table = _claim_table(
        label=VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
        evidence_ids=["fake-synthetic-experiment-candidate-a"],
    )

    checklist = build_manuscript_checklist(skeleton, claim_table, [])

    assert not _item(checklist, "synthetic-not-real-world").passed


def test_checklist_detects_latex_artifact_evidence() -> None:
    skeleton = _skeleton(
        evidence_links=[
            ClaimEvidenceLink(
                claim_id="claim-a",
                artifact_id="paper",
                artifact_type=ArtifactType.LATEX,
                evidence_role="fake_proof",
                supports_label=False,
            )
        ]
    )

    checklist = build_manuscript_checklist(skeleton, _claim_table(), [])

    assert not _item(checklist, "evidence-no-latex").passed
    assert _item(checklist, "evidence-no-latex").category == ChecklistCategory.EVIDENCE_BOUNDARY


def _skeleton(
    *,
    label: VerificationLabel = VerificationLabel.LEAN_VERIFIED,
    section: str = "Theory",
    text: str = (
        "[LeanVerified claim placeholder: claim_id=claim-a "
        "evidence=fake-proof-candidate-a]"
    ),
    evidence_ids: list[str] | None = None,
    evidence_links: list[ClaimEvidenceLink] | None = None,
) -> DraftSkeleton:
    evidence_ids = ["fake-proof-candidate-a"] if evidence_ids is None else evidence_ids
    evidence_links = (
        [
            ClaimEvidenceLink(
                claim_id="claim-a",
                artifact_id=evidence_ids[0],
                artifact_type=ArtifactType.LEAN,
                evidence_role="fake_proof",
                supports_label=True,
            )
        ]
        if evidence_links is None and evidence_ids
        else evidence_links or []
    )
    return DraftSkeleton(
        skeleton_id="draft-final",
        title="Draft",
        abstract_stub="Abstract stub",
        section_stubs=[
            DraftSection(
                section_id="theory",
                section_title=section,
                section_purpose="State claim placeholders.",
                allowed_claim_ids=["claim-a"],
                required_evidence_ids=evidence_ids,
                paragraph_placeholders=["[Paragraph placeholder]"],
            )
        ],
        claim_placeholders=[
            DraftClaimPlaceholder(
                claim_id="claim-a",
                candidate_id="candidate-a",
                claim_label=label,
                placeholder_text=text,
                evidence_artifact_ids=evidence_ids,
                allowed_section=section,
            )
        ],
        evidence_links=evidence_links,
    )


def _claim_table(
    *,
    label: VerificationLabel = VerificationLabel.LEAN_VERIFIED,
    evidence_ids: list[str] | None = None,
) -> ClaimTable:
    evidence_ids = ["fake-proof-candidate-a"] if evidence_ids is None else evidence_ids
    return ClaimTable(
        final_nucleus_id="final-candidate-a",
        claims=[
            Claim(
                claim_id="claim-a",
                claim_text="Candidate claim.",
                claim_label=label,
                candidate_id="candidate-a",
                evidence_artifact_ids=evidence_ids,
                evidence_types=["lean"] if evidence_ids else [],
                allowed_in_main_text=True,
                allowed_section="Theory",
                reason="test claim",
            )
        ],
    )


def _item(checklist, item_id: str):
    return next(item for item in checklist.items if item.id == item_id)
