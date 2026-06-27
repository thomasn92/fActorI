from __future__ import annotations

import json

from typer.testing import CliRunner

from factori.adapters.fake import FakeProseGenerator
from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.full_paper_generation import generate_full_paper
from factori.hashing import sha256_file
from factori.ledger import ResearchLedger
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    ArtifactRef,
    ControllerActionType,
    FullPaperArtifactBundle,
    FullPaperGenerationConfig,
    FullPaperGenerationReport,
    FullPaperGenerationStatus,
    PipelineRunConfig,
    PipelineStage,
)


def test_full_paper_generation_models_are_importable() -> None:
    assert FullPaperGenerationConfig
    assert FullPaperArtifactBundle
    assert FullPaperGenerationReport


def test_generate_full_paper_library_writes_expected_bundle(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-1")
    store = ArtifactStore(tmp_path)
    ledger = ResearchLedger(tmp_path / "runs" / "run-1" / "ledger.sqlite")

    result = generate_full_paper(
        run_id="run-1",
        root=tmp_path,
        store=store,
        ledger=ledger,
        prose_generator=FakeProseGenerator(),
        config=FullPaperGenerationConfig(run_id="run-1", write_report=True),
    )

    bundle = result.artifact_bundle
    assert result.report.generation_status in {
        FullPaperGenerationStatus.PAPER_GENERATION_SUCCEEDED,
        FullPaperGenerationStatus.PAPER_GENERATION_SUCCEEDED_WITH_WARNINGS,
    }
    assert bundle.citation_registry_artifact_id == "citation-registry"
    assert bundle.complete_manuscript_draft_artifact_id == "complete-manuscript-draft"
    assert bundle.latex_artifact_id == "paper"
    assert bundle.latex_source_map_artifact_id == "latex-source-map"
    assert bundle.paper_critic_report_artifact_id == "paper-critic-report"
    assert bundle.full_paper_generation_report_artifact_id == "full-paper-generation-report"
    assert bundle.full_paper_artifact_bundle_artifact_id == "full-paper-artifact-bundle"
    assert result.report.publication_ready is False
    assert result.report.is_verification_evidence is False
    for ref in (result.report_artifact, result.bundle_artifact):
        assert ref is not None
        _assert_non_evidence_artifact(tmp_path, ref)


def test_generate_paper_cli_works_and_json_is_valid(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-json")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-json",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    generated = payload["full_paper_generation_result"]
    assert generated["is_verification_evidence"] is False
    assert generated["report"]["publication_ready"] is False
    bundle = generated["artifact_bundle"]
    assert bundle["complete_manuscript_draft_artifact_id"] == "complete-manuscript-draft"
    assert bundle["latex_artifact_id"] == "paper"
    assert bundle["paper_critic_report_artifact_id"] == "paper-critic-report"


def test_generate_paper_write_report_writes_full_report_and_bundle(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-report")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-report",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    artifacts = payload["artifacts"]
    report_ref = ArtifactRef.model_validate(artifacts["full_paper_generation_report"])
    bundle_ref = ArtifactRef.model_validate(artifacts["full_paper_artifact_bundle"])
    _assert_non_evidence_artifact(tmp_path, report_ref)
    _assert_non_evidence_artifact(tmp_path, bundle_ref)
    assert (tmp_path / "runs" / "run-report" / "reports" / "citation-registry.json").is_file()
    assert (
        tmp_path / "runs" / "run-report" / "reports" / "complete-manuscript-draft.md"
    ).is_file()
    assert (tmp_path / "runs" / "run-report" / "latex" / "paper.tex").is_file()
    assert (
        tmp_path / "runs" / "run-report" / "reports" / "paper-critic-report.json"
    ).is_file()


def test_generate_paper_without_revision_does_not_write_revised_draft(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-no-revision")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-no-revision",
            "--write-report",
        ],
    )

    assert result.exit_code == 0, result.output
    assert not (
        tmp_path / "runs" / "run-no-revision" / "reports" / "revised-manuscript-draft.md"
    ).exists()


def test_generate_paper_with_safe_fake_revision_writes_revised_draft(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-revision")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-revision",
            "--apply-safe-fake-revision",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    bundle = payload["full_paper_generation_result"]["artifact_bundle"]
    assert bundle["revised_manuscript_draft_artifact_id"] == "revised-manuscript-draft"
    revised = tmp_path / "runs" / "run-revision" / "reports" / "revised-manuscript-draft.md"
    assert revised.is_file()
    linked = ArtifactRef.model_validate_json(
        (tmp_path / "runs/run-revision/reports/revised-manuscript-draft.md.meta.json").read_text(
            encoding="utf-8"
        )
    )
    assert linked.metadata["is_verification_evidence"] is False
    assert "publication ready" not in revised.read_text(encoding="utf-8").lower()


def test_generate_paper_reexports_latex_after_revision(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-reexport")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-reexport",
            "--apply-safe-fake-revision",
            "--reexport-latex-after-revision",
            "--write-report",
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    artifacts = payload["artifacts"]
    revised_ref = ArtifactRef.model_validate(artifacts["revised_paper"])
    assert revised_ref.path.endswith("latex/revised-paper.tex")
    _assert_non_evidence_artifact(tmp_path, revised_ref)
    bundle = payload["full_paper_generation_result"]["artifact_bundle"]
    assert bundle["revised_latex_artifact_id"] == "revised-paper"
    assert bundle["revised_latex_source_map_artifact_id"] == "revised-latex-source-map"


def test_generate_paper_render_check_fails_closed_without_external_tools(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-render")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-render",
            "--render-check",
        ],
    )

    assert result.exit_code == 1
    assert "External render tools are disabled" in result.stderr


def test_generate_paper_missing_manuscript_plan_fails_clearly(tmp_path) -> None:
    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "missing-plan",
        ],
    )

    assert result.exit_code == 1
    assert "No manuscript plan found" in result.stderr


def test_repeated_generate_paper_write_report_fails_by_default(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-repeat")
    runner = CliRunner()
    first = runner.invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-repeat",
            "--write-report",
        ],
    )
    second = runner.invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-repeat",
            "--write-report",
        ],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 1
    assert "already exists" in second.stderr


