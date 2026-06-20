"""Read-only deterministic inspection of run-directory output hygiene."""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

from factori.checkpoints import inspect_all_stage_checkpoints, stage_is_optional_checkpoint
from factori.config import DEFAULT_ROOT, LEDGER_FILENAME
from factori.hashing import canonical_json, sha256_file
from factori.reports import render_output_hygiene_report_markdown
from factori.run_files import (
    ALLOWED_TOP_LEVEL_DIRECTORIES,
    NON_PROVENANCE_DIRECTORIES,
    IndexedRunState,
    collect_run_file_state,
)
from factori.schemas import (
    ArtifactManifestEntry,
    OutputHygieneCategory,
    OutputHygieneFinding,
    OutputHygieneReport,
    OutputHygieneSeverity,
    OutputHygieneStatus,
    RunFileClassification,
)

_NON_PROVENANCE_CLASSIFICATIONS = {
    RunFileClassification.NON_PROVENANCE_REPORT,
    RunFileClassification.REPLAY_REPORT,
    RunFileClassification.DIAGNOSTIC_REPORT,
    RunFileClassification.COMPARISON_REPORT,
}
_PERSISTENCE_ONLY_NAMES = {
    "dry-run",
    "pipeline-dry-run",
    "plan-run",
    "run-status",
    "status-report",
    "resume-validation",
    "validate-resume",
}
_COPY_SUFFIX_RE = re.compile(r"(?:[-_. ](?:copy|duplicate)| \(\d+\))$", re.IGNORECASE)


def inspect_output_hygiene(
    run_id: str,
    root: str | Path = DEFAULT_ROOT,
) -> OutputHygieneReport:
    """Inspect one run without deleting, repairing, rewriting, or ledgering files."""
    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    ledger_path = run_path / LEDGER_FILENAME
    manifest_path = run_path / "research_object" / "artifact-manifest.json"
    ledger_hash_before = _file_hash(ledger_path)
    manifest_hash_before = _file_hash(manifest_path)

    state = collect_run_file_state(run_id, root_path)
    findings = _inspect_state(state, root_path)

    ledger_mutated = ledger_hash_before != _file_hash(ledger_path)
    artifact_manifest_mutated = manifest_hash_before != _file_hash(manifest_path)
    if ledger_mutated:
        findings.append(
            _finding(
                "inspection-mutated-ledger",
                OutputHygieneCategory.UNKNOWN,
                OutputHygieneSeverity.BLOCKING,
                "Output hygiene inspection changed the ledger file",
                [LEDGER_FILENAME],
                expected="unchanged ledger",
                observed="ledger content changed",
            )
        )
    if artifact_manifest_mutated:
        findings.append(
            _finding(
                "inspection-mutated-manifest",
                OutputHygieneCategory.UNKNOWN,
                OutputHygieneSeverity.BLOCKING,
                "Output hygiene inspection changed the artifact manifest",
                ["research_object/artifact-manifest.json"],
                expected="unchanged artifact manifest",
                observed="manifest content changed",
            )
        )

    findings = sorted(_deduplicate_findings(findings), key=lambda item: item.finding_id)
    warning_count = sum(
        finding.severity == OutputHygieneSeverity.WARNING for finding in findings
    )
    blocking_count = sum(
        finding.severity == OutputHygieneSeverity.BLOCKING for finding in findings
    )
    status = _hygiene_status(state, warning_count, blocking_count)
    report = OutputHygieneReport(
        run_id=run_id,
        hygiene_status=status,
        file_index=state.index,
        findings=findings,
        files_scanned=state.index.files_scanned,
        manifest_entries=state.index.manifest_entries,
        orphaned_files=_count_paths(findings, OutputHygieneCategory.ORPHANED_ARTIFACT),
        missing_manifest_files=_count_paths(
            findings,
            OutputHygieneCategory.MISSING_FILE_FOR_MANIFEST_ENTRY,
        ),
        hash_mismatches=_count_paths(findings, OutputHygieneCategory.HASH_MISMATCH),
        duplicate_outputs=_count_findings(findings, OutputHygieneCategory.DUPLICATE_OUTPUT),
        non_provenance_files=sum(
            record.classification in _NON_PROVENANCE_CLASSIFICATIONS
            for record in state.index.files
        ),
        unexpected_files=sum(
            record.classification == RunFileClassification.UNEXPECTED
            for record in state.index.files
        ),
        warnings_count=warning_count,
        blocking_findings_count=blocking_count,
        ledger_mutated=ledger_mutated,
        artifact_manifest_mutated=artifact_manifest_mutated,
    )
    return report


