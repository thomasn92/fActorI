from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from factori.artifacts import ARTIFACT_DIRECTORY_BY_TYPE, ArtifactStore
from factori.hashing import canonical_json, sha256_bytes
from factori.kernel_bridge import persist_json_artifact
from factori.protocols import PROTOCOL_VERSION
from factori.schemas import ArtifactType, KernelResponseEnvelope, KernelResponseStatus

KERNEL_BINARY = Path(__file__).parent / ".." / "rust-kernel" / "target" / "debug" / "factori-kernel"


@pytest.fixture(scope="module", autouse=True)
def build_kernel_binary() -> None:
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


def _request(
    *,
    run_id: str = "run-persist",
    artifact_id: str = "artifact-1",
    artifact_type: str = "candidate",
    json_value: object | None = None,
    metadata: dict[str, object] | None = None,
    filename_stem_optional: str | None = None,
) -> dict[str, object]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": f"persist-{artifact_id}",
        "operation": "artifact.persist",
        "mode": "DevelopmentCompatibility",
        "payload": {
            "run_id": run_id,
            "artifact_id": artifact_id,
            "artifact_type": artifact_type,
            "json_value": {} if json_value is None else json_value,
            "metadata": {} if metadata is None else metadata,
            "filename_stem_optional": filename_stem_optional,
            "overwrite_policy": "FailIfExists",
        },
    }


def _run_kernel(root: Path, request: dict[str, object]) -> KernelResponseEnvelope:
    completed = subprocess.run(
        [str(KERNEL_BINARY), "--root", str(root)],
        input=json.dumps(request) + "\n",
        capture_output=True,
        check=True,
        text=True,
    )
    return KernelResponseEnvelope.model_validate_json(completed.stdout)


def test_artifact_persist_bridge_matches_python_bytes_for_every_type(tmp_path: Path) -> None:
    run_id = "run-persist"
    ArtifactStore(tmp_path).init_run(run_id)
    ledger_path = tmp_path / "runs" / run_id / "ledger.sqlite"
    ledger_path.write_bytes(b"ledger-sentinel")
    value = {
        "nested": {"unicode": "é水", "control": "line\nfeed"},
        "numbers": [0, -0.0, 1.25, 1e20],
        "empty": {},
    }
    expected_bytes = (canonical_json(value) + "\n").encode("utf-8")

    for index, artifact_type in enumerate(ArtifactType):
        artifact_id = f"artifact-{index}"
        stem = f"stored-{index}"
        response = persist_json_artifact(
            run_id,
            artifact_id,
            artifact_type,
            value,
            metadata={"note": "context only"},
            filename_stem_optional=stem,
            root=tmp_path,
            kernel_binary=KERNEL_BINARY,
        )
        assert response.status == KernelResponseStatus.ACCEPTED
        assert response.mutation_performed is True
        result = response.result
        assert result.bytes_written == len(expected_bytes)  # type: ignore[union-attr]
        artifact = result.artifact  # type: ignore[union-attr]
        assert artifact.content_hash == sha256_bytes(expected_bytes)
        assert artifact.producing_commit_hash is None
        assert artifact.metadata == {
            "note": "context only",
            "format": "json",
            "is_verification_evidence": False,
        }
        path = (
            tmp_path / "runs" / run_id / ARTIFACT_DIRECTORY_BY_TYPE[artifact_type] / f"{stem}.json"
        )
        assert path.read_bytes() == expected_bytes
        assert not path.with_name(path.name + ".meta.json").exists()

    assert ledger_path.read_bytes() == b"ledger-sentinel"


