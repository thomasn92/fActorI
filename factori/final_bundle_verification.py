"""Independent read-only verification of assembled final release bundles."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from factori.hashing import canonical_json, sha256_file
from factori.schemas import (
    CitationRegistry,
    ClaimEvidenceMap,
    FinalBundleReplaySummary,
    FinalBundleVerificationCheck,
    FinalBundleVerificationReport,
    FinalReleaseBundleManifest,
    FinalReleaseReproducibilityManifest,
)
from factori.storage_protocols import Clock, SystemClock


class FinalBundleVerificationError(RuntimeError):
    """Raised when no bundle can be selected for read-only verification."""


REQUIRED_BUNDLE_PATHS = frozenset(
    {
        "paper/paper.md",
        "paper/paper.tex",
        "paper/references.bib",
        "reports/release-report.json",
        "reports/final-audit.json",
        "reports/claim-evidence-map.json",
        "reports/claim-support-audit.json",
        "reports/citation-safety-report.json",
        "reports/reviewer-bundle-summary.json",
        "sources/citation-registry.json",
        "reproducibility/reproducibility-manifest.json",
        "reproducibility/artifact-manifest.json",
        "reproducibility/hashes.sha256",
        "reproducibility/environment.json",
        "reproducibility/commands.txt",
        "README.md",
    }
)
OPTIONAL_BUNDLE_PATHS = frozenset(
    {
        "paper/paper.pdf",
        "reports/capability-escalation-report.json",
    }
)

_BIB_KEY_RE = re.compile(r"@[A-Za-z]+\s*\{\s*([^,\s]+)")
_LATEX_CITE_RE = re.compile(r"\\cite(?:\[[^\]]*\])?\{([^}]*)\}")
_HASH_LINE_RE = re.compile(r"^([0-9a-f]{64})  (.+)$")
_SAFE_RELEASE_STATUSES = frozenset(
    {
        "ReadyForHumanReview",
        "ReadyForHumanReviewWithWarnings",
    }
)


@dataclass
class _CheckCollector:
    checks: list[FinalBundleVerificationCheck]

    def add(
        self,
        check_id: str,
        category: str,
        status: Literal["passed", "failed", "warned"],
        message: str,
        *,
        blocking: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.checks.append(
            FinalBundleVerificationCheck(
                check_id=check_id,
                category=category,
                status=status,
                message=message,
                blocking=blocking,
                details=details or {},
            )
        )


def verify_final_release_bundle(
    *,
    bundle_path: str | Path | None = None,
    run_id: str | None = None,
    root: str | Path = ".",
    write_report: bool = False,
    clock: Clock | None = None,
) -> FinalBundleVerificationReport:
    """Verify a final bundle from its contents without mutating the bundle."""
    selected, mode = _select_bundle_path(bundle_path=bundle_path, run_id=run_id, root=root)
    selected = selected.resolve()
    if not selected.is_dir():
        raise FinalBundleVerificationError(f"Final bundle directory not found: {selected}")

    collector = _CheckCollector([])
    artifact_manifest_path = selected / "reproducibility" / "artifact-manifest.json"
    reproducibility_path = selected / "reproducibility" / "reproducibility-manifest.json"
    hash_path = selected / "reproducibility" / "hashes.sha256"

    missing_required = sorted(
        relative for relative in REQUIRED_BUNDLE_PATHS if not (selected / relative).is_file()
    )
    collector.add(
        "required_artifacts",
        "completeness",
        "passed" if not missing_required else "failed",
        (
            "All required final bundle artifacts are present."
            if not missing_required
            else f"{len(missing_required)} required bundle artifact(s) are missing."
        ),
        blocking=bool(missing_required),
        details={"missing": missing_required},
    )
    missing_optional = sorted(
        relative for relative in OPTIONAL_BUNDLE_PATHS if not (selected / relative).is_file()
    )
    collector.add(
        "optional_artifacts",
        "completeness",
        "passed" if not missing_optional else "warned",
        (
            "All recognized optional bundle artifacts are present."
            if not missing_optional
            else "Optional bundle artifacts are absent; this does not invalidate the bundle."
        ),
        details={"missing": missing_optional},
    )

    hashes, hash_parse_errors = _read_hash_lock(hash_path)
    if hash_parse_errors:
        collector.add(
            "hash_file_format",
            "integrity",
            "failed",
            "The SHA-256 lock contains malformed, duplicate, or unsafe entries.",
            blocking=True,
            details={"errors": hash_parse_errors},
        )
    elif hash_path.is_file():
        collector.add(
            "hash_file_format",
            "integrity",
            "passed",
            "The SHA-256 lock is well formed.",
            details={"entry_count": len(hashes)},
        )
    else:
        collector.add(
            "hash_file_format",
            "integrity",
            "failed",
            "The SHA-256 lock is missing.",
            blocking=True,
        )

    hash_mismatches: list[str] = []
    hashes_verified = 0
    for relative, expected in hashes.items():
        path = selected / relative
        if not path.is_file() or path.is_symlink():
            hash_mismatches.append(relative)
            continue
        if sha256_file(path) != expected:
            hash_mismatches.append(relative)
            continue
        hashes_verified += 1
    collector.add(
        "hash_integrity",
        "integrity",
        "passed" if not hash_mismatches and bool(hashes) else "failed",
        (
            f"Verified {hashes_verified} locked artifact hashes."
            if not hash_mismatches and hashes
            else f"Detected {len(hash_mismatches)} missing or mismatched locked artifact(s)."
        ),
        blocking=bool(hash_mismatches) or not hashes,
        details={"mismatched_or_missing": hash_mismatches},
    )

    actual_locked_candidates = {
        path.relative_to(selected).as_posix()
        for path in selected.rglob("*")
        if path.is_file() and path != hash_path
    }
    unexpected = sorted(actual_locked_candidates - set(hashes))
    stale_hash_entries = sorted(set(hashes) - actual_locked_candidates)
    collector.add(
        "hash_inventory",
        "integrity",
        "passed" if not unexpected and not stale_hash_entries else "failed",
        (
            "The hash lock covers every bundle file except itself."
            if not unexpected and not stale_hash_entries
            else "The hash lock and bundle file inventory differ."
        ),
        blocking=bool(unexpected or stale_hash_entries),
        details={"unexpected": unexpected, "stale_hash_entries": stale_hash_entries},
    )

    manifest = _read_model(artifact_manifest_path, FinalReleaseBundleManifest)
    manifest_errors = _verify_artifact_manifest(selected, manifest, hashes)
    collector.add(
        "artifact_manifest_consistency",
        "manifest",
        "passed" if not manifest_errors else "failed",
        (
            "The artifact manifest is internally consistent with the bundle and hash lock."
            if not manifest_errors
            else "The artifact manifest is missing, malformed, or inconsistent."
        ),
        blocking=bool(manifest_errors),
        details={"errors": manifest_errors},
    )

    registry = _read_model(selected / "sources" / "citation-registry.json", CitationRegistry)
    reference_result = _verify_references(selected, registry)
    collector.add(
        "accepted_only_references",
        "citations",
        "passed" if reference_result["passed"] else "failed",
        reference_result["message"],
        blocking=not reference_result["passed"],
        details=reference_result["details"],
    )
    latex_result = _verify_latex_citations(selected, registry)
    collector.add(
        "paper_tex_citations",
        "citations",
        "passed" if latex_result["passed"] else "failed",
        latex_result["message"],
        blocking=not latex_result["passed"],
        details=latex_result["details"],
    )

    claim_map = _read_model(selected / "reports" / "claim-evidence-map.json", ClaimEvidenceMap)
    claim_result = _verify_claim_evidence(selected, claim_map)
    collector.add(
        "claim_evidence_map",
        "claim_evidence",
        "passed" if claim_result["passed"] else "failed",
        claim_result["message"],
        blocking=not claim_result["passed"],
        details=claim_result["details"],
    )

    release_result = _verify_release_report(
        selected,
        claim_result["blocking_unsupported_count"],
    )
    collector.add(
        "release_report",
        "release",
        "passed" if release_result["passed"] else "failed",
        release_result["message"],
        blocking=not release_result["passed"],
        details=release_result["details"],
    )

    reproducibility = _read_model(reproducibility_path, FinalReleaseReproducibilityManifest)
    environment = _read_json(selected / "reproducibility" / "environment.json")
    reproducibility_result = _verify_reproducibility(
        selected,
        reproducibility,
        environment,
        manifest,
    )
    collector.add(
        "reproducibility_metadata",
        "reproducibility",
        "passed" if reproducibility_result["passed"] else "failed",
        reproducibility_result["message"],
        blocking=not reproducibility_result["passed"],
        details=reproducibility_result["details"],
    )

    ledger_result = _verify_bundled_ledger(selected, reproducibility)
    collector.add(
        "bundled_ledger_consistency",
        "ledger",
        ledger_result["status"],
        ledger_result["message"],
        blocking=ledger_result["status"] == "failed",
        details=ledger_result["details"],
    )

    replay = _build_replay_summary(reproducibility)
    failed = sum(check.status == "failed" for check in collector.checks)
    warned = sum(check.status == "warned" for check in collector.checks)
    passed = sum(check.status == "passed" for check in collector.checks)
    if failed:
        integrity_failure = bool(
            hash_mismatches
            or hash_parse_errors
            or unexpected
            or stale_hash_entries
            or manifest_errors
            or not reference_result["passed"]
            or not latex_result["passed"]
            or not claim_result["passed"]
            or not release_result["passed"]
            or ledger_result["status"] == "failed"
        )
        status = "failed" if integrity_failure else "incomplete"
    elif missing_required:
        status = "incomplete"
    elif warned:
        status = "verified_with_warnings"
    else:
        status = "verified"

    bundle_id = manifest.bundle_id if manifest is not None else selected.name
    report = FinalBundleVerificationReport(
        bundle_path=selected.as_posix(),
        verification_id=f"final-bundle-verification-{bundle_id}",
        verification_status=status,
        verified_at=(clock or SystemClock()).now(),
        verification_mode=mode,
        bundle_manifest_path=artifact_manifest_path.as_posix(),
        reproducibility_manifest_path=reproducibility_path.as_posix(),
        hash_file_path=hash_path.as_posix(),
        artifact_manifest_path=artifact_manifest_path.as_posix(),
        checks_run=len(collector.checks),
        checks_passed=passed,
        checks_failed=failed,
        checks_warned=warned,
        checks=collector.checks,
        hashes_verified=hashes_verified,
        hash_mismatch_count=len(set(hash_mismatches + stale_hash_entries)),
        missing_required_artifact_count=len(missing_required),
        unexpected_artifact_count=len(unexpected),
        accepted_reference_check_passed=reference_result["passed"],
        rejected_reference_leak_count=reference_result["leak_count"],
        paper_tex_citation_check_passed=latex_result["passed"],
        claim_evidence_check_passed=claim_result["passed"],
        unsupported_claim_count=claim_result["unsupported_count"],
        release_report_check_passed=release_result["passed"],
        publication_ready=release_result["publication_ready"],
        ledger_check_passed=ledger_result["passed"],
        reproducibility_check_passed=reproducibility_result["passed"],
        environment_metadata_present=isinstance(environment, dict),
        network_used=replay.network_used,
        external_api_used=replay.external_api_used,
        external_tools_used=replay.external_tools_used,
        replay_summary=replay,
        bundle_modified=False,
    )
    if write_report:
        write_final_bundle_verification_report(report, root=root)
    return report


def write_final_bundle_verification_report(
    report: FinalBundleVerificationReport,
    *,
    root: str | Path = ".",
) -> tuple[Path, Path]:
    """Write an append-only non-provenance report outside the verified bundle."""
    output = _verification_report_directory(Path(report.bundle_path), Path(root), report)
    output.mkdir(parents=True, exist_ok=True)
    number = len(list(output.glob("final-bundle-verification-[0-9][0-9][0-9][0-9].json"))) + 1
    stem = f"final-bundle-verification-{number:04d}"
    json_path = output / f"{stem}.json"
    markdown_path = output / f"{stem}.md"
    _atomic_write(json_path, canonical_json(report) + "\n")
    _atomic_write(markdown_path, render_final_bundle_verification_markdown(report))
    return json_path, markdown_path


def latest_final_bundle_verification(
    root: str | Path,
    run_id: str,
) -> FinalBundleVerificationReport | None:
    """Load the latest optional verification report without inspecting mutable run inputs."""
    reports = Path(root) / "runs" / run_id / "reports"
    matches = sorted(reports.glob("final-bundle-verification-[0-9][0-9][0-9][0-9].json"))
    if not matches:
        return None
    return _read_model(matches[-1], FinalBundleVerificationReport)


def final_bundle_verification_summary_fields(
    report: FinalBundleVerificationReport | None,
) -> dict[str, Any]:
    """Return stable inspect and lint fields for optional bundle verification."""
    if report is None:
        return {
            "final_bundle_verification_present": False,
            "final_bundle_verified": "unknown",
            "final_bundle_verification_status": None,
            "final_bundle_checks_passed": 0,
            "final_bundle_checks_failed": 0,
            "final_bundle_checks_warned": 0,
            "final_bundle_hash_mismatch_count": 0,
            "final_bundle_missing_required_artifact_count": 0,
            "final_bundle_rejected_reference_leak_count": 0,
            "final_bundle_publication_ready_flag": False,
        }
    return {
        "final_bundle_verification_present": True,
        "final_bundle_verified": report.verification_status
        in {"verified", "verified_with_warnings"},
        "final_bundle_verification_status": report.verification_status,
        "final_bundle_checks_passed": report.checks_passed,
        "final_bundle_checks_failed": report.checks_failed,
        "final_bundle_checks_warned": report.checks_warned,
        "final_bundle_hash_mismatch_count": report.hash_mismatch_count,
        "final_bundle_missing_required_artifact_count": report.missing_required_artifact_count,
        "final_bundle_rejected_reference_leak_count": report.rejected_reference_leak_count,
        "final_bundle_publication_ready_flag": report.publication_ready,
    }


def render_final_bundle_verification_markdown(report: FinalBundleVerificationReport) -> str:
    """Render a concise replay-by-inspection report."""
    return "\n".join(
        [
            "# Final Bundle Verification",
            "",
            f"Bundle: `{report.bundle_path}`",
            f"Status: `{report.verification_status}`",
            f"Checks passed/failed/warned: `{report.checks_passed}/"
            f"{report.checks_failed}/{report.checks_warned}`",
            f"Hashes verified: `{report.hashes_verified}`",
            f"Hash mismatches: `{report.hash_mismatch_count}`",
            f"Missing required artifacts: `{report.missing_required_artifact_count}`",
            "",
            "## Checks",
            *[
                f"- `{check.status}` {check.check_id}: {check.message}"
                for check in report.checks
            ],
            "",
            "This report is read-only replay-by-inspection. It is not provenance, scientific "
            "validation, verification evidence, approval, or publication readiness.",
            "",
            f"- publication_ready: {str(report.publication_ready).lower()}",
            "- creates_scientific_validation: false",
            "- implies_publication_readiness: false",
            "- is_verification_evidence: false",
            "",
        ]
    )


def _select_bundle_path(
    *,
    bundle_path: str | Path | None,
    run_id: str | None,
    root: str | Path,
) -> tuple[Path, Literal["bundle_path", "run_id_lookup"]]:
    if (bundle_path is None) == (run_id is None):
        raise FinalBundleVerificationError("Provide exactly one of --bundle-path or --run-id.")
    if bundle_path is not None:
        return Path(bundle_path), "bundle_path"
    bundles = sorted((Path(root) / "runs" / str(run_id) / "release-bundles").glob("final-bundle-*"))
    bundles = [path for path in bundles if path.is_dir()]
    if not bundles:
        raise FinalBundleVerificationError(f"No final release bundle found for run_id={run_id}.")
    return bundles[-1], "run_id_lookup"


def _read_hash_lock(path: Path) -> tuple[dict[str, str], list[str]]:
    if not path.is_file():
        return {}, ["missing_hash_file"]
    hashes: dict[str, str] = {}
    errors: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        return {}, [f"unreadable_hash_file:{type(exc).__name__}"]
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        match = _HASH_LINE_RE.fullmatch(line)
        if match is None:
            errors.append(f"malformed_line:{number}")
            continue
        digest, relative = match.groups()
        if not _safe_relative_path(relative):
            errors.append(f"unsafe_path:{relative}")
            continue
        if relative in hashes:
            errors.append(f"duplicate_path:{relative}")
            continue
        hashes[relative] = digest
    return hashes, errors


def _verify_artifact_manifest(
    bundle: Path,
    manifest: FinalReleaseBundleManifest | None,
    hashes: dict[str, str],
) -> list[str]:
    if manifest is None:
        return ["missing_or_invalid_artifact_manifest"]
    errors: list[str] = []
    if manifest.publication_ready:
        errors.append("manifest_publication_ready_true")
    if manifest.artifact_count != len(manifest.artifacts):
        errors.append("manifest_artifact_count_mismatch")
    if manifest.hash_count != len(manifest.artifacts):
        errors.append("manifest_hash_count_mismatch")
    seen: set[str] = set()
    for artifact in manifest.artifacts:
        relative = artifact.relative_path
        if relative in seen:
            errors.append(f"duplicate_manifest_path:{relative}")
            continue
        seen.add(relative)
        if not _safe_relative_path(relative):
            errors.append(f"unsafe_manifest_path:{relative}")
            continue
        path = bundle / relative
        if not path.is_file() or path.is_symlink():
            errors.append(f"missing_manifest_artifact:{relative}")
            continue
        actual = sha256_file(path)
        if actual != artifact.sha256:
            errors.append(f"manifest_hash_mismatch:{relative}")
        if hashes.get(relative) != artifact.sha256:
            errors.append(f"hash_lock_manifest_mismatch:{relative}")
    expected_hash_paths = seen | {"reproducibility/artifact-manifest.json"}
    if set(hashes) != expected_hash_paths:
        errors.append("hash_inventory_manifest_mismatch")
    return errors


def _verify_references(bundle: Path, registry: CitationRegistry | None) -> dict[str, Any]:
    bib_path = bundle / "paper" / "references.bib"
    if registry is None or not bib_path.is_file():
        return {
            "passed": False,
            "leak_count": 0,
            "message": "Citation registry or references.bib is missing or malformed.",
            "details": {},
        }
    bib_keys = set(_BIB_KEY_RE.findall(_read_text(bib_path)))
    accepted = {
        record.citation_key
        for record in registry.citations
        if record.accepted_for_registry and record.source_status != "rejected"
    }
    explicitly_rejected = {
        record.citation_key
        for record in registry.citations
        if not record.accepted_for_registry or record.source_status == "rejected"
    }
    leaked = sorted((bib_keys - accepted) | (bib_keys & explicitly_rejected))
    registry_rejected = sorted(
        record.citation_key
        for record in registry.citations
        if not record.accepted_for_registry or record.source_status == "rejected"
    )
    passed = not leaked and not registry_rejected
    return {
        "passed": passed,
        "leak_count": len(leaked) + len(registry_rejected),
        "message": (
            "references.bib contains accepted registry keys only."
            if passed
            else "references.bib or the bundled registry contains rejected or unaccepted keys."
        ),
        "details": {
            "bib_keys": sorted(bib_keys),
            "leaked_or_unknown_keys": leaked,
            "rejected_registry_keys": registry_rejected,
        },
    }


def _verify_latex_citations(bundle: Path, registry: CitationRegistry | None) -> dict[str, Any]:
    tex_path = bundle / "paper" / "paper.tex"
    if registry is None or not tex_path.is_file():
        return {
            "passed": False,
            "message": "Citation registry or paper.tex is missing or malformed.",
            "details": {},
        }
    accepted = {
        record.citation_key
        for record in registry.citations
        if record.accepted_for_registry and record.source_status != "rejected"
    }
    citation_keys: set[str] = set()
    for group in _LATEX_CITE_RE.findall(_read_text(tex_path)):
        citation_keys.update(key.strip() for key in group.split(",") if key.strip())
    unregistered = sorted(citation_keys - accepted)
    return {
        "passed": not unregistered,
        "message": (
            "All paper.tex citation keys are accepted registry keys."
            if not unregistered
            else "paper.tex contains citation keys outside the accepted registry."
        ),
        "details": {
            "citation_keys": sorted(citation_keys),
            "unregistered_keys": unregistered,
        },
    }


def _verify_claim_evidence(bundle: Path, claim_map: ClaimEvidenceMap | None) -> dict[str, Any]:
    if claim_map is None:
        return {
            "passed": False,
            "unsupported_count": 0,
            "blocking_unsupported_count": 0,
            "message": "The claim-evidence map is missing or malformed.",
            "details": {},
        }
    unsupported_ids = set(claim_map.unsupported_non_scaffold_claim_ids)
    unsupported_ids.update(
        link.claim_id
        for link in claim_map.links
        if link.support_status in {"unsupported", "blocked_forbidden_claim"}
        and link.requires_support
    )
    deferred_nonblocking_ids = _deferred_nonblocking_claim_ids(bundle)
    blocking_unsupported_ids = unsupported_ids - deferred_nonblocking_ids
    proof_ids = _artifact_ids(bundle / "evidence" / "proof", "proof_id")
    experiment_ids = _artifact_ids(bundle / "evidence" / "experiments", "experiment_id")
    missing_proof_links: list[str] = []
    missing_experiment_links: list[str] = []
    for link in claim_map.links:
        if link.support_type == "formal_proof_verification":
            missing = set(link.supporting_proof_artifact_ids) - proof_ids
            missing_proof_links.extend(f"{link.claim_id}:{item}" for item in sorted(missing))
        if link.support_type == "experiment_result":
            missing = set(link.supporting_experiment_artifact_ids) - experiment_ids
            missing_experiment_links.extend(f"{link.claim_id}:{item}" for item in sorted(missing))
    forbidden_authority = claim_map.publication_ready or claim_map.implies_publication_readiness
    passed = not (
        blocking_unsupported_ids
        or missing_proof_links
        or missing_experiment_links
        or forbidden_authority
    )
    return {
        "passed": passed,
        "unsupported_count": len(unsupported_ids),
        "blocking_unsupported_count": len(blocking_unsupported_ids),
        "message": (
            "The claim-evidence map has no blocking unsupported claims; any unsupported claims "
            "are explicitly deferred and linked evidence is included."
            if passed
            else "The claim-evidence map has unsupported claims, missing linked evidence, "
            "or forbidden authority."
        ),
        "details": {
            "unsupported_claim_ids": sorted(unsupported_ids),
            "deferred_nonblocking_unsupported_claim_ids": sorted(
                unsupported_ids & deferred_nonblocking_ids
            ),
            "blocking_unsupported_claim_ids": sorted(blocking_unsupported_ids),
            "missing_proof_links": missing_proof_links,
            "missing_experiment_links": missing_experiment_links,
            "publication_ready": claim_map.publication_ready,
        },
    }


def _verify_release_report(bundle: Path, blocking_unsupported_count: int) -> dict[str, Any]:
    payload = _read_json(bundle / "reports" / "release-report.json")
    if not isinstance(payload, dict):
        return {
            "passed": False,
            "publication_ready": False,
            "message": "The release report is missing or malformed.",
            "details": {},
        }
    decision = payload.get("decision") if isinstance(payload.get("decision"), dict) else {}
    publication_ready = bool(payload.get("publication_ready") or decision.get("publication_ready"))
    release_status = str(decision.get("status") or payload.get("release_status") or "")
    blocking_reasons = list(decision.get("blocking_reasons") or [])
    safety_failures = [
        finding
        for finding in payload.get("findings", [])
        if isinstance(finding, dict)
        and str(finding.get("severity", "")).casefold() in {"blocking", "error", "critical"}
    ]
    passed = (
        not publication_ready
        and release_status in _SAFE_RELEASE_STATUSES
        and not blocking_reasons
        and not safety_failures
        and blocking_unsupported_count == 0
    )
    return {
        "passed": passed,
        "publication_ready": publication_ready,
        "message": (
            "The release remains scoped to human review with publication_ready=false."
            if passed
            else "The release report is unscoped, blocked, publication-ready, or inconsistent "
            "with claims."
        ),
        "details": {
            "release_status": release_status,
            "blocking_reasons": blocking_reasons,
            "blocking_safety_finding_count": len(safety_failures),
            "publication_ready": publication_ready,
            "blocking_unsupported_claim_count": blocking_unsupported_count,
        },
    }


def _verify_reproducibility(
    bundle: Path,
    manifest: FinalReleaseReproducibilityManifest | None,
    environment: Any,
    artifact_manifest: FinalReleaseBundleManifest | None,
) -> dict[str, Any]:
    if manifest is None:
        return {
            "passed": False,
            "message": "The reproducibility manifest is missing or malformed.",
            "details": {},
        }
    errors: list[str] = []
    if manifest.publication_ready or manifest.implies_publication_readiness:
        errors.append("reproducibility_manifest_publication_ready")
    if not manifest.factori_protocol_version:
        errors.append("missing_protocol_version")
    if not manifest.python_version or not manifest.platform:
        errors.append("missing_runtime_metadata")
    if not manifest.commands_used:
        errors.append("missing_commands_used")
    if not isinstance(environment, dict):
        errors.append("missing_environment_metadata")
    else:
        if not environment.get("python_version") or not environment.get("platform"):
            errors.append("incomplete_environment_metadata")
        if bool(environment.get("publication_ready")):
            errors.append("environment_publication_ready_true")
    if artifact_manifest is not None and (
        manifest.run_id != artifact_manifest.run_id
        or manifest.bundle_id != artifact_manifest.bundle_id
    ):
        errors.append("manifest_identity_mismatch")
    source_hash_mapping = {
        "final_manuscript": "paper/paper.md",
        "release_report": "reports/release-report.json",
        "claim_evidence_map": "reports/claim-evidence-map.json",
        "claim_support_audit": "reports/claim-support-audit.json",
        "citation_safety_report": "reports/citation-safety-report.json",
        "citation_registry": "sources/citation-registry.json",
        "retrieval_quality_report": "sources/retrieval-quality-report.json",
        "autonomous_loop_report": "reports/autonomous-loop-report.json",
    }
    stale_source_hashes: list[str] = []
    for key, relative in source_hash_mapping.items():
        expected = manifest.artifact_hashes.get(key)
        path = bundle / relative
        if expected and path.is_file() and sha256_file(path) != expected:
            stale_source_hashes.append(key)
    if stale_source_hashes:
        errors.append("source_artifact_hash_mismatch")
    return {
        "passed": not errors,
        "message": (
            "Reproducibility and environment metadata are internally consistent."
            if not errors
            else "Reproducibility or environment metadata is missing or inconsistent."
        ),
        "details": {"errors": errors, "stale_source_hashes": stale_source_hashes},
    }


def _verify_bundled_ledger(
    bundle: Path,
    reproducibility: FinalReleaseReproducibilityManifest | None,
) -> dict[str, Any]:
    payload = _read_json(bundle / "reports" / "ledger-validation.json")
    reproducibility_tip = reproducibility.ledger_tip_hash_optional if reproducibility else None
    if not isinstance(payload, dict):
        return {
            "status": "warned",
            "passed": False,
            "message": "No bundled ledger-validation summary is available.",
            "details": {},
        }
    tips = [str(item) for item in payload.get("tip_hashes", [])]
    status_valid = str(payload.get("status", "")).casefold() == "valid"
    internal_valid = status_valid and len(tips) == 1 and not payload.get("blocking_findings")
    mismatch = bool(reproducibility_tip and tips and reproducibility_tip != tips[0])
    if not internal_valid or mismatch:
        return {
            "status": "failed",
            "passed": False,
            "message": (
                "Bundled ledger metadata is invalid or disagrees with the reproducibility tip."
            ),
            "details": {
                "ledger_status": payload.get("status"),
                "tip_hashes": tips,
                "reproducibility_tip": reproducibility_tip,
            },
        }
    if not reproducibility_tip:
        return {
            "status": "warned",
            "passed": False,
            "message": "Ledger summary is valid, but no reproducibility ledger tip was recorded.",
            "details": {"tip_hashes": tips},
        }
    return {
        "status": "passed",
        "passed": True,
        "message": "Bundled ledger summary and reproducibility ledger tip are consistent.",
        "details": {"ledger_tip": tips[0]},
    }


def _build_replay_summary(
    manifest: FinalReleaseReproducibilityManifest | None,
) -> FinalBundleReplaySummary:
    if manifest is None:
        return FinalBundleReplaySummary()
    return FinalBundleReplaySummary(
        run_id=manifest.run_id,
        bundle_id=manifest.bundle_id,
        factori_protocol_version=manifest.factori_protocol_version,
        commands_used=manifest.commands_used,
        python_version=manifest.python_version,
        platform=manifest.platform,
        uv_lock_paths=manifest.uv_lock_paths,
        sandbox_configurations=manifest.sandbox_configurations,
        artifact_hashes=manifest.artifact_hashes,
        ledger_tip_hash_optional=manifest.ledger_tip_hash_optional,
        network_used=manifest.network_used,
        external_api_used=manifest.external_api_used,
        external_tools_used=manifest.external_tools_used,
        commands_reexecuted=False,
        publication_ready=False,
    )


def _artifact_ids(directory: Path, field: str) -> set[str]:
    if not directory.is_dir():
        return set()
    ids: set[str] = set()
    for path in directory.glob("*.json"):
        payload = _read_json(path)
        if isinstance(payload, dict) and payload.get(field):
            ids.add(str(payload[field]))
    return ids


def _deferred_nonblocking_claim_ids(bundle: Path) -> set[str]:
    payload = _read_json(bundle / "reports" / "autonomous-loop-report.json")
    if not isinstance(payload, dict):
        return set()
    deferred_classes = {
        "deferred_exhausted_proof",
        "deferred_exhausted_retrieval",
        "deferred_budget_exhausted",
        "deferred_requires_external_tool",
        "deferred_requires_network",
        "duplicate_only",
        "noncritical_boundary_gap",
    }
    return {
        str(item["target_claim_id_optional"])
        for item in payload.get("gap_terminal_classifications", [])
        if isinstance(item, dict)
        and item.get("target_claim_id_optional")
        and item.get("terminal_class") in deferred_classes
        and not bool(item.get("blocking"))
    }


def _verification_report_directory(
    bundle: Path,
    root: Path,
    report: FinalBundleVerificationReport,
) -> Path:
    try:
        relative = bundle.resolve().relative_to(root.resolve())
    except ValueError:
        relative = None
    if relative is not None and len(relative.parts) >= 4 and relative.parts[0] == "runs":
        return root / "runs" / relative.parts[1] / "reports"
    run_id = report.replay_summary.run_id or "unknown-run"
    return bundle.parent / f"{run_id}-verification-reports"


def _read_model(path: Path, model_type):
    if not path.is_file():
        return None
    try:
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _safe_relative_path(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(value) and not path.is_absolute() and ".." not in path.parts and "\\" not in value


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


__all__ = [
    "FinalBundleVerificationError",
    "REQUIRED_BUNDLE_PATHS",
    "final_bundle_verification_summary_fields",
    "latest_final_bundle_verification",
    "render_final_bundle_verification_markdown",
    "verify_final_release_bundle",
    "write_final_bundle_verification_report",
]
