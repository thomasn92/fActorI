from __future__ import annotations

import json

from factori.artifacts import ArtifactStore
from factori.hashing import canonical_json, sha256_file
from factori.ledger import ResearchLedger
from factori.manifest import build_artifact_manifest
from factori.run_files import build_run_file_index
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    RunFileClassification,
)


def test_missing_run_file_index_is_explicit_and_deterministic(tmp_path) -> None:
    first = build_run_file_index("missing", root=tmp_path)
    second = build_run_file_index("missing", root=tmp_path)

    assert first == second
    assert not first.run_exists
    assert first.files_scanned == 0


def test_run_file_index_classifies_ledger_artifacts_and_sidecars(tmp_path) -> None:
    _, ledger = _linked_run(tmp_path)
    _write_manifest(tmp_path, ledger)

    index = build_run_file_index("run-1", root=tmp_path)
    records = {record.path: record for record in index.files}

    assert records["ledger.sqlite"].classification == RunFileClassification.LEDGER
    assert records["reports/result.json"].classification == (
        RunFileClassification.MANIFESTED_ARTIFACT
    )
    assert records["reports/result.json"].manifested
    assert records["reports/result.json"].ledgered
    assert records["reports/result.json.meta.json"].classification == (
        RunFileClassification.NORMAL_ARTIFACT
    )
    assert index.manifest_entries == 1


def test_run_file_index_classifies_non_provenance_directories(tmp_path) -> None:
    store, _ = _linked_run(tmp_path)
    run_path = store.run_path("run-1")
    payload = {
        "not_provenance": True,
        "not_evidence": True,
        "not_ledgered": True,
    }
    expected = {
        "replay/report.json": RunFileClassification.REPLAY_REPORT,
        "diagnostics/report.json": RunFileClassification.DIAGNOSTIC_REPORT,
        "comparisons/report.json": RunFileClassification.COMPARISON_REPORT,
        "hygiene/report.json": RunFileClassification.NON_PROVENANCE_REPORT,
    }
    for relative in expected:
        path = run_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(canonical_json(payload) + "\n", encoding="utf-8")

    index = build_run_file_index("run-1", root=tmp_path)
    records = {record.path: record for record in index.files}

    for relative, classification in expected.items():
        assert records[relative].classification == classification
        assert records[relative].non_provenance_marked


def test_run_file_index_classifies_cache_and_unexpected_files(tmp_path) -> None:
    store, _ = _linked_run(tmp_path)
    run_path = store.run_path("run-1")
    (run_path / "reports" / "stale.tmp").write_text("stale", encoding="utf-8")
    (run_path / "notes.txt").write_text("unexpected", encoding="utf-8")

    records = {
        record.path: record
        for record in build_run_file_index("run-1", root=tmp_path).files
    }

    assert records["reports/stale.tmp"].classification == (
        RunFileClassification.CACHE_OR_TEMP
    )
    assert records["notes.txt"].classification == RunFileClassification.UNEXPECTED


def test_run_file_index_does_not_mutate_ledger_or_manifest(tmp_path) -> None:
    _, ledger = _linked_run(tmp_path)
    manifest_path = _write_manifest(tmp_path, ledger)
    commits_before = len(ledger.list_commits("run-1"))
    manifest_hash_before = sha256_file(manifest_path)

    build_run_file_index("run-1", root=tmp_path)

    assert len(ledger.list_commits("run-1")) == commits_before
    assert sha256_file(manifest_path) == manifest_hash_before


def test_hygiene_directory_is_excluded_from_normal_manifest(tmp_path) -> None:
    store, _ = _linked_run(tmp_path)
    hygiene_path = store.run_path("run-1") / "hygiene"
    hygiene_path.mkdir(parents=True)
    (hygiene_path / "output-hygiene-report.json").write_text(
        canonical_json(
            {
                "not_provenance": True,
                "not_evidence": True,
                "not_ledgered": True,
            }
        ),
        encoding="utf-8",
    )

    manifest = build_artifact_manifest("run-1", store)

    assert all("/hygiene/" not in entry.path for entry in manifest.artifacts)


def _linked_run(tmp_path) -> tuple[ArtifactStore, ResearchLedger]:
    store = ArtifactStore(tmp_path)
    store.init_run("run-1")
    ledger = ResearchLedger(store.run_path("run-1") / "ledger.sqlite")
    root_commit = ledger.append_commit(
        run_id="run-1",
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": "run-1"},
        timestamp="1970-01-01T00:00:00.000000Z",
    )
    artifact = store.write_json(
        run_id="run-1",
        artifact_id="result",
        artifact_type=ArtifactType.REPORT,
        data={"result": "deterministic"},
    )
    commit = ledger.append_commit(
        run_id="run-1",
        parent_hash=root_commit.commit_hash,
        action_type=ControllerActionType.WRITE_ARTIFACT,
        payload={"artifact_id": artifact.id},
        artifact_refs=[artifact],
    )
    store.link_artifact_to_commit(artifact, commit.commit_hash)
    return store, ledger


def _write_manifest(tmp_path, ledger: ResearchLedger):
    store = ArtifactStore(tmp_path)
    manifest = build_artifact_manifest("run-1", store)
    path = store.run_path("run-1") / "research_object" / "artifact-manifest.json"
    path.write_text(canonical_json(manifest) + "\n", encoding="utf-8")
    ref = ArtifactRef(
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
        payload=json.loads(path.read_text(encoding="utf-8")),
        artifact_refs=[ref],
    )
    ArtifactStore(tmp_path).link_artifact_to_commit(ref, commit.commit_hash)
    return path
