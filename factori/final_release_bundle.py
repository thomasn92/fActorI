"""Deterministic final release bundle assembly and hash locking."""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from factori.artifacts import ArtifactStore
from factori.citations import CITATION_MARKER_RE
from factori.claim_evidence import (
    claim_evidence_summary_fields,
    latest_claim_evidence_map_path,
)
from factori.final_manuscript_regeneration import (
    latest_final_manuscript_regeneration,
)
from factori.full_paper_generation import (
    build_reviewer_bundle_summary,
    render_reviewer_bundle_summary_markdown,
)
from factori.hashing import canonical_json, sha256_file
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.protocols import PROTOCOL_VERSION
from factori.rerun_policy import validate_ledger_tip
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    CitationRegistry,
    ClaimEvidenceMap,
    ControllerActionType,
    FinalReleaseBundle,
    FinalReleaseBundleArtifact,
    FinalReleaseBundleIndex,
    FinalReleaseBundleManifest,
    FinalReleaseBundleReport,
    FinalReleaseReproducibilityManifest,
)


class FinalReleaseBundleError(RuntimeError):
    """Raised when final release bundle assembly cannot complete safely."""


@dataclass(frozen=True)
class FinalReleaseBundleResult:
    """Persisted final release bundle assembly result."""

    run_id: str
    report: FinalReleaseBundleReport
    index: FinalReleaseBundleIndex
    bundle: FinalReleaseBundle
    manifest: FinalReleaseBundleManifest
    reproducibility_manifest: FinalReleaseReproducibilityManifest
    persistence: PersistenceResult
    report_artifact: ArtifactRef
    index_artifact: ArtifactRef


@dataclass(frozen=True)
class _BundleCandidate:
    """One candidate file to copy or generate into the release bundle."""

    relative_path: str
    artifact_type: str
    created_by_stage: str
    required: bool = False
    source_path: Path | None = None
    content: str | bytes | None = None
    non_evidence_flag: bool = True


_LATEX_SPECIALS = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}
_BIB_KEY_RE = re.compile(r"@[A-Za-z]+\s*\{\s*([^,\s]+)")


