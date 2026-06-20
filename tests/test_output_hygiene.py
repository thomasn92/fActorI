from __future__ import annotations

import json
import shutil

import pytest
from typer.testing import CliRunner

from factori.artifacts import ArtifactStore
from factori.cli import app
from factori.hashing import canonical_json, sha256_file
from factori.ledger import ResearchLedger
from factori.manifest import build_artifact_manifest
from factori.output_hygiene import (
    inspect_output_hygiene,
    summarize_output_hygiene,
    write_output_hygiene_report,
)
from factori.run_all import run_deterministic_pipeline
from factori.schemas import (
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    OutputHygieneCategory,
    OutputHygieneStatus,
    PipelineRunConfig,
)


def test_missing_run_reports_inspection_failed(tmp_path) -> None:
    report = inspect_output_hygiene("missing", root=tmp_path)

    assert report.hygiene_status == OutputHygieneStatus.HYGIENE_INSPECTION_FAILED
    assert report.blocking_findings_count == 1


def test_hygiene_inspection_and_summary_are_deterministic(tmp_path) -> None:
    _manifested_run(tmp_path)

    first = inspect_output_hygiene("run-1", root=tmp_path)
    second = inspect_output_hygiene("run-1", root=tmp_path)

    assert first == second
    assert summarize_output_hygiene(first) == summarize_output_hygiene(second)


def test_complete_run_all_output_is_clean_or_warning_only(tmp_path) -> None:
    run_deterministic_pipeline(
        PipelineRunConfig(
            run_id="complete",
            domain="human geography",
            root=tmp_path,
        )
    )

    report = inspect_output_hygiene("complete", root=tmp_path)

    assert report.hygiene_status in {
        OutputHygieneStatus.CLEAN,
        OutputHygieneStatus.CLEAN_WITH_WARNINGS,
    }
    assert report.blocking_findings_count == 0
    pipeline_record = next(
        record
        for record in report.file_index.files
        if record.path == "reports/pipeline-run-report.json"
    )
    assert pipeline_record.manifested
    assert pipeline_record.ledgered
    cli_result = CliRunner().invoke(
        app,
        ["inspect-hygiene", "--root", str(tmp_path), "--run-id", "complete"],
    )
    assert cli_result.exit_code == 0, cli_result.output
    assert "hygiene_status=" in cli_result.output


def test_orphaned_artifact_and_missing_linkage_are_detected(tmp_path) -> None:
    store, _, _ = _manifested_run(tmp_path)
    (store.run_path("run-1") / "reports" / "orphan.json").write_text(
        "{}\n",
        encoding="utf-8",
    )

    report = inspect_output_hygiene("run-1", root=tmp_path)

    assert _has_category(report, OutputHygieneCategory.ORPHANED_ARTIFACT)
    assert _has_category(report, OutputHygieneCategory.MISSING_MANIFEST_ENTRY)
    assert report.orphaned_files == 1


def test_manifest_entry_pointing_to_missing_file_is_detected(tmp_path) -> None:
    store, _, _ = _manifested_run(tmp_path)
    (store.run_path("run-1") / "reports" / "result.json").unlink()

    report = inspect_output_hygiene("run-1", root=tmp_path)

    assert _has_category(
        report,
        OutputHygieneCategory.MISSING_FILE_FOR_MANIFEST_ENTRY,
    )
    assert report.missing_manifest_files >= 1


def test_hash_mismatch_is_detected(tmp_path) -> None:
    store, _, _ = _manifested_run(tmp_path)
    (store.run_path("run-1") / "reports" / "result.json").write_text(
        '{"tampered":true}\n',
        encoding="utf-8",
    )

    report = inspect_output_hygiene("run-1", root=tmp_path)

    assert _has_category(report, OutputHygieneCategory.HASH_MISMATCH)
    assert report.hash_mismatches >= 1


