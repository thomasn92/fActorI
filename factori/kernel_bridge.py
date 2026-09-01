"""Read-only shadow bridge from persisted Python ledgers to the Rust kernel."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from factori.artifacts import ARTIFACT_DIRECTORY_BY_TYPE
from factori.hashing import canonical_json, sha256_bytes, sha256_file, sha256_text
from factori.ledger import LedgerError, ResearchLedger
from factori.protocols import PROTOCOL_VERSION
from factori.rerun_policy import RerunPolicy, validate_ledger_tip
from factori.schemas import (
    ArtifactManifest,
    ArtifactRef,
    ArtifactType,
    ClaimTable,
    ControllerActionType,
    KernelArtifactPersistPayload,
    KernelArtifactPersistRequest,
    KernelArtifactPersistResult,
    KernelArtifactVerifyRequest,
    KernelArtifactVerifyResult,
    KernelAutonomousPaperCheckpoint,
    KernelAutonomousPaperCheckpointIndex,
    KernelCheckpointIndexLocator,
    KernelCheckpointVerifyPayload,
    KernelCheckpointVerifyRequest,
    KernelCheckpointVerifyResult,
    KernelClaimResolvePayload,
    KernelClaimResolveRequest,
    KernelClaimResolveResult,
    KernelEvidenceBundle,
    KernelEvidenceClassifyRequest,
    KernelEvidenceClassifyResult,
    KernelEvidenceValidateBundleRequest,
    KernelEvidenceValidateBundleResult,
    KernelLedgerVerifyRequest,
    KernelLedgerVerifyResult,
    KernelMode,
    KernelReplayVerifyCorePayload,
    KernelReplayVerifyCoreRequest,
    KernelReplayVerifyCoreResult,
    KernelResponseEnvelope,
    KernelResponseStatus,
)


class KernelBridgeError(RuntimeError):
    """Raised when the shadow bridge cannot obtain a valid kernel response."""


_SAFE_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_SAFE_ARTIFACT_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def verify_persisted_ledger(
    run_id: str,
    *,
    root: str | Path = ".",
    kernel_binary: str | Path | None = None,
    timeout_seconds: float = 30.0,
) -> KernelResponseEnvelope:
    """Verify one persisted SQLite ledger through the standalone Rust kernel.

    The bridge is deliberately shadow-only: Python reads the existing ledger and serializes a
    request snapshot; the Rust response is returned for parity checks and does not mutate state or
    grant scientific authority.
    """
    root_path = Path(root)
    runs_root = (root_path / "runs").resolve()
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise KernelBridgeError(f"unsafe run id: {run_id!r}")
    run_path = (runs_root / run_id).resolve()
    if run_path.parent != runs_root:
        raise KernelBridgeError(f"unsafe run path: {run_id!r}")
    ledger_path = run_path / "ledger.sqlite"
    if ledger_path.is_symlink():
        raise KernelBridgeError(f"unsafe ledger path for run: {run_id!r}")
    try:
        ledger = ResearchLedger.open_existing(ledger_path)
        commits = [
            commit.model_dump(mode="json") for commit in ledger.list_commits_read_only(run_id)
        ]
    except (LedgerError, OSError, sqlite3.Error, ValueError) as exc:
        raise KernelBridgeError(f"could not read persisted ledger: {exc}") from exc

    binary = (
        Path(kernel_binary)
        if kernel_binary is not None
        else root_path / "rust-kernel" / "target" / "debug" / "factori-kernel"
    )
    request = KernelLedgerVerifyRequest(
        protocol_version=PROTOCOL_VERSION,
        request_id=f"ledger-verify-{run_id}",
        operation="ledger.verify",
        mode="DevelopmentCompatibility",
        payload={"run_id": run_id, "commits": commits},
    )
    try:
        completed = subprocess.run(
            [str(binary)],
            input=json.dumps(request.model_dump(mode="json")) + "\n",
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KernelBridgeError(f"kernel transport failed: {exc}") from exc
    if completed.returncode != 0:
        raise KernelBridgeError(
            f"kernel exited with status {completed.returncode}: {completed.stderr.strip()}"
        )
    try:
        response = KernelResponseEnvelope.model_validate_json(completed.stdout)
    except ValueError as exc:
        raise KernelBridgeError(f"kernel returned an invalid response: {exc}") from exc
    request_envelope = request
    if response.protocol_version != request_envelope.protocol_version:
        raise KernelBridgeError("kernel response protocol version does not match request")
    if response.request_id != request_envelope.request_id:
        raise KernelBridgeError("kernel response request id does not match request")
    if response.operation != request_envelope.operation:
        raise KernelBridgeError("kernel response operation does not match request")
    if response.mode != request_envelope.mode:
        raise KernelBridgeError("kernel response mode does not match request")

    python_report = validate_ledger_tip(
        run_id,
        root=root_path,
        policy=RerunPolicy.ALLOW_IF_FORCED,
    )
    python_valid = not python_report.blocking_findings
    rust_valid = response.status == KernelResponseStatus.ACCEPTED
    if python_valid != rust_valid:
        raise KernelBridgeError("Rust and Python ledger validators disagree on validity")
    if rust_valid:
        result = response.result
        if not isinstance(result, KernelLedgerVerifyResult):
            raise KernelBridgeError("ledger verification response has the wrong result type")
        if result.commit_count != len(commits):
            raise KernelBridgeError("Rust and Python ledger validators disagree on commit count")
        expected_root = commits[0]["commit_hash"] if commits else None
        expected_tip = commits[-1]["commit_hash"] if commits else None
        if result.root_hash != expected_root or result.tip_hash != expected_tip:
            raise KernelBridgeError("Rust and Python ledger validators disagree on ledger tip")
    return response


def verify_persisted_artifact(
    run_id: str,
    artifact: ArtifactRef,
    *,
    root: str | Path = ".",
    kernel_binary: str | Path | None = None,
    timeout_seconds: float = 30.0,
) -> KernelResponseEnvelope:
    """Verify one persisted artifact through the read-only Rust kernel shadow.

    The bridge validates the requested identity before asking Rust to read the artifact and
    ledger beneath the configured project root. No file, ledger row, or sidecar is modified.
    """
    root_path = Path(root)
    runs_root = (root_path / "runs").resolve()
    _validate_artifact_request_path(run_id, artifact, runs_root=runs_root)
    artifact_path = root_path / artifact.path
    if artifact_path.is_symlink() or not artifact_path.is_file():
        raise KernelBridgeError(f"artifact path is not a regular file: {artifact.path}")
    binary = (
        Path(kernel_binary)
        if kernel_binary is not None
        else root_path / "rust-kernel" / "target" / "debug" / "factori-kernel"
    )
    request = KernelArtifactVerifyRequest(
        protocol_version=PROTOCOL_VERSION,
        request_id=f"artifact-verify-{run_id}-{artifact.id}",
        operation="artifact.verify",
        mode="DevelopmentCompatibility",
        payload={
            "run_id": run_id,
            "artifact": artifact,
        },
    )
    response = _invoke_kernel(
        request,
        binary=binary,
        root=root_path.resolve(),
        timeout_seconds=timeout_seconds,
    )
    if response.status == KernelResponseStatus.ACCEPTED:
        result = response.result
        if not isinstance(result, KernelArtifactVerifyResult):
            raise KernelBridgeError("artifact verification response has the wrong result type")
        if (
            result.run_id != run_id
            or result.artifact_id != artifact.id
            or result.content_hash != artifact.content_hash
            or result.producing_commit_hash != artifact.producing_commit_hash
        ):
            raise KernelBridgeError("Rust artifact verification response does not match request")
    return response


def persist_json_artifact(
    run_id: str,
    artifact_id: str,
    artifact_type: ArtifactType,
    json_value: Any,
    *,
    metadata: dict[str, Any] | None = None,
    filename_stem_optional: str | None = None,
    root: str | Path = ".",
    kernel_binary: str | Path | None = None,
    timeout_seconds: float = 30.0,
) -> KernelResponseEnvelope:
    """Persist one canonical JSON artifact through the Rust kernel only.

    The bridge computes expected bytes and identity up front, then verifies every returned field
    and proves that pre-existing run files and the ledger were not changed.
    """
    root_path = Path(root).resolve()
    payload = KernelArtifactPersistPayload(
        run_id=run_id,
        artifact_id=artifact_id,
        artifact_type=artifact_type,
        json_value=json_value,
        metadata=metadata or {},
        filename_stem_optional=filename_stem_optional,
        overwrite_policy="FailIfExists",
    )
    canonical = canonical_json(json_value)
    expected_bytes = (canonical + "\n").encode("utf-8")
    if len(expected_bytes) > 12 * 1024 * 1024:
        raise KernelBridgeError("serialized JSON payload exceeds 12 MiB")
    type_directory = ARTIFACT_DIRECTORY_BY_TYPE[artifact_type]
    stem = filename_stem_optional or artifact_id
    destination = root_path / "runs" / run_id / type_directory / f"{stem}.json"
    sidecar = destination.with_name(destination.name + ".meta.json")
    before = _snapshot_run_state(root_path, run_id)
    binary = (
        Path(kernel_binary)
        if kernel_binary is not None
        else root_path / "rust-kernel" / "target" / "debug" / "factori-kernel"
    )
    request = KernelArtifactPersistRequest(
        protocol_version=PROTOCOL_VERSION,
        request_id=f"artifact-persist-{run_id}-{artifact_id}",
        operation="artifact.persist",
        mode="DevelopmentCompatibility",
        payload=payload,
    )
    response = _invoke_kernel(
        request, binary=binary, root=root_path, timeout_seconds=timeout_seconds
    )
    if response.status == KernelResponseStatus.ACCEPTED:
        result = response.result
        if not isinstance(result, KernelArtifactPersistResult):
            raise KernelBridgeError("artifact persistence response has the wrong result type")
        normalized_metadata = {
            **payload.metadata,
            "format": "json",
            "is_verification_evidence": False,
        }
        expected_hash = sha256_bytes(expected_bytes)
        artifact = result.artifact
        if (
            artifact.id != artifact_id
            or artifact.type != artifact_type
            or artifact.path != f"runs/{run_id}/{type_directory}/{stem}.json"
            or artifact.content_hash != expected_hash
            or artifact.producing_commit_hash is not None
            or artifact.metadata != normalized_metadata
            or result.bytes_written != len(expected_bytes)
            or result.created is not True
            or result.linked_to_ledger is not False
            or result.authority_granted is not False
            or not response.mutation_performed
        ):
            raise KernelBridgeError("Rust artifact persistence response does not match request")
        if (
            not destination.is_file()
            or destination.is_symlink()
            or destination.read_bytes() != expected_bytes
        ):
            raise KernelBridgeError("Rust artifact persistence bytes do not match request")
        if sidecar.exists() or sidecar.is_symlink():
            raise KernelBridgeError("artifact persistence unexpectedly created a sidecar")
        after = _snapshot_run_state(root_path, run_id)
        if any(after.get(path) != digest for path, digest in before.items()):
            raise KernelBridgeError("artifact persistence changed pre-existing run state")
        destination_key = destination.relative_to(root_path)
        extras = set(after) - set(before) - {destination_key}
        cleanup_warning = any(
            diagnostic.code == "artifact_persist_temp_cleanup_warning"
            for diagnostic in response.diagnostics
        )
        if extras and not cleanup_warning:
            raise KernelBridgeError("artifact persistence created unexpected files")
        if extras and any(not path.name.startswith(f".{stem}.tmp-") for path in extras):
            raise KernelBridgeError("artifact persistence left an unexpected temporary file")
    else:
        if response.mutation_performed:
            raise KernelBridgeError("artifact persistence failed after mutation; inspect run state")
        after = _snapshot_run_state(root_path, run_id)
        if after != before:
            raise KernelBridgeError("rejected artifact persistence changed run state")
        destination_key = destination.relative_to(root_path)
        if destination_key not in before and (destination.exists() or destination.is_symlink()):
            raise KernelBridgeError("rejected artifact persistence left a destination")
    return response


persist_artifact = persist_json_artifact


def _snapshot_run_state(root: Path, run_id: str) -> dict[Path, str]:
    run_path = root / "runs" / run_id
    if not run_path.is_dir() or run_path.is_symlink():
        raise KernelBridgeError("run directory is not initialized")
    ledger = run_path / "ledger.sqlite"
    paths = list(run_path.rglob("*"))
    if ledger.is_file() and ledger not in paths:
        paths.append(ledger)
    snapshot: dict[Path, str] = {}
    for path in paths:
        relative = path.relative_to(root)
        if path.is_symlink():
            snapshot[relative] = f"symlink:{path.readlink()}"
        elif path.is_dir():
            snapshot[relative] = "directory"
        else:
            snapshot[relative] = sha256_file(path)
    return snapshot


def classify_persisted_artifact(
    run_id: str,
    artifact: ArtifactRef,
    *,
    mode: KernelMode = KernelMode.DEVELOPMENT_COMPATIBILITY,
    root: str | Path = ".",
    kernel_binary: str | Path | None = None,
    timeout_seconds: float = 30.0,
) -> KernelResponseEnvelope:
    """Classify one persisted artifact through the non-authoritative Rust shadow operation.

    Classification is intentionally weaker than evidence validation: an accepted response only
    identifies a context, presentation artifact, or candidate for later bundle validation.
    """
    root_path = Path(root)
    runs_root = (root_path / "runs").resolve()
    _validate_artifact_request_path(run_id, artifact, runs_root=runs_root)
    binary = (
        Path(kernel_binary)
        if kernel_binary is not None
        else root_path / "rust-kernel" / "target" / "debug" / "factori-kernel"
    )
    request = KernelEvidenceClassifyRequest(
        protocol_version=PROTOCOL_VERSION,
        request_id=f"evidence-classify-{run_id}-{artifact.id}-{mode.value}",
        operation="evidence.classify",
        mode=mode,
        payload={"run_id": run_id, "artifact": artifact},
    )
    response = _invoke_kernel(
        request,
        binary=binary,
        root=root_path.resolve(),
        timeout_seconds=timeout_seconds,
    )
    if response.status == KernelResponseStatus.ACCEPTED:
        result = response.result
        if not isinstance(result, KernelEvidenceClassifyResult):
            raise KernelBridgeError("evidence classification response has the wrong result type")
        if (
            result.run_id != run_id
            or result.artifact_id != artifact.id
            or result.authority_granted is not False
        ):
            raise KernelBridgeError("Rust evidence classification response does not match request")
    return response


def validate_persisted_evidence_bundle(
    run_id: str,
    *,
    candidate_id: str,
    claim_id: str,
    producing_commit_hash: str,
    bundle: KernelEvidenceBundle,
    mode: KernelMode = KernelMode.STRICT_PRODUCTION,
    root: str | Path = ".",
    kernel_binary: str | Path | None = None,
    timeout_seconds: float = 30.0,
) -> KernelResponseEnvelope:
    """Validate one persisted Stage C bundle through the non-authoritative Rust shadow.

    The bridge sends only locator fields. Rust resolves artifact references and payload bytes from
    the read-only ledger; the returned result is an integrity decision, never a reusable authority
    token or a claim-label upgrade.
    """
    root_path = Path(root)
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise KernelBridgeError("unsafe run id")
    if not _SAFE_ARTIFACT_SEGMENT.fullmatch(candidate_id) or not _SAFE_ARTIFACT_SEGMENT.fullmatch(
        claim_id
    ):
        raise KernelBridgeError("unsafe candidate or claim id")
    binary = (
        Path(kernel_binary)
        if kernel_binary is not None
        else root_path / "rust-kernel" / "target" / "debug" / "factori-kernel"
    )
    request = KernelEvidenceValidateBundleRequest(
        protocol_version=PROTOCOL_VERSION,
        request_id=f"evidence-validate-bundle-{run_id}-{candidate_id}",
        operation="evidence.validate_bundle",
        mode=mode,
        payload={
            "run_id": run_id,
            "candidate_id": candidate_id,
            "claim_id": claim_id,
            "producing_commit_hash": producing_commit_hash,
            "bundle": bundle,
        },
    )
    response = _invoke_kernel(
        request,
        binary=binary,
        root=root_path.resolve(),
        timeout_seconds=timeout_seconds,
    )
    if response.status == KernelResponseStatus.ACCEPTED:
        result = response.result
        if not isinstance(result, KernelEvidenceValidateBundleResult):
            raise KernelBridgeError("bundle validation response has the wrong result type")
        bundle_payload = bundle.model_dump(mode="json")
        expected_kind = bundle_payload["kind"]
        expected_fields = (
            (
                "contract_artifact_id",
                "payload_artifact_id",
                "trace_artifact_id",
                "result_artifact_id",
                "safety_artifact_id",
            )
            if expected_kind == "LeanProof"
            else (
                "contract_artifact_id",
                "input_artifact_id",
                "trace_artifact_id",
                "output_artifact_id",
                "result_artifact_id",
                "safety_artifact_id",
            )
        )
        expected_artifact_ids = [bundle_payload[field] for field in expected_fields]
        if (
            result.run_id != run_id
            or result.candidate_id != candidate_id
            or result.claim_id != claim_id
            or result.bundle_kind != expected_kind
            or result.producing_commit_hash != producing_commit_hash
            or result.validated_artifact_ids != expected_artifact_ids
            or result.authority_granted is not False
            or result.bundle_valid is not True
        ):
            raise KernelBridgeError("Rust bundle validation response does not match request")
    return response


def resolve_persisted_claim(
    run_id: str,
    *,
    claim_id: str,
    claim_table_artifact_id: str,
    claim_table_producing_commit_hash: str,
    evidence: dict[str, object] | None = None,
    mode: KernelMode = KernelMode.STRICT_PRODUCTION,
    root: str | Path = ".",
    kernel_binary: str | Path | None = None,
    timeout_seconds: float = 30.0,
) -> KernelResponseEnvelope:
    """Resolve one claim through the read-only Rust shadow operation.

    Any supplied evidence locator is revalidated by Rust in this same request. The returned
    admissibility decision remains manuscript context and never grants evidence authority.
    """
    root_path = Path(root)
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise KernelBridgeError("unsafe run id")
    if not _SAFE_ARTIFACT_SEGMENT.fullmatch(claim_id) or not _SAFE_ARTIFACT_SEGMENT.fullmatch(
        claim_table_artifact_id
    ):
        raise KernelBridgeError("unsafe claim or claim-table artifact id")
    payload = KernelClaimResolvePayload(
        run_id=run_id,
        claim_id=claim_id,
        claim_table={
            "artifact_id": claim_table_artifact_id,
            "producing_commit_hash": claim_table_producing_commit_hash,
        },
        evidence=evidence,
    )
    binary = (
        Path(kernel_binary)
        if kernel_binary is not None
        else root_path / "rust-kernel" / "target" / "debug" / "factori-kernel"
    )
    request = KernelClaimResolveRequest(
        protocol_version=PROTOCOL_VERSION,
        request_id=(f"claim-resolve-{run_id}-{claim_id}-{claim_table_producing_commit_hash[:12]}"),
        operation="claim.resolve",
        mode=mode,
        payload=payload,
    )
    response = _invoke_kernel(
        request,
        binary=binary,
        root=root_path.resolve(),
        timeout_seconds=timeout_seconds,
    )
    if response.status == KernelResponseStatus.ACCEPTED:
        result = response.result
        if not isinstance(result, KernelClaimResolveResult):
            raise KernelBridgeError("claim resolution response has the wrong result type")
        if (
            result.run_id != run_id
            or result.claim_id != claim_id
            or result.claim_record_validated is not True
            or result.authority_granted is not False
        ):
            raise KernelBridgeError("Rust claim resolution response does not match request")
    return response


def verify_persisted_autonomous_checkpoints(
    run_id: str,
    *,
    index_artifact_id: str,
    index_producing_commit_hash: str,
    mode: KernelMode = KernelMode.STRICT_PRODUCTION,
    root: str | Path = ".",
    kernel_binary: str | Path | None = None,
    timeout_seconds: float = 30.0,
) -> KernelResponseEnvelope:
    """Verify the latest autonomous-paper checkpoint chain through the Rust shadow kernel."""
    root_path = Path(root)
    if not _SAFE_RUN_ID.fullmatch(run_id) or not _SAFE_ARTIFACT_SEGMENT.fullmatch(
        index_artifact_id
    ):
        raise KernelBridgeError("unsafe checkpoint or run id")
    payload = KernelCheckpointVerifyPayload(
        run_id=run_id,
        index=KernelCheckpointIndexLocator(
            artifact_id=index_artifact_id,
            producing_commit_hash=index_producing_commit_hash,
        ),
    )
    binary = (
        Path(kernel_binary)
        if kernel_binary is not None
        else root_path / "rust-kernel" / "target" / "debug" / "factori-kernel"
    )
    request = KernelCheckpointVerifyRequest(
        protocol_version=PROTOCOL_VERSION,
        request_id=(
            f"checkpoint-verify-{run_id}-{index_artifact_id}-{index_producing_commit_hash[:12]}"
        ),
        operation="checkpoint.verify",
        mode=mode,
        payload=payload,
    )
    response = _invoke_kernel(
        request,
        binary=binary,
        root=root_path.resolve(),
        timeout_seconds=timeout_seconds,
    )
    if response.status == KernelResponseStatus.ACCEPTED:
        result = response.result
        if not isinstance(result, KernelCheckpointVerifyResult):
            raise KernelBridgeError("checkpoint verification response has the wrong result type")
        index, expected_hashes = _load_checkpoint_shadow_expectations(
            root_path,
            run_id,
            index_artifact_id,
        )
        if (
            result.run_id != run_id
            or result.checkpoint_index_artifact_id != index_artifact_id
            or result.checkpoint_index_producing_commit_hash != index_producing_commit_hash
            or result.checkpoint_count != index.checkpoint_count
            or result.validated_checkpoint_hashes != expected_hashes
            or result.latest_completed_stage != index.latest_completed_stage
            or result.checkpoint_chain_valid is not True
            or result.authority_granted is not False
        ):
            raise KernelBridgeError("Rust checkpoint verification response does not match request")
    return response


def verify_persisted_replay_core(
    run_id: str,
    ledger_tip_hash: str,
    *,
    mode: KernelMode = KernelMode.DEVELOPMENT_COMPATIBILITY,
    root: str | Path = ".",
    kernel_binary: str | Path | None = None,
    timeout_seconds: float = 30.0,
) -> KernelResponseEnvelope:
    """Run the bounded, read-only Rust replay-core verifier for one current run tip."""
    root_path = Path(root)
    if not _SAFE_RUN_ID.fullmatch(run_id):
        raise KernelBridgeError("unsafe replay run id")
    binary = (
        Path(kernel_binary)
        if kernel_binary is not None
        else root_path / "rust-kernel" / "target" / "debug" / "factori-kernel"
    )
    payload = KernelReplayVerifyCorePayload(run_id=run_id, ledger_tip_hash=ledger_tip_hash)
    request = KernelReplayVerifyCoreRequest(
        protocol_version=PROTOCOL_VERSION,
        request_id=f"replay-verify-core-{run_id}-{ledger_tip_hash[:12]}",
        operation="replay.verify_core",
        mode=mode,
        payload=payload,
    )
    response = _invoke_kernel(
        request,
        binary=binary,
        root=root_path.resolve(),
        timeout_seconds=timeout_seconds,
    )
    if response.status == KernelResponseStatus.ACCEPTED:
        result = response.result
        if not isinstance(result, KernelReplayVerifyCoreResult):
            raise KernelBridgeError("replay-core response has the wrong result type")
        expected = _load_replay_core_shadow_expectations(root_path, run_id, ledger_tip_hash)
        observed = {
            "run_id": result.run_id,
            "ledger_tip_hash": result.ledger_tip_hash,
            "ledger_commit_count": result.ledger_commit_count,
            "ledger_artifact_count": result.ledger_artifact_count,
            "ledger_artifact_inventory_hash": result.ledger_artifact_inventory_hash,
            "required_outputs_checked": result.required_outputs_checked,
            "manifest_artifact_id": result.manifest_artifact_id,
            "manifest_producing_commit_hash": result.manifest_producing_commit_hash,
            "manifest_entry_count": result.manifest_entry_count,
            "manifest_inventory_hash": result.manifest_inventory_hash,
            "claims_checked": result.claims_checked,
            "claim_evidence_links_checked": result.claim_evidence_links_checked,
        }
        if observed != expected or (
            result.core_replay_valid is not True
            or result.ledger_snapshot_stable is not True
            or result.authority_boundary_valid is not True
            or result.authority_granted is not False
        ):
            raise KernelBridgeError("Rust replay-core response does not match request")
    return response


def _load_replay_core_shadow_expectations(
    root: Path,
    run_id: str,
    ledger_tip_hash: str,
) -> dict[str, Any]:
    ledger_path = root / "runs" / run_id / "ledger.sqlite"
    if ledger_path.is_symlink() or not ledger_path.is_file():
        raise KernelBridgeError("replay ledger is not a regular file")
    try:
        ledger = ResearchLedger.open_existing(ledger_path)
        commits = ledger.list_commits_read_only(run_id)
    except (LedgerError, OSError, sqlite3.Error, ValueError) as exc:
        raise KernelBridgeError(f"could not read replay ledger: {exc}") from exc
    if not commits or commits[-1].commit_hash != ledger_tip_hash:
        raise KernelBridgeError("replay ledger tip changed before bridge comparison")

    inventory = [
        {
            "commit_hash": commit.commit_hash,
            "artifact": artifact.model_dump(mode="json"),
        }
        for commit in commits
        for artifact in commit.artifact_refs
    ]
    manifest_commit = next(
        (
            commit
            for commit in reversed(commits)
            if commit.action_type == ControllerActionType.ARTIFACT_MANIFEST_WRITTEN
            and "artifacts" in commit.payload
        ),
        None,
    )
    claim_commit = next(
        (
            commit
            for commit in reversed(commits)
            if commit.action_type == ControllerActionType.CLAIM_TABLE_BUILT
            and "claims" in commit.payload
        ),
        None,
    )
    if manifest_commit is None or claim_commit is None:
        raise KernelBridgeError("replay comparison prerequisites are missing")
    manifest_refs = [
        artifact
        for artifact in manifest_commit.artifact_refs
        if artifact.id == "artifact-manifest" and artifact.path.endswith(".json")
    ]
    claim_refs = [
        artifact for artifact in claim_commit.artifact_refs if artifact.path.endswith(".json")
    ]
    if len(manifest_refs) != 1 or len(claim_refs) != 1:
        raise KernelBridgeError("replay comparison artifacts are ambiguous")
    if manifest_refs[0].producing_commit_hash != manifest_commit.commit_hash:
        raise KernelBridgeError("replay manifest producer does not match its commit")
    try:
        manifest_raw = json.loads((root / manifest_refs[0].path).read_text(encoding="utf-8"))
        claim_raw = json.loads((root / claim_refs[0].path).read_text(encoding="utf-8"))
        manifest = ArtifactManifest.model_validate(manifest_raw)
        claim_table = ClaimTable.model_validate(claim_raw)
    except (OSError, ValueError) as exc:
        raise KernelBridgeError(f"replay comparison artifact is invalid: {exc}") from exc
    return {
        "run_id": run_id,
        "ledger_tip_hash": ledger_tip_hash,
        "ledger_commit_count": len(commits),
        "ledger_artifact_count": len(inventory),
        "ledger_artifact_inventory_hash": sha256_text(canonical_json(inventory)),
        "required_outputs_checked": 11,
        "manifest_artifact_id": manifest_refs[0].id,
        "manifest_producing_commit_hash": manifest_refs[0].producing_commit_hash,
        "manifest_entry_count": len(manifest.artifacts),
        "manifest_inventory_hash": sha256_text(canonical_json(manifest_raw["artifacts"])),
        "claims_checked": len(claim_table.claims),
        "claim_evidence_links_checked": len(claim_table.evidence_links),
    }


def _load_checkpoint_shadow_expectations(
    root: Path,
    run_id: str,
    index_artifact_id: str,
) -> tuple[KernelAutonomousPaperCheckpointIndex, list[str]]:
    index_relative = f"runs/{run_id}/reports/{index_artifact_id}.json"
    index_path = _resolve_checkpoint_shadow_path(root, run_id, index_relative)
    try:
        index = KernelAutonomousPaperCheckpointIndex.model_validate_json(
            index_path.read_text(encoding="utf-8")
        )
    except (OSError, ValueError) as exc:
        raise KernelBridgeError(f"checkpoint index is unreadable: {exc}") from exc
    if index.run_id != run_id:
        raise KernelBridgeError("checkpoint index run id does not match request")
    hashes: list[str] = []
    for relative in index.checkpoints:
        checkpoint_path = _resolve_checkpoint_shadow_path(root, run_id, relative)
        try:
            checkpoint = KernelAutonomousPaperCheckpoint.model_validate_json(
                checkpoint_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            raise KernelBridgeError(f"checkpoint record is unreadable: {exc}") from exc
        hashes.append(checkpoint.checkpoint_hash)
    return index, hashes


def _resolve_checkpoint_shadow_path(root: Path, run_id: str, relative: str) -> Path:
    if relative.startswith("/") or "\\" in relative:
        raise KernelBridgeError(f"unsafe checkpoint path: {relative}")
    parts = relative.split("/")
    if (
        len(parts) != 4
        or parts[:3] != ["runs", run_id, "reports"]
        or any(part in {"", ".", ".."} for part in parts)
        or not _SAFE_ARTIFACT_SEGMENT.fullmatch(parts[3])
        or not parts[3].endswith(".json")
    ):
        raise KernelBridgeError(f"unsafe checkpoint path: {relative}")
    root_resolved = root.resolve()
    run_root = (root_resolved / "runs" / run_id).resolve()
    if run_root.parent != (root_resolved / "runs").resolve():
        raise KernelBridgeError(f"unsafe checkpoint run path: {run_id}")
    current = root_resolved
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise KernelBridgeError(f"unsafe symlink in checkpoint path: {relative}")
    if not current.is_file():
        raise KernelBridgeError(f"checkpoint path is not a regular file: {relative}")
    resolved = current.resolve()
    try:
        resolved.relative_to(run_root)
    except ValueError:
        raise KernelBridgeError(f"checkpoint path escapes run directory: {relative}") from None
    return resolved


def _validate_artifact_request_path(
    run_id: str,
    artifact: ArtifactRef,
    *,
    runs_root: Path,
) -> None:
    if not _SAFE_RUN_ID.fullmatch(run_id) or not _SAFE_ARTIFACT_SEGMENT.fullmatch(artifact.id):
        raise KernelBridgeError("unsafe artifact or run id")
    if artifact.path.startswith("/") or "\\" in artifact.path:
        raise KernelBridgeError(f"unsafe artifact path: {artifact.path}")
    parts = artifact.path.split("/")
    expected_directory = ARTIFACT_DIRECTORY_BY_TYPE[artifact.type]
    if (
        len(parts) != 4
        or parts[0] != "runs"
        or parts[1] != run_id
        or parts[2] != expected_directory
        or not _SAFE_ARTIFACT_SEGMENT.fullmatch(parts[3])
        or "." not in parts[3]
        or any(part in {"", ".", ".."} for part in parts)
    ):
        raise KernelBridgeError("artifact path is outside its declared run/type directory")
    run_path = (runs_root / run_id).resolve()
    if run_path.parent != runs_root:
        raise KernelBridgeError(f"unsafe run path: {run_id!r}")
    candidate = (runs_root.parent / artifact.path).resolve()
    try:
        candidate.relative_to(run_path)
    except ValueError:
        raise KernelBridgeError(f"artifact path escapes run directory: {artifact.path}") from None
    current = runs_root.parent
    for part in Path(artifact.path).parts:
        current = current / part
        if current.is_symlink():
            raise KernelBridgeError(f"unsafe symlink in artifact path: {artifact.path}")


def _invoke_kernel(
    request: (
        KernelArtifactVerifyRequest
        | KernelLedgerVerifyRequest
        | KernelEvidenceClassifyRequest
        | KernelEvidenceValidateBundleRequest
        | KernelClaimResolveRequest
        | KernelCheckpointVerifyRequest
        | KernelReplayVerifyCoreRequest
        | KernelArtifactPersistRequest
    ),
    *,
    binary: Path,
    root: Path,
    timeout_seconds: float,
) -> KernelResponseEnvelope:
    try:
        completed = subprocess.run(
            [str(binary), "--root", str(root)],
            input=json.dumps(request.model_dump(mode="json")) + "\n",
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise KernelBridgeError(f"kernel transport failed: {exc}") from exc
    if completed.returncode != 0:
        raise KernelBridgeError(
            f"kernel exited with status {completed.returncode}: {completed.stderr.strip()}"
        )
    try:
        response = KernelResponseEnvelope.model_validate_json(completed.stdout)
    except ValueError as exc:
        raise KernelBridgeError(f"kernel returned an invalid response: {exc}") from exc
    if response.protocol_version != request.protocol_version:
        raise KernelBridgeError("kernel response protocol version does not match request")
    if response.request_id != request.request_id:
        raise KernelBridgeError("kernel response request id does not match request")
    if response.operation != request.operation:
        raise KernelBridgeError("kernel response operation does not match request")
    if response.mode != request.mode:
        raise KernelBridgeError("kernel response mode does not match request")
    return response


__all__ = [
    "KernelBridgeError",
    "persist_json_artifact",
    "persist_artifact",
    "classify_persisted_artifact",
    "verify_persisted_autonomous_checkpoints",
    "verify_persisted_replay_core",
    "validate_persisted_evidence_bundle",
    "verify_persisted_artifact",
    "verify_persisted_ledger",
]
