"""Read-only deterministic indexing of files below one run directory."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import ValidationError

from factori.config import DEFAULT_ROOT, LEDGER_FILENAME, RUN_SUBDIRECTORIES
from factori.manifest import EVIDENCE_ROLES
from factori.schemas import (
    ArtifactManifest,
    ArtifactManifestEntry,
    ArtifactRef,
    ArtifactType,
    RunFileClassification,
    RunFileIndex,
    RunFileRecord,
)

NON_PROVENANCE_DIRECTORIES = frozenset(
    {"replay", "diagnostics", "comparisons", "hygiene"}
)
NORMAL_ARTIFACT_DIRECTORIES = frozenset(RUN_SUBDIRECTORIES)
ALLOWED_TOP_LEVEL_DIRECTORIES = NORMAL_ARTIFACT_DIRECTORIES | NON_PROVENANCE_DIRECTORIES

_CACHE_NAMES = {".DS_Store", "Thumbs.db"}
_CACHE_PARTS = {"__pycache__", ".pytest_cache", ".ruff_cache", ".cache"}
_TEMP_SUFFIXES = {
    ".bak",
    ".cache",
    ".old",
    ".orig",
    ".pyc",
    ".swp",
    ".temp",
    ".tmp",
}


@dataclass(frozen=True)
class IndexedRunState:
    """Internal indexed state reused by the hygiene checks."""

    index: RunFileIndex
    run_path: Path
    manifest: ArtifactManifest | None
    manifest_entries: dict[str, ArtifactManifestEntry]
    ledger_refs: dict[str, ArtifactRef]
    metadata_refs: dict[str, ArtifactRef]
    metadata_errors: dict[str, str]
    stored_path_errors: list[str]


def build_run_file_index(
    run_id: str,
    root: str | Path = DEFAULT_ROOT,
) -> RunFileIndex:
    """Walk and classify a run directory without writing any state."""
    return collect_run_file_state(run_id, root).index


def collect_run_file_state(
    run_id: str,
    root: str | Path = DEFAULT_ROOT,
) -> IndexedRunState:
    """Collect deterministic file, manifest, ledger, and sidecar metadata."""
    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    if not run_path.is_dir():
        index = RunFileIndex(
            run_id=run_id,
            run_exists=False,
            run_path=run_path.as_posix(),
            files=[],
            files_scanned=0,
            manifest_entries=0,
            ledger_exists=False,
            ledger_commit_count=0,
            artifact_manifest_exists=False,
        )
        return IndexedRunState(index, run_path, None, {}, {}, {}, {}, [])

    manifest_path = run_path / "research_object" / "artifact-manifest.json"
    manifest, manifest_error = _load_manifest(manifest_path)
    manifest_entries, manifest_path_errors = _manifest_entry_map(
        manifest,
        run_id,
        run_path,
    )
    ledger_path = run_path / LEDGER_FILENAME
    ledger_refs, ledger_count, ledger_error, ledger_path_errors = _load_ledger_refs(
        ledger_path,
        run_id,
        run_path,
    )
    metadata_refs, metadata_errors, metadata_path_errors = _load_metadata_refs(
        run_path,
        run_id,
    )
    load_errors = [error for error in (manifest_error, ledger_error) if error]
    stored_path_errors = sorted(
        {*manifest_path_errors, *ledger_path_errors, *metadata_path_errors}
    )

    directories = sorted(
        path.relative_to(run_path).as_posix()
        for path in run_path.rglob("*")
        if path.is_dir()
    )
    records = [
        _record_for_path(
            path=path,
            run_path=run_path,
            manifest_entries=manifest_entries,
            ledger_refs=ledger_refs,
            metadata_refs=metadata_refs,
        )
        for path in sorted(run_path.rglob("*"))
        if path.is_file()
    ]
    index = RunFileIndex(
        run_id=run_id,
        run_exists=True,
        run_path=run_path.as_posix(),
        files=records,
        directories=directories,
        files_scanned=len(records),
        manifest_entries=len(manifest.artifacts) if manifest is not None else 0,
        ledger_exists=ledger_path.is_file(),
        ledger_commit_count=ledger_count,
        artifact_manifest_exists=manifest_path.is_file(),
        load_errors=sorted(load_errors),
    )
    return IndexedRunState(
        index=index,
        run_path=run_path,
        manifest=manifest,
        manifest_entries=manifest_entries,
        ledger_refs=ledger_refs,
        metadata_refs=metadata_refs,
        metadata_errors=metadata_errors,
        stored_path_errors=stored_path_errors,
    )


def file_has_non_provenance_markers(path: Path) -> bool:
    """Return whether an optional report carries all boundary markers."""
    if path.suffix.lower() == ".json":
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            return False
        return all(
            payload.get(key) is True
            for key in ("not_provenance", "not_evidence", "not_ledgered")
        )
    if path.suffix.lower() in {".md", ".markdown"}:
        try:
            header = "\n".join(path.read_text(encoding="utf-8").splitlines()[:12])
        except (OSError, UnicodeDecodeError):
            return False
        return all(
            marker in header
            for marker in (
                "not_provenance: true",
                "not_evidence: true",
                "not_ledgered: true",
            )
        )
    return False


def is_cache_or_temp_path(relative_path: str) -> bool:
    """Return whether a run-relative path looks like cache or temporary output."""
    path = PurePosixPath(relative_path)
    name = path.name
    suffix = path.suffix.lower()
    return (
        name in _CACHE_NAMES
        or any(part in _CACHE_PARTS for part in path.parts)
        or suffix in _TEMP_SUFFIXES
        or name.startswith("~")
        or name.startswith(".#")
        or name.endswith("~")
        or name
        in {
            f"{LEDGER_FILENAME}-journal",
            f"{LEDGER_FILENAME}-shm",
            f"{LEDGER_FILENAME}-wal",
        }
    )


def _load_manifest(path: Path) -> tuple[ArtifactManifest | None, str | None]:
    if not path.is_file():
        return None, None
    try:
        return ArtifactManifest.model_validate_json(path.read_text(encoding="utf-8")), None
    except (OSError, ValidationError) as exc:
        return None, f"artifact manifest could not be loaded: {exc}"


def _manifest_entry_map(
    manifest: ArtifactManifest | None,
    run_id: str,
    run_path: Path,
) -> tuple[dict[str, ArtifactManifestEntry], list[str]]:
    entries: dict[str, ArtifactManifestEntry] = {}
    errors: list[str] = []
    if manifest is None:
        return entries, errors
    for entry in manifest.artifacts:
        relative = _normalize_stored_path(entry.path, run_id, run_path)
        if relative is None:
            errors.append(f"manifest path escapes run directory: {entry.path}")
            continue
        entries[relative] = entry
    return entries, errors


def _load_ledger_refs(
    ledger_path: Path,
    run_id: str,
    run_path: Path,
) -> tuple[dict[str, ArtifactRef], int, str | None, list[str]]:
    if not ledger_path.is_file():
        return {}, 0, None, []
    refs: dict[str, ArtifactRef] = {}
    path_errors: list[str] = []
    uri = f"file:{ledger_path.resolve().as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True) as connection:
            rows = connection.execute(
                "SELECT artifact_refs_json FROM commits WHERE run_id = ? ORDER BY rowid",
                (run_id,),
            ).fetchall()
    except sqlite3.Error as exc:
        return {}, 0, f"ledger could not be read: {exc}", []
    for (raw_refs,) in rows:
        try:
            parsed = json.loads(str(raw_refs))
            artifacts = [ArtifactRef.model_validate(item) for item in parsed]
        except (json.JSONDecodeError, ValidationError) as exc:
            return refs, len(rows), f"ledger artifact references could not be loaded: {exc}", []
        for artifact in artifacts:
            relative = _normalize_stored_path(artifact.path, run_id, run_path)
            if relative is None:
                path_errors.append(f"ledger artifact path escapes run directory: {artifact.path}")
                continue
            refs[relative] = artifact
    return refs, len(rows), None, path_errors


def _load_metadata_refs(
    run_path: Path,
    run_id: str,
) -> tuple[dict[str, ArtifactRef], dict[str, str], list[str]]:
    refs: dict[str, ArtifactRef] = {}
    errors: dict[str, str] = {}
    path_errors: list[str] = []
    for meta_path in sorted(run_path.rglob("*.meta.json")):
        relative_meta = meta_path.relative_to(run_path).as_posix()
        base_relative = relative_meta.removesuffix(".meta.json")
        try:
            artifact = ArtifactRef.model_validate_json(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValidationError) as exc:
            errors[relative_meta] = str(exc)
            continue
        stored_relative = _normalize_stored_path(artifact.path, run_id, run_path)
        if stored_relative is None:
            path_errors.append(f"metadata artifact path escapes run directory: {artifact.path}")
            continue
        if stored_relative != base_relative:
            errors[relative_meta] = (
                f"metadata path points to {stored_relative}, expected {base_relative}"
            )
            continue
        refs[base_relative] = artifact
    return refs, errors, path_errors


def _record_for_path(
    *,
    path: Path,
    run_path: Path,
    manifest_entries: dict[str, ArtifactManifestEntry],
    ledger_refs: dict[str, ArtifactRef],
    metadata_refs: dict[str, ArtifactRef],
) -> RunFileRecord:
    relative = path.relative_to(run_path).as_posix()
    top = PurePosixPath(relative).parts[0]
    is_meta = relative.endswith(".meta.json")
    base_relative = relative.removesuffix(".meta.json") if is_meta else relative
    manifest_entry = manifest_entries.get(base_relative)
    ledger_ref = ledger_refs.get(base_relative)
    metadata_ref = metadata_refs.get(base_relative)
    artifact_ref = metadata_ref or ledger_ref
    classification = _classification(
        relative,
        top,
        manifest_entry is not None,
        artifact_ref is not None,
        is_meta,
    )
    is_evidence, is_presentation = _evidence_flags(
        relative=base_relative,
        manifest_entry=manifest_entry,
        artifact_ref=artifact_ref,
    )
    return RunFileRecord(
        path=relative,
        classification=classification,
        size_bytes=path.stat().st_size,
        suffix=path.suffix.lower(),
        artifact_id=(
            manifest_entry.artifact_id
            if manifest_entry is not None
            else artifact_ref.id
            if artifact_ref is not None
            else None
        ),
        artifact_type=(
            manifest_entry.artifact_type
            if manifest_entry is not None
            else artifact_ref.type
            if artifact_ref is not None
            else None
        ),
        artifact_manifest_entry=manifest_entry is not None,
        manifested=(manifest_entry is not None or artifact_ref is not None),
        ledgered=ledger_ref is not None,
        has_metadata=metadata_ref is not None,
        non_provenance_marked=(
            file_has_non_provenance_markers(path)
            if top in NON_PROVENANCE_DIRECTORIES and not is_meta
            else False
        ),
        is_evidence=is_evidence,
        is_presentation=is_presentation,
    )


def _classification(
    relative: str,
    top: str,
    in_manifest: bool,
    linked: bool,
    is_meta: bool,
) -> RunFileClassification:
    if relative == LEDGER_FILENAME:
        return RunFileClassification.LEDGER
    if top == "replay":
        return RunFileClassification.REPLAY_REPORT
    if top == "diagnostics":
        return RunFileClassification.DIAGNOSTIC_REPORT
    if top == "comparisons":
        return RunFileClassification.COMPARISON_REPORT
    if top == "hygiene":
        return RunFileClassification.NON_PROVENANCE_REPORT
    if is_cache_or_temp_path(relative):
        return RunFileClassification.CACHE_OR_TEMP
    if in_manifest and not is_meta:
        return RunFileClassification.MANIFESTED_ARTIFACT
    if linked or is_meta:
        return RunFileClassification.NORMAL_ARTIFACT
    if top in NORMAL_ARTIFACT_DIRECTORIES:
        return RunFileClassification.UNMANIFESTED_FILE
    return RunFileClassification.UNEXPECTED


def _evidence_flags(
    *,
    relative: str,
    manifest_entry: ArtifactManifestEntry | None,
    artifact_ref: ArtifactRef | None,
) -> tuple[bool, bool]:
    if manifest_entry is not None:
        return manifest_entry.is_evidence, manifest_entry.is_presentation
    if artifact_ref is None:
        suffix = PurePosixPath(relative).suffix.lower()
        return False, suffix in {".md", ".markdown", ".pdf", ".tex"}
    suffix = PurePosixPath(relative).suffix.lower()
    presentation = artifact_ref.type == ArtifactType.LATEX or suffix in {
        ".md",
        ".markdown",
        ".pdf",
        ".tex",
    }
    role = artifact_ref.metadata.get("evidence_role")
    evidence = (
        artifact_ref.is_mvp_verification_evidence()
        and (
            role in EVIDENCE_ROLES
            or artifact_ref.type == ArtifactType.LITERATURE
        )
    )
    return evidence, presentation


def _normalize_stored_path(
    value: str,
    run_id: str,
    run_path: Path,
) -> str | None:
    path = Path(value)
    if path.is_absolute():
        try:
            return path.relative_to(run_path).as_posix()
        except ValueError:
            return None
    pure = PurePosixPath(value)
    parts = pure.parts
    prefix = ("runs", run_id)
    if parts[:2] == prefix:
        parts = parts[2:]
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return PurePosixPath(*parts).as_posix()


__all__ = [
    "ALLOWED_TOP_LEVEL_DIRECTORIES",
    "IndexedRunState",
    "NON_PROVENANCE_DIRECTORIES",
    "NORMAL_ARTIFACT_DIRECTORIES",
    "build_run_file_index",
    "collect_run_file_state",
    "file_has_non_provenance_markers",
    "is_cache_or_temp_path",
]
