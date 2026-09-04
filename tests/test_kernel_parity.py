from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from factori.hashing import canonical_json, sha256_file, sha256_text
from factori.kernel_bridge import verify_persisted_autonomous_checkpoints
from factori.ledger import ResearchLedger
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AutonomousPaperRunStage,
    Candidate,
    Claim,
    ControllerActionType,
    KernelArtifactVerifyRequest,
    KernelClaimResolveRequest,
    KernelEvidenceClassifyRequest,
    KernelEvidenceClassifyResult,
    KernelEvidenceValidateBundleResult,
    KernelLeanEvidenceBundle,
    KernelLedgerVerifyRequest,
    KernelMode,
    KernelRequestEnvelope,
    KernelResponseEnvelope,
    KernelSyntheticEvidenceBundle,
    VerificationLabel,
)

FIXTURE = Path(__file__).parent / ".." / "rust-kernel" / "fixtures" / "canonical-json.json"
LEDGER_FIXTURE = (
    Path(__file__).parent / ".." / "rust-kernel" / "fixtures" / "ledger-commit-hashes.json"
)
KERNEL_BINARY = Path(__file__).parent / ".." / "rust-kernel" / "target" / "debug" / "factori-kernel"


def _write_checkpoint_chain(
    root: Path,
    *,
    run_id: str = "run-kernel-checkpoints",
    safety_gate_status: str = "passed",
    stage_status: str = "completed",
    stage_specs: tuple[tuple[str, str, str], ...] | None = None,
    controller_run_ids: tuple[str, ...] | None = None,
) -> tuple[ResearchLedger, object]:
    from factori.artifacts import ArtifactStore
    from factori.autonomous_paper_checkpoint import write_autonomous_paper_checkpoint

    store = ArtifactStore(root)
    store.init_run(run_id)
    ledger = ResearchLedger(root / "runs" / run_id / "ledger.sqlite")
    previous_hash: str | None = None
    result = None
    specs = stage_specs or tuple(
        (stage_name, safety_gate_status, stage_status)
        for stage_name in ("base_generation", "autonomous_loop")
    )
    for number, (stage_name, current_safety_gate_status, current_stage_status) in enumerate(
        specs, start=1
    ):
        relative = f"runs/{run_id}/reports/stage-output-{number:04d}.json"
        output_path = root / relative
        output_path.write_text(json.dumps({"stage": stage_name}), encoding="utf-8")
        stage = AutonomousPaperRunStage(
            stage_name=stage_name,
            stage_status=current_stage_status,
            started_at=f"2026-01-01T00:00:0{number}.000000Z",
            completed_at=f"2026-01-01T00:00:1{number}.000000Z",
            summary=f"checkpoint {number}",
        )
        result = write_autonomous_paper_checkpoint(
            run_id=run_id,
            controller_run_id=(
                controller_run_ids[number - 1]
                if controller_run_ids is not None
                else "controller-checkpoint-test"
            ),
            stage=stage,
            artifact_paths=[relative],
            safety_gate_status=current_safety_gate_status,
            release_status=None,
            input_hashes=(
                {} if previous_hash is None else {"previous_checkpoint": previous_hash}
            ),
            root=root,
            store=store,
            ledger=ledger,
        )
        previous_hash = result.checkpoint.checkpoint_hash
    assert result is not None
    return ledger, result


def _rewrite_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(canonical_json(payload) + "\n", encoding="utf-8")


def _reseal_latest_checkpoint_commit(
    root: Path,
    ledger: ResearchLedger,
    latest: Any,
) -> str:
    import sqlite3

    from factori.ledger import compute_commit_hash

    old_hash = latest.index_artifact.producing_commit_hash
    assert old_hash is not None
    commit = ledger.get_commit(old_hash)
    refs = [
        ref.model_copy(
            update={
                "content_hash": sha256_file(root / ref.path),
                "producing_commit_hash": None,
            }
        )
        for ref in commit.artifact_refs
    ]
    checkpoint_ref = next(
        ref
        for ref in refs
        if ref.id.startswith("autonomous-paper-checkpoint-")
        and "-index-" not in ref.id
    )
    checkpoint_payload = json.loads((root / checkpoint_ref.path).read_text(encoding="utf-8"))
    commit_payload = {**commit.payload, "checkpoint_hash": checkpoint_payload["checkpoint_hash"]}
    self_link_ids = {ref.id for ref in refs}
    new_hash = compute_commit_hash(
        parent_hash=commit.parent_hash,
        run_id=commit.run_id,
        candidate_id=commit.candidate_id,
        action_type=commit.action_type,
        payload=commit_payload,
        artifact_refs=refs,
        timestamp=commit.timestamp,
        self_link_artifact_ids=self_link_ids,
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
                canonical_json(commit_payload),
                canonical_json(linked_refs),
                old_hash,
            ),
        )
    return new_hash