def summarize_output_hygiene(report: OutputHygieneReport) -> dict[str, Any]:
    """Return the canonical compact hygiene summary used by the CLI."""
    return {
        "run_id": report.run_id,
        "files_scanned": report.files_scanned,
        "manifest_entries": report.manifest_entries,
        "orphaned_files": report.orphaned_files,
        "missing_manifest_files": report.missing_manifest_files,
        "hash_mismatches": report.hash_mismatches,
        "duplicate_outputs": report.duplicate_outputs,
        "non_provenance_files": report.non_provenance_files,
        "unexpected_files": report.unexpected_files,
        "warnings": report.warnings_count,
        "blocking_findings": report.blocking_findings_count,
        "hygiene_status": report.hygiene_status.value,
    }


def write_output_hygiene_report(
    *,
    run_id: str,
    report: OutputHygieneReport,
    root: str | Path = DEFAULT_ROOT,
) -> tuple[Path, Path]:
    """Write optional hygiene reports outside provenance and normal manifests."""
    hygiene_path = Path(root) / "runs" / run_id / "hygiene"
    hygiene_path.mkdir(parents=True, exist_ok=True)
    payload = {
        "not_provenance": True,
        "not_evidence": True,
        "not_ledgered": True,
        "report": report.model_dump(mode="json"),
    }
    json_path = hygiene_path / "output-hygiene-report.json"
    markdown_path = hygiene_path / "output-hygiene-report.md"
    json_path.write_text(canonical_json(payload) + "\n", encoding="utf-8")
    markdown = "\n".join(
        [
            "---",
            "not_provenance: true",
            "not_evidence: true",
            "not_ledgered: true",
            "---",
            "",
            render_output_hygiene_report_markdown(hygiene_report=report),
        ]
    )
    markdown_path.write_text(markdown, encoding="utf-8")
    return json_path, markdown_path


def _inspect_state(state: IndexedRunState, root: Path) -> list[OutputHygieneFinding]:
    if not state.index.run_exists:
        return [
            _finding(
                "run-directory-missing",
                OutputHygieneCategory.UNKNOWN,
                OutputHygieneSeverity.BLOCKING,
                f"Run directory does not exist: {state.index.run_path}",
                expected="existing run directory",
                observed="missing",
            )
        ]

    findings: list[OutputHygieneFinding] = []
    findings.extend(_load_and_path_findings(state))
    findings.extend(_manifest_presence_findings(state))
    findings.extend(_reference_findings(state))
    findings.extend(_evidence_findings(state))
    findings.extend(_non_provenance_findings(state))
    findings.extend(_unmanifested_findings(state))
    findings.extend(_duplicate_findings(state))
    findings.extend(_stale_and_unexpected_findings(state))
    findings.extend(_checkpoint_findings(state, root))
    return findings


def _load_and_path_findings(state: IndexedRunState) -> list[OutputHygieneFinding]:
    findings = [
        _finding(
            f"load-error-{index}",
            OutputHygieneCategory.UNKNOWN,
            OutputHygieneSeverity.BLOCKING,
            error,
        )
        for index, error in enumerate(state.index.load_errors)
    ]
    findings.extend(
        _finding(
            f"stored-path-error-{index}",
            OutputHygieneCategory.MANIFEST_EXCLUSION_VIOLATION,
            OutputHygieneSeverity.BLOCKING,
            error,
        )
        for index, error in enumerate(state.stored_path_errors)
    )
    findings.extend(
        _finding(
            f"metadata-error-{_safe_id(path)}",
            OutputHygieneCategory.STALE_OUTPUT,
            OutputHygieneSeverity.WARNING,
            f"Artifact metadata sidecar is invalid: {error}",
            [path],
        )
        for path, error in sorted(state.metadata_errors.items())
    )
    return findings


def _manifest_presence_findings(state: IndexedRunState) -> list[OutputHygieneFinding]:
    package_or_later_exists = any(
        (state.run_path / relative).is_file()
        for relative in (
            "research_object/research-object.json",
            "research_object/paper-skeleton.json",
            "reports/final-audit-report.json",
            "reports/export-readiness-report.json",
        )
    )
    if package_or_later_exists and not state.index.artifact_manifest_exists:
        return [
            _finding(
                "artifact-manifest-missing",
                OutputHygieneCategory.MISSING_MANIFEST_ENTRY,
                OutputHygieneSeverity.BLOCKING,
                "Packaged or later-stage outputs exist but artifact-manifest.json is missing",
                ["research_object/artifact-manifest.json"],
                expected="artifact manifest after research object packaging",
                observed="missing",
            )
        ]
    return []