def test_evidence_without_producing_commit_is_detected(tmp_path) -> None:
    _, _, manifest = _manifested_run(tmp_path)
    entry = manifest.artifacts[0].model_copy(
        update={"is_evidence": True, "producing_commit_hash": None}
    )
    _replace_manifest(tmp_path, manifest.model_copy(update={"artifacts": [entry]}))

    report = inspect_output_hygiene("run-1", root=tmp_path)

    assert _has_category(report, OutputHygieneCategory.EVIDENCE_BOUNDARY_RISK)


def test_markdown_or_latex_as_verification_evidence_is_detected(tmp_path) -> None:
    _, _, manifest = _manifested_run(tmp_path, markdown=True)
    entry = manifest.artifacts[0].model_copy(update={"is_evidence": True})
    _replace_manifest(tmp_path, manifest.model_copy(update={"artifacts": [entry]}))

    report = inspect_output_hygiene("run-1", root=tmp_path)

    assert any(
        finding.category == OutputHygieneCategory.EVIDENCE_BOUNDARY_RISK
        and "presentation" in finding.message
        for finding in report.findings
    )


@pytest.mark.parametrize("directory", ["replay", "diagnostics", "comparisons"])
def test_non_provenance_report_in_manifest_is_detected(tmp_path, directory) -> None:
    store, _, manifest = _manifested_run(tmp_path)
    relative = f"{directory}/leaked-report.json"
    path = store.run_path("run-1") / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical_json(_boundary_markers()) + "\n", encoding="utf-8")
    leaked_entry = ArtifactManifestEntry(
        artifact_id=f"{directory}-report",
        artifact_type=ArtifactType.REPORT,
        path=f"runs/run-1/{relative}",
        content_hash=sha256_file(path),
        is_evidence=False,
        is_presentation=True,
    )
    _replace_manifest(
        tmp_path,
        manifest.model_copy(update={"artifacts": [*manifest.artifacts, leaked_entry]}),
    )

    report = inspect_output_hygiene("run-1", root=tmp_path)

    assert _has_category(
        report,
        OutputHygieneCategory.MANIFEST_EXCLUSION_VIOLATION,
    )


def test_optional_reports_are_allowed_when_marked_outside_provenance(tmp_path) -> None:
    store, _, _ = _manifested_run(tmp_path)
    for directory in ("replay", "diagnostics", "comparisons"):
        path = store.run_path("run-1") / directory / "report.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(_boundary_markers()) + "\n", encoding="utf-8")

    report = inspect_output_hygiene("run-1", root=tmp_path)

    assert report.non_provenance_files == 3
    assert not _has_category(report, OutputHygieneCategory.NON_PROVENANCE_LEAK)
    assert not _has_category(report, OutputHygieneCategory.REPLAY_DIAGNOSTICS_LEAK)


def test_duplicate_logical_outputs_are_detected(tmp_path) -> None:
    store, _, _ = _manifested_run(tmp_path)
    original = store.run_path("run-1") / "reports" / "result.json"
    shutil.copyfile(original, original.with_name("result-copy.json"))

    report = inspect_output_hygiene("run-1", root=tmp_path)

    assert _has_category(report, OutputHygieneCategory.DUPLICATE_OUTPUT)
    assert report.duplicate_outputs == 1


def test_stale_temp_and_sidecar_files_are_detected(tmp_path) -> None:
    store, _, _ = _manifested_run(tmp_path)
    reports = store.run_path("run-1") / "reports"
    (reports / "stale.tmp").write_text("stale", encoding="utf-8")
    shutil.copyfile(
        reports / "result.json.meta.json",
        reports / "missing.json.meta.json",
    )

    report = inspect_output_hygiene("run-1", root=tmp_path)

    assert _has_category(report, OutputHygieneCategory.STALE_OUTPUT)


