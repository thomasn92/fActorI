from __future__ import annotations

import pytest
from typer.testing import CliRunner

from factori.abstract_synthesis import run_abstract_synthesis
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.draft_skeleton import build_draft_skeleton, run_draft_skeleton_generation
from factori.final_paper import (
    PaperAssemblyError,
    assemble_paper_skeleton,
    build_paper_assembly_report,
    load_paper_assembly_inputs,
    run_paper_assembly,
)
from factori.ledger import ResearchLedger
from factori.manuscript_plan import run_manuscript_planning
from factori.research_object import build_research_object
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BlockedClaim,
    Claim,
    ClaimTable,
    ConstraintSet,
    ControllerActionType,
    FinalNucleus,
    FinalNucleusType,
    ManuscriptPlan,
    ManuscriptSectionPlan,
    ResearchObject,
    VerificationLabel,
)
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_selection import run_stage_c_selection


def test_assemble_paper_skeleton_errors_without_research_object_packaging(tmp_path) -> None:
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    with pytest.raises(PaperAssemblyError, match="Research object artifacts not found"):
        load_paper_assembly_inputs("run-1", ledger)


def test_manuscript_plan_draft_claim_table_and_research_object_are_loaded(tmp_path) -> None:
    _, ledger = _run_pipeline_to_research_object(tmp_path)

    inputs = load_paper_assembly_inputs("run-1", ledger)

    assert inputs.manuscript_plan.sections
    assert inputs.draft_skeleton.section_stubs
    assert inputs.claim_table.claims
    assert inputs.research_object.final_nucleus.id


def test_paper_assembly_is_deterministic() -> None:
    plan, draft, claim_table, blocked, research_object = _manual_inputs()

    first = assemble_paper_skeleton("run-1", plan, draft, claim_table, research_object, blocked)
    second = assemble_paper_skeleton("run-1", plan, draft, claim_table, research_object, blocked)

    assert first == second


def test_paper_sections_follow_abstract_nucleus_structure() -> None:
    plan, draft, claim_table, blocked, research_object = _manual_inputs(
        FinalNucleusType.ABSTRACT_NUCLEUS
    )

    paper = assemble_paper_skeleton("run-1", plan, draft, claim_table, research_object, blocked)
    titles = [section.title for section in paper.sections]

    assert "General Model" in titles
    assert "Instantiations / Special Cases" in titles
    assert "Problem Setup" not in titles


def test_paper_sections_follow_branch_nucleus_structure() -> None:
    plan, draft, claim_table, blocked, research_object = _manual_inputs(
        FinalNucleusType.BRANCH_NUCLEUS
    )

    paper = assemble_paper_skeleton("run-1", plan, draft, claim_table, research_object, blocked)
    titles = [section.title for section in paper.sections]

    assert "Problem Setup" in titles
    assert "Method" in titles
    assert "General Model" not in titles


def test_no_new_scientific_claims_are_created() -> None:
    plan, draft, claim_table, blocked, research_object = _manual_inputs()

    paper = assemble_paper_skeleton("run-1", plan, draft, claim_table, research_object, blocked)
    source_claim_ids = {claim.claim_id for claim in claim_table.claims}
    paper_claim_ids = {placeholder.claim_id for placeholder in paper.claim_placeholders}

    assert paper_claim_ids <= source_claim_ids


def test_claim_labels_are_preserved() -> None:
    plan, draft, claim_table, blocked, research_object = _manual_inputs()

    paper = assemble_paper_skeleton("run-1", plan, draft, claim_table, research_object, blocked)
    labels = {
        placeholder.claim_id: placeholder.claim_label
        for placeholder in paper.claim_placeholders
    }

    assert labels["claim-lean"] == VerificationLabel.LEAN_VERIFIED


def test_conjectures_remain_conjectures() -> None:
    plan, draft, claim_table, blocked, research_object = _manual_inputs(
        extra_claims=[
            _claim(
                "claim-conjecture",
                VerificationLabel.CONJECTURE,
                "Theory",
                evidence_ids=["supporting-note"],
            )
        ],
        allowed_claim_ids=["claim-lean", "claim-conjecture"],
    )

    paper = assemble_paper_skeleton("run-1", plan, draft, claim_table, research_object, blocked)
    labels = {
        placeholder.claim_id: placeholder.claim_label
        for placeholder in paper.claim_placeholders
    }

    assert labels["claim-conjecture"] == VerificationLabel.CONJECTURE


