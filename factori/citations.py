"""Deterministic citation registry and citation-safety helpers."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any

from factori.artifacts import ArtifactStore
from factori.hashing import sha256_json
from factori.ledger import ResearchLedger
from factori.persistence import ArtifactWriteSpec, PersistenceResult, persist_artifacts_with_commit
from factori.schemas import (
    ArtifactRef,
    ArtifactType,
    BibliographyEntry,
    CitationRecord,
    CitationRegistry,
    CitationSafetyReport,
    CitationUsage,
    ControllerActionType,
    LiteraturePositioningReport,
    RetrievalRunReport,
)

CITATION_KEY_POLICY = "FirstAuthorYearShortTitle; duplicate keys receive deterministic letters."
CITATION_MARKER_RE = re.compile(r"\[@([A-Za-z0-9][A-Za-z0-9_.:-]*)\]")
_EXHAUSTIVE_CLAIMS = (
    "exhaustive literature coverage",
    "complete literature coverage",
    "covers all prior work",
    "all prior work",
    "comprehensive literature review",
    "exhaustive review",
)
_RETRIEVAL_PROOF_CLAIMS = (
    "retrieval proves novelty",
    "retrieval proves",
    "citations prove novelty",
    "citation proves novelty",
    "novelty is proven by retrieval",
    "novelty is proven",
    "retrieval as proof",
    "citation as proof",
    "citation evidence proves",
    "retrieval evidence proves",
    "proof evidence from citations",
    "experiment evidence from citations",
)


@dataclass(frozen=True)
class CitationRegistryArtifacts:
    """Artifacts produced by optional citation-registry persistence."""

    citation_registry_artifact: ArtifactRef
    literature_positioning_artifact: ArtifactRef
    citation_safety_artifact: ArtifactRef
    commit_hash: str


def build_citation_registry(
    run_id: str,
    retrieval_results: list[Any],
    *,
    source_artifact_ids: dict[str, str] | None = None,
) -> CitationRegistry:
    """Build a deterministic citation registry from normalized retrieval results."""
    source_artifact_ids = source_artifact_ids or {}
    ordered = sorted(
        retrieval_results,
        key=lambda item: (
            str(getattr(item, "provider", "")),
            str(getattr(item, "query", "")),
            int(getattr(item, "rank", 0)),
            str(getattr(item, "source_id", "")),
        ),
    )
    base_keys = [_base_citation_key(result) for result in ordered]
    counts = Counter(base_keys)
    seen: dict[str, int] = defaultdict(int)
    records = []
    for result, base_key in zip(ordered, base_keys, strict=True):
        seen[base_key] += 1
        suffix = _letter_suffix(seen[base_key]) if counts[base_key] > 1 else ""
        key = f"{base_key}{suffix}"
        warnings = _citation_warnings(result)
        source_id = str(getattr(result, "source_id", "unknown-source"))
        records.append(
            CitationRecord(
                citation_id=_citation_id(source_id),
                citation_key=key,
                source_id=source_id,
                title=str(getattr(result, "title", "") or f"Untitled source {source_id}"),
                authors=list(getattr(result, "authors", []) or []),
                year=getattr(result, "year", None),
                venue=getattr(result, "venue", None),
                doi=getattr(result, "doi", None),
                url=getattr(result, "url", None),
                provider=str(getattr(result, "provider", "unknown")),
                retrieved_at=str(getattr(result, "retrieved_at", "1970-01-01T00:00:00Z")),
                raw_metadata_hash=str(getattr(result, "raw_metadata_hash", "")),
                source_artifact_id=source_artifact_ids.get(source_id),
                warnings=warnings,
            )
        )
    bibliography = [_bibliography_entry(record) for record in records]
    warnings = sorted({warning for record in records for warning in record.warnings})
    if not records:
        warnings.append("No retrieval sources were available for citation registry construction.")
    return CitationRegistry(
        run_id=run_id,
        citations=records,
        bibliography=bibliography,
        citation_key_policy=CITATION_KEY_POLICY,
        source_registry_hash=sha256_json([record.model_dump(mode="json") for record in records]),
        warnings=warnings,
    )


def build_citation_registry_from_ledger(
    run_id: str,
    ledger: ResearchLedger,
) -> CitationRegistry:
    """Build a citation registry from ledgered retrieval-run reports, if any exist."""
    results = []
    source_artifacts: dict[str, str] = {}
    for commit in ledger.list_commits(run_id):
        if commit.action_type != ControllerActionType.RETRIEVAL_RUN_RECORDED:
            continue
        try:
            report = RetrievalRunReport.model_validate(commit.payload)
        except Exception:
            continue
        results.extend(report.results)
        for artifact in commit.artifact_refs:
            if artifact.id.startswith("retrieval-normalized-results-"):
                for result in report.results:
                    source_artifacts[result.source_id] = artifact.id
    return build_citation_registry(
        run_id,
        results,
        source_artifact_ids=source_artifacts,
    )


def validate_citation_usage(
    draft: str | Any,
    citation_registry: CitationRegistry,
) -> CitationSafetyReport:
    """Validate Markdown citation markers against a citation registry."""
    markdown = getattr(draft, "markdown", draft)
    if not isinstance(markdown, str):
        markdown = str(markdown)
    key_to_record = {record.citation_key: record for record in citation_registry.citations}
    markers = CITATION_MARKER_RE.findall(markdown)
    marker_counts = Counter(markers)
    unknown = sorted(key for key in marker_counts if key not in key_to_record)
    duplicate_keys = sorted(
        key
        for key, count in Counter(
            record.citation_key for record in citation_registry.citations
        ).items()
        if count > 1
    )
    reasons: list[str] = []
    warnings: list[str] = []
    if unknown:
        reasons.append(f"unknown or invented citation keys: {', '.join(unknown)}")
    if duplicate_keys:
        reasons.append(f"duplicate ambiguous citation keys: {', '.join(duplicate_keys)}")
    bibliography_missing = sorted(
        entry.citation_key
        for entry in citation_registry.bibliography
        if not entry.has_source_provenance
    )
    if bibliography_missing:
        reasons.append(
            "bibliography entries lack source provenance: "
            + ", ".join(bibliography_missing)
        )
    lowered = " ".join(markdown.lower().split())
    if _contains_unbounded_claim(lowered, _EXHAUSTIVE_CLAIMS):
        reasons.append("unsupported exhaustive literature coverage claim appears in draft")
    if _contains_unbounded_claim(lowered, _RETRIEVAL_PROOF_CLAIMS):
        reasons.append("retrieval or citations are described as novelty/proof evidence")
    if not markers:
        warnings.append("No citation markers were used in the draft.")
    usages = [
        CitationUsage(
            citation_key=key,
            count=count,
            known=key in key_to_record,
            citation_id=key_to_record[key].citation_id if key in key_to_record else None,
        )
        for key, count in sorted(marker_counts.items())
    ]
    used_ids = sorted(
        key_to_record[key].citation_id for key in marker_counts if key in key_to_record
    )
    return CitationSafetyReport(
        run_id=citation_registry.run_id,
        safe=not reasons,
        rejected=bool(reasons),
        citation_usages=usages,
        unknown_citation_keys=unknown,
        invented_bibliography_keys=unknown,
        reasons=sorted(set(reasons)),
        warnings=sorted(set(warnings)),
        used_citation_keys=sorted(marker_counts),
        used_citation_ids=used_ids,
        bibliography_entries_count=len(citation_registry.bibliography),
    )


def write_citation_registry_reports(
    *,
    run_id: str,
    store: ArtifactStore,
    ledger: ResearchLedger,
    citation_registry: CitationRegistry,
    literature_positioning_report: LiteraturePositioningReport,
    citation_safety_report: CitationSafetyReport,
) -> CitationRegistryArtifacts:
    """Persist citation/literature-positioning reports as context artifacts."""
    metadata = {
        "stage": "citation_registry",
        "artifact_role": "literature_positioning_context",
        "is_verification_evidence": False,
        "creates_scientific_validation": False,
        "proves_novelty": False,
        "claims_literature_coverage": False,
    }
    persistence = persist_artifacts_with_commit(
        run_id=run_id,
        store=store,
        ledger=ledger,
        artifact_specs=[
            ArtifactWriteSpec(
                artifact_id="citation-registry",
                artifact_type=ArtifactType.REPORT,
                payload=citation_registry,
                artifact_format="json",
                metadata=metadata,
            ),
            ArtifactWriteSpec(
                artifact_id="literature-positioning-report",
                artifact_type=ArtifactType.REPORT,
                payload=literature_positioning_report,
                artifact_format="json",
                metadata=metadata,
            ),
            ArtifactWriteSpec(
                artifact_id="citation-safety-report",
                artifact_type=ArtifactType.REPORT,
                payload=citation_safety_report,
                artifact_format="json",
                metadata=metadata,
            ),
        ],
        action_type=ControllerActionType.CITATION_REGISTRY_WRITTEN,
        commit_payload={
            "run_id": run_id,
            "citations": len(citation_registry.citations),
            "citation_safety_safe": citation_safety_report.safe,
            "is_verification_evidence": False,
            "creates_scientific_validation": False,
            "proves_novelty": False,
        },
    )
    return _citation_artifacts_from_persistence(persistence)


def _citation_artifacts_from_persistence(
    persistence: PersistenceResult,
) -> CitationRegistryArtifacts:
    artifacts = {artifact.id: artifact for artifact in persistence.artifacts}
    return CitationRegistryArtifacts(
        citation_registry_artifact=artifacts["citation-registry"],
        literature_positioning_artifact=artifacts["literature-positioning-report"],
        citation_safety_artifact=artifacts["citation-safety-report"],
        commit_hash=persistence.commit.commit_hash,
    )


def _base_citation_key(result: Any) -> str:
    authors = list(getattr(result, "authors", []) or [])
    year = getattr(result, "year", None)
    title = str(getattr(result, "title", "") or "")
    source_id = str(getattr(result, "source_id", "") or "source")
    author_part = _sanitize_key_part(_author_part(authors)) or "Source"
    year_part = str(year) if isinstance(year, int) else "NoYear"
    title_part = _sanitize_key_part(_short_title(title))
    if title_part:
        key = f"{author_part}{year_part}{title_part}"
    else:
        key = f"{author_part}{year_part}{_sanitize_key_part(source_id)}"
    return key or f"Source{_sanitize_key_part(source_id)}"


def _author_part(authors: list[str]) -> str:
    if not authors:
        return "Source"
    tokens = [token for token in re.split(r"\s+", authors[0].strip()) if token]
    return tokens[-1] if tokens else "Source"


def _short_title(title: str) -> str:
    stop = {"a", "an", "and", "for", "in", "of", "on", "the", "to", "with"}
    words = [
        word
        for word in re.findall(r"[A-Za-z0-9]+", title)
        if word.lower() not in stop
    ]
    return "".join(word[:1].upper() + word[1:16] for word in words[:3])


def _sanitize_key_part(value: str) -> str:
    cleaned = "".join(ch for ch in value if ch.isalnum())
    if not cleaned:
        return ""
    return cleaned[:1].upper() + cleaned[1:]


def _letter_suffix(index: int) -> str:
    letters = "abcdefghijklmnopqrstuvwxyz"
    index -= 1
    if index < len(letters):
        return letters[index]
    return letters[index % len(letters)] + str(index // len(letters) + 1)


def _citation_id(source_id: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.:-]+", "-", source_id).strip("-")
    return f"citation-{cleaned or sha256_json(source_id)[:12]}"


def _citation_warnings(result: Any) -> list[str]:
    warnings = []
    if not getattr(result, "authors", []):
        warnings.append(f"source {getattr(result, 'source_id', 'unknown')} has no authors")
    if getattr(result, "year", None) is None:
        warnings.append(f"source {getattr(result, 'source_id', 'unknown')} has no year")
    if not str(getattr(result, "raw_metadata_hash", "")).strip():
        warnings.append(
            f"source {getattr(result, 'source_id', 'unknown')} has no raw metadata hash"
        )
    return warnings


def _bibliography_entry(record: CitationRecord) -> BibliographyEntry:
    author_text = ", ".join(record.authors) if record.authors else "Unknown author"
    year_text = str(record.year) if record.year is not None else "n.d."
    tail = []
    if record.venue:
        tail.append(record.venue)
    if record.doi:
        tail.append(f"doi:{record.doi}")
    elif record.url:
        tail.append(record.url)
    tail_text = " ".join(tail)
    markdown = (
        f"- [@{record.citation_key}] {author_text} ({year_text}). "
        f"{record.title}. {tail_text} Source: `{record.source_id}`."
    ).replace("  ", " ").strip()
    return BibliographyEntry(
        citation_id=record.citation_id,
        citation_key=record.citation_key,
        source_id=record.source_id,
        markdown=markdown,
        has_source_provenance=bool(record.raw_metadata_hash and record.provider),
        warnings=list(record.warnings),
    )


def _contains_unbounded_claim(text: str, phrases: tuple[str, ...]) -> bool:
    return any(
        phrase in text
        and f"not {phrase}" not in text
        and f"not proof of {phrase}" not in text
        for phrase in phrases
    )


__all__ = [
    "CITATION_KEY_POLICY",
    "CITATION_MARKER_RE",
    "CitationRegistryArtifacts",
    "build_citation_registry",
    "build_citation_registry_from_ledger",
    "validate_citation_usage",
    "write_citation_registry_reports",
]