def test_generate_paper_skip_if_complete_reuses_existing_report(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-skip")
    runner = CliRunner()
    first = runner.invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-skip",
            "--write-report",
        ],
    )
    second = runner.invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-skip",
            "--write-report",
            "--rerun-policy",
            "skip-if-complete",
            "--json",
        ],
    )

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    payload = json.loads(second.output)
    assert payload["artifacts"]["full_paper_generation_report"] is not None


def test_full_paper_generation_does_not_mutate_claim_or_evidence_tables(tmp_path) -> None:
    _prepare_run(tmp_path, run_id="run-boundary")
    before = _claim_table_snapshot(tmp_path, "run-boundary")

    result = CliRunner().invoke(
        app,
        [
            "generate-paper",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-boundary",
            "--apply-safe-fake-revision",
            "--write-report",
        ],
    )

    assert result.exit_code == 0, result.output
    assert _claim_table_snapshot(tmp_path, "run-boundary") == before
    ledger = ResearchLedger(tmp_path / "runs" / "run-boundary" / "ledger.sqlite")
    actions = [commit.action_type for commit in ledger.list_commits("run-boundary")]
    assert ControllerActionType.FULL_PAPER_GENERATION_WRITTEN in actions


def _prepare_run(tmp_path, *, run_id: str) -> None:
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id=run_id,
            domain="human geography",
            root=tmp_path,
            stop_after=PipelineStage.PLAN_MANUSCRIPT,
        )
    )


def _assert_non_evidence_artifact(tmp_path, ref: ArtifactRef) -> None:
    path = tmp_path / ref.path
    assert path.is_file()
    assert ref.content_hash == sha256_file(path)
    linked = ArtifactRef.model_validate_json(
        (tmp_path / f"{ref.path}.meta.json").read_text(encoding="utf-8")
    )
    assert linked.metadata["is_verification_evidence"] is False
    assert linked.metadata["creates_scientific_validation"] is False
    assert linked.metadata["implies_publication_readiness"] is False


def _claim_table_snapshot(tmp_path, run_id: str) -> bytes:
    path = tmp_path / "runs" / run_id / "reports" / "claim-table.json"
    return path.read_bytes()