@pytest.mark.parametrize(
    "mode", [KernelMode.STRICT_PRODUCTION, KernelMode.DEVELOPMENT_COMPATIBILITY]
)
def test_rust_checkpoint_verification_accepts_writer_produced_chain(
    tmp_path: Path, mode: KernelMode
) -> None:
    _build_kernel_binary()
    ledger, latest = _write_checkpoint_chain(tmp_path)

    response = verify_persisted_autonomous_checkpoints(
        "run-kernel-checkpoints",
        index_artifact_id=latest.index_artifact.id,
        index_producing_commit_hash=latest.index_artifact.producing_commit_hash or "",
        mode=mode,
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status.value == "accepted"
    result = response.result
    assert result.checkpoint_count == 2
    expected_hashes = [
        json.loads((tmp_path / relative).read_text(encoding="utf-8"))["checkpoint_hash"]
        for relative in latest.index.checkpoints
    ]
    assert result.validated_checkpoint_hashes == expected_hashes
    assert result.latest_completed_stage == "autonomous_loop"
    assert result.validated_output_count == 2
    assert result.checkpoint_chain_valid is True
    assert result.resume_allowed is True
    assert result.authority_granted is False


@pytest.mark.parametrize(
    "mode", [KernelMode.STRICT_PRODUCTION, KernelMode.DEVELOPMENT_COMPATIBILITY]
)
def test_rust_checkpoint_verification_accepts_terminal_failed_chain_without_resume_authority(
    tmp_path: Path, mode: KernelMode
) -> None:
    _build_kernel_binary()
    _, latest = _write_checkpoint_chain(
        tmp_path,
        run_id="run-kernel-failed-checkpoints",
        stage_specs=(
            ("base_generation", "passed", "completed"),
            ("autonomous_loop", "failed", "blocked"),
        ),
    )

    response = verify_persisted_autonomous_checkpoints(
        "run-kernel-failed-checkpoints",
        index_artifact_id=latest.index_artifact.id,
        index_producing_commit_hash=latest.index_artifact.producing_commit_hash or "",
        mode=mode,
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status.value == "accepted"
    assert response.result.resume_allowed is False
    assert response.result.authority_granted is False
    assert [diagnostic.code for diagnostic in response.diagnostics] == [
        "checkpoint_not_reusable"
    ]


def test_rust_checkpoint_verification_accepts_warning_chain(tmp_path: Path) -> None:
    _build_kernel_binary()
    _, latest = _write_checkpoint_chain(
        tmp_path,
        run_id="run-kernel-warning-checkpoints",
        stage_specs=(
            ("base_generation", "passed_with_warnings", "completed_with_warnings"),
            ("autonomous_loop", "passed_with_warnings", "completed_with_warnings"),
        ),
    )

    response = verify_persisted_autonomous_checkpoints(
        "run-kernel-warning-checkpoints",
        index_artifact_id=latest.index_artifact.id,
        index_producing_commit_hash=latest.index_artifact.producing_commit_hash or "",
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status.value == "accepted"
    assert response.result.resume_allowed is True
    assert response.result.authority_granted is False
    assert response.diagnostics == []


@pytest.mark.parametrize(
    "mode", [KernelMode.STRICT_PRODUCTION, KernelMode.DEVELOPMENT_COMPATIBILITY]
)
def test_rust_checkpoint_verification_rejects_failed_then_reusable_chain(
    tmp_path: Path, mode: KernelMode
) -> None:
    _build_kernel_binary()
    ledger, latest = _write_checkpoint_chain(
        tmp_path,
        run_id="run-kernel-failed-then-reusable",
        stage_specs=(
            ("base_generation", "failed", "blocked"),
            ("autonomous_loop", "passed", "completed"),
        ),
    )
    producing_commit_hash = _reseal_latest_checkpoint_commit(tmp_path, ledger, latest)

    response = verify_persisted_autonomous_checkpoints(
        "run-kernel-failed-then-reusable",
        index_artifact_id=latest.index_artifact.id,
        index_producing_commit_hash=producing_commit_hash,
        mode=mode,
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status.value == "rejected"
    assert response.diagnostics[0].code == "checkpoint_chain_mismatch"


def test_rust_checkpoint_verification_accepts_resumed_controller_chain(tmp_path: Path) -> None:
    _build_kernel_binary()
    ledger, latest = _write_checkpoint_chain(
        tmp_path,
        run_id="run-kernel-resumed-checkpoints",
        stage_specs=(
            ("base_generation", "passed", "completed"),
            ("autonomous_loop", "passed", "completed"),
            ("final_release_bundle_assembly", "passed", "completed"),
            ("final_release_bundle_assembly", "passed", "reused"),
        ),
        controller_run_ids=(
            "controller-initial",
            "controller-initial",
            "controller-resumed",
            "controller-resumed",
        ),
    )

    response = verify_persisted_autonomous_checkpoints(
        "run-kernel-resumed-checkpoints",
        index_artifact_id=latest.index_artifact.id,
        index_producing_commit_hash=latest.index_artifact.producing_commit_hash or "",
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status.value == "accepted"
    assert response.result.checkpoint_count == 4
    assert response.result.latest_completed_stage == "final_release_bundle_assembly"
    assert response.result.resume_allowed is True
    checkpoint_commits = [
        commit
        for commit in ledger.list_commits("run-kernel-resumed-checkpoints")
        if commit.action_type == ControllerActionType.AUTONOMOUS_PAPER_CHECKPOINT_WRITTEN
    ]
    assert [commit.payload["controller_run_id"] for commit in checkpoint_commits] == [
        "controller-initial",
        "controller-initial",
        "controller-resumed",
        "controller-resumed",
    ]


def test_rust_checkpoint_verification_rejects_stale_index_and_mutated_output(
    tmp_path: Path,
) -> None:
    _build_kernel_binary()
    ledger, latest = _write_checkpoint_chain(tmp_path)
    checkpoint_commits = [
        commit
        for commit in ledger.list_commits("run-kernel-checkpoints")
        if commit.action_type == ControllerActionType.AUTONOMOUS_PAPER_CHECKPOINT_WRITTEN
    ]
    first_index = next(
        artifact
        for artifact in checkpoint_commits[0].artifact_refs
        if artifact.id.startswith("autonomous-paper-checkpoint-index-")
    )
    stale = verify_persisted_autonomous_checkpoints(
        "run-kernel-checkpoints",
        index_artifact_id=first_index.id,
        index_producing_commit_hash=checkpoint_commits[0].commit_hash,
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )
    assert stale.status.value == "rejected"
    assert stale.diagnostics[0].code == "checkpoint_not_latest"

    (tmp_path / "runs/run-kernel-checkpoints/reports/stage-output-0002.json").write_text(
        "mutated", encoding="utf-8"
    )
    mutated = verify_persisted_autonomous_checkpoints(
        "run-kernel-checkpoints",
        index_artifact_id=latest.index_artifact.id,
        index_producing_commit_hash=latest.index_artifact.producing_commit_hash or "",
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )
    assert mutated.status.value == "rejected"
    assert mutated.diagnostics[0].code == "checkpoint_output_hash_mismatch"


@pytest.mark.parametrize("mutation", ["missing", "symlink"])
def test_rust_checkpoint_verification_rejects_missing_or_symlinked_output(
    tmp_path: Path,
    mutation: str,
) -> None:
    _build_kernel_binary()
    _, latest = _write_checkpoint_chain(tmp_path)
    output = tmp_path / "runs/run-kernel-checkpoints/reports/stage-output-0002.json"
    output.unlink()
    if mutation == "symlink":
        outside = tmp_path / "outside-checkpoint-output.json"
        outside.write_text("outside", encoding="utf-8")
        output.symlink_to(outside)

    response = verify_persisted_autonomous_checkpoints(
        "run-kernel-checkpoints",
        index_artifact_id=latest.index_artifact.id,
        index_producing_commit_hash=latest.index_artifact.producing_commit_hash or "",
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status.value == "rejected"
    assert response.diagnostics[0].code == (
        "checkpoint_output_missing" if mutation == "missing" else "checkpoint_output_path_invalid"
    )


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("self_hash", "checkpoint_hash_mismatch"),
        ("predecessor", "checkpoint_chain_mismatch"),
        ("ledger_tip", "checkpoint_ledger_mismatch"),
        ("protocol", "checkpoint_protocol_mismatch"),
        ("authority", "checkpoint_authority_violation"),
        ("output_hash", "checkpoint_output_hash_mismatch"),
        ("output_path", "checkpoint_output_path_invalid"),
        ("unexpected_field", "checkpoint_record_invalid"),
    ],
)
def test_rust_checkpoint_verification_rejects_resealed_record_mutations(
    tmp_path: Path,
    mutation: str,
    expected_code: str,
) -> None:
    _build_kernel_binary()
    ledger, latest = _write_checkpoint_chain(tmp_path)
    checkpoint_path = tmp_path / latest.index.checkpoints[-1]
    checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    if mutation == "self_hash":
        checkpoint["checkpoint_hash"] = "f" * 64
    elif mutation == "predecessor":
        checkpoint["input_hashes"] = {"previous_checkpoint": "f" * 64}
    elif mutation == "ledger_tip":
        checkpoint["ledger_tip_hash_optional"] = "f" * 64
    elif mutation == "protocol":
        checkpoint["protocol_version"] = "0.84.0"
    elif mutation == "authority":
        checkpoint["publication_ready"] = True
    elif mutation == "output_hash":
        checkpoint["output_hashes"][checkpoint["stage_artifact_paths"][0]] = "f" * 64
    elif mutation == "output_path":
        checkpoint["stage_artifact_paths"] = ["../outside.json"]
        checkpoint["output_hashes"] = {"../outside.json": "f" * 64}
    else:
        checkpoint["unexpected"] = True
    if mutation not in {"self_hash", "unexpected_field"}:
        checkpoint["checkpoint_hash"] = sha256_text(
            canonical_json(
                {key: value for key, value in checkpoint.items() if key != "checkpoint_hash"}
            )
        )
    _rewrite_json(checkpoint_path, checkpoint)
    producing_commit_hash = _reseal_latest_checkpoint_commit(tmp_path, ledger, latest)

    response = verify_persisted_autonomous_checkpoints(
        "run-kernel-checkpoints",
        index_artifact_id=latest.index_artifact.id,
        index_producing_commit_hash=producing_commit_hash,
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status.value == "rejected"
    assert response.diagnostics[0].code == expected_code


@pytest.mark.parametrize("mutation", ["count", "order", "path", "unexpected_field"])
def test_rust_checkpoint_verification_rejects_resealed_index_mutations(
    tmp_path: Path,
    mutation: str,
) -> None:
    _build_kernel_binary()
    ledger, latest = _write_checkpoint_chain(tmp_path)
    index_path = tmp_path / latest.index_artifact.path
    index = json.loads(index_path.read_text(encoding="utf-8"))
    if mutation == "count":
        index["checkpoint_count"] = 1
    elif mutation == "order":
        index["checkpoints"] = list(reversed(index["checkpoints"]))
    elif mutation == "path":
        index["checkpoints"][0] = "runs/run-kernel-checkpoints/reports/wrong.json"
    else:
        index["unexpected"] = True
    _rewrite_json(index_path, index)
    producing_commit_hash = _reseal_latest_checkpoint_commit(tmp_path, ledger, latest)

    response = verify_persisted_autonomous_checkpoints(
        "run-kernel-checkpoints",
        index_artifact_id=latest.index_artifact.id,
        index_producing_commit_hash=producing_commit_hash,
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status.value == "rejected"
    assert response.diagnostics[0].code == "checkpoint_index_invalid"


def test_checkpoint_bridge_rejects_accepted_response_with_wrong_hash_inventory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from factori.kernel_bridge import KernelBridgeError

    _, latest = _write_checkpoint_chain(tmp_path)
    producing_commit_hash = latest.index_artifact.producing_commit_hash or ""
    response = KernelResponseEnvelope.model_validate(
        {
            "protocol_version": "0.89.0",
            "kernel_version": "0.1.0-dev",
            "request_id": (
                "checkpoint-verify-run-kernel-checkpoints-"
                f"{latest.index_artifact.id}-{producing_commit_hash[:12]}"
            ),
            "operation": "checkpoint.verify",
            "mode": "StrictProduction",
            "status": "accepted",
            "result": {
                "run_id": "run-kernel-checkpoints",
                "checkpoint_index_artifact_id": latest.index_artifact.id,
                "checkpoint_index_producing_commit_hash": producing_commit_hash,
                "checkpoint_count": 2,
                "validated_checkpoint_hashes": ["d" * 64, "e" * 64],
                "latest_checkpoint_hash": "e" * 64,
                "latest_completed_stage": "autonomous_loop",
                "validated_output_count": 2,
                "checkpoint_chain_valid": True,
                "resume_allowed": True,
                "authority_granted": False,
            },
            "diagnostics": [],
            "mutation_performed": False,
        }
    )
    monkeypatch.setattr("factori.kernel_bridge._invoke_kernel", lambda *args, **kwargs: response)

    with pytest.raises(KernelBridgeError, match="does not match request"):
        verify_persisted_autonomous_checkpoints(
            "run-kernel-checkpoints",
            index_artifact_id=latest.index_artifact.id,
            index_producing_commit_hash=producing_commit_hash,
            root=tmp_path,
        )


def test_python_canonical_json_matches_kernel_golden_corpus() -> None:
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))

    for case in cases:
        rendered = canonical_json(case["value"])
        assert rendered == case["canonical_json"], case["name"]
        assert sha256_text(rendered) == case["sha256"], case["name"]


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


def test_rust_kernel_cli_matches_python_canonical_json_corpus() -> None:
    _build_kernel_binary()
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))

    for case in cases:
        request = {
            "protocol_version": "0.89.0",
            "request_id": case["name"],
            "operation": "hash.canonical_json",
            "mode": "DevelopmentCompatibility",
            "payload": {"value": case["value"]},
        }
        completed = subprocess.run(
            [str(KERNEL_BINARY)],
            input=json.dumps(request) + "\n",
            capture_output=True,
            check=True,
            text=True,
        )
        response = json.loads(completed.stdout)
        KernelResponseEnvelope.model_validate(response)
        assert response["status"] == "accepted", case["name"]
        assert response["result"]["canonical_json"] == case["canonical_json"], case["name"]
        assert response["result"]["sha256"] == case["sha256"], case["name"]


def test_rust_protocol_validation_rejects_every_invalid_python_envelope() -> None:
    _build_kernel_binary()
    invalid_instances = [
        (
            "KernelRequestEnvelope",
            KernelRequestEnvelope,
            {
                "protocol_version": "0.89.0",
                "request_id": "",
                "operation": "hash.canonical_json",
                "mode": "DevelopmentCompatibility",
                "payload": {"value": 1},
            },
        ),
        (
            "KernelRequestEnvelope",
            KernelRequestEnvelope,
            {
                "protocol_version": "0.89.0",
                "request_id": "request-invalid-payload",
                "operation": "hash.canonical_json",
                "mode": "DevelopmentCompatibility",
                "payload": {},
            },
        ),
        (
            "KernelRequestEnvelope",
            KernelRequestEnvelope,
            {
                "protocol_version": "0.89.0",
                "request_id": "request-invalid-commit-hash",
                "operation": "ledger.verify",
                "mode": "DevelopmentCompatibility",
                "payload": {
                    "run_id": "run-1",
                    "commits": [
                        {
                            "commit_hash": "bad",
                            "parent_hash": None,
                            "run_id": "run-1",
                            "candidate_id": None,
                            "action_type": "InitRun",
                            "payload": {},
                            "artifact_refs": [],
                            "timestamp": "2026-01-01T00:00:00Z",
                        }
                    ],
                },
            },
        ),
        (
            "KernelRequestEnvelope",
            KernelRequestEnvelope,
            {
                "protocol_version": "0.89.0",
                "request_id": "request-invalid-checkpoint-locator",
                "operation": "checkpoint.verify",
                "mode": "StrictProduction",
                "payload": {
                    "run_id": "run-1",
                    "index": {
                        "artifact_id": "autonomous-paper-checkpoint-index-0001",
                        "producing_commit_hash": "not-a-hash",
                    },
                },
            },
        ),
        (
            "KernelResponseEnvelope",
            KernelResponseEnvelope,
            {
                "protocol_version": "0.89.0",
                "kernel_version": "0.1.0-dev",
                "request_id": "response-invalid-diagnostic",
                "operation": "hash.canonical_json",
                "mode": "DevelopmentCompatibility",
                "status": "rejected",
                "result": {},
                "diagnostics": [{"code": "", "message": "", "path": None}],
                "mutation_performed": False,
            },
        ),
        (
            "KernelResponseEnvelope",
            KernelResponseEnvelope,
            {
                "protocol_version": "0.89.0",
                "kernel_version": "0.1.0-dev",
                "request_id": "response-zero-replay-count",
                "operation": "replay.verify_core",
                "mode": "DevelopmentCompatibility",
                "status": "accepted",
                "result": {
                    "run_id": "run-1",
                    "ledger_tip_hash": "0" * 64,
                    "ledger_commit_count": 0,
                    "ledger_artifact_count": 0,
                    "ledger_artifact_inventory_hash": "0" * 64,
                    "required_outputs_checked": 11,
                    "manifest_artifact_id": "artifact-manifest",
                    "manifest_producing_commit_hash": "0" * 64,
                    "manifest_entry_count": 1,
                    "manifest_inventory_hash": "0" * 64,
                    "claims_checked": 0,
                    "claim_evidence_links_checked": 0,
                    "core_replay_valid": True,
                    "ledger_snapshot_stable": True,
                    "authority_boundary_valid": True,
                    "authority_granted": False,
                },
                "diagnostics": [],
                "mutation_performed": False,
            },
        ),
        (
            "KernelResponseEnvelope",
            KernelResponseEnvelope,
            {
                "protocol_version": "0.89.0",
                "kernel_version": "0.1.0-dev",
                "request_id": "response-coerced-count",
                "operation": "ledger.verify",
                "mode": "DevelopmentCompatibility",
                "status": "accepted",
                "result": {"valid": True, "run_id": "run-1", "commit_count": 1.0},
                "diagnostics": [],
                "mutation_performed": False,
            },
        ),
        (
            "KernelResponseEnvelope",
            KernelResponseEnvelope,
            {
                "protocol_version": "0.89.0",
                "kernel_version": "0.1.0-dev",
                "request_id": "response-coerced-mutation",
                "operation": "ledger.verify",
                "mode": "DevelopmentCompatibility",
                "status": "accepted",
                "result": {"valid": True, "run_id": "run-1", "commit_count": 1},
                "diagnostics": [],
                "mutation_performed": 0,
            },
        ),
        (
            "KernelResponseEnvelope",
            KernelResponseEnvelope,
            {
                "protocol_version": "0.89.0",
                "kernel_version": "0.1.0-dev",
                "request_id": "response-coerced-valid",
                "operation": "ledger.verify",
                "mode": "DevelopmentCompatibility",
                "status": "accepted",
                "result": {"valid": 1, "run_id": "run-1", "commit_count": 1},
                "diagnostics": [],
                "mutation_performed": False,
            },
        ),
        (
            "KernelResponseEnvelope",
            KernelResponseEnvelope,
            {
                "protocol_version": "0.89.0",
                "kernel_version": "0.1.0-dev",
                "request_id": "response-empty-run-id",
                "operation": "ledger.verify",
                "mode": "DevelopmentCompatibility",
                "status": "accepted",
                "result": {"valid": True, "run_id": "", "commit_count": 1},
                "diagnostics": [],
                "mutation_performed": False,
            },
        ),
        (
            "KernelResponseEnvelope",
            KernelResponseEnvelope,
            {
                "protocol_version": "0.89.0",
                "kernel_version": "0.1.0-dev",
                "request_id": "response-invalid-result",
                "operation": "hash.canonical_json",
                "mode": "DevelopmentCompatibility",
                "status": "accepted",
                "result": {},
                "diagnostics": [],
                "mutation_performed": False,
            },
        ),
    ]

    for protocol_name, model_type, instance in invalid_instances:
        try:
            model_type.model_validate(instance)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"invalid Python fixture was accepted: {protocol_name}")
        request = {
            "protocol_version": "0.89.0",
            "request_id": f"validate-{protocol_name}",
            "operation": "protocol.validate",
            "mode": "DevelopmentCompatibility",
            "payload": {"protocol_name": protocol_name, "instance": instance},
        }
        completed = subprocess.run(
            [str(KERNEL_BINARY)],
            input=json.dumps(request) + "\n",
            capture_output=True,
            check=True,
            text=True,
        )
        assert json.loads(completed.stdout)["status"] == "rejected"


def test_rust_protocol_validation_accepts_optional_candidate_kind_omission() -> None:
    _build_kernel_binary()
    instance = {
        "protocol_version": "0.89.0",
        "kernel_version": "0.1.0-dev",
        "request_id": "response-context-without-candidate-kind",
        "operation": "evidence.classify",
        "mode": "DevelopmentCompatibility",
        "status": "accepted",
        "result": {
            "run_id": "run-1",
            "artifact_id": "artifact-1",
            "authority_class": "Context",
            "compatibility_only": False,
            "authority_granted": False,
        },
        "diagnostics": [],
        "mutation_performed": False,
    }
    KernelResponseEnvelope.model_validate(instance)
    request = {
        "protocol_version": "0.89.0",
        "request_id": "validate-optional-candidate-kind",
        "operation": "protocol.validate",
        "mode": "DevelopmentCompatibility",
        "payload": {"protocol_name": "KernelResponseEnvelope", "instance": instance},
    }

    completed = subprocess.run(
        [str(KERNEL_BINARY)],
        input=json.dumps(request) + "\n",
        capture_output=True,
        check=True,
        text=True,
    )

    assert json.loads(completed.stdout)["status"] == "accepted"


def test_python_commit_hash_payload_matches_kernel_golden_corpus() -> None:
    cases = json.loads(LEDGER_FIXTURE.read_text(encoding="utf-8"))

    for case in cases:
        rendered = canonical_json(case["value"])
        assert rendered == case["canonical_json"], case["name"]
        assert sha256_text(rendered) == case["sha256"], case["name"]


def _run_kernel(
    request: dict[str, object],
    *,
    root: Path | None = None,
) -> dict[str, object]:
    command = [str(KERNEL_BINARY)]
    if root is not None:
        command.extend(["--root", str(root)])
    completed = subprocess.run(
        command,
        input=json.dumps(request) + "\n",
        capture_output=True,
        check=True,
        text=True,
    )
    return json.loads(completed.stdout)


def test_rust_ledger_verification_matches_python_commit_chain(tmp_path: Path) -> None:
    _build_kernel_binary()
    ledger = ResearchLedger(tmp_path / "ledger.sqlite")
    root = ledger.append_commit(
        run_id="run-kernel-ledger",
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": "run-kernel-ledger"},
        timestamp="2026-01-01T00:00:00.000000Z",
    )
    tip = ledger.append_commit(
        run_id="run-kernel-ledger",
        parent_hash=root.commit_hash,
        action_type=ControllerActionType.WRITE_ARTIFACT,
        payload={"artifact_id": "artifact-001"},
        timestamp="2026-01-01T00:00:01.000000Z",
    )
    commits = [
        commit.model_dump(mode="json") for commit in ledger.list_commits("run-kernel-ledger")
    ]
    request = KernelLedgerVerifyRequest(
        protocol_version="0.89.0",
        request_id="ledger-verify-test",
        operation="ledger.verify",
        mode="DevelopmentCompatibility",
        payload={"run_id": "run-kernel-ledger", "commits": commits},
    )

    response = _run_kernel(request.model_dump(mode="json"))

    assert response["status"] == "accepted"
    assert response["mutation_performed"] is False
    assert response["result"] == {
        "valid": True,
        "run_id": "run-kernel-ledger",
        "commit_count": 2,
        "root_hash": root.commit_hash,
        "tip_hash": tip.commit_hash,
    }


def test_rust_ledger_verification_rejects_tampered_commit_payload(tmp_path: Path) -> None:
    _build_kernel_binary()
    ledger = ResearchLedger(tmp_path / "ledger.sqlite")
    commit = ledger.append_commit(
        run_id="run-kernel-ledger",
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": "run-kernel-ledger"},
        timestamp="2026-01-01T00:00:00.000000Z",
    )
    tampered = commit.model_dump(mode="json")
    tampered["payload"] = {"run_id": "tampered"}
    request = {
        "protocol_version": "0.89.0",
        "request_id": "ledger-verify-tampered",
        "operation": "ledger.verify",
        "mode": "DevelopmentCompatibility",
        "payload": {"run_id": "run-kernel-ledger", "commits": [tampered]},
    }

    response = _run_kernel(request)

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "ledger_hash_mismatch"


def test_rust_protocol_validation_rejects_malformed_nested_ledger_request() -> None:
    _build_kernel_binary()
    malformed_request = {
        "protocol_version": "0.89.0",
        "request_id": "nested-ledger-invalid",
        "operation": "ledger.verify",
        "mode": "DevelopmentCompatibility",
        "payload": {"run_id": "run-kernel-ledger", "commits": [{"commit_hash": "bad"}]},
    }
    response = _run_kernel(
        {
            "protocol_version": "0.89.0",
            "request_id": "validate-nested-ledger",
            "operation": "protocol.validate",
            "mode": "DevelopmentCompatibility",
            "payload": {
                "protocol_name": "KernelRequestEnvelope",
                "instance": malformed_request,
            },
        }
    )
    assert response["status"] == "rejected"
    with pytest.raises(ValidationError):
        KernelRequestEnvelope.model_validate(malformed_request)


def test_rust_protocol_validation_rejects_malformed_nested_bundle_request() -> None:
    _build_kernel_binary()
    malformed_request = {
        "protocol_version": "0.89.0",
        "request_id": "nested-bundle-invalid",
        "operation": "evidence.validate_bundle",
        "mode": "StrictProduction",
        "payload": {},
    }

    response = _run_kernel(
        {
            "protocol_version": "0.89.0",
            "request_id": "validate-nested-bundle",
            "operation": "protocol.validate",
            "mode": "StrictProduction",
            "payload": {
                "protocol_name": "KernelRequestEnvelope",
                "instance": malformed_request,
            },
        }
    )

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "protocol_invalid"
    with pytest.raises(ValidationError):
        KernelRequestEnvelope.model_validate(malformed_request)


def test_rust_protocol_validation_only_checks_nested_ledger_shape(
    tmp_path: Path,
) -> None:
    _build_kernel_binary()
    ledger = ResearchLedger(tmp_path / "ledger.sqlite")
    commit = ledger.append_commit(
        run_id="run-kernel-ledger",
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": "run-kernel-ledger"},
        timestamp="2026-01-01T00:00:00.000000Z",
    )
    tampered = commit.model_dump(mode="json")
    tampered["payload"] = {"run_id": "tampered"}
    response = _run_kernel(
        {
            "protocol_version": "0.89.0",
            "request_id": "validate-nested-ledger-semantic",
            "operation": "protocol.validate",
            "mode": "DevelopmentCompatibility",
            "payload": {
                "protocol_name": "KernelRequestEnvelope",
                "instance": {
                    "protocol_version": "0.89.0",
                    "request_id": "nested-ledger-semantic",
                    "operation": "ledger.verify",
                    "mode": "DevelopmentCompatibility",
                    "payload": {"run_id": "run-kernel-ledger", "commits": [tampered]},
                },
            },
        }
    )
    assert response["status"] == "accepted"
    assert response["result"] == {
        "valid": True,
        "protocol_name": "KernelRequestEnvelope",
    }


def test_rust_ledger_verification_rejects_forked_or_non_tip_append(tmp_path: Path) -> None:
    _build_kernel_binary()
    ledger = ResearchLedger(tmp_path / "ledger.sqlite")
    root = ledger.append_commit(
        run_id="run-kernel-ledger",
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": "run-kernel-ledger"},
        timestamp="2026-01-01T00:00:00.000000Z",
    )
    tip = ledger.append_commit(
        run_id="run-kernel-ledger",
        parent_hash=root.commit_hash,
        action_type=ControllerActionType.WRITE_ARTIFACT,
        payload={"artifact_id": "artifact-001"},
        timestamp="2026-01-01T00:00:01.000000Z",
    )
    fork = ledger.append_commit(
        run_id="run-kernel-ledger",
        parent_hash=root.commit_hash,
        action_type=ControllerActionType.WRITE_ARTIFACT,
        payload={"artifact_id": "artifact-002"},
        timestamp="2026-01-01T00:00:02.000000Z",
    )
    assert fork.parent_hash == root.commit_hash
    response = _run_kernel(
        KernelLedgerVerifyRequest(
            protocol_version="0.89.0",
            request_id="ledger-forked",
            operation="ledger.verify",
            mode="DevelopmentCompatibility",
            payload={
                "run_id": "run-kernel-ledger",
                "commits": [
                    commit.model_dump(mode="json")
                    for commit in ledger.list_commits("run-kernel-ledger")
                ],
            },
        ).model_dump(mode="json")
    )
    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "ledger_non_tip_append"
    assert tip.commit_hash != fork.commit_hash


def test_rust_ledger_verification_accepts_python_defaulted_fields(tmp_path: Path) -> None:
    _build_kernel_binary()
    ledger = ResearchLedger(tmp_path / "ledger.sqlite")
    root = ledger.append_commit(
        run_id="run-kernel-ledger",
        action_type=ControllerActionType.INIT_RUN,
        payload={},
        timestamp="2026-01-01T00:00:00.000000Z",
    )
    raw = root.model_dump(mode="json")
    raw.pop("payload")
    raw.pop("artifact_refs")
    response = _run_kernel(
        {
            "protocol_version": "0.89.0",
            "request_id": "ledger-defaults",
            "operation": "ledger.verify",
            "mode": "DevelopmentCompatibility",
            "payload": {"run_id": "run-kernel-ledger", "commits": [raw]},
        }
    )
    assert response["status"] == "accepted"
    assert response["result"]["root_hash"] == root.commit_hash


def test_rust_ledger_verification_rejects_duplicate_artifact_ids(tmp_path: Path) -> None:
    _build_kernel_binary()
    ledger = ResearchLedger(tmp_path / "ledger.sqlite")
    duplicate_ref = ArtifactRef(
        id="artifact-duplicate",
        type=ArtifactType.REPORT,
        path="reports/one.json",
        content_hash="0" * 64,
    )
    commit = ledger.append_commit(
        run_id="run-kernel-ledger",
        action_type=ControllerActionType.INIT_RUN,
        payload={},
        artifact_refs=[duplicate_ref, duplicate_ref],
        timestamp="2026-01-01T00:00:00.000000Z",
    )
    response = _run_kernel(
        {
            "protocol_version": "0.89.0",
            "request_id": "ledger-duplicate-artifacts",
            "operation": "ledger.verify",
            "mode": "DevelopmentCompatibility",
            "payload": {"run_id": "run-kernel-ledger", "commits": [commit.model_dump(mode="json")]},
        }
    )
    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "ledger_artifact_duplicate"


def test_rust_ledger_verification_rejects_artifact_with_wrong_producer(tmp_path: Path) -> None:
    _build_kernel_binary()
    ledger = ResearchLedger(tmp_path / "ledger.sqlite")
    root_ref = ArtifactRef(
        id="artifact-root",
        type=ArtifactType.REPORT,
        path="reports/root.json",
        content_hash="0" * 64,
    )
    root = ledger.append_commit(
        run_id="run-kernel-ledger",
        action_type=ControllerActionType.INIT_RUN,
        payload={},
        artifact_refs=[root_ref],
        timestamp="2026-01-01T00:00:00.000000Z",
    )
    forged_ref = ArtifactRef(
        id="artifact-forged",
        type=ArtifactType.REPORT,
        path="reports/forged.json",
        content_hash="1" * 64,
        producing_commit_hash=root.commit_hash,
    )
    ledger.append_commit(
        run_id="run-kernel-ledger",
        parent_hash=root.commit_hash,
        action_type=ControllerActionType.WRITE_ARTIFACT,
        payload={},
        artifact_refs=[forged_ref],
        timestamp="2026-01-01T00:00:01.000000Z",
    )
    response = _run_kernel(
        KernelLedgerVerifyRequest(
            protocol_version="0.89.0",
            request_id="ledger-wrong-producer",
            operation="ledger.verify",
            mode="DevelopmentCompatibility",
            payload={
                "run_id": "run-kernel-ledger",
                "commits": [
                    commit.model_dump(mode="json")
                    for commit in ledger.list_commits("run-kernel-ledger")
                ],
            },
        ).model_dump(mode="json")
    )
    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "ledger_artifact_commit_mismatch"


def test_persisted_ledger_bridge_reads_sqlite_without_mutating_it(tmp_path: Path) -> None:
    _build_kernel_binary()
    run_id = "run-kernel-bridge"
    ledger_path = tmp_path / "runs" / run_id / "ledger.sqlite"
    ledger = ResearchLedger(ledger_path)
    ledger.append_commit(
        run_id=run_id,
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": run_id},
        timestamp="2026-01-01T00:00:00.000000Z",
    )
    before = sha256_file(ledger_path)
    from factori.kernel_bridge import verify_persisted_ledger

    response = verify_persisted_ledger(
        run_id,
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )
    assert response.status.value == "accepted"
    assert response.result.commit_count == 1
    assert sha256_file(ledger_path) == before


def test_persisted_ledger_bridge_rejects_unsafe_run_ids(tmp_path: Path) -> None:
    from factori.kernel_bridge import KernelBridgeError, verify_persisted_ledger

    with pytest.raises(KernelBridgeError, match="unsafe run id"):
        verify_persisted_ledger("../outside", root=tmp_path, kernel_binary=KERNEL_BINARY)


def test_persisted_ledger_bridge_agrees_that_empty_ledgers_are_valid_warnings(
    tmp_path: Path,
) -> None:
    _build_kernel_binary()
    run_id = "run-kernel-empty"
    ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    from factori.kernel_bridge import verify_persisted_ledger

    response = verify_persisted_ledger(
        run_id,
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )
    assert response.status.value == "accepted"
    assert response.result.commit_count == 0
    assert response.result.root_hash is None
    assert response.result.tip_hash is None


def test_persisted_ledger_bridge_wraps_corrupt_sqlite_errors(tmp_path: Path) -> None:
    from factori.kernel_bridge import KernelBridgeError, verify_persisted_ledger

    run_id = "run-kernel-corrupt"
    ledger_path = tmp_path / "runs" / run_id / "ledger.sqlite"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_bytes(b"not a sqlite database")

    with pytest.raises(KernelBridgeError, match="could not read persisted ledger"):
        verify_persisted_ledger(run_id, root=tmp_path, kernel_binary=KERNEL_BINARY)


def test_persisted_ledger_bridge_rejects_symlinked_ledger_files(tmp_path: Path) -> None:
    from factori.kernel_bridge import KernelBridgeError, verify_persisted_ledger

    outside_ledger = tmp_path / "outside.sqlite"
    ResearchLedger(outside_ledger)
    run_id = "run-kernel-symlink"
    ledger_path = tmp_path / "runs" / run_id / "ledger.sqlite"
    ledger_path.parent.mkdir(parents=True)
    ledger_path.symlink_to(outside_ledger)

    with pytest.raises(KernelBridgeError, match="unsafe ledger path"):
        verify_persisted_ledger(run_id, root=tmp_path, kernel_binary=KERNEL_BINARY)


def _persist_kernel_artifact_fixture(tmp_path: Path) -> tuple[str, ArtifactRef]:
    from factori.artifacts import ArtifactStore

    run_id = "run-kernel-artifact"
    artifact = ArtifactStore(tmp_path).write_bytes(
        run_id=run_id,
        artifact_id="artifact-001",
        artifact_type=ArtifactType.REPORT,
        content=b'{"value": 1}\n',
        extension="json",
        format_label="json",
    )
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    ledger.append_commit(
        run_id=run_id,
        action_type=ControllerActionType.WRITE_ARTIFACT,
        payload={"artifact_id": artifact.id},
        artifact_refs=[artifact],
        timestamp="2026-01-01T00:00:00.000000Z",
    )
    linked = ledger.list_commits(run_id)[0].artifact_refs[0]
    return run_id, linked


def test_persisted_artifact_bridge_verifies_bytes_and_producer_link(tmp_path: Path) -> None:
    _build_kernel_binary()
    run_id, artifact = _persist_kernel_artifact_fixture(tmp_path)
    from factori.kernel_bridge import verify_persisted_artifact

    ledger_path = tmp_path / "runs" / run_id / "ledger.sqlite"
    ledger_hash = sha256_file(ledger_path)
    response = verify_persisted_artifact(
        run_id,
        artifact,
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status.value == "accepted"
    assert response.result.artifact_id == artifact.id
    assert response.result.content_hash == artifact.content_hash
    assert response.result.producing_commit_hash == artifact.producing_commit_hash
    assert sha256_file(ledger_path) == ledger_hash


def _persist_synthetic_bundle_fixture(
    root: Path,
    *,
    run_id: str = "run-kernel-synthetic-bundle",
) -> tuple[str, str, str, KernelSyntheticEvidenceBundle, dict[str, ArtifactRef]]:
    from factori.adapters.experiment_real import (
        ExperimentToolRunResult,
        LocalSyntheticExperimentRunner,
    )
    from factori.artifacts import ArtifactStore
    from factori.stage_c_phases import run_real_experiment_validation

    class Runner:
        def run(self, **kwargs) -> ExperimentToolRunResult:
            return ExperimentToolRunResult(
                exit_code=0,
                stdout='{"metrics":{"delta":0.08,"lcb_95":0.02}}',
                output_payload={
                    "metrics": {"delta": 0.08, "lcb_95": 0.02},
                    "synthetic_only": True,
                },
                metrics={"delta": 0.08, "lcb_95": 0.02},
                elapsed_ms=7,
                runner_version="test-runner-v1",
            )

    store = ArtifactStore(root)
    store.init_run(run_id)
    ledger = ResearchLedger(root / "runs" / run_id / "ledger.sqlite")
    ledger.append_commit(
        run_id=run_id,
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": run_id},
        timestamp="1970-01-01T00:00:00.000000Z",
    )
    candidate = Candidate(
        id="candidate-synthetic-bundle",
        domain="synthetic methods",
        method="synthetic simulation",
        question="Does the controlled simulation meet its declared metric?",
        hypothesis="The synthetic-only method meets the declared acceptance criteria.",
        data_requirement="SyntheticOnly",
    )
    _, artifacts, _, contract = run_real_experiment_validation(
        run_id=run_id,
        candidate=candidate,
        experiment_runner=LocalSyntheticExperimentRunner(
            runner_name="local-runner",
            runner=Runner(),
            allow_external_tools=True,
            replications=5,
            timeout_seconds=10,
        ),
        store=store,
        ledger=ledger,
    )
    by_id = {artifact.id: artifact for artifact in artifacts}
    bundle = KernelSyntheticEvidenceBundle(
        kind="SyntheticExperiment",
        contract_artifact_id=f"experiment-contract-{candidate.id}",
        input_artifact_id=f"experiment-input-{candidate.id}",
        trace_artifact_id=f"experiment-trace-{candidate.id}",
        output_artifact_id=f"experiment-output-{candidate.id}",
        result_artifact_id=f"experiment-result-{candidate.id}",
        safety_artifact_id=f"experiment-safety-{candidate.id}",
    )
    assert set(bundle.model_dump().values()) - {"SyntheticExperiment"} <= set(by_id)
    return (
        candidate.id,
        contract.claim_id,
        ledger.latest_commit_hash(run_id) or "",
        bundle,
        by_id,
    )


@pytest.mark.parametrize("mode", list(KernelMode))
def test_rust_validates_persisted_synthetic_bundle_without_granting_authority(
    tmp_path: Path,
    mode: KernelMode,
) -> None:
    _build_kernel_binary()
    run_id = "run-kernel-synthetic-bundle"
    candidate_id, claim_id, commit_hash, bundle, by_id = _persist_synthetic_bundle_fixture(
        tmp_path,
        run_id=run_id,
    )
    from factori.kernel_bridge import validate_persisted_evidence_bundle

    response = validate_persisted_evidence_bundle(
        run_id,
        candidate_id=candidate_id,
        claim_id=claim_id,
        producing_commit_hash=commit_hash,
        bundle=bundle,
        mode=mode,
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status.value == "accepted", [
        (diagnostic.code, diagnostic.message, diagnostic.path)
        for diagnostic in response.diagnostics
    ]
    assert response.result.bundle_valid is True
    assert response.result.authority_granted is False
    assert response.result.validated_artifact_ids == [
        by_id[f"experiment-contract-{candidate_id}"].id,
        by_id[f"experiment-input-{candidate_id}"].id,
        by_id[f"experiment-trace-{candidate_id}"].id,
        by_id[f"experiment-output-{candidate_id}"].id,
        by_id[f"experiment-result-{candidate_id}"].id,
        by_id[f"experiment-safety-{candidate_id}"].id,
    ]


def _clone_stage_c_bundle_with_mutation(
    *,
    source_root: Path,
    target_root: Path,
    run_id: str,
    mutate_payloads: Callable[[dict[str, dict[str, Any]]], None] | None = None,
    metadata_overrides: dict[str, dict[str, Any]] | None = None,
    raw_overrides: dict[str, str] | None = None,
) -> str:
    from factori.artifacts import ArtifactStore

    source_ledger = ResearchLedger.open_existing(source_root / "runs" / run_id / "ledger.sqlite")
    source_commits = source_ledger.list_commits(run_id)
    assert len(source_commits) == 2
    root_commit, evidence_commit = source_commits
    payloads = {
        artifact.id: json.loads((source_root / artifact.path).read_text(encoding="utf-8"))
        for artifact in evidence_commit.artifact_refs
    }
    if mutate_payloads is not None:
        mutate_payloads(payloads)

    target_store = ArtifactStore(target_root)
    target_store.init_run(run_id)
    target_ledger = ResearchLedger(target_root / "runs" / run_id / "ledger.sqlite")
    cloned_root = target_ledger.append_commit(
        run_id=run_id,
        action_type=root_commit.action_type,
        payload=root_commit.payload,
        candidate_id=root_commit.candidate_id,
        timestamp=root_commit.timestamp,
    )
    cloned_artifacts = []
    for artifact in evidence_commit.artifact_refs:
        metadata = dict(artifact.metadata)
        metadata.update((metadata_overrides or {}).get(artifact.id, {}))
        format_label = str(metadata.pop("format", "json"))
        if artifact.id in (raw_overrides or {}):
            cloned = target_store.write_text(
                run_id=run_id,
                artifact_id=artifact.id,
                artifact_type=artifact.type,
                text=(raw_overrides or {})[artifact.id],
                extension="json",
                format_label=format_label,
                metadata=metadata,
            )
        elif format_label != "json":
            cloned = target_store.write_text(
                run_id=run_id,
                artifact_id=artifact.id,
                artifact_type=artifact.type,
                text=canonical_json(payloads[artifact.id]) + "\n",
                extension="json",
                format_label=format_label,
                metadata=metadata,
            )
        else:
            cloned = target_store.write_json(
                run_id=run_id,
                artifact_id=artifact.id,
                artifact_type=artifact.type,
                data=payloads[artifact.id],
                metadata=metadata,
            )
        cloned_artifacts.append(cloned)
    cloned_commit = target_ledger.append_commit(
        run_id=run_id,
        parent_hash=cloned_root.commit_hash,
        action_type=evidence_commit.action_type,
        payload=evidence_commit.payload,
        candidate_id=evidence_commit.candidate_id,
        artifact_refs=cloned_artifacts,
        timestamp=evidence_commit.timestamp,
    )
    return cloned_commit.commit_hash


@pytest.mark.parametrize("mode", list(KernelMode))
def test_rust_bundle_operation_preserves_duplicate_member_diagnostic(
    mode: KernelMode,
) -> None:
    _build_kernel_binary()
    response = _run_kernel(
        {
            "protocol_version": "0.89.0",
            "request_id": f"bundle-duplicate-{mode.value}",
            "operation": "evidence.validate_bundle",
            "mode": mode.value,
            "payload": {
                "run_id": "run-001",
                "candidate_id": "candidate-001",
                "claim_id": "claim-001",
                "producing_commit_hash": "f" * 64,
                "bundle": {
                    "kind": "SyntheticExperiment",
                    "contract_artifact_id": "contract-001",
                    "input_artifact_id": "contract-001",
                    "trace_artifact_id": "trace-001",
                    "output_artifact_id": "output-001",
                    "result_artifact_id": "result-001",
                    "safety_artifact_id": "safety-001",
                },
            },
        }
    )

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "bundle_member_duplicate"


@pytest.mark.parametrize("mode", list(KernelMode))
@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("fake_metadata", "fake_backend_denied"),
        ("output_hash", "bundle_output_hash_mismatch"),
        ("claim", "bundle_claim_mismatch"),
        ("unsafe_contract", "bundle_contract_invalid"),
        ("unknown_result_field", "protocol_invalid"),
        ("invalid_safety", "bundle_safety_invalid"),
        ("boolean_metric", "bundle_metrics_invalid"),
        ("trace_hash", "bundle_trace_hash_mismatch"),
        ("duplicate_json_key", "protocol_invalid"),
        ("presentation_metadata", "authority_denied"),
    ],
)
def test_rust_rejects_persisted_synthetic_bundle_mutations_in_both_modes(
    tmp_path: Path,
    mode: KernelMode,
    mutation: str,
    expected_code: str,
) -> None:
    _build_kernel_binary()
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    run_id = "run-kernel-synthetic-mutation"
    candidate_id, claim_id, _, bundle, by_id = _persist_synthetic_bundle_fixture(
        source_root,
        run_id=run_id,
    )
    contract_id = bundle.contract_artifact_id
    output_id = bundle.output_artifact_id
    result_id = bundle.result_artifact_id
    safety_id = bundle.safety_artifact_id
    metadata_overrides: dict[str, dict[str, Any]] = {}
    raw_overrides: dict[str, str] = {}

    def mutate_payloads(payloads: dict[str, dict[str, Any]]) -> None:
        if mutation == "output_hash":
            assert payloads[output_id].pop("synthetic_only") is True
        elif mutation == "claim":
            payloads[contract_id]["claim_id"] = "claim-other"
        elif mutation == "unsafe_contract":
            payloads[contract_id]["model_spec"]["source"] = "load /tmp/data"
        elif mutation == "unknown_result_field":
            payloads[result_id]["unexpected"] = True
        elif mutation == "invalid_safety":
            payloads[safety_id]["result_valid"] = False
        elif mutation == "boolean_metric":
            payloads[output_id]["metrics"]["delta"] = True
            payloads[result_id]["metrics"]["delta"] = True
        elif mutation == "trace_hash":
            payloads[result_id]["stdout_hash"] = "0" * 64

    if mutation == "fake_metadata":
        metadata_overrides[output_id] = {"fake": True}
    elif mutation == "presentation_metadata":
        metadata_overrides[output_id] = {"format": "markdown"}
    elif mutation == "duplicate_json_key":
        source_result = json.loads(
            (source_root / by_id[result_id].path).read_text(encoding="utf-8")
        )
        raw_overrides[result_id] = json.dumps(source_result)[:-1] + ',"fake":false}'

    commit_hash = _clone_stage_c_bundle_with_mutation(
        source_root=source_root,
        target_root=target_root,
        run_id=run_id,
        mutate_payloads=mutate_payloads,
        metadata_overrides=metadata_overrides,
        raw_overrides=raw_overrides,
    )
    from factori.kernel_bridge import validate_persisted_evidence_bundle

    ledger_path = target_root / "runs" / run_id / "ledger.sqlite"
    ledger_hash = sha256_file(ledger_path)
    response = validate_persisted_evidence_bundle(
        run_id,
        candidate_id=candidate_id,
        claim_id=claim_id,
        producing_commit_hash=commit_hash,
        bundle=bundle,
        mode=mode,
        root=target_root,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status.value == "rejected"
    assert response.result.model_dump() == {}
    assert response.diagnostics[0].code == expected_code
    assert response.mutation_performed is False
    assert sha256_file(ledger_path) == ledger_hash


@pytest.mark.parametrize("mode", list(KernelMode))
@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("candidate", "bundle_candidate_mismatch"),
        ("claim", "bundle_claim_mismatch"),
        ("commit", "bundle_commit_mismatch"),
        ("member", "bundle_member_unexpected"),
    ],
)
def test_rust_rejects_cross_identity_bundle_requests_in_both_modes(
    tmp_path: Path,
    mode: KernelMode,
    mutation: str,
    expected_code: str,
) -> None:
    _build_kernel_binary()
    run_id = "run-kernel-synthetic-cross-identity"
    candidate_id, claim_id, commit_hash, bundle, _ = _persist_synthetic_bundle_fixture(
        tmp_path,
        run_id=run_id,
    )
    bundle_payload = bundle.model_dump(mode="json")
    if mutation == "candidate":
        candidate_id = "candidate-other"
    elif mutation == "claim":
        claim_id = "claim-other"
    elif mutation == "commit":
        ledger = ResearchLedger.open_existing(tmp_path / "runs" / run_id / "ledger.sqlite")
        commit_hash = ledger.append_commit(
            run_id=run_id,
            parent_hash=commit_hash,
            action_type=ControllerActionType.WRITE_ARTIFACT,
            payload={"artifact_id": "unrelated"},
            candidate_id=candidate_id,
            timestamp="1970-01-01T00:00:01.000000Z",
        ).commit_hash
    elif mutation == "member":
        bundle_payload["output_artifact_id"] = "experiment-output-other"

    ledger_path = tmp_path / "runs" / run_id / "ledger.sqlite"
    ledger_hash = sha256_file(ledger_path)
    response = _run_kernel(
        {
            "protocol_version": "0.89.0",
            "request_id": f"bundle-cross-{mutation}-{mode.value}",
            "operation": "evidence.validate_bundle",
            "mode": mode.value,
            "payload": {
                "run_id": run_id,
                "candidate_id": candidate_id,
                "claim_id": claim_id,
                "producing_commit_hash": commit_hash,
                "bundle": bundle_payload,
            },
        },
        root=tmp_path,
    )

    assert response["status"] == "rejected"
    assert response["result"] == {}
    assert response["diagnostics"][0]["code"] == expected_code
    assert response["mutation_performed"] is False
    assert sha256_file(ledger_path) == ledger_hash