def _reference_findings(state: IndexedRunState) -> list[OutputHygieneFinding]:
    findings: list[OutputHygieneFinding] = []
    all_paths = sorted(
        set(state.manifest_entries) | set(state.ledger_refs) | set(state.metadata_refs)
    )
    for relative in all_paths:
        path = state.run_path / relative
        sources: list[tuple[str, str | None]] = []
        entry = state.manifest_entries.get(relative)
        if entry is not None:
            sources.append(("artifact manifest", entry.content_hash))
        ledger_ref = state.ledger_refs.get(relative)
        if ledger_ref is not None:
            sources.append(("ledger", ledger_ref.content_hash))
        metadata_ref = state.metadata_refs.get(relative)
        if metadata_ref is not None:
            sources.append(("metadata sidecar", metadata_ref.content_hash))
        if not path.is_file():
            findings.append(
                _finding(
                    f"referenced-file-missing-{_safe_id(relative)}",
                    OutputHygieneCategory.MISSING_FILE_FOR_MANIFEST_ENTRY,
                    OutputHygieneSeverity.BLOCKING,
                    "A manifest, ledger, or metadata entry points to a missing file",
                    [relative],
                    expected="existing referenced artifact",
                    observed="missing",
                )
            )
            continue
        observed_hash = sha256_file(path)
        mismatched_sources = [
            source
            for source, stored_hash in sources
            if not stored_hash or stored_hash != observed_hash
        ]
        if mismatched_sources:
            findings.append(
                _finding(
                    f"hash-mismatch-{_safe_id(relative)}",
                    OutputHygieneCategory.HASH_MISMATCH,
                    OutputHygieneSeverity.BLOCKING,
                    "Stored artifact hash does not match current file content",
                    [relative],
                    expected=f"matching hashes from {', '.join(mismatched_sources)}",
                    observed=observed_hash,
                )
            )
    return findings


def _evidence_findings(state: IndexedRunState) -> list[OutputHygieneFinding]:
    findings: list[OutputHygieneFinding] = []
    for relative, entry in sorted(state.manifest_entries.items()):
        if entry.is_evidence and not entry.producing_commit_hash:
            findings.append(
                _finding(
                    f"evidence-commit-missing-{_safe_id(relative)}",
                    OutputHygieneCategory.EVIDENCE_BOUNDARY_RISK,
                    OutputHygieneSeverity.BLOCKING,
                    "Evidence artifact lacks a producing commit hash",
                    [relative],
                )
            )
        if entry.is_evidence and (
            entry.is_presentation or _is_markdown_or_latex(relative, entry)
        ):
            findings.append(
                _finding(
                    f"presentation-evidence-{_safe_id(relative)}",
                    OutputHygieneCategory.EVIDENCE_BOUNDARY_RISK,
                    OutputHygieneSeverity.BLOCKING,
                    "Markdown, LaTeX, or presentation output is classified as "
                    "verification evidence",
                    [relative],
                )
            )
        top = PurePosixPath(relative).parts[0]
        if top in NON_PROVENANCE_DIRECTORIES:
            findings.append(
                _finding(
                    f"excluded-output-manifested-{_safe_id(relative)}",
                    OutputHygieneCategory.MANIFEST_EXCLUSION_VIOLATION,
                    OutputHygieneSeverity.BLOCKING,
                    "A non-provenance report appears in the normal artifact manifest",
                    [relative],
                )
            )
    return findings


def _non_provenance_findings(state: IndexedRunState) -> list[OutputHygieneFinding]:
    findings: list[OutputHygieneFinding] = []
    for record in state.index.files:
        if record.classification not in _NON_PROVENANCE_CLASSIFICATIONS:
            continue
        if record.path.endswith(".meta.json"):
            findings.append(
                _finding(
                    f"non-provenance-sidecar-{_safe_id(record.path)}",
                    OutputHygieneCategory.NON_PROVENANCE_LEAK,
                    OutputHygieneSeverity.BLOCKING,
                    "A non-provenance report has artifact-store metadata",
                    [record.path],
                )
            )
            continue
        if not record.non_provenance_marked:
            category = (
                OutputHygieneCategory.REPLAY_DIAGNOSTICS_LEAK
                if record.classification
                in {
                    RunFileClassification.REPLAY_REPORT,
                    RunFileClassification.DIAGNOSTIC_REPORT,
                }
                else OutputHygieneCategory.NON_PROVENANCE_LEAK
            )
            findings.append(
                _finding(
                    f"non-provenance-markers-missing-{_safe_id(record.path)}",
                    category,
                    OutputHygieneSeverity.BLOCKING,
                    "Optional report lacks non-provenance, non-evidence, or non-ledgered markers",
                    [record.path],
                )
            )
        if record.artifact_manifest_entry or record.ledgered or record.has_metadata:
            findings.append(
                _finding(
                    f"non-provenance-linked-{_safe_id(record.path)}",
                    OutputHygieneCategory.NON_PROVENANCE_LEAK,
                    OutputHygieneSeverity.BLOCKING,
                    "Optional report leaked into normal artifact or ledger provenance",
                    [record.path],
                )
            )
    return findings