def test_synthetic_evidence_remains_synthetic_only() -> None:
    synthetic = _claim(
        "claim-synthetic",
        VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED,
        "Synthetic Experiments",
        evidence_ids=["fake-synthetic-experiment-candidate-a"],
        evidence_types=["experiment"],
        text="Synthetic simulation validates the controlled generator.",
    )
    plan, draft, claim_table, blocked, research_object = _manual_inputs(
        extra_claims=[synthetic],
        allowed_claim_ids=["claim-synthetic"],
    )

    paper = assemble_paper_skeleton("run-1", plan, draft, claim_table, research_object, blocked)
    placeholder = paper.claim_placeholders[0]

    assert placeholder.claim_label == VerificationLabel.SYNTHETIC_EXPERIMENT_VERIFIED
    assert "real-world" not in placeholder.placeholder_text.lower()


def test_negative_results_remain_negative_or_boundary_labeled() -> None:
    negative = _claim(
        "claim-negative",
        VerificationLabel.NEGATIVE_RESULT,
        "Negative Results",
        evidence_ids=["negative-evidence"],
    )
    plan, draft, claim_table, blocked, research_object = _manual_inputs(
        FinalNucleusType.ABSTRACT_NUCLEUS,
        extra_claims=[negative],
        allowed_claim_ids=["claim-negative"],
    )

    paper = assemble_paper_skeleton("run-1", plan, draft, claim_table, research_object, blocked)
    labels = {
        placeholder.claim_id: placeholder.claim_label
        for placeholder in paper.claim_placeholders
    }

    assert labels["claim-negative"] == VerificationLabel.NEGATIVE_RESULT
    assert any(
        "Negative Results" in appendix.title or "Blocked" in appendix.title
        for appendix in paper.appendices
    )


def test_unsupported_claims_do_not_appear_in_main_results() -> None:
    unsupported = _claim(
        "claim-unsupported",
        VerificationLabel.UNSUPPORTED,
        "Results",
        evidence_ids=["unsupported-note"],
    )
    plan, draft, claim_table, blocked, research_object = _manual_inputs(
        extra_claims=[unsupported],
        allowed_claim_ids=["claim-unsupported"],
    )

    paper = assemble_paper_skeleton("run-1", plan, draft, claim_table, research_object, blocked)

    assert "claim-unsupported" not in {
        placeholder.claim_id for placeholder in paper.claim_placeholders
    }


def test_blocked_claims_appear_only_in_blocked_claims_appendix() -> None:
    blocked_claim = BlockedClaim(
        claim_id="claim-blocked",
        candidate_id="candidate-a",
        claim_text="Blocked claim.",
        claim_label=VerificationLabel.UNSUPPORTED,
        blocked_reason="unsupported main result",
        suggested_section="Future Work",
    )
    claim = _claim("claim-blocked", VerificationLabel.UNSUPPORTED, "Results")
    plan, draft, claim_table, _, research_object = _manual_inputs(
        extra_claims=[claim],
        allowed_claim_ids=["claim-blocked"],
        blocked_claims=[blocked_claim],
    )

    paper = assemble_paper_skeleton(
        "run-1",
        plan,
        draft,
        claim_table,
        research_object,
        [blocked_claim],
    )

    assert "claim-blocked" not in {
        placeholder.claim_id for placeholder in paper.claim_placeholders
    }
    blocked_appendix = next(
        appendix for appendix in paper.appendices if appendix.title.startswith("Appendix B")
    )
    assert any("claim-blocked" in line for line in blocked_appendix.content_lines)


def test_every_included_main_claim_has_evidence_links() -> None:
    plan, draft, claim_table, blocked, research_object = _manual_inputs()
    paper = assemble_paper_skeleton("run-1", plan, draft, claim_table, research_object, blocked)

    assert all(placeholder.evidence_artifact_ids for placeholder in paper.claim_placeholders)