def _claim_resolve_request(
    *,
    run_id: str,
    claim_id: str,
    claim_table_commit_hash: str,
    claim_table_artifact_id: str = "claim-table",
    evidence: dict[str, object] | None = None,
) -> dict[str, object]:
    return KernelClaimResolveRequest(
        protocol_version="0.89.0",
        request_id=f"claim-resolve-{run_id}-{claim_id}",
        operation="claim.resolve",
        mode=KernelMode.STRICT_PRODUCTION,
        payload={
            "run_id": run_id,
            "claim_id": claim_id,
            "claim_table": {
                "artifact_id": claim_table_artifact_id,
                "producing_commit_hash": claim_table_commit_hash,
            },
            "evidence": evidence,
        },
    ).model_dump(mode="json")


def _persist_claim_table_fixture(
    root: Path,
    *,
    run_id: str,
    candidate_id: str,
    claim_id: str,
    claim_text: str,
    claim_label: str,
    evidence_artifact_ids: list[str] | None = None,
    allowed_in_main_text: bool = True,
    allowed_section: str = "Theory",
) -> str:
    from factori.artifacts import ArtifactStore

    store = ArtifactStore(root)
    ledger = ResearchLedger.open_existing(root / "runs" / run_id / "ledger.sqlite")
    evidence_ids = list(evidence_artifact_ids or [])
    payload = {
        "final_nucleus_id": f"final-{candidate_id}",
        "claims": [
            {
                "claim_id": claim_id,
                "claim_text": claim_text,
                "claim_label": claim_label,
                "candidate_id": candidate_id,
                "evidence_artifact_ids": evidence_ids,
                "evidence_types": (
                    ["proof"]
                    if evidence_ids and claim_label == "LeanVerified"
                    else ["experiment"]
                    if evidence_ids
                    else []
                ),
                "allowed_in_main_text": allowed_in_main_text,
                "allowed_section": allowed_section,
                "reason": "bounded persisted claim fixture",
            }
        ],
        "evidence_links": [],
    }
    artifact = store.write_json(
        run_id=run_id,
        artifact_id="claim-table",
        artifact_type=ArtifactType.REPORT,
        data=payload,
        metadata={"stage": "manuscript_planning", "fake": True},
    )
    commit = ledger.append_commit(
        run_id=run_id,
        parent_hash=ledger.latest_commit_hash(run_id),
        action_type=ControllerActionType.CLAIM_TABLE_BUILT,
        payload=payload,
        artifact_refs=[artifact],
        timestamp="1970-01-01T00:00:02.000000Z",
    )
    store.link_artifact_to_commit(artifact, commit.commit_hash)
    return commit.commit_hash


