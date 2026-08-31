from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from factori.abstract_synthesis import run_abstract_synthesis
from factori.artifacts import ArtifactStore
from factori.draft_skeleton import run_draft_skeleton_generation
from factori.export_plan import prepare_export
from factori.final_audit import run_final_audit
from factori.final_paper import run_paper_assembly
from factori.hashing import sha256_file
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


def test_rust_replay_core_has_both_mode_parity_and_is_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = _run_pipeline_to_export(tmp_path)
    _build_kernel_binary()
    commits = ledger.list_commits_read_only("run-replay-core")
    tip = commits[-1].commit_hash
    ledger_path = tmp_path / "runs" / "run-replay-core" / "ledger.sqlite"
    ledger_before = ledger_path.read_bytes()
    artifact_hashes_before = {
        artifact.path: sha256_file(tmp_path / artifact.path)
        for commit in commits
        for artifact in commit.artifact_refs
    }
    replay_dir = tmp_path / "runs" / "run-replay-core" / "replay"

    responses = [
        verify_persisted_replay_core(
            "run-replay-core",
            tip,
            mode=mode,
            root=tmp_path,
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
        root=tmp_path,
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
        verify_persisted_replay_core("run-replay-core", tip, root=tmp_path)

    assert ledger_path.read_bytes() == ledger_before
    assert len(ledger.list_commits_read_only("run-replay-core")) == len(commits)
    assert ledger.list_commits_read_only("run-replay-core")[-1].commit_hash == tip
    assert {
        path: sha256_file(tmp_path / path) for path in artifact_hashes_before
    } == artifact_hashes_before
    assert not replay_dir.exists()


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
