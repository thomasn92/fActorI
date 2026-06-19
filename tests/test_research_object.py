from __future__ import annotations

import pytest
from typer.testing import CliRunner

from factori.abstract_synthesis import run_abstract_synthesis
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.draft_skeleton import run_draft_skeleton_generation
from factori.ledger import ResearchLedger
from factori.manuscript_plan import run_manuscript_planning
from factori.research_object import ResearchObjectError, build_research_object
from factori.schemas import ConstraintSet, ControllerActionType
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_selection import run_stage_c_selection


def test_package_research_object_errors_clearly_without_draft_skeleton(tmp_path) -> None:
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    with pytest.raises(ResearchObjectError, match="Draft skeleton artifacts not found"):
        build_research_object(run_id="run-1", store=store, ledger=ledger)


def test_research_object_includes_final_nucleus_reference(tmp_path) -> None:
    store, ledger = _run_pipeline_to_draft(tmp_path)

    result = build_research_object(run_id="run-1", store=store, ledger=ledger)

    assert result.research_object.final_nucleus.id
    assert result.research_object.final_nucleus.supporting_candidate_ids
    assert result.manifest.research_object_json.path.endswith(
        "research_object/research-object.json"
    )


def test_research_object_includes_manuscript_plan_reference(tmp_path) -> None:
    store, ledger = _run_pipeline_to_draft(tmp_path)

    result = build_research_object(run_id="run-1", store=store, ledger=ledger)

    assert result.research_object.manuscript_plan_ref.path.endswith(
        "reports/manuscript-plan.json"
    )


def test_research_object_includes_draft_skeleton_reference(tmp_path) -> None:
    store, ledger = _run_pipeline_to_draft(tmp_path)

    result = build_research_object(run_id="run-1", store=store, ledger=ledger)

    assert result.research_object.draft_skeleton_ref.path.endswith(
        "reports/draft-skeleton.json"
    )


def test_research_object_includes_claim_table_and_blocked_claims(tmp_path) -> None:
    store, ledger = _run_pipeline_to_draft(tmp_path)

    result = build_research_object(run_id="run-1", store=store, ledger=ledger)

    assert result.research_object.claim_table_ref.path.endswith("reports/claim-table.json")
    assert result.research_object.blocked_claims_ref.path.endswith(
        "reports/blocked-claims.json"
    )


def test_research_object_includes_stage_reports(tmp_path) -> None:
    store, ledger = _run_pipeline_to_draft(tmp_path)

    result = build_research_object(run_id="run-1", store=store, ledger=ledger)

    assert set(result.research_object.stage_reports) == {
        "stage_a",
        "stage_b",
        "stage_c_selection",
        "stage_c_verification",
        "abstract_synthesis",
    }


def test_research_object_markdown_and_json_are_created(tmp_path) -> None:
    store, ledger = _run_pipeline_to_draft(tmp_path)

    result = build_research_object(run_id="run-1", store=store, ledger=ledger)

    assert (tmp_path / result.manifest.research_object_json.path).is_file()
    assert (tmp_path / result.manifest.research_object_markdown.path).is_file()


def test_every_packaging_artifact_has_a_hash(tmp_path) -> None:
    store, ledger = _run_pipeline_to_draft(tmp_path)

    result = build_research_object(run_id="run-1", store=store, ledger=ledger)
    artifacts = [
        result.manifest.research_object_json,
        result.manifest.research_object_markdown,
        result.manifest.artifact_manifest,
        result.manifest.ledger_summary,
        result.manifest.branch_outcomes,
        result.manifest.reproducibility_manifest,
    ]

    assert all(len(artifact.content_hash) == 64 for artifact in artifacts)
    assert all(artifact.producing_commit_hash for artifact in artifacts)


def test_packaging_creates_ledger_commits(tmp_path) -> None:
    store, ledger = _run_pipeline_to_draft(tmp_path)

    build_research_object(run_id="run-1", store=store, ledger=ledger)
    action_types = [commit.action_type for commit in ledger.list_commits("run-1")]

    for action_type in [
        ControllerActionType.RESEARCH_OBJECT_PACKAGING_STARTED,
        ControllerActionType.ARTIFACT_MANIFEST_WRITTEN,
        ControllerActionType.LEDGER_SUMMARY_WRITTEN,
        ControllerActionType.BRANCH_OUTCOMES_WRITTEN,
        ControllerActionType.REPRODUCIBILITY_MANIFEST_WRITTEN,
        ControllerActionType.RESEARCH_OBJECT_WRITTEN,
    ]:
        assert action_type in action_types


def test_cli_package_research_object_works_after_full_flow(tmp_path) -> None:
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
    package = runner.invoke(
        app,
        ["package-research-object", "--root", str(tmp_path), "--run-id", "run-1"],
    )

    assert stage_a.exit_code == 0
    assert stage_b.exit_code == 0
    assert select.exit_code == 0
    assert stage_c.exit_code == 0
    assert synthesize.exit_code == 0
    assert plan.exit_code == 0
    assert draft.exit_code == 0
    assert package.exit_code == 0
    assert "run_id=run-1" in package.output
    assert "research_object=runs/run-1/research_object/research-object.md" in package.output


def test_cli_package_research_object_errors_without_draft_skeleton(tmp_path) -> None:
    runner = CliRunner()

    result = runner.invoke(
        app,
        ["package-research-object", "--root", str(tmp_path), "--run-id", "run-1"],
    )

    assert result.exit_code == 1
    assert "Draft skeleton artifacts not found" in result.stderr


def _run_pipeline_to_draft(tmp_path) -> tuple[ArtifactStore, ResearchLedger]:
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
    return store, ledger