def _persist_empty_run_fixture(root: Path, *, run_id: str) -> None:
    from factori.artifacts import ArtifactStore

    ArtifactStore(root).init_run(run_id)
    ResearchLedger(root / "runs" / run_id / "ledger.sqlite").append_commit(
        run_id=run_id,
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": run_id},
        timestamp="1970-01-01T00:00:00.000000Z",
    )


def test_rust_claim_resolution_revalidates_synthetic_bundle_and_grants_no_authority(
    tmp_path: Path,
) -> None:
    _build_kernel_binary()
    run_id = "run-kernel-claim-resolve"
    candidate_id, claim_id, evidence_commit_hash, bundle, _ = _persist_synthetic_bundle_fixture(
        tmp_path,
        run_id=run_id,
    )
    claim_text = "This synthetic simulation remains bounded to the configured setting."
    claim_table_commit_hash = _persist_claim_table_fixture(
        tmp_path,
        run_id=run_id,
        candidate_id=candidate_id,
        claim_id=claim_id,
        claim_text=claim_text,
        claim_label="SyntheticExperimentVerified",
        evidence_artifact_ids=[
            bundle.trace_artifact_id,
            bundle.output_artifact_id,
            bundle.result_artifact_id,
        ],
        allowed_section="Synthetic Experiments",
    )
    response = _run_kernel(
        _claim_resolve_request(
            run_id=run_id,
            claim_id=claim_id,
            claim_table_commit_hash=claim_table_commit_hash,
            evidence={
                "producing_commit_hash": evidence_commit_hash,
                "bundle": bundle.model_dump(mode="json"),
            },
        ),
        root=tmp_path,
    )

    assert response["status"] == "accepted"
    assert response["result"] == {
        "run_id": run_id,
        "candidate_id": candidate_id,
        "claim_id": claim_id,
        "claim_text_hash": sha256_text(claim_text),
        "claim_label": "SyntheticExperimentVerified",
        "allowed_in_main_text": True,
        "allowed_section": "Synthetic Experiments",
        "claim_record_validated": True,
        "admissible": True,
        "evidence_bundle_validated": True,
        "authority_granted": False,
    }


