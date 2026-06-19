from __future__ import annotations

import pytest
from typer.testing import CliRunner

from factori.abstract_synthesis import run_abstract_synthesis
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.draft_skeleton import (
    DraftSkeletonError,
    build_draft_skeleton,
    load_manuscript_planning_artifacts,
    run_draft_skeleton_generation,
)
from factori.ledger import ResearchLedger
from factori.manuscript_plan import run_manuscript_planning
from factori.schemas import (
    BlockedClaim,
    Claim,
    ClaimTable,
    ConstraintSet,
    ControllerActionType,
    FinalNucleusType,
    ManuscriptPlan,
    ManuscriptSectionPlan,
    VerificationLabel,
)
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_selection import run_stage_c_selection


def test_build_draft_skeleton_errors_clearly_without_manuscript_planning(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    with pytest.raises(DraftSkeletonError, match="Manuscript planning artifacts not found"):
        load_manuscript_planning_artifacts("run-1", ledger)


def test_manuscript_plan_claim_table_and_blocked_claims_are_loaded(tmp_path) -> None:
    _, ledger = _run_pipeline(tmp_path)

    manuscript_plan, claim_table, blocked_claims = load_manuscript_planning_artifacts(
        "run-1",
        ledger,
    )

    assert manuscript_plan.sections
    assert claim_table.claims
    assert blocked_claims == []


def test_draft_skeleton_generation_is_deterministic(tmp_path) -> None:
    _, ledger = _run_pipeline(tmp_path)
    manuscript_plan, claim_table, blocked_claims = load_manuscript_planning_artifacts(
        "run-1",
        ledger,
    )

    first = build_draft_skeleton(manuscript_plan, claim_table, blocked_claims)
    second = build_draft_skeleton(manuscript_plan, claim_table, blocked_claims)

    assert first == second


def test_section_stubs_follow_abstract_nucleus_outline() -> None:
    skeleton = build_draft_skeleton(
        _manuscript_plan(FinalNucleusType.ABSTRACT_NUCLEUS),
        _claim_table(),
        [],
    )
    section_titles = [section.section_title for section in skeleton.section_stubs]

    assert "General Model" in section_titles
    assert "Instantiations / Special Cases" in section_titles


def test_section_stubs_follow_branch_nucleus_outline() -> None:
    skeleton = build_draft_skeleton(
        _manuscript_plan(FinalNucleusType.BRANCH_NUCLEUS),
        _claim_table(),
        [],
    )
    section_titles = [section.section_title for section in skeleton.section_stubs]

    assert "Problem Setup" in section_titles
    assert "Method" in section_titles
    assert "General Model" not in section_titles


def test_allowed_claim_placeholders_preserve_labels() -> None:
    claim_table = ClaimTable(
        final_nucleus_id="final-candidate-a",
        claims=[
            _claim("claim-lean", VerificationLabel.LEAN_VERIFIED, "Theory"),
            _claim(
                "claim-synthetic",
                VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
                "Synthetic Experiments",
                evidence_ids=["fake-synthetic-experiment-candidate-a"],
                evidence_types=["experiment"],
            ),
        ],
    )
    plan = _manuscript_plan(
        FinalNucleusType.BRANCH_NUCLEUS,
        allowed_claim_ids=["claim-lean", "claim-synthetic"],
    )

    skeleton = build_draft_skeleton(plan, claim_table, [])
    labels = {
        placeholder.claim_id: placeholder.claim_label
        for placeholder in skeleton.claim_placeholders
    }

    assert labels["claim-lean"] == VerificationLabel.LEAN_VERIFIED
    assert labels["claim-synthetic"] == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
    assert "real-world" not in skeleton.claim_placeholders[1].placeholder_text.lower()


def test_blocked_and_unsupported_claims_do_not_appear_in_main_results() -> None:
    blocked = [
        BlockedClaim(
            claim_id="claim-unsupported",
            candidate_id="candidate-a",
            claim_text="Unsupported result.",
            claim_label=VerificationLabel.UNSUPPORTED,
            blocked_reason="unsupported claim is blocked from the manuscript body",
            suggested_section="Future Work",
        )
    ]
    plan = _manuscript_plan(
        FinalNucleusType.BRANCH_NUCLEUS,
        allowed_claim_ids=["claim-lean"],
        blocked_claim_ids=["claim-unsupported"],
    )
    claim_table = ClaimTable(
        final_nucleus_id="final-candidate-a",
        claims=[
            _claim("claim-lean", VerificationLabel.LEAN_VERIFIED, "Theory"),
            _claim("claim-unsupported", VerificationLabel.UNSUPPORTED, "Results"),
        ],
    )

    skeleton = build_draft_skeleton(plan, claim_table, blocked)

    placeholder_ids = {placeholder.claim_id for placeholder in skeleton.claim_placeholders}
    result_claim_ids = {
        claim_id
        for section in skeleton.section_stubs
        if section.section_title == "Results"
        for claim_id in section.allowed_claim_ids
    }
    assert "claim-unsupported" not in placeholder_ids
    assert "claim-unsupported" not in result_claim_ids
    assert any("claim-unsupported" in warning for warning in skeleton.blocked_claim_warnings)


def test_run_draft_skeleton_writes_ledgered_artifacts(tmp_path) -> None:
    store, ledger = _run_pipeline(tmp_path)

    result = run_draft_skeleton_generation(run_id="run-1", store=store, ledger=ledger)

    assert result.draft_skeleton.section_stubs
    assert result.draft_skeleton.claim_placeholders
    assert result.checklist.items
    commits = ledger.list_commits("run-1")
    action_types = [commit.action_type for commit in commits]
    for action_type in [
        ControllerActionType.DRAFT_SKELETON_STARTED,
        ControllerActionType.DRAFT_SKELETON_BUILT,
        ControllerActionType.MANUSCRIPT_CHECKLIST_BUILT,
        ControllerActionType.DRAFT_SKELETON_REPORT_WRITTEN,
        ControllerActionType.MANUSCRIPT_CHECKLIST_REPORT_WRITTEN,
    ]:
        assert action_type in action_types
    artifacts = [
        result.draft_json_artifact,
        result.draft_markdown_artifact,
        result.checklist_json_artifact,
        result.checklist_markdown_artifact,
    ]
    assert all(len(artifact.content_hash) == 64 for artifact in artifacts)
    assert all(artifact.producing_commit_hash for artifact in artifacts)
    assert (tmp_path / result.draft_markdown_artifact.path).is_file()


def test_cli_build_draft_skeleton_works_after_full_flow(tmp_path) -> None:
    runner = CliRunner()
    stage_a = runner.invoke(
        app,
        [
            "run-stage-a",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--domain",
            "human geography",
        ],
    )
    stage_b = runner.invoke(app, ["run-stage-b", "--root", str(tmp_path), "--run-id", "run-1"])
    select = runner.invoke(app, ["select-stage-c", "--root", str(tmp_path), "--run-id", "run-1"])
    stage_c = runner.invoke(app, ["run-stage-c", "--root", str(tmp_path), "--run-id", "run-1"])
    synthesize = runner.invoke(
        app,
        ["synthesize-abstract", "--root", str(tmp_path), "--run-id", "run-1"],
    )
    plan = runner.invoke(app, ["plan-manuscript", "--root", str(tmp_path), "--run-id", "run-1"])
    draft = runner.invoke(
        app,
        ["build-draft-skeleton", "--root", str(tmp_path), "--run-id", "run-1"],
    )

    assert stage_a.exit_code == 0
    assert stage_b.exit_code == 0
    assert select.exit_code == 0
    assert stage_c.exit_code == 0
    assert synthesize.exit_code == 0
    assert plan.exit_code == 0
    assert draft.exit_code == 0
    assert "sections=10" in draft.output
    assert "claim_placeholders=1" in draft.output
    assert "draft_skeleton=runs/run-1/reports/draft-skeleton.md" in draft.output
    assert "manuscript_checklist=runs/run-1/reports/manuscript-checklist.md" in draft.output


def test_cli_build_draft_skeleton_errors_without_manuscript_planning(tmp_path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["build-draft-skeleton", "--root", str(tmp_path), "--run-id", "run-1"],
    )

    assert result.exit_code == 1
    assert "Manuscript planning artifacts not found" in result.stderr


def _run_pipeline(tmp_path) -> tuple[ArtifactStore, ResearchLedger]:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )
    run_stage_b(run_id="run-1", store=store, ledger=ledger)
    run_stage_c_selection(run_id="run-1", store=store, ledger=ledger)
    run_stage_c(run_id="run-1", store=store, ledger=ledger)
    run_abstract_synthesis(run_id="run-1", store=store, ledger=ledger)
    run_manuscript_planning(run_id="run-1", store=store, ledger=ledger)
    return store, ledger


