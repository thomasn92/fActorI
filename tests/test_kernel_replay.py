from __future__ import annotations

import json
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from factori.abstract_synthesis import run_abstract_synthesis
from factori.artifacts import ArtifactStore
from factori.draft_skeleton import run_draft_skeleton_generation
from factori.export_plan import prepare_export
from factori.final_audit import run_final_audit
from factori.final_paper import run_paper_assembly
from factori.hashing import canonical_json, sha256_file
from factori.kernel_bridge import KernelBridgeError, verify_persisted_replay_core
from factori.ledger import ResearchLedger
from factori.manuscript_plan import run_manuscript_planning
from factori.research_object import build_research_object
from factori.schemas import (
    ConstraintSet,
    KernelMode,
    KernelReplayVerifyCoreResult,
    KernelResponseStatus,
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
    if mutation == "derived_path":
        artifact = latest.artifact_refs[0]
        old_path = root / artifact.path
        new_relative = f"runs/run-replay-core/replay/{old_path.name}"
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
    from factori.ledger import compute_commit_hash

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
