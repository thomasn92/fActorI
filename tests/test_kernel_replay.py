from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from factori.abstract_synthesis import run_abstract_synthesis
from factori.artifacts import ArtifactStore
from factori.draft_skeleton import run_draft_skeleton_generation
from factori.export_plan import prepare_export
from factori.final_audit import run_final_audit
from factori.final_paper import run_paper_assembly
from factori.hashing import canonical_json, sha256_file
from factori.kernel_bridge import KernelBridgeError, verify_persisted_replay_core
from factori.ledger import ResearchLedger, compute_commit_hash
from factori.manuscript_plan import run_manuscript_planning
from factori.research_object import build_research_object
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ConstraintSet,
    ControllerActionType,
    KernelMode,
    KernelReplayVerifyCoreResult,
    KernelResponseStatus,
    LedgerCommit,
)
from factori.stage_a import run_stage_a
from factori.stage_b import run_stage_b
from factori.stage_c import run_stage_c
from factori.stage_c_selection import run_stage_c_selection

KERNEL_BINARY = Path("rust-kernel/target/debug/factori-kernel")


@pytest.fixture(scope="module")
def replay_fixture(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("replay-core-fixture")
    _run_pipeline_to_export(root)
    _build_kernel_binary()
    return root


def test_rust_replay_core_has_both_mode_parity_and_is_read_only(
    replay_fixture: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = replay_fixture
    ledger = ResearchLedger(root / "runs" / "run-replay-core" / "ledger.sqlite")
    commits = ledger.list_commits_read_only("run-replay-core")
    tip = commits[-1].commit_hash
    ledger_path = root / "runs" / "run-replay-core" / "ledger.sqlite"
    ledger_before = ledger_path.read_bytes()
    artifact_hashes_before = {
        artifact.path: sha256_file(root / artifact.path)
        for commit in commits
        for artifact in commit.artifact_refs
    }
    replay_dir = root / "runs" / "run-replay-core" / "replay"

    responses = [
        verify_persisted_replay_core(
            "run-replay-core",
            tip,
            mode=mode,
            root=root,
            kernel_binary=KERNEL_BINARY,
        )
        for mode in (
            KernelMode.DEVELOPMENT_COMPATIBILITY,
            KernelMode.STRICT_PRODUCTION,
        )
    ]

    assert all(response.status == KernelResponseStatus.ACCEPTED for response in responses)
    development = responses[0].result
    strict = responses[1].result
    assert isinstance(development, KernelReplayVerifyCoreResult)
    assert development == strict
    assert development.required_outputs_checked == 11
    assert development.authority_granted is False

    stale = verify_persisted_replay_core(
        "run-replay-core",
        commits[-2].commit_hash,
        root=root,
        kernel_binary=KERNEL_BINARY,
    )
    assert stale.status == KernelResponseStatus.REJECTED
    assert [item.code for item in stale.diagnostics] == ["replay_not_latest"]

    wrong_result = development.model_copy(update={"ledger_artifact_inventory_hash": "0" * 64})
    wrong_response = responses[0].model_copy(update={"result": wrong_result})
    monkeypatch.setattr(
        "factori.kernel_bridge._invoke_kernel",
        lambda *args, **kwargs: wrong_response,
    )
    with pytest.raises(KernelBridgeError, match="does not match request"):
        verify_persisted_replay_core("run-replay-core", tip, root=root)

    assert ledger_path.read_bytes() == ledger_before
    assert len(ledger.list_commits_read_only("run-replay-core")) == len(commits)
    assert ledger.list_commits_read_only("run-replay-core")[-1].commit_hash == tip
    assert {
        path: sha256_file(root / path) for path in artifact_hashes_before
    } == artifact_hashes_before
    assert not replay_dir.exists()


@pytest.mark.parametrize(
    "mutation, expected_code",
    [
        ("tamper_artifact", "replay_required_output_invalid"),
        ("missing_output", "replay_required_output_invalid"),
        ("manifest_tamper", "replay_required_output_invalid"),
        ("claim_tamper", "replay_required_output_invalid"),
        ("ledger_corruption", "replay_required_output_invalid"),
        ("symlink_artifact", "replay_required_output_invalid"),
        ("authority_assertion", "replay_authority_violation"),
        ("derived_path", "replay_required_output_invalid"),
        ("diagnostics_path", "replay_required_output_invalid"),
        ("comparisons_path", "replay_required_output_invalid"),
    ],
)
@pytest.mark.parametrize(
    "mode", [KernelMode.DEVELOPMENT_COMPATIBILITY, KernelMode.STRICT_PRODUCTION]
)
def test_replay_core_mutations_fail_closed_in_both_modes(
    replay_fixture: Path,
    tmp_path: Path,
    mutation: str,
    expected_code: str,
    mode: KernelMode,
) -> None:
    root = tmp_path / "mutated"
    shutil.copytree(replay_fixture, root)
    ledger = ResearchLedger(root / "runs" / "run-replay-core" / "ledger.sqlite")
    tip = _apply_replay_mutation(root, ledger, mutation)

    response = verify_persisted_replay_core(
        "run-replay-core",
        tip,
        mode=mode,
        root=root,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status == KernelResponseStatus.REJECTED
    assert [diagnostic.code for diagnostic in response.diagnostics] == [expected_code]


@pytest.mark.parametrize(
    "mutation, expected_code",
    [
        ("manifest_count", "replay_manifest_mismatch"),
        ("manifest_order", "replay_manifest_invalid"),
        ("manifest_metadata", "replay_manifest_mismatch"),
        ("manifest_path", "replay_manifest_mismatch"),
        ("manifest_prefix", "replay_manifest_mismatch"),
        ("manifest_classification", "replay_authority_violation"),
        ("claim_role", "replay_dependency_mismatch"),
        ("claim_type", "replay_dependency_mismatch"),
        ("claim_contradiction", "replay_dependency_mismatch"),
        ("claim_duplicate", "replay_dependency_ambiguous"),
        ("claim_dangling", "replay_dependency_mismatch"),
        ("claim_ambiguous", "replay_dependency_ambiguous"),
        ("claim_missing_support", "replay_dependency_mismatch"),
        ("claim_extraneous_support", "replay_dependency_mismatch"),
        ("claim_evidence_types", "replay_dependency_mismatch"),
        ("claim_presentation", "replay_authority_violation"),
        ("forbidden_label", "replay_authority_violation"),
    ],
)
@pytest.mark.parametrize(
    "mode", [KernelMode.DEVELOPMENT_COMPATIBILITY, KernelMode.STRICT_PRODUCTION]
)
def test_replay_core_resealed_semantic_mutations_reach_intended_validator(
    replay_fixture: Path,
    tmp_path: Path,
    mutation: str,
    expected_code: str,
    mode: KernelMode,
) -> None:
    root = tmp_path / "semantic-mutation"
    shutil.copytree(replay_fixture, root)
    ledger = ResearchLedger(root / "runs" / "run-replay-core" / "ledger.sqlite")
    tip = _apply_resealed_semantic_mutation(root, ledger, mutation)

    response = verify_persisted_replay_core(
        "run-replay-core",
        tip,
        mode=mode,
        root=root,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status == KernelResponseStatus.REJECTED
    assert [diagnostic.code for diagnostic in response.diagnostics] == [expected_code]


@pytest.mark.parametrize(
    "mutation, expected_code",
    [
        ("incomplete", "replay_not_complete"),
        ("duplicate_path", "replay_required_output_invalid"),
        ("producer_mismatch", "replay_required_output_invalid"),
        ("path_escape", "replay_required_output_invalid"),
        ("required_output_ambiguous", "replay_required_output_invalid"),
        ("required_output_missing", "replay_required_output_missing"),
        ("manifest_artifact_path", "replay_manifest_invalid"),
    ],
)
@pytest.mark.parametrize(
    "mode", [KernelMode.DEVELOPMENT_COMPATIBILITY, KernelMode.STRICT_PRODUCTION]
)
def test_replay_core_resealed_structural_mutations_fail_closed(
    replay_fixture: Path,
    tmp_path: Path,
    mutation: str,
    expected_code: str,
    mode: KernelMode,
) -> None:
    root = tmp_path / "structural-mutation"
    shutil.copytree(replay_fixture, root)
    ledger = ResearchLedger(root / "runs" / "run-replay-core" / "ledger.sqlite")
    tip = _apply_resealed_structural_mutation(root, ledger, mutation)

    response = verify_persisted_replay_core(
        "run-replay-core",
        tip,
        mode=mode,
        root=root,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status == KernelResponseStatus.REJECTED
    assert [diagnostic.code for diagnostic in response.diagnostics] == [expected_code]


@pytest.mark.parametrize("mutation", ["ledger_append", "artifact_replacement"])
@pytest.mark.parametrize(
    "mode", [KernelMode.DEVELOPMENT_COMPATIBILITY, KernelMode.STRICT_PRODUCTION]
)
def test_replay_core_detects_deterministic_concurrent_snapshot_changes(
    replay_fixture: Path,
    tmp_path: Path,
    mutation: str,
    mode: KernelMode,
) -> None:
    root = tmp_path / "concurrent-mutation"
    shutil.copytree(replay_fixture, root)
    ledger = ResearchLedger(root / "runs" / "run-replay-core" / "ledger.sqlite")
    sentinel_path = root / "runs" / "run-replay-core" / "logs" / "snapshot-sentinel.bin"
    sentinel_path.parent.mkdir(parents=True, exist_ok=True)
    with sentinel_path.open("wb") as sentinel:
        sentinel.truncate(64 * 1024 * 1024)
    sentinel_ref = ArtifactRef(
        id="snapshot-sentinel",
        type=ArtifactType.LOG,
        path=sentinel_path.relative_to(root).as_posix(),
        content_hash=sha256_file(sentinel_path),
        metadata={"format": "binary", "test_fixture": True},
    )
    sentinel_commit = ledger.append_commit(
        run_id="run-replay-core",
        parent_hash=ledger.latest_commit_hash("run-replay-core"),
        action_type=ControllerActionType.WRITE_ARTIFACT,
        payload={"test_fixture": "snapshot-sentinel"},
        artifact_refs=[sentinel_ref],
        timestamp="2099-01-01T00:00:00.000000Z",
    )
    request = {
        "protocol_version": "0.89.0",
        "request_id": f"concurrent-{mutation}-{mode.value}",
        "operation": "replay.verify_core",
        "mode": mode.value,
        "payload": {
            "run_id": "run-replay-core",
            "ledger_tip_hash": sentinel_commit.commit_hash,
        },
    }
    request_path = root / "concurrent-request.json"
    _rewrite_json(request_path, request)

    with request_path.open(encoding="utf-8") as request_stream:
        process = subprocess.Popen(
            [str(KERNEL_BINARY.resolve()), "--root", str(root)],
            stdin=request_stream,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        _wait_until_kernel_reads(process, sentinel_path)
        if mutation == "ledger_append":
            ledger.append_commit(
                run_id="run-replay-core",
                parent_hash=sentinel_commit.commit_hash,
                action_type=ControllerActionType.WRITE_ARTIFACT,
                payload={"test_fixture": "concurrent-append"},
                timestamp="2099-01-01T00:00:01.000000Z",
            )
        else:
            first_artifact = next(
                ref
                for commit in ledger.list_commits_read_only("run-replay-core")
                for ref in commit.artifact_refs
                if ref.path != sentinel_ref.path
            )
            (root / first_artifact.path).write_bytes(
                (root / first_artifact.path).read_bytes() + b"concurrent replacement"
            )
        try:
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
            raise

    assert process.returncode == 0, stderr
    response = json.loads(stdout)
    assert response["status"] == "rejected"
    assert [diagnostic["code"] for diagnostic in response["diagnostics"]] == [
        "replay_snapshot_changed"
    ]


def _run_pipeline_to_export(root: Path) -> ResearchLedger:
    run_id = "run-replay-core"
    store = ArtifactStore(root)
    ledger = ResearchLedger(root / "runs" / run_id / "ledger.sqlite")
    run_stage_a(
        run_id=run_id,
        constraints=ConstraintSet(domain="human geography"),
        store=store,
        ledger=ledger,
    )
    run_stage_b(run_id=run_id, store=store, ledger=ledger)
    run_stage_c_selection(run_id=run_id, store=store, ledger=ledger)
    run_stage_c(run_id=run_id, store=store, ledger=ledger)
    run_abstract_synthesis(run_id=run_id, store=store, ledger=ledger)
    run_manuscript_planning(run_id=run_id, store=store, ledger=ledger)
    run_draft_skeleton_generation(run_id=run_id, store=store, ledger=ledger)
    build_research_object(run_id=run_id, store=store, ledger=ledger)
    run_paper_assembly(run_id=run_id, store=store, ledger=ledger)
    run_final_audit(run_id=run_id, store=store, ledger=ledger)
    prepare_export(run_id=run_id, store=store, ledger=ledger)
    return ledger


def _rewrite_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")


def _wait_until_kernel_reads(process: subprocess.Popen[str], path: Path) -> None:
    expected = path.resolve()
    deadline = time.monotonic() + 10
    descriptors = Path(f"/proc/{process.pid}/fd")
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError("kernel exited before reading the snapshot sentinel")
        try:
            if any(descriptor.resolve() == expected for descriptor in descriptors.iterdir()):
                return
        except (FileNotFoundError, OSError):
            pass
        time.sleep(0.001)
    process.kill()
    process.wait()
    raise AssertionError("kernel did not open the snapshot sentinel before the test timeout")


def _apply_replay_mutation(root: Path, ledger: ResearchLedger, mutation: str) -> str:
    """Apply a bounded fixture mutation and return the resealed current tip."""
    commits = ledger.list_commits_read_only("run-replay-core")
    latest = commits[-1]
    if mutation == "tamper_artifact":
        artifact = latest.artifact_refs[0]
        (root / artifact.path).write_bytes((root / artifact.path).read_bytes() + b"tampered")
        return _reseal_latest_commit(root, ledger)
    if mutation == "missing_output":
        artifact = latest.artifact_refs[0]
        (root / artifact.path).unlink()
        return latest.commit_hash
    if mutation in {"manifest_tamper", "claim_tamper"}:
        action = (
            "ArtifactManifestWritten" if mutation == "manifest_tamper" else "ClaimTableBuilt"
        )
        commit = next(commit for commit in commits if commit.action_type == action)
        artifact = commit.artifact_refs[0]
        (root / artifact.path).write_bytes((root / artifact.path).read_bytes() + b"tampered")
        return latest.commit_hash
    if mutation == "ledger_corruption":
        with sqlite3.connect(ledger.path) as connection:
            connection.execute("DROP TRIGGER commits_no_update")
            connection.execute("DROP TRIGGER commits_no_delete")
            connection.execute(
                "UPDATE commits SET payload_json = ? WHERE commit_hash = ?",
                ("{}", latest.commit_hash),
            )
        return latest.commit_hash
    if mutation == "symlink_artifact":
        artifact = latest.artifact_refs[0]
        artifact_path = root / artifact.path
        artifact_path.unlink()
        target = next(
            candidate
            for candidate in (ref for commit in commits for ref in commit.artifact_refs)
            if candidate.path != artifact.path
        )
        artifact_path.symlink_to(root / target.path)
        return latest.commit_hash
    if mutation == "authority_assertion":
        artifact = latest.artifact_refs[0]
        payload = json.loads((root / artifact.path).read_text(encoding="utf-8"))
        payload["publication_ready"] = True
        _rewrite_json(root / artifact.path, payload)
        latest_payload = {**latest.payload, "publication_ready": True}
        return _reseal_latest_commit(root, ledger, payload=latest_payload)
    if mutation in {"derived_path", "diagnostics_path", "comparisons_path"}:
        artifact = latest.artifact_refs[0]
        old_path = root / artifact.path
        directory = {
            "derived_path": "replay",
            "diagnostics_path": "diagnostics",
            "comparisons_path": "comparisons",
        }[mutation]
        new_relative = f"runs/run-replay-core/{directory}/{old_path.name}"
        new_path = root / new_relative
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.rename(new_path)
        return _reseal_latest_commit(root, ledger, path=new_relative)
    raise AssertionError(f"unknown replay mutation: {mutation}")


def _reseal_latest_commit(
    root: Path,
    ledger: ResearchLedger,
    *,
    payload: dict[str, object] | None = None,
    path: str | None = None,
) -> str:
    latest = ledger.list_commits_read_only("run-replay-core")[-1]
    refs = []
    for ref in latest.artifact_refs:
        updated = ref.model_copy(
            update={
                "path": path if path is not None else ref.path,
                "content_hash": sha256_file(root / (path if path is not None else ref.path)),
                "producing_commit_hash": None,
            }
        )
        refs.append(updated)
    new_hash = compute_commit_hash(
        parent_hash=latest.parent_hash,
        run_id=latest.run_id,
        candidate_id=latest.candidate_id,
        action_type=latest.action_type,
        payload=latest.payload if payload is None else payload,
        artifact_refs=refs,
        timestamp=latest.timestamp,
        self_link_artifact_ids={ref.id for ref in refs},
    )
    linked_refs = [ref.model_copy(update={"producing_commit_hash": new_hash}) for ref in refs]
    with sqlite3.connect(ledger.path) as connection:
        connection.execute("DROP TRIGGER commits_no_update")
        connection.execute("DROP TRIGGER commits_no_delete")
        connection.execute(
            """
            UPDATE commits
            SET commit_hash = ?, payload_json = ?, artifact_refs_json = ?
            WHERE commit_hash = ?
            """,
            (
                new_hash,
                canonical_json(latest.payload if payload is None else payload),
                canonical_json(linked_refs),
                latest.commit_hash,
            ),
        )
    return new_hash


def _apply_resealed_semantic_mutation(
    root: Path,
    ledger: ResearchLedger,
    mutation: str,
) -> str:
    if mutation == "claim_ambiguous":
        commits = ledger.list_commits_read_only("run-replay-core")
        proof_ref = next(
            ref
            for commit in commits
            for ref in commit.artifact_refs
            if ref.id.startswith("fake-proof-")
        )
        duplicate_path = proof_ref.path.removesuffix(".json") + "-duplicate.json"
        shutil.copyfile(root / proof_ref.path, root / duplicate_path)
        duplicate_ref = proof_ref.model_copy(
            update={"path": duplicate_path, "producing_commit_hash": None}
        )
        return _reseal_commit_suffix(
            root,
            ledger,
            start_action="AbstractSynthesisStarted",
            ref_mutator=lambda refs: [*refs, duplicate_ref],
        )
    if mutation.startswith("manifest_"):
        return _reseal_commit_suffix(
            root,
            ledger,
            start_action="ArtifactManifestWritten",
            manifest_mutator=lambda payload: _mutate_manifest(payload, mutation),
        )
    return _reseal_commit_suffix(
        root,
        ledger,
        start_action="ClaimTableBuilt",
        payload_mutator=lambda payload: _mutate_claim_table(payload, mutation),
    )


def _apply_resealed_structural_mutation(
    root: Path,
    ledger: ResearchLedger,
    mutation: str,
) -> str:
    commits = ledger.list_commits_read_only("run-replay-core")
    latest = commits[-1]
    if mutation == "incomplete":
        first_completion = next(
            commit
            for commit in commits
            if commit.action_type == "ExportReadinessReportWritten"
        )
        first_index = commits.index(first_completion)
        with sqlite3.connect(ledger.path) as connection:
            connection.execute("DROP TRIGGER commits_no_update")
            connection.execute("DROP TRIGGER commits_no_delete")
            for commit in reversed(commits[first_index:]):
                connection.execute(
                    "DELETE FROM commits WHERE commit_hash = ?", (commit.commit_hash,)
                )
        return commits[first_index - 1].commit_hash
    if mutation == "duplicate_path":
        ref = latest.artifact_refs[0].model_copy(update={"producing_commit_hash": None})
        return _rewrite_latest_refs(ledger, [ref, ref], self_link_ids={ref.id})
    if mutation == "producer_mismatch":
        ref = latest.artifact_refs[0].model_copy(
            update={"producing_commit_hash": "0" * 64}
        )
        return _rewrite_latest_refs(ledger, [ref], self_link_ids=set())
    if mutation == "path_escape":
        ref = latest.artifact_refs[0].model_copy(
            update={
                "path": "runs/run-replay-core/../escaped.json",
                "producing_commit_hash": None,
            }
        )
        return _rewrite_latest_refs(ledger, [ref], self_link_ids={ref.id})
    if mutation == "required_output_ambiguous":
        def add_json_ref(refs: list[Any]) -> list[Any]:
            source = next(ref for ref in refs if ref.path.endswith(".json"))
            duplicate_path = source.path.removesuffix(".json") + "-duplicate.json"
            shutil.copyfile(root / source.path, root / duplicate_path)
            duplicate = source.model_copy(
                update={
                    "id": source.id + "-duplicate",
                    "path": duplicate_path,
                    "producing_commit_hash": None,
                }
            )
            return [*refs, duplicate]

        return _reseal_commit_suffix(
            root,
            ledger,
            start_action="ExportReadinessReportWritten",
            ref_mutator=add_json_ref,
        )
    if mutation == "required_output_missing":
        return _reseal_commit_suffix(
            root,
            ledger,
            start_action="ManuscriptPlanBuilt",
            payload_mutator=lambda payload: {
                key: value for key, value in payload.items() if key != "sections"
            },
        )
    if mutation == "manifest_artifact_path":
        def move_manifest(refs: list[Any]) -> list[Any]:
            manifest = next(ref for ref in refs if ref.path.endswith(".json"))
            return [
                ref.model_copy(
                    update={
                        "path": "runs/run-replay-core/reports/artifact-manifest.json"
                    }
                )
                if ref == manifest
                else ref
                for ref in refs
            ]

        return _reseal_commit_suffix(
            root,
            ledger,
            start_action="ArtifactManifestWritten",
            ref_mutator=move_manifest,
        )
    raise AssertionError(f"unknown structural mutation: {mutation}")


def _mutate_manifest(payload: dict[str, Any], mutation: str) -> dict[str, Any]:
    if mutation == "manifest_count":
        payload["evidence_artifact_count"] += 1
    elif mutation == "manifest_order":
        payload["artifacts"] = list(reversed(payload["artifacts"]))
    elif mutation == "manifest_metadata":
        payload["artifacts"][0]["metadata"] = {
            **payload["artifacts"][0]["metadata"],
            "injected": True,
        }
    elif mutation == "manifest_path":
        payload["artifacts"][0]["path"] += ".moved"
    elif mutation == "manifest_prefix":
        payload["artifacts"] = payload["artifacts"][:-1]
    elif mutation == "manifest_classification":
        evidence = next(entry for entry in payload["artifacts"] if entry["is_evidence"])
        evidence["is_presentation"] = True
    else:
        raise AssertionError(f"unknown manifest mutation: {mutation}")
    return payload


def _mutate_claim_table(payload: dict[str, Any], mutation: str) -> dict[str, Any]:
    link = payload["evidence_links"][0]
    if mutation == "claim_role":
        link["evidence_role"] = "proof"
    elif mutation == "claim_type":
        link["artifact_type"] = "report"
    elif mutation == "claim_contradiction":
        payload["evidence_links"].append({**link, "supports_label": False})
    elif mutation == "claim_duplicate":
        payload["evidence_links"].append(dict(link))
    elif mutation == "claim_dangling":
        link["claim_id"] = "claim-missing"
    elif mutation == "claim_missing_support":
        link["supports_label"] = False
    elif mutation == "claim_extraneous_support":
        payload["evidence_links"].append(
            {
                "claim_id": link["claim_id"],
                "artifact_id": "claim-table",
                "artifact_type": "report",
                "evidence_role": None,
                "supports_label": True,
            }
        )
    elif mutation == "claim_evidence_types":
        payload["claims"][0]["evidence_types"] = ["experiment"]
    elif mutation == "claim_presentation":
        payload["claims"][0]["evidence_artifact_ids"] = ["claim-table"]
        payload["claims"][0]["evidence_types"] = ["report"]
        payload["evidence_links"] = [
            {
                "claim_id": link["claim_id"],
                "artifact_id": "claim-table",
                "artifact_type": "report",
                "evidence_role": None,
                "supports_label": True,
            }
        ]
    elif mutation == "forbidden_label":
        payload["claims"][0]["claim_label"] = "ExperimentVerified"
    else:
        raise AssertionError(f"unknown claim mutation: {mutation}")
    return payload


def _reseal_commit_suffix(
    root: Path,
    ledger: ResearchLedger,
    *,
    start_action: str,
    payload_mutator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    manifest_mutator: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    ref_mutator: Callable[[list[Any]], list[Any]] | None = None,
) -> str:
    """Reseal one mutation and every descendant without using production writers."""
    commits = ledger.list_commits_read_only("run-replay-core")
    start_index = next(
        index for index, commit in enumerate(commits) if commit.action_type == start_action
    )
    previous_hash = commits[start_index - 1].commit_hash
    prefix_refs = [ref for commit in commits[:start_index] for ref in commit.artifact_refs]
    updates: list[tuple[LedgerCommit, str, str, dict[str, Any], list[Any]]] = []
    start_mutated = False

    for commit in commits[start_index:]:
        payload = json.loads(canonical_json(commit.payload))
        source_refs = list(commit.artifact_refs)
        if commit.action_type == start_action and not start_mutated:
            if payload_mutator is not None:
                payload = payload_mutator(payload)
                artifact = next(ref for ref in source_refs if ref.path.endswith(".json"))
                _rewrite_json(root / artifact.path, payload)
            if ref_mutator is not None:
                source_refs = ref_mutator(source_refs)
            start_mutated = True
        if commit.action_type == "ArtifactManifestWritten":
            payload = _rebuilt_manifest_payload(payload, prefix_refs)
            if manifest_mutator is not None:
                payload = manifest_mutator(payload)
            artifact = next(ref for ref in source_refs if ref.path.endswith(".json"))
            _rewrite_json(root / artifact.path, payload)

        refs_without_links = [
            ref.model_copy(
                update={
                    "content_hash": sha256_file(root / ref.path),
                    "producing_commit_hash": None,
                }
            )
            for ref in source_refs
        ]
        new_hash = compute_commit_hash(
            parent_hash=previous_hash,
            run_id=commit.run_id,
            candidate_id=commit.candidate_id,
            action_type=commit.action_type,
            payload=payload,
            artifact_refs=refs_without_links,
            timestamp=commit.timestamp,
            self_link_artifact_ids={ref.id for ref in refs_without_links},
        )
        linked_refs = [
            ref.model_copy(update={"producing_commit_hash": new_hash})
            for ref in refs_without_links
        ]
        updates.append((commit, new_hash, previous_hash, payload, linked_refs))
        prefix_refs.extend(linked_refs)
        previous_hash = new_hash

    with sqlite3.connect(ledger.path) as connection:
        connection.execute("DROP TRIGGER commits_no_update")
        connection.execute("DROP TRIGGER commits_no_delete")
        for commit, new_hash, parent_hash, payload, linked_refs in updates:
            connection.execute(
                """
                UPDATE commits
                SET commit_hash = ?, parent_hash = ?, payload_json = ?, artifact_refs_json = ?
                WHERE commit_hash = ?
                """,
                (
                    new_hash,
                    parent_hash,
                    canonical_json(payload),
                    canonical_json(linked_refs),
                    commit.commit_hash,
                ),
            )
    return previous_hash


def _rewrite_latest_refs(
    ledger: ResearchLedger,
    refs_without_links: list[Any],
    *,
    self_link_ids: set[str],
) -> str:
    latest = ledger.list_commits_read_only("run-replay-core")[-1]
    new_hash = compute_commit_hash(
        parent_hash=latest.parent_hash,
        run_id=latest.run_id,
        candidate_id=latest.candidate_id,
        action_type=latest.action_type,
        payload=latest.payload,
        artifact_refs=refs_without_links,
        timestamp=latest.timestamp,
        self_link_artifact_ids=self_link_ids,
    )
    linked_refs = [
        ref.model_copy(update={"producing_commit_hash": new_hash})
        if ref.id in self_link_ids
        else ref
        for ref in refs_without_links
    ]
    with sqlite3.connect(ledger.path) as connection:
        connection.execute("DROP TRIGGER commits_no_update")
        connection.execute("DROP TRIGGER commits_no_delete")
        connection.execute(
            """
            UPDATE commits
            SET commit_hash = ?, artifact_refs_json = ?
            WHERE commit_hash = ?
            """,
            (new_hash, canonical_json(linked_refs), latest.commit_hash),
        )
    return new_hash


def _rebuilt_manifest_payload(
    payload: dict[str, Any],
    prefix_refs: list[Any],
) -> dict[str, Any]:
    existing = {
        (entry["artifact_id"], entry["path"]): entry for entry in payload["artifacts"]
    }
    entries = []
    for ref in prefix_refs:
        entry = existing.get((ref.id, ref.path))
        if entry is None:
            entry = next(
                candidate
                for (artifact_id, _), candidate in existing.items()
                if artifact_id == ref.id
            )
        entries.append(
            {
                **entry,
                "artifact_id": ref.id,
                "artifact_type": ref.type.value,
                "path": ref.path,
                "content_hash": ref.content_hash,
                "producing_commit_hash": ref.producing_commit_hash,
                "metadata": ref.metadata,
            }
        )
    entries.sort(key=lambda entry: (entry["path"], entry["artifact_id"]))
    return {
        **payload,
        "artifacts": entries,
        "evidence_artifact_count": sum(entry["is_evidence"] for entry in entries),
        "presentation_artifact_count": sum(entry["is_presentation"] for entry in entries),
    }


def _build_kernel_binary() -> None:
    subprocess.run(
        [
            "cargo",
            "build",
            "--manifest-path",
            "rust-kernel/Cargo.toml",
            "--locked",
            "--offline",
        ],
        capture_output=True,
        check=True,
        text=True,
    )
