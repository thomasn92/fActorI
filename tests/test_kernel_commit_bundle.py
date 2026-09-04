from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError

from factori.artifacts import ARTIFACT_DIRECTORY_BY_TYPE, ArtifactStore
from factori.hashing import canonical_json, sha256_bytes, sha256_text
from factori.kernel_bridge import KernelBridgeError, commit_artifact_bundle
from factori.ledger import ResearchLedger, compute_commit_hash
from factori.protocols import PROTOCOL_VERSION
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    ControllerActionType,
    KernelCommitBundleArtifact,
    KernelMode,
    KernelPersistenceCommitBundlePayload,
    KernelPersistenceCommitBundleRequest,
    KernelResponseEnvelope,
    KernelResponseStatus,
)

KERNEL_BINARY = Path(__file__).parent.parent / "rust-kernel" / "target" / "debug" / "factori-kernel"


@pytest.fixture(scope="module", autouse=True)
def build_kernel_binary() -> None:
    subprocess.run(
        ["cargo", "build", "--manifest-path", "rust-kernel/Cargo.toml", "--locked", "--offline"],
        check=True,
        capture_output=True,
        text=True,
    )


def _fixture(tmp_path: Path) -> tuple[str, ResearchLedger, str]:
    run_id = "run-bundle"
    ArtifactStore(tmp_path).init_run(run_id)
    ledger = ResearchLedger(tmp_path / "runs" / run_id / "ledger.sqlite")
    root = ledger.append_commit(
        run_id=run_id,
        action_type=ControllerActionType.INIT_RUN,
        payload={},
        timestamp="2026-01-01T00:00:00Z",
    )
    return run_id, ledger, root.commit_hash


def _expected_intent(
    run_id: str,
    tip: str,
    artifacts: list[KernelCommitBundleArtifact],
    *,
    action_type: ControllerActionType = ControllerActionType.WRITE_ARTIFACT,
    commit_payload: dict[str, object] | None = None,
    candidate_id: str | None = None,
    timestamp: str = "2026-01-01T00:00:01Z",
) -> tuple[bytes, dict[Path, bytes], str]:
    payload = KernelPersistenceCommitBundlePayload(
        run_id=run_id,
        expected_tip_hash=tip,
        artifacts=artifacts,
        action_type=action_type,
        commit_payload=commit_payload or {},
        candidate_id_optional=candidate_id,
        timestamp=timestamp,
    )
    files: dict[Path, bytes] = {}
    refs = []
    for item in artifacts:
        directory = ARTIFACT_DIRECTORY_BY_TYPE[item.artifact_type]
        stem = item.filename_stem_optional or item.artifact_id
        path = Path("runs") / run_id / directory / f"{stem}.json"
        value_bytes = (canonical_json(item.json_value) + "\n").encode()
        files[path] = value_bytes
        refs.append(
            ArtifactRef(
                id=item.artifact_id,
                type=item.artifact_type,
                path=path.as_posix(),
                content_hash=sha256_bytes(value_bytes),
                producing_commit_hash=None,
                metadata={
                    **item.metadata,
                    "format": "json",
                    "is_verification_evidence": False,
                },
            )
        )
    commit_hash = compute_commit_hash(
        parent_hash=tip,
        run_id=run_id,
        candidate_id=candidate_id,
        action_type=action_type,
        payload=commit_payload or {},
        artifact_refs=refs,
        timestamp=timestamp,
        self_link_artifact_ids={ref.id for ref in refs},
    )
    linked = [ref.model_copy(update={"producing_commit_hash": commit_hash}) for ref in refs]
    outputs = []
    for ref in linked:
        artifact_path = Path(ref.path)
        sidecar_path = Path(f"{ref.path}.meta.json")
        sidecar_bytes = (canonical_json(ref.model_dump(mode="json")) + "\n").encode()
        files[sidecar_path] = sidecar_bytes
        outputs.append(
            {
                "artifact_path": artifact_path.as_posix(),
                "artifact_length": len(files[artifact_path]),
                "artifact_hash": sha256_bytes(files[artifact_path]),
                "sidecar_path": sidecar_path.as_posix(),
                "sidecar_length": len(sidecar_bytes),
                "sidecar_hash": sha256_bytes(sidecar_bytes),
            }
        )
    fingerprint = sha256_text(
        canonical_json(
            {
                "protocol_version": PROTOCOL_VERSION,
                "operation": "persistence.commit_bundle",
                "payload": payload.model_dump(mode="json"),
            }
        )
    )
    intent = {
        "protocol_version": PROTOCOL_VERSION,
        "operation": "persistence.commit_bundle",
        "fingerprint": fingerprint,
        "expected_tip_hash": tip,
        "new_commit_hash": commit_hash,
        "outputs": outputs,
    }
    return (canonical_json(intent) + "\n").encode(), files, commit_hash