def test_claim_resolution_bridge_checks_identity_and_authority_boundary(
    tmp_path: Path,
) -> None:
    _build_kernel_binary()
    run_id = "run-kernel-claim-bridge"
    candidate_id, claim_id, evidence_commit_hash, bundle, _ = _persist_synthetic_bundle_fixture(
        tmp_path,
        run_id=run_id,
    )
    claim_table_commit_hash = _persist_claim_table_fixture(
        tmp_path,
        run_id=run_id,
        candidate_id=candidate_id,
        claim_id=claim_id,
        claim_text="This synthetic simulation remains bounded.",
        claim_label="SyntheticExperimentVerified",
        evidence_artifact_ids=[
            bundle.trace_artifact_id,
            bundle.output_artifact_id,
            bundle.result_artifact_id,
        ],
        allowed_section="Synthetic Experiments",
    )
    from factori.kernel_bridge import resolve_persisted_claim

    response = resolve_persisted_claim(
        run_id,
        claim_id=claim_id,
        claim_table_artifact_id="claim-table",
        claim_table_producing_commit_hash=claim_table_commit_hash,
        evidence={
            "producing_commit_hash": evidence_commit_hash,
            "bundle": bundle.model_dump(mode="json"),
        },
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status.value == "accepted"
    assert response.result.run_id == run_id
    assert response.result.candidate_id == candidate_id
    assert response.result.claim_id == claim_id
    assert response.result.claim_record_validated is True
    assert response.result.authority_granted is False


def test_rust_claim_resolution_revalidates_lean_bundle(tmp_path: Path) -> None:
    _build_kernel_binary()
    run_id = "run-kernel-claim-resolve-lean"
    candidate_id, claim_id, evidence_commit_hash, bundle, _ = _persist_lean_bundle_fixture(
        tmp_path,
        run_id=run_id,
    )
    claim_text = "The persisted formal theorem is accepted by the configured Lean checker."
    claim_table_commit_hash = _persist_claim_table_fixture(
        tmp_path,
        run_id=run_id,
        candidate_id=candidate_id,
        claim_id=claim_id,
        claim_text=claim_text,
        claim_label="LeanVerified",
        evidence_artifact_ids=[bundle.trace_artifact_id, bundle.result_artifact_id],
        allowed_section="Theory",
    )

    response = _run_kernel(
        _claim_resolve_request(
            run_id=run_id,
            claim_id=claim_id,
            claim_table_commit_hash=claim_table_commit_hash,
            evidence={
                "producing_commit_hash": evidence_commit_hash,
                "bundle": bundle.model_dump(mode="json"),
            },
        ),
        root=tmp_path,
    )

    assert response["status"] == "accepted"
    assert response["result"]["candidate_id"] == candidate_id
    assert response["result"]["claim_text_hash"] == sha256_text(claim_text)
    assert response["result"]["admissible"] is True
    assert response["result"]["evidence_bundle_validated"] is True
    assert response["result"]["authority_granted"] is False


@pytest.mark.parametrize(
    ("claim_label", "allowed_section", "allowed_in_main_text", "expected"),
    [
        ("Conjecture", "Theory", True, True),
        ("NegativeResult", "Results", True, True),
        ("Limitation", "Limitations", True, True),
        ("Unsupported", "Future Work", False, True),
        ("Unsupported", "Future Work", True, False),
        ("RealDataExperimentVerified", "Results", True, False),
    ],
)
def test_rust_claim_resolution_preserves_non_verified_claim_boundaries(
    tmp_path: Path,
    claim_label: str,
    allowed_section: str,
    allowed_in_main_text: bool,
    expected: bool,
) -> None:
    _build_kernel_binary()
    run_id = f"run-kernel-claim-boundary-{claim_label}-{allowed_in_main_text}"
    candidate_id = "candidate-claim-boundary"
    claim_id = f"claim-{claim_label}"
    claim_text = "A bounded statement about the configured study."
    _persist_empty_run_fixture(tmp_path, run_id=run_id)
    claim_table_commit_hash = _persist_claim_table_fixture(
        tmp_path,
        run_id=run_id,
        candidate_id=candidate_id,
        claim_id=claim_id,
        claim_text=claim_text,
        claim_label=claim_label,
        allowed_in_main_text=allowed_in_main_text,
        allowed_section=allowed_section,
    )
    response = _run_kernel(
        _claim_resolve_request(
            run_id=run_id,
            claim_id=claim_id,
            claim_table_commit_hash=claim_table_commit_hash,
        ),
        root=tmp_path,
    )

    from factori.claims import is_claim_admissible

    python_admissible = is_claim_admissible(
        Claim(
            claim_id=claim_id,
            claim_text=claim_text,
            claim_label=VerificationLabel(claim_label),
            candidate_id=candidate_id,
            evidence_artifact_ids=[],
            evidence_types=[],
            allowed_in_main_text=allowed_in_main_text,
            allowed_section=allowed_section,
            reason="bounded persisted claim fixture",
        ),
        [],
    )
    assert response["status"] == "accepted"
    assert python_admissible is expected
    assert response["result"]["admissible"] is python_admissible
    assert response["result"]["evidence_bundle_validated"] is False
    assert response["result"]["authority_granted"] is False


def test_rust_claim_resolution_requires_evidence_for_verified_labels(tmp_path: Path) -> None:
    _build_kernel_binary()
    run_id = "run-kernel-claim-missing-evidence"
    candidate_id, claim_id, _, bundle, _ = _persist_synthetic_bundle_fixture(
        tmp_path,
        run_id=run_id,
    )
    claim_table_commit_hash = _persist_claim_table_fixture(
        tmp_path,
        run_id=run_id,
        candidate_id=candidate_id,
        claim_id=claim_id,
        claim_text="This synthetic simulation is bounded.",
        claim_label="SyntheticExperimentVerified",
        evidence_artifact_ids=[
            bundle.trace_artifact_id,
            bundle.output_artifact_id,
            bundle.result_artifact_id,
        ],
        allowed_section="Synthetic Experiments",
    )
    response = _run_kernel(
        _claim_resolve_request(
            run_id=run_id,
            claim_id=claim_id,
            claim_table_commit_hash=claim_table_commit_hash,
        ),
        root=tmp_path,
    )

    assert response["status"] == "accepted"
    assert response["result"]["admissible"] is False
    assert response["result"]["evidence_bundle_validated"] is False
    assert response["diagnostics"][0]["code"] == "claim_evidence_missing"


def test_rust_claim_resolution_rejects_mismatched_claim_evidence(tmp_path: Path) -> None:
    _build_kernel_binary()
    run_id = "run-kernel-claim-evidence-mismatch"
    candidate_id, claim_id, evidence_commit_hash, bundle, _ = _persist_synthetic_bundle_fixture(
        tmp_path,
        run_id=run_id,
    )
    claim_table_commit_hash = _persist_claim_table_fixture(
        tmp_path,
        run_id=run_id,
        candidate_id=candidate_id,
        claim_id=claim_id,
        claim_text="A bounded synthetic simulation statement.",
        claim_label="SyntheticExperimentVerified",
        evidence_artifact_ids=[bundle.trace_artifact_id, bundle.result_artifact_id],
        allowed_section="Synthetic Experiments",
    )
    response = _run_kernel(
        _claim_resolve_request(
            run_id=run_id,
            claim_id=claim_id,
            claim_table_commit_hash=claim_table_commit_hash,
            evidence={
                "producing_commit_hash": evidence_commit_hash,
                "bundle": bundle.model_dump(mode="json"),
            },
        ),
        root=tmp_path,
    )

    assert response["status"] == "accepted"
    assert response["result"]["admissible"] is False
    assert response["diagnostics"][0]["code"] == "claim_evidence_mismatch"


def test_rust_claim_resolution_does_not_ignore_mismatched_optional_evidence(
    tmp_path: Path,
) -> None:
    _build_kernel_binary()
    run_id = "run-kernel-conjecture-evidence-mismatch"
    candidate_id, claim_id, evidence_commit_hash, bundle, _ = _persist_synthetic_bundle_fixture(
        tmp_path,
        run_id=run_id,
    )
    claim_table_commit_hash = _persist_claim_table_fixture(
        tmp_path,
        run_id=run_id,
        candidate_id=candidate_id,
        claim_id=claim_id,
        claim_text="A bounded conjecture about the configured study.",
        claim_label="Conjecture",
        allowed_section="Theory",
    )

    response = _run_kernel(
        _claim_resolve_request(
            run_id=run_id,
            claim_id=claim_id,
            claim_table_commit_hash=claim_table_commit_hash,
            evidence={
                "producing_commit_hash": evidence_commit_hash,
                "bundle": bundle.model_dump(mode="json"),
            },
        ),
        root=tmp_path,
    )

    assert response["status"] == "accepted"
    assert response["result"]["admissible"] is False
    assert response["result"]["evidence_bundle_validated"] is True
    assert response["diagnostics"][0]["code"] == "claim_evidence_mismatch"


def test_rust_claim_resolution_rejects_unbounded_synthetic_text(tmp_path: Path) -> None:
    _build_kernel_binary()
    run_id = "run-kernel-claim-invalid-scope"
    candidate_id, claim_id, evidence_commit_hash, bundle, _ = _persist_synthetic_bundle_fixture(
        tmp_path,
        run_id=run_id,
    )
    claim_table_commit_hash = _persist_claim_table_fixture(
        tmp_path,
        run_id=run_id,
        candidate_id=candidate_id,
        claim_id=claim_id,
        claim_text="This synthetic simulation establishes universal deployment performance.",
        claim_label="SyntheticExperimentVerified",
        evidence_artifact_ids=[
            bundle.trace_artifact_id,
            bundle.output_artifact_id,
            bundle.result_artifact_id,
        ],
        allowed_section="Synthetic Experiments",
    )
    response = _run_kernel(
        _claim_resolve_request(
            run_id=run_id,
            claim_id=claim_id,
            claim_table_commit_hash=claim_table_commit_hash,
            evidence={
                "producing_commit_hash": evidence_commit_hash,
                "bundle": bundle.model_dump(mode="json"),
            },
        ),
        root=tmp_path,
    )

    assert response["status"] == "accepted"
    assert response["result"]["admissible"] is False
    assert response["diagnostics"][0]["code"] == "claim_scope_denied"


@pytest.mark.parametrize(
    ("claim_text", "allowed_section"),
    [("", "Theory"), ("A bounded statement.", "NotASection")],
)
def test_rust_claim_resolution_rejects_invalid_persisted_claim_record(
    tmp_path: Path,
    claim_text: str,
    allowed_section: str,
) -> None:
    _build_kernel_binary()
    run_id = f"run-kernel-claim-invalid-record-{len(claim_text)}-{allowed_section}"
    _persist_empty_run_fixture(tmp_path, run_id=run_id)
    claim_table_commit_hash = _persist_claim_table_fixture(
        tmp_path,
        run_id=run_id,
        candidate_id="candidate-claim-invalid-record",
        claim_id="claim-invalid-record",
        claim_text=claim_text,
        claim_label="Conjecture",
        allowed_section=allowed_section,
    )
    response = _run_kernel(
        _claim_resolve_request(
            run_id=run_id,
            claim_id="claim-invalid-record",
            claim_table_commit_hash=claim_table_commit_hash,
        ),
        root=tmp_path,
    )

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "claim_record_invalid"


def test_rust_claim_resolution_rejects_unknown_payload_fields(tmp_path: Path) -> None:
    _build_kernel_binary()
    request = _claim_resolve_request(
        run_id="run-kernel-claim-unknown",
        claim_id="claim-claim-unknown",
        claim_table_commit_hash="a" * 64,
    )
    request["payload"]["unexpected"] = True

    completed = subprocess.run(
        [str(KERNEL_BINARY), "--root", str(tmp_path)],
        input=json.dumps(request) + "\n",
        capture_output=True,
        check=False,
        text=True,
    )

    assert completed.returncode == 2
    response = json.loads(completed.stdout)
    assert response["status"] == "error"
    assert response["diagnostics"][0]["code"] == "transport_invalid"


def test_bundle_bridge_rejects_accepted_response_with_wrong_bundle_members(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from factori.kernel_bridge import KernelBridgeError

    bundle = KernelSyntheticEvidenceBundle(
        kind="SyntheticExperiment",
        contract_artifact_id="contract-001",
        input_artifact_id="input-001",
        trace_artifact_id="trace-001",
        output_artifact_id="output-001",
        result_artifact_id="result-001",
        safety_artifact_id="safety-001",
    )
    response = KernelResponseEnvelope.model_validate(
        {
            "protocol_version": "0.89.0",
            "kernel_version": "0.1.0-dev",
            "request_id": "evidence-validate-bundle-run-001-candidate-001",
            "operation": "evidence.validate_bundle",
            "mode": "StrictProduction",
            "status": "accepted",
            "result": KernelEvidenceValidateBundleResult(
                run_id="run-001",
                candidate_id="candidate-001",
                claim_id="claim-001",
                bundle_kind="SyntheticExperiment",
                producing_commit_hash="f" * 64,
                validated_artifact_ids=[
                    "wrong-001",
                    "input-001",
                    "trace-001",
                    "output-001",
                    "result-001",
                    "safety-001",
                ],
                bundle_valid=True,
                authority_granted=False,
            ),
            "diagnostics": [],
            "mutation_performed": False,
        }
    )
    monkeypatch.setattr("factori.kernel_bridge._invoke_kernel", lambda *args, **kwargs: response)

    from factori.kernel_bridge import validate_persisted_evidence_bundle

    with pytest.raises(KernelBridgeError, match="does not match request"):
        validate_persisted_evidence_bundle(
            "run-001",
            candidate_id="candidate-001",
            claim_id="claim-001",
            producing_commit_hash="f" * 64,
            bundle=bundle,
            root=tmp_path,
        )


def _persist_lean_bundle_fixture(
    root: Path,
    *,
    run_id: str = "run-kernel-lean-bundle",
) -> tuple[str, str, str, KernelLeanEvidenceBundle, dict[str, ArtifactRef]]:
    from factori.adapters.proof_real import LeanProofVerifier, ProofToolRunResult
    from factori.artifacts import ArtifactStore
    from factori.stage_c_phases import run_real_proof_validation

    class Runner:
        def run(self, **kwargs) -> ProofToolRunResult:
            return ProofToolRunResult(
                exit_code=0,
                stdout="theorem accepted",
                stderr="",
                elapsed_ms=7,
                tool_version="lean-test-v1",
            )

    store = ArtifactStore(root)
    store.init_run(run_id)
    ledger = ResearchLedger(root / "runs" / run_id / "ledger.sqlite")
    ledger.append_commit(
        run_id=run_id,
        action_type=ControllerActionType.INIT_RUN,
        payload={"run_id": run_id},
        timestamp="1970-01-01T00:00:00.000000Z",
    )
    candidate = Candidate(
        id="candidate-lean-bundle",
        domain="formal methods",
        method="Lean proof",
        question="Does the formal statement hold?",
        hypothesis="The formal statement holds.",
        theory="A formal theorem claim.",
        data_requirement="NoData",
    )
    _, artifacts, _, contract = run_real_proof_validation(
        run_id=run_id,
        candidate=candidate,
        proof_verifier=LeanProofVerifier(
            proof_executable="lean",
            runner=Runner(),
            allow_external_tools=True,
        ),
        store=store,
        ledger=ledger,
    )
    by_id = {artifact.id: artifact for artifact in artifacts}
    bundle = KernelLeanEvidenceBundle(
        kind="LeanProof",
        contract_artifact_id=f"proof-contract-{candidate.id}",
        payload_artifact_id=f"proof-payload-{candidate.id}",
        trace_artifact_id=f"proof-trace-{candidate.id}",
        result_artifact_id=f"proof-result-{candidate.id}",
        safety_artifact_id=f"proof-safety-{candidate.id}",
    )
    assert set(bundle.model_dump().values()) - {"LeanProof"} <= set(by_id)
    return (
        candidate.id,
        contract.claim_id,
        ledger.latest_commit_hash(run_id) or "",
        bundle,
        by_id,
    )


@pytest.mark.parametrize("mode", list(KernelMode))
def test_rust_validates_persisted_lean_bundle_without_granting_authority(
    tmp_path: Path,
    mode: KernelMode,
) -> None:
    _build_kernel_binary()
    run_id = "run-kernel-lean-bundle"
    candidate_id, claim_id, commit_hash, bundle, _ = _persist_lean_bundle_fixture(
        tmp_path,
        run_id=run_id,
    )
    from factori.kernel_bridge import validate_persisted_evidence_bundle

    response = validate_persisted_evidence_bundle(
        run_id,
        candidate_id=candidate_id,
        claim_id=claim_id,
        producing_commit_hash=commit_hash,
        bundle=bundle,
        mode=mode,
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status.value == "accepted", [
        (diagnostic.code, diagnostic.message, diagnostic.path)
        for diagnostic in response.diagnostics
    ]
    assert response.result.bundle_valid is True
    assert response.result.authority_granted is False


@pytest.mark.parametrize("mode", list(KernelMode))
@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("fake_metadata", "fake_backend_denied"),
        ("payload_hash", "bundle_payload_hash_mismatch"),
        ("claim", "bundle_claim_mismatch"),
        ("malformed_import", "bundle_contract_invalid"),
        ("unknown_trace_field", "protocol_invalid"),
        ("invalid_safety", "bundle_safety_invalid"),
        ("trace_hash", "bundle_trace_hash_mismatch"),
        ("presentation_metadata", "authority_denied"),
    ],
)
def test_rust_rejects_persisted_lean_bundle_mutations_in_both_modes(
    tmp_path: Path,
    mode: KernelMode,
    mutation: str,
    expected_code: str,
) -> None:
    _build_kernel_binary()
    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    run_id = "run-kernel-lean-mutation"
    candidate_id, claim_id, _, bundle, _ = _persist_lean_bundle_fixture(
        source_root,
        run_id=run_id,
    )
    contract_id = bundle.contract_artifact_id
    trace_id = bundle.trace_artifact_id
    result_id = bundle.result_artifact_id
    safety_id = bundle.safety_artifact_id
    metadata_overrides: dict[str, dict[str, Any]] = {}

    def mutate_payloads(payloads: dict[str, dict[str, Any]]) -> None:
        if mutation == "payload_hash":
            payloads[result_id]["proof_payload_hash"] = "0" * 64
        elif mutation == "claim":
            payloads[contract_id]["claim_id"] = "claim-other"
        elif mutation == "malformed_import":
            payloads[contract_id]["allowed_imports"] = ["Mathlib..Invalid"]
        elif mutation == "unknown_trace_field":
            payloads[trace_id]["unexpected"] = True
        elif mutation == "invalid_safety":
            payloads[safety_id]["result_valid"] = False
        elif mutation == "trace_hash":
            payloads[result_id]["stdout_hash"] = "0" * 64

    if mutation == "fake_metadata":
        metadata_overrides[trace_id] = {"fake": True}
    elif mutation == "presentation_metadata":
        metadata_overrides[trace_id] = {"format": "markdown"}

    commit_hash = _clone_stage_c_bundle_with_mutation(
        source_root=source_root,
        target_root=target_root,
        run_id=run_id,
        mutate_payloads=mutate_payloads,
        metadata_overrides=metadata_overrides,
    )
    from factori.kernel_bridge import validate_persisted_evidence_bundle

    ledger_path = target_root / "runs" / run_id / "ledger.sqlite"
    ledger_hash = sha256_file(ledger_path)
    response = validate_persisted_evidence_bundle(
        run_id,
        candidate_id=candidate_id,
        claim_id=claim_id,
        producing_commit_hash=commit_hash,
        bundle=bundle,
        mode=mode,
        root=target_root,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status.value == "rejected"
    assert response.result.model_dump() == {}
    assert response.diagnostics[0].code == expected_code
    assert response.mutation_performed is False
    assert sha256_file(ledger_path) == ledger_hash


def test_rust_artifact_verification_rejects_tampered_raw_bytes(tmp_path: Path) -> None:
    _build_kernel_binary()
    run_id, artifact = _persist_kernel_artifact_fixture(tmp_path)
    request = KernelArtifactVerifyRequest(
        protocol_version="0.89.0",
        request_id="artifact-tampered-bytes",
        operation="artifact.verify",
        mode="DevelopmentCompatibility",
        payload={
            "run_id": run_id,
            "artifact": artifact,
        },
    )
    (tmp_path / artifact.path).write_bytes(b'{"value": 2}\n')

    response = _run_kernel(request.model_dump(mode="json"), root=tmp_path)

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "artifact_hash_mismatch"


def test_rust_artifact_verification_rejects_wrong_directory_and_presentation_override(
    tmp_path: Path,
) -> None:
    _build_kernel_binary()
    artifact = ArtifactRef(
        id="artifact-001",
        type=ArtifactType.REPORT,
        path="runs/run-kernel-artifact/latex/artifact-001.json",
        content_hash=sha256_text("content"),
        metadata={"is_verification_evidence": True},
    )
    request = {
        "protocol_version": "0.89.0",
        "request_id": "artifact-invalid-location",
        "operation": "artifact.verify",
        "mode": "DevelopmentCompatibility",
        "payload": {
            "run_id": "run-kernel-artifact",
            "artifact": artifact.model_dump(mode="json"),
        },
    }

    response = _run_kernel(request, root=tmp_path)

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "artifact_path_invalid"

    artifact = artifact.model_copy(
        update={"path": "runs/run-kernel-artifact/reports/artifact-001.pdf"}
    )
    request["request_id"] = "artifact-presentation-override"
    request["payload"]["artifact"] = artifact.model_dump(mode="json")
    response = _run_kernel(request, root=tmp_path)

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "artifact_presentation_override"


def test_rust_artifact_verification_rejects_missing_persisted_producer(tmp_path: Path) -> None:
    _build_kernel_binary()
    run_id, artifact = _persist_kernel_artifact_fixture(tmp_path)
    artifact = artifact.model_copy(update={"producing_commit_hash": "f" * 64})
    request = KernelArtifactVerifyRequest(
        protocol_version="0.89.0",
        request_id="artifact-missing-producer",
        operation="artifact.verify",
        mode="DevelopmentCompatibility",
        payload={
            "run_id": run_id,
            "artifact": artifact,
        },
    )

    response = _run_kernel(request.model_dump(mode="json"), root=tmp_path)

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "artifact_producer_missing"


def test_rust_artifact_verification_rejects_unlinked_implicit_evidence(tmp_path: Path) -> None:
    _build_kernel_binary()
    from factori.artifacts import ArtifactStore

    artifact = ArtifactStore(tmp_path).write_bytes(
        run_id="run-kernel-unlinked",
        artifact_id="artifact-unlinked",
        artifact_type=ArtifactType.REPORT,
        content=b"content",
        extension="json",
        format_label="json",
    )
    request = KernelArtifactVerifyRequest(
        protocol_version="0.89.0",
        request_id="artifact-unlinked-evidence",
        operation="artifact.verify",
        mode="DevelopmentCompatibility",
        payload={"run_id": "run-kernel-unlinked", "artifact": artifact},
    )

    response = _run_kernel(request.model_dump(mode="json"), root=tmp_path)

    assert artifact.is_mvp_verification_evidence() is True
    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "artifact_producer_missing"


def test_rust_artifact_verification_accepts_unlinked_context_artifact(tmp_path: Path) -> None:
    _build_kernel_binary()
    from factori.artifacts import ArtifactStore

    artifact = (
        ArtifactStore(tmp_path)
        .write_bytes(
            run_id="run-kernel-context",
            artifact_id="artifact-context",
            artifact_type=ArtifactType.REPORT,
            content=b"context",
            extension="json",
            format_label="json",
        )
        .model_copy(update={"metadata": {"is_verification_evidence": False}})
    )
    request = KernelArtifactVerifyRequest(
        protocol_version="0.89.0",
        request_id="artifact-unlinked-context",
        operation="artifact.verify",
        mode="DevelopmentCompatibility",
        payload={"run_id": "run-kernel-context", "artifact": artifact},
    )

    response = _run_kernel(request.model_dump(mode="json"), root=tmp_path)

    assert artifact.is_mvp_verification_evidence() is False
    assert response["status"] == "accepted"


def test_rust_artifact_verification_rejects_corrupt_persisted_ledger(tmp_path: Path) -> None:
    _build_kernel_binary()
    run_id, artifact = _persist_kernel_artifact_fixture(tmp_path)
    import sqlite3

    with sqlite3.connect(tmp_path / "runs" / run_id / "ledger.sqlite") as connection:
        connection.execute("DROP TRIGGER commits_no_update")
        connection.execute(
            "UPDATE commits SET payload_json = ? WHERE commit_hash = ?",
            ('{"artifact_id":"tampered"}', artifact.producing_commit_hash),
        )
    request = KernelArtifactVerifyRequest(
        protocol_version="0.89.0",
        request_id="artifact-corrupt-ledger",
        operation="artifact.verify",
        mode="DevelopmentCompatibility",
        payload={"run_id": run_id, "artifact": artifact},
    )

    response = _run_kernel(request.model_dump(mode="json"), root=tmp_path)

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "ledger_hash_mismatch"


def test_rust_artifact_verification_requires_root(tmp_path: Path) -> None:
    _build_kernel_binary()
    run_id, artifact = _persist_kernel_artifact_fixture(tmp_path)
    request = KernelArtifactVerifyRequest(
        protocol_version="0.89.0",
        request_id="artifact-root-missing",
        operation="artifact.verify",
        mode="DevelopmentCompatibility",
        payload={"run_id": run_id, "artifact": artifact},
    )

    response = _run_kernel(request.model_dump(mode="json"))

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "kernel_root_missing"


def test_rust_artifact_verification_rejects_python_invalid_identifier_grammar(
    tmp_path: Path,
) -> None:
    _build_kernel_binary()
    artifact = ArtifactRef(
        id=".artifact",
        type=ArtifactType.REPORT,
        path="runs/.run/reports/.artifact.json",
        content_hash="0" * 64,
        metadata={"is_verification_evidence": False},
    )
    request = KernelArtifactVerifyRequest(
        protocol_version="0.89.0",
        request_id="artifact-invalid-identifier",
        operation="artifact.verify",
        mode="DevelopmentCompatibility",
        payload={"run_id": ".run", "artifact": artifact},
    )

    response = _run_kernel(request.model_dump(mode="json"), root=tmp_path)

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "artifact_path_invalid"


def test_persisted_artifact_bridge_rejects_symlinked_artifacts(tmp_path: Path) -> None:
    _build_kernel_binary()
    run_id, artifact = _persist_kernel_artifact_fixture(tmp_path)
    artifact_path = tmp_path / artifact.path
    outside = tmp_path / "outside.json"
    outside.write_bytes(artifact_path.read_bytes())
    artifact_path.unlink()
    artifact_path.symlink_to(outside)
    from factori.kernel_bridge import KernelBridgeError, verify_persisted_artifact

    with pytest.raises(KernelBridgeError, match="artifact path"):
        verify_persisted_artifact(
            run_id,
            artifact,
            root=tmp_path,
            kernel_binary=KERNEL_BINARY,
        )


def test_rust_artifact_verification_rejects_symlinked_artifacts(tmp_path: Path) -> None:
    _build_kernel_binary()
    from factori.artifacts import ArtifactStore

    artifact = (
        ArtifactStore(tmp_path)
        .write_bytes(
            run_id="run-kernel-rust-symlink",
            artifact_id="artifact-symlink",
            artifact_type=ArtifactType.REPORT,
            content=b"context",
            extension="json",
            format_label="json",
        )
        .model_copy(update={"metadata": {"is_verification_evidence": False}})
    )
    artifact_path = tmp_path / artifact.path
    outside = tmp_path / "outside-rust.json"
    outside.write_bytes(artifact_path.read_bytes())
    artifact_path.unlink()
    artifact_path.symlink_to(outside)
    request = KernelArtifactVerifyRequest(
        protocol_version="0.89.0",
        request_id="artifact-rust-symlink",
        operation="artifact.verify",
        mode="DevelopmentCompatibility",
        payload={"run_id": "run-kernel-rust-symlink", "artifact": artifact},
    )

    response = _run_kernel(request.model_dump(mode="json"), root=tmp_path)

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "artifact_path_invalid"


def _persist_classifiable_artifact(
    tmp_path: Path,
    *,
    artifact_type: ArtifactType,
    metadata: dict[str, object] | None = None,
    artifact_id: str = "artifact-classify",
    extension: str = "json",
) -> tuple[str, ArtifactRef]:
    from factori.artifacts import ArtifactStore

    run_id = f"run-kernel-classify-{artifact_type.value}-{artifact_id}"
    artifact = ArtifactStore(tmp_path).write_bytes(
        run_id=run_id,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        content=b"classifiable artifact\n",
        extension=extension,
        format_label=extension,
        metadata=metadata,
    )
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    ledger.append_commit(
        run_id=run_id,
        action_type=ControllerActionType.WRITE_ARTIFACT,
        payload={"artifact_id": artifact.id},
        artifact_refs=[artifact],
        timestamp="2026-01-01T00:00:00.000000Z",
    )
    return run_id, ledger.list_commits(run_id)[0].artifact_refs[0]


def _classify_kernel_artifact(
    tmp_path: Path,
    run_id: str,
    artifact: ArtifactRef,
    *,
    mode: KernelMode = KernelMode.DEVELOPMENT_COMPATIBILITY,
) -> dict[str, object]:
    request = KernelEvidenceClassifyRequest(
        protocol_version="0.89.0",
        request_id=f"classify-{artifact.id}-{mode.value}",
        operation="evidence.classify",
        mode=mode,
        payload={"run_id": run_id, "artifact": artifact},
    )
    response = _run_kernel(request.model_dump(mode="json"), root=tmp_path)
    if response["status"] == "accepted":
        result = response["result"]
        assert result["authority_granted"] is False
        assert not {
            "verification_label",
            "capability",
            "capability_id",
        }.intersection(result)
    return response


def test_evidence_classification_preserves_non_authority_and_precedence(tmp_path: Path) -> None:
    _build_kernel_binary()
    report_run, report = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.REPORT,
    )
    response = _classify_kernel_artifact(tmp_path, report_run, report)
    assert response["status"] == "accepted"
    assert response["result"] == {
        "run_id": report_run,
        "artifact_id": report.id,
        "authority_class": "Presentation",
        "candidate_kind": None,
        "compatibility_only": False,
        "authority_granted": False,
    }

    context_run, context = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.REPORT,
        artifact_id="artifact-explicit-context",
        metadata={"is_verification_evidence": False, "evidence_role": "proof"},
    )
    response = _classify_kernel_artifact(tmp_path, context_run, context)
    assert response["status"] == "accepted"
    assert response["result"]["authority_class"] == "Context"
    assert response["result"]["authority_granted"] is False


