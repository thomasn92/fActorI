from __future__ import annotations

from factori.prose_contract import build_prose_generation_contract
from factori.schemas import (
    AuditCategory,
    AuditCheck,
    AuditCheckStatus,
    AuditSeverity,
    Claim,
    ClaimTable,
    DraftClaimPlaceholder,
    FinalAuditReport,
    PaperAppendix,
    PaperSection,
    PaperSkeleton,
    ReleaseGateDecision,
    ReleaseGateStatus,
    VerificationLabel,
)


def test_prose_generation_contract_is_deterministic() -> None:
    first = build_prose_generation_contract(
        "run-1",
        _paper_skeleton(),
        _claim_table(),
        _audit_report(),
        _release_decision(),
    )
    second = build_prose_generation_contract(
        "run-1",
        _paper_skeleton(),
        _claim_table(),
        _audit_report(),
        _release_decision(),
    )

    assert first == second


def test_forbidden_label_inflation_transformations_are_present() -> None:
    contract = build_prose_generation_contract(
        "run-1",
        _paper_skeleton(),
        _claim_table(
            [
                _claim("claim-conjecture", VerificationLabel.CONJECTURE),
                _claim("claim-synthetic", VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED),
                _claim("claim-negative", VerificationLabel.NEGATIVE_RESULT),
            ]
        ),
        _audit_report(),
        _release_decision(),
    )

    assert "upgrade Conjecture to theorem" in contract.forbidden_transformations
    assert (
        "upgrade SyntheticExperimentVerified to real-world validation"
        in contract.forbidden_transformations
    )
    assert "upgrade NegativeResult to positive evidence" in contract.forbidden_transformations


def test_blocked_claims_are_excluded_from_allowed_claims_and_listed() -> None:
    paper = _paper_skeleton(blocked_claim_ids=["claim-blocked"])
    contract = build_prose_generation_contract(
        "run-1",
        paper,
        _claim_table([_claim("claim-main", VerificationLabel.LEAN_VERIFIED)]),
        _audit_report(),
        _release_decision(),
    )

    assert "claim-blocked" in contract.blocked_claims
    assert "claim-blocked" not in contract.allowed_claims


def test_every_exported_main_claim_has_evidence_links_in_contract() -> None:
    contract = build_prose_generation_contract(
        "run-1",
        _paper_skeleton(),
        _claim_table(),
        _audit_report(),
        _release_decision(),
    )

    assert all(
        contract.claim_evidence_links[claim_id]
        for claim_id in contract.allowed_claims
    )


def test_required_disclaimers_are_present_for_fake_validators_and_labels() -> None:
    contract = build_prose_generation_contract(
        "run-1",
        _paper_skeleton(),
        _claim_table(
            [
                _claim("claim-synthetic", VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED),
                _claim("claim-conjecture", VerificationLabel.CONJECTURE),
                _claim("claim-negative", VerificationLabel.NEGATIVE_RESULT),
            ]
        ),
        _audit_report(),
        _release_decision(),
    )

    assert any("fake deterministic validators" in item for item in contract.required_disclaimers)
    assert any("Synthetic evidence supports only" in item for item in contract.required_disclaimers)
    assert any("Conjectural statements" in item for item in contract.required_disclaimers)
    assert any("Negative results" in item for item in contract.required_disclaimers)


def test_contract_not_ready_when_release_gate_blocks() -> None:
    decision = _release_decision(ready=False, status=ReleaseGateStatus.RELEASE_BLOCKED)

    contract = build_prose_generation_contract(
        "run-1",
        _paper_skeleton(),
        _claim_table(),
        _audit_report(blocking=1),
        decision,
    )

    assert not contract.ready_for_polished_prose


def _paper_skeleton(blocked_claim_ids: list[str] | None = None) -> PaperSkeleton:
    placeholder = DraftClaimPlaceholder(
        claim_id="claim-main",
        candidate_id="candidate-a",
        claim_label=VerificationLabel.LEAN_VERIFIED,
        placeholder_text="[LeanVerified placeholder]",
        evidence_artifact_ids=["fake-proof-candidate-a"],
        allowed_section="Theory",
    )
    return PaperSkeleton(
        paper_id="paper",
        run_id="run-1",
        title="Paper",
        abstract_scaffold="Abstract.",
        sections=[
            PaperSection(
                section_id="theory",
                title="Theory or Synthetic Experiments",
                purpose="Use claim table.",
                claim_placeholders=[placeholder],
                evidence_artifact_ids=["fake-proof-candidate-a"],
            )
        ],
        appendices=[
            PaperAppendix(
                appendix_id="appendix-b",
                title="Appendix B: Blocked or Downgraded Claims",
                content_lines=[f"{claim_id}: blocked" for claim_id in blocked_claim_ids or []]
                or ["none"],
            )
        ],
        claim_placeholders=[placeholder],
        provenance_refs={},
    )


def _claim_table(claims: list[Claim] | None = None) -> ClaimTable:
    return ClaimTable(
        final_nucleus_id="final",
        claims=claims or [_claim("claim-main", VerificationLabel.LEAN_VERIFIED)],
    )


def _claim(claim_id: str, label: VerificationLabel) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim_text="Deterministic claim.",
        claim_label=label,
        candidate_id="candidate-a",
        evidence_artifact_ids=["fake-proof-candidate-a"],
        evidence_types=["lean"],
        allowed_in_main_text=True,
        allowed_section="Theory",
        reason="test",
    )


def _audit_report(blocking: int = 0) -> FinalAuditReport:
    check = AuditCheck(
        check_id="paper_required_sections",
        category=AuditCategory.PAPER_SKELETON_CONSISTENCY,
        status=AuditCheckStatus.PASS if blocking == 0 else AuditCheckStatus.FAIL,
        severity=AuditSeverity.INFO if blocking == 0 else AuditSeverity.BLOCKING,
        message="ok" if blocking == 0 else "blocking",
    )
    return FinalAuditReport(
        run_id="run-1",
        checks=[check],
        passes_count=0 if blocking else 1,
        warnings_count=0,
        failures_count=blocking,
        blocking_failures_count=blocking,
    )


def _release_decision(
    *,
    ready: bool = True,
    status: ReleaseGateStatus = ReleaseGateStatus.RELEASE_READY,
) -> ReleaseGateDecision:
    return ReleaseGateDecision(
        run_id="run-1",
        status=status,
        ready_for_polished_prose=ready,
        ready_for_latex_export=ready,
        ready_for_external_review=False,
        audit_checks=1,
    )
