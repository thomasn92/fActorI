"""Deterministic manuscript checklist generation."""

from __future__ import annotations

from factori.schemas import (
    ArtifactType,
    BlockedClaim,
    ChecklistCategory,
    ChecklistItem,
    ClaimTable,
    DraftSkeleton,
    ManuscriptChecklist,
    VerificationLabel,
)

EVIDENCE_REQUIRED_LABELS = {
    VerificationLabel.LEAN_VERIFIED,
    VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
}

MAIN_RESULT_SECTIONS = {
    "Abstract",
    "Results",
    "Theory",
    "Synthetic Experiments",
    "Theory or Synthetic Experiments",
}


def build_manuscript_checklist(
    draft_skeleton: DraftSkeleton,
    claim_table: ClaimTable,
    blocked_claims: list[BlockedClaim],
) -> ManuscriptChecklist:
    """Build deterministic pass/fail checklist items for a draft skeleton."""
    items = [
        _item(
            "evidence-main-claims",
            ChecklistCategory.EVIDENCE_BOUNDARY,
            "All main claims have evidence links.",
            _all_main_claims_have_evidence(draft_skeleton),
        ),
        _item(
            "evidence-no-latex",
            ChecklistCategory.EVIDENCE_BOUNDARY,
            "No LaTeX artifact is used as evidence.",
            not any(
                link.artifact_type == ArtifactType.LATEX
                for link in draft_skeleton.evidence_links
            ),
        ),
        _item(
            "claim-labels-present",
            ChecklistCategory.CLAIM_LABELS,
            "Every claim has a label.",
            all(claim.claim_label for claim in claim_table.claims),
        ),
        _item(
            "claim-labels-preserved",
            ChecklistCategory.CLAIM_LABELS,
            "Every allowed claim placeholder preserves its claim-table label.",
            _labels_are_preserved(draft_skeleton, claim_table),
        ),
        _item(
            "synthetic-not-real-world",
            ChecklistCategory.SYNTHETIC_DATA_BOUNDARY,
            "No synthetic result is described as real-world validation.",
            not _has_synthetic_real_world_inflation(draft_skeleton),
        ),
        _item(
            "blocked-claims-listed",
            ChecklistCategory.BLOCKED_CLAIMS,
            "Blocked claims are listed.",
            _blocked_claims_are_listed(draft_skeleton, blocked_claims),
        ),
        _item(
            "unsupported-not-main-results",
            ChecklistCategory.BLOCKED_CLAIMS,
            "No unsupported claim appears in main results.",
            not _has_unsupported_main_claim(draft_skeleton),
        ),
        _item(
            "section-purposes-present",
            ChecklistCategory.SECTION_COMPLETENESS,
            "Every section has at least one purpose statement.",
            all(section.section_purpose.strip() for section in draft_skeleton.section_stubs),
        ),
        _item(
            "section-placeholders-present",
            ChecklistCategory.SECTION_COMPLETENESS,
            "Every section has paragraph placeholders.",
            all(section.paragraph_placeholders for section in draft_skeleton.section_stubs),
        ),
        _item(
            "deterministic-scaffold",
            ChecklistCategory.REPRODUCIBILITY,
            "Draft skeleton is deterministic and marked as fake MVP output.",
            draft_skeleton.fake,
        ),
        _item(
            "evidence-links-reference-artifacts",
            ChecklistCategory.LEDGER_LINKS,
            "Evidence links reference artifact IDs.",
            all(link.artifact_id for link in draft_skeleton.evidence_links),
        ),
        _item(
            "evidence-artifact-ids-present",
            ChecklistCategory.ARTIFACT_HASHES,
            "Every evidence artifact reference has a stable artifact ID.",
            _evidence_ids_are_present(draft_skeleton),
        ),
        _item(
            "final-synthesis-ready",
            ChecklistCategory.FINAL_SYNTHESIS_READINESS,
            "Draft skeleton is ready for a later deterministic drafting pass.",
            _final_synthesis_ready(draft_skeleton),
        ),
    ]
    failures_count = sum(1 for item in items if not item.passed)
    return ManuscriptChecklist(
        checklist_id=f"checklist-{draft_skeleton.skeleton_id}",
        items=items,
        failures_count=failures_count,
    )


def _item(
    item_id: str,
    category: ChecklistCategory,
    description: str,
    passed: bool,
) -> ChecklistItem:
    return ChecklistItem(
        id=item_id,
        category=category,
        description=description,
        passed=passed,
        reason="passed deterministic check" if passed else "failed deterministic check",
    )


def _all_main_claims_have_evidence(draft_skeleton: DraftSkeleton) -> bool:
    for placeholder in draft_skeleton.claim_placeholders:
        if (
            placeholder.claim_label in EVIDENCE_REQUIRED_LABELS
            and not placeholder.evidence_artifact_ids
        ):
            return False
    return True


def _labels_are_preserved(
    draft_skeleton: DraftSkeleton,
    claim_table: ClaimTable,
) -> bool:
    labels_by_claim_id = {claim.claim_id: claim.claim_label for claim in claim_table.claims}
    return all(
        labels_by_claim_id.get(placeholder.claim_id) == placeholder.claim_label
        for placeholder in draft_skeleton.claim_placeholders
    )


def _has_synthetic_real_world_inflation(draft_skeleton: DraftSkeleton) -> bool:
    for placeholder in draft_skeleton.claim_placeholders:
        if placeholder.claim_label != VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED:
            continue
        text = placeholder.placeholder_text.lower()
        if "real-world" in text or "real world" in text or "field deployment" in text:
            return True
    return False


def _blocked_claims_are_listed(
    draft_skeleton: DraftSkeleton,
    blocked_claims: list[BlockedClaim],
) -> bool:
    if not blocked_claims:
        return True
    warnings = "\n".join(draft_skeleton.blocked_claim_warnings)
    return all(blocked.claim_id in warnings for blocked in blocked_claims)


def _has_unsupported_main_claim(draft_skeleton: DraftSkeleton) -> bool:
    return any(
        placeholder.claim_label == VerificationLabel.UNSUPPORTED
        and placeholder.allowed_section in MAIN_RESULT_SECTIONS
        for placeholder in draft_skeleton.claim_placeholders
    )


def _evidence_ids_are_present(draft_skeleton: DraftSkeleton) -> bool:
    for placeholder in draft_skeleton.claim_placeholders:
        if (
            placeholder.claim_label in EVIDENCE_REQUIRED_LABELS
            and not all(evidence_id for evidence_id in placeholder.evidence_artifact_ids)
        ):
            return False
    return True


def _final_synthesis_ready(draft_skeleton: DraftSkeleton) -> bool:
    critical_failures = [
        not _all_main_claims_have_evidence(draft_skeleton),
        _has_synthetic_real_world_inflation(draft_skeleton),
        _has_unsupported_main_claim(draft_skeleton),
        any(link.artifact_type == ArtifactType.LATEX for link in draft_skeleton.evidence_links),
    ]
    return not any(critical_failures)