def test_persisted_artifact_bridge_classifies_without_granting_authority(tmp_path: Path) -> None:
    _build_kernel_binary()
    run_id, artifact = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.LEAN,
        metadata={"evidence_role": "proof"},
        artifact_id="artifact-bridge-proof",
    )
    from factori.kernel_bridge import classify_persisted_artifact

    response = classify_persisted_artifact(
        run_id,
        artifact,
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status.value == "accepted"
    assert response.result.authority_class.value == "CapabilityCandidate"
    assert response.result.candidate_kind.value == "LeanProof"
    assert response.result.authority_granted is False


def test_evidence_classification_distinguishes_real_and_fake_candidates(tmp_path: Path) -> None:
    _build_kernel_binary()
    lean_run, lean = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.LEAN,
        metadata={"evidence_role": "proof"},
        artifact_id="artifact-proof",
    )
    response = _classify_kernel_artifact(tmp_path, lean_run, lean)
    assert response["status"] == "accepted"
    assert response["result"]["authority_class"] == "CapabilityCandidate"
    assert response["result"]["candidate_kind"] == "LeanProof"
    assert response["result"]["authority_granted"] is False

    fake_run, fake = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.LEAN,
        metadata={"evidence_role": "fake_proof"},
        artifact_id="artifact-fake-proof",
    )
    response = _classify_kernel_artifact(tmp_path, fake_run, fake)
    assert response["result"]["authority_class"] == "CapabilityCandidate"
    assert response["result"]["compatibility_only"] is True
    response = _classify_kernel_artifact(
        tmp_path,
        fake_run,
        fake,
        mode=KernelMode.STRICT_PRODUCTION,
    )
    assert response["status"] == "accepted"
    assert response["result"]["authority_class"] == "Context"
    assert response["diagnostics"][0]["code"] == "fake_backend_denied"


