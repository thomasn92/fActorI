from __future__ import annotations

import pytest
from typer.testing import CliRunner

from factori.abstract_synthesis import run_abstract_synthesis
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.ledger import ResearchLedger
from factori.manuscript_plan import (
    ManuscriptPlanError,
    build_manuscript_plan,
    identify_blocked_claims,
    load_final_nucleus,
    run_manuscript_planning,
)
from factori.schemas import (
    Claim,
    ClaimTable,
    ConstraintSet,
    ControllerActionType,
    FinalNucleus,
    FinalNucleusType,
    VerificationLabel,
)
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_selection import run_stage_c_selection


def test_plan_manuscript_errors_clearly_if_abstract_synthesis_has_not_run(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    with pytest.raises(ManuscriptPlanError, match="Final nucleus not found"):
        load_final_nucleus("run-1", ledger)


def test_final_nucleus_is_loaded(tmp_path) -> None:
    _, ledger = _run_pipeline(tmp_path, max_stage_c_candidates=1)

    final_nucleus = load_final_nucleus("run-1", ledger)

    assert final_nucleus.nucleus_type == FinalNucleusType.BRANCH_NUCLEUS
    assert final_nucleus.supporting_candidate_ids


def test_abstract_nucleus_produces_general_model_outline() -> None:
    final_nucleus = FinalNucleus(
        id="final-abstract",
        nucleus_type=FinalNucleusType.ABSTRACT_NUCLEUS,
        supporting_candidate_ids=["candidate-a"],
        labels_by_candidate={"candidate-a": VerificationLabel.LEAN_VERIFIED},
        reason="test abstract nucleus",
    )
    plan = build_manuscript_plan(final_nucleus, _claim_table())

    titles = [section.title for section in plan.sections]

    assert "Method and Model" in titles
    assert "Claim and Evidence Boundaries" in titles
    assert "Deterministic" not in plan.title
    assert "Manuscript Plan" not in plan.title


def test_branch_nucleus_produces_focused_branch_outline() -> None:
    final_nucleus = _branch_nucleus()

    plan = build_manuscript_plan(final_nucleus, _claim_table())
    titles = [section.title for section in plan.sections]

    assert "Introduction and Problem Framing" in titles
    assert "Method and Model" in titles
    assert "Claim and Evidence Boundaries" in titles
    assert len(titles) == 7
    assert "General Model" not in titles


def test_section_plans_include_allowed_claim_ids_only() -> None:
    final_nucleus = _branch_nucleus()
    claim_table = ClaimTable(
        final_nucleus_id=final_nucleus.id,
        claims=[
            _claim("claim-ok", VerificationLabel.LEAN_VERIFIED, "Theory", ["lean"]),
            _claim(
                "claim-bad",
                VerificationLabel.CONJECTURE,
                "Results",
                [],
                text="Theorem: this conjecture is proven.",
            ),
        ],
    )

    plan = build_manuscript_plan(final_nucleus, claim_table)
    section_claim_ids = {
        claim_id
        for section in plan.sections
        for claim_id in section.allowed_claim_ids
    }

    assert "claim-ok" in section_claim_ids
    assert "claim-bad" not in section_claim_ids
    assert "claim-bad" in plan.blocked_claim_ids


def test_quality_aware_plan_avoids_placeholder_title() -> None:
    final_nucleus = _branch_nucleus()
    claim_table = ClaimTable(
        final_nucleus_id=final_nucleus.id,
        claims=[
            _claim(
                "claim-ok",
                VerificationLabel.CONJECTURE,
                "Theory",
                [],
                text=(
                    "For the selected branch, candidate candidate-a remains a conjecture: "
                    "How can transport costs bound neighborhood flows?"
                ),
            )
        ],
    )

    plan = build_manuscript_plan(final_nucleus, claim_table)

    assert plan.title == "Bounded Study of Transport Costs Bound Neighborhood Flows?"
    assert plan.title not in {
        "Deterministic Branch Manuscript Plan",
        "Untitled",
        "Placeholder",
        "Draft",
        "Paper",
    }


def test_quality_aware_plan_reduces_section_count_for_no_evidence_drafts() -> None:
    plan = build_manuscript_plan(_branch_nucleus(), _claim_table())
    titles = [section.title for section in plan.sections]

    assert len(titles) == 7
    assert "Empirical Results" not in titles
    assert "Bibliography" not in titles
    assert "Demonstration Status" in titles


def test_blocked_claims_are_deterministic_and_cover_required_failure_modes() -> None:
    claim_table = ClaimTable(
        final_nucleus_id="final-branch",
        claims=[
            _claim(
                "claim-real-world",
                VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
                "Synthetic Experiments",
                ["experiment"],
                text="Synthetic evidence proves real-world performance.",
            ),
            _claim(
                "claim-conjecture",
                VerificationLabel.CONJECTURE,
                "Results",
                [],
                text="Theorem: this conjecture is proven.",
            ),
            _claim("claim-unsupported", VerificationLabel.UNSUPPORTED, "Results", []),
            _claim(
                "claim-negative",
                VerificationLabel.NEGATIVE_RESULT,
                "Results",
                [],
                text="This is positive evidence.",
            ),
            _claim("claim-latex", VerificationLabel.LEAN_VERIFIED, "Theory", ["latex"]),
        ],
    )

    first = identify_blocked_claims(claim_table)
    second = identify_blocked_claims(claim_table)
    reasons = [claim.blocked_reason for claim in first]

    assert first == second
    assert any("real-world" in reason for reason in reasons)
    assert any("conjecture" in reason for reason in reasons)
    assert any("unsupported" in reason for reason in reasons)
    assert any("negative" in reason for reason in reasons)
    assert any("LaTeX" in reason for reason in reasons)


def test_run_manuscript_planning_writes_ledgered_artifacts(tmp_path) -> None:
    store, ledger = _run_pipeline(tmp_path, max_stage_c_candidates=1)

    result = run_manuscript_planning(run_id="run-1", store=store, ledger=ledger)

    assert result.claim_table.claims
    assert result.manuscript_plan.allowed_claim_ids
    commits = ledger.list_commits("run-1")
    action_types = [commit.action_type for commit in commits]
    for action_type in [
        ControllerActionType.MANUSCRIPT_PLANNING_STARTED,
        ControllerActionType.CLAIM_TABLE_BUILT,
        ControllerActionType.BLOCKED_CLAIMS_IDENTIFIED,
        ControllerActionType.MANUSCRIPT_PLAN_BUILT,
        ControllerActionType.MANUSCRIPT_PLAN_REPORT_WRITTEN,
    ]:
        assert action_type in action_types
    artifacts = [
        result.claim_table_artifact,
        result.blocked_claims_artifact,
        result.manuscript_plan_artifact,
        result.markdown_artifact,
    ]
    assert all(len(artifact.content_hash) == 64 for artifact in artifacts)
    assert all(artifact.producing_commit_hash for artifact in artifacts)
    assert (tmp_path / result.markdown_artifact.path).is_file()


def test_cli_plan_manuscript_works_after_abstract_synthesis(tmp_path) -> None:
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
    select = runner.invoke(
        app,
        ["select-stage-c", "--root", str(tmp_path), "--run-id", "run-1"],
    )
    stage_c = runner.invoke(app, ["run-stage-c", "--root", str(tmp_path), "--run-id", "run-1"])
    synthesize = runner.invoke(
        app,
        ["synthesize-abstract", "--root", str(tmp_path), "--run-id", "run-1"],
    )
    plan = runner.invoke(app, ["plan-manuscript", "--root", str(tmp_path), "--run-id", "run-1"])

    assert stage_a.exit_code == 0
    assert stage_b.exit_code == 0
    assert select.exit_code == 0
    assert stage_c.exit_code == 0
    assert synthesize.exit_code == 0
    assert plan.exit_code == 0
    assert "final_nucleus_type=BranchNucleus" in plan.output
    assert "claims_total=1" in plan.output
    assert "manuscript_plan=runs/run-1/reports/manuscript-plan.md" in plan.output
    assert "claim_table=runs/run-1/reports/claim-table.json" in plan.output


def test_cli_plan_manuscript_errors_without_abstract_synthesis(tmp_path) -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["plan-manuscript", "--root", str(tmp_path), "--run-id", "run-1"])

    assert result.exit_code == 1
    assert "Final nucleus not found" in result.stderr