def test_unexpected_top_level_file_and_directory_are_detected(tmp_path) -> None:
    store, _, _ = _manifested_run(tmp_path)
    run_path = store.run_path("run-1")
    (run_path / "notes.txt").write_text("notes", encoding="utf-8")
    extra = run_path / "unknown-dir"
    extra.mkdir()
    (extra / "value.json").write_text("{}", encoding="utf-8")

    report = inspect_output_hygiene("run-1", root=tmp_path)

    assert _has_category(report, OutputHygieneCategory.UNEXPECTED_FILE)
    assert _has_category(report, OutputHygieneCategory.UNEXPECTED_DIRECTORY)


def test_missing_prior_checkpoint_output_is_detected(tmp_path) -> None:
    store, _, _ = _manifested_run(tmp_path)
    stage_b = store.run_path("run-1") / "reports" / "stage-b-report.md"
    stage_b.write_text("# Stage B\n", encoding="utf-8")

    report = inspect_output_hygiene("run-1", root=tmp_path)

    assert any(
        finding.category == OutputHygieneCategory.STALE_OUTPUT
        and "run-stage-a" in finding.message
        for finding in report.findings
    )


def test_read_only_planning_and_status_commands_persist_no_artifacts(tmp_path) -> None:
    store, _, _ = _manifested_run(tmp_path)
    before = _relative_files(store.run_path("run-1"))
    runner = CliRunner()

    status = runner.invoke(
        app,
        ["status", "--root", str(tmp_path), "--run-id", "run-1"],
    )
    resume = runner.invoke(
        app,
        [
            "validate-resume",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--start-at",
            "run-stage-a",
        ],
    )
    dry_run = runner.invoke(
        app,
        [
            "plan-run",
            "--root",
            str(tmp_path),
            "--run-id",
            "planned",
            "--domain",
            "human geography",
        ],
    )

    assert status.exit_code == 0
    assert resume.exit_code == 0
    assert dry_run.exit_code == 0
    assert _relative_files(store.run_path("run-1")) == before
    assert not store.run_path("planned").exists()


def test_inspection_does_not_mutate_ledger_or_manifest(tmp_path) -> None:
    store, ledger, _ = _manifested_run(tmp_path)
    manifest_path = store.run_path("run-1") / "research_object" / "artifact-manifest.json"
    commits_before = len(ledger.list_commits("run-1"))
    manifest_hash_before = sha256_file(manifest_path)

    report = inspect_output_hygiene("run-1", root=tmp_path)

    assert len(ledger.list_commits("run-1")) == commits_before
    assert sha256_file(manifest_path) == manifest_hash_before
    assert not report.ledger_mutated
    assert not report.artifact_manifest_mutated


def test_write_report_is_marked_and_remains_outside_provenance(tmp_path) -> None:
    store, ledger, _ = _manifested_run(tmp_path)
    manifest_path = store.run_path("run-1") / "research_object" / "artifact-manifest.json"
    report = inspect_output_hygiene("run-1", root=tmp_path)
    commits_before = len(ledger.list_commits("run-1"))
    manifest_hash_before = sha256_file(manifest_path)

    json_path, markdown_path = write_output_hygiene_report(
        run_id="run-1",
        report=report,
        root=tmp_path,
    )

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    markdown = markdown_path.read_text(encoding="utf-8")
    assert payload["not_provenance"] is True
    assert payload["not_evidence"] is True
    assert payload["not_ledgered"] is True
    assert "not_provenance: true" in markdown
    assert "not_evidence: true" in markdown
    assert "not_ledgered: true" in markdown
    assert len(ledger.list_commits("run-1")) == commits_before
    assert sha256_file(manifest_path) == manifest_hash_before
    assert not any(
        "/hygiene/" in entry.path
        for entry in build_artifact_manifest("run-1", store).artifacts
    )

    follow_up = inspect_output_hygiene("run-1", root=tmp_path)
    assert not _has_category(follow_up, OutputHygieneCategory.NON_PROVENANCE_LEAK)