def build_final_release_bundle(
    *,
    run_id: str,
    root: str | Path,
    store: ArtifactStore,
    ledger: ResearchLedger,
    compile_pdf: bool = False,
    strict_export: bool = False,
) -> FinalReleaseBundleResult:
    """Assemble an immutable final release bundle from the latest scoped artifacts."""
    root_path = Path(root)
    run_path = root_path / "runs" / run_id
    reports = run_path / "reports"
    if not reports.is_dir():
        raise FinalReleaseBundleError(f"No reports directory found for run_id={run_id}.")

    final_report, final_index = latest_final_manuscript_regeneration(root_path, run_id)
    if final_report is None or final_index is None:
        raise FinalReleaseBundleError(
            "Build a final manuscript before assembling a release bundle."
        )

    number = _next_bundle_number(run_path)
    bundle_id = f"final-bundle-{number:04d}"
    report_id = f"final-release-bundle-{number:04d}"
    index_id = f"final-release-bundle-index-{number:04d}"
    reviewer_id = f"reviewer-bundle-summary-after-final-release-bundle-{number:04d}"
    bundle_dir = run_path / "release-bundles" / bundle_id
    if bundle_dir.exists():
        raise FinalReleaseBundleError(f"Bundle directory already exists: {bundle_dir}")
    bundle_dir.mkdir(parents=True)

    paths = _source_paths(root_path, run_id, final_report)
    registry = _read_model(paths["citation_registry"], CitationRegistry)
    claim_map = _read_model(paths["claim_evidence_map"], ClaimEvidenceMap)
    if registry is None:
        raise FinalReleaseBundleError("A citation registry is required for final bundle assembly.")
    if claim_map is None:
        raise FinalReleaseBundleError("A claim-evidence map is required for final bundle assembly.")
    _require_accepted_registry(registry)
    paper_markdown = _read_text(paths["final_manuscript"])
    paper_tex = markdown_to_latex(paper_markdown, run_id=run_id)
    references_bib = build_references_bib(registry)
    accepted_sources = [
        record.model_dump(mode="json")
        for record in registry.citations
        if record.accepted_for_registry
    ]
    rejected_sources = _rejected_sources_payload(paths)
    ledger_tip = validate_ledger_tip(run_id, root=root_path)
    ledger_validation = ledger_tip.model_dump(mode="json")

    candidates = _bundle_candidates(
        root_path=root_path,
        run_id=run_id,
        paths=paths,
        paper_markdown=paper_markdown,
        paper_tex=paper_tex,
        references_bib=references_bib,
        accepted_sources=accepted_sources,
        rejected_sources=rejected_sources,
        ledger_validation=ledger_validation,
    )
    missing_required = _write_candidates(bundle_dir, candidates)
    pdf_path: str | None = None
    pdf_missing_reason: str | None = None
    if compile_pdf:
        pdf_path, pdf_missing_reason = _compile_pdf(
            bundle_dir=bundle_dir, strict_export=strict_export
        )
        if pdf_path is not None:
            candidates.append(
                _BundleCandidate(
                    relative_path="paper/paper.pdf",
                    artifact_type="pdf",
                    created_by_stage="optional_latex_compile",
                    source_path=bundle_dir / "paper" / "paper.pdf",
                    required=False,
                )
            )
        elif strict_export:
            missing_required.append(pdf_missing_reason or "paper.pdf")

    reproducibility = _build_reproducibility_manifest(
        run_id=run_id,
        bundle_id=bundle_id,
        root=root_path,
        run_path=run_path,
        paths=paths,
        network_used=False,
        external_tools_used=bool(pdf_path),
        ledger_tip_hash=ledger.latest_commit_hash(run_id),
    )
    _write_text(
        bundle_dir / "reproducibility" / "reproducibility-manifest.json",
        canonical_json(reproducibility) + "\n",
    )
    candidates.append(
        _BundleCandidate(
            relative_path="reproducibility/reproducibility-manifest.json",
            artifact_type="reproducibility_manifest",
            created_by_stage="final_release_bundle",
            required=True,
            source_path=bundle_dir / "reproducibility" / "reproducibility-manifest.json",
        )
    )
    manifest = _build_manifest(
        run_id=run_id,
        bundle_id=bundle_id,
        bundle_path=_relative(bundle_dir, root_path),
        bundle_dir=bundle_dir,
        root=root_path,
        candidates=candidates,
    )
    _write_text(
        bundle_dir / "reproducibility" / "artifact-manifest.json",
        canonical_json(manifest) + "\n",
    )
    hashes = _write_hash_lock(bundle_dir)
    _verify_hash_lock(bundle_dir)

    manifest_path = _relative(bundle_dir / "reproducibility" / "artifact-manifest.json", root_path)
    reproducibility_path = _relative(
        bundle_dir / "reproducibility" / "reproducibility-manifest.json",
        root_path,
    )
    hashes_path = _relative(bundle_dir / "reproducibility" / "hashes.sha256", root_path)
    paper_md_path = _relative(bundle_dir / "paper" / "paper.md", root_path)
    paper_tex_path = _relative(bundle_dir / "paper" / "paper.tex", root_path)
    references_path = _relative(bundle_dir / "paper" / "references.bib", root_path)
    pdf_relative = _relative(bundle_dir / "paper" / "paper.pdf", root_path) if pdf_path else None
    release_report_bundle_path = _relative(
        bundle_dir / "reports" / "release-report.json", root_path
    )
    reviewer_bundle_path = _relative(
        bundle_dir / "reports" / "reviewer-bundle-summary.json",
        root_path,
    )
    status = "incomplete" if missing_required else "complete"
    claim_counts = claim_evidence_summary_fields(claim_map)
    if int(claim_counts["claim_evidence_unsupported_count"]):
        status = "incomplete"
        missing_required.append("claim_evidence_map_without_unsupported_claims")
    post_check_missing = _post_bundle_checks(
        bundle_dir=bundle_dir,
        registry=registry,
        claim_map=claim_map,
    )
    missing_required.extend(item for item in post_check_missing if item not in missing_required)
    if post_check_missing:
        status = "incomplete"

    report = FinalReleaseBundleReport(
        run_id=run_id,
        bundle_id=bundle_id,
        bundle_status=status,
        bundle_path=_relative(bundle_dir, root_path),
        manifest_path=manifest_path,
        reproducibility_manifest_path=reproducibility_path,
        final_manuscript_path=final_report.final_manuscript_path,
        paper_markdown_path=paper_md_path,
        paper_tex_path_optional=paper_tex_path,
        references_bib_path_optional=references_path,
        pdf_path_optional=pdf_relative,
        claim_evidence_map_path=_relative(
            bundle_dir / "reports" / "claim-evidence-map.json", root_path
        ),
        citation_registry_path=_relative(
            bundle_dir / "sources" / "citation-registry.json", root_path
        ),
        retrieval_report_paths=_bundle_retrieval_paths(bundle_dir, root_path),
        proof_artifact_paths=_bundle_evidence_paths(bundle_dir, root_path, "evidence/proof/*.json"),
        experiment_artifact_paths=_bundle_evidence_paths(
            bundle_dir,
            root_path,
            "evidence/experiments/*.json",
        ),
        autonomous_loop_report_path=_relative(
            bundle_dir / "reports" / "autonomous-loop-report.json",
            root_path,
        ),
        capability_escalation_report_path_optional=(
            _relative(bundle_dir / "reports" / "capability-escalation-report.json", root_path)
            if (bundle_dir / "reports" / "capability-escalation-report.json").is_file()
            else None
        ),
        release_report_path=release_report_bundle_path,
        reviewer_summary_path=reviewer_bundle_path,
        ledger_validation_path_optional=_relative(
            bundle_dir / "reports" / "ledger-validation.json",
            root_path,
        ),
        artifact_count=len(hashes),
        hash_count=len(hashes),
        missing_required_artifacts=sorted(set(missing_required)),
        deferred_gap_count=final_report.deferred_gap_count,
        publication_ready=False,
    )
    bundle = FinalReleaseBundle(
        run_id=run_id,
        bundle_id=bundle_id,
        bundle_status=report.bundle_status,
        bundle_path=report.bundle_path,
        manifest_path=manifest_path,
        reproducibility_manifest_path=reproducibility_path,
        artifact_manifest_path=manifest_path,
        hashes_path=hashes_path,
        artifact_count=report.artifact_count,
        hash_count=report.hash_count,
        missing_required_artifacts=report.missing_required_artifacts,
        publication_ready=False,
    )
    index = FinalReleaseBundleIndex(
        run_id=run_id,
        latest_bundle_id=bundle_id,
        bundle_count=number,
        latest_bundle_status=report.bundle_status,
        latest_bundle_path=report.bundle_path,
        latest_report_path=f"runs/{run_id}/reports/{report_id}.json",
        latest_manifest_path=manifest_path,
        latest_reproducibility_manifest_path=reproducibility_path,
        publication_ready=False,
    )
    reviewer = build_reviewer_bundle_summary(
        run_id=run_id,
        root=root_path,
        final_release_bundle_report=report,
        final_release_bundle_index=index,
    )
    _write_text(
        bundle_dir / "reports" / "reviewer-bundle-summary.json",
        canonical_json(reviewer) + "\n",
    )
    _write_text(
        bundle_dir / "reports" / "reviewer-bundle-summary.md",
        render_reviewer_bundle_summary_markdown(reviewer),
    )
    manifest = _build_manifest(
        run_id=run_id,
        bundle_id=bundle_id,
        bundle_path=_relative(bundle_dir, root_path),
        bundle_dir=bundle_dir,
        root=root_path,
        candidates=[
            *candidates,
            _BundleCandidate(
                relative_path="reports/reviewer-bundle-summary.json",
                artifact_type="reviewer_summary",
                created_by_stage="final_release_bundle",
                required=True,
                source_path=bundle_dir / "reports" / "reviewer-bundle-summary.json",
            ),
            _BundleCandidate(
                relative_path="reports/reviewer-bundle-summary.md",
                artifact_type="reviewer_summary_markdown",
                created_by_stage="final_release_bundle",
                source_path=bundle_dir / "reports" / "reviewer-bundle-summary.md",
            ),
        ],
    )
    _write_text(
        bundle_dir / "reproducibility" / "artifact-manifest.json",
        canonical_json(manifest) + "\n",
    )
    hashes = _write_hash_lock(bundle_dir)
    _verify_hash_lock(bundle_dir)
    report = report.model_copy(update={"artifact_count": len(hashes), "hash_count": len(hashes)})
    bundle = bundle.model_copy(update={"artifact_count": len(hashes), "hash_count": len(hashes)})

    metadata = _artifact_metadata("final_release_bundle", "final_release_bundle_context")
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(report_id, ArtifactType.REPORT, report, "json", metadata),
            ArtifactWriteSpec(
                f"{report_id}-markdown",
                ArtifactType.REPORT,
                render_final_release_bundle_markdown(report),
                "markdown",
                metadata,
                filename_stem=report_id,
            ),
            ArtifactWriteSpec(index_id, ArtifactType.REPORT, index, "json", metadata),
            ArtifactWriteSpec(reviewer_id, ArtifactType.REPORT, reviewer, "json", metadata),
            ArtifactWriteSpec(
                f"{reviewer_id}-markdown",
                ArtifactType.REPORT,
                render_reviewer_bundle_summary_markdown(reviewer),
                "markdown",
                metadata,
                filename_stem=reviewer_id,
            ),
        ],
        action_type=ControllerActionType.FINAL_RELEASE_BUNDLE_ASSEMBLED,
        commit_payload={
            "run_id": run_id,
            "bundle_id": bundle_id,
            "bundle_status": report.bundle_status,
            "artifact_count": report.artifact_count,
            "hash_count": report.hash_count,
            "missing_required_artifacts": report.missing_required_artifacts,
            "publication_ready": False,
            "creates_scientific_validation": False,
            "implies_publication_readiness": False,
            "is_verification_evidence": False,
        },
    )
    by_id = {artifact.id: artifact for artifact in persistence.artifacts}
    return FinalReleaseBundleResult(
        run_id=run_id,
        report=report,
        index=index,
        bundle=bundle,
        manifest=manifest,
        reproducibility_manifest=reproducibility,
        persistence=persistence,
        report_artifact=by_id[report_id],
        index_artifact=by_id[index_id],
    )