def test_latex_and_markdown_are_not_treated_as_verification_evidence() -> None:
    claim = _claim(
        "claim-latex",
        VerificationLabel.LEAN_VERIFIED,
        "Theory",
        evidence_ids=["paper-latex"],
        evidence_types=["latex", "markdown"],
    )
    plan, draft, claim_table, blocked, research_object = _manual_inputs(
        extra_claims=[claim],
        allowed_claim_ids=["claim-latex"],
    )
    paper = assemble_paper_skeleton("run-1", plan, draft, claim_table, research_object, blocked)

    report = build_paper_assembly_report(
        paper_skeleton=paper,
        manuscript_plan=plan,
        draft_skeleton=draft,
        claim_table=claim_table,
        blocked_claims=blocked,
        research_object=research_object,
    )

    assert any("presentation artifact" in warning for warning in report.warnings)
    assert not report.ready_for_polished_prose


def test_provenance_appendix_includes_ledger_summary_reference() -> None:
    plan, draft, claim_table, blocked, research_object = _manual_inputs()

    paper = assemble_paper_skeleton("run-1", plan, draft, claim_table, research_object, blocked)
    appendix = next(
        item for item in paper.appendices if item.title.startswith("Appendix C")
    )

    assert any("ledger_summary_ref=" in line for line in appendix.content_lines)


def test_failed_deferred_pruned_branch_appendix_is_included() -> None:
    plan, draft, claim_table, blocked, research_object = _manual_inputs()

    paper = assemble_paper_skeleton("run-1", plan, draft, claim_table, research_object, blocked)

    assert any(
        appendix.title == "Appendix D: Failed, Deferred, and Pruned Branches"
        for appendix in paper.appendices
    )


def test_ready_for_polished_prose_false_when_evidence_links_missing() -> None:
    claim = _claim(
        "claim-missing-evidence",
        VerificationLabel.LEAN_VERIFIED,
        "Theory",
        evidence_ids=[],
    )
    plan, draft, claim_table, blocked, research_object = _manual_inputs(
        extra_claims=[claim],
        allowed_claim_ids=["claim-missing-evidence"],
    )
    paper = assemble_paper_skeleton("run-1", plan, draft, claim_table, research_object, blocked)

    report = build_paper_assembly_report(
        paper_skeleton=paper,
        manuscript_plan=plan,
        draft_skeleton=draft,
        claim_table=claim_table,
        blocked_claims=blocked,
        research_object=research_object,
    )

    assert not report.ready_for_polished_prose
    assert any("missing evidence" in warning for warning in report.warnings)


def test_ready_for_polished_prose_false_when_unsupported_main_claim_exists() -> None:
    unsupported = _claim("claim-unsupported", VerificationLabel.UNSUPPORTED, "Results")
    plan, draft, claim_table, blocked, research_object = _manual_inputs(
        extra_claims=[unsupported],
        allowed_claim_ids=["claim-unsupported"],
    )
    paper = assemble_paper_skeleton("run-1", plan, draft, claim_table, research_object, blocked)

    report = build_paper_assembly_report(
        paper_skeleton=paper,
        manuscript_plan=plan,
        draft_skeleton=draft,
        claim_table=claim_table,
        blocked_claims=blocked,
        research_object=research_object,
    )

    assert not report.ready_for_polished_prose
    assert any("unsupported claim" in warning for warning in report.warnings)


def test_ready_for_polished_prose_true_for_complete_deterministic_run(tmp_path) -> None:
    store, ledger = _run_pipeline_to_research_object(tmp_path)

    result = run_paper_assembly(run_id="run-1", store=store, ledger=ledger)

    assert result.assembly_report.ready_for_polished_prose


def test_paper_artifacts_are_created(tmp_path) -> None:
    store, ledger = _run_pipeline_to_research_object(tmp_path)

    result = run_paper_assembly(run_id="run-1", store=store, ledger=ledger)

    assert (tmp_path / result.paper_markdown_artifact.path).is_file()
    assert (tmp_path / result.paper_json_artifact.path).is_file()
    assert (tmp_path / result.assembly_report_artifact.path).is_file()


def test_every_paper_assembly_artifact_has_hash_and_commit(tmp_path) -> None:
    store, ledger = _run_pipeline_to_research_object(tmp_path)

    result = run_paper_assembly(run_id="run-1", store=store, ledger=ledger)
    artifacts = [
        result.paper_json_artifact,
        result.paper_markdown_artifact,
        result.assembly_report_artifact,
    ]

    assert all(len(artifact.content_hash) == 64 for artifact in artifacts)
    assert all(artifact.producing_commit_hash for artifact in artifacts)