def _unmanifested_findings(state: IndexedRunState) -> list[OutputHygieneFinding]:
    findings: list[OutputHygieneFinding] = []
    for record in state.index.files:
        if record.classification != RunFileClassification.UNMANIFESTED_FILE:
            continue
        findings.append(
            _finding(
                f"orphaned-artifact-{_safe_id(record.path)}",
                OutputHygieneCategory.ORPHANED_ARTIFACT,
                OutputHygieneSeverity.WARNING,
                "Artifact-like file has no manifest, ledger, or metadata linkage",
                [record.path],
            )
        )
        findings.append(
            _finding(
                f"manifest-entry-missing-{_safe_id(record.path)}",
                OutputHygieneCategory.MISSING_MANIFEST_ENTRY,
                OutputHygieneSeverity.WARNING,
                "Normal output is missing artifact linkage metadata",
                [record.path],
            )
        )
    return findings


def _duplicate_findings(state: IndexedRunState) -> list[OutputHygieneFinding]:
    groups: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for record in state.index.files:
        if record.path.endswith(".meta.json"):
            continue
        if record.classification in _NON_PROVENANCE_CLASSIFICATIONS | {
            RunFileClassification.LEDGER,
            RunFileClassification.CACHE_OR_TEMP,
            RunFileClassification.UNEXPECTED,
        }:
            continue
        path = PurePosixPath(record.path)
        logical_id = record.artifact_id or _COPY_SUFFIX_RE.sub("", path.stem)
        groups[(path.parent.as_posix(), logical_id, path.suffix.lower())].append(record.path)
    findings = []
    for (_parent, logical_id, suffix), paths in sorted(groups.items()):
        unique_paths = sorted(set(paths))
        if len(unique_paths) < 2:
            continue
        findings.append(
            _finding(
                f"duplicate-output-{_safe_id(logical_id + suffix)}",
                OutputHygieneCategory.DUPLICATE_OUTPUT,
                OutputHygieneSeverity.WARNING,
                f"Multiple files represent the same logical output {logical_id}{suffix}",
                unique_paths,
            )
        )
    if state.manifest is not None:
        manifest_paths = [entry.path for entry in state.manifest.artifacts]
        for path in sorted({item for item in manifest_paths if manifest_paths.count(item) > 1}):
            findings.append(
                _finding(
                    f"duplicate-manifest-entry-{_safe_id(path)}",
                    OutputHygieneCategory.DUPLICATE_OUTPUT,
                    OutputHygieneSeverity.WARNING,
                    "Artifact manifest contains duplicate path entries",
                    [path],
                )
            )
    return findings


