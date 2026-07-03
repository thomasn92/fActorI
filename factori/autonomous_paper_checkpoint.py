"""Crash-safe checkpoints and resume reports for autonomous paper finalization."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.hashing import sha256_file, sha256_json
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.protocols import PROTOCOL_VERSION
from factori.rerun_policy import validate_ledger_tip
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    AutonomousPaperCheckpoint,
    AutonomousPaperCheckpointIndex,
    AutonomousPaperResumeReport,
    AutonomousPaperRunStage,
    ControllerActionType,
    LedgerTipStatus,
)
from factori.storage_protocols import Clock, SystemClock


class AutonomousPaperCheckpointError(RuntimeError):
    """Raised when checkpoint persistence or inspection cannot proceed safely."""


@dataclass(frozen=True)
class AutonomousPaperCheckpointWriteResult:
    checkpoint: AutonomousPaperCheckpoint
    index: AutonomousPaperCheckpointIndex
    persistence: PersistenceResult
    checkpoint_artifact: ArtifactRef
    index_artifact: ArtifactRef


@dataclass(frozen=True)
class AutonomousPaperCheckpointVerification:
    index: AutonomousPaperCheckpointIndex | None
    checkpoints: list[AutonomousPaperCheckpoint]
    checked_count: int
    verified_count: int
    failed_count: int
    blockers: list[str]

    @property
    def resume_allowed(self) -> bool:
        return self.index is not None and not self.blockers


@dataclass(frozen=True)
class AutonomousPaperResumeWriteResult:
    report: AutonomousPaperResumeReport
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    markdown_artifact: ArtifactRef


def write_autonomous_paper_checkpoint(
    *,
    run_id: str,
    controller_run_id: str,
    stage: AutonomousPaperRunStage,
    artifact_paths: list[str],
    safety_gate_status: str,
    release_status: str | None,
    input_hashes: dict[str, str],
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    clock: Clock | None = None,
) -> AutonomousPaperCheckpointWriteResult:
    """Persist one immutable checkpoint and a numbered immutable index snapshot."""
    root_path = Path(root)
    clock = clock or SystemClock()
    reports = root_path / "runs" / run_id / "reports"
    existing = _checkpoint_paths(reports)
    number = len(existing) + 1
    stage_slug = stage.stage_name.replace("_", "-")
    checkpoint_id = f"autonomous-paper-checkpoint-{number:04d}-{stage_slug}"
    checkpoint_path = f"runs/{run_id}/reports/{checkpoint_id}.json"
    output_hashes = _hash_artifact_paths(root_path, artifact_paths)
    checkpoint = AutonomousPaperCheckpoint(
        run_id=run_id,
        controller_run_id=controller_run_id,
        stage_name=stage.stage_name,
        stage_status=stage.stage_status,
        stage_artifact_paths=sorted(set(artifact_paths)),
        stage_started_at=stage.started_at,
        stage_completed_at=stage.completed_at,
        protocol_version=PROTOCOL_VERSION,
        ledger_tip_hash_optional=ledger.latest_commit_hash(run_id),
        checkpoint_hash="0" * 64,
        input_hashes=dict(sorted(input_hashes.items())),
        output_hashes=output_hashes,
        safety_gate_status=safety_gate_status,
        release_status_optional=release_status,
        publication_ready=False,
        verified_for_resume=safety_gate_status != "failed",
        verification_status=(
            "verified_with_warnings"
            if safety_gate_status == "passed_with_warnings"
            else "verified"
            if safety_gate_status == "passed"
            else "failed"
        ),
        verification_errors=[],
    )
    checkpoint = checkpoint.model_copy(
        update={"checkpoint_hash": _checkpoint_hash(checkpoint)}
    )
    previous_index = latest_autonomous_paper_checkpoint_index(root_path, run_id)
    previous_paths = list(previous_index.checkpoints) if previous_index else []
    index_id = f"autonomous-paper-checkpoint-index-{number:04d}"
    index = AutonomousPaperCheckpointIndex(
        run_id=run_id,
        latest_controller_run_id=controller_run_id,
        checkpoint_count=len(previous_paths) + 1,
        latest_completed_stage=stage.stage_name,
        checkpoints=[*previous_paths, checkpoint_path],
        resume_allowed=safety_gate_status != "failed",
        resume_blockers=([] if safety_gate_status != "failed" else [stage.stage_name]),
        publication_ready=False,
    )
    metadata = _metadata("autonomous_paper_checkpoint")
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(checkpoint_id, ArtifactType.REPORT, checkpoint, "json", metadata),
            ArtifactWriteSpec(index_id, ArtifactType.REPORT, index, "json", metadata),
        ],
        action_type=ControllerActionType.AUTONOMOUS_PAPER_CHECKPOINT_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "controller_run_id": controller_run_id,
            "stage_name": stage.stage_name,
            "checkpoint_hash": checkpoint.checkpoint_hash,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
        clock=clock,
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return AutonomousPaperCheckpointWriteResult(
        checkpoint=checkpoint,
        index=index,
        persistence=persistence,
        checkpoint_artifact=by_id[checkpoint_id],
        index_artifact=by_id[index_id],
    )


def verify_autonomous_paper_checkpoints(
    *,
    run_id: str,
    root: str | Path = ".",
) -> AutonomousPaperCheckpointVerification:
    """Verify checkpoint integrity, artifacts, protocol version, and ledger lineage read-only."""
    root_path = Path(root)
    index = latest_autonomous_paper_checkpoint_index(root_path, run_id)
    if index is None:
        return AutonomousPaperCheckpointVerification(
            index=None,
            checkpoints=[],
            checked_count=0,
            verified_count=0,
            failed_count=0,
            blockers=["Autonomous paper checkpoint index is missing or unreadable."],
        )
    blockers: list[str] = []
    checkpoints: list[AutonomousPaperCheckpoint] = []
    ledger_status = validate_ledger_tip(run_id, root=root_path)
    if ledger_status.status == LedgerTipStatus.INVALID:
        blockers.append("Ledger tip validation failed for checkpoint resume.")
    ledger_hashes: set[str] = set()
    if ledger_status.ledger_exists:
        try:
            ledger_hashes = {
                commit.commit_hash
                for commit in ResearchLedger(
                    root_path / "runs" / run_id / "ledger.sqlite"
                ).list_commits(run_id)
            }
        except Exception as exc:  # pragma: no cover - defensive corrupt-database path.
            blockers.append(f"Ledger could not be read for checkpoint verification: {exc}")
    if index.checkpoint_count != len(index.checkpoints):
        blockers.append("Checkpoint index count does not match its checkpoint inventory.")
    previous_hash: str | None = None
    for relative in index.checkpoints:
        path = _safe_run_file(root_path, run_id, relative)
        if path is None or not path.is_file():
            blockers.append(f"Checkpoint file is missing or unsafe: {relative}")
            continue
        try:
            checkpoint = AutonomousPaperCheckpoint.model_validate_json(
                path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError) as exc:
            blockers.append(f"Checkpoint file is unreadable: {relative}: {exc}")
            continue
        errors = _verify_checkpoint(
            checkpoint,
            run_id=run_id,
            root=root_path,
            ledger_hashes=ledger_hashes,
        )
        expected_previous = checkpoint.input_hashes.get("previous_checkpoint")
        if expected_previous != previous_hash:
            errors.append("checkpoint chain does not link to the preceding checkpoint")
        if errors:
            blockers.extend(f"{checkpoint.stage_name}: {error}" for error in errors)
            checkpoint = checkpoint.model_copy(
                update={
                    "verified_for_resume": False,
                    "verification_status": "failed",
                    "verification_errors": errors,
                }
            )
        checkpoints.append(checkpoint)
        previous_hash = checkpoint.checkpoint_hash
    failed = sum(not checkpoint.verified_for_resume for checkpoint in checkpoints)
    return AutonomousPaperCheckpointVerification(
        index=index.model_copy(
            update={"resume_allowed": not blockers, "resume_blockers": sorted(set(blockers))}
        ),
        checkpoints=checkpoints,
        checked_count=len(index.checkpoints),
        verified_count=len(checkpoints) - failed,
        failed_count=failed + max(len(index.checkpoints) - len(checkpoints), 0),
        blockers=sorted(set(blockers)),
    )


def inspect_autonomous_paper_checkpoints(
    *, run_id: str, root: str | Path = "."
) -> dict[str, Any]:
    verification = verify_autonomous_paper_checkpoints(run_id=run_id, root=root)
    if verification.index is None:
        raise AutonomousPaperCheckpointError(verification.blockers[0])
    return {
        **verification.index.model_dump(mode="json"),
        "autonomous_paper_checkpoint_present": True,
        "autonomous_paper_checkpoint_count": verification.index.checkpoint_count,
        "autonomous_paper_latest_completed_checkpoint": (
            verification.index.latest_completed_stage
        ),
        "autonomous_paper_resume_allowed": verification.resume_allowed,
        "autonomous_paper_resume_blocker_count": len(verification.blockers),
        "checkpoints_checked": verification.checked_count,
        "checkpoints_verified": verification.verified_count,
        "checkpoints_failed": verification.failed_count,
        "verified_checkpoints": [item.model_dump(mode="json") for item in verification.checkpoints],
    }


def latest_autonomous_paper_checkpoint_index(
    root: Path, run_id: str
) -> AutonomousPaperCheckpointIndex | None:
    reports = root / "runs" / run_id / "reports"
    paths = sorted(reports.glob("autonomous-paper-checkpoint-index-[0-9][0-9][0-9][0-9].json"))
    if not paths:
        return None
    try:
        return AutonomousPaperCheckpointIndex.model_validate_json(
            paths[-1].read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None


def write_autonomous_paper_resume_report(
    report: AutonomousPaperResumeReport,
    *,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    clock: Clock | None = None,
) -> AutonomousPaperResumeWriteResult:
    root_path = Path(root)
    reports = root_path / "runs" / report.run_id / "reports"
    number = len(_resume_report_paths(reports)) + 1
    report_id = f"autonomous-paper-resume-{number:04d}"
    metadata = _metadata("autonomous_paper_resume")
    persistence = persist_artifacts_with_commit(
        run_id=report.run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report_id}-markdown",
                ArtifactType.REPORT,
                render_autonomous_paper_resume_markdown(report),
                "markdown",
                metadata,
                filename_stem=report_id,
            ),
        ],
        action_type=ControllerActionType.AUTONOMOUS_PAPER_RESUME_WRITTEN,
        commit_payload={
            "run_id": report.run_id,
            "resume_id": report.resume_id,
            "resume_status": report.resume_status,
            "stages_reused": report.stages_reused,
            "stages_rerun": report.stages_rerun,
            "publication_ready": False,
        },
        clock=clock,
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return AutonomousPaperResumeWriteResult(
        report=report,
        persistence=persistence,
        report_artifact=by_id[report_id],
        markdown_artifact=by_id[f"{report_id}-markdown"],
    )


def latest_autonomous_paper_resume_report(
    root: str | Path, run_id: str
) -> AutonomousPaperResumeReport | None:
    paths = _resume_report_paths(Path(root) / "runs" / run_id / "reports")
    if not paths:
        return None
    try:
        return AutonomousPaperResumeReport.model_validate_json(
            paths[-1].read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None


def inspect_autonomous_paper_resume(
    *, run_id: str, root: str | Path = "."
) -> dict[str, Any]:
    report = latest_autonomous_paper_resume_report(root, run_id)
    if report is None:
        raise AutonomousPaperCheckpointError(
            f"No autonomous paper resume report found for run_id={run_id}."
        )
    return {
        **report.model_dump(mode="json"),
        "autonomous_paper_latest_resume_status": report.resume_status,
        "autonomous_paper_stages_reused_count": len(report.stages_reused),
        "autonomous_paper_stages_rerun_count": len(report.stages_rerun),
        "autonomous_paper_resume_blocker_count": len(report.resume_blockers),
    }


def autonomous_paper_checkpoint_summary_fields(
    *, run_id: str, root: str | Path = "."
) -> dict[str, Any]:
    verification = verify_autonomous_paper_checkpoints(run_id=run_id, root=root)
    resume = latest_autonomous_paper_resume_report(root, run_id)
    index = verification.index
    return {
        "autonomous_paper_checkpoint_present": index is not None,
        "autonomous_paper_checkpoint_count": index.checkpoint_count if index else 0,
        "autonomous_paper_latest_completed_checkpoint": (
            index.latest_completed_stage if index else None
        ),
        "autonomous_paper_resume_allowed": verification.resume_allowed,
        "autonomous_paper_latest_resume_status": resume.resume_status if resume else None,
        "autonomous_paper_resume_blocker_count": len(verification.blockers),
        "autonomous_paper_stages_reused_count": len(resume.stages_reused) if resume else 0,
        "autonomous_paper_stages_rerun_count": len(resume.stages_rerun) if resume else 0,
        "autonomous_paper_resume_blockers": list(verification.blockers),
    }


def render_autonomous_paper_resume_markdown(report: AutonomousPaperResumeReport) -> str:
    return "\n".join(
        [
            "# Autonomous Paper Resume Report",
            "",
            f"Run ID: `{report.run_id}`",
            f"Resume ID: `{report.resume_id}`",
            f"Status: `{report.resume_status}`",
            f"Requested / actual stage: `{report.requested_resume_stage}` / "
            f"`{report.actual_resume_stage}`",
            f"Checkpoints checked/verified/failed: `{report.checkpoints_checked}/"
            f"{report.checkpoints_verified}/{report.checkpoints_failed}`",
            f"Stages reused: `{', '.join(report.stages_reused) or 'none'}`",
            f"Stages rerun: `{', '.join(report.stages_rerun) or 'none'}`",
            f"Final bundle verification rerun: "
            f"`{str(report.final_bundle_verification_rerun).lower()}`",
            "",
            "Resume verification is reliability context only and creates no scientific authority.",
            "",
            "- publication_ready: false",
            "- creates_scientific_validation: false",
            "- implies_publication_readiness: false",
            "- is_verification_evidence: false",
            "",
        ]
    )


def _verify_checkpoint(
    checkpoint: AutonomousPaperCheckpoint,
    *,
    run_id: str,
    root: Path,
    ledger_hashes: set[str],
) -> list[str]:
    errors: list[str] = []
    if checkpoint.run_id != run_id:
        errors.append("run_id does not match")
    if checkpoint.protocol_version != PROTOCOL_VERSION:
        errors.append(
            f"protocol version {checkpoint.protocol_version} is stale; expected {PROTOCOL_VERSION}"
        )
    if checkpoint.publication_ready:
        errors.append("publication_ready=true is forbidden")
    if checkpoint.creates_scientific_validation or checkpoint.implies_publication_readiness:
        errors.append("checkpoint claims forbidden scientific or publication authority")
    if checkpoint.is_verification_evidence:
        errors.append("checkpoint cannot be verification evidence")
    if checkpoint.checkpoint_hash != _checkpoint_hash(checkpoint):
        errors.append("checkpoint hash does not match checkpoint content")
    if checkpoint.safety_gate_status == "failed":
        errors.append("stage safety gate did not pass")
    if not checkpoint.verified_for_resume or checkpoint.verification_status == "failed":
        errors.append("checkpoint was not marked verified for resume")
    if checkpoint.stage_status not in {"completed", "completed_with_warnings", "reused"}:
        errors.append(f"stage status is not reusable: {checkpoint.stage_status}")
    if (
        checkpoint.ledger_tip_hash_optional
        and checkpoint.ledger_tip_hash_optional not in ledger_hashes
    ):
        errors.append("checkpoint ledger tip is not present in the current ledger")
    for relative, expected in checkpoint.output_hashes.items():
        path = _safe_run_file(root, run_id, relative)
        if path is None or not path.is_file() or path.is_symlink():
            errors.append(f"checkpoint output is missing or unsafe: {relative}")
        elif sha256_file(path) != expected:
            errors.append(f"checkpoint output hash mismatch: {relative}")
    if set(checkpoint.stage_artifact_paths) != set(checkpoint.output_hashes):
        errors.append("stage artifact paths and output hash inventory differ")
    return sorted(set(errors))


def _checkpoint_hash(checkpoint: AutonomousPaperCheckpoint) -> str:
    return sha256_json(checkpoint.model_dump(mode="json", exclude={"checkpoint_hash"}))


def _hash_artifact_paths(root: Path, paths: list[str]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in sorted(set(paths)):
        path = (root / relative).resolve()
        try:
            path.relative_to(root.resolve())
        except ValueError as exc:
            raise AutonomousPaperCheckpointError(
                f"Checkpoint artifact escapes workspace: {relative}"
            ) from exc
        if not path.is_file() or path.is_symlink():
            raise AutonomousPaperCheckpointError(
                f"Checkpoint artifact is missing or unsafe: {relative}"
            )
        hashes[relative] = sha256_file(path)
    return hashes


def _safe_run_file(root: Path, run_id: str, relative: str) -> Path | None:
    path = (root / relative).resolve()
    run_path = (root / "runs" / run_id).resolve()
    try:
        path.relative_to(run_path)
    except ValueError:
        return None
    return path


def _checkpoint_paths(reports: Path) -> list[Path]:
    return sorted(
        path
        for path in reports.glob("autonomous-paper-checkpoint-*.json")
        if "-index-" not in path.name and not path.name.endswith(".meta.json")
    )


def _resume_report_paths(reports: Path) -> list[Path]:
    return sorted(
        path
        for path in reports.glob("autonomous-paper-resume-[0-9][0-9][0-9][0-9].json")
        if not path.name.endswith(".meta.json")
    )


def _metadata(role: str) -> dict[str, Any]:
    return {
        "stage": role,
        "artifact_role": "controller_reliability_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }


__all__ = [
    "AutonomousPaperCheckpointError",
    "AutonomousPaperCheckpointVerification",
    "AutonomousPaperCheckpointWriteResult",
    "AutonomousPaperResumeWriteResult",
    "autonomous_paper_checkpoint_summary_fields",
    "inspect_autonomous_paper_checkpoints",
    "inspect_autonomous_paper_resume",
    "latest_autonomous_paper_checkpoint_index",
    "latest_autonomous_paper_resume_report",
    "render_autonomous_paper_resume_markdown",
    "verify_autonomous_paper_checkpoints",
    "write_autonomous_paper_checkpoint",
    "write_autonomous_paper_resume_report",
]
