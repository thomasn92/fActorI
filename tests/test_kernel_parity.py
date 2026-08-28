from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from factori.hashing import canonical_json, sha256_file, sha256_text
from factori.ledger import ResearchLedger
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    KernelArtifactVerifyRequest,
    KernelEvidenceClassifyRequest,
    KernelEvidenceClassifyResult,
    KernelLedgerVerifyRequest,
    KernelMode,
    KernelRequestEnvelope,
    KernelResponseEnvelope,
)

FIXTURE = Path(__file__).parent / ".." / "rust-kernel" / "fixtures" / "canonical-json.json"
LEDGER_FIXTURE = (
    Path(__file__).parent / ".." / "rust-kernel" / "fixtures" / "ledger-commit-hashes.json"
)
KERNEL_BINARY = Path(__file__).parent / ".." / "rust-kernel" / "target" / "debug" / "factori-kernel"


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
            "protocol_version": "0.82.0",
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
                "protocol_version": "0.82.0",
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
                "protocol_version": "0.82.0",
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
                "protocol_version": "0.82.0",
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
            "KernelResponseEnvelope",
            KernelResponseEnvelope,
            {
                "protocol_version": "0.82.0",
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
                "protocol_version": "0.82.0",
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
                "protocol_version": "0.82.0",
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
                "protocol_version": "0.82.0",
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
                "protocol_version": "0.82.0",
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
                "protocol_version": "0.82.0",
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
            "protocol_version": "0.82.0",
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
        "protocol_version": "0.82.0",
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
        "protocol_version": "0.82.0",
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
        protocol_version="0.82.0",
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
        "protocol_version": "0.82.0",
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
        "protocol_version": "0.82.0",
        "request_id": "nested-ledger-invalid",
        "operation": "ledger.verify",
        "mode": "DevelopmentCompatibility",
        "payload": {"run_id": "run-kernel-ledger", "commits": [{"commit_hash": "bad"}]},
    }
    response = _run_kernel(
        {
            "protocol_version": "0.82.0",
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
            "protocol_version": "0.82.0",
            "request_id": "validate-nested-ledger-semantic",
            "operation": "protocol.validate",
            "mode": "DevelopmentCompatibility",
            "payload": {
                "protocol_name": "KernelRequestEnvelope",
                "instance": {
                    "protocol_version": "0.82.0",
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
            protocol_version="0.82.0",
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
            "protocol_version": "0.82.0",
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
            "protocol_version": "0.82.0",
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
            protocol_version="0.82.0",
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


def test_rust_artifact_verification_rejects_tampered_raw_bytes(tmp_path: Path) -> None:
    _build_kernel_binary()
    run_id, artifact = _persist_kernel_artifact_fixture(tmp_path)
    request = KernelArtifactVerifyRequest(
        protocol_version="0.82.0",
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
        "protocol_version": "0.82.0",
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
        protocol_version="0.82.0",
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
        protocol_version="0.82.0",
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
        protocol_version="0.82.0",
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
        protocol_version="0.82.0",
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
        protocol_version="0.82.0",
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
        protocol_version="0.82.0",
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
        protocol_version="0.82.0",
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
        protocol_version="0.82.0",
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
    cross_run = target.model_copy(
        update={"producing_commit_hash": source.producing_commit_hash}
    )

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
