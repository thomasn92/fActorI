from __future__ import annotations

from factori.latex_plan import build_latex_export_plan
from factori.schemas import (
    DraftClaimPlaceholder,
    LatexExportPlan,
    PaperAppendix,
    PaperSection,
    PaperSkeleton,
    ProseGenerationContract,
    VerificationLabel,
)


def test_latex_export_plan_is_deterministic() -> None:
    first = build_latex_export_plan("run-1", _paper_skeleton(), _contract())
    second = build_latex_export_plan("run-1", _paper_skeleton(), _contract())

    assert first == second


def test_latex_export_plan_lists_forbidden_commands() -> None:
    plan = build_latex_export_plan("run-1", _paper_skeleton(), _contract())

    assert "\\write18" in plan.forbidden_latex_commands
    assert "\\input from external absolute paths" in plan.forbidden_latex_commands
    assert "\\include from external absolute paths" in plan.forbidden_latex_commands
    assert "shell escape dependent commands" in plan.forbidden_latex_commands


def test_latex_export_plan_preserves_sections_claims_and_evidence_placeholders() -> None:
    plan = build_latex_export_plan("run-1", _paper_skeleton(), _contract())

    assert plan.section_order == ["Abstract", "Theory or Synthetic Experiments"]
    assert plan.section_ids == ["abstract", "theory"]
    assert plan.claim_placeholder_ids == ["claim-main"]
    assert plan.evidence_placeholder_ids == ["evidence-fake-proof-candidate-a"]
    assert plan.appendix_order == ["Appendix A: Claim/Evidence Table"]


def test_latex_export_plan_is_not_latex_source() -> None:
    plan = build_latex_export_plan("run-1", _paper_skeleton(), _contract())

    assert isinstance(plan, LatexExportPlan)
    assert plan.target_template_name == "factori-mvp-paper-skeleton"
    assert any("not LaTeX source" in warning for warning in plan.latex_safety_warnings)


def test_latex_export_plan_not_ready_if_prose_contract_not_ready() -> None:
    plan = build_latex_export_plan(
        "run-1",
        _paper_skeleton(),
        _contract(ready=False),
    )

    assert not plan.ready_for_latex_export


def _paper_skeleton() -> PaperSkeleton:
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
                section_id="abstract",
                title="Abstract",
                purpose="Summarize allowed claims.",
            ),
            PaperSection(
                section_id="theory",
                title="Theory or Synthetic Experiments",
                purpose="Use claim table.",
                claim_placeholders=[placeholder],
                evidence_artifact_ids=["fake-proof-candidate-a"],
            ),
        ],
        appendices=[
            PaperAppendix(
                appendix_id="appendix-a",
                title="Appendix A: Claim/Evidence Table",
                content_lines=["claim-main"],
            )
        ],
        claim_placeholders=[placeholder],
        provenance_refs={},
    )


def _contract(*, ready: bool = True) -> ProseGenerationContract:
    return ProseGenerationContract(
        run_id="run-1",
        allowed_sections=["Abstract", "Theory or Synthetic Experiments"],
        allowed_claims=["claim-main"],
        blocked_claims=[],
        claim_labels={"claim-main": VerificationLabel.LEAN_VERIFIED},
        claim_evidence_links={"claim-main": ["fake-proof-candidate-a"]},
        style_constraints=["preserve labels"],
        forbidden_transformations=["create new scientific claims"],
        required_disclaimers=["fake validators"],
        ready_for_polished_prose=ready,
    )
