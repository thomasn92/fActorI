"""Read-only shadow bridge from persisted Python ledgers to the Rust kernel."""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from pathlib import Path

from factori.artifacts import ARTIFACT_DIRECTORY_BY_TYPE
from factori.ledger import LedgerError, ResearchLedger
from factori.protocols import PROTOCOL_VERSION
from factori.rerun_policy import RerunPolicy, validate_ledger_tip
from factori.schemas import (
    ArtifactRef,
    KernelArtifactVerifyRequest,
    KernelArtifactVerifyResult,
    KernelEvidenceBundle,
    KernelEvidenceClassifyRequest,
    KernelEvidenceClassifyResult,
    KernelEvidenceValidateBundleRequest,
    KernelEvidenceValidateBundleResult,
    KernelLedgerVerifyRequest,
    KernelLedgerVerifyResult,
    KernelMode,
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
    "classify_persisted_artifact",
    "validate_persisted_evidence_bundle",
    "verify_persisted_artifact",
    "verify_persisted_ledger",
]