def test_paper_assembly_creates_ledger_commits(tmp_path) -> None:
    store, ledger = _run_pipeline_to_research_object(tmp_path)

    run_paper_assembly(run_id="run-1", store=store, ledger=ledger)
    action_types = [commit.action_type for commit in ledger.list_commits("run-1")]

    assert ControllerActionType.PAPER_ASSEMBLY_STARTED in action_types
    assert ControllerActionType.PAPER_SKELETON_WRITTEN in action_types
    assert ControllerActionType.PAPER_ASSEMBLY_REPORT_WRITTEN in action_types


def test_cli_assemble_paper_skeleton_works_after_full_flow(tmp_path) -> None:
    runner = CliRunner()
    commands = [
        [
            "run-stage-a",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--domain",
            "human geography",
        ],
        ["run-stage-b", "--root", str(tmp_path), "--run-id", "run-1"],
        ["select-stage-c", "--root", str(tmp_path), "--run-id", "run-1"],
        ["run-stage-c", "--root", str(tmp_path), "--run-id", "run-1"],
        ["synthesize-abstract", "--root", str(tmp_path), "--run-id", "run-1"],
        ["plan-manuscript", "--root", str(tmp_path), "--run-id", "run-1"],
        ["build-draft-skeleton", "--root", str(tmp_path), "--run-id", "run-1"],
        ["package-research-object", "--root", str(tmp_path), "--run-id", "run-1"],
        ["assemble-paper-skeleton", "--root", str(tmp_path), "--run-id", "run-1"],
    ]
    results = [runner.invoke(app, command) for command in commands]

    assert all(result.exit_code == 0 for result in results)
    assert "ready_for_polished_prose=true" in results[-1].output
    assert "paper_skeleton=runs/run-1/research_object/paper-skeleton.md" in results[-1].output


def test_cli_assemble_paper_skeleton_errors_without_research_object(tmp_path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["assemble-paper-skeleton", "--root", str(tmp_path), "--run-id", "run-1"],
    )

    assert result.exit_code == 1
    assert "Research object artifacts not found" in result.stderr


def _run_pipeline_to_research_object(tmp_path) -> tuple[ArtifactStore, ResearchLedger]:
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
    run_draft_skeleton_generation(run_id="run-1", store=store, ledger=ledger)
    build_research_object(run_id="run-1", store=store, ledger=ledger)
    return store, ledger


def _manual_inputs(
    nucleus_type: FinalNucleusType = FinalNucleusType.BRANCH_NUCLEUS,
    *,
    extra_claims: list[Claim] | None = None,
    allowed_claim_ids: list[str] | None = None,
    blocked_claims: list[BlockedClaim] | None = None,
) -> tuple[ManuscriptPlan, object, ClaimTable, list[BlockedClaim], ResearchObject]:
    blocked_claims = blocked_claims or []
    claims = [
        _claim("claim-lean", VerificationLabel.LEAN_VERIFIED, "Theory"),
        *(extra_claims or []),
    ]
    allowed_claim_ids = allowed_claim_ids or ["claim-lean"]
    plan = _manuscript_plan(
        nucleus_type,
        allowed_claim_ids=allowed_claim_ids,
        blocked_claim_ids=[claim.claim_id for claim in blocked_claims],
    )
    claim_table = ClaimTable(final_nucleus_id="final-test", claims=claims)
    draft = build_draft_skeleton(plan, claim_table, blocked_claims)
    return plan, draft, claim_table, blocked_claims, _research_object(nucleus_type)