def _stale_and_unexpected_findings(
    state: IndexedRunState,
) -> list[OutputHygieneFinding]:
    findings: list[OutputHygieneFinding] = []
    for record in state.index.files:
        if record.classification == RunFileClassification.CACHE_OR_TEMP:
            findings.append(
                _finding(
                    f"stale-output-{_safe_id(record.path)}",
                    OutputHygieneCategory.STALE_OUTPUT,
                    OutputHygieneSeverity.WARNING,
                    "Temporary, cache, backup, or stale output is present inside the run",
                    [record.path],
                )
            )
        if record.path.endswith(".meta.json"):
            base = record.path.removesuffix(".meta.json")
            if not (state.run_path / base).is_file():
                findings.append(
                    _finding(
                        f"stale-sidecar-{_safe_id(record.path)}",
                        OutputHygieneCategory.STALE_OUTPUT,
                        OutputHygieneSeverity.WARNING,
                        "Artifact metadata sidecar points to a missing output file",
                        [record.path, base],
                    )
                )
        if record.classification == RunFileClassification.UNEXPECTED:
            findings.append(
                _finding(
                    f"unexpected-file-{_safe_id(record.path)}",
                    OutputHygieneCategory.UNEXPECTED_FILE,
                    OutputHygieneSeverity.WARNING,
                    "Unexpected file is present in the run directory",
                    [record.path],
                )
            )
        stem = PurePosixPath(record.path).stem.lower()
        if any(name in stem for name in _PERSISTENCE_ONLY_NAMES):
            findings.append(
                _finding(
                    f"read-only-output-persisted-{_safe_id(record.path)}",
                    OutputHygieneCategory.NON_PROVENANCE_LEAK,
                    OutputHygieneSeverity.WARNING,
                    "Dry-run, status, or resume-validation output was persisted as a run artifact",
                    [record.path],
                )
            )

    top_level_directories = [path for path in state.index.directories if "/" not in path]
    for directory in top_level_directories:
        if directory not in ALLOWED_TOP_LEVEL_DIRECTORIES:
            findings.append(
                _finding(
                    f"unexpected-directory-{_safe_id(directory)}",
                    OutputHygieneCategory.UNEXPECTED_DIRECTORY,
                    OutputHygieneSeverity.WARNING,
                    "Unexpected top-level directory is present in the run",
                    [directory],
                )
            )
    return findings


def _checkpoint_findings(
    state: IndexedRunState,
    root: Path,
) -> list[OutputHygieneFinding]:
    checkpoints = inspect_all_stage_checkpoints(
        state.index.run_id,
        root,
        include_optional=False,
    )
    completed_indexes = [index for index, item in enumerate(checkpoints) if item.completed]
    if not completed_indexes:
        return []
    highest_completed = max(completed_indexes)
    findings: list[OutputHygieneFinding] = []
    for checkpoint in checkpoints[: highest_completed + 1]:
        if checkpoint.completed or stage_is_optional_checkpoint(checkpoint.stage_name):
            continue
        findings.append(
            _finding(
                f"stage-output-gap-{checkpoint.stage_name.value}",
                OutputHygieneCategory.STALE_OUTPUT,
                OutputHygieneSeverity.BLOCKING,
                f"A later stage is complete while {checkpoint.stage_name.value} "
                "outputs are missing",
                checkpoint.required_artifacts_missing,
            )
        )
    return findings


def _is_markdown_or_latex(relative: str, entry: ArtifactManifestEntry) -> bool:
    suffix = PurePosixPath(relative).suffix.lower()
    return entry.artifact_type.value == "latex" or suffix in {
        ".md",
        ".markdown",
        ".pdf",
        ".tex",
    }


def _hygiene_status(
    state: IndexedRunState,
    warnings: int,
    blocking: int,
) -> OutputHygieneStatus:
    if not state.index.run_exists or state.index.load_errors:
        return OutputHygieneStatus.HYGIENE_INSPECTION_FAILED
    if blocking:
        return OutputHygieneStatus.HYGIENE_ISSUES_FOUND
    if warnings:
        return OutputHygieneStatus.CLEAN_WITH_WARNINGS
    return OutputHygieneStatus.CLEAN


def _finding(
    finding_id: str,
    category: OutputHygieneCategory,
    severity: OutputHygieneSeverity,
    message: str,
    paths: list[str] | None = None,
    *,
    expected: str | None = None,
    observed: str | None = None,
) -> OutputHygieneFinding:
    return OutputHygieneFinding(
        finding_id=finding_id,
        category=category,
        severity=severity,
        message=message,
        paths=sorted(set(paths or [])),
        expected=expected,
        observed=observed,
    )


def _deduplicate_findings(
    findings: list[OutputHygieneFinding],
) -> list[OutputHygieneFinding]:
    by_id: dict[str, OutputHygieneFinding] = {}
    for finding in findings:
        by_id[finding.finding_id] = finding
    return list(by_id.values())


def _count_paths(
    findings: list[OutputHygieneFinding],
    category: OutputHygieneCategory,
) -> int:
    return len(
        {
            path
            for finding in findings
            if finding.category == category
            for path in finding.paths
        }
    )


def _count_findings(
    findings: list[OutputHygieneFinding],
    category: OutputHygieneCategory,
) -> int:
    return sum(finding.category == category for finding in findings)


def _safe_id(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_.-]+", "-", value).strip("-") or "item"


def _file_hash(path: Path) -> str | None:
    return sha256_file(path) if path.is_file() else None


__all__ = [
    "inspect_output_hygiene",
    "summarize_output_hygiene",
    "write_output_hygiene_report",
]