def test_cli_inspect_hygiene_and_json_output_work(tmp_path) -> None:
    _manifested_run(tmp_path)
    runner = CliRunner()

    plain = runner.invoke(
        app,
        ["inspect-hygiene", "--root", str(tmp_path), "--run-id", "run-1"],
    )
    json_result = runner.invoke(
        app,
        [
            "inspect-hygiene",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--json",
        ],
    )

    assert plain.exit_code == 0, plain.output
    assert "hygiene_status=" in plain.output
    assert "ledger_mutated=false" in plain.output
    payload = json.loads(json_result.output)
    assert payload["run_id"] == "run-1"
    assert payload["not_provenance"] is True


def test_cli_write_report_works_without_ledger_mutation(tmp_path) -> None:
    store, ledger, _ = _manifested_run(tmp_path)
    commits_before = len(ledger.list_commits("run-1"))

    result = CliRunner().invoke(
        app,
        [
            "inspect-hygiene",
            "--root",
            str(tmp_path),
            "--run-id",
            "run-1",
            "--write-report",
        ],
    )

    assert result.exit_code == 0, result.output
    assert (store.run_path("run-1") / "hygiene" / "output-hygiene-report.json").is_file()
    assert (store.run_path("run-1") / "hygiene" / "output-hygiene-report.md").is_file()
    assert len(ledger.list_commits("run-1")) == commits_before


def _manifested_run(
    tmp_path,
    *,
    markdown: bool = False,
) -> tuple[ArtifactStore, ResearchLedger, ArtifactManifest]:
    store = ArtifactStore(tmp_path)
    store.init_run("run-1")
    ledger = ResearchLedger(store.run_path("run-1") / "ledger.sqlite")
    ledger.append_commit(
        run_id="run-1",
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": "run-1"},
        timestamp="1970-01-01T00:00:00.000000Z",
    )
    if markdown:
        artifact = store.write_markdown(
            run_id="run-1",
            artifact_id="result",
            artifact_type=ArtifactType.REPORT,
            markdown="# Result\n",
        )
    else:
        artifact = store.write_json(
            run_id="run-1",
            artifact_id="result",
            artifact_type=ArtifactType.REPORT,
            data={"result": "deterministic"},
        )
    commit = ledger.append_commit(
        run_id="run-1",
        parent_hash=ledger.latest_commit_hash("run-1"),
        action_type=ControllerActionType.WRITE_ARTIFACT,
        payload={"artifact_id": artifact.id},
        artifact_refs=[artifact],
    )
    store.link_artifact_to_commit(artifact, commit.commit_hash)
    manifest = build_artifact_manifest("run-1", store)
    _write_manifest(tmp_path, ledger, manifest)
    return store, ledger, manifest


def _write_manifest(tmp_path, ledger: ResearchLedger, manifest: ArtifactManifest):
    path = tmp_path / "runs" / "run-1" / "research_object" / "artifact-manifest.json"
    path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    artifact = ArtifactRef(
        id="artifact-manifest",
        type=ArtifactType.REPORT,
        path=path.relative_to(tmp_path).as_posix(),
        content_hash=sha256_file(path),
        metadata={"format": "json", "package_part": "artifact_manifest"},
    )
    commit = ledger.append_commit(
        run_id="run-1",
        parent_hash=ledger.latest_commit_hash("run-1"),
        action_type=ControllerActionType.ARTIFACT_MANIFEST_WRITTEN,
        payload=manifest.model_dump(mode="json"),
        artifact_refs=[artifact],
    )
    ArtifactStore(tmp_path).link_artifact_to_commit(artifact, commit.commit_hash)
    return path


def _replace_manifest(tmp_path, manifest: ArtifactManifest) -> None:
    path = tmp_path / "runs" / "run-1" / "research_object" / "artifact-manifest.json"
    path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")


def _has_category(report, category: OutputHygieneCategory) -> bool:
    return any(finding.category == category for finding in report.findings)


def _boundary_markers() -> dict[str, bool]:
    return {
        "not_provenance": True,
        "not_evidence": True,
        "not_ledgered": True,
    }


def _relative_files(run_path) -> set[str]:
    return {
        path.relative_to(run_path).as_posix()
        for path in run_path.rglob("*")
        if path.is_file()
    }