def _manuscript_plan(
    nucleus_type: FinalNucleusType,
    *,
    allowed_claim_ids: list[str],
    blocked_claim_ids: list[str],
) -> ManuscriptPlan:
    section_titles = (
        [
            "Title",
            "Abstract",
            "Introduction",
            "General Model",
            "Instantiations / Special Cases",
            "Theory or Synthetic Experiments",
            "Negative Results or Boundary Cases",
            "Limitations",
            "Conclusion",
            "Appendix",
        ]
        if nucleus_type == FinalNucleusType.ABSTRACT_NUCLEUS
        else [
            "Title",
            "Abstract",
            "Introduction",
            "Problem Setup",
            "Method",
            "Theory or Synthetic Experiments",
            "Results",
            "Limitations",
            "Conclusion",
            "Appendix",
        ]
    )
    sections = [
        ManuscriptSectionPlan(
            section_id=title.lower().replace(" ", "-").replace("/", "-"),
            title=title,
            bullets=[f"Purpose for {title}"],
            allowed_claim_ids=_section_claims(title, allowed_claim_ids),
        )
        for title in section_titles
    ]
    return ManuscriptPlan(
        plan_id="manuscript-plan-test",
        final_nucleus_id="final-test",
        nucleus_type=nucleus_type,
        title="Deterministic Paper Skeleton",
        sections=sections,
        allowed_claim_ids=allowed_claim_ids,
        blocked_claim_ids=blocked_claim_ids,
    )


def _section_claims(title: str, allowed_claim_ids: list[str]) -> list[str]:
    if title in {
        "Theory or Synthetic Experiments",
        "Results",
        "Negative Results or Boundary Cases",
        "Limitations",
    }:
        return allowed_claim_ids
    return []


def _research_object(nucleus_type: FinalNucleusType) -> ResearchObject:
    ref = _artifact_ref("placeholder", "runs/run-1/research_object/placeholder.json")
    final_nucleus = FinalNucleus(
        id="final-test",
        nucleus_type=nucleus_type,
        candidate_id="candidate-a",
        supporting_candidate_ids=["candidate-a"],
        labels_by_candidate={"candidate-a": VerificationLabel.LEAN_VERIFIED},
        reason="test final nucleus",
    )
    return ResearchObject(
        run_id="run-1",
        final_nucleus=final_nucleus,
        manuscript_plan_ref=_artifact_ref(
            "manuscript-plan",
            "runs/run-1/reports/manuscript-plan.json",
        ),
        draft_skeleton_ref=_artifact_ref(
            "draft-skeleton",
            "runs/run-1/reports/draft-skeleton.json",
        ),
        claim_table_ref=_artifact_ref("claim-table", "runs/run-1/reports/claim-table.json"),
        blocked_claims_ref=_artifact_ref(
            "blocked-claims",
            "runs/run-1/reports/blocked-claims.json",
        ),
        checklist_ref=_artifact_ref(
            "manuscript-checklist",
            "runs/run-1/reports/manuscript-checklist.json",
        ),
        stage_reports={"stage_a": ref},
        artifact_manifest_ref=_artifact_ref(
            "artifact-manifest",
            "runs/run-1/research_object/artifact-manifest.json",
        ),
        ledger_summary_ref=_artifact_ref(
            "ledger-summary",
            "runs/run-1/research_object/ledger-summary.json",
        ),
        branch_outcomes_ref=_artifact_ref(
            "branch-outcomes",
            "runs/run-1/research_object/branch-outcomes.json",
        ),
        reproducibility_manifest_ref=_artifact_ref(
            "reproducibility-manifest",
            "runs/run-1/research_object/reproducibility-manifest.json",
        ),
        created_at="1970-01-01T00:00:00.000000Z",
    )


def _claim(
    claim_id: str,
    label: VerificationLabel,
    section: str,
    *,
    evidence_ids: list[str] | None = None,
    evidence_types: list[str] | None = None,
    text: str = "Deterministic labeled claim.",
) -> Claim:
    if evidence_ids is None:
        evidence_ids = ["fake-proof-candidate-a"]
    if evidence_types is None:
        evidence_types = ["lean"]
    return Claim(
        claim_id=claim_id,
        claim_text=text,
        claim_label=label,
        candidate_id="candidate-a",
        evidence_artifact_ids=evidence_ids,
        evidence_types=evidence_types,
        allowed_in_main_text=section != "Future Work",
        allowed_section=section,
        reason="test claim",
    )


def _artifact_ref(artifact_id: str, path: str) -> ArtifactRef:
    return ArtifactRef(
        id=artifact_id,
        type=ArtifactType.REPORT,
        path=path,
        content_hash="0" * 64,
        producing_commit_hash="1" * 64,
        metadata={"fake": True},
    )