@pytest.mark.parametrize(
    ("payload_update", "expected_code"),
    [
        ({"run_id": "../escape"}, "artifact_persist_payload_invalid"),
        ({"artifact_id": "../escape"}, "artifact_persist_payload_invalid"),
        ({"filename_stem_optional": "../escape"}, "artifact_persist_payload_invalid"),
        ({"artifact_type": "binary"}, "artifact_persist_payload_invalid"),
        ({"overwrite_policy": "Replace"}, "artifact_persist_payload_invalid"),
        (
            {"metadata": {"publication_ready": True}},
            "artifact_persist_payload_invalid",
        ),
        (
            {"metadata": {"is_verification_evidence": False}},
            "artifact_persist_payload_invalid",
        ),
    ],
)
def test_artifact_persist_rejects_invalid_payload_without_mutation(
    tmp_path: Path,
    payload_update: dict[str, object],
    expected_code: str,
) -> None:
    ArtifactStore(tmp_path).init_run("run-persist")
    request = _request()
    request_payload = request["payload"]
    assert isinstance(request_payload, dict)
    request_payload.update(payload_update)

    response = _run_kernel(tmp_path, request)

    assert response.status == KernelResponseStatus.REJECTED
    assert response.diagnostics[0].code == expected_code
    assert response.mutation_performed is False
    assert not (tmp_path / "runs/run-persist/candidates/artifact-1.json").exists()


def test_artifact_persist_rejects_missing_and_symlink_directories(tmp_path: Path) -> None:
    (tmp_path / "runs" / "run-persist").mkdir(parents=True)
    missing = _run_kernel(tmp_path, _request())
    assert missing.status == KernelResponseStatus.REJECTED
    assert missing.diagnostics[0].code == "artifact_persist_directory_invalid"
    assert missing.mutation_performed is False

    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "runs/run-persist/candidates").symlink_to(outside, target_is_directory=True)
    symlinked = _run_kernel(tmp_path, _request())
    assert symlinked.status == KernelResponseStatus.REJECTED
    assert symlinked.diagnostics[0].code == "artifact_persist_directory_invalid"
    assert not (outside / "artifact-1.json").exists()


@pytest.mark.parametrize(
    "persist_request",
    [
        _request(json_value="x" * (12 * 1024 * 1024)),
        _request(metadata={"note": "x" * (64 * 1024)}),
    ],
)
def test_artifact_persist_rejects_serialized_size_overflow(
    tmp_path: Path,
    persist_request: dict[str, object],
) -> None:
    ArtifactStore(tmp_path).init_run("run-persist")

    response = _run_kernel(tmp_path, persist_request)

    assert response.status == KernelResponseStatus.REJECTED
    assert response.diagnostics[0].code == "artifact_persist_size_exceeded"
    assert response.mutation_performed is False
    assert not (tmp_path / "runs/run-persist/candidates/artifact-1.json").exists()


@pytest.mark.parametrize("existing_kind", ["file", "directory", "symlink", "dangling", "sidecar"])
def test_artifact_persist_fail_if_exists_covers_all_target_kinds(
    tmp_path: Path,
    existing_kind: str,
) -> None:
    ArtifactStore(tmp_path).init_run("run-persist")
    target = tmp_path / "runs/run-persist/candidates/artifact-1.json"
    if existing_kind == "file":
        target.write_bytes(b"existing")
    elif existing_kind == "directory":
        target.mkdir()
    elif existing_kind == "symlink":
        source = tmp_path / "source.json"
        source.write_bytes(b"source")
        target.symlink_to(source)
    elif existing_kind == "dangling":
        target.symlink_to(tmp_path / "missing.json")
    else:
        target.with_name(target.name + ".meta.json").write_bytes(b"sidecar")

    response = _run_kernel(tmp_path, _request())

    assert response.status == KernelResponseStatus.REJECTED
    assert response.diagnostics[0].code == "artifact_persist_target_exists"
    assert response.mutation_performed is False