def test_evidence_classification_rejects_authority_mismatches_and_real_data(tmp_path: Path) -> None:
    _build_kernel_binary()
    wrong_run, wrong = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.REPORT,
        metadata={"evidence_role": "proof"},
        artifact_id="artifact-wrong-proof-type",
    )
    response = _classify_kernel_artifact(tmp_path, wrong_run, wrong)
    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "authority_denied"

    real_run, real = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.EXPERIMENT,
        metadata={"evidence_role": "real_data_experiment"},
        artifact_id="artifact-real-data",
    )
    response = _classify_kernel_artifact(tmp_path, real_run, real)
    assert response["status"] == "accepted"
    assert response["result"]["authority_class"] == "Context"
    response = _classify_kernel_artifact(
        tmp_path,
        real_run,
        real,
        mode=KernelMode.STRICT_PRODUCTION,
    )
    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "data_regime_denied"


@pytest.mark.parametrize("mode", list(KernelMode))
def test_evidence_classification_preserves_presentation_for_markdown_artifacts(
    tmp_path: Path,
    mode: KernelMode,
) -> None:
    _build_kernel_binary()
    report_run, markdown = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.REPORT,
        artifact_id="artifact-markdown",
        extension="md",
    )

    response = _classify_kernel_artifact(tmp_path, report_run, markdown, mode=mode)

    assert response["status"] == "accepted"
    assert response["result"]["authority_class"] == "Presentation"
    assert response["result"]["candidate_kind"] is None

    override_run, override = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.REPORT,
        metadata={"is_verification_evidence": True},
        artifact_id="artifact-markdown-override",
        extension="md",
    )
    response = _classify_kernel_artifact(tmp_path, override_run, override, mode=mode)
    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "artifact_presentation_override"