def _kernel_request(
    run_id: str,
    tip: str,
    artifacts: list[KernelCommitBundleArtifact],
) -> KernelPersistenceCommitBundleRequest:
    return KernelPersistenceCommitBundleRequest(
        protocol_version=PROTOCOL_VERSION,
        request_id="bundle-test",
        operation="persistence.commit_bundle",
        mode="DevelopmentCompatibility",
        payload=KernelPersistenceCommitBundlePayload(
            run_id=run_id,
            expected_tip_hash=tip,
            artifacts=artifacts,
            action_type=ControllerActionType.WRITE_ARTIFACT,
            commit_payload={},
            candidate_id_optional=None,
            timestamp="2026-01-01T00:00:01Z",
        ),
    )


def _run_kernel(
    root: Path,
    request: KernelPersistenceCommitBundleRequest,
) -> KernelResponseEnvelope:
    completed = subprocess.run(
        [str(KERNEL_BINARY), "--root", str(root)],
        input=json.dumps(request.model_dump(mode="json")) + "\n",
        capture_output=True,
        check=True,
        text=True,
    )
    return KernelResponseEnvelope.model_validate_json(completed.stdout)


@pytest.mark.parametrize("mode", list(KernelMode))
def test_commit_artifact_bundle_persists_artifacts_sidecars_and_commit(
    tmp_path: Path,
    mode: KernelMode,
) -> None:
    run_id, ledger, tip = _fixture(tmp_path)
    artifacts = [
        KernelCommitBundleArtifact(
            artifact_id="candidate-1",
            artifact_type="candidate",
            json_value={"b": 2, "a": 1},
            metadata={"context": "test"},
        ),
        KernelCommitBundleArtifact(
            artifact_id="report-1",
            artifact_type="report",
            json_value=["ok", 1],
        ),
    ]

    response = commit_artifact_bundle(
        run_id,
        tip,
        artifacts,
        ControllerActionType.WRITE_ARTIFACT,
        {"artifact_ids": ["candidate-1", "report-1"]},
        timestamp="2026-01-01T00:00:01Z",
        mode=mode,
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status is KernelResponseStatus.ACCEPTED
    assert response.mutation_performed is True
    result = response.result
    assert result.artifact_count == 2  # type: ignore[union-attr]
    assert result.sidecar_count == 2  # type: ignore[union-attr]
    assert result.recovered_from_intent is False  # type: ignore[union-attr]
    assert result.authority_granted is False  # type: ignore[union-attr]
    assert len(ledger.list_commits_read_only()) == 2
    for artifact in result.artifacts:  # type: ignore[union-attr]
        path = tmp_path / artifact.path
        assert path.is_file()
        assert (tmp_path / f"{artifact.path}.meta.json").is_file()
        assert artifact.producing_commit_hash == result.new_tip_hash  # type: ignore[union-attr]


def test_commit_artifact_bundle_rejects_existing_destination_without_mutation(
    tmp_path: Path,
) -> None:
    run_id, ledger, tip = _fixture(tmp_path)
    destination = tmp_path / "runs" / run_id / "candidates" / "candidate-1.json"
    destination.write_bytes(b"existing\n")
    with pytest.raises(KernelBridgeError, match="destination already exists"):
        commit_artifact_bundle(
            run_id,
            tip,
            [
                KernelCommitBundleArtifact(
                    artifact_id="candidate-1", artifact_type="candidate", json_value={}
                )
            ],
            ControllerActionType.WRITE_ARTIFACT,
            {},
            timestamp="2026-01-01T00:00:01Z",
            root=tmp_path,
            kernel_binary=KERNEL_BINARY,
        )
    assert destination.read_bytes() == b"existing\n"
    assert len(ledger.list_commits_read_only()) == 1


@pytest.mark.parametrize("mode", list(KernelMode))
def test_commit_bundle_recovers_intent_only_state(tmp_path: Path, mode: KernelMode) -> None:
    run_id, ledger, tip = _fixture(tmp_path)
    artifacts = [
        KernelCommitBundleArtifact(
            artifact_id="candidate-1",
            artifact_type="candidate",
            json_value={"unicode": "é水", "control": "line\nfeed", "negative_zero": -0.0},
            filename_stem_optional="custom-stem",
        )
    ]
    intent, files, _ = _expected_intent(run_id, tip, artifacts)
    intent_path = tmp_path / "runs" / run_id / ".factori-commit-bundle.intent.json"
    intent_path.write_bytes(intent)

    response = commit_artifact_bundle(
        run_id,
        tip,
        artifacts,
        ControllerActionType.WRITE_ARTIFACT,
        {},
        timestamp="2026-01-01T00:00:01Z",
        mode=mode,
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status is KernelResponseStatus.ACCEPTED
    assert response.result.recovered_from_intent is True  # type: ignore[union-attr]
    assert len(ledger.list_commits_read_only()) == 2
    assert not intent_path.exists()
    assert all((tmp_path / path).read_bytes() == value for path, value in files.items())


def test_commit_bundle_recovers_partial_output_state(tmp_path: Path) -> None:
    run_id, ledger, tip = _fixture(tmp_path)
    artifacts = [
        KernelCommitBundleArtifact(
            artifact_id="candidate-1", artifact_type="candidate", json_value={"x": 1}
        ),
        KernelCommitBundleArtifact(
            artifact_id="report-1", artifact_type="report", json_value={"x": 2}
        ),
    ]
    intent, files, _ = _expected_intent(run_id, tip, artifacts)
    intent_path = tmp_path / "runs" / run_id / ".factori-commit-bundle.intent.json"
    intent_path.write_bytes(intent)
    first_artifact = next(path for path in files if not path.name.endswith(".meta.json"))
    (tmp_path / first_artifact).write_bytes(files[first_artifact])

    response = commit_artifact_bundle(
        run_id,
        tip,
        artifacts,
        ControllerActionType.WRITE_ARTIFACT,
        {},
        timestamp="2026-01-01T00:00:01Z",
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert response.status is KernelResponseStatus.ACCEPTED
    assert response.result.recovered_from_intent is True  # type: ignore[union-attr]
    assert len(ledger.list_commits_read_only()) == 2
    assert all((tmp_path / path).read_bytes() == value for path, value in files.items())


def test_commit_bundle_recovers_committed_precleanup_state(tmp_path: Path) -> None:
    run_id, ledger, tip = _fixture(tmp_path)
    artifacts = [
        KernelCommitBundleArtifact(
            artifact_id="candidate-1", artifact_type="candidate", json_value={"x": 1}
        )
    ]
    intent, _, expected_hash = _expected_intent(run_id, tip, artifacts)
    first = commit_artifact_bundle(
        run_id,
        tip,
        artifacts,
        ControllerActionType.WRITE_ARTIFACT,
        {},
        timestamp="2026-01-01T00:00:01Z",
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )
    assert first.status is KernelResponseStatus.ACCEPTED
    intent_path = tmp_path / "runs" / run_id / ".factori-commit-bundle.intent.json"
    intent_path.write_bytes(intent)

    recovered = commit_artifact_bundle(
        run_id,
        tip,
        artifacts,
        ControllerActionType.WRITE_ARTIFACT,
        {},
        timestamp="2026-01-01T00:00:01Z",
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )

    assert recovered.status is KernelResponseStatus.ACCEPTED
    assert recovered.result.recovered_from_intent is True  # type: ignore[union-attr]
    assert recovered.result.new_tip_hash == expected_hash  # type: ignore[union-attr]
    assert len(ledger.list_commits_read_only()) == 2
    assert not intent_path.exists()


@pytest.mark.parametrize("damage", ["extra", "noncanonical", "symlink"])
def test_commit_bundle_rejects_invalid_intent_without_mutation(
    tmp_path: Path,
    damage: str,
) -> None:
    run_id, ledger, tip = _fixture(tmp_path)
    artifacts = [
        KernelCommitBundleArtifact(
            artifact_id="candidate-1", artifact_type="candidate", json_value={}
        )
    ]
    intent, _, _ = _expected_intent(run_id, tip, artifacts)
    intent_path = tmp_path / "runs" / run_id / ".factori-commit-bundle.intent.json"
    if damage == "extra":
        value = json.loads(intent)
        value["raw_secret"] = "must not be accepted"
        intent_path.write_bytes((canonical_json(value) + "\n").encode())
    elif damage == "noncanonical":
        intent_path.write_bytes(b"  " + intent)
    else:
        target = tmp_path / "foreign-intent.json"
        target.write_bytes(intent)
        intent_path.symlink_to(target)
    before = ledger.list_commits_read_only()

    with pytest.raises(KernelBridgeError, match="intent"):
        commit_artifact_bundle(
            run_id,
            tip,
            artifacts,
            ControllerActionType.WRITE_ARTIFACT,
            {},
            timestamp="2026-01-01T00:00:01Z",
            root=tmp_path,
            kernel_binary=KERNEL_BINARY,
        )

    assert ledger.list_commits_read_only() == before
    assert intent_path.exists() or intent_path.is_symlink()


def test_rust_kernel_rejects_open_or_noncanonical_intent_without_mutation(tmp_path: Path) -> None:
    run_id, ledger, tip = _fixture(tmp_path)
    artifacts = [
        KernelCommitBundleArtifact(
            artifact_id="candidate-1", artifact_type="candidate", json_value={}
        )
    ]
    intent, _, _ = _expected_intent(run_id, tip, artifacts)
    value = json.loads(intent)
    value["unexpected"] = True
    intent_path = tmp_path / "runs" / run_id / ".factori-commit-bundle.intent.json"
    intent_path.write_bytes((canonical_json(value) + "\n").encode())
    before = intent_path.read_bytes()

    response = _run_kernel(tmp_path, _kernel_request(run_id, tip, artifacts))

    assert response.status is KernelResponseStatus.REJECTED
    assert response.diagnostics[0].code == "persistence_bundle_recovery_conflict"
    assert response.mutation_performed is False
    assert intent_path.read_bytes() == before
    assert len(ledger.list_commits_read_only()) == 1


def test_concurrent_bundle_callers_create_one_linear_commit(tmp_path: Path) -> None:
    run_id, ledger, tip = _fixture(tmp_path)
    artifacts = [
        KernelCommitBundleArtifact(
            artifact_id="candidate-1", artifact_type="candidate", json_value={"x": 1}
        )
    ]
    request = _kernel_request(run_id, tip, artifacts)
    raw = json.dumps(request.model_dump(mode="json")) + "\n"
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
    responses = []
    for process in processes:
        stdout, _ = process.communicate(raw, timeout=30)
        assert process.returncode == 0
        responses.append(KernelResponseEnvelope.model_validate_json(stdout))

    assert sum(response.status is KernelResponseStatus.ACCEPTED for response in responses) == 1
    assert all(
        response.status is KernelResponseStatus.ACCEPTED
        or response.diagnostics[0].code
        in {"persistence_bundle_busy", "persistence_bundle_tip_mismatch"}
        for response in responses
    )
    commits = ledger.list_commits_read_only()
    assert len(commits) == 2
    assert commits[1].parent_hash == commits[0].commit_hash


def test_bundle_payload_counts_sidecars_in_aggregate_limit() -> None:
    limit = 12 * 1024 * 1024
    item = KernelCommitBundleArtifact(
        artifact_id="candidate-1",
        artifact_type="candidate",
        json_value="x" * (limit - 3),
    )
    with pytest.raises(ValidationError, match="artifact and sidecar"):
        KernelPersistenceCommitBundlePayload(
            run_id="run-bundle",
            expected_tip_hash="0" * 64,
            artifacts=[item],
            action_type=ControllerActionType.WRITE_ARTIFACT,
            commit_payload={},
            timestamp="2026-01-01T00:00:01Z",
        )


@pytest.mark.parametrize("mode", list(KernelMode))
def test_commit_bundle_preserves_every_artifact_type_and_order(
    tmp_path: Path,
    mode: KernelMode,
) -> None:
    run_id, _, tip = _fixture(tmp_path)
    artifacts = [
        KernelCommitBundleArtifact(
            artifact_id=f"artifact-{index}",
            artifact_type=artifact_type,
            json_value={"index": index},
        )
        for index, artifact_type in enumerate(ArtifactType)
    ]
    response = commit_artifact_bundle(
        run_id,
        tip,
        artifacts,
        ControllerActionType.WRITE_ARTIFACT,
        {},
        candidate_id_optional="candidate-1",
        timestamp="2026-01-01T00:00:01Z",
        mode=mode,
        root=tmp_path,
        kernel_binary=KERNEL_BINARY,
    )
    assert response.status is KernelResponseStatus.ACCEPTED
    assert response.result.commit.candidate_id == "candidate-1"  # type: ignore[union-attr]
    assert [item.id for item in response.result.artifacts] == [  # type: ignore[union-attr]
        item.artifact_id for item in artifacts
    ]