def test_artifact_persist_concurrent_same_target_has_one_winner(tmp_path: Path) -> None:
    ArtifactStore(tmp_path).init_run("run-persist")
    input_text = json.dumps(_request(json_value={"race": True})) + "\n"
    processes = [
        subprocess.Popen(
            [str(KERNEL_BINARY), "--root", str(tmp_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(2)
    ]
    outputs = [process.communicate(input_text, timeout=10)[0] for process in processes]
    responses = [KernelResponseEnvelope.model_validate_json(output) for output in outputs]

    assert [response.status for response in responses].count(KernelResponseStatus.ACCEPTED) == 1
    loser = next(
        response for response in responses if response.status == KernelResponseStatus.REJECTED
    )
    assert loser.diagnostics[0].code == "artifact_persist_target_exists"
    assert loser.mutation_performed is False
    assert (tmp_path / "runs/run-persist/candidates/artifact-1.json").read_text(
        encoding="utf-8"
    ) == '{"race":true}\n'


def _accepted_response() -> dict[str, object]:
    return {
        "protocol_version": PROTOCOL_VERSION,
        "kernel_version": "0.1.0-dev",
        "request_id": "persist-artifact-1",
        "operation": "artifact.persist",
        "mode": "DevelopmentCompatibility",
        "status": "accepted",
        "result": {
            "artifact": {
                "id": "artifact-1",
                "type": "candidate",
                "path": "runs/run-persist/candidates/artifact-1.json",
                "content_hash": "0" * 64,
                "producing_commit_hash": None,
                "metadata": {"format": "json", "is_verification_evidence": False},
            },
            "bytes_written": 3,
            "created": True,
            "linked_to_ledger": False,
            "authority_granted": False,
        },
        "diagnostics": [],
        "mutation_performed": True,
    }


@pytest.mark.parametrize(
    "mutation",
    [
        "accepted_false",
        "accepted_wrong_diagnostic",
        "wrong_path",
        "evidence_true",
        "authority_metadata",
        "postpublish_rejected",
        "postpublish_wrong_code",
        "prepublish_cleanup_warning",
    ],
)
def test_artifact_persist_response_schema_rejects_malformed_semantics(mutation: str) -> None:
    response = _accepted_response()
    result = response["result"]
    assert isinstance(result, dict)
    artifact = result["artifact"]
    assert isinstance(artifact, dict)
    metadata = artifact["metadata"]
    assert isinstance(metadata, dict)
    if mutation == "accepted_false":
        response["mutation_performed"] = False
    elif mutation == "accepted_wrong_diagnostic":
        response["diagnostics"] = [{"code": "other_warning", "message": "bad", "path": None}]
    elif mutation == "wrong_path":
        artifact["path"] = "runs/run-persist/reports/artifact-1.json"
    elif mutation == "evidence_true":
        metadata["is_verification_evidence"] = True
    elif mutation == "authority_metadata":
        metadata["publication_ready"] = True
    elif mutation == "prepublish_cleanup_warning":
        response["status"] = "rejected"
        response["result"] = {}
        response["diagnostics"] = [
            {
                "code": "artifact_persist_temp_cleanup_warning",
                "message": "bad",
                "path": "payload",
            }
        ]
        response["mutation_performed"] = False
    else:
        response["status"] = "rejected" if mutation == "postpublish_rejected" else "error"
        response["result"] = {}
        response["diagnostics"] = [
            {
                "code": (
                    "artifact_persist_postcondition_failed"
                    if mutation == "postpublish_rejected"
                    else "artifact_persist_publish_failed"
                ),
                "message": "bad",
                "path": "payload",
            }
        ]
        response["mutation_performed"] = True

    with pytest.raises(ValidationError):
        KernelResponseEnvelope.model_validate(response)


def test_rust_protocol_validation_rejects_malformed_persist_response(tmp_path: Path) -> None:
    malformed = _accepted_response()
    malformed["mutation_performed"] = False
    request = {
        "protocol_version": PROTOCOL_VERSION,
        "request_id": "validate-persist-response",
        "operation": "protocol.validate",
        "mode": "DevelopmentCompatibility",
        "payload": {
            "protocol_name": "KernelResponseEnvelope",
            "instance": malformed,
        },
    }

    response = _run_kernel(tmp_path, request)

    assert response.status == KernelResponseStatus.REJECTED
    assert response.diagnostics[0].code == "protocol_invalid"
    assert response.mutation_performed is False