@pytest.mark.parametrize("mode", list(KernelMode))
def test_evidence_classification_rejects_forged_role_metadata(
    tmp_path: Path,
    mode: KernelMode,
) -> None:
    _build_kernel_binary()
    run_id, artifact = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.LEAN,
        artifact_id="artifact-forged-role",
    )
    forged = artifact.model_copy(
        update={"metadata": {**artifact.metadata, "evidence_role": "proof"}}
    )

    response = _classify_kernel_artifact(tmp_path, run_id, forged, mode=mode)

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "artifact_producer_link_mismatch"


@pytest.mark.parametrize("mode", list(KernelMode))
def test_evidence_classification_rejects_explicit_authority_without_role(
    tmp_path: Path,
    mode: KernelMode,
) -> None:
    _build_kernel_binary()
    run_id, artifact = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.LEAN,
        metadata={"is_verification_evidence": True},
        artifact_id="artifact-missing-role",
    )

    response = _classify_kernel_artifact(tmp_path, run_id, artifact, mode=mode)

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "authority_denied"


@pytest.mark.parametrize("mode", list(KernelMode))
def test_evidence_classification_covers_role_precedence_in_both_modes(
    tmp_path: Path,
    mode: KernelMode,
) -> None:
    _build_kernel_binary()
    literature_run, literature = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.LITERATURE,
        artifact_id="artifact-literature",
    )
    response = _classify_kernel_artifact(
        tmp_path,
        literature_run,
        literature,
        mode=mode,
    )
    assert response["status"] == "accepted"
    assert response["result"]["authority_class"] == "Context"

    unknown_run, unknown = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.LEAN,
        metadata={"evidence_role": "llm_review"},
        artifact_id="artifact-unknown-role",
    )
    response = _classify_kernel_artifact(tmp_path, unknown_run, unknown, mode=mode)
    assert response["status"] == "accepted"
    assert response["result"]["authority_class"] == "Context"

    unknown_authority_run, unknown_authority = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.LEAN,
        metadata={"is_verification_evidence": True, "evidence_role": "llm_review"},
        artifact_id="artifact-unknown-authority-role",
    )
    response = _classify_kernel_artifact(
        tmp_path,
        unknown_authority_run,
        unknown_authority,
        mode=mode,
    )
    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "authority_denied"

    missing_run, missing = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.LEAN,
        artifact_id="artifact-missing-role-context",
    )
    response = _classify_kernel_artifact(tmp_path, missing_run, missing, mode=mode)
    assert response["status"] == "accepted"
    assert response["result"]["authority_class"] == "Context"

    explicit_false_run, explicit_false = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.LEAN,
        metadata={"is_verification_evidence": False, "evidence_role": "proof"},
        artifact_id="artifact-explicit-false",
    )
    response = _classify_kernel_artifact(
        tmp_path,
        explicit_false_run,
        explicit_false,
        mode=mode,
    )
    assert response["status"] == "accepted"
    assert response["result"]["authority_class"] == "Context"

    synthetic_run, synthetic = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.EXPERIMENT,
        metadata={"evidence_role": "synthetic_experiment"},
        artifact_id="artifact-synthetic",
    )
    response = _classify_kernel_artifact(tmp_path, synthetic_run, synthetic, mode=mode)
    assert response["status"] == "accepted"
    assert response["result"]["authority_class"] == "CapabilityCandidate"
    assert response["result"]["candidate_kind"] == "SyntheticExperiment"
    assert response["result"]["compatibility_only"] is False

    fake_run, fake = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.EXPERIMENT,
        metadata={"evidence_role": "fake_synthetic_experiment"},
        artifact_id="artifact-fake-synthetic",
    )
    response = _classify_kernel_artifact(tmp_path, fake_run, fake, mode=mode)
    assert response["status"] == "accepted"
    if mode == KernelMode.DEVELOPMENT_COMPATIBILITY:
        assert response["result"]["authority_class"] == "CapabilityCandidate"
        assert response["result"]["candidate_kind"] == "SyntheticExperiment"
        assert response["result"]["compatibility_only"] is True
    else:
        assert response["result"]["authority_class"] == "Context"
        assert response["diagnostics"][0]["code"] == "fake_backend_denied"


@pytest.mark.parametrize("mode", list(KernelMode))
@pytest.mark.parametrize(
    ("artifact_type", "role"),
    [
        (ArtifactType.REPORT, "proof"),
        (ArtifactType.LEAN, "synthetic_experiment"),
    ],
)
def test_evidence_classification_rejects_wrong_role_type_pairs_in_both_modes(
    tmp_path: Path,
    mode: KernelMode,
    artifact_type: ArtifactType,
    role: str,
) -> None:
    _build_kernel_binary()
    run_id, artifact = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=artifact_type,
        metadata={"evidence_role": role},
        artifact_id=f"artifact-wrong-{artifact_type.value}-{role}",
    )

    response = _classify_kernel_artifact(tmp_path, run_id, artifact, mode=mode)

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "authority_denied"


@pytest.mark.parametrize("mode", list(KernelMode))
def test_evidence_classification_rejects_unlinked_candidates_in_both_modes(
    tmp_path: Path,
    mode: KernelMode,
) -> None:
    _build_kernel_binary()
    from factori.artifacts import ArtifactStore

    run_id = "run-kernel-classify-unlinked"
    artifact = ArtifactStore(tmp_path).write_bytes(
        run_id=run_id,
        artifact_id="artifact-unlinked-proof",
        artifact_type=ArtifactType.LEAN,
        content=b"unlinked proof\n",
        extension="json",
        format_label="json",
        metadata={"evidence_role": "proof"},
    )

    response = _classify_kernel_artifact(tmp_path, run_id, artifact, mode=mode)

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "artifact_producer_missing"


@pytest.mark.parametrize("mode", list(KernelMode))
def test_evidence_classification_rejects_tampered_bytes_in_both_modes(
    tmp_path: Path,
    mode: KernelMode,
) -> None:
    _build_kernel_binary()
    run_id, artifact = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.LEAN,
        metadata={"evidence_role": "proof"},
        artifact_id="artifact-tampered-proof",
    )
    (tmp_path / artifact.path).write_bytes(b"tampered proof\n")

    response = _classify_kernel_artifact(tmp_path, run_id, artifact, mode=mode)

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "artifact_hash_mismatch"


@pytest.mark.parametrize("mode", list(KernelMode))
def test_evidence_classification_rejects_corrupt_ledgers_in_both_modes(
    tmp_path: Path,
    mode: KernelMode,
) -> None:
    _build_kernel_binary()
    import sqlite3

    run_id, artifact = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.LEAN,
        metadata={"evidence_role": "proof"},
        artifact_id="artifact-corrupt-ledger-proof",
    )
    with sqlite3.connect(tmp_path / "runs" / run_id / "ledger.sqlite") as connection:
        connection.execute("DROP TRIGGER commits_no_update")
        connection.execute(
            "UPDATE commits SET payload_json = ? WHERE commit_hash = ?",
            ('{"artifact_id":"tampered"}', artifact.producing_commit_hash),
        )

    response = _classify_kernel_artifact(tmp_path, run_id, artifact, mode=mode)

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "ledger_hash_mismatch"


@pytest.mark.parametrize("mode", list(KernelMode))
def test_evidence_classification_rejects_cross_run_producers_in_both_modes(
    tmp_path: Path,
    mode: KernelMode,
) -> None:
    _build_kernel_binary()
    from factori.artifacts import ArtifactStore

    _, source = _persist_classifiable_artifact(
        tmp_path,
        artifact_type=ArtifactType.LEAN,
        metadata={"evidence_role": "proof"},
        artifact_id="artifact-source-proof",
    )
    target_run = "run-kernel-classify-cross-run-target"
    target = ArtifactStore(tmp_path).write_bytes(
        run_id=target_run,
        artifact_id="artifact-target-proof",
        artifact_type=ArtifactType.LEAN,
        content=b"classifiable artifact\n",
        extension="json",
        format_label="json",
        metadata={"evidence_role": "proof"},
    )
    ResearchLedger(tmp_path / "runs" / target_run / "ledger.sqlite")
    cross_run = target.model_copy(update={"producing_commit_hash": source.producing_commit_hash})

    response = _classify_kernel_artifact(tmp_path, target_run, cross_run, mode=mode)

    assert response["status"] == "rejected"
    assert response["diagnostics"][0]["code"] == "artifact_producer_missing"


@pytest.mark.parametrize(
    "update",
    [
        {"authority_class": "Context", "candidate_kind": "LeanProof"},
        {"authority_class": "CapabilityCandidate", "candidate_kind": None},
        {"authority_class": "Context", "compatibility_only": True},
        {"authority_granted": 0},
    ],
)
def test_evidence_classification_result_rejects_incoherent_shapes(
    update: dict[str, object],
) -> None:
    payload = {
        "run_id": "run-kernel-classify",
        "artifact_id": "artifact-classify",
        "authority_class": "Context",
        "candidate_kind": None,
        "compatibility_only": False,
        "authority_granted": False,
    }

    with pytest.raises(ValidationError):
        KernelEvidenceClassifyResult.model_validate({**payload, **update})


def test_rust_kernel_cli_rejects_oversized_requests() -> None:
    _build_kernel_binary()

    completed = subprocess.run(
        [str(KERNEL_BINARY)],
        input=b" " * (16 * 1024 * 1024 + 1),
        capture_output=True,
        check=False,
    )
    response = json.loads(completed.stdout)

    assert completed.returncode == 2
    assert response["status"] == "error"
    assert response["diagnostics"][0]["code"] == "transport_invalid"
    assert "transport limit" in response["diagnostics"][0]["message"]