def _manuscript_plan(
    nucleus_type: FinalNucleusType,
    *,
    allowed_claim_ids: list[str] | None = None,
    blocked_claim_ids: list[str] | None = None,
) -> ManuscriptPlan:
    if nucleus_type == FinalNucleusType.ABSTRACT_NUCLEUS:
        section_titles = [
            "Title",
            "Abstract",
            "Introduction",
            "General Model",
            "Instantiations / Special Cases",
            "Appendix",
        ]
    else:
        section_titles = ["Title", "Abstract", "Introduction", "Problem Setup", "Method", "Results"]
    allowed_claim_ids = allowed_claim_ids or ["claim-lean"]
    sections = [
        ManuscriptSectionPlan(
            section_id=title.lower().replace(" ", "-"),
            title=title,
            bullets=[f"Purpose for {title}"],
            allowed_claim_ids=allowed_claim_ids if title in {"Results", "General Model"} else [],
        )
        for title in section_titles
    ]
    return ManuscriptPlan(
        plan_id="manuscript-plan-final",
        final_nucleus_id="final-candidate-a",
        nucleus_type=nucleus_type,
        title="Test Manuscript Plan",
        sections=sections,
        allowed_claim_ids=allowed_claim_ids,
        blocked_claim_ids=blocked_claim_ids or [],
    )


def _claim_table() -> ClaimTable:
    return ClaimTable(
        final_nucleus_id="final-candidate-a",
        claims=[_claim("claim-lean", VerificationLabel.LEAN_VERIFIED, "Theory")],
    )


def _claim(
    claim_id: str,
    label: VerificationLabel,
    section: str,
    *,
    evidence_ids: list[str] | None = None,
    evidence_types: list[str] | None = None,
) -> Claim:
    evidence_ids = ["fake-proof-candidate-a"] if evidence_ids is None else evidence_ids
    evidence_types = ["lean"] if evidence_types is None else evidence_types
    return Claim(
        claim_id=claim_id,
        claim_text="Candidate claim.",
        claim_label=label,
        candidate_id="candidate-a",
        evidence_artifact_ids=evidence_ids,
        evidence_types=evidence_types,
        allowed_in_main_text=section != "Future Work",
        allowed_section=section,
        reason="test claim",
    )