def _run_pipeline(
    tmp_path,
    *,
    max_stage_c_candidates: int,
) -> tuple[ArtifactStore, ResearchLedger]:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")
    run_stage_a(
        run_id="run-1",
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )
    run_stage_b(run_id="run-1", store=store, ledger=ledger)
    run_stage_c_selection(
        run_id="run-1",
        store=store,
        ledger=ledger,
        max_stage_c_candidates=max_stage_c_candidates,
    )
    run_stage_c(run_id="run-1", store=store, ledger=ledger)
    run_abstract_synthesis(run_id="run-1", store=store, ledger=ledger)
    return store, ledger


def _branch_nucleus() -> FinalNucleus:
    return FinalNucleus(
        id="final-candidate-a",
        nucleus_type=FinalNucleusType.BRANCH_NUCLEUS,
        candidate_id="candidate-a",
        supporting_candidate_ids=["candidate-a"],
        labels_by_candidate={"candidate-a": VerificationLabel.LEAN_VERIFIED},
        reason="test branch nucleus",
    )


def _claim_table() -> ClaimTable:
    return ClaimTable(
        final_nucleus_id="final-candidate-a",
        claims=[_claim("claim-ok", VerificationLabel.LEAN_VERIFIED, "Theory", ["lean"])],
    )


def _claim(
    claim_id: str,
    label: VerificationLabel,
    section: str,
    evidence_types: list[str],
    *,
    text: str = "Candidate claim is explicitly labeled.",
) -> Claim:
    return Claim(
        claim_id=claim_id,
        claim_text=text,
        claim_label=label,
        candidate_id="candidate-a",
        evidence_artifact_ids=[],
        evidence_types=evidence_types,
        allowed_in_main_text=section != "Future Work",
        allowed_section=section,
        reason="test claim",
    )
