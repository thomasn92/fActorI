"""Deterministic artifact and reproducibility manifests."""

from __future__ import annotations

import platform

from factori.artifacts import ARTIFACT_DIRECTORY_BY_TYPE, ArtifactStore
from factori.config import LEDGER_FILENAME
from factori.hashing import sha256_file
from factori.schemas import (
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactRef,
    ArtifactType,
    LedgerSummary,
    ReproducibilityManifest,
)

EVIDENCE_ROLES = {
    "proof",
    "fake_proof",
    "fake_synthetic_experiment",
    "retrieval_evidence",
    "literature_evidence",
}


def build_artifact_manifest(run_id: str, artifact_store: ArtifactStore) -> ArtifactManifest:
    """Build a deterministic manifest of run artifacts."""
    run_path = artifact_store.run_path(run_id)
    entries: list[ArtifactManifestEntry] = []
    for path in sorted(run_path.rglob("*")):
        if not path.is_file():
            continue
        if path.name == LEDGER_FILENAME or path.suffix == ".sqlite":
            continue
        if ".meta.json" in path.name or path.as_posix().endswith(".meta.json"):
            continue
        relative_parts = path.relative_to(run_path).parts
        if "research_object" in relative_parts:
            continue
        if "replay" in relative_parts:
            continue
        if "diagnostics" in relative_parts:
            continue
        if "comparisons" in relative_parts:
            continue
        if "hygiene" in relative_parts:
            continue
        entries.append(_entry_for_path(artifact_store, run_id, path))

    entries = sorted(entries, key=lambda entry: (entry.path, entry.artifact_id))
    return ArtifactManifest(
        run_id=run_id,
        artifacts=entries,
        evidence_artifact_count=sum(1 for entry in entries if entry.is_evidence),
        presentation_artifact_count=sum(1 for entry in entries if entry.is_presentation),
    )


def build_reproducibility_manifest(
    run_id: str,
    artifact_manifest: ArtifactManifest,
    ledger_summary: LedgerSummary,
) -> ReproducibilityManifest:
    """Build deterministic reproducibility checks for a packaged run."""
    paths = {entry.path for entry in artifact_manifest.artifacts}
    ledger_exists = ledger_summary.commit_count > 0
    root_commit_exists = ledger_summary.root_commit_hash is not None
    latest_commit_exists = ledger_summary.latest_commit_hash is not None
    all_artifacts_have_hashes = all(
        bool(entry.content_hash) for entry in artifact_manifest.artifacts
    )
    all_evidence_have_commits = all(
        bool(entry.producing_commit_hash)
        for entry in artifact_manifest.artifacts
        if entry.is_evidence
    )
    claim_table_exists = any(path.endswith("reports/claim-table.json") for path in paths)
    draft_skeleton_exists = any(path.endswith("reports/draft-skeleton.json") for path in paths)
    manuscript_plan_exists = any(path.endswith("reports/manuscript-plan.json") for path in paths)
    final_nucleus_exists = any(path.endswith("reports/final-nucleus.json") for path in paths)
    blocked_claims_exists = any(path.endswith("reports/blocked-claims.json") for path in paths)
    environment_metadata_present = bool(platform.python_version())

    checks = {
        "ledger_exists": ledger_exists,
        "root_commit_exists": root_commit_exists,
        "latest_commit_exists": latest_commit_exists,
        "all_artifacts_have_hashes": all_artifacts_have_hashes,
        "all_evidence_artifacts_have_producing_commits": all_evidence_have_commits,
        "claim_table_exists": claim_table_exists,
        "draft_skeleton_exists": draft_skeleton_exists,
        "manuscript_plan_exists": manuscript_plan_exists,
        "final_nucleus_exists": final_nucleus_exists,
        "blocked_claims_list_exists": blocked_claims_exists,
        "environment_metadata_present": environment_metadata_present,
    }
    blocking_issues = [
        name for name, passed in checks.items() if not passed
    ]
    warnings = ["derived manifest; immutable ledger remains source of truth"]
    return ReproducibilityManifest(
        run_id=run_id,
        ledger_exists=ledger_exists,
        root_commit_exists=root_commit_exists,
        latest_commit_exists=latest_commit_exists,
        all_artifacts_have_hashes=all_artifacts_have_hashes,
        all_evidence_artifacts_have_producing_commits=all_evidence_have_commits,
        claim_table_exists=claim_table_exists,
        draft_skeleton_exists=draft_skeleton_exists,
        manuscript_plan_exists=manuscript_plan_exists,
        final_nucleus_exists=final_nucleus_exists,
        blocked_claims_list_exists=blocked_claims_exists,
        environment_metadata_present=environment_metadata_present,
        reproducible=not blocking_issues,
        blocking_issues=blocking_issues,
        warnings=warnings,
    )


def _entry_for_path(
    artifact_store: ArtifactStore,
    run_id: str,
    path,
) -> ArtifactManifestEntry:
    meta_path = path.with_name(f"{path.name}.meta.json")
    if meta_path.is_file():
        artifact = ArtifactRef.model_validate_json(meta_path.read_text(encoding="utf-8"))
    else:
        artifact = ArtifactRef(
            id=path.stem,
            type=_infer_artifact_type(artifact_store, run_id, path),
            path=path.relative_to(artifact_store.root).as_posix(),
            content_hash=sha256_file(path),
            metadata={"format": path.suffix.lstrip(".") or "unknown"},
        )
    is_evidence = _is_evidence_entry(artifact)
    return ArtifactManifestEntry(
        artifact_id=artifact.id,
        artifact_type=artifact.type,
        path=artifact.path,
        content_hash=artifact.content_hash,
        producing_commit_hash=artifact.producing_commit_hash,
        is_evidence=is_evidence,
        is_presentation=_is_presentation_entry(artifact, is_evidence),
        metadata=artifact.metadata,
    )


def _infer_artifact_type(artifact_store: ArtifactStore, run_id: str, path) -> ArtifactType:
    relative_parts = path.relative_to(artifact_store.run_path(run_id)).parts
    directory = relative_parts[0] if relative_parts else ""
    for artifact_type, mapped_directory in ARTIFACT_DIRECTORY_BY_TYPE.items():
        if directory == mapped_directory:
            return artifact_type
    return ArtifactType.REPORT


def _is_evidence_entry(artifact: ArtifactRef) -> bool:
    if not artifact.is_mvp_verification_evidence():
        return False
    if (
        artifact.type == ArtifactType.LEAN
        and artifact.metadata.get("evidence_role") in {"fake_proof", "proof"}
    ):
        return True
    if (
        artifact.type == ArtifactType.EXPERIMENT
        and artifact.metadata.get("evidence_role") == "fake_synthetic_experiment"
    ):
        return True
    if artifact.type == ArtifactType.LITERATURE:
        return True
    return artifact.metadata.get("evidence_role") in EVIDENCE_ROLES


def _is_presentation_entry(artifact: ArtifactRef, is_evidence: bool) -> bool:
    suffix = artifact.path.rsplit(".", maxsplit=1)[-1].lower() if "." in artifact.path else ""
    if artifact.type == ArtifactType.LATEX:
        return True
    if suffix in {"md", "markdown", "tex", "pdf"}:
        return True
    return artifact.type == ArtifactType.REPORT and not is_evidence