def inspect_final_release_bundle(*, run_id: str, root: str | Path = ".") -> dict[str, Any]:
    """Inspect the latest final release bundle without mutation."""
    report, index = latest_final_release_bundle(Path(root), run_id)
    if report is None or index is None:
        raise FinalReleaseBundleError(f"No final release bundle found for run_id={run_id}.")
    return {
        **report.model_dump(mode="json"),
        **final_release_bundle_summary_fields(report, index),
        "final_release_bundle_index": index.model_dump(mode="json"),
    }


def latest_final_release_bundle(
    root: Path,
    run_id: str,
) -> tuple[FinalReleaseBundleReport | None, FinalReleaseBundleIndex | None]:
    """Load the latest immutable final release bundle report and index."""
    reports = root / "runs" / run_id / "reports"
    indexes = _numbered_paths(reports, "final-release-bundle-index-*.json")
    if not indexes:
        return None, None
    try:
        index = FinalReleaseBundleIndex.model_validate_json(indexes[-1].read_text(encoding="utf-8"))
        report = FinalReleaseBundleReport.model_validate_json(
            (root / index.latest_report_path).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return None, None
    return report, index


def final_release_bundle_summary_fields(
    report: FinalReleaseBundleReport | None,
    index: FinalReleaseBundleIndex | None = None,
) -> dict[str, Any]:
    """Return stable inspect/lint/reviewer fields for final release bundles."""
    if report is None:
        return {
            "final_release_bundle_present": False,
            "final_release_bundle_status": None,
            "final_release_bundle_count": 0,
            "final_release_bundle_path": None,
            "final_release_bundle_artifact_count": 0,
            "final_release_bundle_hash_count": 0,
            "paper_tex_present": False,
            "references_bib_present": False,
            "paper_pdf_present": False,
            "final_release_bundle_missing_required_artifact_count": 0,
            "missing_required_bundle_artifacts": [],
        }
    return {
        "final_release_bundle_present": True,
        "final_release_bundle_status": report.bundle_status,
        "final_release_bundle_count": index.bundle_count if index else 1,
        "final_release_bundle_path": report.bundle_path,
        "final_release_bundle_artifact_count": report.artifact_count,
        "final_release_bundle_hash_count": report.hash_count,
        "paper_tex_present": report.paper_tex_path_optional is not None,
        "references_bib_present": report.references_bib_path_optional is not None,
        "paper_pdf_present": report.pdf_path_optional is not None,
        "final_release_bundle_missing_required_artifact_count": len(
            report.missing_required_artifacts
        ),
        "missing_required_bundle_artifacts": list(report.missing_required_artifacts),
    }


def render_final_release_bundle_markdown(report: FinalReleaseBundleReport) -> str:
    """Render a concise non-evidence final bundle report."""
    missing = report.missing_required_artifacts or ["none"]
    return "\n".join(
        [
            "# Final Release Bundle Report",
            "",
            f"Run ID: `{report.run_id}`",
            f"Bundle ID: `{report.bundle_id}`",
            f"Status: `{report.bundle_status}`",
            f"Bundle path: `{report.bundle_path}`",
            f"Artifacts included: `{report.artifact_count}`",
            f"Hashes written: `{report.hash_count}`",
            f"Deferred gaps represented: `{report.deferred_gap_count}`",
            "",
            "## Missing Required Artifacts",
            *[f"- {item}" for item in missing],
            "",
            "This release bundle is packaging and reproducibility context only. It does not create "
            "proof, experiment evidence, scientific validation, or publication readiness.",
            "",
            "- publication_ready: false",
            "- creates_scientific_validation: false",
            "- implies_publication_readiness: false",
            "- is_verification_evidence: false",
            "",
        ]
    )


def markdown_to_latex(markdown: str, *, run_id: str) -> str:
    """Convert a constrained Markdown manuscript to deterministic LaTeX."""
    lines = markdown.splitlines()
    title = f"Final Evidence-Aware Manuscript for {run_id}"
    output: list[str] = []
    in_itemize = False
    in_verbatim = False
    for raw in lines:
        line = raw.rstrip()
        if line.startswith("```"):
            if in_itemize:
                output.append(r"\end{itemize}")
                in_itemize = False
            output.append(r"\begin{verbatim}" if not in_verbatim else r"\end{verbatim}")
            in_verbatim = not in_verbatim
            continue
        if in_verbatim:
            output.append(line)
            continue
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*$", line)
        if heading:
            if in_itemize:
                output.append(r"\end{itemize}")
                in_itemize = False
            level = len(heading.group(1))
            text = _latex_inline(heading.group(2))
            if level == 1:
                title = heading.group(2).strip()
            elif level == 2:
                output.append(rf"\section{{{text}}}")
            elif level == 3:
                output.append(rf"\subsection{{{text}}}")
            else:
                output.append(rf"\paragraph{{{text}}}")
            continue
        if not line.strip():
            if in_itemize:
                output.append(r"\end{itemize}")
                in_itemize = False
            output.append("")
            continue
        if line.startswith("- "):
            if not in_itemize:
                output.append(r"\begin{itemize}")
                in_itemize = True
            output.append(rf"\item {_latex_inline(line[2:].strip())}")
            continue
        if in_itemize:
            output.append(r"\end{itemize}")
            in_itemize = False
        output.append(_latex_inline(line))
    if in_itemize:
        output.append(r"\end{itemize}")
    if in_verbatim:
        output.append(r"\end{verbatim}")
    return "\n".join(
        [
            "% Generated by fActorI final release bundle.",
            "% publication_ready = false",
            r"\documentclass[11pt]{article}",
            r"\usepackage[T1]{fontenc}",
            r"\usepackage[utf8]{inputenc}",
            r"\usepackage{hyperref}",
            r"\usepackage{geometry}",
            r"\geometry{margin=1in}",
            rf"\title{{{_latex_escape(title)}}}",
            r"\date{}",
            r"\begin{document}",
            r"\maketitle",
            "",
            *output,
            "",
            r"\bibliographystyle{plain}",
            r"\bibliography{references}",
            r"\end{document}",
            "",
        ]
    )


def build_references_bib(registry: CitationRegistry) -> str:
    """Generate deterministic BibTeX from accepted registry entries only."""
    entries = []
    for record in sorted(registry.citations, key=lambda item: item.citation_key):
        if not record.accepted_for_registry:
            continue
        fields = [("title", record.title)]
        if record.authors:
            fields.append(("author", " and ".join(record.authors)))
        if record.year is not None:
            fields.append(("year", str(record.year)))
        if record.venue:
            fields.append(("journal", record.venue))
        if record.doi:
            fields.append(("doi", record.doi))
        if record.url:
            fields.append(("url", record.url))
        fields.append(("note", f"Bounded background context only; source_id={record.source_id}"))
        body = ",\n".join(
            f"  {key} = {{{_bib_escape(value)}}}" for key, value in fields if str(value).strip()
        )
        entries.append(f"@misc{{{record.citation_key},\n{body}\n}}")
    return "\n\n".join(entries).rstrip() + ("\n" if entries else "")


def _bundle_candidates(
    *,
    root_path: Path,
    run_id: str,
    paths: dict[str, Path],
    paper_markdown: str,
    paper_tex: str,
    references_bib: str,
    accepted_sources: list[dict[str, Any]],
    rejected_sources: list[dict[str, Any]],
    ledger_validation: dict[str, Any],
) -> list[_BundleCandidate]:
    candidates = [
        _BundleCandidate(
            "paper/paper.md",
            "markdown",
            "final_manuscript_regeneration",
            True,
            content=paper_markdown,
        ),
        _BundleCandidate(
            "paper/paper.tex", "latex", "final_release_bundle", True, content=paper_tex
        ),
        _BundleCandidate(
            "paper/references.bib", "bibtex", "final_release_bundle", True, content=references_bib
        ),
        _BundleCandidate(
            "reports/release-report.json",
            "release_report",
            "final_manuscript_regeneration",
            True,
            paths["release_report"],
        ),
        _final_audit_candidate(paths),
        _BundleCandidate(
            "reports/claim-evidence-map.json",
            "claim_evidence_map",
            "claim_evidence",
            True,
            paths["claim_evidence_map"],
        ),
        _BundleCandidate(
            "reports/claim-support-audit.json",
            "claim_support_audit",
            "final_manuscript_regeneration",
            True,
            paths["claim_support_audit"],
        ),
        _BundleCandidate(
            "reports/citation-safety-report.json",
            "citation_safety_report",
            "final_manuscript_regeneration",
            True,
            paths["citation_safety_report"],
        ),
        _BundleCandidate(
            "reports/autonomous-loop-report.json",
            "autonomous_loop_report",
            "autonomous_loop",
            True,
            paths["autonomous_loop_report"],
        ),
        _BundleCandidate(
            "reports/ledger-validation.json",
            "ledger_validation",
            "final_release_bundle",
            False,
            content=canonical_json(ledger_validation) + "\n",
        ),
        _BundleCandidate(
            "sources/citation-registry.json",
            "citation_registry",
            "citations",
            True,
            paths["citation_registry"],
        ),
        _BundleCandidate(
            "sources/retrieval-quality-report.json",
            "retrieval_quality_report",
            "retrieval",
            False,
            paths["retrieval_quality_report"],
        ),
        _BundleCandidate(
            "sources/accepted-sources.json",
            "accepted_sources",
            "final_release_bundle",
            False,
            content=canonical_json({"run_id": run_id, "accepted_sources": accepted_sources}) + "\n",
        ),
        _BundleCandidate(
            "sources/rejected-sources.json",
            "rejected_sources",
            "final_release_bundle",
            False,
            content=canonical_json({"run_id": run_id, "rejected_sources": rejected_sources}) + "\n",
        ),
        _BundleCandidate(
            "README.md", "readme", "final_release_bundle", False, content=_bundle_readme(run_id)
        ),
        _BundleCandidate(
            "reproducibility/environment.json",
            "environment",
            "final_release_bundle",
            False,
            content=canonical_json(_environment_payload()) + "\n",
        ),
        _BundleCandidate(
            "reproducibility/commands.txt",
            "commands",
            "final_release_bundle",
            False,
            content=f"factori build-final-release-bundle --run-id {run_id}\n",
        ),
    ]
    if paths["capability_escalation_report"].is_file():
        candidates.append(
            _BundleCandidate(
                "reports/capability-escalation-report.json",
                "capability_escalation_report",
                "capability_escalation",
                False,
                paths["capability_escalation_report"],
            )
        )
    for source in _glob_files(root_path / "runs" / run_id / "reports", "proof-artifact-*.json"):
        if source.name.startswith("proof-artifact-index-"):
            continue
        candidates.append(
            _BundleCandidate(
                f"evidence/proof/{source.name}",
                "proof_artifact",
                "proof_artifact_intake",
                False,
                source,
            )
        )
    for source in _glob_files(
        root_path / "runs" / run_id / "reports", "experiment-artifact-*.json"
    ):
        if source.name.startswith("experiment-artifact-index-"):
            continue
        candidates.append(
            _BundleCandidate(
                f"evidence/experiments/{source.name}",
                "experiment_artifact",
                "experiment_artifact_intake",
                False,
                source,
            )
        )
    for source in _glob_files(root_path / "runs" / run_id / "reports", "human-review-*.json"):
        candidates.append(
            _BundleCandidate(
                f"evidence/human-review/{source.name}",
                "human_review_artifact",
                "human_review",
                False,
                source,
            )
        )
    for source in _glob_files(
        root_path / "runs" / run_id / "experiments", "python-experiment-sandbox-run-*/*.json"
    ):
        candidates.append(
            _BundleCandidate(
                f"evidence/experiments/sandbox/{source.parent.name}/{source.name}",
                "python_sandbox_artifact",
                "python_experiment_sandbox",
                False,
                source,
            )
        )
    for uv_lock in _glob_files(
        root_path / "runs" / run_id / "experiments", "python-experiment-sandbox-run-*/uv.lock"
    ):
        candidates.append(
            _BundleCandidate(
                f"reproducibility/uv-locks/{uv_lock.parent.name}-uv.lock",
                "uv_lock",
                "python_experiment_sandbox",
                False,
                uv_lock,
            )
        )
    return candidates


def _final_audit_candidate(paths: dict[str, Path]) -> _BundleCandidate:
    if paths["final_audit"].is_file():
        return _BundleCandidate(
            "reports/final-audit.json",
            "final_audit",
            "final_audit",
            True,
            paths["final_audit"],
        )
    payload = {
        "audit_status": "derived_bundle_snapshot",
        "source_note": (
            "No standalone final-audit-report.json was present. This bundle snapshot records "
            "the final release inputs included in the bundle and remains non-evidence context."
        ),
        "release_report_path": _relative(paths["release_report"], paths["run_root"]),
        "claim_evidence_map_path": _relative(paths["claim_evidence_map"], paths["run_root"]),
        "claim_support_audit_path": _relative(paths["claim_support_audit"], paths["run_root"]),
        "citation_safety_report_path": _relative(
            paths["citation_safety_report"], paths["run_root"]
        ),
        "citation_registry_path": _relative(paths["citation_registry"], paths["run_root"]),
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "is_verification_evidence": False,
        "publication_ready": False,
    }
    return _BundleCandidate(
        "reports/final-audit.json",
        "final_audit_snapshot",
        "final_release_bundle",
        True,
        content=canonical_json(payload) + "\n",
    )


def _source_paths(root_path: Path, run_id: str, final_report) -> dict[str, Path]:
    run_path = root_path / "runs" / run_id
    reports = run_path / "reports"
    return {
        "run_root": root_path,
        "final_manuscript": root_path / final_report.final_manuscript_path,
        "release_report": _latest_report_path(
            reports,
            "full-paper-release-report-after-final-manuscript-*.json",
            reports / "full-paper-release-report.json",
        ),
        "final_audit": reports / "final-audit-report.json",
        "claim_evidence_map": latest_claim_evidence_map_path(root_path, run_id)
        or reports / "claim-evidence-map.json",
        "claim_support_audit": _latest_report_path(
            reports,
            "claim-support-audit-after-final-manuscript-*.json",
            reports / "claim-support-audit.json",
        ),
        "citation_safety_report": _latest_report_path(
            reports,
            "citation-safety-report-after-final-manuscript-*.json",
            reports / "citation-safety-report.json",
        ),
        "reviewer_summary": _latest_report_path(
            reports,
            "reviewer-bundle-summary-after-final-manuscript-*.json",
            reports / "reviewer-bundle-summary.json",
        ),
        "citation_registry": reports / "citation-registry.json",
        "retrieval_quality_report": reports / "retrieval-quality-report.json",
        "autonomous_loop_report": _latest_report_path(
            reports,
            "autonomous-loop-[0-9][0-9][0-9][0-9].json",
            reports / "autonomous-loop-0000.json",
        ),
        "capability_escalation_report": _latest_report_path(
            reports,
            "capability-escalation-[0-9][0-9][0-9][0-9].json",
            reports / "capability-escalation-0000.json",
        ),
    }


def _write_candidates(bundle_dir: Path, candidates: list[_BundleCandidate]) -> list[str]:
    missing: list[str] = []
    for candidate in candidates:
        destination = bundle_dir / candidate.relative_path
        if candidate.content is not None:
            _write_payload(destination, candidate.content)
            continue
        if candidate.source_path is None or not candidate.source_path.is_file():
            if candidate.required:
                missing.append(candidate.relative_path)
            continue
        _write_payload(destination, candidate.source_path.read_bytes())
    return missing


def _build_manifest(
    *,
    run_id: str,
    bundle_id: str,
    bundle_path: str,
    bundle_dir: Path,
    root: Path,
    candidates: list[_BundleCandidate],
) -> FinalReleaseBundleManifest:
    artifacts = []
    seen: set[str] = set()
    for candidate in sorted(candidates, key=lambda item: item.relative_path):
        path = bundle_dir / candidate.relative_path
        if not path.is_file() or candidate.relative_path in seen:
            continue
        seen.add(candidate.relative_path)
        artifacts.append(
            FinalReleaseBundleArtifact(
                relative_path=candidate.relative_path,
                sha256=sha256_file(path),
                artifact_type=candidate.artifact_type,
                source_path=(
                    _relative(candidate.source_path, root)
                    if candidate.source_path is not None
                    and candidate.source_path.exists()
                    and not _is_relative_to(candidate.source_path, bundle_dir)
                    else None
                ),
                created_by_stage=candidate.created_by_stage,
                required_for_bundle=candidate.required,
                non_evidence_flag=candidate.non_evidence_flag,
            )
        )
    return FinalReleaseBundleManifest(
        run_id=run_id,
        bundle_id=bundle_id,
        bundle_path=bundle_path,
        artifact_count=len(artifacts),
        hash_count=len(artifacts),
        artifacts=artifacts,
        publication_ready=False,
    )


def _build_reproducibility_manifest(
    *,
    run_id: str,
    bundle_id: str,
    root: Path,
    run_path: Path,
    paths: dict[str, Path],
    network_used: bool,
    external_tools_used: bool,
    ledger_tip_hash: str | None,
) -> FinalReleaseReproducibilityManifest:
    sandbox_reports = [
        _read_json(path)
        for path in _glob_files(run_path / "reports", "python-experiment-sandbox-run-*.json")
    ]
    uv_locks = [
        _relative(path, root)
        for path in _glob_files(run_path / "experiments", "python-experiment-sandbox-run-*/uv.lock")
    ]
    main_config = _read_json(paths["release_report"]) or {}
    loop_config = _read_json(paths["autonomous_loop_report"]) or {}
    return FinalReleaseReproducibilityManifest(
        run_id=run_id,
        bundle_id=bundle_id,
        created_at=_utc_now(),
        factori_protocol_version=PROTOCOL_VERSION,
        git_commit_hash_optional=_git_head(root),
        python_version=sys.version.split()[0],
        platform=platform.platform(),
        commands_used=[f"factori build-final-release-bundle --run-id {run_id}"],
        main_run_configuration=main_config.get("config", {})
        if isinstance(main_config, dict)
        else {},
        autonomous_loop_configuration=(
            {
                "loop_backend": loop_config.get("loop_backend"),
                "max_iterations": loop_config.get("max_iterations"),
                "iterations_completed": loop_config.get("iterations_completed"),
                "stop_reason": loop_config.get("stop_reason"),
            }
            if isinstance(loop_config, dict)
            else {}
        ),
        sandbox_configurations=[item for item in sandbox_reports if isinstance(item, dict)],
        uv_environment_paths=[
            _relative(path.parent, root)
            for path in _glob_files(
                run_path / "experiments", "python-experiment-sandbox-run-*/pyproject.toml"
            )
        ],
        uv_lock_paths=uv_locks,
        network_used=network_used,
        external_api_used=False,
        external_tools_used=external_tools_used,
        artifact_hashes=_source_artifact_hashes(paths),
        ledger_tip_hash_optional=ledger_tip_hash,
        publication_ready=False,
    )


def _write_hash_lock(bundle_dir: Path) -> dict[str, str]:
    paths = sorted(
        path for path in bundle_dir.rglob("*") if path.is_file() and path.name != "hashes.sha256"
    )
    hashes = {_relative(path, bundle_dir): sha256_file(path) for path in paths}
    content = "".join(f"{digest}  {path}\n" for path, digest in sorted(hashes.items()))
    _write_text(bundle_dir / "reproducibility" / "hashes.sha256", content)
    return hashes


def _verify_hash_lock(bundle_dir: Path) -> None:
    hash_path = bundle_dir / "reproducibility" / "hashes.sha256"
    for line in hash_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, relative = line.split("  ", maxsplit=1)
        actual = sha256_file(bundle_dir / relative)
        if actual != digest:
            raise FinalReleaseBundleError(f"Bundle hash mismatch for {relative}.")


def _post_bundle_checks(
    *,
    bundle_dir: Path,
    registry: CitationRegistry,
    claim_map: ClaimEvidenceMap,
) -> list[str]:
    missing: list[str] = []
    bib_keys = set(_BIB_KEY_RE.findall((bundle_dir / "paper" / "references.bib").read_text()))
    accepted_keys = {
        record.citation_key for record in registry.citations if record.accepted_for_registry
    }
    rejected_keys = {
        record.citation_key for record in registry.citations if not record.accepted_for_registry
    }
    if not bib_keys <= accepted_keys:
        missing.append("references_bib_accepted_sources_only")
    paper_tex = (bundle_dir / "paper" / "paper.tex").read_text(encoding="utf-8")
    if any(key in paper_tex for key in rejected_keys):
        missing.append("paper_tex_without_rejected_source_keys")
    markdown_keys = set(CITATION_MARKER_RE.findall((bundle_dir / "paper" / "paper.md").read_text()))
    if not markdown_keys <= accepted_keys:
        missing.append("paper_markdown_accepted_citations_only")
    if claim_map.unsupported_non_scaffold_claim_ids:
        missing.append("claim_evidence_map_without_unsupported_claims")
    return missing


def _compile_pdf(*, bundle_dir: Path, strict_export: bool) -> tuple[str | None, str | None]:
    pdflatex = shutil.which("pdflatex")
    if pdflatex is None:
        return None, "pdflatex_not_available"
    paper_dir = bundle_dir / "paper"
    try:
        result = subprocess.run(
            [pdflatex, "-interaction=nonstopmode", "paper.tex"],
            cwd=paper_dir,
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return None, f"pdf_compile_failed:{type(exc).__name__}"
    _write_text(paper_dir / "pdflatex.stdout.txt", result.stdout)
    _write_text(paper_dir / "pdflatex.stderr.txt", result.stderr)
    if result.returncode != 0 or not (paper_dir / "paper.pdf").is_file():
        return None, "pdf_compile_failed" if strict_export else "pdf_compile_warning"
    return "paper/paper.pdf", None


def _latex_inline(text: str) -> str:
    placeholders: dict[str, str] = {}

    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        placeholder = f"FACTORI_CITE_{len(placeholders)}"
        placeholders[placeholder] = rf"\cite{{{_latex_escape(key)}}}"
        return placeholder

    escaped = _latex_escape(CITATION_MARKER_RE.sub(repl, text))
    for placeholder, command in placeholders.items():
        escaped = escaped.replace(placeholder, command)
    escaped = re.sub(r"`([^`]+)`", lambda m: r"\texttt{" + _latex_escape(m.group(1)) + "}", escaped)
    return escaped


def _latex_escape(text: str) -> str:
    return "".join(_LATEX_SPECIALS.get(char, char) for char in text)


def _bib_escape(text: str) -> str:
    return str(text).replace("\\", "\\textbackslash{}").replace("{", "\\{").replace("}", "\\}")


def _require_accepted_registry(registry: CitationRegistry) -> None:
    rejected = [
        record.citation_key for record in registry.citations if not record.accepted_for_registry
    ]
    if rejected:
        raise FinalReleaseBundleError(
            "Citation registry contains non-accepted sources: " + ", ".join(sorted(rejected))
        )


def _rejected_sources_payload(paths: dict[str, Path]) -> list[dict[str, Any]]:
    report = _read_json(paths["retrieval_quality_report"])
    if not isinstance(report, dict):
        return []
    reasons = report.get("rejection_reasons") or {}
    return [
        {
            "source_id": source_id,
            "rejection_reason": reasons.get(source_id, "rejected_by_quality_filter"),
        }
        for source_id in report.get("rejected_source_ids", [])
    ]


def _bundle_retrieval_paths(bundle_dir: Path, root: Path) -> list[str]:
    return [
        _relative(path, root)
        for path in sorted((bundle_dir / "sources").glob("*retrieval*.json"))
        if path.is_file()
    ]


def _bundle_evidence_paths(bundle_dir: Path, root: Path, pattern: str) -> list[str]:
    return [_relative(path, root) for path in sorted(bundle_dir.glob(pattern)) if path.is_file()]


def _read_model(path: Path, model_type):
    if not path.is_file():
        return None
    try:
        return model_type.model_validate_json(path.read_text(encoding="utf-8"))
    except ValueError:
        return None


def _read_text(path: Path) -> str:
    if not path.is_file():
        raise FinalReleaseBundleError(f"Required file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _read_json(path: Path) -> Any | None:
    if not path.is_file():
        return None
    try:
        import json

        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _source_artifact_hashes(paths: dict[str, Path]) -> dict[str, str]:
    return {
        key: sha256_file(path)
        for key, path in sorted(paths.items())
        if path.is_file() and key not in {"capability_escalation_report"}
    }


def _latest_report_path(directory: Path, pattern: str, fallback: Path) -> Path:
    matches = sorted(
        path for path in directory.glob(pattern) if not path.name.endswith(".meta.json")
    )
    return matches[-1] if matches else fallback


def _numbered_paths(directory: Path, pattern: str) -> list[Path]:
    return sorted(path for path in directory.glob(pattern) if not path.name.endswith(".meta.json"))


def _next_bundle_number(run_path: Path) -> int:
    reports = run_path / "reports"
    return len(_numbered_paths(reports, "final-release-bundle-[0-9][0-9][0-9][0-9].json")) + 1


def _glob_files(directory: Path, pattern: str) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        path
        for path in directory.glob(pattern)
        if path.is_file() and not path.name.endswith(".meta.json")
    )


def _write_payload(path: Path, content: str | bytes) -> None:
    if isinstance(content, str):
        _write_text(path, content)
    else:
        _write_bytes(path, content)


def _write_text(path: Path, text: str) -> None:
    _write_bytes(path, text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8"))


def _write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _bundle_readme(run_id: str) -> str:
    return "\n".join(
        [
            "# fActorI Final Release Bundle",
            "",
            f"Run ID: `{run_id}`",
            "",
            "This bundle is packaging and reproducibility context only. It does not create proof,",
            "experiment evidence, scientific validation, human approval, or publication readiness.",
            "",
            "- `paper/paper.md` is the preferred final manuscript.",
            "- `paper/paper.tex` is deterministic presentation output.",
            "- `paper/references.bib` is generated from accepted registry sources only.",
            "- `reproducibility/hashes.sha256` locks included file content.",
            "- publication_ready: false",
            "",
        ]
    )


def _environment_payload() -> dict[str, Any]:
    return {
        "python_version": sys.version,
        "platform": platform.platform(),
        "network_used": False,
        "external_api_used": False,
        "publication_ready": False,
    }


def _git_head(root: Path) -> str | None:
    git = root / ".git"
    head = git / "HEAD"
    if not head.is_file():
        return None
    value = head.read_text(encoding="utf-8").strip()
    if value.startswith("ref: "):
        ref = git / value.removeprefix("ref: ").strip()
        return ref.read_text(encoding="utf-8").strip() if ref.is_file() else None
    return value or None


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _artifact_metadata(stage: str, role: str) -> dict[str, Any]:
    return {
        "stage": stage,
        "artifact_role": role,
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "implies_publication_readiness": False,
        "publication_ready": False,
    }


def _relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


__all__ = [
    "FinalReleaseBundleError",
    "FinalReleaseBundleResult",
    "build_final_release_bundle",
    "inspect_final_release_bundle",
    "latest_final_release_bundle",
    "final_release_bundle_summary_fields",
    "render_final_release_bundle_markdown",
    "markdown_to_latex",
    "build_references_bib",
]
